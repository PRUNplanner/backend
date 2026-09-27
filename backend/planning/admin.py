from datetime import timedelta

from core.admin import ChangeHeaderMixin, SummaryStripMixin, changelist_url, daily_counts, is_changelist, percent
from core.admin_charts import Chart, Fact, Summary, bar, fold_other
from core.env import settings
from django.contrib import admin
from django.db.models import Count, JSONField, Prefetch, Q, QuerySet, Sum
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django_json_widget.widgets import JSONEditorWidget
from gamedata.models import GamePlanet
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.sections import TableSection

from planning.models import PlanningCX, PlanningEmpire, PlanningEmpirePlan, PlanningPlan, PlanningShared

NEW_PLANS_CHART_DAYS = 90
JSON_WIDGETS = {JSONField: {'widget': JSONEditorWidget}}
CREATED_MODIFIED_FILTERS = [('created_at', RangeDateTimeFilter), ('modified_at', RangeDateTimeFilter)]
# the columns a plan list or link label needs; never plan_data
PLAN_LIST_FIELDS = ('uuid', 'user_id', 'plan_name', 'planet_natural_id', 'modified_at')


def _split_chart(title: str, rows: list[tuple[str, int]]) -> Chart:
    """A category split as horizontal bars in one colour; beyond ten categories the rest folds into "Other"."""
    rows = fold_other(rows, keep=9)
    return {
        'title': title,
        'kind': 'bar',
        'config': bar([label for label, _ in rows], [('Count', [value for _, value in rows])], horizontal=True),
        'height': max(120, 26 * len(rows)),
    }


def _plan_link(plan: PlanningPlan) -> str:
    return format_html(
        '<a href="{}">{}</a>', reverse('admin:planning_planningplan_change', args=[plan.pk]), plan.plan_name
    )


class PlanningEmpirePlanInline(TabularInline):
    model = PlanningEmpirePlan
    extra = 0
    tab = True

    autocomplete_fields = ['plan', 'user']

    def get_queryset(self, request: HttpRequest) -> QuerySet[PlanningEmpirePlan]:
        return super().get_queryset(request).select_related('plan', 'user').defer('plan__plan_data')


class PlanningEmpireInline(TabularInline):
    model = PlanningEmpire
    fields = [
        'empire_name',
        'user',
        'empire_faction',
        'empire_permits_used',
        'empire_permits_total',
        'needs_state_sync',
    ]
    extra = 0
    tab = True
    show_change_link = True

    autocomplete_fields = ['user']

    def get_queryset(self, request: HttpRequest) -> QuerySet[PlanningEmpire]:
        return super().get_queryset(request).select_related('user').defer('empire_state')


class EmpirePlansSection(TableSection):
    verbose_name = 'Linked plans'
    related_name = 'plans'
    fields = ['plan', 'planet_natural_id', 'modified_at']

    def plan(self, obj: PlanningPlan) -> str:
        return _plan_link(obj)


