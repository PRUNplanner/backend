from pathlib import Path

import orjson
import pytest
from analytics.models import AnalyticsEmpireMaterialSnapshot, AnalyticsPlanAggregate
from analytics.services.planinsight_aggregator_service import PlanInsightAggregatorService
from core.management.commands.seed_perf import SCALES, USERNAME_PREFIX
from django.core.management import call_command
from django.core.management.base import CommandError
from gamedata.models import GameExchange, GameExchangeCXPC, GameMaterial, GamePlanet, GameRecipe
from planning.models import PlanningCX, PlanningEmpire, PlanningEmpirePlan, PlanningPlan
from planning.schemas.latest_schemas import LATEST_SCHEMA
from pytest_django.fixtures import SettingsWrapper
from user.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def perf_mode(settings: SettingsWrapper) -> SettingsWrapper:
    settings.PERF_MODE = True
    return settings


class TestSeedPerf:
    def test_refuses_to_run_outside_perf_mode(self) -> None:
        with pytest.raises(CommandError, match='core.config.django.perf'):
            call_command('seed_perf', '--scale', 'tiny')

        assert not User.objects.exists()

    def test_seeds_the_requested_scale(self, perf_mode: SettingsWrapper, tmp_path: Path) -> None:
        targets_path = tmp_path / 'targets.json'
        scale = SCALES['tiny']

        call_command('seed_perf', '--scale', 'tiny', '--targets-out', str(targets_path))

        assert User.objects.filter(username__startswith=USERNAME_PREFIX).count() == scale.users
        assert GamePlanet.objects.count() == scale.planets
        assert GameMaterial.objects.count() == scale.materials
        assert GameExchange.objects.count() == scale.materials * 4
        assert PlanningEmpire.objects.exists()
        assert PlanningEmpirePlan.objects.exists()

        # the first user is the power user the benchmarks run as
        power_user = User.objects.get(username=f'{USERNAME_PREFIX}0')
        assert PlanningPlan.objects.filter(user=power_user).count() == scale.max_plans_per_user * 3
        assert power_user.check_password(orjson.loads(targets_path.read_bytes())['password'])

    def test_plans_match_the_current_plan_schema(self, perf_mode: SettingsWrapper) -> None:
        call_command('seed_perf', '--scale', 'tiny')

        for plan in PlanningPlan.objects.all():
            LATEST_SCHEMA['PLANNING_DATA'].model_validate(plan.plan_data)

    def test_one_planet_passes_the_planet_insights_thresholds(self, perf_mode: SettingsWrapper) -> None:
        call_command('seed_perf', '--scale', 'tiny')

        aggregate = AnalyticsPlanAggregate.objects.get(total_users__gte=10)
        top = aggregate.insights_data['buildings'][0]
        assert top['users'] >= 10
        assert len(top['mixes']) == 2
        # the frontend rejects a plan without a COGC value
        assert not PlanningPlan.objects.filter(plan_cogc='').exists()

    def test_cx_data_has_every_field_of_the_current_cx_schema(self, perf_mode: SettingsWrapper) -> None:
        # the API returns cx_data as stored, and the frontend requires every field
        call_command('seed_perf', '--scale', 'tiny')

        fields = set(LATEST_SCHEMA['CX_DATA'].model_fields)
        assert PlanningCX.objects.exists()
        for cx in PlanningCX.objects.all():
            assert set(cx.cx_data) == fields
            LATEST_SCHEMA['CX_DATA'].model_validate(cx.cx_data)

    def test_same_seed_gives_same_targets(self, perf_mode: SettingsWrapper, tmp_path: Path) -> None:
        first, second = tmp_path / 'first.json', tmp_path / 'second.json'

        call_command('seed_perf', '--scale', 'tiny', '--seed', '7', '--targets-out', str(first))
        call_command('seed_perf', '--scale', 'tiny', '--seed', '7', '--flush', '--targets-out', str(second))

        assert first.read_bytes() == second.read_bytes()

    def test_refuses_to_seed_twice_without_flush(self, perf_mode: SettingsWrapper) -> None:
        call_command('seed_perf', '--scale', 'tiny')

        with pytest.raises(CommandError, match='--flush'):
            call_command('seed_perf', '--scale', 'tiny')


