from django.db import models


class LegacyUser(models.Model):
    user_id = models.BigIntegerField(primary_key=True)
    hashed_password = models.CharField(max_length=255)

    username = models.CharField(max_length=250)
    email = models.CharField(max_length=255, blank=True, null=True)

    level = models.IntegerField()

    fio_apikey = models.CharField(max_length=250)
    prun_username = models.CharField(max_length=250)

    class Meta:
        managed = False
        db_table = 'user'
