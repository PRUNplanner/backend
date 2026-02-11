import uuid

from django.db import models


class GameRecipe(models.Model):
    standard_recipe_name = models.CharField(primary_key=True, max_length=255, blank=False)
    recipe_name = models.CharField(max_length=255, blank=False)
    building_ticker = models.CharField(max_length=3, blank=False, db_index=True)
    time_ms = models.PositiveIntegerField()

    objects: models.Manager['GameRecipe'] = models.Manager()

    class Meta:
        db_table = 'prunplanner_game_recipes'
        verbose_name = 'Recipe'
        verbose_name_plural = 'Recipes'

    def __str__(self) -> str:
        return self.standard_recipe_name


class GameRecipeInput(models.Model):
    recipe_input_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipe = models.ForeignKey(GameRecipe, related_name='inputs', on_delete=models.CASCADE)
    material_ticker = models.CharField(max_length=3, blank=False, db_index=True)
    material_amount = models.PositiveIntegerField()

    objects: models.Manager['GameRecipeInput'] = models.Manager()

    class Meta:
        db_table = 'prunplanner_game_recipes_inputs'
        verbose_name = 'Recipe Input'
        verbose_name_plural = 'Recipe Inputs'

    def __str__(self) -> str:
        return f'{self.material_amount} x {self.material_ticker} from {self.recipe}'


class GameRecipeOutput(models.Model):
    recipe_output_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipe = models.ForeignKey(GameRecipe, related_name='outputs', on_delete=models.CASCADE)
    material_ticker = models.CharField(max_length=3, blank=False, db_index=True)
    material_amount = models.PositiveIntegerField()

    objects: models.Manager['GameRecipeOutput'] = models.Manager()

    class Meta:
        db_table = 'prunplanner_game_recipes_outputs'
        verbose_name = 'Recipe Output'
        verbose_name_plural = 'Recipe Outputs'

    def __str__(self) -> str:
        return f'{self.material_amount} x {self.material_ticker} from {self.recipe}'
