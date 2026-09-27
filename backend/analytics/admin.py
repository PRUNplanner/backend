import structlog
from core.admin import ReadOnlyAdminMixin, changelist_url, log_admin_action
from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponseRedirect
from unfold.admin import ModelAdmin
from unfold.decorators import action

from analytics.models import AnalyticsEmpireMaterialSnapshot, AnalyticsPlanAggregate, AppStatistic
from analytics.tasks import analytics_update_plan_insight_aggregates

logger = structlog.get_logger(__name__)


@admin.register(AppStatistic)
class AppStatisticAdmin(ReadOnlyAdminMixin, ModelAdmin):
    list_display = [
        'date',
        'user_count',
        'signups',
        'users_active_today',
        'users_active_7d',
        'users_active_30d',
        'plan_count',
        'empire_count',
        'cx_count',
    ]
    search_fields = ['date']
    ordering = ['-date']
    date_hierarchy = 'date'


@admin.register(AnalyticsPlanAggregate)
class AnalyticsPlanAggregateAdmin(ReadOnlyAdminMixin, ModelAdmin):
    list_display = ['planet_natural_id', 'total_plans_analyzed', 'last_updated']
    search_fields = ['planet_natural_id']
    ordering = ['-last_updated']

    actions_list = ['action_aggregate_all']

    @action(description='Run aggregator', url_path='analytics-aggregate-all', icon='play_arrow')
    def action_aggregate_all(self, request: HttpRequest) -> HttpResponseRedirect:
        try:
            analytics_update_plan_insight_aggregates.delay()
            log_admin_action(request, AnalyticsPlanAggregate, 'Queued plan insight aggregation')
            messages.success(request, 'Queued the plan insight aggregation. Counts land in the worker log.')
        except Exception:
            logger.exception('admin_aggregate_queue_failed')
            messages.error(request, 'Could not queue the plan insight aggregation.')
        return HttpResponseRedirect(changelist_url(AnalyticsPlanAggregate))


@admin.register(AnalyticsEmpireMaterialSnapshot)
class AnalyticsEmpireMaterialSnapshotAdmin(ReadOnlyAdminMixin, ModelAdmin):
    list_display = ['id', 'empire', 'material_ticker', 'production', 'consumption', 'delta']
    search_fields = ['empire__uuid', 'material_ticker']
    list_select_related = ['empire']

    def get_queryset(self, request: HttpRequest):
        # the empire column only needs its name, never the empire_state JSON
        return super().get_queryset(request).defer('empire__empire_state')
