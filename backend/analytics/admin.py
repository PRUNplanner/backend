import json
import time

import redis
from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.humanize.templatetags.humanize import intcomma
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.db.models import Count, Q
from django_celery_beat.models import PeriodicTask
from gamedata.models import GameFIOPlayerData, GamePlanet
from unfold.admin import ModelAdmin

from analytics.models import AppStatistic


def dashboard_index(request, context):

    def kpi_data():
        return (
            AppStatistic.objects.order_by('-date')
            .values(
                'user_count',
                'users_active_today',
                'users_active_30d',
                'user_count_delta',
                'plan_count',
                'empire_count',
                'cx_count',
                'plan_count_delta',
                'empire_count_delta',
                'cx_count_delta',
            )
            .first()
        )

    chart_data = list(
        AppStatistic.objects.order_by('-date')[:30].values(
            'date',
            'user_count',
            'users_active_today',
            'user_count_delta',
            'plan_count_delta',
            'empire_count_delta',
            'cx_count_delta',
        )
    )
    chart_data.reverse()

    def get_automation_status_data():

        return {
            'planet': GamePlanet.objects.aggregate(
                total_count=Count('pk'),
                ok_count=Count('pk', filter=Q(automation_refresh_status='ok')),
                retrying_count=Count('pk', filter=Q(automation_refresh_status='retrying')),
                failed_count=Count('pk', filter=Q(automation_refresh_status='failed')),
            ),
            'fio_userdata': GameFIOPlayerData.objects.aggregate(
                total_count=Count('pk'),
                ok_count=Count('pk', filter=Q(automation_refresh_status='ok')),
                retrying_count=Count('pk', filter=Q(automation_refresh_status='retrying')),
                failed_count=Count('pk', filter=Q(automation_refresh_status='failed')),
            ),
        }

    def get_redis_stats():
        try:
            r = redis.from_url(settings.CACHES['default']['LOCATION'])
            info = r.info()

            # sse metrics
            stats_key = 'stream:active_connections'
            # prune stale sesions
            r.zremrangebyscore(stats_key, 0, time.time() - 30)
            # get active users
            sse_users = r.zcard(stats_key)

            hits = info.get('keyspace_hits', 0)
            misses = info.get('keyspace_misses', 0)
            total_reqs = hits + misses
            hit_rate = f'{(hits / total_reqs * 100):.1f}%' if total_reqs > 0 else 'N/A'

            return {
                'usage': info.get('used_memory_human', '0B'),
                'hit_rate': hit_rate,
                'active_stream_users': sse_users,
                'active_connections': info.get('connected_clients', 0),
                'blocked_clients': info.get('blocked_clients', 0),
                'fragmentation': info.get('mem_fragmentation_ratio', 0),
                'status': 'Healthy' if info.get('evicted_keys', 0) == 0 else 'Memory Pressure',
            }
        except Exception:
            return None

    def get_postgres_perf_stats():
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    (SELECT count(*) FROM pg_stat_activity) as active_conns,
                    (SELECT sum(xact_commit) FROM pg_stat_database) as total_commits,
                    (SELECT pg_size_pretty(sum(pg_total_relation_size(quote_ident(schemaname) || '.'
                           || quote_ident(relname))))
                    FROM pg_stat_user_tables) as total_data_size,
                    (SELECT
                        round(sum(heap_blks_hit) * 100.0 / NULLIF(sum(heap_blks_hit) + sum(heap_blks_read), 0), 2)
                    FROM pg_statio_user_tables) as cache_hit_rate,
                    (SELECT sum(n_dead_tup) FROM pg_stat_user_tables) as dead_tuples
            """)

            row = cursor.fetchone()

            return {
                'active_connections': row[0] or 0,
                'total_commits': row[1] or 0,
                'db_size': row[2] or '0B',
                'cache_hit_rate': f'{row[3] or 100}%',
                'dead_tuples': row[4] or 0,
                'needs_vacuum': row[4] > 50000 if row[4] else False,
            }

    def get_system_stats():
        count_apps = ['user', 'gamedata', 'planning', 'analytics']
        data = {'models': [], 'total_db_size': '0B', 'total_records': 0}

        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_size_pretty(pg_database_size(current_database()))')
            data['total_db_size'] = cursor.fetchone()[0]

            for model in apps.get_models():
                if model._meta.app_label in count_apps:
                    table_name = model._meta.db_table

                    cursor.execute(
                        """
                        SELECT
                            pg_size_pretty(pg_total_relation_size(%s)),
                            reltuples::bigint
                        FROM pg_class
                        WHERE relname = %s
                    """,
                        [table_name, table_name],
                    )

                    result = cursor.fetchone()
                    size = result[0] if result else '0B'

                    estimate = result[1] if result and result[1] > 0 else model.objects.count()
                    data['total_records'] += estimate

                    data['models'].append(
                        {
                            'name': model._meta.verbose_name,
                            'model_name': model._meta.model_name,
                            'app': model._meta.app_label,
                            'count': estimate,
                            'size': size,
                        }
                    )

        data['models'] = sorted(data['models'], key=lambda x: x['count'], reverse=True)
        return data

    def get_task_data():
        tasks = PeriodicTask.objects.all().values('name', 'enabled', 'last_run_at', 'total_run_count')

        for task in tasks:
            if not task['enabled']:
                task['status_color'] = '#999999'
                task['status_label'] = 'PAUSED'
            else:
                task['status_color'] = '#28a745'
                task['status_label'] = 'RUNNING'

        return tasks

    pg_stats = get_postgres_perf_stats()
    redis_stats = get_redis_stats()
    system_stats = get_system_stats()
    model_rows = [[r['name'], r['app'], intcomma(r['count']), r['size']] for r in system_stats['models']]
    task_data = get_task_data()
    automation_status_data = get_automation_status_data()

    # combine all datapoints
    context.update(
        {
            'kpi_data': kpi_data(),
            'model_counts': system_stats,
            'automation_status_data': automation_status_data,
            'task_data': task_data,
            'redis_stats': redis_stats,
            'postgres_stats': pg_stats,
            'chart_data': json.dumps(chart_data, cls=DjangoJSONEncoder),
            'database_table': {
                'rows': [
                    ['Active Connections', pg_stats['active_connections']],
                    ['Database Size', pg_stats['db_size']],
                    ['Cache Hit-Rate', pg_stats['cache_hit_rate']],
                    ['Total Commits', intcomma(pg_stats['total_commits'])],
                    ['Dead Tuples', pg_stats['dead_tuples']],
                    ['Needs Vacuum', pg_stats['needs_vacuum']],
                ],
            },
            'redis_table': {
                'rows': [
                    ['Active Connections', redis_stats['active_connections']],
                    ['Stream Connections', redis_stats['active_stream_users']],
                    ['Usage', redis_stats['usage']],
                    ['Hit Rate', redis_stats['hit_rate']],
                    ['Blocked Clients', redis_stats['blocked_clients']],
                    ['Fragmentation', redis_stats['fragmentation']],
                    ['Status', redis_stats['status']],
                ],
            },
            'models_table': {'headers': ['Model', 'App', 'Record Count', 'Table Size'], 'rows': model_rows},
            'task_table': {
                'headers': ['Task Name', 'Status', 'Last Run', 'Total Runs'],
                'rows': [
                    [t['name'], t['status_label'], t['last_run_at'], intcomma(t['total_run_count'])] for t in task_data
                ],
            },
            'automation_table': {
                'headers': ['Model', 'Total', 'Retrying', 'Failed'],
                'rows': [
                    [
                        'Planets',
                        intcomma(automation_status_data['planet']['total_count']),
                        intcomma(automation_status_data['planet']['retrying_count']),
                        intcomma(automation_status_data['planet']['failed_count']),
                    ],
                    [
                        'FIO Userdata',
                        intcomma(automation_status_data['fio_userdata']['total_count']),
                        intcomma(automation_status_data['fio_userdata']['retrying_count']),
                        intcomma(automation_status_data['fio_userdata']['failed_count']),
                    ],
                ],
            },
        }
    )

    return context


@admin.register(AppStatistic)
class AppStatisticAdmin(ModelAdmin):
    list_display = [
        'date',
        'user_count',
        'users_active_today',
        'users_active_30d',
        'plan_count',
        'empire_count',
        'cx_count',
    ]
    search_fields = ['date']
    readonly_fields = ['last_updated']
