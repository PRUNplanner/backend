from django.contrib import admin
from django.db.models import JSONField
from django_json_widget.widgets import JSONEditorWidget

from planning.models import PlanningCX, PlanningEmpire, PlanningEmpirePlan, PlanningPlan, PlanningShared


class PlanningEmpirePlanInline(admin.TabularInline):
    model = PlanningEmpirePlan
    extra = 0

    autocomplete_fields = ['plan', 'user']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('plan', 'user')


class PlanningEmpireInline(admin.TabularInline):
    model = PlanningEmpire
    extra = 0

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('cx')


@admin.register(PlanningPlan)
class PlanningPlanAdmin(admin.ModelAdmin):
    list_display = ['uuid', 'user', 'planet_natural_id', 'plan_name', 'created_at', 'modified_at']
    search_fields = ['uuid', 'planet_natural_id', 'plan_name', 'user__username']
    ordering = ['-modified_at']

    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget},
    }


@admin.register(PlanningEmpire)
class PlanningEmpireAdmin(admin.ModelAdmin):
    list_display = ['uuid', 'user', 'empire_name']
    search_fields = ['uuid', 'empire_name', 'user__username']
    ordering = ['-modified_at']

    inlines = [PlanningEmpirePlanInline]


@admin.register(PlanningCX)
class PlanningCXAdmin(admin.ModelAdmin):
    list_display = ['uuid', 'user', 'cx_name']
    search_fields = ['uuid', 'cx_name', 'user__username']
    ordering = ['-modified_at']

    inlines = [PlanningEmpireInline]

    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget},
    }


@admin.register(PlanningEmpirePlan)
class PlanningEmpirePlanAdmin(admin.ModelAdmin):
    list_display = ['uuid', 'user', 'empire', 'plan']
    search_fields = ['uuid', 'user__username', 'empire__empire_name', 'plan__plan_name']


@admin.register(PlanningShared)
class PlanningSharedAdmin(admin.ModelAdmin):
    list_display = ['uuid', 'user', 'plan', 'view_count']
    search_fields = ['uuid', 'user__username', 'plan__plan_name']
    ordering = ['-view_count']