@admin.register(PlanningPlan)
class PlanningPlanAdmin(SummaryStripMixin, ChangeHeaderMixin[PlanningPlan], ModelAdmin):
    list_display = ['uuid', 'user', 'planet_natural_id', 'plan_name', 'plan_cogc', 'created_at', 'modified_at']
    list_select_related = ['user']
    search_fields = ['uuid', 'planet_natural_id', 'plan_name', 'user__username']
    ordering = ['-modified_at']
    list_filter = [('plan_cogc', ChoicesDropdownFilter), 'plan_corphq', *CREATED_MODIFIED_FILTERS]
    list_filter_submit = True
    date_hierarchy = 'created_at'
    readonly_fields = ['uuid', 'created_at', 'modified_at']
    autocomplete_fields = ['user']

    formfield_overrides = JSON_WIDGETS

    def get_queryset(self, request: HttpRequest) -> QuerySet[PlanningPlan]:
        return super().get_queryset(request).defer('plan_data')

    def get_summary(self, request: HttpRequest) -> Summary:
        total = PlanningPlan.objects.count()
        created = PlanningPlan.objects.filter(created_at__gte=timezone.now() - timedelta(days=7)).count()
        shared = PlanningShared.objects.count()
        labels, values = daily_counts(PlanningPlan.objects.all(), 'created_at', NEW_PLANS_CHART_DAYS)
        cogc = list(
            PlanningPlan.objects.order_by().values('plan_cogc').annotate(n=Count('pk')).values_list('plan_cogc', 'n')
        )
        return {
            'tiles': [
                {'label': 'Plans', 'value': f'{total:,}'},
                {'label': 'Created in 7 d', 'value': f'{created:,}'},
                {
                    'label': 'Shared',
                    'value': percent(shared, total),
                    'sub': f'{shared:,} plans',
                    'href': changelist_url(PlanningShared),
                },
            ],
            'charts': [
                {
                    'title': f'New plans per day, last {NEW_PLANS_CHART_DAYS} days',
                    'kind': 'bar',
                    'config': bar(labels, [('New plans', values)]),
                    'height': 180,
                },
                _split_chart('Plans by COGC program', [(cogc_name or '—', n) for cogc_name, n in cogc]),
            ],
        }

    def get_header(self, request: HttpRequest, obj: PlanningPlan) -> list[Fact]:
        planet_id = (
            GamePlanet.objects.filter(planet_natural_id=obj.planet_natural_id).values_list('pk', flat=True).first()
        )
        empire_count = PlanningEmpire.objects.filter(plans=obj).count()
        share = PlanningShared.objects.filter(plan=obj).only('uuid', 'view_count').first()
        facts: list[Fact] = [
            {
                'label': 'Owner',
                'value': str(obj.user),
                'href': reverse('admin:user_user_change', args=[obj.user_id]),  # ty: ignore[unresolved-attribute]
            },
            {
                'label': 'Planet',
                'value': obj.planet_natural_id,
                'href': reverse('admin:gamedata_gameplanet_change', args=[planet_id]) if planet_id else '',
            },
            {
                'label': 'In empires',
                'value': f'{empire_count:,}',
                'href': changelist_url(PlanningEmpire, f'plans={obj.pk}'),
            },
        ]
        if share is None:
            facts.append({'label': 'Share link', 'value': 'not shared'})
        else:
            facts.append(
                {
                    'label': 'Share link',
                    'value': f'{share.view_count:,} views',
                    'href': reverse('admin:planning_planningshared_change', args=[share.pk]),
                }
            )
        return facts


@admin.register(PlanningEmpire)
class PlanningEmpireAdmin(SummaryStripMixin, ChangeHeaderMixin[PlanningEmpire], ModelAdmin):
    list_display = [
        'uuid',
        'user',
        'empire_name',
        'empire_faction',
        'cx',
        'needs_state_sync',
        'created_at',
        'modified_at',
    ]
    list_select_related = ['user', 'cx']
    search_fields = ['uuid', 'empire_name', 'user__username']
    ordering = ['-modified_at']
    list_filter = [('empire_faction', ChoicesDropdownFilter), 'needs_state_sync', *CREATED_MODIFIED_FILTERS]
    list_filter_submit = True
    date_hierarchy = 'created_at'
    readonly_fields = ['uuid', 'created_at', 'modified_at']
    autocomplete_fields = ['user', 'cx']
    list_sections = [EmpirePlansSection]

    inlines = [PlanningEmpirePlanInline]

    formfield_overrides = JSON_WIDGETS

    def get_queryset(self, request: HttpRequest) -> QuerySet[PlanningEmpire]:
        queryset = super().get_queryset(request).defer('empire_state', 'cx__cx_data')
        if is_changelist(request):
            # one query for every row's expandable plan list
            queryset = queryset.prefetch_related(
                Prefetch('plans', queryset=PlanningPlan.objects.only(*PLAN_LIST_FIELDS).order_by('-modified_at'))
            )
        return queryset

    def get_summary(self, request: HttpRequest) -> Summary:
        stats = PlanningEmpire.objects.aggregate(
            total=Count('pk'), needs_sync=Count('pk', filter=Q(needs_state_sync=True))
        )
        links = PlanningEmpirePlan.objects.count()
        factions = list(
            PlanningEmpire.objects.order_by()
            .values('empire_faction')
            .annotate(n=Count('pk'))
            .values_list('empire_faction', 'n')
        )
        return {
            'tiles': [
                {'label': 'Empires', 'value': f'{stats["total"]:,}'},
                {
                    'label': 'Needs state sync',
                    'value': f'{stats["needs_sync"]:,}',
                    'href': changelist_url(PlanningEmpire, 'needs_state_sync__exact=1'),
                },
                {
                    'label': 'Plans per empire',
                    'value': f'{links / stats["total"]:.1f}' if stats['total'] else '—',
                    'sub': 'average',
                },
            ],
            'charts': [_split_chart('Empires by faction', factions)],
        }

    def get_header(self, request: HttpRequest, obj: PlanningEmpire) -> list[Fact]:
        cx = obj.cx
        return [
            {
                'label': 'Owner',
                'value': str(obj.user),
                'href': reverse('admin:user_user_change', args=[obj.user_id]),  # ty: ignore[unresolved-attribute]
            },
            {
                'label': 'CX preference',
                'value': cx.cx_name if cx else '—',
                'href': reverse('admin:planning_planningcx_change', args=[cx.pk]) if cx else '',
            },
            {
                'label': 'Plans',
                'value': f'{PlanningEmpirePlan.objects.filter(empire=obj).count():,}',
                'href': changelist_url(PlanningPlan, f'empires={obj.pk}'),
            },
            {
                'label': 'State sync',
                'value': 'pending' if obj.needs_state_sync else 'in sync',
                'badge': 'warning' if obj.needs_state_sync else 'success',
            },
        ]


