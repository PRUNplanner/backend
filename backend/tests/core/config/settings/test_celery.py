from django.conf import settings


def test_webhooks_are_served_before_user_fio_refreshes() -> None:
    # lower number first; dispatched refreshes go out with 7 (gamedata.tasks.DISPATCH_PRIORITY)
    annotations = settings.CELERY_TASK_ANNOTATIONS

    assert annotations['gamedata_process_fio_webhook']['priority'] == 2
    assert annotations['gamedata_refresh_user_fiodata']['priority'] == 3
