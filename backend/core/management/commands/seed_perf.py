"""
Fill the perf database with deterministic data for benchmarks and load tests.

Game data comes from the FIO snapshot written by perf_snapshot when there is one
(--source auto), otherwise it is fake. Users, plans and empires are always fake,
built on that game data. Only runs under PERF_MODE (core.config.django.perf),
never against a dev or production database. See perf/README.md.
"""

import itertools
import random
import string
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import orjson
from analytics.models import AnalyticsEmpireMaterialSnapshot, AnalyticsPlanAggregate
from analytics.tasks import analytics_bulk_materialize_empire_snapshots, analytics_update_plan_insight_aggregates
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection, models, transaction
from gamedata.fio.importers import (
    cxpc_objects,
    save_buildings,
    save_exchanges,
    save_materials,
    save_planets,
    save_recipes,
)
from gamedata.models import (
    GameBuilding,
    GameBuildingCost,
    GameExchange,
    GameExchangeCXPC,
    GameMaterial,
    GamePlanet,
    GamePlanetCOGCProgram,
    GamePlanetInfrastructureReport,
    GamePlanetProductionFee,
    GamePlanetResource,
    GameRecipe,
    GameRecipeInput,
    GameRecipeOutput,
)
from gamedata.models.game_building import GameBuildingExpertiseChoices
from gamedata.models.game_planet import GamePlanetCOGCProgramChoices, GamePlanetWorkforceLevelChoices
from planning.models import (
    PlanningCOGCChoices,
    PlanningCX,
    PlanningEmpire,
    PlanningEmpirePlan,
    PlanningFactionChoices,
    PlanningPlan,
    PlanningShared,
)
from planning.schemas.latest_schemas import LATEST_SCHEMA
from user.models import User, UserPreference

from core.management.commands.perf_snapshot import SNAPSHOT_PATH, GamedataSnapshot

USERNAME_PREFIX = 'perf_user_'
PASSWORD = 'perf-password'  # fake credential, only ever used in the throwaway perf database
# a crowd on one planet that passes every planet insights threshold (10 users, buildings and mixes of 10 users)
INSIGHTS_USERNAME_PREFIX = 'insights_user_'
INSIGHTS_USERS = 12
INSIGHTS_PLANET = 'KW-688c'

LIVE_EXCHANGES = ('AI1', 'CI1', 'IC1', 'NC1')
CXPC_EXCHANGES = (*LIVE_EXCHANGES, 'UNIVERSE')
EXPERT_TYPES = (
    'Agriculture',
    'Chemistry',
    'Construction',
    'Electronics',
    'Food_Industries',
    'Fuel_Refining',
    'Manufacturing',
    'Metallurgy',
    'Resource_Extraction',
)
WORKFORCE_TYPES = ('pioneer', 'settler', 'technician', 'engineer', 'scientist')
INFRASTRUCTURE = ('HB1', 'HB2', 'HB3', 'HB4', 'HB5', 'HBB', 'HBC', 'HBM', 'HBL', 'STO')
# players crowd onto a few popular planets; enough plans there for the planet insights threshold
HOT_PLANETS = 20
HOT_PLANET_SHARE = 0.5
BATCH_SIZE = 2000
DAY_MS = 86_400_000

type JSONValue = str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]
type FieldValue = str | int | float | bool
type MaterialAmounts = list[tuple[str, int]]


@dataclass(frozen=True)
class Recipe:
    time_ms: int
    inputs: MaterialAmounts
    outputs: MaterialAmounts


@dataclass(frozen=True)
class Scale:
    users: int
    max_plans_per_user: int
    max_empires_per_user: int
    # fake game data only; a snapshot loads all of its game data
    planets: int
    materials: int
    buildings: int
    recipes: int
    cxpc_days: int
    # share of users with 1-5 plans and one empire, as most real users; the rest spread up to the max
    small_user_share: float = 0.0


