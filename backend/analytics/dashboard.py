"""
Admin dashboard: problems first, then headline numbers, trends, breakdowns, and infrastructure last.

Every card is built by its own function behind `safe()`: an exception is logged and the card renders "unavailable",
never a 500. The whole context is cached for 60 s per range (`admin:dashboard:<days>`); `?refresh=1` rebuilds it and
`?range=7|30|90|365` picks the window.
"""

import statistics
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import TypedDict, cast

import structlog
from core.admin import changelist_url, daily_counts, percent
from core.admin_charts import LIME, STATUS, Chart, Tile, bar, change, line, sparkline
from core.models import CeleryAutomationModel
from core.services.task_health import TaskHealth, is_overdue, task_health_rows
from django.core.cache import cache
from django.db import connection
from django.db.models import Count, Exists, F, IntegerField, Min, OuterRef, Q, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from django.utils.timesince import timesince
from django_celery_beat.models import PeriodicTask
from gamedata.models import GameFIOPlayerData, GamePlanet
from planning.models import PlanningCX, PlanningEmpire, PlanningEmpirePlan, PlanningPlan, PlanningShared
from planning.signup_defaults import SIGNUP_CX_DATA, SIGNUP_EMPIRE
from redis import Redis
from user.models import GlobalConfigWebhook, User, UserAPIKey
from user.models.user import FIO_LINKED_Q

from analytics.models import AppStatistic

logger = structlog.get_logger(__name__)

RANGES = (7, 30, 90, 365)
DEFAULT_RANGE = 30
CACHE_TTL_SECONDS = 60
LONG_QUERY_SECONDS = 30
TOP_LIST_SIZE = 10
# rows created this long after their owner's signup were created by the user, not seeded by signup
SIGNUP_SEED_WINDOW = timedelta(seconds=60)
API_KEY_ACTIVE_DAYS = 30
# plans per user histogram: (label, lowest count, highest count or None)
PLAN_BUCKETS = (('0', 0, 0), ('1', 1, 1), ('2–5', 2, 5), ('6–20', 6, 20), ('21+', 21, None))

type Row = dict[str, str | int | float | None]


class Chip(TypedDict):
    ok: bool
    icon: str
    colour: str
    text: str
    href: str


def safe[T](build: Callable[[], T]) -> T | None:
    """Fault isolation per card: a failing card logs and renders "unavailable"."""
    try:
        return build()
    except Exception:
        logger.exception('admin_dashboard_card_failed', card=getattr(build, '__name__', repr(build)))
        return None


def _age(since: date | None) -> str:
    return timesince(since).split(',')[0] if since else 'never'


# ---------------------------------------------------------------------------
# row 1: needs attention


def overdue_tasks_chip() -> Chip:
    now = timezone.now()
    overdue = [
        pt
        for pt in PeriodicTask.objects.filter(enabled=True).select_related('interval', 'crontab', 'solar', 'clocked')
        if is_overdue(pt, now)
    ]
    href = f'{reverse("admin:task_health")}?state=overdue'
    if not overdue:
        return {'ok': True, 'icon': 'check_circle', 'colour': STATUS['good'], 'text': 'No task overdue', 'href': href}
    first = overdue[0]
    text = f'Task overdue · {first.name} · {_age(first.last_run_at)} since last run'
    if len(overdue) > 1:
        text = f'{len(overdue)} tasks overdue · {first.name} and {len(overdue) - 1} more'
    return {'ok': False, 'icon': 'schedule', 'colour': STATUS['critical'], 'text': text, 'href': href}


def stuck_planets_chip() -> Chip:
    stuck = GamePlanet.objects.filter(CeleryAutomationModel.stuck_q()).aggregate(
        n=Count('pk'), oldest=Min('automation_last_refreshed_at')
    )
    href = changelist_url(GamePlanet, 'stuck=lease_expired')
    if not stuck['n']:
        return {'ok': True, 'icon': 'check_circle', 'colour': STATUS['good'], 'text': 'No planet stuck', 'href': href}
    return {
        'ok': False,
        'icon': 'hourglass_disabled',
        'colour': STATUS['critical'],
        'text': f'Planets stuck in pending · {stuck["n"]:,} · oldest refreshed {_age(stuck["oldest"])} ago',
        'href': href,
    }


