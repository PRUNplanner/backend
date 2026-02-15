from typing import Any

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from user.services.verification_service import VerificationService

from .models import User, VerificationeCodeChoices


@receiver([post_save], sender=User)
def trigger_fio_refresh(sender: type[User], instance: User, **kwargs: Any):
    from gamedata.tasks import gamedata_clean_user_fiodata, gamedata_refresh_user_fiodata

    if instance._has_fio_credentials():
        gamedata_refresh_user_fiodata.delay(instance.id, instance.prun_username, instance.fio_apikey)
    else:
        gamedata_clean_user_fiodata.delay(instance.id)


@receiver([post_delete], sender=User)
def cleanup_fio_on_delete(sender: type[User], instance: User, **kwargs: Any):
    from gamedata.tasks import gamedata_clean_user_fiodata

    gamedata_clean_user_fiodata.delay(instance.id)


# email verification logics
@receiver(pre_save, sender=User)
def check_email_change(sender, instance, **kwargs):
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


@receiver(post_save, sender=User)
def handle_email_verification_trigger(sender, instance, created, **kwargs):
    if getattr(instance, '_migration_in_progress', False):
        return

    # check if email changed or newly created and email given
    if getattr(instance, '_email_changed', False) or (created and instance.email and not instance.is_email_verified):
        VerificationService.create_and_send_code(instance, VerificationeCodeChoices.EMAIL_VERIFICATION)

        print('trigger email verification', instance.username)

        if hasattr(instance, '_email_changed'):
            del instance._email_changed
