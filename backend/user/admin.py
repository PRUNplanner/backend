from datetime import timedelta

import structlog
from core.admin import (
    ChangeHeaderMixin,
    ReadOnlyAdminMixin,
    SummaryStripMixin,
    changelist_url,
    confirm_action,
    daily_counts,
    is_changelist,
    log_admin_action,
    percent,
)
from core.admin_charts import Fact, Summary, bar
from django.contrib import admin, messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.db.models import Count, IntegerField, JSONField, Max, OuterRef, Prefetch, Q, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django_json_widget.widgets import JSONEditorWidget
from gamedata.models import GameFIOPlayerData
from gamedata.services.fio_refresh import fio_connection
from planning.models import PlanningCX, PlanningEmpire, PlanningPlan
from rest_framework_api_key.admin import APIKeyAdmin
from rest_framework_api_key.models import APIKey
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import RangeDateTimeFilter
from unfold.decorators import action, display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.sections import TableSection

from user.models import GlobalConfigWebhook, User, UserAPIKey, UserPreference, VerificationCode
from user.models.user import FIO_LINKED_Q
from user.models.verification_codes import VerificationeCodeChoices
from user.services.verification_service import VerificationService

logger = structlog.get_logger(__name__)

SIGNUP_CHART_DAYS = 90


def mask_secret(value: str | None) -> str:
    value = (value or '').strip()
    return f'••••{value[-4:]}' if value else 'not set'


# ---------------------------------------------------------------------------
# webhooks, preferences


@admin.register(GlobalConfigWebhook)
class GlobalConfigWebhookAdmin(ChangeHeaderMixin[GlobalConfigWebhook], ModelAdmin):
    list_display = ['path', 'sender', 'is_active', 'total_calls', 'last_received_at']
    readonly_fields = ['path', 'total_calls', 'last_received_at']

    def get_header(self, request: HttpRequest, obj: GlobalConfigWebhook) -> list[Fact]:
        url = request.build_absolute_uri(reverse('data:fio-webhook-ingest', args=[obj.path]))
        return [{'label': 'Webhook URL (copy into the sender)', 'value': url, 'block': True}]


@admin.register(UserPreference)
class UserPreferenceAdmin(ModelAdmin):
    list_display = ['user', 'updated_at']
    list_select_related = ['user']
    search_fields = ['user__username']
    autocomplete_fields = ['user']
    ordering = ['-updated_at']

    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget},
    }


# ---------------------------------------------------------------------------
# users


class FioLinkedFilter(admin.SimpleListFilter):
    title = 'FIO linked'
    parameter_name = 'fio'

    def lookups(self, request: HttpRequest, model_admin: admin.ModelAdmin) -> list[tuple[str, str]]:
        return [('yes', 'Yes'), ('no', 'No')]

    def queryset(self, request: HttpRequest, queryset: QuerySet) -> QuerySet:
        if self.value() == 'yes':
            return queryset.filter(FIO_LINKED_Q)
        if self.value() == 'no':
            return queryset.exclude(FIO_LINKED_Q)
        return queryset


def _count_by_user(model: type[PlanningPlan] | type[PlanningEmpire]) -> Coalesce:
    rows = model.objects.filter(user=OuterRef('pk')).order_by().values('user').annotate(n=Count('pk')).values('n')
    return Coalesce(Subquery(rows[:1], output_field=IntegerField()), Value(0))


class UserPlansSection(TableSection):
    verbose_name = 'Plans'
    related_name = 'plans'
    fields = ['plan', 'planet_natural_id', 'modified_at']

    def plan(self, obj: PlanningPlan) -> str:
        return format_html(
            '<a href="{}">{}</a>', reverse('admin:planning_planningplan_change', args=[obj.pk]), obj.plan_name
        )


class UserEmpiresSection(TableSection):
    verbose_name = 'Empires'
    related_name = 'empires'
    fields = ['empire', 'empire_faction', 'modified_at']

    def empire(self, obj: PlanningEmpire) -> str:
        return format_html(
            '<a href="{}">{}</a>', reverse('admin:planning_planningempire_change', args=[obj.pk]), obj.empire_name
        )


class UserPlanInline(ReadOnlyAdminMixin, TabularInline):
    model = PlanningPlan
    fields = ['plan_name', 'planet_natural_id', 'modified_at']
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True
    tab = True

    def get_queryset(self, request: HttpRequest) -> QuerySet[PlanningPlan]:
        return super().get_queryset(request).defer('plan_data').order_by('-modified_at')


class UserEmpireInline(ReadOnlyAdminMixin, TabularInline):
    model = PlanningEmpire
    fields = ['empire_name', 'empire_faction', 'needs_state_sync', 'modified_at']
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True
    tab = True

    def get_queryset(self, request: HttpRequest) -> QuerySet[PlanningEmpire]:
        return super().get_queryset(request).defer('empire_state').order_by('-modified_at')