# ---------------------------------------------------------------------------
# deliberate use: every signup gets an empire and a CX preference (`planning.signup_defaults`), so owning one says
# nothing. These count users who changed or added to what signup gave them, each as a correlated subquery on User.


def _owned_count(model: type[PlanningEmpire] | type[PlanningCX]) -> Coalesce:
    rows = model.objects.filter(user=OuterRef('pk')).order_by().values('user').annotate(n=Count('pk')).values('n')
    return Coalesce(Subquery(rows[:1], output_field=IntegerField()), Value(0))


def empire_set_up() -> QuerySet[User]:
    """Users with 2+ empires, or an empire whose name, faction or permits differ from the signup defaults."""
    changed = PlanningEmpire.objects.filter(user=OuterRef('pk')).exclude(**SIGNUP_EMPIRE)
    return User.objects.alias(empire_count=_owned_count(PlanningEmpire)).filter(
        Q(empire_count__gte=2) | Exists(changed)
    )


def empire_in_use() -> QuerySet[User]:
    """Users with an empire linked to 2+ plans."""
    busy = (
        PlanningEmpirePlan.objects.filter(user=OuterRef('pk'))
        .order_by()
        .values('empire')
        .annotate(n=Count('pk'))
        .filter(n__gte=2)
    )
    return User.objects.filter(Exists(busy))


def pricing_customised() -> QuerySet[User]:
    """Users with 2+ CX preferences, or one whose data differs from the signup default."""
    changed = PlanningCX.objects.filter(user=OuterRef('pk')).exclude(cx_data=SIGNUP_CX_DATA)
    return User.objects.alias(cx_count=_owned_count(PlanningCX)).filter(Q(cx_count__gte=2) | Exists(changed))


def created_after_signup(queryset: QuerySet) -> QuerySet:
    return queryset.filter(created_at__gt=F('user__date_joined') + SIGNUP_SEED_WINDOW)


# ---------------------------------------------------------------------------
# rows 2 and 3: headline tiles and growth, from the daily AppStatistic snapshots


def _window(days: int) -> tuple[list[AppStatistic], AppStatistic | None]:
    start = timezone.now().date() - timedelta(days=days)
    stats = list(AppStatistic.objects.filter(date__gt=start).order_by('date'))
    previous = AppStatistic.objects.filter(date__lte=start).order_by('-date').first()
    return stats, previous


def headline(days: int) -> dict[str, object]:
    stats, previous = _window(days)
    latest = stats[-1] if stats else None

    def tile(label: str, field: str, href: str) -> Tile:
        current = getattr(latest, field) if latest else None
        result: Tile = {'label': label, 'value': f'{current:,}' if current is not None else '—', 'href': href}
        if moved := change(current, getattr(previous, field) if previous else None):
            result['change'] = moved
        if stats:
            result['spark'] = sparkline([getattr(s, field) for s in stats])
        return result

    # the users list sorts by last login already
    active = tile('Active users (DAU)', 'users_active_today', changelist_url(User))
    if latest:
        wau, mau = latest.users_active_7d, latest.users_active_30d
        stickiness = percent(latest.users_active_today, mau) if mau else '—'
        active['sub'] = f'WAU {wau if wau is not None else "—"} · MAU {mau:,} · stickiness {stickiness}'

    return {
        'as_of': latest.date if latest else None,
        'tiles': [
            tile('Users', 'user_count', changelist_url(User)),
            active,
            tile('Plans', 'plan_count', changelist_url(PlanningPlan)),
            # live, no history: AppStatistic's empire_count / cx_count only track signups
            {
                'label': 'Empires set up',
                'value': f'{empire_set_up().count():,}',
                'href': changelist_url(PlanningEmpire),
            },
            {
                'label': 'Pricing customised',
                'value': f'{pricing_customised().count():,}',
                'href': changelist_url(PlanningCX),
            },
        ],
    }


def growth(days: int) -> list[Chart]:
    stats, _ = _window(days)
    labels = [f'{s.date:%m-%d}' for s in stats]
    return [
        {
            'title': f'Total users, last {days} days',
            'kind': 'line',
            'config': line(labels, [('Users', [s.user_count for s in stats])]),
            'height': 220,
        },
        {
            'title': f'Active users: DAU / WAU / MAU, last {days} days',
            'kind': 'line',
            'config': line(
                labels,
                [
                    ('DAU', [s.users_active_today for s in stats]),
                    ('WAU', [s.users_active_7d for s in stats]),
                    ('MAU', [s.users_active_30d for s in stats]),
                ],
            ),
            'height': 220,
        },
    ]


