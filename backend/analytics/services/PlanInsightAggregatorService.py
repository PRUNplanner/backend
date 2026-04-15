from collections import Counter, defaultdict
from datetime import timedelta

from analytics.models import AnalyticsPlanAggregate
from django.db.models import Count, Value
from django.db.models.functions import Concat
from django.utils import timezone
from gamedata.models import GameBuilding, GameRecipe
from planning.models import PlanningPlan


class PlanInsightAggregatorService:
    MIN_PLANS_THRESHOLD = 15
    USER_ACTIVITY_DAYS = 90
    PLAN_STALENESS_DAYS = 180
    BUILDING_USAGE_CUTOFF = 20
    RECIPE_USAGE_CUTOFF = 10

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
            .annotate(total=Count('uuid'))
            .filter(total__gte=self.MIN_PLANS_THRESHOLD)
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

        plans = PlanningPlan.objects.filter(
            planet_natural_id=planet_natural_id, user__last_login__gte=login_cutoff, modified_at__gte=modified_cutoff
        ).iterator(chunk_size=500)

        total_valid_plans = 0
        experts_total = Counter()
        building_presence = Counter()
        recipe_distribution = defaultdict(Counter)

        for plan in plans:
            total_valid_plans += 1
            data = plan.plan_data

            # experts
            for exp in data.get('experts', []):
                if exp.get('amount', 0) > 0:
                    experts_total[exp['type']] += exp['amount']

            # buildings and recipes
            plan_buildings = data.get('buildings', [])
            prod_count = 0
            seen_in_this_plan = set()

            for b in plan_buildings:
                b_code = b.get('name')

                # invalid / not-existing-anymore building
                if b_code not in self.valid_buildings:
                    continue

                prod_count += b.get('amount', 0)
                seen_in_this_plan.add(b_code)

                for r in b.get('active_recipes', []):
                    r_id = r.get('recipeid')

                    # recipe must still be valid / existing
                    if r_id in self.valid_recipes:
                        recipe_distribution[b_code][r_id] += 1

            for b_code in seen_in_this_plan:
                building_presence[b_code] += 1

        if total_valid_plans < self.MIN_PLANS_THRESHOLD:
            return None

        building_distribution, building_tickers = self._get_building_distribution(building_presence, total_valid_plans)

        insights_payload = {
            'expert_distribution': self._get_expert_distribution(experts_total),
            'recipe_distribution': self._get_recipe_distribution(recipe_distribution, building_tickers),
            'building_distribution': building_distribution,
        }

        AnalyticsPlanAggregate.objects.update_or_create(
            planet_natural_id=planet_natural_id,
            defaults={'insights_data': insights_payload, 'total_plans_analyzed': total_valid_plans},
        )

        return planet_natural_id

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
                ticker = rid.split('#')[0]
                if ticker not in building_tickers:
                    continue

                percentage = round(count / total_recipe_runs * 100, 2)
                if percentage >= self.RECIPE_USAGE_CUTOFF:
                    top_three.append({'recipe_id': rid, 'percentage': percentage})

            if top_three:
                deep_dive[ticker] = top_three

        return deep_dive