SCALES: dict[str, Scale] = {
    # tiny is for the command's own tests
    'tiny': Scale(3, 4, 2, 12, 20, 8, 15, 3),
    'small': Scale(100, 20, 3, 500, 150, 60, 200, 30),
    'medium': Scale(1000, 20, 3, 3000, 330, 110, 500, 60),
    'large': Scale(5000, 30, 4, 6000, 330, 110, 500, 90),
    # production's shape (2026-09): 6330 users, ~40k plans, ~8.8k empires; most users small, power users up to 50 plans
    'prod': Scale(6330, 50, 5, 6000, 330, 110, 500, 90, small_user_share=0.865),
}


def random_field_values(model: type[models.Model], rng: random.Random, skip: set[str]) -> dict[str, FieldValue]:
    """Random values for choice fields and for booleans, floats and integers without a default."""
    values: dict[str, FieldValue] = {}
    for field in model._meta.concrete_fields:
        if field.name in skip or field.primary_key or field.is_relation:
            continue
        if field.choices:
            values[field.name] = rng.choice([str(value) for value, _label in field.flatchoices])
        elif field.has_default():
            continue
        elif isinstance(field, models.BooleanField):
            values[field.name] = rng.random() < 0.5
        elif isinstance(field, models.FloatField):
            values[field.name] = round(rng.uniform(0, 100), 4)
        elif isinstance(field, models.IntegerField):
            values[field.name] = rng.randint(0, 1000)
    return values


def unique_tickers(rng: random.Random, count: int) -> list[str]:
    pool = [''.join(chars) for chars in itertools.product(string.ascii_uppercase, repeat=3)]
    return rng.sample(pool, count)


