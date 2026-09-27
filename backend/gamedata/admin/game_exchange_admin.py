from datetime import UTC, date, datetime, timedelta

from analytics.models import AppStatistic
from core.admin import ReadOnlyAdminMixin, Summary, SummaryStripMixin, changelist_url
from core.admin_charts import Chart, Tile, line
from django.contrib import admin
from django.db.models import Max, Model, QuerySet
from django.http import HttpRequest, HttpResponseRedirect
from django.utils import timezone
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import ChoicesDropdownFilter, DropdownFilter
from unfold.decorators import action
from unfold.paginator import InfinitePaginator

from gamedata.admin.fio_import import queue_fio_import
from gamedata.models import GameExchange, GameExchangeAnalytics, GameExchangeCXPC

ROWS_CHART_DAYS = 90


class TickerDropdownFilter(DropdownFilter):
    title = 'ticker'
    parameter_name = 'ticker'

    def lookups(self, request: HttpRequest, model_admin: admin.ModelAdmin) -> list[tuple[str, str]]:
        # the material list is small and indexed; the market tables are not scanned for it
        tickers = GameExchange.objects.order_by('ticker').values_list('ticker', flat=True).distinct()
        return [(ticker, ticker) for ticker in tickers]

    def queryset(self, request: HttpRequest, queryset: QuerySet) -> QuerySet:
        return queryset.filter(ticker=self.value()) if self.value() else queryset


def _age_text(latest: date) -> str:
    days = (timezone.now().date() - latest).days
    return 'today' if days <= 0 else f'{days} d old'


def _freshness_tiles(model: type[Model], latest_by_exchange: dict[str, date]) -> list[Tile]:
    return [
        {
            'label': code,
            'value': f'{latest:%Y-%m-%d}',
            'sub': _age_text(latest),
            'href': changelist_url(model, f'exchange_code={code}'),
        }
        for code, latest in sorted(latest_by_exchange.items())
    ]


def _rows_chart(field: str, title: str) -> Chart:
    start = timezone.now().date() - timedelta(days=ROWS_CHART_DAYS)
    stats = list(AppStatistic.objects.filter(date__gte=start).order_by('date').values_list('date', field))
    return {
        'title': title,
        'kind': 'line',
        'config': line([f'{d:%m-%d}' for d, _ in stats], [('Rows', [v for _, v in stats])]),
        'height': 180,
    }


@admin.register(GameExchange)
class GameExchangeAdmin(ReadOnlyAdminMixin, ModelAdmin):
    list_display = ['ticker_id', 'ticker', 'exchange_code']
    search_fields = ['ticker_id', 'ticker', 'exchange_code']
    list_filter = ['exchange_code']

    actions_list = ['action_fio_import_exchange']

    @action(description='Import from FIO', url_path='changelist-fio-import-exchange', icon='download')
    def action_fio_import_exchange(self, request: HttpRequest) -> HttpResponseRedirect:
        return queue_fio_import(request, GameExchange, 'exchanges')


@admin.register(GameExchangeAnalytics)
class GameExchangeAnalyticsAdmin(ReadOnlyAdminMixin, SummaryStripMixin, ModelAdmin):
    list_display = ['ticker', 'exchange_code', 'calendar_date', 'vwap_daily', 'traded_daily']
    search_fields = ['ticker']
    list_filter = [('exchange_code', ChoicesDropdownFilter), TickerDropdownFilter]
    ordering = ['-calendar_date']
    date_hierarchy = 'calendar_date'
    show_full_result_count = False
    paginator = InfinitePaginator

    def has_delete_permission(self, request: HttpRequest, obj: GameExchangeAnalytics | None = None) -> bool:
        # a materialized view: rows can't be deleted, the next refresh rebuilds them
        return False

    def get_summary(self, request: HttpRequest) -> Summary:
        # ponytail: one grouped scan of the materialized view, cached 5 min; add an index if it gets slow
        latest = dict(
            GameExchangeAnalytics.objects.order_by()
            .values('exchange_code')
            .annotate(latest=Max('calendar_date'))
            .values_list('exchange_code', 'latest')
        )
        return {
            'tiles': _freshness_tiles(GameExchangeAnalytics, latest),
            'charts': [
                _rows_chart('exchange_analytics_count', f'Exchange analytics rows, last {ROWS_CHART_DAYS} days')
            ],
        }


@admin.register(GameExchangeCXPC)
class GameExchangeCXPCAdmin(ReadOnlyAdminMixin, SummaryStripMixin, ModelAdmin):
    list_display = ['ticker', 'exchange_code', 'date_epoch', 'volume', 'traded']
    search_fields = ['ticker']
    list_filter = [('exchange_code', ChoicesDropdownFilter), TickerDropdownFilter]
    # pk order: the only index a newest-first sort can use on this table
    ordering = ['-pk']
    show_full_result_count = False
    paginator = InfinitePaginator

    def has_delete_permission(self, request: HttpRequest, obj: GameExchangeCXPC | None = None) -> bool:
        # the next sync upserts the rows again, and the infinite pager offers "select all" over the whole table
        return False

    def get_summary(self, request: HttpRequest) -> Summary:
        # ponytail: one grouped scan of CXPC, cached 5 min; add an (exchange_code, date_epoch) index if it gets slow
        latest_epoch = (
            GameExchangeCXPC.objects.order_by()
            .values('exchange_code')
            .annotate(latest=Max('date_epoch'))
            .values_list('exchange_code', 'latest')
        )
        latest = {code: datetime.fromtimestamp(epoch / 1000, tz=UTC).date() for code, epoch in latest_epoch if epoch}
        return {
            'tiles': _freshness_tiles(GameExchangeCXPC, latest),
            'charts': [_rows_chart('exchange_cxpc_count', f'CXPC rows, last {ROWS_CHART_DAYS} days')],
        }
