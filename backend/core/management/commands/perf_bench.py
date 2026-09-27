"""
Benchmark API endpoints in-process against the seeded perf database.

For every endpoint it records the SQL query count, the response size and the
latency with a cold cache (cache cleared before each request, so the database
path runs) and a warm cache. Only runs under PERF_MODE. See perf/README.md.
"""

import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import orjson
from analytics.models import AnalyticsPlanAggregate
from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection
from django.db.models import Count
from django.http import HttpResponse
from django.test import Client
from django.test.utils import CaptureQueriesContext
from gamedata.models import GameMaterial, GamePlanet, GamePlanetResource
from planning.models import PlanningEmpire, PlanningPlan
from rest_framework_simplejwt.tokens import AccessToken
from user.models import User

from core.management.commands.seed_perf import USERNAME_PREFIX

type Stats = dict[str, float]
type EndpointResult = dict[str, int | str | Stats | None]


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    path: str
    body: list[str] | dict[str, list[str] | bool] | None = None
    auth: bool = False


def summarize(durations_ms: list[float]) -> Stats:
    ordered = sorted(durations_ms)
    p95_index = max(0, round(0.95 * len(ordered)) - 1)
    return {
        'median_ms': round(statistics.median(ordered), 2),
        'p95_ms': round(ordered[p95_index], 2),
        'min_ms': round(ordered[0], 2),
        'mean_ms': round(statistics.fmean(ordered), 2),
    }


def build_endpoints() -> list[Endpoint]:
    """The endpoints the frontend hits most, addressed at the seeded power user and data."""
    user = User.objects.filter(username=f'{USERNAME_PREFIX}0').first()
    if user is None:
        raise CommandError('No seeded data found; run seed_perf first.')

    plan = PlanningPlan.objects.filter(user=user).order_by('plan_name').first()
    empire = PlanningEmpire.objects.filter(user=user).order_by('empire_name').first()
    planet_ids = list(
        GamePlanet.objects.filter(resources__isnull=False)
        .order_by('planet_natural_id')
        .values_list('planet_natural_id', flat=True)
        .distinct()[:20]
    )
    ticker = GameMaterial.objects.order_by('ticker').values_list('ticker', flat=True).first()
    if plan is None or empire is None or not planet_ids or ticker is None:
        raise CommandError('Seeded data is incomplete; run seed_perf --flush again.')

    planet = planet_ids[0]
    # a planet with enough plans for insights, else the below-threshold answer
    insights_planet = (
        AnalyticsPlanAggregate.objects.order_by('planet_natural_id').values_list('planet_natural_id', flat=True).first()
        or planet
    )
    # the most common resource on planets the search below can match (normal gravity, pressure, temperature)
    search_ticker = (
        GamePlanetResource.objects.filter(
            planet__gravity_type='NORMAL', planet__pressure_type='NORMAL', planet__temperature_type='NORMAL'
        )
        .exclude(material_ticker=None)
        .values('material_ticker')
        .annotate(planets=Count('planet'))
        .order_by('-planets', 'material_ticker')
        .values_list('material_ticker', flat=True)
        .first()
        or ticker
    )
    search: dict[str, list[str] | bool] = {
        'materials': [search_ticker],
        'cogc_programs': [],
        'must_be_fertile': False,
        **{
            f'environment_{env}': False
            for env in (
                'rocky',
                'gaseous',
                'low_gravity',
                'high_gravity',
                'low_pressure',
                'high_pressure',
                'low_temperature',
                'high_temperature',
            )
        },
        **{
            f'must_have_{infra}': False
            for infra in ('localmarket', 'chamberofcommerce', 'warehouse', 'administrationcenter', 'shipyard')
        },
    }

    return [
        # public game data
        Endpoint('data.materials', 'get', '/data/materials/'),
        Endpoint('data.recipes', 'get', '/data/recipes/'),
        Endpoint('data.buildings', 'get', '/data/buildings/'),
        Endpoint('data.exchanges', 'get', '/data/exchanges/'),
        Endpoint('data.planets', 'get', '/data/planets/'),
        Endpoint('data.planet', 'get', f'/data/planet/{planet}/'),
        Endpoint('data.planet_search_single', 'get', f'/data/planets/{planet[:4]}/'),
        Endpoint('data.planets_multiple', 'post', '/data/planets/multiple/', body=planet_ids),
        Endpoint('data.planets_search', 'post', '/data/planets/search/', body=search),
        Endpoint('data.cxpc_ticker', 'get', f'/data/cxpc/{ticker}/'),
        Endpoint('analytics.planet_insights', 'get', f'/analytics/planet_insights/{insights_planet}/'),
        Endpoint('analytics.materials', 'get', '/analytics/planning_insights/materials/'),
        # the power user's planning data
        Endpoint('user.profile', 'get', '/user/profile/', auth=True),
        Endpoint('user.preferences', 'get', '/user/preferences/', auth=True),
        Endpoint('planning.plan_list', 'get', '/planning/plan/', auth=True),
        Endpoint('planning.plan_detail', 'get', f'/planning/plan/{plan.uuid}/', auth=True),
        Endpoint('planning.empire_list', 'get', '/planning/empire/', auth=True),
        Endpoint('planning.empire_detail', 'get', f'/planning/empire/{empire.uuid}/', auth=True),
        Endpoint('planning.empire_plans', 'get', f'/planning/empire/{empire.uuid}/plans/', auth=True),
        Endpoint('planning.cx_list', 'get', '/planning/cx/', auth=True),
        Endpoint('planning.shared_list', 'get', '/planning/shared/', auth=True),
    ]


