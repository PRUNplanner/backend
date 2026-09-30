from typing import Any

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from gamedata.gamedata_cache_manager import GamedataCacheManager
from structlog import get_logger

from user.services.verification_service import VerificationService

from .models import User, VerificationeCodeChoices

logger = get_logger(__name__)


# email verification logics
@receiver(pre_save, sender=User)
def check_user_changes(sender, instance, **kwargs):

    # existing email / migration logic to track if email was changed and needs to be verified again

    if getattr(instance, '_migration_in_progress', False):
        return

    # previous row, read once for the email and fio checks below
    old_instance = (
        sender.objects.only('email', 'prun_username', 'fio_apikey').filter(pk=instance.pk).first()
        if instance.pk  # pk only available on update
        else None
    )

    if old_instance:
        if instance.email != old_instance.email and instance.email:
            instance._email_changed = True
            instance.is_email_verified = False
    elif not instance.pk:
        # It's a brand new user
        if instance.email and not instance.is_email_verified:
            instance._email_changed = True

    # logic to flag users that had fio before and potential credentials change

    instance._fio_existed_before = False
    instance._fio_credentials_changed = False

    if old_instance:
        instance._fio_existed_before = old_instance._has_fio_credentials()
        instance._fio_credentials_changed = (
            old_instance.prun_username != instance.prun_username or old_instance.fio_apikey != instance.fio_apikey
        )


@receiver([post_save], sender=User)
def trigger_fio_refresh(sender: type[User], instance: User, created: bool, **kwargs: Any):
    from gamedata.models import GameFIOPlayerData
    from gamedata.services.fio_refresh import request_fio_refresh
    from gamedata.tasks import gamedata_clean_user_fiodata

    # grab pre_save flag and current fio status
    fio_existed_before = getattr(instance, '_fio_existed_before', False)
    fio_credentials_changed = getattr(instance, '_fio_credentials_changed', False)
    fio_now = instance._has_fio_credentials()

    if fio_now:
        # only new credentials refresh from here; login and token refresh ask request_fio_refresh themselves
        if fio_credentials_changed or created:
            # old failures and the lock belong to the old credentials
            GameFIOPlayerData.objects.filter(user_id=instance.pk).update(
                automation_refresh_status='ok', automation_error_count=0, automation_next_retry_at=None
            )
            GamedataCacheManager.delete_fio_refresh_lock(instance.pk)
            request_fio_refresh(instance.pk, 'credentials')

    elif fio_existed_before:
        # user had fio, but not anymore, so we clean the users data
        logger.info('fio_data_cleanup_queued', user_id=instance.id)

        # clean up refresh lock
        GamedataCacheManager.delete_fio_refresh_lock(instance.pk)
        transaction.on_commit(lambda: gamedata_clean_user_fiodata.delay(instance.id))


@receiver([post_delete], sender=User)
def cleanup_fio_on_delete(sender: type[User], instance: User, **kwargs: Any):
    from gamedata.tasks import gamedata_clean_user_fiodata

    logger.info('fio_data_cleanup_queued', user_id=instance.id)
    transaction.on_commit(lambda: gamedata_clean_user_fiodata.delay(instance.id))


@receiver(post_save, sender=User)
def handle_email_verification_trigger(sender, instance, created, **kwargs):
    if getattr(instance, '_migration_in_progress', False):
        return

    # check if email changed or newly created and email given
    if getattr(instance, '_email_changed', False) or (created and instance.email and not instance.is_email_verified):
        VerificationService.create_and_send_code(instance, VerificationeCodeChoices.EMAIL_VERIFICATION)

        logger.info('email_verification_queued', user_id=instance.id)

        if hasattr(instance, '_email_changed'):
            del instance._email_changed
