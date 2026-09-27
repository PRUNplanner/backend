from datetime import timedelta

import structlog
from django.conf import settings as django_settings
from django.contrib import admin, messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import Count, Min, Model, Q, QuerySet
from django.db.models.functions import TruncDate
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse
from django.utils import timezone
from django.utils.text import Truncator
from django.utils.timesince import timesince
from django.views.generic import TemplateView
from django_celery_beat.admin import (
    CrontabScheduleAdmin as BaseCrontabScheduleAdmin,
    PeriodicTaskAdmin as BasePeriodicTaskAdmin,
    PeriodicTaskForm,
    TaskSelectWidget,
)
from django_celery_beat.models import ClockedSchedule, CrontabSchedule, IntervalSchedule, PeriodicTask, SolarSchedule
from unfold.admin import ModelAdmin
from unfold.decorators import action, display
from unfold.views import UnfoldModelAdminViewMixin
from unfold.widgets import UnfoldAdminSelectWidget, UnfoldAdminTextInputWidget

from core.admin_charts import STATUS, Fact, Summary
from core.models import CeleryAutomationModel
from core.services.task_health import STATES, task_health_rows

logger = structlog.get_logger(__name__)

SUMMARY_TTL_SECONDS = 300
BADGE_TTL_SECONDS = 60


# ---------------------------------------------------------------------------
# site-wide callbacks (UNFOLD settings)


def environment_callback(request: HttpRequest) -> list[str]:
    if getattr(django_settings, 'ENVIRONMENT_NAME', 'local') == 'production':
        return ['Production', 'danger']
    return ['Local', 'success']


def environment_title_prefix_callback(request: HttpRequest) -> str:
    return f'[{environment_callback(request)[0]}]'


def badge_stuck_planets(request: HttpRequest) -> str:
    """Sidebar badge on Planets: rows stuck in pending with an expired lease."""
    from gamedata.models import GamePlanet

    try:
        count = cache.get_or_set(
            'admin:badge:stuck_planets',
            lambda: GamePlanet.objects.filter(CeleryAutomationModel.stuck_q()).count(),
            BADGE_TTL_SECONDS,
        )
    except Exception:
        logger.exception('admin_badge_failed', badge='stuck_planets')
        return ''
    # '' hides the badge (see templates/unfold/helpers/app_list_badge.html)
    return str(count) if count else ''


# ---------------------------------------------------------------------------
# shared helpers


def log_admin_action(
    request: HttpRequest, target: Model | type[Model], message: str, action_flag: int = CHANGE
) -> LogEntry:
    """One audit entry for a custom admin action, on an object or (for bulk/list actions) on its model."""
    instance = target if isinstance(target, Model) else None
    model = type(target) if isinstance(target, Model) else target
    return LogEntry.objects.create(
        user_id=request.user.pk,
        content_type=ContentType.objects.get_for_model(model, for_concrete_model=False),
        object_id=str(instance.pk) if instance is not None else None,
        object_repr=(str(instance) if instance is not None else str(model._meta.verbose_name_plural))[:200],
        action_flag=action_flag,
        change_message=message,
    )


def is_changelist(request: HttpRequest) -> bool:
    """Changelist-only query shaping (prefetches for row sections), kept out of autocomplete and change views."""
    match = request.resolver_match
    return bool(match and match.url_name and match.url_name.endswith('_changelist'))


def changelist_url(model: type[Model], query: str = '') -> str:
    opts = model._meta
    url = reverse(f'admin:{opts.app_label}_{opts.model_name}_changelist')
    return f'{url}?{query}' if query else url


def daily_counts(queryset: QuerySet, field: str, days: int) -> tuple[list[str], list[int]]:
    """Rows created per day over the last `days` days (today included), zero-filled, oldest first."""
    today = timezone.now().date()
    start = today - timedelta(days=days - 1)
    per_day = dict(
        queryset.filter(**{f'{field}__date__gte': start})
        .annotate(day=TruncDate(field))
        .order_by()
        .values('day')
        .annotate(n=Count('pk'))
        .values_list('day', 'n')
    )
    dates = [start + timedelta(days=offset) for offset in range(days)]
    return [f'{d:%m-%d}' for d in dates], [per_day.get(d, 0) for d in dates]