# ---------------------------------------------------------------------------
# row 3b: engagement in the selected range, against the range before it


def engagement(days: int) -> list[Tile]:
    """Using the tool, not just logging in: plan edits, activation of new users, new shares."""
    now = timezone.now()
    start, previous_start = now - timedelta(days=days), now - timedelta(days=2 * days)

    def in_window(queryset: QuerySet, field: str, since: datetime, until: datetime) -> QuerySet:
        return queryset.filter(**{f'{field}__gte': since, f'{field}__lt': until})

    plans = PlanningPlan.objects.all()
    planners = _users_with(in_window(plans, 'modified_at', start, now))
    planners_before = _users_with(in_window(plans, 'modified_at', previous_start, start))
    edited = in_window(plans, 'modified_at', start, now).count()
    edited_before = in_window(plans, 'modified_at', previous_start, start).count()
    shares = in_window(PlanningShared.objects.all(), 'created_at', start, now).count()
    shares_before = in_window(PlanningShared.objects.all(), 'created_at', previous_start, start).count()

    joined = User.objects.filter(date_joined__gte=start)
    new_users = joined.count()
    activated = joined.filter(Exists(PlanningPlan.objects.filter(user=OuterRef('pk')))).count()

    tiles: list[Tile] = [
        {
            'label': f'Active planners, {days} d',
            'value': f'{planners:,}',
            'sub': 'users who edited a plan',
            'href': changelist_url(PlanningPlan),
        },
        {'label': f'Plans edited, {days} d', 'value': f'{edited:,}', 'href': changelist_url(PlanningPlan)},
        {
            'label': f'Activation, {days} d',
            'value': percent(activated, new_users),
            'sub': f'{activated:,} of {new_users:,} new users created a plan',
            'href': changelist_url(User),
        },
        {'label': f'Shares created, {days} d', 'value': f'{shares:,}', 'href': changelist_url(PlanningShared)},
    ]
    for tile, current, before in (
        (tiles[0], planners, planners_before),
        (tiles[1], edited, edited_before),
        (tiles[3], shares, shares_before),
    ):
        if moved := change(current, before):
            tile['change'] = moved
    return tiles


# ---------------------------------------------------------------------------
# row 4: new rows per day, small multiples with their own scales


def activity(days: int) -> list[Chart]:
    # empires and CX preferences seeded by signup don't count
    sources = (
        ('Signups', 'New signups per day', User.objects.all(), 'date_joined'),
        ('Plans', 'New plans per day', PlanningPlan.objects.all(), 'created_at'),
        ('Empires', 'Empires created by users', created_after_signup(PlanningEmpire.objects.all()), 'created_at'),
        (
            'CX preferences',
            'CX preferences created by users',
            created_after_signup(PlanningCX.objects.all()),
            'created_at',
        ),
    )
    charts: list[Chart] = []
    for name, title, queryset, field in sources:
        labels, values = daily_counts(queryset, field, days)
        charts.append(
            {
                'title': f'{title} ({sum(values):,} in {days} d)',
                'kind': 'bar',
                'config': bar(labels, [(name, values)]),
                'height': 140,
            }
        )
    return charts


# ---------------------------------------------------------------------------
# row 5: product


def plans_per_user() -> Chart:
    per_user = list(PlanningPlan.objects.order_by().values('user').annotate(n=Count('pk')).values_list('n', flat=True))
    users = User.objects.count()
    counts = {label: 0 for label, _, _ in PLAN_BUCKETS}
    counts['0'] = max(users - len(per_user), 0)
    for n in per_user:
        for label, low, high in PLAN_BUCKETS[1:]:
            if n >= low and (high is None or n <= high):
                counts[label] += 1
                break
    return {
        'title': 'Users by number of plans',
        'kind': 'bar',
        'config': bar(list(counts), [('Users', list(counts.values()))]),
        'height': 180,
    }


