from django.contrib import admin, messages
from django.http import HttpRequest
from django.shortcuts import redirect
from structlog import get_logger
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action

from gamedata.fio.importers import import_all_buildings
from gamedata.models import GameBuilding, GameBuildingCost

logger = get_logger(__name__)


class BuildingCostInline(TabularInline):
    model = GameBuildingCost
    can_delete = False
    extra = 0
    fk_name = 'building'
    tab = True


@admin.register(GameBuilding)
class GameBuildingAdmin(ModelAdmin):
    list_display = ['building_ticker', 'building_name', 'expertise', 'building_type']
    search_fields = ['building_ticker', 'building_name', 'expertise']

    inlines = [
        BuildingCostInline,
    ]

    actions_list = ['action_fio_import_building']

    @action(description='Import from FIO', url_path='changelist-fio-import-building')
    def action_fio_import_building(self, request: HttpRequest):
        try:
            buildings, costs = import_all_buildings()
            self.message_user(request, f'Buildings synced! Created: {buildings} with {costs} costs.', messages.SUCCESS)
        except Exception:
            self.message_user(request, 'Failed to sync buildings from FIO', messages.ERROR)

        return redirect('../')
