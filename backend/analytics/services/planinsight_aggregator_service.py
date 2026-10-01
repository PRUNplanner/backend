import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from analytics.models import AnalyticsPlanAggregate
from django.db.models import Count, Value
from django.db.models.functions import Concat
from django.utils import timezone
from gamedata.models import GameBuilding, GameRecipe
from planning.models import PlanningPlan


@dataclass
class _Usage:
    """Per-plan amounts of one item (building, recipe, mix, expert) and who planned it."""

    amounts: list[int] = field(default_factory=list)
    users: set[int] = field(default_factory=set)


@dataclass
class _MixUsage(_Usage):
    recipe_amounts: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))


@dataclass
class _BuildingUsage(_Usage):
    recipes: dict[str, _Usage] = field(default_factory=lambda: defaultdict(_Usage))
    mixes: dict[tuple[str, ...], _MixUsage] = field(default_factory=lambda: defaultdict(_MixUsage))


def _median(amounts: list[int]) -> int:
    # round half up, amounts are never negative
    return math.floor(statistics.median(amounts) + 0.5)


class PlanInsightAggregatorService:
    MIN_PLANS_THRESHOLD = 15
    MIN_USERS_THRESHOLD = 10
    USER_ACTIVITY_DAYS = 90
    PLAN_STALENESS_DAYS = 180
    # v1 keys
    BUILDING_USAGE_CUTOFF = 20
    RECIPE_USAGE_CUTOFF = 10
    # v2 keys: buildings, recipes and mixes
    V2_MIN_PERCENTAGE = 5
    V2_MIN_USERS = 10
    V2_TOP_MIXES = 3
    EXTRACTION_BUILDINGS = {'COL', 'EXT', 'RIG'}

    def __init__(self):
        # cache valid recipes for validation
        self.valid_recipes = set(
            GameRecipe.objects.annotate(full_id=Concat('building_ticker', Value('#'), 'recipe_name')).values_list(
                'full_id', flat=True
            )
        )
        self.valid_buildings = set(GameBuilding.objects.values_list('building_ticker', flat=True).distinct())

    def aggregate_all_plans(self) -> tuple[int, int]:
        login_cutoff = timezone.now() - timedelta(days=self.USER_ACTIVITY_DAYS)
        modified_cutoff = timezone.now() - timedelta(days=self.PLAN_STALENESS_DAYS)

        active_planets = (
            PlanningPlan.objects.filter(user__last_login__gte=login_cutoff, modified_at__gte=modified_cutoff)
            .values('planet_natural_id')
            .annotate(total=Count('uuid'), users=Count('user', distinct=True))
            .filter(total__gte=self.MIN_PLANS_THRESHOLD, users__gte=self.MIN_USERS_THRESHOLD)
            .values_list('planet_natural_id', flat=True)
        )

        processed_ids = []

        # process all active plans
        for planet_id in active_planets:
            result_id = self.process_planet(planet_id)
            if result_id:
                processed_ids.append(result_id)

        # clean up insights for planets not processed, i.e. stale ones
        deleted_count, _ = AnalyticsPlanAggregate.objects.exclude(planet_natural_id__in=processed_ids).delete()

        return len(processed_ids), deleted_count

    def process_planet(self, planet_natural_id: str) -> str | None:

        login_cutoff = timezone.now() - timedelta(days=self.USER_ACTIVITY_DAYS)
        modified_cutoff = timezone.now() - timedelta(days=self.PLAN_STALENESS_DAYS)

        plans = (
            PlanningPlan.objects.filter(
                planet_natural_id=planet_natural_id,
                user__last_login__gte=login_cutoff,
                modified_at__gte=modified_cutoff,
            )
            .values_list('user_id', 'plan_data')
            .iterator(chunk_size=500)
        )

        total_valid_plans = 0
        users: set[int] = set()
        experts_total = Counter()
        building_presence = Counter()
        recipe_distribution = defaultdict(Counter)
        buildings_v2: dict[str, _BuildingUsage] = defaultdict(_BuildingUsage)
        experts_v2: dict[str, _Usage] = defaultdict(_Usage)

        for user_id, data in plans:
            total_valid_plans += 1
            users.add(user_id)

            # experts
            plan_experts = Counter()
            for exp in data.get('experts', []):
                if exp.get('amount', 0) > 0:
                    experts_total[exp['type']] += exp['amount']
                    plan_experts[exp['type']] += exp['amount']

            # buildings and recipes
            plan_buildings = data.get('buildings', [])
            prod_count = 0
            seen_in_this_plan = set()
            # v2: amounts of this plan, summed when a building is listed twice
            plan_amounts = Counter()
            plan_recipes = defaultdict(Counter)

            for b in plan_buildings:
                b_code = b.get('name')

                # invalid / not-existing-anymore building
                if b_code not in self.valid_buildings:
                    continue

                prod_count += b.get('amount', 0)
                seen_in_this_plan.add(b_code)
                plan_amounts[b_code] += b.get('amount', 0)

                for r in b.get('active_recipes', []):
                    r_id = r.get('recipeid')

                    # recipe must still be valid / existing or extraction building
                    ticker = r_id.split('#')[0]
                    if ticker in self.EXTRACTION_BUILDINGS or r_id in self.valid_recipes:
                        recipe_distribution[b_code][r_id] += 1
                        plan_recipes[b_code][r_id] += r.get('amount', 0)

            for b_code in seen_in_this_plan:
                building_presence[b_code] += 1

            self._collect_v2(user_id, plan_amounts, plan_recipes, plan_experts, buildings_v2, experts_v2)

        if total_valid_plans < self.MIN_PLANS_THRESHOLD or len(users) < self.MIN_USERS_THRESHOLD:
            return None

        building_distribution, building_tickers = self._get_building_distribution(building_presence, total_valid_plans)

        insights_payload = {
            'expert_distribution': self._get_expert_distribution(experts_total),
            'recipe_distribution': self._get_recipe_distribution(recipe_distribution, building_tickers),
            'building_distribution': building_distribution,
            'buildings': self._get_buildings_v2(buildings_v2, total_valid_plans),
            'experts': self._get_experts_v2(experts_v2, total_valid_plans),
        }

        AnalyticsPlanAggregate.objects.update_or_create(
            planet_natural_id=planet_natural_id,
            defaults={
                'insights_data': insights_payload,
                'total_plans_analyzed': total_valid_plans,
                'total_users': len(users),
            },
        )

        return planet_natural_id

    @staticmethod
    def _collect_v2(
        user_id: int,
        plan_amounts: Counter[str],
        plan_recipes: dict[str, Counter[str]],
        plan_experts: Counter[str],
        buildings: dict[str, _BuildingUsage],
        experts: dict[str, _Usage],
    ) -> None:
        """Adds one plan; only items with an amount count, so medians are over plans that have them."""
        for expert_type, amount in plan_experts.items():
            experts[expert_type].amounts.append(amount)
            experts[expert_type].users.add(user_id)

        for b_code, amount in plan_amounts.items():
            if amount <= 0:
                continue
            building = buildings[b_code]
            building.amounts.append(amount)
            building.users.add(user_id)

            recipes = {r_id: slots for r_id, slots in plan_recipes[b_code].items() if slots > 0}
            for r_id, slots in recipes.items():
                building.recipes[r_id].amounts.append(slots)
                building.recipes[r_id].users.add(user_id)

            if recipes:
                mix = building.mixes[tuple(sorted(recipes))]
                mix.amounts.append(amount)
                mix.users.add(user_id)
                for r_id, slots in recipes.items():
                    mix.recipe_amounts[r_id].append(slots)

    def _passes_v2(self, usage: _Usage, total: int) -> bool:
        return len(usage.users) >= self.V2_MIN_USERS and len(usage.amounts) / total * 100 >= self.V2_MIN_PERCENTAGE

    def _get_buildings_v2(self, buildings: dict[str, _BuildingUsage], total_plans: int) -> list[dict]:
        result = []
        for ticker, building in buildings.items():
            if not self._passes_v2(building, total_plans):
                continue
            building_plans = len(building.amounts)

            recipes = [
                {
                    'recipe_id': r_id,
                    'plans': len(recipe.amounts),
                    'percentage': round(len(recipe.amounts) / building_plans * 100, 2),
                    'median_amount': _median(recipe.amounts),
                }
                for r_id, recipe in building.recipes.items()
                if self._passes_v2(recipe, building_plans)
            ]
            mixes = [
                {
                    'recipe_ids': list(recipe_ids),
                    'plans': len(mix.amounts),
                    'percentage': round(len(mix.amounts) / building_plans * 100, 2),
                    'median_building_amount': _median(mix.amounts),
                    'recipe_amounts': {r_id: _median(amounts) for r_id, amounts in mix.recipe_amounts.items()},
                }
                for recipe_ids, mix in building.mixes.items()
                if self._passes_v2(mix, building_plans)
            ]

            result.append(
                {
                    'ticker': ticker,
                    'plans': building_plans,
                    'users': len(building.users),
                    'percentage': round(building_plans / total_plans * 100, 2),
                    'median_amount': _median(building.amounts),
                    'recipes': sorted(recipes, key=lambda r: (-r['plans'], r['recipe_id'])),
                    'mixes': sorted(mixes, key=lambda m: (-m['plans'], m['recipe_ids']))[: self.V2_TOP_MIXES],
                }
            )

        return sorted(result, key=lambda b: (-b['plans'], b['ticker']))

    @staticmethod
    def _get_experts_v2(experts: dict[str, _Usage], total_plans: int) -> list[dict]:
        result = [
            {
                'type': expert_type,
                'plans_percentage': round(len(usage.amounts) / total_plans * 100, 2),
                'median_amount': _median(usage.amounts),
            }
            for expert_type, usage in experts.items()
        ]
        return sorted(result, key=lambda e: (-e['plans_percentage'], e['type']))

    def _get_expert_distribution(self, expert_totals: Counter) -> list:

        total_points = sum(expert_totals.values())

        if total_points == 0:
            return []

        expert_split = []

        for expert_type, amount in expert_totals.items():
            percentage = (amount / total_points) * 100

            if percentage > 0:
                expert_split.append({'type': expert_type.replace('_', ' ').title(), 'percentage': round(percentage, 2)})

        return sorted(expert_split, key=lambda x: x['percentage'], reverse=True)

    def _get_building_distribution(self, building_presence: Counter, total_plans: int) -> tuple[list, list]:

        builds = []
        for ticker, count in building_presence.items():
            percentage = (count / total_plans) * 100
            if percentage >= self.BUILDING_USAGE_CUTOFF:
                builds.append({'ticker': ticker, 'percentage': round(percentage, 2)})

        builds = sorted(builds, key=lambda x: x['percentage'], reverse=True)
        tickers = [b['ticker'] for b in builds]

        return builds, tickers

    def _get_recipe_distribution(self, recipe_distribution: dict, building_tickers: list[str]) -> dict:

        deep_dive = {}

        for ticker, recipes in recipe_distribution.items():
            total_recipe_runs = sum(recipes.values())
            if total_recipe_runs == 0:
                continue

            # top 5 most used recipes for this building
            top_three = []
            for rid, count in recipes.most_common(5):
                recipe_building = rid.split('#')[0]
                if recipe_building not in self.EXTRACTION_BUILDINGS and recipe_building not in building_tickers:
                    continue

                percentage = round(count / total_recipe_runs * 100, 2)
                if percentage >= self.RECIPE_USAGE_CUTOFF:
                    top_three.append({'recipe_id': rid, 'percentage': percentage})

            if top_three:
                deep_dive[ticker] = top_three

        return deep_dive