@pytest.mark.usefixtures('locmem_cache')  # the FIO importers purge cache patterns
class TestSeedPerfFromSnapshot:
    def test_loads_the_snapshot_through_the_importers(
        self, perf_mode: SettingsWrapper, snapshot_path: Path, tmp_path: Path
    ) -> None:
        targets_path = tmp_path / 'targets.json'

        call_command(
            'seed_perf', '--scale', 'tiny', '--source', 'snapshot', '--snapshot', str(snapshot_path),
            '--targets-out', str(targets_path),
        )  # fmt: skip

        # all of the snapshot's game data, whatever the scale
        assert GameMaterial.objects.count() == 6
        assert GameRecipe.objects.count() == 3
        assert set(GamePlanet.objects.values_list('planet_natural_id', flat=True)) == {'OT-580b', 'OT-580c'}
        montem = GamePlanet.objects.get(planet_natural_id='OT-580b')
        assert set(montem.resources.values_list('material_ticker', flat=True)) == {'H2O', 'NE', 'LST', 'FEO'}
        assert GameExchange.objects.get(ticker_id='DW.AI1').price_average == 80.0
        # the scale still sets the users
        assert User.objects.filter(username__startswith=USERNAME_PREFIX).count() == SCALES['tiny'].users

        targets = orjson.loads(targets_path.read_bytes())
        assert targets['source'] == 'snapshot'
        assert targets['planet_names'] == ['Montem']

    def test_plans_use_real_planets_buildings_and_recipes(
        self, perf_mode: SettingsWrapper, snapshot_path: Path
    ) -> None:
        call_command('seed_perf', '--scale', 'tiny', '--source', 'snapshot', '--snapshot', str(snapshot_path))

        recipe_ids = {f'{r.building_ticker}#{r.recipe_name}' for r in GameRecipe.objects.all()}
        for plan in PlanningPlan.objects.all():
            LATEST_SCHEMA['PLANNING_DATA'].model_validate(plan.plan_data)
            assert plan.planet_natural_id in {'OT-580b', 'OT-580c'}
            for building in plan.plan_data['buildings']:
                assert building['name'] in {'FP', 'BMP'}
                assert {r['recipeid'] for r in building['active_recipes']} <= recipe_ids

    def test_real_price_history_replaces_the_fake_one_for_sampled_tickers(
        self, perf_mode: SettingsWrapper, snapshot_path: Path
    ) -> None:
        call_command('seed_perf', '--scale', 'tiny', '--source', 'snapshot', '--snapshot', str(snapshot_path))

        # the snapshot's one daily row; FIO's hourly row is dropped as in the real import
        real = GameExchangeCXPC.objects.filter(ticker='DW', exchange_code='AI1')
        assert list(real.values_list('date_epoch', flat=True)) == [1_750_000_000_000]
        assert GameExchangeCXPC.objects.filter(ticker='DW', exchange_code='NC1').count() == SCALES['tiny'].cxpc_days

    def test_runs_the_analytics_aggregation(
        self, perf_mode: SettingsWrapper, snapshot_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # tiny has too few plans per planet for the production threshold
        monkeypatch.setattr(PlanInsightAggregatorService, 'MIN_PLANS_THRESHOLD', 1)

        call_command('seed_perf', '--scale', 'tiny', '--source', 'snapshot', '--snapshot', str(snapshot_path))

        assert AnalyticsPlanAggregate.objects.exists()
        assert set(AnalyticsEmpireMaterialSnapshot.objects.values_list('material_ticker', flat=True)) <= {
            'H2O', 'NE', 'LST', 'FEO', 'DW', 'RAT',
        }  # fmt: skip
        assert AnalyticsEmpireMaterialSnapshot.objects.filter(production__gt=0).exists()
        assert not PlanningEmpire.objects.filter(needs_state_sync=True).exists()

    def test_same_seed_gives_same_targets(
        self, perf_mode: SettingsWrapper, snapshot_path: Path, tmp_path: Path
    ) -> None:
        first, second = tmp_path / 'first.json', tmp_path / 'second.json'
        args = ['seed_perf', '--scale', 'tiny', '--source', 'snapshot', '--snapshot', str(snapshot_path)]

        call_command(*args, '--targets-out', str(first))
        call_command(*args, '--flush', '--targets-out', str(second))

        assert first.read_bytes() == second.read_bytes()

    def test_auto_uses_the_snapshot_when_there_is_one(
        self, perf_mode: SettingsWrapper, snapshot_path: Path, tmp_path: Path
    ) -> None:
        targets_path = tmp_path / 'targets.json'

        call_command(
            'seed_perf', '--scale', 'tiny', '--snapshot', str(snapshot_path), '--targets-out', str(targets_path)
        )

        assert orjson.loads(targets_path.read_bytes())['source'] == 'snapshot'
        assert GameMaterial.objects.count() == 6


class TestSeedPerfFallback:
    def test_auto_falls_back_to_fake_game_data(self, perf_mode: SettingsWrapper, tmp_path: Path) -> None:
        targets_path = tmp_path / 'targets.json'

        call_command(
            'seed_perf', '--scale', 'tiny', '--snapshot', str(tmp_path / 'missing.json.gz'),
            '--targets-out', str(targets_path),
        )  # fmt: skip

        assert orjson.loads(targets_path.read_bytes())['source'] == 'fake'
        assert GameMaterial.objects.count() == SCALES['tiny'].materials

    def test_source_snapshot_requires_the_file(self, perf_mode: SettingsWrapper, tmp_path: Path) -> None:
        with pytest.raises(CommandError, match='perf_snapshot'):
            call_command('seed_perf', '--source', 'snapshot', '--snapshot', str(tmp_path / 'missing.json.gz'))

        assert not User.objects.exists()

    def test_fake_plans_reference_seeded_recipes(self, perf_mode: SettingsWrapper) -> None:
        call_command('seed_perf', '--scale', 'tiny', '--source', 'fake')

        recipe_ids = {f'{r.building_ticker}#{r.recipe_name}' for r in GameRecipe.objects.all()}
        for plan in PlanningPlan.objects.all():
            for building in plan.plan_data['buildings']:
                assert {r['recipeid'] for r in building['active_recipes']} <= recipe_ids
