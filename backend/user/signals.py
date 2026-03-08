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

    if instance.pk:  # pk only available on update
        try:
            old_instance = User.objects.get(pk=instance.pk)

            if instance.email != old_instance.email and instance.email:
                instance._email_changed = True
                instance.is_email_verified = False
        except User.DoesNotExist:
            pass
    else:
        # It's a brand new user
        if instance.email and not instance.is_email_verified:
            instance._email_changed = True

    # logic to flag users that had fio before and potential credentials change

    instance._fio_existed_before = False
    instance._fio_credentials_changed = False

    if instance.pk:
        old_instance = sender.objects.only('prun_username', 'fio_apikey').filter(pk=instance.pk).first()
        if old_instance:
            instance._fio_existed_before = old_instance._has_fio_credentials()
            instance._fio_credentials_changed = (
                old_instance.prun_username != instance.prun_username or old_instance.fio_apikey != instance.fio_apikey
            )


@receiver([post_save], sender=User)
def trigger_fio_refresh(sender: type[User], instance: User, **kwargs: Any):
    from gamedata.tasks import gamedata_clean_user_fiodata, gamedata_refresh_user_fiodata

    # grab pre_save flag and current fio status
    fio_existed_before = getattr(instance, '_fio_existed_before', False)
    fio_credentials_changed = getattr(instance, '_fio_credentials_changed', False)
    fio_now = instance._has_fio_credentials()

    if fio_now:
        # if credentials have changed, clean potential refresh lock
        if fio_credentials_changed:
            GamedataCacheManager.delete_fio_refresh_lock(instance.pk)

        # trigger refresh on every save (last_login update)
        logger.info('signal:trigger_fio_refresh', user=instance.id, task='gamedata_refresh_user_fiodata')
        transaction.on_commit(
            lambda: gamedata_refresh_user_fiodata.delay(instance.id, instance.prun_username, instance.fio_apikey)
        )

    elif fio_existed_before:
        # user had fio, but not anymore, so we clean the users data
        logger.info('signal:trigger_fio_refresh', user=instance.id, task='gamedata_clean_user_fiodata')

        # clean up refresh lock
        GamedataCacheManager.delete_fio_refresh_lock(instance.pk)
        transaction.on_commit(lambda: gamedata_clean_user_fiodata.delay(instance.id))


@receiver([post_delete], sender=User)
def cleanup_fio_on_delete(sender: type[User], instance: User, **kwargs: Any):
    from gamedata.tasks import gamedata_clean_user_fiodata

    logger.info('signal:cleanup_fio_on_delete', user=instance.id, task='gamedata_clean_user_fiodata')
    transaction.on_commit(lambda: gamedata_clean_user_fiodata.delay(instance.id))


@receiver(post_save, sender=User)
def handle_email_verification_trigger(sender, instance, created, **kwargs):
    if getattr(instance, '_migration_in_progress', False):
        return

    # check if email changed or newly created and email given
    if getattr(instance, '_email_changed', False) or (created and instance.email and not instance.is_email_verified):
        VerificationService.create_and_send_code(instance, VerificationeCodeChoices.EMAIL_VERIFICATION)

        logger.info(
            'signal:handle_email_verification_trigger',
            user=instance.id,
            task='VerificationService.create_and_send_code',
        )

        if hasattr(instance, '_email_changed'):
            del instance._email_changed
