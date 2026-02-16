from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import path

from gamedata.fio.importers import import_all_buildings
from gamedata.models import GameBuilding, GameBuildingCost


class BuildingCostInline(admin.TabularInline):
    model = GameBuildingCost
    can_delete = False
    extra = 0
    fk_name = 'building'


@admin.register(GameBuilding)
class GameBuildingAdmin(admin.ModelAdmin):
    change_list_template = 'admin/gamedata/building_change_list.html'

    list_display = ['building_ticker', 'building_name', 'expertise', 'building_type']
    search_fields = ['building_ticker', 'building_name', 'expertise']

    inlines = [
        BuildingCostInline,
    ]

    def get_urls(self) -> list:
        urls = super().get_urls()
        custom_urls = [
            path(
                'fio_building_import/',
                self.admin_site.admin_view(self.fio_building_import),
                name='fio_building_import',
            )
        ]
        return custom_urls + urls

    def fio_building_import(self, request: HttpRequest) -> HttpResponseRedirect:
        try:
            buildings, costs = import_all_buildings()

            self.message_user(
                request,
                f'Buildings synced! Created: {buildings} with {costs} costs.',
            )
        except Exception as exc:
            self.message_user(request, 'Failed to sync buildings from FIO', messages.ERROR)
            print(exc)

        return redirect('../')