class UserCXInline(ReadOnlyAdminMixin, TabularInline):
    model = PlanningCX
    fields = ['cx_name', 'modified_at']
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True
    tab = True

    def get_queryset(self, request: HttpRequest) -> QuerySet[PlanningCX]:
        return super().get_queryset(request).defer('cx_data').order_by('-modified_at')


class UserAPIKeyInline(ReadOnlyAdminMixin, TabularInline):
    model = UserAPIKey
    fields = ['name', 'prefix', 'created', 'last_used', 'revoked']
    readonly_fields = fields
    extra = 0
    can_delete = False
    tab = True
    verbose_name_plural = 'API keys'


@admin.register(User)
class UserAdmin(SummaryStripMixin, ChangeHeaderMixin[User], BaseUserAdmin, ModelAdmin):
    list_display = [
        'display_user',
        'id',
        'display_badges',
        'last_login',
        'date_joined',
        'plan_count',
        'empire_count',
    ]
    search_fields = ['username', 'email', 'id', 'prun_username']
    ordering = ['-last_login']
    list_per_page = 50
    empty_value_display = '—'
    list_filter = [
        'is_active',
        'is_staff',
        'is_email_verified',
        FioLinkedFilter,
        ('last_login', RangeDateTimeFilter),
        ('date_joined', RangeDateTimeFilter),
    ]
    list_filter_submit = True
    list_sections = [UserPlansSection, UserEmpiresSection]

    fieldsets = (
        (None, {'fields': ('username', 'password', 'email', 'is_email_verified')}),
        ('FIO', {'fields': ('prun_username', 'fio_apikey_masked')}),
        ('Activity', {'fields': ('date_joined', 'last_login')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
    )
    readonly_fields = ['fio_apikey_masked', 'date_joined', 'last_login']
    inlines = [UserPlanInline, UserEmpireInline, UserCXInline, UserAPIKeyInline]

    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    actions = [
        'action_refresh_fio',
        'action_clear_fio',
        'action_deactivate',
        'action_reactivate',
        'action_resend_verification',
    ]
    actions_detail = ['action_detail_clear_fio']

    def get_queryset(self, request: HttpRequest) -> QuerySet[User]:
        queryset = super().get_queryset(request)
        if not is_changelist(request):
            return queryset
        # counts as subqueries and row sections from two prefetches: a constant query count per page
        return queryset.annotate(
            plan_total=_count_by_user(PlanningPlan), empire_total=_count_by_user(PlanningEmpire)
        ).prefetch_related(
            Prefetch(
                'plans',
                queryset=PlanningPlan.objects.only(
                    'uuid', 'user_id', 'plan_name', 'planet_natural_id', 'modified_at'
                ).order_by('-modified_at'),
            ),
            Prefetch(
                'empires',
                queryset=PlanningEmpire.objects.only(
                    'uuid', 'user_id', 'empire_name', 'empire_faction', 'modified_at'
                ).order_by('-modified_at'),
            ),
        )

    # -- columns

    @display(description='User', header=True, ordering='username')
    def display_user(self, obj: User) -> list[str]:
        return [obj.username, obj.email or '—']

    @display(description='Flags', label=True)
    def display_badges(self, obj: User) -> list[str]:
        badges = []
        if obj.is_staff:
            badges.append('staff')
        if obj.is_email_verified:
            badges.append('verified')
        if obj._has_fio_credentials():
            badges.append('FIO')
        if not obj.is_active:
            badges.append('inactive')
        return badges

    @display(description='Plans', ordering='plan_total')
    def plan_count(self, obj: User) -> int:
        return getattr(obj, 'plan_total', 0)

    @display(description='Empires', ordering='empire_total')
    def empire_count(self, obj: User) -> int:
        return getattr(obj, 'empire_total', 0)

    @display(description='FIO API key')
    def fio_apikey_masked(self, obj: User) -> str:
        return mask_secret(obj.fio_apikey)

    # -- strip and header

    def get_summary(self, request: HttpRequest) -> Summary:
        stats = User.objects.aggregate(
            total=Count('pk'),
            active=Count('pk', filter=Q(last_login__gte=timezone.now() - timedelta(days=30))),
            fio=Count('pk', filter=FIO_LINKED_Q),
            verified=Count('pk', filter=Q(is_email_verified=True)),
        )
        labels, values = daily_counts(User.objects.all(), 'date_joined', SIGNUP_CHART_DAYS)
        return {
            'tiles': [
                {'label': 'Users', 'value': f'{stats["total"]:,}'},
                {
                    'label': 'Active in 30 d',
                    'value': f'{stats["active"]:,}',
                    'sub': percent(stats['active'], stats['total']),
                },
                {
                    'label': 'FIO linked',
                    'value': percent(stats['fio'], stats['total']),
                    'sub': f'{stats["fio"]:,} users',
                    'href': changelist_url(User, 'fio=yes'),
                },
                {
                    'label': 'Email verified',
                    'value': percent(stats['verified'], stats['total']),
                    'sub': f'{stats["verified"]:,} users',
                    'href': changelist_url(User, 'is_email_verified__exact=1'),
                },
            ],
            'charts': [
                {
                    'title': f'Signups per day, last {SIGNUP_CHART_DAYS} days',
                    'kind': 'bar',
                    'config': bar(labels, [('Signups', values)]),
                    'height': 180,
                }
            ],
        }

    def get_header(self, request: HttpRequest, obj: User) -> list[Fact]:
        user_filter = f'user__id__exact={obj.pk}'
        fio = (
            GameFIOPlayerData.objects.filter(user=obj)
            .only('uuid', 'automation_refresh_status', 'automation_error', 'automation_last_refreshed_at')
            .first()
        )
        keys = UserAPIKey.objects.filter(user=obj)
        last_edit = PlanningPlan.objects.filter(user=obj).aggregate(last=Max('modified_at'))['last']
        facts: list[Fact] = [
            {'label': 'Joined', 'value': f'{obj.date_joined:%Y-%m-%d}' if obj.date_joined else '—'},
            {'label': 'Last login', 'value': f'{obj.last_login:%Y-%m-%d %H:%M} UTC' if obj.last_login else '—'},
            {
                'label': 'Plans',
                'value': f'{PlanningPlan.objects.filter(user=obj).count():,}',
                'href': changelist_url(PlanningPlan, user_filter),
            },
            {
                'label': 'Empires',
                'value': f'{PlanningEmpire.objects.filter(user=obj).count():,}',
                'href': changelist_url(PlanningEmpire, user_filter),
            },
            {
                'label': 'CX preferences',
                'value': f'{PlanningCX.objects.filter(user=obj).count():,}',
                'href': changelist_url(PlanningCX, user_filter),
            },
            {
                'label': 'API keys',
                'value': f'{keys.filter(revoked=False).count()} active / {keys.count()}',
                'href': changelist_url(UserAPIKey, user_filter),
            },
            {'label': 'FIO API key', 'value': mask_secret(obj.fio_apikey)},
            {'label': 'Last plan edit', 'value': f'{last_edit:%Y-%m-%d %H:%M} UTC' if last_edit else '—'},
        ]
        status, _ = fio_connection(obj)
        facts.append(
            {
                'label': 'FIO status (user sees)',
                'value': status,
                'badge': {'ok': 'success', 'none': 'default', 'syncing': 'info', 'no_data': 'warning'}.get(
                    status, 'danger'
                ),
            }
        )
        if fio is None:
            facts.append({'label': 'FIO sync', 'value': 'no data', 'badge': 'default'})
        else:
            facts.append(
                {
                    'label': 'FIO sync',
                    'value': fio.automation_refresh_status,
                    'badge': {'ok': 'success', 'pending': 'info', 'retrying': 'warning', 'failed': 'danger'}.get(
                        fio.automation_refresh_status, 'info'
                    ),
                    'href': reverse('admin:gamedata_gamefioplayerdata_change', args=[fio.pk]),
                }
            )
            if fio.automation_error:
                facts.append({'label': 'Last FIO error', 'value': fio.automation_error, 'block': True})
        return facts

    # -- actions

    @admin.action(description='Refresh FIO')
    def action_refresh_fio(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        from gamedata.tasks import gamedata_refresh_user_fiodata

        user_ids = list(queryset.filter(FIO_LINKED_Q).values_list('pk', flat=True))
        try:
            for user_id in user_ids:
                gamedata_refresh_user_fiodata.delay(user_id)
        except Exception:
            logger.exception('admin_user_fio_refresh_queue_failed')
            messages.error(request, 'Queueing the FIO refreshes failed.')
            return
        log_admin_action(request, User, f'Queued FIO refresh for {len(user_ids)} users')
        messages.success(request, f'Queued {len(user_ids)} FIO refreshes (users without credentials skipped).')

    def _clear_fio(self, users: QuerySet[User]) -> int:
        cleared = 0
        for user in users.exclude(fio_apikey__isnull=True):
            # a save, not an update: the user signals drop the stored FIO data and the refresh lock
            user.fio_apikey = None
            user.save(update_fields=['fio_apikey'])
            cleared += 1
        return cleared

    @admin.action(description='Clear FIO credentials')
    def action_clear_fio(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        cleared = self._clear_fio(queryset)
        log_admin_action(request, User, f'Cleared FIO API key of {cleared} users')
        messages.success(request, f'Cleared the FIO API key of {cleared} users; their FIO data is removed.')

    @action(description='Clear FIO credentials', url_path='detail-clear-fio', icon='key_off')
    def action_detail_clear_fio(self, request: HttpRequest, object_id: str) -> HttpResponse:
        """GET shows the confirmation; the clear runs on POST."""
        user = User.objects.filter(pk=object_id).first()
        change_url = reverse('admin:user_user_change', args=[object_id])
        if user is None:
            messages.error(request, 'User not found.')
            return HttpResponseRedirect(changelist_url(User))
        if request.method != 'POST':
            return confirm_action(
                request,
                title='Clear FIO credentials',
                message=(
                    f'Removes the FIO API key of {user.username} and deletes their synced FIO data. '
                    'The in-game username stays. The user has to enter a new key to sync again.'
                ),
                submit_label='Clear FIO credentials',
                cancel_url=change_url,
            )
        self._clear_fio(User.objects.filter(pk=user.pk))
        log_admin_action(request, user, 'Cleared FIO API key')
        messages.success(request, f'Cleared the FIO API key of {user.username}.')
        return HttpResponseRedirect(change_url)

    @admin.action(description='Deactivate')
    def action_deactivate(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        # never lock yourself out
        count = queryset.exclude(pk=request.user.pk).update(is_active=False)
        log_admin_action(request, User, f'Deactivated {count} users')
        messages.success(request, f'Deactivated {count} users.')

    @admin.action(description='Reactivate')
    def action_reactivate(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        count = queryset.update(is_active=True)
        log_admin_action(request, User, f'Reactivated {count} users')
        messages.success(request, f'Reactivated {count} users.')

    @admin.action(description='Resend verification email')
    def action_resend_verification(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        users = list(queryset.filter(is_email_verified=False, email__isnull=False).exclude(email=''))
        try:
            for user in users:
                VerificationService.create_and_send_code(user, VerificationeCodeChoices.EMAIL_VERIFICATION)
        except Exception:
            logger.exception('admin_resend_verification_failed')
            messages.error(request, 'Queueing the verification emails failed.')
            return
        log_admin_action(request, User, f'Queued verification email for {len(users)} users')
        messages.success(
            request, f'Queued {len(users)} verification emails (verified users and users without email skipped).'
        )


# ---------------------------------------------------------------------------
# audit log: read-only, it is the audit trail


@admin.register(LogEntry)
class LogEntryAdmin(ModelAdmin):
    list_display = ['action_time', 'user', 'content_type', 'object_link', 'display_action', 'display_message']
    list_select_related = ['user', 'content_type']
    list_filter = ['action_flag', 'content_type']
    date_hierarchy = 'action_time'
    search_fields = ['object_repr', 'change_message']
    ordering = ['-action_time']

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: LogEntry | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: LogEntry | None = None) -> bool:
        return False

    @display(description='Object')
    def object_link(self, obj: LogEntry) -> str:
        url = obj.get_admin_url() if obj.object_id else ''
        return format_html('<a href="{}">{}</a>', url, obj.object_repr) if url else obj.object_repr

    @display(description='Action', label={'Addition': 'success', 'Change': 'info', 'Deletion': 'danger'})
    def display_action(self, obj: LogEntry) -> str:
        return {ADDITION: 'Addition', CHANGE: 'Change', DELETION: 'Deletion'}.get(obj.action_flag, str(obj.action_flag))

    @display(description='Message')
    def display_message(self, obj: LogEntry) -> str:
        return obj.get_change_message()


# ---------------------------------------------------------------------------
# API keys and verification codes

# remove the standard DRF API Key and group admin pages
admin.site.unregister(APIKey)
admin.site.unregister(Group)


@admin.register(UserAPIKey)
class UserAPIKeyAdmin(APIKeyAdmin):
    list_display = [*APIKeyAdmin.list_display, 'user', 'last_used']
    list_select_related = ['user']
    search_fields = [*APIKeyAdmin.search_fields, 'user__username', 'user__email']
    autocomplete_fields = ['user']


@admin.register(VerificationCode)
class VerificationCodeAdmin(ReadOnlyAdminMixin, ModelAdmin):
    """Codes are account-takeover material: never shown, not even to staff."""

    list_display = ['created_at', 'user', 'purpose', 'is_used']
    list_select_related = ['user']
    list_filter = ['purpose', 'is_used']
    search_fields = ['user__username', 'user__email']
    exclude = ['code']
    ordering = ['-created_at']
