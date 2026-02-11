from django.db import models


class UserPreference(models.Model):
    user = models.OneToOneField('user.User', on_delete=models.CASCADE, related_name='preferences')
    preferences = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects: models.Manager['UserPreference'] = models.Manager()

    class Meta:
        db_table = 'prunplanner_user_preferences'
        verbose_name = 'Preference'
        verbose_name_plural = 'Preferences'

    def __str__(self):
        return f'{self.user.username} Preference'
