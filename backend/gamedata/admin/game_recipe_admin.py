from django.contrib import admin, messages
from django.http import HttpRequest
from django.shortcuts import redirect
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action

from gamedata.fio.importers import import_all_recipes
from gamedata.models import GameRecipe, GameRecipeInput, GameRecipeOutput


class RecipeInputInline(TabularInline):
    model = GameRecipeInput
    can_delete = False
    extra = 0
    fk_name = 'recipe'
    tab = True


class RecipeOutputInline(TabularInline):
    model = GameRecipeOutput
    can_delete = False
    extra = 0
    fk_name = 'recipe'
    tab = True


@admin.register(GameRecipe)
class GameRecipeAdmin(ModelAdmin):
    list_display = ['standard_recipe_name', 'building_ticker', 'time_ms']
    search_fields = ['standard_recipe_name', 'building_ticker', 'time_ms']

    inlines = [
        RecipeInputInline,
        RecipeOutputInline,
    ]

    actions_list = ['action_fio_import_recipe']

    @action(description='Import from FIO', url_path='changelist-fio-import-recipe')
    def action_fio_import_recipe(self, request: HttpRequest):
        try:
            recipes, inputs, outputs = import_all_recipes()
            self.message_user(
                request, f'Recipes synced! Recipes: {recipes}, Inputs: {inputs}, Outputs: {outputs}.', messages.SUCCESS
            )
        except Exception:
            self.message_user(request, 'Failed refresh recipes.', messages.ERROR)

        return redirect('../')
