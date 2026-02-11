import json
from datetime import timedelta

from django.contrib import admin
from django.db.models import Count, F
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import path
from django.utils import timezone

from gamedata.fio.importers import import_all_exchanges
from gamedata.models import GameExchange, GameExchangeAnalytics, GameExchangeCXPC


@admin.register(GameExchange)
class GameExchangeAdmin(admin.ModelAdmin):
    change_list_template = 'admin/gamedata/exchange_change_list.html'

    list_display = ['ticker_id', 'ticker', 'exchange_code']
    search_fields = ['ticker_id', 'ticker', 'exchange_code']
    list_filter = ['exchange_code']

    def get_urls(self) -> list:
        urls = super().get_urls()
        custom_urls = [
            path(
                'fio_exchange_import/',
                self.admin_site.admin_view(self.fio_exchange_import),
                name='fio_exchange_import',
            )
        ]
        return custom_urls + urls

    def fio_exchange_import(self, request: HttpRequest) -> HttpResponseRedirect:
        status = import_all_exchanges()

        self.message_user(
            request,
            f'Exchange sync status: {status}.',
        )

        return redirect('../')


@admin.register(GameExchangeAnalytics)
class GameExchangeAnalyticsAdmin(admin.ModelAdmin):
    list_display = ['ticker', 'exchange_code', 'calendar_date', 'vwap_daily', 'traded_daily']
    search_fields = ['ticker', 'exchange_code', 'calendar_date']
    list_filter = ['exchange_code']

    def changelist_view(self, request, extra_context=None):
        last_30_days = timezone.now().date() - timedelta(days=30)

        chart_data = (
            GameExchangeAnalytics.objects.annotate(date=F('calendar_date'))
            .filter(calendar_date__gte=last_30_days)
            .values('calendar_date')
            .annotate(y=Count('id'))
            .order_by('calendar_date')
        )

        as_json = json.dumps(list(chart_data), default=str)

        extra_context = extra_context or {'chart_data': as_json}
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(GameExchangeCXPC)
class GameExchangeCXPCAdmin(admin.ModelAdmin):
    list_display = ['ticker', 'exchange_code', 'date_epoch', 'volume', 'traded']
    search_fields = ['ticker', 'exchange_code']
    list_filter = ['exchange_code']