def percent(part: int, whole: int) -> str:
    return f'{part / whole * 100:.1f}%' if whole else '—'


def confirm_action(
    request: HttpRequest,
    *,
    title: str,
    message: str,
    submit_label: str,
    cancel_url: str,
    expected: str | None = None,
) -> TemplateResponse:
    """
    Confirmation page for a destructive custom action: the action link is a GET, so it only renders this page, and
    the action runs on the CSRF-protected POST back to the same URL. With `expected`, the operator has to type it.
    """
    return TemplateResponse(
        request,
        'admin/confirm_action.html',
        {
            **admin.site.each_context(request),
            'title': title,
            'message': message,
            'submit_label': submit_label,
            'cancel_url': cancel_url,
            'expected': expected,
        },
    )


class ReadOnlyAdminMixin:
    """Derived or imported data: viewable and deletable (standard confirm view), never added or edited by hand."""

    def has_add_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False


class SummaryStripMixin:
    """
    A changelist strip (at most 4 tiles and 1 chart) above the list, via Unfold's `list_before_template`. Cached for
    5 minutes and ignoring the current filters; a failure hides the strip and is logged, never a 500.
    """

    list_before_template = 'admin/summary/strip.html'

    def get_summary(self, request: HttpRequest) -> Summary:
        raise NotImplementedError

    def cached_summary(self, request: HttpRequest) -> Summary | None:
        label = self.model._meta.label_lower  # ty: ignore[unresolved-attribute]
        key = f'admin:summary:{label}'
        # a cache outage only costs the caching, the strip is still built
        try:
            summary = cache.get(key)
        except Exception:
            logger.exception('admin_summary_cache_failed', model=label)
            summary = None
        if summary is not None:
            return summary

        try:
            summary = self.get_summary(request)
        except Exception:
            logger.exception('admin_summary_failed', model=label)
            return None

        try:
            cache.set(key, summary, SUMMARY_TTL_SECONDS)
        except Exception:
            logger.exception('admin_summary_cache_failed', model=label)
        return summary


class ChangeHeaderMixin[M: Model]:
    """A facts header above the change form, via Unfold's `change_form_before_template`."""

    change_form_before_template = 'admin/summary/header.html'

    def get_header(self, request: HttpRequest, obj: M) -> list[Fact]:
        raise NotImplementedError

    def safe_header(self, request: HttpRequest, obj: M) -> list[Fact] | None:
        try:
            return self.get_header(request, obj)
        except Exception:
            logger.exception('admin_header_failed', model=obj._meta.label_lower)
            return None


# ---------------------------------------------------------------------------
# automation (CeleryAutomationModel admins: planets, FIO player data)

AUTOMATION_STATUS_LABELS = {'ok': 'success', 'pending': 'info', 'retrying': 'warning', 'failed': 'danger'}
# status -> (label, icon, status colour) for the stacked status bar; never colour alone
AUTOMATION_SEGMENTS = {
    'ok': ('OK', 'check_circle', 'good'),
    'pending': ('Pending', 'hourglass_top', 'warning'),
    'retrying': ('Retrying', 'autorenew', 'serious'),
    'failed': ('Failed', 'error', 'critical'),
}


class StuckPendingFilter(admin.SimpleListFilter):
    title = 'stuck'
    parameter_name = 'stuck'

    def lookups(self, request: HttpRequest, model_admin: admin.ModelAdmin) -> list[tuple[str, str]]:
        return [('lease_expired', 'Stuck pending (lease expired)')]

    def queryset(self, request: HttpRequest, queryset: QuerySet) -> QuerySet:
        if self.value() == 'lease_expired':
            return queryset.filter(CeleryAutomationModel.stuck_q())
        return queryset


