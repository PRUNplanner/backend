from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import path

from gamedata.fio.importers import import_all_planets, import_planet
from gamedata.models import GamePlanet, GamePlanetCOGCProgram, GamePlanetProductionFee, GamePlanetResource


@admin.action(description='Refresh Planet from FIO')
def action_refresh_planet(modeladmin: admin.ModelAdmin, request: HttpRequest, queryset: QuerySet) -> None:
    data = queryset.values_list('planet_natural_id', flat=True).all()

    for planet_natural_id in data:
        import_planet(planet_natural_id)


class PlanetCOGCProgramInline(admin.TabularInline):
    model = GamePlanetCOGCProgram
    can_delete = True
    extra = 0
    fk_name = 'planet'


class PlanetResourceInline(admin.TabularInline):
    model = GamePlanetResource
    can_delete = True
    extra = 0
    fk_name = 'planet'


class PlanetProductionFeeInline(admin.TabularInline):
    model = GamePlanetProductionFee
    can_delete = True
    extra = 0
    fk_name = 'planet'


@admin.register(GamePlanet)
class GamePlanetAdmin(admin.ModelAdmin):
    change_list_template = 'admin/gamedata/planet_change_list.html'
    list_display = ['planet_natural_id', 'planet_name', 'automation_refresh_status', 'automation_last_refreshed_at']
    search_fields = ['planet_natural_id', 'planet_name']
    list_filter = ['automation_refresh_status']
    ordering = ['-automation_last_refreshed_at']

    inlines = [
        PlanetResourceInline,
        PlanetCOGCProgramInline,
        PlanetProductionFeeInline,
    ]

    @admin.action(description='Delete all planets')
    def delete_all_planets(self, request: HttpRequest, queryset: QuerySet | None = None) -> None:
        count = GamePlanet.objects.count()
        GamePlanet.objects.all().delete()
        self.message_user(request, f'Deleted {count} log entries.', level=messages.SUCCESS)

    actions = [action_refresh_planet, delete_all_planets]

    def get_urls(self) -> list:
        urls = super().get_urls()
        custom_urls = [
            path(
                'fio_import_planet/',
                self.admin_site.admin_view(self.fio_import_planet),
                name='fio_import_planet',
            ),
            path(
                'fio_import_all_planets/',
                self.admin_site.admin_view(self.fio_import_all_planets),
                name='fio_import_all_planets',
            ),
        ]
        return custom_urls + urls

    def fio_import_planet(self, request: HttpRequest) -> HttpResponseRedirect:
        value = request.GET.get('value')

        if value and isinstance(value, str):
            status = import_planet(value)

            if status:
                self.message_user(request, f'Imported: {value}')
            else:
                self.message_user(request, f'Failed: {value}')

        return redirect('../')

    def fio_import_all_planets(self, request: HttpRequest) -> HttpResponseRedirect:
        print('fetch all')

        import_all_planets()

        return redirect('../')
