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
