import structlog
from celery import Task, shared_task
from core.env import settings
from django.contrib.auth.models import update_last_login
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags
from gamedata.services.fio_refresh import request_fio_refresh
from structlog.typing import FilteringBoundLogger

from user.models import User, VerificationCode
from user.models.verification_codes import EXPIRY_TIME

logger = structlog.get_logger(__name__)

EMAIL_MAX_RETRIES = 3
EMAIL_RETRY_BASE_SECONDS = 30


def send_with_retry(task: Task, message: EmailMultiAlternatives, log: FilteringBoundLogger) -> bool:
    """Sends, retrying a failed send with backoff (30 s, 60 s, 120 s); only the last failure is logged."""
    try:
        message.send()
    except Exception as exc:
        if task.request.retries < EMAIL_MAX_RETRIES:
            raise task.retry(exc=exc, countdown=EMAIL_RETRY_BASE_SECONDS * 2**task.request.retries) from exc
        log.exception('email_send_failed')
        return False
    log.info('email_sent')
    return True


def expiry_text(minutes: int) -> str:
    # "24 hours", "1 hour" or "45 minutes" for the emails
    if minutes % 60:
        return f'{minutes} minute{"s" if minutes != 1 else ""}'
    hours = minutes // 60
    return f'{hours} hour{"s" if hours != 1 else ""}'


@shared_task(name='user_send_email_verification_code', bind=True, max_retries=EMAIL_MAX_RETRIES)
def send_email_verification_code(self: Task, user_id: int, user_username: str, user_email: str, code_str: str):
    log = logger.bind(user_id=user_id, email_kind='verification')

    context = {
        'username': user_username,
        'verification_url': f'https://prunplanner.org/verify-email/{code_str}',
        'verification_expiry': expiry_text(settings.email.verification_expiry_minutes),
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

    return send_with_retry(self, message, log)


@shared_task(name='user_send_password_reset_code', bind=True, max_retries=EMAIL_MAX_RETRIES)
def send_password_reset_code(self: Task, user_id: int, user_username: str, user_email: str, code_str: str):
    log = logger.bind(user_id=user_id, email_kind='password_reset')

    context = {
        'username': user_username,
        'password_reset_url': f'https://prunplanner.org/password-reset/{code_str}',
        'password_reset_expiry': expiry_text(settings.email.password_reset_expiry_minutes),
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

    return send_with_retry(self, message, log)


@shared_task(name='user_send_email_changed_notice', bind=True, max_retries=EMAIL_MAX_RETRIES)
def send_email_changed_notice(self: Task, user_id: int, user_username: str, old_email: str):
    log = logger.bind(user_id=user_id, email_kind='email_changed')

    html_content = render_to_string('emails/email_changed.html', {'username': user_username})
    text_content = strip_tags(html_content)

    message = EmailMultiAlternatives(
        subject='PRUNplanner Email Changed',
        body=text_content,
        from_email=settings.email.from_email,
        to=[old_email],
    )

    message.attach_alternative(html_content, 'text/html')

    return send_with_retry(self, message, log)


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
    # every code past its purpose's expiry (VerificationCode.is_expired) is gone by then
    cutoff = timezone.now() - max(EXPIRY_TIME.values())
    deleted, _ = VerificationCode.objects.filter(Q(is_used=True) | Q(created_at__lt=cutoff)).delete()
    logger.info('verification_codes_purged', deleted=deleted)