class Adoption(TypedDict):
    label: str
    pct: float
    text: str
    href: str


def _share(part: int, whole: int) -> tuple[float, str]:
    pct = round(part / whole * 100, 1) if whole else 0.0
    return pct, f'{pct}% · {part:,}'


def _users_with(queryset: QuerySet) -> int:
    return queryset.order_by().values('user').distinct().count()


def feature_adoption() -> dict[str, object]:
    """Which features are used: the share of all users with at least one of each, FIO sync as a share of FIO users."""
    users = User.objects.count()
    api_since = timezone.now() - timedelta(days=API_KEY_ACTIVE_DAYS)
    features = (
        ('Plans', _users_with(PlanningPlan.objects.all()), changelist_url(PlanningPlan)),
        ('Empire set up', empire_set_up().count(), changelist_url(PlanningEmpire)),
        ('Empire with 2+ plans', empire_in_use().count(), changelist_url(PlanningEmpire)),
        ('Pricing customised', pricing_customised().count(), changelist_url(PlanningCX)),
        ('Shared plans', _users_with(PlanningShared.objects.all()), changelist_url(PlanningShared)),
        (
            f'API key used in {API_KEY_ACTIVE_DAYS} d',
            _users_with(UserAPIKey.objects.filter(revoked=False, last_used__gte=api_since)),
            changelist_url(UserAPIKey),
        ),
        ('FIO credentials', User.objects.filter(FIO_LINKED_Q).count(), changelist_url(User, 'fio=yes')),
    )
    rows: list[Adoption] = []
    for label, count, href in features:
        pct, text = _share(count, users)
        rows.append({'label': label, 'pct': pct, 'text': text, 'href': href})

    linked = features[-1][1]
    syncing = GameFIOPlayerData.objects.filter(
        user__in=User.objects.filter(FIO_LINKED_Q), automation_refresh_status='ok'
    ).count()
    pct, text = _share(syncing, linked)
    rows.append(
        {
            'label': 'FIO users syncing OK',
            'pct': pct,
            'text': text,
            'href': changelist_url(GameFIOPlayerData, 'automation_refresh_status=ok'),
        }
    )
    return {'rows': rows, 'users': f'{users:,}'}


class Funnel(TypedDict):
    rows: list[Adoption]
    median_to_first_plan: str


def onboarding_funnel(days: int) -> Funnel:
    """How far users who signed up in the range got: empire set up, first plan, pricing customised."""
    start = timezone.now() - timedelta(days=days)
    joined = User.objects.filter(date_joined__gte=start)
    signups = joined.count()
    steps = (
        (
            'Signed up',
            signups,
            changelist_url(User, f'date_joined_from_0={start:%Y-%m-%d}&date_joined_from_1={start:%H:%M:%S}'),
        ),
        ('Set up their empire', empire_set_up().filter(date_joined__gte=start).count(), ''),
        ('Created a plan', joined.filter(Exists(PlanningPlan.objects.filter(user=OuterRef('pk')))).count(), ''),
        ('Customised pricing', pricing_customised().filter(date_joined__gte=start).count(), ''),
    )
    rows: list[Adoption] = []
    for label, count, href in steps:
        pct, text = _share(count, signups)
        rows.append({'label': label, 'pct': pct, 'text': text, 'href': href})

    waits = list(
        joined.annotate(wait=Min('plans__created_at') - F('date_joined'))
        .filter(wait__isnull=False)
        .values_list('wait', flat=True)
    )
    return {'rows': rows, 'median_to_first_plan': _duration(statistics.median(waits)) if waits else '—'}


