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

    def __str__(self):
        return f'Legacy plan {self.user_baseplanner_id}'


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

    def __str__(self):
        return f'Legacy empire {self.user_empire_id}'


class LegacyCX(models.Model):
    user_cx_id = models.BigIntegerField(primary_key=True)
    user_id = models.BigIntegerField()
    uuid = models.UUIDField()
    name = models.CharField(max_length=100)

    cx_data = models.TextField(max_length=5000)

    class Meta:
        managed = False
        db_table = 'user_cx'

    def __str__(self):
        return f'Legacy cx {self.user_cx_id}'


class LegacyEmpirePlanJunction(models.Model):
    empire_id = models.BigIntegerField()
    baseplanner_id = models.BigIntegerField()

    class Meta:
        managed = False
        db_table = 'jct_user_empire_baseplanner'

    def __str__(self):
        return f'Legacy empire-plan-jct {self.empire_id} x {self.baseplanner_id}'


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

    def __str__(self):
        return f'Legacy shared {self.share_id}'