class Command(BaseCommand):
    help = 'Benchmark API endpoints against the seeded perf database (PERF_MODE only).'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('--out', type=Path, help='Write the results as JSON to this file.')
        parser.add_argument('--iterations', type=int, default=20)
        parser.add_argument('--warmup', type=int, default=2)
        parser.add_argument('--only', default='', help='Comma-separated name fragments, e.g. planning,data.planet')
        parser.add_argument('--show-sql', default='', help='Print the SQL of one cold request to this endpoint.')

    def handle(self, *args: object, **options: object) -> None:
        if not getattr(settings, 'PERF_MODE', False):
            raise CommandError('perf_bench only runs with DJANGO_SETTINGS_MODULE=core.config.django.perf')

        iterations = options['iterations']
        warmup = options['warmup']
        if not isinstance(iterations, int) or not isinstance(warmup, int) or iterations < 1 or warmup < 0:
            raise CommandError('--iterations must be >= 1 and --warmup >= 0')

        endpoints = build_endpoints()
        show_sql = str(options['show_sql'] or '')
        only = [part for part in str(options['only'] or '').split(',') if part]
        if show_sql:
            endpoints = [e for e in endpoints if e.name == show_sql]
            if not endpoints:
                raise CommandError(f'Unknown endpoint {show_sql!r}')
        elif only:
            endpoints = [e for e in endpoints if any(part in e.name for part in only)]

        client = Client(
            raise_request_exception=False, HTTP_ACCEPT_ENCODING='gzip'
        )  # a crashing endpoint is reported, not fatal
        token = str(AccessToken.for_user(User.objects.get(username=f'{USERNAME_PREFIX}0')))

        def call(endpoint: Endpoint) -> HttpResponse:
            headers = {'Authorization': f'Bearer {token}'} if endpoint.auth else {}
            if endpoint.method == 'post':
                response = client.post(
                    endpoint.path, data=endpoint.body, content_type='application/json', headers=headers
                )
            else:
                response = client.get(endpoint.path, headers=headers)
            assert isinstance(response, HttpResponse)  # not streamed, so .content is there
            return response

        if show_sql:
            cache.clear()
            with CaptureQueriesContext(connection) as ctx:
                response = call(endpoints[0])
            self.stdout.write(f'{endpoints[0].name}: HTTP {response.status_code}, {len(ctx.captured_queries)} queries')
            for index, query in enumerate(ctx.captured_queries, start=1):
                sql, seconds = query['sql'], query['time']
                self.stdout.write(f'\n-- {index} ({seconds}s)\n{sql}')
            return

        results: dict[str, EndpointResult] = {}
        for endpoint in endpoints:
            results[endpoint.name] = self.bench(endpoint, call, iterations, warmup)
            result = results[endpoint.name]
            cold, warm, queries, size = result['cold'], result['warm'], result['queries'], result['bytes']
            if isinstance(cold, dict) and isinstance(warm, dict):
                cold_ms, warm_ms = cold['median_ms'], warm['median_ms']
                self.stdout.write(
                    f'{endpoint.name:<30} {queries:>4} q  cold {cold_ms:>8.2f} ms  '
                    f'warm {warm_ms:>7.2f} ms  {size:>9,} B'
                )
            else:
                error = result['error']
                self.stdout.write(self.style.ERROR(f'{endpoint.name:<30} {error}'))

        cache.clear()
        out = options['out']
        if isinstance(out, Path):
            out.parent.mkdir(parents=True, exist_ok=True)
            payload = {'iterations': iterations, 'endpoints': results}
            out.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))

    @staticmethod
    def bench(
        endpoint: Endpoint, call: Callable[[Endpoint], HttpResponse], iterations: int, warmup: int
    ) -> EndpointResult:
        cold_ms: list[float] = []
        queries = 0
        response: HttpResponse | None = None
        for run in range(warmup + iterations):
            cache.clear()
            with CaptureQueriesContext(connection) as ctx:
                started = time.perf_counter()
                response = call(endpoint)
                elapsed = (time.perf_counter() - started) * 1000
            if response.status_code >= 400:
                detail = response.content[:200].decode(errors='replace')
                return {
                    'status': response.status_code,
                    'error': f'HTTP {response.status_code}: {detail}',
                    'queries': None,
                    'bytes': None,
                    'cold': None,
                    'warm': None,
                }
            if run >= warmup:
                cold_ms.append(elapsed)
                queries = len(ctx.captured_queries)

        # the last cold request primed the cache
        warm_ms: list[float] = []
        for _ in range(iterations):
            started = time.perf_counter()
            call(endpoint)
            warm_ms.append((time.perf_counter() - started) * 1000)

        assert response is not None
        return {
            'status': response.status_code,
            'error': None,
            'queries': queries,
            'bytes': len(response.content),
            'cold': summarize(cold_ms),
            'warm': summarize(warm_ms),
        }
