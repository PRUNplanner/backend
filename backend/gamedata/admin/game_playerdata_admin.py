import structlog
from core.admin import (
    AutomationAdminMixin,
    ChangeHeaderMixin,
    Fact,
    PermanentlyFailedFilter,
    ReadOnlyAdminMixin,
    StuckPendingFilter,
    SummaryStripMixin,
    log_admin_action,
)
from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.urls import reverse
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import RangeDateTimeFilter

from gamedata.gamedata_cache_manager import GamedataCacheManager
from gamedata.models import GameFIOPlayerData
from gamedata.tasks import gamedata_refresh_user_fiodata

logger = structlog.get_logger(__name__)

JSON_FIELDS = ('storage_data', 'site_data', 'warehouse_data', 'ship_data')


@admin.register(GameFIOPlayerData)
class GameFIOPlayerDataAdmin(
    ReadOnlyAdminMixin,
    AutomationAdminMixin[GameFIOPlayerData],
    SummaryStripMixin,
    ChangeHeaderMixin[GameFIOPlayerData],
    ModelAdmin,
):
    list_display = [
        'uuid',
        'user',
        'fio_status_code',
        *AutomationAdminMixin.automation_columns,
        'automation_last_refreshed_at',
    ]
    list_select_related = ['user']
    search_fields = ['user__username', 'user__prun_username']
    list_filter = [
        'automation_refresh_status',
        'fio_status_code',
        StuckPendingFilter,
        PermanentlyFailedFilter,
        ('automation_last_refreshed_at', RangeDateTimeFilter),
    ]
    list_filter_submit = True
    ordering = ['-automation_last_refreshed_at']

    actions = ['action_user_refresh_fio', 'action_reset_and_retry']
    actions_detail = ['action_detail_reset_and_retry']

    def get_queryset(self, request: HttpRequest) -> QuerySet[GameFIOPlayerData]:
        # the FIO payloads are large; a change page loads them on access
        return super().get_queryset(request).defer(*JSON_FIELDS)

    def enqueue_refresh(self, obj: GameFIOPlayerData) -> None:
        # a reset asks for a refresh now, so the cooldown lock of the last attempt must not swallow it
        GamedataCacheManager.delete_fio_refresh_lock(obj.user_id)  # ty: ignore[unresolved-attribute]
        gamedata_refresh_user_fiodata.delay(obj.user_id)  # ty: ignore[unresolved-attribute]

    def get_header(self, request: HttpRequest, obj: GameFIOPlayerData) -> list[Fact]:
        return [
            {
                'label': 'User',
                'value': str(obj.user),
                'href': reverse('admin:user_user_change', args=[obj.user_id]),  # ty: ignore[unresolved-attribute]
            },
            # what the user's profile shows comes from this, not from the refresh status alone
            {'label': 'FIO answer', 'value': obj.get_fio_status_code_display() or 'none yet'},  # ty: ignore[unresolved-attribute]
            *self.automation_facts(obj),
        ]

    @admin.action(description='Refresh FIO')
    def action_user_refresh_fio(self, request: HttpRequest, queryset: QuerySet[GameFIOPlayerData]) -> None:
        queued = 0
        try:
            for data in queryset.select_related('user').only('uuid', 'user__prun_username', 'user__fio_apikey'):
                if data.user._has_fio_credentials():
                    gamedata_refresh_user_fiodata.delay(data.user_id)  # ty: ignore[unresolved-attribute]
                    queued += 1
        except Exception:
            logger.exception('admin_fio_refresh_queue_failed')
            messages.error(request, f'Queued {queued} refreshes, then queueing failed.')
            return
        log_admin_action(request, GameFIOPlayerData, f'Queued FIO refresh for {queued} users')
        messages.success(request, f'Queued {queued} FIO refreshes (users without credentials skipped).')
