from django.contrib import admin, messages
from django.http import HttpRequest
from django.shortcuts import redirect
from unfold.admin import ModelAdmin
from unfold.decorators import action

from gamedata.fio.importers import import_all_exchanges
from gamedata.models import GameExchange, GameExchangeAnalytics, GameExchangeCXPC


@admin.register(GameExchange)
class GameExchangeAdmin(ModelAdmin):
    list_display = ['ticker_id', 'ticker', 'exchange_code']
    search_fields = ['ticker_id', 'ticker', 'exchange_code']
    list_filter = ['exchange_code']

    actions_list = ['action_fio_import_exchange']

    @action(description='Import from FIO', url_path='changelist-fio-import-exchange')
    def action_fio_import_exchange(self, request: HttpRequest):
        status = import_all_exchanges()

        if status:
            self.message_user(request, 'Exchanges imported from FIO.', messages.SUCCESS)
        else:
            self.message_user(request, 'Exchanges import from FIO failed.', messages.ERROR)

        return redirect('../')


@admin.register(GameExchangeAnalytics)
class GameExchangeAnalyticsAdmin(ModelAdmin):
    list_display = ['ticker', 'exchange_code', 'calendar_date', 'vwap_daily', 'traded_daily']
    search_fields = ['ticker', 'exchange_code', 'calendar_date']
    list_filter = ['exchange_code']


@admin.register(GameExchangeCXPC)
class GameExchangeCXPCAdmin(ModelAdmin):
    list_display = ['ticker', 'exchange_code', 'date_epoch', 'volume', 'traded']
    search_fields = ['ticker', 'exchange_code']
    list_filter = ['exchange_code']