class Seeder:
    def __init__(self, scale: Scale, seed: int) -> None:
        self.scale = scale
        self.rng = random.Random(seed)
        self.counts: dict[str, int] = {}
        self.material_tickers: list[str] = []
        self.recipes: dict[str, Recipe] = {}  # by plan recipe id, BUILDING#recipe_name
        self.recipes_by_building: dict[str, list[str]] = {}
        self.planet_natural_ids: list[str] = []
        self.planet_names: list[str] = []
        self.hot_planets: list[str] = []

    def _bulk[M: models.Model](self, model: type[M], objs: list[M]) -> list[M]:
        created = model.objects.bulk_create(objs, batch_size=BATCH_SIZE)
        self.counts[model.__name__] = self.counts.get(model.__name__, 0) + len(created)
        return created

    def _hex(self) -> str:
        return uuid.UUID(int=self.rng.getrandbits(128)).hex

    def _add_recipe(self, building_ticker: str, recipe_name: str, recipe: Recipe) -> None:
        recipe_id = f'{building_ticker}#{recipe_name}'
        self.recipes[recipe_id] = recipe
        self.recipes_by_building.setdefault(building_ticker, []).append(recipe_id)

    # game data from the FIO snapshot

    def seed_snapshot_gamedata(self, snapshot: GamedataSnapshot) -> None:
        # the production importers, so the field mapping is the one real imports use
        save_materials(snapshot.materials)
        save_buildings(snapshot.buildings)
        save_recipes(snapshot.recipes)
        save_planets(snapshot.planets)
        save_exchanges(snapshot.exchanges)
        for model in (
            GameMaterial,
            GameBuilding,
            GameBuildingCost,
            GameRecipe,
            GameRecipeInput,
            GameRecipeOutput,
            GamePlanet,
            GamePlanetResource,
            GamePlanetProductionFee,
            GamePlanetCOGCProgram,
            GameExchange,
        ):
            self.counts[model.__name__] = model.objects.count()

        # sorted, so the random draws below do not depend on FIO's response order
        self.material_tickers = sorted(m.ticker for m in snapshot.materials)
        for r in sorted(snapshot.recipes, key=lambda r: (r.building_ticker, r.recipe_name)):
            inputs = [(i.material_ticker, i.material_amount) for i in r.inputs]
            outputs = [(o.material_ticker, o.material_amount) for o in r.outputs]
            self._add_recipe(r.building_ticker, r.recipe_name, Recipe(r.time_ms, inputs, outputs))
        self.planet_natural_ids = sorted(p.planet_natural_id for p in snapshot.planets)
        # FIO names unnamed planets by their natural id
        self.planet_names = sorted(
            p.planet_name for p in snapshot.planets if p.planet_name and p.planet_name != p.planet_natural_id
        )

        prices: dict[str, list[float]] = defaultdict(list)
        for e in snapshot.exchanges:
            if e.price_average > 0:
                prices[e.ticker].append(e.price_average)
        base_prices = {
            t: sum(p) / len(p) if (p := prices.get(t)) else self.rng.uniform(10, 5000) for t in self.material_tickers
        }

        real_history = {
            (ticker, snapshot.cxpc_exchange): cxpc_objects(ticker, snapshot.cxpc_exchange, rows)
            for ticker, rows in sorted(snapshot.cxpc.items())
        }
        self.seed_price_history(base_prices, real_history)

    # fake game data

    def seed_fake_gamedata(self) -> None:
        self.seed_materials()
        self.seed_buildings_and_recipes()
        self.seed_planets()
        self.seed_price_history(self.seed_exchanges(), real={})

    def seed_materials(self) -> None:
        self.material_tickers = unique_tickers(self.rng, self.scale.materials)
        self._bulk(
            GameMaterial,
            [
                GameMaterial(
                    material_id=self._hex(),
                    category_name=f'category {i % 20}',
                    category_id=f'cat{i % 20}',
                    name=f'Material {ticker}',
                    ticker=ticker,
                    weight=round(self.rng.uniform(0.01, 10), 3),
                    volume=round(self.rng.uniform(0.01, 10), 3),
                )
                for i, ticker in enumerate(self.material_tickers)
            ],
        )

    def seed_buildings_and_recipes(self) -> None:
        building_tickers = unique_tickers(self.rng, self.scale.buildings)
        buildings = self._bulk(
            GameBuilding,
            [
                GameBuilding(
                    building_id=self._hex(),
                    building_name=f'Building {ticker}',
                    building_ticker=ticker,
                    expertise=self.rng.choice(GameBuildingExpertiseChoices.values),
                    pioneers=self.rng.randint(0, 100),
                    settlers=self.rng.randint(0, 100),
                    technicians=self.rng.randint(0, 50),
                    engineers=self.rng.randint(0, 30),
                    scientists=self.rng.randint(0, 20),
                    area_cost=self.rng.randint(5, 50),
                )
                for ticker in building_tickers
            ],
        )
        self._bulk(
            GameBuildingCost,
            [
                GameBuildingCost(building=building, material_ticker=ticker, material_amount=self.rng.randint(1, 50))
                for building in buildings
                for ticker in self.rng.sample(self.material_tickers, self.rng.randint(2, 5))
            ],
        )

        recipes: list[GameRecipe] = []
        ios: list[Recipe] = []
        for i in range(self.scale.recipes):
            building_ticker = self.rng.choice(building_tickers)
            recipe = Recipe(
                time_ms=self.rng.randint(1, 48) * 3_600_000,
                inputs=[
                    (t, self.rng.randint(1, 20)) for t in self.rng.sample(self.material_tickers, self.rng.randint(1, 3))
                ],
                outputs=[
                    (t, self.rng.randint(1, 10)) for t in self.rng.sample(self.material_tickers, self.rng.randint(1, 2))
                ],
            )
            self._add_recipe(building_ticker, f'R{i}', recipe)
            ios.append(recipe)
            recipes.append(
                GameRecipe(
                    standard_recipe_name=f'{building_ticker}#R{i}',
                    recipe_name=f'R{i}',
                    building_ticker=building_ticker,
                    time_ms=recipe.time_ms,
                )
            )
        recipes = self._bulk(GameRecipe, recipes)
        self._bulk(
            GameRecipeInput,
            [
                GameRecipeInput(recipe=recipe, material_ticker=ticker, material_amount=amount)
                for recipe, io in zip(recipes, ios, strict=True)
                for ticker, amount in io.inputs
            ],
        )
        self._bulk(
            GameRecipeOutput,
            [
                GameRecipeOutput(recipe=recipe, material_ticker=ticker, material_amount=amount)
                for recipe, io in zip(recipes, ios, strict=True)
                for ticker, amount in io.outputs
            ],
        )

    def seed_planets(self) -> None:
        natural_ids: set[str] = set()
        while len(natural_ids) < self.scale.planets:
            prefix = ''.join(self.rng.choices(string.ascii_uppercase, k=2))
            natural_ids.add(f'{prefix}-{self.rng.randint(0, 999):03d}{self.rng.choice("abcdefg")}')
        self.planet_natural_ids = sorted(natural_ids)

        planets: list[GamePlanet] = []
        for natural_id in self.planet_natural_ids:
            name = f'Planet {natural_id[:2]}{self.rng.randint(0, 9999)}' if self.rng.random() < 0.3 else ''
            if name:
                self.planet_names.append(name)
            planets.append(
                GamePlanet(
                    planet_id=self._hex(),
                    planet_natural_id=natural_id,
                    planet_name=name,
                    system_id=self._hex(),
                    population_id=self._hex(),
                    **random_field_values(GamePlanet, self.rng, skip={'automation_refresh_status'}),
                )
            )
        planets = self._bulk(GamePlanet, planets)

        now_ms = int(time.time() * 1000)
        fee_slots = [
            (category, level)
            for category in GameBuildingExpertiseChoices.values
            for level in GamePlanetWorkforceLevelChoices.values
        ]
        resources: list[GamePlanetResource] = []
        fees: list[GamePlanetProductionFee] = []
        programs: list[GamePlanetCOGCProgram] = []
        reports: list[GamePlanetInfrastructureReport] = []
        for planet in planets:
            for ticker in self.rng.sample(self.material_tickers, self.rng.randint(0, 4)):
                resources.append(
                    GamePlanetResource(
                        planet=planet,
                        material_id=ticker.lower(),
                        material_ticker=ticker,
                        daily_extraction=round(self.rng.uniform(0, 30), 3),
                        max_daily_extraction=round(self.rng.uniform(30, 60), 3),
                        **random_field_values(GamePlanetResource, self.rng, skip=set()),
                    )
                )
            for category, level in self.rng.sample(fee_slots, 5):
                fees.append(
                    GamePlanetProductionFee(
                        planet=planet,
                        category=category,
                        workforce_level=level,
                        **random_field_values(GamePlanetProductionFee, self.rng, skip={'category', 'workforce_level'}),
                    )
                )
            # one past program, and an active one on half of the planets
            programs.append(
                GamePlanetCOGCProgram(
                    planet=planet,
                    program_type=self.rng.choice(GamePlanetCOGCProgramChoices.values),
                    start_epochms=now_ms - 14 * DAY_MS,
                    end_epochms=now_ms - 7 * DAY_MS,
                )
            )
            if self.rng.random() < 0.5:
                programs.append(
                    GamePlanetCOGCProgram(
                        planet=planet,
                        program_type=self.rng.choice(GamePlanetCOGCProgramChoices.values),
                        start_epochms=now_ms - 7 * DAY_MS,
                        end_epochms=now_ms + 7 * DAY_MS,
                    )
                )
            if self.rng.random() < 0.5:
                reports.append(
                    GamePlanetInfrastructureReport(
                        planet=planet,
                        infrastructure_report_id=self._hex(),
                        simulation_period=self.rng.randint(1, 500),
                        **random_field_values(
                            GamePlanetInfrastructureReport,
                            self.rng,
                            skip={'infrastructure_report_id', 'simulation_period'},
                        ),
                    )
                )
        self._bulk(GamePlanetResource, resources)
        self._bulk(GamePlanetProductionFee, fees)
        self._bulk(GamePlanetCOGCProgram, programs)
        self._bulk(GamePlanetInfrastructureReport, reports)

    def seed_exchanges(self) -> dict[str, float]:
        """Fake live exchange data; returns each material's base price."""
        base_prices: dict[str, float] = {}
        exchanges: list[GameExchange] = []
        for ticker in self.material_tickers:
            base_price = base_prices[ticker] = self.rng.uniform(10, 5000)
            for code in LIVE_EXCHANGES:
                price = round(base_price * self.rng.uniform(0.8, 1.2), 2)
                exchanges.append(
                    GameExchange(
                        ticker_id=f'{ticker}.{code}',
                        ticker=ticker,
                        exchange_code=code,
                        mm_buy=None,
                        mm_sell=None,
                        price_average=price,
                        ask=round(price * 1.05, 2),
                        bid=round(price * 0.95, 2),
                        ask_count=self.rng.randint(0, 50),
                        bid_count=self.rng.randint(0, 50),
                        supply=self.rng.randint(0, 100_000),
                        demand=self.rng.randint(0, 100_000),
                    )
                )
        self._bulk(GameExchange, exchanges)
        return base_prices

    def seed_price_history(
        self, base_prices: dict[str, float], real: dict[tuple[str, str], list[GameExchangeCXPC]]
    ) -> None:
        """Fake daily price history around the base prices, except for the (ticker, exchange) pairs in real."""
        history: list[GameExchangeCXPC] = [row for rows in real.values() for row in rows]
        today = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        for ticker in self.material_tickers:
            for code in CXPC_EXCHANGES:
                if (ticker, code) in real:
                    continue
                for day in range(self.scale.cxpc_days):
                    price = Decimal(str(round(base_prices[ticker] * self.rng.uniform(0.8, 1.2), 4)))
                    traded = Decimal(self.rng.randint(0, 5000))
                    history.append(
                        GameExchangeCXPC(
                            ticker=ticker,
                            exchange_code=code,
                            date_epoch=int((today - timedelta(days=day)).timestamp() * 1000),
                            open_p=price,
                            close_p=price,
                            high_p=price * Decimal('1.05'),
                            low_p=price * Decimal('0.95'),
                            volume=price * traded,
                            traded=traded,
                        )
                    )
        self._bulk(GameExchangeCXPC, history)

        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('REFRESH MATERIALIZED VIEW prunplanner_game_exchanges_analytics;')

    # users and planning

    def _plan_data(self) -> dict[str, JSONValue]:
        building_tickers = list(self.recipes_by_building)
        buildings: list[JSONValue] = []
        for ticker in self.rng.sample(building_tickers, min(len(building_tickers), self.rng.randint(2, 8))):
            recipes = self.recipes_by_building[ticker]
            buildings.append(
                {
                    'name': ticker,
                    'amount': self.rng.randint(1, 12),
                    'active_recipes': [
                        {'recipeid': recipe, 'amount': self.rng.randint(1, 3)}
                        for recipe in self.rng.sample(recipes, min(len(recipes), self.rng.randint(1, 3)))
                    ],
                }
            )
        data: dict[str, JSONValue] = {
            'experts': [{'type': t, 'amount': self.rng.randint(0, 5)} for t in EXPERT_TYPES],
            'workforce': [
                {'type': t, 'lux1': self.rng.random() < 0.5, 'lux2': self.rng.random() < 0.3} for t in WORKFORCE_TYPES
            ],
            'infrastructure': [{'building': b, 'amount': self.rng.randint(0, 10)} for b in INFRASTRUCTURE],
            'buildings': buildings,
        }
        # fail loudly if the fake plans drift from the current plan schema
        return LATEST_SCHEMA['PLANNING_DATA'].model_validate(data).model_dump(mode='json')

    def _plan_material_io(self, plan: PlanningPlan) -> dict[str, list[float]]:
        """Daily [production, consumption] per material.

        ponytail: runs every recipe back to back, no efficiency or workforce; the frontend computes the real state.
        """
        io: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for building in plan.plan_data['buildings']:
            for active in building['active_recipes']:
                recipe = self.recipes[active['recipeid']]
                if recipe.time_ms <= 0:
                    continue
                runs = DAY_MS / recipe.time_ms * building['amount'] * active['amount']
                for ticker, amount in recipe.outputs:
                    io[ticker][0] += amount * runs
                for ticker, amount in recipe.inputs:
                    io[ticker][1] += amount * runs
        return io

    def seed_users_and_planning(self) -> None:
        password_hash = make_password(PASSWORD)  # hashed once: bcrypt per user would dominate the seed time
        now = datetime.now(tz=UTC)
        self._bulk(
            User,
            [
                User(
                    username=f'{USERNAME_PREFIX}{i}',
                    email=f'{USERNAME_PREFIX}{i}@example.com',
                    password=password_hash,
                    is_email_verified=True,
                    # recently active, so the plan insights aggregation counts their plans
                    last_login=now - timedelta(days=self.rng.randint(0, 60)),
                )
                for i in range(self.scale.users)
            ],
        )
        users = list(User.objects.filter(username__startswith=USERNAME_PREFIX).order_by('id'))
        self._bulk(UserPreference, [UserPreference(user=user, preferences={}) for user in users])

        cx_by_user = {
            cx.user.pk: cx
            for cx in self._bulk(
                PlanningCX,
                [
                    PlanningCX(
                        user=user, cx_name=f'{user.username} CX', cx_data=LATEST_SCHEMA['CX_DATA']().model_dump()
                    )
                    for user in users
                ],
            )
        }

        self.hot_planets = self.rng.sample(self.planet_natural_ids, min(HOT_PLANETS, len(self.planet_natural_ids)))
        plans: list[PlanningPlan] = []
        empires: list[PlanningEmpire] = []
        for index, user in enumerate(users):
            # the first user is the power user every benchmark runs as
            if index == 0:
                plan_count = self.scale.max_plans_per_user * 3
                empire_count = self.scale.max_empires_per_user + 2
            elif self.scale.small_user_share and self.rng.random() < self.scale.small_user_share:
                plan_count = self.rng.randint(1, 5)
                empire_count = 1
            else:
                plan_count = self.rng.randint(0, self.scale.max_plans_per_user)
                empire_count = self.rng.randint(1, self.scale.max_empires_per_user)

            for p in range(plan_count):
                planets = self.hot_planets if self.rng.random() < HOT_PLANET_SHARE else self.planet_natural_ids
                plans.append(
                    PlanningPlan(
                        user=user,
                        plan_name=f'Plan {p:03d}',
                        planet_natural_id=self.rng.choice(planets),
                        plan_permits_used=self.rng.randint(1, 3),
                        plan_cogc=self.rng.choice(PlanningCOGCChoices.values),
                        plan_corphq=self.rng.random() < 0.1,
                        plan_data=self._plan_data(),
                    )
                )
            for e in range(empire_count):
                permits_total = self.rng.randint(2, 6)
                empires.append(
                    PlanningEmpire(
                        user=user,
                        cx=cx_by_user[user.pk] if self.rng.random() < 0.5 else None,
                        empire_name=f'Empire {e}',
                        empire_faction=self.rng.choice(PlanningFactionChoices.values),
                        empire_permits_used=self.rng.randint(1, permits_total),
                        empire_permits_total=permits_total,
                    )
                )
        plans = self._bulk(PlanningPlan, plans)
        empires = self._bulk(PlanningEmpire, empires)

        empires_by_user: dict[int, list[PlanningEmpire]] = {}
        for empire in empires:
            empires_by_user.setdefault(empire.user.pk, []).append(empire)

        links: list[PlanningEmpirePlan] = []
        shares: list[PlanningShared] = []
        for plan in plans:
            user = plan.user
            user_empires = empires_by_user[user.pk]
            for empire in self.rng.sample(user_empires, min(len(user_empires), self.rng.randint(0, 2))):
                links.append(PlanningEmpirePlan(user=user, empire=empire, plan=plan))
            if self.rng.random() < 0.1:
                shares.append(PlanningShared(user=user, plan=plan, view_count=self.rng.randint(0, 500)))
        self._bulk(PlanningEmpirePlan, links)
        self._bulk(PlanningShared, shares)
        self.seed_empire_states(links)

    def seed_insights_planet(self) -> None:
        """INSIGHTS_USERS users with 2 plans each on one planet, all running FRM in two recipe mixes."""
        rng_state = self.rng.getstate()  # leave the rest of the seeded data and the targets as they were
        planet = INSIGHTS_PLANET if INSIGHTS_PLANET in self.planet_natural_ids else self.hot_planets[0]
        by_building = self.recipes_by_building
        ticker = 'FRM' if 'FRM' in by_building else max(by_building, key=lambda t: len(by_building[t]))
        # two mixes {r0, r1} and {r0, r2}; fewer recipes (tiny test snapshots) collapse into one
        r0, r1, r2 = (by_building[ticker][i % len(by_building[ticker])] for i in range(3))
        now = datetime.now(tz=UTC)
        self._bulk(
            User,
            [
                User(
                    username=f'{INSIGHTS_USERNAME_PREFIX}{i}',
                    email=f'{INSIGHTS_USERNAME_PREFIX}{i}@example.com',
                    password=make_password(None),
                    is_email_verified=True,
                    last_login=now,
                )
                for i in range(INSIGHTS_USERS)
            ],
        )
        users = User.objects.filter(username__startswith=INSIGHTS_USERNAME_PREFIX).order_by('id')
        plans: list[PlanningPlan] = []
        for user in users:
            for p, recipes in enumerate(({r0: 2, r1: 1}, {r0: 1, r2: 3})):
                data = self._plan_data()
                buildings = [b for b in cast(list[dict], data['buildings']) if b['name'] != ticker]
                buildings.append(
                    {
                        'name': ticker,
                        'amount': self.rng.randint(3, 6),
                        'active_recipes': [{'recipeid': r, 'amount': a} for r, a in recipes.items()],
                    }
                )
                plans.append(
                    PlanningPlan(
                        user=user,
                        plan_name=f'Insights {p}',
                        planet_natural_id=planet,
                        plan_permits_used=1,
                        plan_cogc=self.rng.choice(PlanningCOGCChoices.values),
                        plan_data=data | {'buildings': buildings},
                    )
                )
        self._bulk(PlanningPlan, plans)
        self.rng.setstate(rng_state)

    def seed_empire_states(self, links: list[PlanningEmpirePlan]) -> None:
        """The material totals the frontend syncs for each empire, flagged for the analytics snapshot."""
        totals: dict[PlanningEmpire, dict[str, list[float]]] = {}
        for link in links:
            empire_total = totals.setdefault(link.empire, defaultdict(lambda: [0.0, 0.0]))
            for ticker, (produced, consumed) in self._plan_material_io(link.plan).items():
                empire_total[ticker][0] += produced
                empire_total[ticker][1] += consumed
        for empire, empire_total in totals.items():
            empire.empire_state = {
                'empire_total': {t: {'p': p, 'c': c, 'd': p - c} for t, (p, c) in sorted(empire_total.items())}
            }
            empire.needs_state_sync = True
        PlanningEmpire.objects.bulk_update(list(totals), ['empire_state', 'needs_state_sync'], batch_size=BATCH_SIZE)

    def run_analytics(self) -> None:
        """The scheduled aggregations, so the analytics endpoints serve the seeded plans."""
        analytics_update_plan_insight_aggregates()
        analytics_bulk_materialize_empire_snapshots()
        for model in (AnalyticsPlanAggregate, AnalyticsEmpireMaterialSnapshot):
            self.counts[model.__name__] = model.objects.count()

    def targets(self, scale_name: str, source: str) -> dict[str, JSONValue]:
        """What the load test needs to address the seeded data."""
        others = [p for p in self.planet_natural_ids if p not in set(self.hot_planets)]
        return {
            'scale': scale_name,
            'source': source,
            'users': self.scale.users,
            'username_prefix': USERNAME_PREFIX,
            'password': PASSWORD,
            'planets': [*self.hot_planets, *self.rng.sample(others, min(200 - len(self.hot_planets), len(others)))],
            'planet_names': list(self.planet_names[:50]),
            'materials': list(self.rng.sample(self.material_tickers, min(50, len(self.material_tickers)))),
            'scale_config': asdict(self.scale),
        }


