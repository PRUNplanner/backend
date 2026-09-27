from core.admin import ReadOnlyAdminMixin
from django.contrib import admin
from django.http import HttpRequest, HttpResponseRedirect
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action

from gamedata.admin.fio_import import queue_fio_import
from gamedata.models import GameRecipe, GameRecipeInput, GameRecipeOutput


class RecipeInputInline(ReadOnlyAdminMixin, TabularInline):
    model = GameRecipeInput
    can_delete = False
    extra = 0
    fk_name = 'recipe'
    tab = True


class RecipeOutputInline(ReadOnlyAdminMixin, TabularInline):
    model = GameRecipeOutput
    can_delete = False
    extra = 0
    fk_name = 'recipe'
    tab = True


@admin.register(GameRecipe)
class GameRecipeAdmin(ReadOnlyAdminMixin, ModelAdmin):
    list_display = ['standard_recipe_name', 'building_ticker', 'time_ms']
    search_fields = ['standard_recipe_name', 'building_ticker']

    inlines = [RecipeInputInline, RecipeOutputInline]

    actions_list = ['action_fio_import_recipe']

    @action(description='Import from FIO', url_path='changelist-fio-import-recipe', icon='download')
    def action_fio_import_recipe(self, request: HttpRequest) -> HttpResponseRedirect:
        return queue_fio_import(request, GameRecipe, 'recipes')
