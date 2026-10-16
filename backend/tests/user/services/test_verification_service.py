from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import timedelta
from unittest.mock import patch

import pytest
from core.env import EmailSettings, settings
from django.utils import timezone
from model_bakery import baker
from rest_framework import serializers
from user.models import User, VerificationCode, VerificationeCodeChoices
from user.models.verification_codes import EXPIRY_TIME
from user.services import VerificationService

pytestmark = pytest.mark.django_db

CaptureOnCommit = Callable[..., AbstractContextManager[list]]
CodeFactory = Callable[..., VerificationCode]


def _backdate(code: VerificationCode, age: timedelta) -> VerificationCode:
    # created_at is auto_now_add, so backdate after creation
    VerificationCode.objects.filter(pk=code.pk).update(created_at=timezone.now() - age)
    code.refresh_from_db()
    return code


class TestVerificationService:
    def test_generate_numeric_code_is_correct_format(self) -> None:
        code = VerificationService.generate_numeric_code()
        assert len(code) == 8
        assert code.isalnum() and code.isupper()

    def test_check_code(self, user: User, verification_code_factory: CodeFactory) -> None:
        code_obj = verification_code_factory(user=user, code='ABCDEF12')

        assert VerificationService.check_code(code_obj, 'abcdef12') is None
        assert VerificationService.check_code(code_obj, 'WRONGCOD') == 'invalid'

        with patch.object(VerificationCode, 'is_expired', new=True):
            assert VerificationService.check_code(code_obj, 'ABCDEF12') == 'expired'
            # only someone holding the code learns that it expired
            assert VerificationService.check_code(code_obj, 'WRONGCOD') == 'invalid'

    @pytest.mark.parametrize(
        ('purpose', 'setting'),
        [
            (VerificationeCodeChoices.EMAIL_VERIFICATION, 'verification_expiry_minutes'),
            (VerificationeCodeChoices.PASSWORD_RESET, 'password_reset_expiry_minutes'),
        ],
    )
    def test_expiry_depends_on_the_purpose(
        self, purpose: VerificationeCodeChoices, setting: str, user: User, verification_code_factory: CodeFactory
    ) -> None:
        expiry = timedelta(minutes=getattr(settings.email, setting))
        assert EXPIRY_TIME[purpose] == expiry
        code = verification_code_factory(user=user, purpose=purpose)

        assert _backdate(code, expiry - timedelta(minutes=1)).is_expired is False
        assert _backdate(code, expiry + timedelta(minutes=1)).is_expired is True

    @pytest.mark.parametrize(
        'purpose, task_path',
        [
            (VerificationeCodeChoices.EMAIL_VERIFICATION, 'user.tasks.send_email_verification_code.apply_async'),
            (VerificationeCodeChoices.PASSWORD_RESET, 'user.tasks.send_password_reset_code.apply_async'),
        ],
    )
    def test_create_and_send_code_queues_after_commit(
        self,
        purpose: VerificationeCodeChoices,
        task_path: str,
        user: User,
        verification_code_factory: CodeFactory,
        django_capture_on_commit_callbacks: CaptureOnCommit,
    ) -> None:
        verification_code_factory(user=user, purpose=purpose)  # old code

        with patch(task_path) as mock_task, django_capture_on_commit_callbacks() as callbacks:
            VerificationService.create_and_send_code(user, purpose)
            mock_task.assert_not_called()

        assert VerificationCode.objects.filter(user=user, purpose=purpose).count() == 1
        assert len(callbacks) == 1
        with patch(task_path) as mock_task:
            callbacks[0]()
        mock_task.assert_called_once()
        assert mock_task.call_args.kwargs['args'][2] == user.email

    @pytest.mark.parametrize('email', [None, ''])
    def test_create_and_send_code_needs_an_email(
        self, email: str | None, django_capture_on_commit_callbacks: CaptureOnCommit
    ) -> None:
        user: User = baker.make('user.User', email=email)

        with django_capture_on_commit_callbacks() as callbacks, pytest.raises(ValueError):
            VerificationService.create_and_send_code(user, VerificationeCodeChoices.EMAIL_VERIFICATION)

        assert callbacks == []
        assert not VerificationCode.objects.filter(user=user).exists()

    @pytest.mark.parametrize(
        'purpose', [VerificationeCodeChoices.EMAIL_VERIFICATION, VerificationeCodeChoices.PASSWORD_RESET]
    )
    def test_codes_are_sent_with_the_annotated_priority(
        self, purpose: VerificationeCodeChoices, user: User, django_capture_on_commit_callbacks: CaptureOnCommit
    ) -> None:
        # Redis broker: 0 is the highest priority; CELERY_TASK_ANNOTATIONS sets 1 for both code emails
        with (
            patch('celery.app.base.Celery.send_task') as send_task,
            django_capture_on_commit_callbacks(execute=True),
        ):
            VerificationService.create_and_send_code(user, purpose)

        send_task.assert_called_once()
        assert send_task.call_args.kwargs['priority'] == 1

    def test_verify_code_confirms_the_email(self, user: User, verification_code_factory: CodeFactory) -> None:
        code = verification_code_factory(user=user, code='VALID123')

        assert VerificationService.verify_code(user, 'valid123') == (True, 'verified')

        user.refresh_from_db()
        code.refresh_from_db()
        assert user.is_email_verified is True
        assert code.is_used is True
        assert VerificationService.verify_code(user, 'VALID123') == (False, 'already_verified')

    @pytest.mark.parametrize(
        ('purpose', 'age', 'code_input', 'expected'),
        [
            (VerificationeCodeChoices.EMAIL_VERIFICATION, timedelta(0), 'WRONG123', 'invalid'),
            (VerificationeCodeChoices.EMAIL_VERIFICATION, timedelta(days=30), 'VALID123', 'expired'),
            # a reset code doesn't confirm the email
            (VerificationeCodeChoices.PASSWORD_RESET, timedelta(0), 'VALID123', 'no_code'),
        ],
        ids=['wrong', 'expired', 'other-purpose'],
    )
    def test_verify_code_failures(
        self,
        purpose: VerificationeCodeChoices,
        age: timedelta,
        code_input: str,
        expected: str,
        user: User,
        verification_code_factory: CodeFactory,
    ) -> None:
        _backdate(verification_code_factory(user=user, code='VALID123', purpose=purpose), age)

        assert VerificationService.verify_code(user, code_input) == (False, expected)
        user.refresh_from_db()
        assert user.is_email_verified is False

    def test_get_valid_code_or_raise(self, verification_code_factory: CodeFactory) -> None:
        user: User = baker.make('user.User', email='pilot@example.com', is_email_verified=False)
        verification_code_factory(user=user, code='PASS1234', purpose=VerificationeCodeChoices.PASSWORD_RESET)

        def failure(email: str, code: str) -> object:
            with pytest.raises(serializers.ValidationError) as exc:
                VerificationService.get_valid_code_or_raise(email, code)
            return exc.value.detail

        # unconfirmed users can't reset
        assert failure('pilot@example.com', 'PASS1234') == {'detail': 'Invalid code.', 'code': 'invalid'}

        User.objects.filter(pk=user.pk).update(is_email_verified=True)
        assert failure('pilot@example.com', 'WRONG123') == {'detail': 'Invalid code.', 'code': 'invalid'}
        assert failure('nobody@example.com', 'PASS1234') == {'detail': 'Invalid code.', 'code': 'invalid'}

        found_user, code_obj = VerificationService.get_valid_code_or_raise('Pilot@EXAMPLE.com', 'pass1234')
        assert found_user == user and code_obj.code == 'PASS1234'

        _backdate(code_obj, EXPIRY_TIME[VerificationeCodeChoices.PASSWORD_RESET] + timedelta(minutes=1))
        assert failure('pilot@example.com', 'PASS1234') == {'detail': 'Code expired.', 'code': 'expired'}

    def test_execute_password_reset(self, user: User, verification_code_factory: CodeFactory) -> None:
        code_obj = verification_code_factory(user=user, purpose=VerificationeCodeChoices.PASSWORD_RESET)
        other = verification_code_factory(user=user, purpose=VerificationeCodeChoices.PASSWORD_RESET)
        verification = verification_code_factory(user=user)
        old_password = user.password

        VerificationService.execute_password_reset(user, code_obj, 'NewPass123!')

        user.refresh_from_db()
        code_obj.refresh_from_db()
        assert user.check_password('NewPass123!')
        assert user.password != old_password
        assert code_obj.is_used is True
        assert not VerificationCode.objects.filter(pk=other.pk).exists()
        assert VerificationCode.objects.filter(pk=verification.pk).exists()


def test_expiry_defaults() -> None:
    # 24 hours to confirm an address, 1 hour for a reset; an EMAIL_*_EXPIRY_MINUTES env var overrides them
    fields = EmailSettings.model_fields
    assert fields['verification_expiry_minutes'].default == 24 * 60
    assert fields['password_reset_expiry_minutes'].default == 60
