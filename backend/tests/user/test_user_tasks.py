from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import structlog
from celery import Task
from celery.exceptions import Retry
from core.env import settings
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from gamedata.gamedata_cache_manager import GamedataCacheManager
from model_bakery import baker
from user.models import User, VerificationCode, VerificationeCodeChoices
from user.models.verification_codes import EXPIRY_TIME
from user.tasks import (
    EMAIL_MAX_RETRIES,
    EMAIL_RETRY_BASE_SECONDS,
    expiry_text,
    send_email_changed_notice,
    send_email_verification_code,
    send_password_reset_code,
    send_with_retry,
    user_handle_post_refresh,
    user_purge_verification_codes,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('locmem_cache')]

CaptureOnCommit = Callable[..., AbstractContextManager[object]]


def _run(user: User, capture_on_commit: CaptureOnCommit) -> tuple[MagicMock, MagicMock]:
    """Runs the task and its on-commit callbacks, as outside a test transaction."""
    with (
        patch('gamedata.tasks.gamedata_refresh_user_fiodata.delay') as refresh,
        patch('gamedata.tasks.gamedata_clean_user_fiodata.delay') as clean,
        capture_on_commit(execute=True),
    ):
        user_handle_post_refresh(user.id)
    return refresh, clean


class TestUserHandlePostRefresh:
    def test_queues_fio_refresh_once_for_user_with_credentials(
        self, django_capture_on_commit_callbacks: CaptureOnCommit
    ) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')

        refresh, _ = _run(user, django_capture_on_commit_callbacks)

        refresh.assert_called_once_with(user.id)

    @pytest.mark.parametrize(('refreshed_minutes_ago', 'queued'), [(5, False), (16, True)])
    def test_fio_refresh_waits_for_the_interval(
        self, django_capture_on_commit_callbacks: CaptureOnCommit, refreshed_minutes_ago: int, queued: bool
    ) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')
        baker.make(
            'gamedata.GameFIOPlayerData',
            user=user,
            automation_last_refreshed_at=timezone.now() - timedelta(minutes=refreshed_minutes_ago),
        )

        refresh, _ = _run(user, django_capture_on_commit_callbacks)

        assert refresh.call_count == (1 if queued else 0)

    def test_asks_for_the_refresh_as_token_refresh(self) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')

        with patch('user.tasks.request_fio_refresh') as request_refresh:
            user_handle_post_refresh(user.id)

        request_refresh.assert_called_once_with(user.id, 'token_refresh')

    def test_skips_fio_refresh_while_lock_is_held(self, django_capture_on_commit_callbacks: CaptureOnCommit) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')
        GamedataCacheManager.set_fio_refresh_lock(user.id)

        refresh, _ = _run(user, django_capture_on_commit_callbacks)

        refresh.assert_not_called()

    def test_user_without_credentials_queues_nothing(self, django_capture_on_commit_callbacks: CaptureOnCommit) -> None:
        user: User = baker.make('user.User', prun_username=None, fio_apikey=None)

        refresh, clean = _run(user, django_capture_on_commit_callbacks)

        refresh.assert_not_called()
        clean.assert_not_called()

    def test_updates_last_login(self, django_capture_on_commit_callbacks: CaptureOnCommit) -> None:
        user: User = baker.make('user.User', last_login=None)

        _run(user, django_capture_on_commit_callbacks)

        user.refresh_from_db()
        assert user.last_login is not None

    def test_missing_user_is_ignored(self) -> None:
        user_handle_post_refresh(999_999)


def _code(user: User, purpose: VerificationeCodeChoices, age: timedelta, is_used: bool = False) -> VerificationCode:
    code: VerificationCode = baker.make('user.VerificationCode', user=user, purpose=purpose, is_used=is_used)
    # created_at is auto_now_add, so backdate after creation
    VerificationCode.objects.filter(pk=code.pk).update(created_at=timezone.now() - age)
    return code


# the longer expiry, so no code is purged before it expires
PURGE_AGE = max(EXPIRY_TIME.values())


class TestUserPurgeVerificationCodes:
    @pytest.mark.parametrize('purpose', list(VerificationeCodeChoices))
    def test_deletes_used_and_expired_keeps_active(self, purpose: VerificationeCodeChoices) -> None:
        user: User = baker.make('user.User')
        _code(user, purpose, timedelta(minutes=1), is_used=True)
        _code(user, purpose, PURGE_AGE + timedelta(minutes=1))
        active = _code(user, purpose, PURGE_AGE - timedelta(minutes=1))

        user_purge_verification_codes()

        remaining = set(VerificationCode.objects.values_list('pk', flat=True))
        assert remaining == {active.pk}
        assert User.objects.filter(pk=user.pk).exists()


CodeEmailTask = Task

VERIFY = VerificationeCodeChoices.EMAIL_VERIFICATION
RESET = VerificationeCodeChoices.PASSWORD_RESET


def _pilot_code(purpose: VerificationeCodeChoices) -> VerificationCode:
    user: User = baker.make('user.User', username='pilot')
    # update(): an email set through save() would already get a verification code from the signal
    User.objects.filter(pk=user.pk).update(email='pilot@example.com')
    return baker.make('user.VerificationCode', user=user, purpose=purpose)


