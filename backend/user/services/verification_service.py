import secrets
import string
from typing import Literal

import structlog
from django.db import transaction
from rest_framework import serializers

from user.models import User, VerificationCode, VerificationeCodeChoices
from user.tasks import send_email_verification_code, send_password_reset_code

logger = structlog.get_logger(__name__)

SEND_TASKS = {
    VerificationeCodeChoices.EMAIL_VERIFICATION: send_email_verification_code,
    VerificationeCodeChoices.PASSWORD_RESET: send_password_reset_code,
}

# the `code` of an email or reset result and its `detail` text
CODE_DETAILS = {
    'sent': 'Verification code sent to your email.',
    'no_email': 'No email address to verify.',
    'verified': 'Email verified.',
    'already_verified': 'Email already verified.',
    'no_code': 'No active verification code found or code expired',
    'invalid': 'Invalid code.',
    'expired': 'Code expired.',
    'requested': 'If your email is valid and attached to a PRUNplanner user, please check your inbox.',
    'reset': 'Password successfully reset.',
}

CodeFailure = Literal['invalid', 'expired']


class VerificationService:
    CODE_LENGTH = 8

    @classmethod
    def generate_numeric_code(cls) -> str:
        chars = string.ascii_uppercase + string.digits
        return ''.join(secrets.choice(chars) for _ in range(cls.CODE_LENGTH))

    @classmethod
    def create_and_send_code(cls, user: User, purpose: VerificationeCodeChoices) -> None:
        if not user.email:
            raise ValueError('User has no email to send a code to.')

        code_str = cls.generate_numeric_code()

        with transaction.atomic():
            VerificationCode.objects.filter(user=user, purpose=purpose).delete()
            VerificationCode.objects.create(user=user, code=code_str, purpose=purpose)

        task = SEND_TASKS[purpose]
        args = [user.id, user.username, user.email, code_str]
        # after commit: a rolled-back signup or change must not send anything
        transaction.on_commit(lambda: task.apply_async(args=args))

    @staticmethod
    def check_code(code_obj: VerificationCode, code_input: str) -> CodeFailure | None:
        # the match first, so only someone holding the code learns that it expired
        # bytes: compare_digest rejects non-ASCII str, which users can type
        if not secrets.compare_digest(code_obj.code.upper().encode(), code_input.upper().encode()):
            return 'invalid'
        if code_obj.is_expired:
            return 'expired'
        return None

    @classmethod
    def verify_code(cls, user: User, code_input: str) -> tuple[bool, str]:
        """Confirms the user's email with a verification code. Returns (ok, code), see CODE_DETAILS."""
        if user.is_email_verified:
            return cls._verify_failed(user, 'already_verified')

        code_obj = VerificationCode.objects.filter(
            user=user, purpose=VerificationeCodeChoices.EMAIL_VERIFICATION, is_used=False
        ).first()
        if code_obj is None:
            return cls._verify_failed(user, 'no_code')

        failure = cls.check_code(code_obj, code_input)
        if failure:
            return cls._verify_failed(user, failure)

        with transaction.atomic():
            user.is_email_verified = True
            user.save()
            code_obj.is_used = True
            code_obj.save()

        logger.info('email_verified', user_id=user.pk)
        return True, 'verified'

    @staticmethod
    def _verify_failed(user: User, reason: str) -> tuple[bool, str]:
        logger.info('email_verify_failed', user_id=user.pk, reason=reason)
        return False, reason

    @classmethod
    def get_valid_code_or_raise(cls, email: str, code_input: str) -> tuple[User, VerificationCode]:
        """Reset code of a confirmed user; raises a 400 body {detail, code} with code invalid or expired."""
        user = User.objects.filter(email__iexact=email, is_email_verified=True).order_by('pk').first()
        code_obj = (
            VerificationCode.objects.filter(
                user=user, purpose=VerificationeCodeChoices.PASSWORD_RESET, is_used=False
            ).first()
            if user
            else None
        )

        failure: CodeFailure | None = 'invalid' if code_obj is None else cls.check_code(code_obj, code_input)
        if user is None or code_obj is None or failure:
            reason = failure or 'invalid'
            logger.info('password_reset_failed', user_id=user.pk if user else None, reason=reason)
            raise serializers.ValidationError({'detail': CODE_DETAILS[reason], 'code': reason})

        return user, code_obj

    @classmethod
    @transaction.atomic
    def execute_password_reset(cls, user: User, code_obj: VerificationCode, new_password: str) -> None:
        user.set_password(new_password)
        user.save()
        code_obj.is_used = True
        code_obj.save()
        VerificationCode.objects.filter(
            user=user, purpose=VerificationeCodeChoices.PASSWORD_RESET, is_used=False
        ).delete()
        logger.info('password_reset_completed', user_id=user.pk)