class PermanentlyFailedFilter(admin.SimpleListFilter):
    title = 'retries'
    parameter_name = 'permanently_failed'

    def lookups(self, request: HttpRequest, model_admin: admin.ModelAdmin) -> list[tuple[str, str]]:
        return [('yes', 'Permanently failed')]

    def queryset(self, request: HttpRequest, queryset: QuerySet) -> QuerySet:
        if self.value() == 'yes':
            return queryset.filter(CeleryAutomationModel.failed_q())
        return queryset


class AutomationAdminMixin[M: CeleryAutomationModel]:
    """Status columns, filters and "Reset & retry" (selection and detail) for CeleryAutomationModel admins."""

    automation_columns = ('automation_status', 'automation_error_count', 'automation_next_retry_at', 'error_excerpt')

    @display(description='Status', label=AUTOMATION_STATUS_LABELS, ordering='automation_refresh_status')
    def automation_status(self, obj: CeleryAutomationModel) -> str:
        return obj.automation_refresh_status

    @display(description='Last error')
    def error_excerpt(self, obj: CeleryAutomationModel) -> str:
        return Truncator(obj.automation_error or '').chars(60) or '—'

    def enqueue_refresh(self, obj: M) -> None:
        raise NotImplementedError

    def reset_and_retry(self, queryset: QuerySet) -> int:
        rows = list(queryset)
        queryset.model.objects.filter(pk__in=[row.pk for row in rows]).update(
            automation_refresh_status='ok', automation_error_count=0, automation_next_retry_at=None
        )
        for row in rows:
            self.enqueue_refresh(row)
        return len(rows)

    @admin.action(description='Reset & retry')
    def action_reset_and_retry(self, request: HttpRequest, queryset: QuerySet) -> None:
        count = self.reset_and_retry(queryset)
        log_admin_action(request, queryset.model, f'Reset & retry: {count} rows, refresh queued')
        messages.success(request, f'Reset {count} rows and queued a refresh for each.')

    @action(description='Reset & retry', url_path='detail-reset-and-retry', icon='restart_alt')
    def action_detail_reset_and_retry(self, request: HttpRequest, object_id: str) -> HttpResponse:
        model = self.model  # ty: ignore[unresolved-attribute]
        queryset = model.objects.filter(pk=object_id)
        obj = queryset.first()
        if obj is not None:
            self.reset_and_retry(queryset)
            log_admin_action(request, obj, 'Reset & retry, refresh queued')
            messages.success(request, 'Reset and queued a refresh.')
        opts = model._meta
        return HttpResponseRedirect(reverse(f'admin:{opts.app_label}_{opts.model_name}_change', args=[object_id]))

    def get_summary(self, request: HttpRequest) -> Summary:
        """Strip shared by automation admins: freshness, stuck, failed, oldest refresh and the status split."""
        model = self.model  # ty: ignore[unresolved-attribute]
        now = timezone.now()
        stats = model.objects.aggregate(
            total=Count('pk'),
            refreshed=Count('pk', filter=Q(automation_last_refreshed_at__gte=now - timedelta(hours=24))),
            stuck=Count('pk', filter=CeleryAutomationModel.stuck_q()),
            permanently_failed=Count('pk', filter=CeleryAutomationModel.failed_q()),
            oldest=Min('automation_last_refreshed_at'),
            **{status: Count('pk', filter=Q(automation_refresh_status=status)) for status in AUTOMATION_SEGMENTS},
        )
        total = stats['total'] or 0
        oldest = stats['oldest']
        return {
            'tiles': [
                {'label': 'Refreshed in 24 h', 'value': f'{stats["refreshed"]:,}', 'sub': f'of {total:,}'},
                {
                    'label': 'Stuck pending',
                    'value': f'{stats["stuck"]:,}',
                    'sub': 'lease expired',
                    'href': changelist_url(model, 'stuck=lease_expired'),
                },
                {
                    'label': 'Permanently failed',
                    'value': f'{stats["permanently_failed"]:,}',
                    'sub': f'{CeleryAutomationModel.MAX_RETRIES}+ errors',
                    'href': changelist_url(model, 'permanently_failed=yes'),
                },
                {
                    'label': 'Oldest refresh',
                    'value': f'{timesince(oldest, now).split(",")[0]} ago' if oldest else '—',
                    'href': changelist_url(model, 'o=' + self._last_refreshed_order()),
                },
            ],
            'segments_title': 'Refresh status',
            'segments': [
                {
                    'label': label,
                    'icon': icon,
                    'value': stats[status],
                    'pct': round(stats[status] / total * 100, 2) if total else 0.0,
                    'colour': STATUS[severity],
                    'href': changelist_url(model, f'automation_refresh_status={status}'),
                }
                for status, (label, icon, severity) in AUTOMATION_SEGMENTS.items()
            ],
        }

    def _last_refreshed_order(self) -> str:
        # changelist sort parameter: ascending on the last-refreshed column
        columns = list(self.list_display)  # ty: ignore[unresolved-attribute]
        return str(columns.index('automation_last_refreshed_at') + 1)

    def automation_facts(self, obj: CeleryAutomationModel) -> list[Fact]:
        return [
            {
                'label': 'Status',
                'value': obj.automation_refresh_status,
                'badge': AUTOMATION_STATUS_LABELS.get(obj.automation_refresh_status, 'info'),
            },
            {'label': 'Last refreshed', 'value': f'{obj.automation_last_refreshed_at:%Y-%m-%d %H:%M} UTC'},
            {'label': 'Error count', 'value': f'{obj.automation_error_count} / {obj.MAX_RETRIES}'},
            {
                'label': 'Next retry / lease',
                'value': f'{obj.automation_next_retry_at:%Y-%m-%d %H:%M} UTC' if obj.automation_next_retry_at else '—',
            },
            {'label': 'Last error', 'value': obj.automation_error or '—', 'block': bool(obj.automation_error)},
        ]