class Command(BaseCommand):
    help = 'Seed the perf database with deterministic data (PERF_MODE only).'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('--scale', choices=list(SCALES), default='small')
        parser.add_argument('--seed', type=int, default=42, help='Random seed; the same seed gives the same data.')
        parser.add_argument(
            '--source',
            choices=['auto', 'snapshot', 'fake'],
            default='auto',
            help='Game data: the FIO snapshot (perf_snapshot), fake, or the snapshot if there is one (default).',
        )
        parser.add_argument('--snapshot', type=Path, default=SNAPSHOT_PATH, help='The snapshot file.')
        parser.add_argument('--flush', action='store_true', help='Empty the whole database first.')
        parser.add_argument('--targets-out', type=Path, help='Write usernames, planet ids etc. for the load test.')

    def handle(self, *args: object, **options: object) -> None:
        if not getattr(settings, 'PERF_MODE', False):
            raise CommandError('seed_perf only runs with DJANGO_SETTINGS_MODULE=core.config.django.perf')

        scale_name = str(options['scale'])
        seed = options['seed']
        snapshot_path = options['snapshot']
        if not isinstance(seed, int) or not isinstance(snapshot_path, Path):
            raise CommandError('--seed must be an integer and --snapshot a path')

        source = str(options['source'])
        if source == 'auto':
            source = 'snapshot' if snapshot_path.exists() else 'fake'
        if source == 'snapshot' and not snapshot_path.exists():
            raise CommandError(f'No snapshot at {snapshot_path}; run perf_snapshot first or pass --source fake.')
        snapshot = GamedataSnapshot.load(snapshot_path) if source == 'snapshot' else None

        if options['flush']:
            call_command('flush', interactive=False, verbosity=0)
        elif User.objects.filter(username__startswith=USERNAME_PREFIX).exists():
            raise CommandError('The database is already seeded; pass --flush to start over.')

        started = time.perf_counter()
        seeder = Seeder(SCALES[scale_name], seed)
        with transaction.atomic():
            if snapshot is None:
                seeder.seed_fake_gamedata()
            else:
                seeder.seed_snapshot_gamedata(snapshot)
            seeder.seed_users_and_planning()
            seeder.seed_insights_planet()
            seeder.run_analytics()

        for model_name, count in seeder.counts.items():
            self.stdout.write(f'  {model_name:<32} {count:>9,}')
        from_snapshot = f' from the {snapshot.downloaded_at:%Y-%m-%d} FIO snapshot' if snapshot else ' (fake game data)'
        self.stdout.write(
            self.style.SUCCESS(f'Seeded scale={scale_name}{from_snapshot} in {time.perf_counter() - started:.1f}s')
        )

        targets_out = options.get('targets_out')
        if isinstance(targets_out, Path):
            targets_out.parent.mkdir(parents=True, exist_ok=True)
            targets_out.write_bytes(orjson.dumps(seeder.targets(scale_name, source), option=orjson.OPT_INDENT_2))
