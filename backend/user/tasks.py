import structlog
from celery import shared_task
from core.env import settings
from django.contrib.auth.models import update_last_login
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags
from gamedata.services.fio_refresh import request_fio_refresh

from user.models import User, VerificationCode
from user.models.verification_codes import EXPIRY_TIME

logger = structlog.get_logger(__name__)


@shared_task(name='user_send_email_verification_code')
def send_email_verification_code(user_id: int, user_username: str, user_email: str, code_str: str):
    log = logger.bind(user_id=user_id, email_kind='verification')

    context = {
        'username': user_username,
        'verification_url': f'https://prunplanner.org/verify-email/{code_str}',
        'verification_expiry': settings.email.verification_expiry_minutes,
    }
    html_content = render_to_string('emails/email_verification.html', context)
    text_content = strip_tags(html_content)

    message = EmailMultiAlternatives(
        subject='PRUNplanner Email Verification',
        body=text_content,
        from_email=settings.email.from_email,
        to=[user_email],
    )

    message.attach_alternative(html_content, 'text/html')

    try:
        message.send()
        log.info('email_sent')
        return True
    except Exception:
        log.exception('email_send_failed')
        return False


@shared_task(name='user_send_password_reset_code')
def send_password_reset_code(user_id: int, user_username: str, user_email: str, code_str: str):
    log = logger.bind(user_id=user_id, email_kind='password_reset')

    context = {
        'username': user_username,
        'password_reset_url': f'https://prunplanner.org/password-reset/{code_str}',
        'password_reset_expiry': settings.email.password_reset_expiry_minutes,
    }
    html_content = render_to_string('emails/email_passwordreset.html', context)
    text_content = strip_tags(html_content)

    message = EmailMultiAlternatives(
        subject='PRUNplanner Password Reset',
        body=text_content,
        from_email=settings.email.from_email,
        to=[user_email],
    )

    message.attach_alternative(html_content, 'text/html')

    try:
        message.send()
        log.info('email_sent')
        return True
    except Exception:
        log.exception('email_send_failed')
        return False


@shared_task(name='user_handle_post_refresh', ignore_result=True)
def user_handle_post_refresh(user_id: int):
    try:
        user = User.objects.get(id=user_id)

        update_last_login(User, user)
        request_fio_refresh(user.id, 'token_refresh')
    except User.DoesNotExist:
        pass


@shared_task(name='user_purge_verification_codes', ignore_result=True)
def user_purge_verification_codes() -> None:
    # same cutoff as VerificationCode.is_expired
    cutoff = timezone.now() - EXPIRY_TIME
    deleted, _ = VerificationCode.objects.filter(Q(is_used=True) | Q(created_at__lt=cutoff)).delete()
    logger.info('verification_codes_purged', deleted=deleted)
