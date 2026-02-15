import structlog
from celery import shared_task
from core.env import settings
from django.contrib.auth.models import update_last_login
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from user.models import User

logger = structlog.get_logger(__name__)


@shared_task(name='user_send_email_verification_code')
def send_email_verification_code(user_id: int, user_username: str, user_email: str, code_str: str):
    structlog.contextvars.bind_contextvars(
        task_category='user_send_email_verification_code',
    )
    log = logger.bind(name='send_email_verification_code', user_id=user_id)

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
        log.info('email_send')
        return True
    except Exception as exc:
        log.error('email_send_exception', exc_info=exc)
        return False


@shared_task(name='user_send_password_reset_code')
def send_password_reset_code(user_id: int, user_username: str, user_email: str, code_str: str):
    structlog.contextvars.bind_contextvars(
        task_category='user_send_password_reset_code',
    )
    log = logger.bind(name='send_password_reset_code', user_id=user_id)

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
        log.info('email_send')
        return True
    except Exception as exc:
        log.error('email_send_exception', exc_info=exc)
        return False


@shared_task(name='user_handle_post_refresh')
def user_handle_post_refresh(user_id: int):
    structlog.contextvars.bind_contextvars(
        task_category='user_handle_post_refresh',
    )

    try:
        user = User.objects.get(id=user_id)

        update_last_login(User, user)

        if user._has_fio_credentials():
            from gamedata.tasks import gamedata_refresh_user_fiodata

            gamedata_refresh_user_fiodata.delay(user.id, user.prun_username, user.fio_apikey)
        else:
            from gamedata.tasks import gamedata_clean_user_fiodata

            gamedata_clean_user_fiodata.delay(user.id)
    except User.DoesNotExist:
        pass
