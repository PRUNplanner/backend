from django.contrib import admin, messages
from django.http import HttpRequest
from django.shortcuts import redirect
from unfold.admin import ModelAdmin
from unfold.decorators import action

from gamedata.fio.importers import import_all_materials
from gamedata.models import GameMaterial


@admin.register(GameMaterial)
class GameMaterialAdmin(ModelAdmin):
    list_display = ['ticker', 'name', 'category_name']
    search_fields = ['ticker', 'name', 'category_name']

    actions_list = ['action_fio_import_material']

    @action(description='Import from FIO', url_path='changelist-fio-import-material')
    def action_fio_import_material(self, request: HttpRequest):
        try:
            deleted_count, materials = import_all_materials()

            self.message_user(
                request, f'Materials synced! Deleted: {deleted_count}, Created: {materials}', messages.SUCCESS
            )
        except Exception:
            self.message_user(request, 'Failed to sync Material from FIO', messages.ERROR)
        return redirect('../')
