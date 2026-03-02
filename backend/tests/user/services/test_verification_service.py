from unittest.mock import patch

import pytest
from rest_framework import serializers
from user.models import VerificationCode, VerificationeCodeChoices
from user.services import VerificationService

pytestmark = pytest.mark.django_db


class TestVerificationService:
    def test_generate_numeric_code_is_correct_format(self):
        code = VerificationService.generate_numeric_code()
        assert len(code) == 8
        assert code.isalnum() and code.isupper()

    def test_validate_code_logic(self, user, verification_code_factory):
        """Combines case-insensitivity and basic failure checks."""
        code_obj = verification_code_factory(user=user, code='ABCDEF12')

        assert VerificationService.validate_code(code_obj, 'abcdef12') is True
        assert VerificationService.validate_code(code_obj, 'WRONGCODE') is False

        with patch.object(VerificationCode, 'is_expired', new=True):
            assert VerificationService.validate_code(code_obj, 'ABCDEF12') is False

    @pytest.mark.parametrize(
        'purpose, task_path',
        [
            (VerificationeCodeChoices.EMAIL_VERIFICATION, 'user.tasks.send_email_verification_code.apply_async'),
            (VerificationeCodeChoices.PASSWORD_RESET, 'user.tasks.send_password_reset_code.apply_async'),
        ],
    )
    def test_create_and_send_code(self, purpose, task_path, user, verification_code_factory):
        verification_code_factory(user=user, purpose=purpose)  # Old code

        with patch(task_path) as mock_task:
            VerificationService.create_and_send_code(user, purpose)

            assert VerificationCode.objects.filter(user=user, purpose=purpose).count() == 1
            mock_task.assert_called_once()
            _, kwargs = mock_task.call_args
            assert kwargs.get('args')[2] == user.email

    @pytest.mark.parametrize(
        'purpose, input_code, expected_success, expected_msg',
        [
            (VerificationeCodeChoices.EMAIL_VERIFICATION, 'VALID123', True, 'Email verified.'),
            (VerificationeCodeChoices.PASSWORD_RESET, 'VALID123', True, 'Valid password reset code'),
            (VerificationeCodeChoices.EMAIL_VERIFICATION, 'WRONG', False, 'Code invalid or expired'),
            ('NON_EXISTENT', 'VALID123', False, 'Unknown purpose.'),
        ],
    )
    def test_verify_code_branches(
        self, purpose, input_code, expected_success, expected_msg, user, verification_code_factory
    ):
        verification_code_factory(user=user, code='VALID123', purpose=purpose)

        success, message = VerificationService.verify_code(user, input_code, purpose)

        assert success == expected_success
        assert expected_msg in message

        if success and purpose == VerificationeCodeChoices.EMAIL_VERIFICATION:
            user.refresh_from_db()
            assert user.is_email_verified is True

    def test_verify_code_fails_if_no_code_exists(self, user):
        success, message = VerificationService.verify_code(user, 'ANYCODE', VerificationeCodeChoices.EMAIL_VERIFICATION)
        assert success is False
        assert 'No active' in message

    def test_get_valid_code_or_raise_logic(self, user, verification_code_factory):
        """Combines various 'raise' scenarios for the password reset flow."""

        user.is_email_verified = False
        user.save()
        with pytest.raises(serializers.ValidationError, match='User not found or not verified.'):
            VerificationService.get_valid_code_or_raise(user.email, 'ANY')

        user.is_email_verified = True
        user.save()
        with pytest.raises(serializers.ValidationError, match='Invalid or expired code.'):
            VerificationService.get_valid_code_or_raise(user.email, 'WRONG')

        verification_code_factory(user=user, code='PASS12', purpose=VerificationeCodeChoices.PASSWORD_RESET)
        u, c = VerificationService.get_valid_code_or_raise(user.email, 'PASS12')
        assert u == user and c.code == 'PASS12'

    def test_execute_password_reset_success(self, user, verification_code_factory):
        code_obj = verification_code_factory(user=user)
        old_password = user.password

        VerificationService.execute_password_reset(user, code_obj, 'NewPass123!')

        user.refresh_from_db()
        code_obj.refresh_from_db()
        assert user.check_password('NewPass123!')
        assert user.password != old_password
        assert code_obj.is_used is True