def _duration(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return '< 1 min'
    if minutes < 60:
        return f'{minutes} min'
    if minutes < 48 * 60:
        return f'{minutes / 60:.1f} h'
    return f'{minutes / 1440:.1f} d'


# ---------------------------------------------------------------------------
# row 6: top lists (HTML bars, so every row links to its rows)


def top_planets() -> list[Row]:
    rows = list(
        PlanningPlan.objects.order_by()
        .values('planet_natural_id')
        .annotate(n=Count('pk'))
        .order_by('-n', 'planet_natural_id')[:TOP_LIST_SIZE]
    )
    top = rows[0]['n'] if rows else 0
    return [
        {
            'label': row['planet_natural_id'],
            'value': row['n'],
            'pct': round(row['n'] / top * 100, 1) if top else 0.0,
            'href': changelist_url(PlanningPlan, f'planet_natural_id={row["planet_natural_id"]}'),
        }
        for row in rows
    ]


def top_shared() -> list[Row]:
    shares = list(
        PlanningShared.objects.select_related('plan', 'user')
        .only('uuid', 'view_count', 'plan__uuid', 'plan__plan_name', 'user__username')
        .order_by('-view_count')[:TOP_LIST_SIZE]
    )
    top = shares[0].view_count if shares else 0
    return [
        {
            'label': share.plan.plan_name,
            'owner': share.user.username,
            'value': share.view_count,
            'pct': round(share.view_count / top * 100, 1) if top else 0.0,
            'href': reverse('admin:planning_planningshared_change', args=[share.pk]),
        }
        for share in shares
    ]


# ---------------------------------------------------------------------------
# row 7: operations


def task_health() -> list[TaskHealth]:
    rows, _ = task_health_rows(include_unscheduled=False)
    return rows


def webhooks() -> list[tuple[str, str, str, str]]:
    """Inbound FIO webhooks: whether they are on and still being called."""
    return [
        (
            hook.sender,
            'active' if hook.is_active else 'off',
            f'{hook.total_calls:,}',
            f'{_age(hook.last_received_at)} ago' if hook.last_received_at else 'never',
        )
        for hook in GlobalConfigWebhook.objects.order_by('sender')
    ]


def _fetch(sql: str, params: list[int | str] | None = None) -> list[tuple[object, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        return cursor.fetchall()


def postgres_stats() -> list[tuple[str, str]]:
    """This database only (`current_database()`), not the whole cluster."""
    ((connections, active, commits, rollbacks, size, hit_rate, dead),) = _fetch(
        """
        SELECT
            (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()),
            (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND state = 'active'),
            d.xact_commit,
            d.xact_rollback,
            pg_size_pretty(pg_database_size(current_database())),
            round(d.blks_hit * 100.0 / NULLIF(d.blks_hit + d.blks_read, 0), 2),
            (SELECT COALESCE(sum(n_dead_tup), 0) FROM pg_stat_user_tables)
        FROM pg_stat_database d
        WHERE d.datname = current_database()
        """
    )
    return [
        ('Connections (active)', f'{connections} ({active})'),
        ('Database size', str(size)),
        ('Cache hit rate', f'{hit_rate}%' if hit_rate is not None else '—'),
        ('Commits / rollbacks', f'{commits:,} / {rollbacks:,}'),
        ('Dead tuples', f'{dead:,}'),
    ]


def dead_tuple_tables() -> list[tuple[str, str, str]]:
    rows = _fetch(
        """
        SELECT relname, n_dead_tup, n_live_tup, COALESCE(last_autovacuum, last_vacuum)
        FROM pg_stat_user_tables
        WHERE n_dead_tup > 0
        ORDER BY n_dead_tup DESC
        LIMIT 5
        """
    )
    return [
        (str(name), f'{dead:,} / {live:,}', f'{_age(vacuumed)} ago' if isinstance(vacuumed, datetime) else 'never')
        for name, dead, live, vacuumed in rows
    ]


def long_running_queries() -> list[tuple[str, str, str]]:
    rows = _fetch(
        """
        SELECT pid, extract(epoch FROM now() - query_start)::int, left(query, 140)
        FROM pg_stat_activity
        WHERE datname = current_database()
          AND state <> 'idle'
          AND pid <> pg_backend_pid()
          AND query_start < now() - make_interval(secs => %s)
        ORDER BY query_start
        LIMIT 10
        """,
        [LONG_QUERY_SECONDS],
    )
    return [(str(pid), f'{seconds} s', str(query)) for pid, seconds, query in rows]


def slowest_queries() -> list[tuple[str, str, str]] | None:
    """Only when the pg_stat_statements extension is installed; None hides the card."""
    if not _fetch("SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'"):
        return None
    rows = _fetch(
        """
        SELECT left(query, 140), calls, round(mean_exec_time::numeric, 1)
        FROM pg_stat_statements
        WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
        ORDER BY mean_exec_time DESC
        LIMIT 10
        """
    )
    return [(str(query), f'{calls:,}', f'{mean} ms') for query, calls, mean in rows]


def _redis() -> Redis:
    from django_redis import get_redis_connection

    return get_redis_connection('default')


def _number(value: object) -> int:
    return int(value) if isinstance(value, int | float) else 0


def redis_stats() -> list[tuple[str, str]]:
    r = _redis()
    info = cast(dict[str, object], r.info())
    # live SSE sessions: prune the stale ones first
    stats_key = 'stream:active_connections'
    r.zremrangebyscore(stats_key, 0, time.time() - 30)
    stream_users = r.zcard(stats_key)

    hits, misses = _number(info.get('keyspace_hits')), _number(info.get('keyspace_misses'))
    return [
        ('Memory used', str(info.get('used_memory_human', '—'))),
        ('Keyspace hit rate', percent(hits, hits + misses)),
        ('Clients (blocked)', f'{_number(info.get("connected_clients"))} ({_number(info.get("blocked_clients"))})'),
        ('Stream connections', str(stream_users)),
        ('Fragmentation', str(info.get('mem_fragmentation_ratio', '—'))),
        ('Evicted keys', f'{_number(info.get("evicted_keys")):,}'),
    ]


# ---------------------------------------------------------------------------
# row 8: table sizes (collapsed)


def table_sizes() -> list[tuple[str, str, str]]:
    rows = _fetch(
        """
        SELECT c.relname, pg_size_pretty(pg_total_relation_size(c.oid)), GREATEST(c.reltuples, 0)::bigint
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'm') AND n.nspname = 'public'
        ORDER BY pg_total_relation_size(c.oid) DESC
        LIMIT 10
        """
    )
    return [(str(name), str(size), f'~{rows_estimate:,}') for name, size, rows_estimate in rows]


# ---------------------------------------------------------------------------


def build_dashboard(days: int) -> dict[str, object]:
    heading = safe(lambda: headline(days))
    chips = [safe(overdue_tasks_chip), safe(stuck_planets_chip)]
    return {
        'built_at': time.time(),
        'chips': chips,
        'all_ok': all(chip is not None and chip['ok'] for chip in chips),
        'headline': heading,
        'growth': safe(lambda: growth(days)),
        'activity': safe(lambda: activity(days)),
        'plans_per_user': safe(plans_per_user),
        'feature_adoption': safe(feature_adoption),
        'engagement': safe(lambda: engagement(days)),
        'onboarding': safe(lambda: onboarding_funnel(days)),
        'webhooks': safe(webhooks),
        'top_planets': safe(top_planets),
        'top_shared': safe(top_shared),
        'task_health': safe(task_health),
        'postgres': safe(postgres_stats),
        'dead_tuples': safe(dead_tuple_tables),
        'long_queries': safe(long_running_queries),
        'slow_queries': safe(slowest_queries),
        'redis': safe(redis_stats),
        'table_sizes': safe(table_sizes),
        'bar_colour': LIME,
        'dead_tuple_headers': ['Table', 'Dead / live', 'Vacuumed'],
        'long_query_headers': ['PID', 'Running', 'Query'],
        'slow_query_headers': ['Query', 'Calls', 'Mean'],
        'table_size_headers': ['Table', 'Size', 'Rows'],
        'webhook_headers': ['Sender', 'State', 'Calls', 'Last received'],
    }


def selected_range(request: HttpRequest) -> int:
    try:
        days = int(request.GET.get('range', DEFAULT_RANGE))
    except ValueError:
        return DEFAULT_RANGE
    return days if days in RANGES else DEFAULT_RANGE


def dashboard_index(request: HttpRequest, context: dict[str, object]) -> dict[str, object]:
    days = selected_range(request)
    key = f'admin:dashboard:{days}'

    data = None
    if request.GET.get('refresh') != '1':
        data = safe(lambda: cache.get(key))
    if data is None:
        data = build_dashboard(days)
        safe(lambda: cache.set(key, data, CACHE_TTL_SECONDS))

    built_at = data['built_at']
    cached_ago = int(time.time() - built_at) if isinstance(built_at, float) else 0
    context.update(
        {
            **data,
            'title': 'Overview',
            'range_days': days,
            'ranges': RANGES,
            'cached_seconds_ago': cached_ago,
        }
    )
    return context
