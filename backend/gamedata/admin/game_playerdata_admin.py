from django.contrib import admin
from django.db.models import JSONField, QuerySet
from django.http import HttpRequest
from django_json_widget.widgets import JSONEditorWidget
from user.models import User

from gamedata.models import GameFIOPlayerData
from gamedata.tasks import refresh_user_fio_data


@admin.action(description='Refresh User FIO')
def action_user_refresh_fio(modeladmin: admin.ModelAdmin, request: HttpRequest, queryset: QuerySet) -> None:
    data = queryset.prefetch_related('user').all()

    for fio_storage in data:
        user: User = fio_storage.user

        if user._has_fio_credentials():
            refresh_user_fio_data(user.id, user.prun_username, user.fio_apikey)


@admin.register(GameFIOPlayerData)
class GameFIOPlayerDataAdmin(admin.ModelAdmin):
    # defer json data in list view
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.defer('storage_data', 'site_data', 'warehouse_data', 'ship_data')

    list_select_related = ['user']
    list_display = ['uuid', 'user', 'automation_refresh_status', 'automation_last_refreshed_at']
    search_fields = ['user__username', 'user__prun_username']
    list_filter = ['automation_refresh_status']
    ordering = ['-automation_last_refreshed_at']

    actions = [action_user_refresh_fio]

    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget},
    }
