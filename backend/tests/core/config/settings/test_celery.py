from django.conf import settings


def test_webhooks_are_served_before_user_fio_refreshes() -> None:
    # lower number first; dispatched refreshes go out with 7 (gamedata.tasks.DISPATCH_PRIORITY)
    annotations = settings.CELERY_TASK_ANNOTATIONS

    assert annotations['gamedata_process_fio_webhook']['priority'] == 2
    assert annotations['gamedata_refresh_user_fiodata']['priority'] == 3


def test_user_mails_share_the_top_priority() -> None:
    annotations = settings.CELERY_TASK_ANNOTATIONS
    mails = ['user_send_email_verification_code', 'user_send_password_reset_code', 'user_send_email_changed_notice']

    assert {annotations[task]['priority'] for task in mails} == {1}
    assert min(a['priority'] for a in annotations.values() if 'priority' in a) == 1


def test_every_task_has_a_time_limit_and_admin_imports_a_longer_one() -> None:
    assert (settings.CELERY_TASK_SOFT_TIME_LIMIT, settings.CELERY_TASK_TIME_LIMIT) == (120, 150)

    admin = settings.CELERY_TASK_ANNOTATIONS['gamedata_admin_import']
    assert admin['soft_time_limit'] == 2 * 60 * 60
    assert admin['time_limit'] > admin['soft_time_limit']


# beat intervals in production (Axiom, 2026-11-03); a queued run that outlives the next one is dropped
BEAT_INTERVALS = {
    'gamedata_refresh_planet': 9,
    'gamedata_warm_planet_caches': 10 * 60,
    'gamedata_dispatch_fio_updates': 12 * 60,
    'gamedata_refresh_exchanges': 30 * 60,
    'update_daily_stats': 60 * 60,
    'analytics_bulk_materialize_empire_snapshots': 60 * 60,
    'gamedata_trigger_refresh_cxpc': 3 * 60 * 60,
    'analytics_update_plan_insight_aggregates': 3 * 60 * 60,
    'user_purge_verification_codes': 24 * 60 * 60,
}


def test_periodic_tasks_expire_before_their_next_run() -> None:
    annotations = settings.CELERY_TASK_ANNOTATIONS

    for task, interval in BEAT_INTERVALS.items():
        assert 0 < annotations[task]['expires'] < interval, task


def test_dead_config_is_gone() -> None:
    assert not hasattr(settings, 'CELERY_WORKER_CONCURRENCY')
    assert settings.CELERY_TASK_ANNOTATIONS['gamedata_refresh_cxpc'].keys() == {'priority', 'rate_limit'}


def test_the_registered_tasks_resolve_their_limits() -> None:
    import gamedata.tasks  # noqa: F401  (registers the tasks)
    from core.celery import app

    assert (app.conf.task_soft_time_limit, app.conf.task_time_limit) == (120, 150)
    assert app.tasks['gamedata_admin_import'].soft_time_limit == 2 * 60 * 60
