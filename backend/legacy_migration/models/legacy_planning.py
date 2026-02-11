from django.db import models


class LegacyBaseplanner(models.Model):
    user_baseplanner_id = models.BigIntegerField(primary_key=True)
    user_id = models.BigIntegerField()
    uuid = models.UUIDField()
    name = models.CharField(max_length=100)
    planet_id = models.CharField(max_length=8)

    baseplanner_data = models.TextField(max_length=5000)

    faction = models.CharField(max_length=200)
    permits_used = models.IntegerField()
    permits_total = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'user_baseplanner'


class LegacyEmpire(models.Model):
    user_empire_id = models.BigIntegerField(primary_key=True)
    user_id = models.BigIntegerField()
    cx_id = models.BigIntegerField(null=True, blank=True)
    uuid = models.UUIDField()
    name = models.CharField(max_length=100)
    faction = models.CharField(max_length=200)
    permits_used = models.IntegerField()
    permits_total = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'user_empire'


class LegacyCX(models.Model):
    user_cx_id = models.BigIntegerField(primary_key=True)
    user_id = models.BigIntegerField()
    uuid = models.UUIDField()
    name = models.CharField(max_length=100)

    cx_data = models.TextField(max_length=5000)

    class Meta:
        managed = False
        db_table = 'user_cx'


class LegacyEmpirePlanJunction(models.Model):
    empire_id = models.BigIntegerField()
    baseplanner_id = models.BigIntegerField()

    class Meta:
        managed = False
        db_table = 'jct_user_empire_baseplanner'


class LegacyShared(models.Model):
    share_id = models.BigIntegerField(primary_key=True)
    uuid = models.UUIDField()
    created_date = models.DateTimeField()
    view_count = models.IntegerField(default=0)
    user_id = models.BigIntegerField()
    baseplanner_id = models.BigIntegerField()

    class Meta:
        managed = False
        db_table = 'shared'
