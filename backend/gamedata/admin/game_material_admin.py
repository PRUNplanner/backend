from core.admin import ReadOnlyAdminMixin
from django.contrib import admin
from django.http import HttpRequest, HttpResponseRedirect
from unfold.admin import ModelAdmin
from unfold.decorators import action

from gamedata.admin.fio_import import queue_fio_import
from gamedata.models import GameMaterial


@admin.register(GameMaterial)
class GameMaterialAdmin(ReadOnlyAdminMixin, ModelAdmin):
    list_display = ['ticker', 'name', 'category_name']
    search_fields = ['ticker', 'name', 'category_name']
    list_filter = ['category_name']

    actions_list = ['action_fio_import_material']

    @action(description='Import from FIO', url_path='changelist-fio-import-material', icon='download')
    def action_fio_import_material(self, request: HttpRequest) -> HttpResponseRedirect:
        return queue_fio_import(request, GameMaterial, 'materials')
