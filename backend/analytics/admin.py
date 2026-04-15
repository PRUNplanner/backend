from django.contrib import admin, messages
from django.db.models import JSONField
from django.shortcuts import redirect
from django_json_widget.widgets import JSONEditorWidget
from unfold.admin import ModelAdmin
from unfold.decorators import action

from analytics.models import AnalyticsPlanAggregate, AppStatistic


@admin.register(AppStatistic)
class AppStatisticAdmin(ModelAdmin):
    list_display = [
        'date',
        'user_count',
        'users_active_today',
        'users_active_30d',
        'plan_count',
        'empire_count',
        'cx_count',
    ]
    search_fields = ['date']
    readonly_fields = ['last_updated']


@admin.register(AnalyticsPlanAggregate)
class AnalyticsPlanAggregateAdmin(ModelAdmin):
    list_display = ['planet_natural_id', 'total_plans_analyzed', 'last_updated']
    search_fields = ['planet_natural_id']
    ordering = ['-last_updated']

    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget},
    }

    actions_list = ['action_aggregate_all']

    @action(description='Run Aggregator', url_path='analytics-aggregate-all')
    def action_aggregate_all(self, request):

        from analytics.services.PlanInsightAggregatorService import PlanInsightAggregatorService

        try:
            aggregator = PlanInsightAggregatorService()
            processed, deleted = aggregator.aggregate_all_plans()
            self.message_user(
                request,
                f'Aggregates updated! Processed: {processed}, deleted {deleted}.',
                messages.SUCCESS,
            )
        except Exception:
            self.message_user(request, 'Error processing aggregates.', messages.ERROR)

        return redirect('../')