@pytest.mark.parametrize(
    ('task', 'purpose', 'link', 'subject'),
    [
        # the links must match the frontend routes /verify-email/:verifyCode and /password-reset/:resetCode
        (
            send_email_verification_code,
            VERIFY,
            'https://prunplanner.org/verify-email/',
            'PRUNplanner Email Verification',
        ),
        (send_password_reset_code, RESET, 'https://prunplanner.org/password-reset/', 'PRUNplanner Password Reset'),
    ],
)
class TestCodeEmails:
    def test_sends_the_code_link_as_html_and_text(
        self, task: CodeEmailTask, purpose: VerificationeCodeChoices, link: str, subject: str
    ) -> None:
        code = _pilot_code(purpose)
        link += code.code
        assert task(code.pk) is True

        (message,) = mail.outbox
        assert isinstance(message, EmailMultiAlternatives)
        assert message.to == ['pilot@example.com']
        assert message.subject == subject
        ((html, mimetype),) = message.alternatives
        assert mimetype == 'text/html'
        assert isinstance(html, str)
        assert link in html
        assert link in message.body
        assert 'pilot' in message.body

    def test_a_retry_backs_off(
        self, task: CodeEmailTask, purpose: VerificationeCodeChoices, link: str, subject: str
    ) -> None:
        code = _pilot_code(purpose)
        with (
            patch('user.tasks.EmailMultiAlternatives.send', side_effect=OSError('provider down')),
            patch.object(task, 'retry', side_effect=Retry()) as retry,
            pytest.raises(Retry),
        ):
            task(code.pk)

        assert retry.call_args.kwargs['countdown'] == EMAIL_RETRY_BASE_SECONDS

    def test_a_replaced_or_used_code_sends_nothing(
        self, task: CodeEmailTask, purpose: VerificationeCodeChoices, link: str, subject: str
    ) -> None:
        used = _pilot_code(purpose)
        VerificationCode.objects.filter(pk=used.pk).update(is_used=True)

        assert task(used.pk) is False
        assert task(used.pk + 1000) is False
        assert mail.outbox == []


@pytest.mark.parametrize(
    ('minutes', 'text'),
    [(1440, '24 hours'), (60, '1 hour'), (30, '30 minutes'), (1, '1 minute'), (90, '90 minutes')],
)
def test_expiry_text(minutes: int, text: str) -> None:
    assert expiry_text(minutes) == text


@pytest.mark.parametrize(
    ('task', 'purpose', 'setting'),
    [
        (send_email_verification_code, VERIFY, 'verification_expiry_minutes'),
        (send_password_reset_code, RESET, 'password_reset_expiry_minutes'),
    ],
)
def test_code_emails_state_their_expiry(task: CodeEmailTask, purpose: VerificationeCodeChoices, setting: str) -> None:
    task(_pilot_code(purpose).pk)

    (message,) = mail.outbox
    assert f'This link expires in {expiry_text(getattr(settings.email, setting))}.' in message.body


class TestEmailChangedNotice:
    def test_goes_to_the_old_address_as_html_and_text(self) -> None:
        user = baker.make('user.User', username='pilot')
        assert send_email_changed_notice(user.pk, 'old@example.com') is True

        (message,) = mail.outbox
        assert isinstance(message, EmailMultiAlternatives)
        assert message.to == ['old@example.com']
        assert message.subject == 'PRUNplanner Email Changed'
        ((html, mimetype),) = message.alternatives
        assert mimetype == 'text/html'
        assert 'pilot' in message.body
        assert 'someone else knows your password' in message.body

    def test_a_failed_send_is_retried(self) -> None:
        user = baker.make('user.User')
        with (
            patch('user.tasks.EmailMultiAlternatives.send', side_effect=OSError('provider down')),
            patch.object(send_email_changed_notice, 'retry', side_effect=Retry()) as retry,
            pytest.raises(Retry),
        ):
            send_email_changed_notice(user.pk, 'old@example.com')

        assert retry.call_args.kwargs['countdown'] == EMAIL_RETRY_BASE_SECONDS


def email_task(retries: int) -> MagicMock:
    """A bound task on its given attempt; retry() raises like Celery's does."""
    task = MagicMock(spec=Task)
    task.request = SimpleNamespace(retries=retries)
    task.retry.side_effect = Retry()
    return task


def failing_message() -> MagicMock:
    message = MagicMock()
    message.send.side_effect = OSError('provider down')
    return message


def email_events(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.msg['event'] for r in caplog.records if isinstance(r.msg, dict) and r.msg['event'].startswith('email_')]


class TestSendWithRetry:
    """AC6: a failed send retries with backoff (30 s, 60 s, 120 s), and only the last failure is logged."""

    @pytest.mark.parametrize('retries', range(EMAIL_MAX_RETRIES))
    def test_a_failure_with_retries_left_retries_silently(self, retries: int, caplog: pytest.LogCaptureFixture) -> None:
        task, message = email_task(retries), failing_message()

        with pytest.raises(Retry):
            send_with_retry(task, message, structlog.get_logger('user.tasks'))

        assert task.retry.call_args.kwargs['countdown'] == EMAIL_RETRY_BASE_SECONDS * 2**retries
        assert email_events(caplog) == []

    def test_the_last_failure_is_logged_once_and_reported(self, caplog: pytest.LogCaptureFixture) -> None:
        task, message = email_task(EMAIL_MAX_RETRIES), failing_message()

        assert send_with_retry(task, message, structlog.get_logger('user.tasks')) is False

        task.retry.assert_not_called()
        assert email_events(caplog) == ['email_send_failed']

    def test_a_retried_send_that_succeeds_is_logged_as_sent(self, caplog: pytest.LogCaptureFixture) -> None:
        task, message = email_task(1), MagicMock()

        assert send_with_retry(task, message, structlog.get_logger('user.tasks')) is True
        assert email_events(caplog) == ['email_sent']

    @pytest.mark.parametrize(
        'email_task_fn', [send_email_verification_code, send_password_reset_code, send_email_changed_notice]
    )
    def test_every_email_task_allows_the_retries(self, email_task_fn: Task) -> None:
        assert email_task_fn.max_retries == EMAIL_MAX_RETRIES
