from django.contrib import admin
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import path

from gamedata.fio.importers import import_all_recipes
from gamedata.models import GameRecipe, GameRecipeInput, GameRecipeOutput


class RecipeInputInline(admin.TabularInline):
    model = GameRecipeInput
    can_delete = False
    extra = 0
    fk_name = 'recipe'


class RecipeOutputInline(admin.TabularInline):
    model = GameRecipeOutput
    can_delete = False
    extra = 0
    fk_name = 'recipe'


@admin.register(GameRecipe)
class GameRecipeAdmin(admin.ModelAdmin):
    change_list_template = 'admin/gamedata/recipe_change_list.html'

    list_display = ['standard_recipe_name', 'building_ticker', 'time_ms']
    search_fields = ['standard_recipe_name', 'building_ticker', 'time_ms']

    inlines = [
        RecipeInputInline,
        RecipeOutputInline,
    ]

    def get_urls(self) -> list:
        urls = super().get_urls()
        custom_urls = [
            path(
                'fio-recipe-import/',
                self.admin_site.admin_view(self.fio_recipe_import),
                name='fio_recipe_import',
            ),
        ]
        return custom_urls + urls

    def fio_recipe_import(self, request: HttpRequest) -> HttpResponseRedirect:
        recipes, inputs, outputs = import_all_recipes()

        self.message_user(
            request,
            f'Recipes synced! Recipes: {recipes}, Inputs: {inputs}, Outputs: {outputs}',
        )
        return redirect('../')
