from pathlib import Path

import orjson
import pytest
from analytics.services.planinsight_aggregator_service import PlanInsightAggregatorService
from django.core.management import call_command
from django.core.management.base import CommandError
from pytest_django.fixtures import SettingsWrapper

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded(settings: SettingsWrapper) -> SettingsWrapper:
    settings.PERF_MODE = True
    call_command('seed_perf', '--scale', 'tiny')
    return settings


class TestPerfBench:
    def test_refuses_to_run_outside_perf_mode(self) -> None:
        with pytest.raises(CommandError, match='core.config.django.perf'):
            call_command('perf_bench')

    def test_requires_seeded_data(self, settings: SettingsWrapper) -> None:
        settings.PERF_MODE = True

        with pytest.raises(CommandError, match='seed_perf'):
            call_command('perf_bench')

    def test_writes_query_counts_and_timings(self, seeded: SettingsWrapper, tmp_path: Path) -> None:
        out = tmp_path / 'bench.json'

        call_command(
            'perf_bench', '--iterations', '2', '--warmup', '0', '--only', 'planning.plan,user.', '--out', str(out)
        )

        endpoints = orjson.loads(out.read_bytes())['endpoints']
        assert set(endpoints) == {'planning.plan_list', 'planning.plan_detail', 'user.profile', 'user.preferences'}
        plan_list = endpoints['planning.plan_list']
        assert plan_list['status'] == 200
        assert plan_list['error'] is None
        assert plan_list['queries'] > 0
        assert plan_list['bytes'] > 0
        assert set(plan_list['cold']) == {'median_ms', 'p95_ms', 'min_ms', 'mean_ms'}

    def test_show_sql_prints_the_queries(self, seeded: SettingsWrapper, capsys: pytest.CaptureFixture[str]) -> None:
        call_command('perf_bench', '--show-sql', 'planning.plan_list')

        output = capsys.readouterr().out
        assert output.startswith('planning.plan_list: HTTP 200')
        assert 'SELECT' in output

    def test_show_sql_rejects_unknown_endpoints(self, seeded: SettingsWrapper) -> None:
        with pytest.raises(CommandError, match='Unknown endpoint'):
            call_command('perf_bench', '--show-sql', 'nope')

    @pytest.mark.usefixtures('locmem_cache')
    def test_search_and_analytics_return_data_on_snapshot_data(
        self, settings: SettingsWrapper, snapshot_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings.PERF_MODE = True
        monkeypatch.setattr(PlanInsightAggregatorService, 'MIN_PLANS_THRESHOLD', 1)
        call_command('seed_perf', '--scale', 'tiny', '--source', 'snapshot', '--snapshot', str(snapshot_path))
        out = tmp_path / 'bench.json'

        call_command(
            'perf_bench',
            '--iterations',
            '1',
            '--warmup',
            '0',
            '--only',
            'data.planets_search,analytics.',
            '--out',
            str(out),
        )

        endpoints = orjson.loads(out.read_bytes())['endpoints']
        assert endpoints['data.planets_search']['bytes'] > len(b'[]')
        assert endpoints['analytics.materials']['bytes'] > len(b'[]')
        assert endpoints['analytics.planet_insights']['status'] == 200
