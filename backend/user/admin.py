from django.contrib import admin, messages
from django.contrib.admin.models import LogEntry
from django.db.models import F, JSONField, QuerySet
from django.http import HttpRequest
from django_json_widget.widgets import JSONEditorWidget
from rest_framework_api_key.admin import APIKeyAdmin
from rest_framework_api_key.models import APIKey

from user.models import User, UserAPIKey, UserPreference, VerificationCode


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ['user', 'updated_at']
    ordering = ['-updated_at']

    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget},
    }


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['username', 'id', 'email', 'last_login', 'prun_username']
    search_fields = ['username', 'email', 'id', 'prun_username']
    ordering = [F('last_login').desc(nulls_last=True)]


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
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
class VerificationCodeAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'user', 'purpose', 'is_used']
    search_fields = ['user']