@admin.register(PlanningCX)
class PlanningCXAdmin(ModelAdmin):
    list_display = ['uuid', 'user', 'cx_name', 'created_at', 'modified_at']
    list_select_related = ['user']
    search_fields = ['uuid', 'cx_name', 'user__username']
    ordering = ['-modified_at']
    list_filter = CREATED_MODIFIED_FILTERS
    list_filter_submit = True
    date_hierarchy = 'created_at'
    readonly_fields = ['uuid', 'created_at', 'modified_at']
    autocomplete_fields = ['user']

    inlines = [PlanningEmpireInline]

    formfield_overrides = JSON_WIDGETS

    def get_queryset(self, request: HttpRequest) -> QuerySet[PlanningCX]:
        return super().get_queryset(request).defer('cx_data')


@admin.register(PlanningEmpirePlan)
class PlanningEmpirePlanAdmin(ModelAdmin):
    list_display = ['uuid', 'user', 'empire', 'plan']
    list_select_related = ['user', 'empire', 'plan']
    search_fields = ['uuid', 'user__username', 'empire__empire_name', 'plan__plan_name']
    autocomplete_fields = ['user', 'empire', 'plan']

    def get_queryset(self, request: HttpRequest) -> QuerySet[PlanningEmpirePlan]:
        return super().get_queryset(request).defer('empire__empire_state', 'plan__plan_data')


@admin.register(PlanningShared)
class PlanningSharedAdmin(SummaryStripMixin, ModelAdmin):
    list_display = ['uuid', 'user', 'plan', 'view_count', 'created_at', 'modified_at']
    list_select_related = ['user', 'plan']
    search_fields = ['uuid', 'user__username', 'plan__plan_name']
    ordering = ['-view_count']
    list_filter = CREATED_MODIFIED_FILTERS
    list_filter_submit = True
    readonly_fields = ['uuid', 'created_at', 'modified_at']
    autocomplete_fields = ['user', 'plan']

    def get_queryset(self, request: HttpRequest) -> QuerySet[PlanningShared]:
        return super().get_queryset(request).defer('plan__plan_data')

    def get_view_on_site_url(self, obj: PlanningShared | None = None) -> str | None:
        # the frontend's public share page; hidden when FRONTEND_URL is not configured
        if obj is None or not settings.frontend_url:
            return None
        return f'{settings.frontend_url.rstrip("/")}/shared/{obj.uuid}'

    def get_summary(self, request: HttpRequest) -> Summary:
        stats = PlanningShared.objects.aggregate(shares=Count('pk'), views=Sum('view_count'))
        top = (
            PlanningShared.objects.select_related('plan')
            .only('uuid', 'view_count', 'plan__uuid', 'plan__plan_name')
            .order_by('-view_count')
            .first()
        )
        return {
            'tiles': [
                {'label': 'Shares', 'value': f'{stats["shares"]:,}'},
                {'label': 'Total views', 'value': f'{stats["views"] or 0:,}'},
                {
                    'label': 'Top plan',
                    'value': top.plan.plan_name if top else '—',
                    'sub': f'{top.view_count:,} views' if top else '',
                    'href': reverse('admin:planning_planningshared_change', args=[top.pk]) if top else '',
                },
            ],
        }
