from django.core.validators import MinLengthValidator, MinValueValidator
from django.db import models


class GameExchangeCodeChoices(models.TextChoices):
    AI1 = 'AI1'
    CI1 = 'CI1'
    CI2 = 'CI2'
    IC1 = 'IC1'
    NC1 = 'NC1'
    NC2 = 'NC2'
    UNIVERSE = 'UNIVERSE'


class GameExchange(models.Model):
    ticker_id = models.CharField(max_length=8, validators=[MinLengthValidator(5)], primary_key=True)

    ticker = models.CharField(max_length=3, db_index=True, validators=[MinLengthValidator(1)])
    exchange_code = models.CharField(max_length=8, db_index=True, choices=GameExchangeCodeChoices.choices)

    mm_buy = models.FloatField(blank=True, null=True, validators=[MinValueValidator(0.0)])
    mm_sell = models.FloatField(blank=True, null=True, validators=[MinValueValidator(0.0)])

    price_average = models.FloatField(validators=[MinValueValidator(0.0)])
    ask = models.FloatField(blank=True, null=True, validators=[MinValueValidator(0.0)])
    bid = models.FloatField(blank=True, null=True, validators=[MinValueValidator(0.0)])

    ask_count = models.PositiveIntegerField(blank=True, null=True)
    bid_count = models.PositiveIntegerField(blank=True, null=True)
    supply = models.PositiveIntegerField(blank=True, null=True)
    demand = models.PositiveIntegerField(blank=True, null=True)

    objects: models.Manager['GameExchange'] = models.Manager()

    class Meta:
        db_table = 'prunplanner_game_exchanges'
        verbose_name = 'Exchange'
        verbose_name_plural = 'Exchanges'

    def __str__(self) -> str:
        return self.ticker_id


class GameExchangeCXPC(models.Model):
    # Bronze Layer: Raw daily CXPC data

    ticker = models.CharField(max_length=3, db_index=True, validators=[MinLengthValidator(1)])
    exchange_code = models.CharField(max_length=8, choices=GameExchangeCodeChoices.choices)
    date_epoch = models.BigIntegerField()

    open_p = models.DecimalField('Open Price', max_digits=20, decimal_places=4)
    close_p = models.DecimalField('Close Price', max_digits=20, decimal_places=4)
    high_p = models.DecimalField('High Price', max_digits=20, decimal_places=4)
    low_p = models.DecimalField('Low Price', max_digits=20, decimal_places=4)
    volume = models.DecimalField('Price Volume', max_digits=20, decimal_places=4)
    traded = models.DecimalField('Traded Amount', max_digits=20, decimal_places=4)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['ticker', 'exchange_code', 'date_epoch'], name='unique_ticker_exchange_date'
            )
        ]

        indexes = [
            # query by ticker, or (ticker, exchange_code) the same
            models.Index(fields=['ticker', 'exchange_code'], name='idx_ticker_exchange')
        ]

        db_table = 'prunplanner_game_exchanges_cxpc'
        verbose_name = 'Exchange CXPC'
        verbose_name_plural = 'Exchange CXPCs'

    def __str__(self) -> str:
        return f'{self.ticker}.{self.exchange_code} ({self.date_epoch})'


class GameExchangeAnalytics(models.Model):
    # Gold Layer: Read-only view of calculated stats.

    ticker = models.CharField(max_length=20)
    exchange_code = models.CharField(max_length=20)
    date_epoch = models.BigIntegerField()
    calendar_date = models.DateField()

    # Daily Metrics
    traded_daily = models.IntegerField('Daily Traded Amount')
    vwap_daily = models.DecimalField('Daily VWAP', max_digits=20, decimal_places=4)

    # 7-Day Metrics
    sum_traded_7d = models.IntegerField('7d Traded Amount')
    avg_traded_7d = models.DecimalField('7d Traded Average', max_digits=20, decimal_places=4)
    vwap_7d = models.DecimalField('7d VWAP', max_digits=20, decimal_places=4)

    # 30-Day Metrics
    sum_traded_30d = models.IntegerField('30d Traded Amount')
    avg_traded_30d = models.DecimalField('30d Traded Average', max_digits=20, decimal_places=4)
    vwap_30d = models.DecimalField('30d VWAP', max_digits=20, decimal_places=4)

    class Meta:
        managed = False  # Critical: Django will not create a table
        db_table = 'prunplanner_game_exchanges_analytics'
        verbose_name = 'Exchange Analytics'
        verbose_name_plural = 'Exchange Analytics'

    def __str__(self) -> str:
        return f'{self.ticker}.{self.exchange_code} ({self.calendar_date})'
