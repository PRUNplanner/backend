from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import path

from gamedata.fio.importers import import_all_materials
from gamedata.models import GameMaterial


@admin.register(GameMaterial)
class GameMaterialAdmin(admin.ModelAdmin):
    change_list_template = 'admin/gamedata/material_change_list.html'

    list_display = ['ticker', 'name', 'category_name']
    search_fields = ['ticker', 'name', 'category_name']

    def get_urls(self) -> list:
        urls = super().get_urls()
        custom_urls = [
            path(
                'fio_material_import/',
                self.admin_site.admin_view(self.fio_material_import),
                name='fio_material_import',
            ),
        ]
        return custom_urls + urls

    def fio_material_import(self, request: HttpRequest) -> HttpResponseRedirect:
        try:
            deleted_count, materials = import_all_materials()

            self.message_user(
                request, f'Materials synced! Deleted: {deleted_count}, Created: {materials}', messages.SUCCESS
            )
        except Exception:
            self.message_user(request, 'Failed to sync Material from FIO', messages.ERROR)
        return redirect('../')