# ---------------------------------------------------------------------------
# django-celery-beat, with the Task health page

admin.site.unregister(PeriodicTask)
admin.site.unregister(IntervalSchedule)
admin.site.unregister(CrontabSchedule)
admin.site.unregister(SolarSchedule)
admin.site.unregister(ClockedSchedule)


class UnfoldTaskSelectWidget(UnfoldAdminSelectWidget, TaskSelectWidget):
    pass


class UnfoldPeriodicTaskForm(PeriodicTaskForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['task'].widget = UnfoldAdminTextInputWidget()
        self.fields['regtask'].widget = UnfoldTaskSelectWidget()


class TaskHealthView(UnfoldModelAdminViewMixin, TemplateView):
    title = 'Task health'
    permission_required = ()
    template_name = 'admin/task_health.html'

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        state = self.request.GET.get('state') or None
        state = state if state in STATES else None
        rows, redis_ok = task_health_rows(state=state)
        return super().get_context_data(**kwargs, rows=rows, redis_ok=redis_ok, state=state, states=STATES)


@admin.register(PeriodicTask)
class PeriodicTaskAdmin(BasePeriodicTaskAdmin, ModelAdmin):
    form = UnfoldPeriodicTaskForm
    list_before_template = 'admin/summary/task_health_link.html'

    def get_urls(self) -> list[URLPattern]:
        return [
            path(
                'health/',
                self.admin_site.admin_view(TaskHealthView.as_view(model_admin=self)),
                name='task_health',
            ),
            *super().get_urls(),
        ]


@admin.register(IntervalSchedule)
class IntervalScheduleAdmin(ModelAdmin):
    pass


@admin.register(CrontabSchedule)
class CrontabScheduleAdmin(BaseCrontabScheduleAdmin, ModelAdmin):
    pass
