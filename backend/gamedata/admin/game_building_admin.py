from core.admin import ReadOnlyAdminMixin
from django.contrib import admin
from django.http import HttpRequest, HttpResponseRedirect
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action

from gamedata.admin.fio_import import queue_fio_import
from gamedata.models import GameBuilding, GameBuildingCost


class BuildingCostInline(ReadOnlyAdminMixin, TabularInline):
    model = GameBuildingCost
    can_delete = False
    extra = 0
    fk_name = 'building'
    tab = True


@admin.register(GameBuilding)
class GameBuildingAdmin(ReadOnlyAdminMixin, ModelAdmin):
    list_display = ['building_ticker', 'building_name', 'expertise', 'building_type']
    search_fields = ['building_ticker', 'building_name', 'expertise']
    list_filter = ['expertise', 'building_type']

    inlines = [BuildingCostInline]

    actions_list = ['action_fio_import_building']

    @action(description='Import from FIO', url_path='changelist-fio-import-building', icon='download')
    def action_fio_import_building(self, request: HttpRequest) -> HttpResponseRedirect:
        return queue_fio_import(request, GameBuilding, 'buildings')
