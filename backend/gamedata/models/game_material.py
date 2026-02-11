from django.db import models


class GameMaterial(models.Model):
    material_id = models.CharField(primary_key=True, max_length=32)
    category_name = models.CharField(max_length=255)
    category_id = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    ticker = models.CharField(max_length=3, db_index=True)
    weight = models.FloatField()
    volume = models.FloatField()

    objects: models.Manager['GameMaterial'] = models.Manager()

    class Meta:
        db_table = 'prunplanner_game_materials'
        verbose_name = 'Material'
        verbose_name_plural = 'Materials'

    def __str__(self) -> str:
        return f'{self.name} ({self.ticker})'

    @classmethod
    def material_id_ticker_map(cls) -> dict[str, str]:
        return dict(cls.objects.values_list('material_id', 'ticker'))
