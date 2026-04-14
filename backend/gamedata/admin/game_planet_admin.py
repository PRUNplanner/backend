from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse
from structlog import get_logger
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.decorators import action

from gamedata.fio.importers import import_all_planets, import_planet, import_planet_infrastructure
from gamedata.models import (
    GamePlanet,
    GamePlanetCOGCProgram,
    GamePlanetInfrastructureReport,
    GamePlanetProductionFee,
    GamePlanetResource,
)

logger = get_logger(__name__)


class PlanetCOGCProgramInline(TabularInline):
    model = GamePlanetCOGCProgram
    can_delete = True
    extra = 0
    fk_name = 'planet'
    tab = True


class PlanetResourceInline(TabularInline):
    model = GamePlanetResource
    can_delete = True
    extra = 0
    fk_name = 'planet'
    tab = True


class PlanetProductionFeeInline(TabularInline):
    model = GamePlanetProductionFee
    can_delete = True
    extra = 0
    fk_name = 'planet'
    tab = True


class PlanetInfrastructureReportInline(StackedInline):
    model = GamePlanetInfrastructureReport
    can_delete = True
    extra = 0
    fk_name = 'planet'
    tab = True


@admin.register(GamePlanet)
class GamePlanetAdmin(ModelAdmin):
    list_display = ['planet_natural_id', 'planet_name', 'automation_refresh_status', 'automation_last_refreshed_at']
    search_fields = ['planet_natural_id', 'planet_name']
    list_filter = ['automation_refresh_status']
    ordering = ['-automation_last_refreshed_at']

    inlines = [
        PlanetResourceInline,
        PlanetCOGCProgramInline,
        PlanetProductionFeeInline,
        PlanetInfrastructureReportInline,
    ]

    actions_list = ['action_fio_import_all_planet', 'action_fio_delete_all_planet']
    actions_row = ['action_delete_planet', 'action_refresh_planet', 'action_refresh_planet_infrastructure']

    @action(description='Import from FIO', url_path='changelist-fio-import-all-planet')
    def action_fio_import_all_planet(self, request: HttpRequest):
        try:
            import_all_planets()
            self.message_user(request, 'All Planets imported from FIO.', messages.SUCCESS)
        except Exception:
            self.message_user(request, 'Failed to import all Planets from FIO', messages.ERROR)

        return redirect('../')

    @action(description='Delete All Planets', url_path='changelist-fio-delete-all-planet')
    def action_fio_delete_all_planet(self, request: HttpRequest):
        try:
            count = GamePlanet.objects.count()
            GamePlanet.objects.all().delete()
            self.message_user(request, f'Deleted {count} planets.', messages.SUCCESS)
        except Exception:
            self.message_user(request, 'Failed to delete all planets.', messages.ERROR)

        return redirect('../')

    @admin.action(description='Delete all planets')
    def delete_all_planets(self, request: HttpRequest, queryset: QuerySet | None = None) -> None:
        count = GamePlanet.objects.count()
        GamePlanet.objects.all().delete()

        logger.info('Admin delete all planets', count=count)

        self.message_user(request, f'Deleted {count} log entries.', level=messages.SUCCESS)

    @action(description='Delete', url_path='changelist-delete-planet')
    def action_delete_planet(self, request: HttpRequest, object_id: int):
        try:
            GamePlanet.objects.filter(planet_id=object_id).delete()
            self.message_user(request, 'Planet deleted.', messages.SUCCESS)
        except Exception:
            self.message_user(request, 'Failed to delete planet.', messages.ERROR)

        opts = self.model._meta
        changelist_url = reverse(f'admin:{opts.app_label}_{opts.model_name}_changelist')
        return HttpResponseRedirect(changelist_url)

    @action(description='Refresh', url_path='changelist-refresh-planet')
    def action_refresh_planet(self, request: HttpRequest, object_id: int):
        try:
            planet = GamePlanet.objects.filter(planet_id=object_id).first()
            if planet:
                import_planet(planet.planet_natural_id)

            self.message_user(request, 'Planet refreshed from FIO.', messages.SUCCESS)
        except Exception:
            self.message_user(request, 'Failed to refresh planet.', messages.ERROR)

        opts = self.model._meta
        changelist_url = reverse(f'admin:{opts.app_label}_{opts.model_name}_changelist')
        return HttpResponseRedirect(changelist_url)

    @action(description='Refresh Infrastructure', url_path='changelist-refresh-planet-infrastructure')
    def action_refresh_planet_infrastructure(self, request: HttpRequest, object_id: int):
        try:
            planet = GamePlanet.objects.filter(planet_id=object_id).first()
            if planet:
                import_planet_infrastructure(planet.planet_natural_id)

            self.message_user(request, 'Planet refreshed from FIO.', messages.SUCCESS)
        except Exception:
            self.message_user(request, 'Failed to refresh planet.', messages.ERROR)

        opts = self.model._meta
        changelist_url = reverse(f'admin:{opts.app_label}_{opts.model_name}_changelist')
        return HttpResponseRedirect(changelist_url)
