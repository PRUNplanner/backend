import secrets
import string

import structlog
from core.env import settings
from django.db import transaction
from rest_framework import serializers

from user.models import User, VerificationCode, VerificationeCodeChoices
from user.tasks import send_email_verification_code, send_password_reset_code

logger = structlog.get_logger(__name__)


class VerificationService:
    EXPIRE_MINUTES = settings.email.verification_expiry_minutes
    CODE_LENGTH = 8

    @classmethod
    def generate_numeric_code(cls):
        chars = string.ascii_uppercase + string.digits
        return ''.join(secrets.choice(chars) for _ in range(cls.CODE_LENGTH))

    @classmethod
    def create_and_send_code(cls, user, purpose: VerificationeCodeChoices):
        code_str = cls.generate_numeric_code()

        with transaction.atomic():
            VerificationCode.objects.filter(user=user, purpose=purpose).delete()
            VerificationCode.objects.create(user=user, code=code_str, purpose=purpose)

        if purpose == VerificationeCodeChoices.EMAIL_VERIFICATION:
            send_email_verification_code.apply_async(args=[user.id, user.username, user.email, code_str], priority=10)
        elif purpose == VerificationeCodeChoices.PASSWORD_RESET:
            send_password_reset_code.apply_async(args=[user.id, user.username, user.email, code_str], priority=10)

    @classmethod
    def set_code_used(cls, code_obj: VerificationCode):
        code_obj.is_used = True
        code_obj.save()

    @classmethod
    def validate_code(cls, code_obj: VerificationCode, code_input: str) -> bool:
        # code expired?
        if code_obj.is_expired:
            return False

        # validate code, case-insensitive
        if code_obj.code.upper() != code_input.upper():
            return False

        return True

    @classmethod
    def verify_code(cls, user, code_input, purpose: VerificationeCodeChoices):
        # find active code for user
        code_obj = VerificationCode.objects.filter(user=user, purpose=purpose, is_used=False).first()

        if not code_obj:
            return False, 'No active verification code found or code expired'

        status_code_valid = cls.validate_code(code_obj, code_input)

        # code expired?
        if not status_code_valid:
            return False, 'Code invalid or expired'

        # side-effects per purpose
        if purpose == VerificationeCodeChoices.EMAIL_VERIFICATION:
            with transaction.atomic():
                user.is_email_verified = True
                user.save()

                # set code to used
                cls.set_code_used(code_obj)

            return True, 'Email verified.'

        elif purpose == VerificationeCodeChoices.PASSWORD_RESET:
            return True, 'Valid password reset code'

        return False, 'Unknown purpose.'

    @classmethod
    def get_valid_code_or_raise(cls, email, code_input):
        user = User.objects.filter(email=email, is_email_verified=True).first()
        if not user:
            raise serializers.ValidationError('User not found or not verified.')

        code_obj = VerificationCode.objects.filter(
            user=user, purpose=VerificationeCodeChoices.PASSWORD_RESET, is_used=False
        ).first()

        # Assuming validate_code is a logic check you've written
        if not code_obj or not cls.validate_code(code_obj, code_input):
            raise serializers.ValidationError('Invalid or expired code.')

        return user, code_obj

    @classmethod
    @transaction.atomic
    def execute_password_reset(cls, user, code_obj, new_password):
        user.set_password(new_password)
        user.save()
        code_obj.is_used = True
        code_obj.save()
