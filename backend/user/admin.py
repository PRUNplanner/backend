from django.contrib import admin, messages
from django.contrib.admin.models import LogEntry
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import F, JSONField, QuerySet
from django.http import HttpRequest
from django_json_widget.widgets import JSONEditorWidget
from rest_framework_api_key.admin import APIKeyAdmin
from rest_framework_api_key.models import APIKey
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from user.models import GlobalConfigWebhook, User, UserAPIKey, UserPreference, VerificationCode


@admin.register(GlobalConfigWebhook)
class GlobalConfigWebhookAdmin(ModelAdmin):
    list_display = ['path', 'sender', 'is_active', 'total_calls', 'last_received_at']


@admin.register(UserPreference)
class UserPreferenceAdmin(ModelAdmin):
    list_display = ['user', 'updated_at']
    ordering = ['-updated_at']

    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget},
    }


@admin.register(User)
class UserAdmin(
    BaseUserAdmin,
    ModelAdmin,
):
    list_display = ['username', 'id', 'email', 'last_login', 'prun_username']
    search_fields = ['username', 'email', 'id', 'prun_username']
    ordering = [F('last_login').desc(nulls_last=True)]

    fieldsets = (
        (None, {'fields': ('username', 'password', 'email', 'prun_username', 'fio_apikey', 'is_email_verified')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
    )

    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(LogEntry)
class LogEntryAdmin(ModelAdmin):
    list_display = [
        'action_time',
        'user',
        'content_type',
        'object_repr',
        'action_flag',
        'change_message',
    ]
    list_filter = ['user', 'content_type', 'action_flag']
    date_hierarchy = 'action_time'
    search_fields = ['object_repr', 'change_message']
    ordering = ['-action_time']

    actions = ['delete_all_logs']

    @admin.action(description='Delete all log entries')
    def delete_all_logs(self, request: HttpRequest, queryset: QuerySet | None = None) -> None:
        count = LogEntry.objects.count()
        LogEntry.objects.all().delete()
        self.message_user(request, f'Deleted {count} log entries.', level=messages.SUCCESS)


# remove standard DRF API Key admin page
try:
    admin.site.unregister(APIKey)
except Exception:
    pass


@admin.register(UserAPIKey)
class UserAPIKeyAdmin(APIKeyAdmin):
    list_display = [*APIKeyAdmin.list_display, 'user', 'last_used']
    search_fields = [*APIKeyAdmin.search_fields, 'user__username', 'user__email']


@admin.register(VerificationCode)
class VerificationCodeAdmin(ModelAdmin):
    list_display = ['created_at', 'user', 'purpose', 'is_used']
    search_fields = ['user']
