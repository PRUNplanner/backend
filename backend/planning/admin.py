from django.contrib import admin
from django.db.models import JSONField
from django_json_widget.widgets import JSONEditorWidget
from unfold.admin import ModelAdmin, TabularInline

from planning.models import PlanningCX, PlanningEmpire, PlanningEmpirePlan, PlanningPlan, PlanningShared


class PlanningEmpirePlanInline(TabularInline):
    model = PlanningEmpirePlan
    extra = 0
    tab = True

    autocomplete_fields = ['plan', 'user']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('plan', 'user')


class PlanningEmpireInline(TabularInline):
    model = PlanningEmpire
    extra = 0
    tab = True

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('cx')


@admin.register(PlanningPlan)
class PlanningPlanAdmin(ModelAdmin):
    list_display = ['uuid', 'user', 'planet_natural_id', 'plan_name', 'created_at', 'modified_at']
    search_fields = ['uuid', 'planet_natural_id', 'plan_name', 'user__username']
    ordering = ['-modified_at']

    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget},
    }


@admin.register(PlanningEmpire)
class PlanningEmpireAdmin(ModelAdmin):
    list_display = ['uuid', 'user', 'empire_name', 'created_at', 'modified_at']
    search_fields = ['uuid', 'empire_name', 'user__username']
    ordering = ['-modified_at']

    inlines = [PlanningEmpirePlanInline]

    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget},
    }


@admin.register(PlanningCX)
class PlanningCXAdmin(ModelAdmin):
    list_display = ['uuid', 'user', 'cx_name', 'created_at', 'modified_at']
    search_fields = ['uuid', 'cx_name', 'user__username']
    ordering = ['-modified_at']

    inlines = [PlanningEmpireInline]

    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget},
    }


@admin.register(PlanningEmpirePlan)
class PlanningEmpirePlanAdmin(ModelAdmin):
    list_display = ['uuid', 'user', 'empire', 'plan']
    search_fields = ['uuid', 'user__username', 'empire__empire_name', 'plan__plan_name']


@admin.register(PlanningShared)
class PlanningSharedAdmin(ModelAdmin):
    list_display = ['uuid', 'user', 'plan', 'view_count', 'created_at', 'modified_at']
    search_fields = ['uuid', 'user__username', 'plan__plan_name']
    ordering = ['-view_count']
