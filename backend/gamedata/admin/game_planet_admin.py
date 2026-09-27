import structlog
from analytics.models import AnalyticsPlanAggregate
from core.admin import (
    AutomationAdminMixin,
    ChangeHeaderMixin,
    Fact,
    PermanentlyFailedFilter,
    ReadOnlyAdminMixin,
    StuckPendingFilter,
    SummaryStripMixin,
    changelist_url,
    confirm_action,
    log_admin_action,
)
from django.contrib import admin, messages
from django.contrib.admin.models import DELETION
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from planning.models import PlanningPlan
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.contrib.filters.admin import RangeDateTimeFilter
from unfold.decorators import action
from unfold.enums import ActionVariant

from gamedata.admin.fio_import import queue_fio_import
from gamedata.models import (
    GamePlanet,
    GamePlanetCOGCProgram,
    GamePlanetInfrastructureReport,
    GamePlanetProductionFee,
    GamePlanetResource,
)
from gamedata.tasks import gamedata_refresh_planet_infrastructure, gamedata_refresh_single_planet

logger = structlog.get_logger(__name__)


class PlanetCOGCProgramInline(ReadOnlyAdminMixin, TabularInline):
    model = GamePlanetCOGCProgram
    can_delete = False
    extra = 0
    fk_name = 'planet'
    tab = True


class PlanetResourceInline(ReadOnlyAdminMixin, TabularInline):
    model = GamePlanetResource
    can_delete = False
    extra = 0
    fk_name = 'planet'
    tab = True


class PlanetProductionFeeInline(ReadOnlyAdminMixin, TabularInline):
    model = GamePlanetProductionFee
    can_delete = False
    extra = 0
    fk_name = 'planet'
    tab = True


class PlanetInfrastructureReportInline(ReadOnlyAdminMixin, StackedInline):
    model = GamePlanetInfrastructureReport
    can_delete = False
    extra = 0
    fk_name = 'planet'
    tab = True


@admin.register(GamePlanet)
class GamePlanetAdmin(
    ReadOnlyAdminMixin, AutomationAdminMixin[GamePlanet], SummaryStripMixin, ChangeHeaderMixin[GamePlanet], ModelAdmin
):
    list_display = [
        'planet_natural_id',
        'planet_name',
        *AutomationAdminMixin.automation_columns,
        'automation_last_refreshed_at',
    ]
    search_fields = ['planet_natural_id', 'planet_name']
    list_filter = [
        'automation_refresh_status',
        StuckPendingFilter,
        PermanentlyFailedFilter,
        ('automation_last_refreshed_at', RangeDateTimeFilter),
    ]
    list_filter_submit = True
    ordering = ['-automation_last_refreshed_at']

    inlines = [
        PlanetResourceInline,
        PlanetCOGCProgramInline,
        PlanetProductionFeeInline,
        PlanetInfrastructureReportInline,
    ]

    actions = ['action_reset_and_retry']
    actions_list = ['action_fio_import_all_planet', 'action_delete_all_planets']
    actions_row = ['action_refresh_planet', 'action_refresh_planet_infrastructure']
    actions_detail = ['action_detail_reset_and_retry']

    def enqueue_refresh(self, obj: GamePlanet) -> None:
        gamedata_refresh_single_planet.delay(obj.planet_natural_id)

    def get_header(self, request: HttpRequest, obj: GamePlanet) -> list[Fact]:
        plan_count = PlanningPlan.objects.filter(planet_natural_id=obj.planet_natural_id).count()
        aggregate = AnalyticsPlanAggregate.objects.filter(planet_natural_id=obj.planet_natural_id).only('pk').first()
        facts = self.automation_facts(obj)
        facts.insert(
            3,
            {
                'label': 'Plans on this planet',
                'value': f'{plan_count:,}',
                'href': changelist_url(PlanningPlan, f'planet_natural_id={obj.planet_natural_id}'),
            },
        )
        if aggregate is not None:
            facts.insert(
                4,
                {
                    'label': 'Plan aggregate',
                    'value': 'View insights',
                    'href': reverse('admin:analytics_analyticsplanaggregate_change', args=[aggregate.pk]),
                },
            )
        return facts

    @action(description='Import from FIO', url_path='changelist-fio-import-all-planet', icon='download')
    def action_fio_import_all_planet(self, request: HttpRequest) -> HttpResponseRedirect:
        return queue_fio_import(request, GamePlanet, 'planets')

    @action(
        description='Delete all planets',
        url_path='changelist-delete-all-planets',
        icon='delete_forever',
        variant=ActionVariant.DANGER,
    )
    def action_delete_all_planets(self, request: HttpRequest) -> HttpResponse:
        """GET only shows the confirmation; the POST must carry the current planet count."""
        count = GamePlanet.objects.count()
        if request.method != 'POST':
            return confirm_action(
                request,
                title='Delete all planets',
                message=(
                    f'This permanently deletes all {count:,} planets with their resources, COGC programs, fees and '
                    'infrastructure reports. Plans are not deleted. A new "Import from FIO" restores the planets.'
                ),
                submit_label=f'Delete {count:,} planets',
                cancel_url=changelist_url(GamePlanet),
                expected=str(count),
            )

        if request.POST.get('confirm', '').strip() != str(count):
            messages.error(request, 'The typed count does not match the current planet count. Nothing was deleted.')
            return HttpResponseRedirect(request.path)

        GamePlanet.objects.all().delete()
        log_admin_action(request, GamePlanet, f'Deleted all {count} planets', action_flag=DELETION)
        logger.info('admin_delete_all_planets', count=count, user=request.user.pk)
        messages.success(request, f'Deleted {count:,} planets.')
        return HttpResponseRedirect(changelist_url(GamePlanet))

    def _queue_row_refresh(self, request: HttpRequest, object_id: str, infrastructure: bool) -> HttpResponseRedirect:
        planet = GamePlanet.objects.filter(pk=object_id).only('planet_id', 'planet_natural_id').first()
        if planet is None:
            messages.error(request, 'Planet not found.')
            return HttpResponseRedirect(changelist_url(GamePlanet))

        what = 'infrastructure refresh' if infrastructure else 'refresh'
        try:
            task = gamedata_refresh_planet_infrastructure if infrastructure else gamedata_refresh_single_planet
            task.delay(planet.planet_natural_id)
            log_admin_action(request, planet, f'Queued {what}')
            messages.success(request, f'Queued {what} of {planet.planet_natural_id}.')
        except Exception:
            logger.exception('admin_planet_refresh_queue_failed', planet=planet.planet_natural_id, what=what)
            messages.error(request, f'Could not queue the {what} of {planet.planet_natural_id}.')
        return HttpResponseRedirect(changelist_url(GamePlanet))

    @action(description='Refresh', url_path='changelist-refresh-planet', icon='sync')
    def action_refresh_planet(self, request: HttpRequest, object_id: str) -> HttpResponseRedirect:
        return self._queue_row_refresh(request, object_id, infrastructure=False)

    @action(description='Refresh infrastructure', url_path='changelist-refresh-planet-infrastructure', icon='sync')
    def action_refresh_planet_infrastructure(self, request: HttpRequest, object_id: str) -> HttpResponseRedirect:
        return self._queue_row_refresh(request, object_id, infrastructure=True)
