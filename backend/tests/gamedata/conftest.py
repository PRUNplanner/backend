import pytest
from model_bakery import baker


@pytest.fixture(scope='session', autouse=True)
def create_unmanaged_tables(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        from django.db import connection

        with connection.cursor() as cursor:
            # Manually create the table schema here
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prunplanner_game_exchanges_analytics (
                    id integer PRIMARY KEY AUTOINCREMENT,
                    ticker varchar(20),
                    exchange_code varchar(20),
                    date_epoch bigint,
                    calendar_date date,
                    traded_daily integer,
                    vwap_daily decimal,
                    sum_traded_7d integer,
                    avg_traded_7d decimal,
                    vwap_7d decimal,
                    sum_traded_30d integer,
                    avg_traded_30d decimal,
                    vwap_30d decimal
                )
            """)


@pytest.fixture()
def recipe_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GameRecipe', make_m2m=True, **kwargs)


@pytest.fixture()
def material_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GameMaterial', **kwargs)


@pytest.fixture()
def building_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GameBuilding', **kwargs)


@pytest.fixture()
def planet_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GamePlanet', make_m2m=True, **kwargs)


@pytest.fixture()
def building_cost_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GameBuildingCost', **kwargs)


@pytest.fixture()
def popr_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GamePlanetInfrastructureReport', **kwargs)


@pytest.fixture()
def exchange_analytics_factory():

    return lambda **kwargs: baker.make('gamedata.GameExchangeAnalytics', **kwargs)


@pytest.fixture()
def fio_playerdata_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GameFIOPlayerData', schema_version=1, **kwargs)


@pytest.fixture()
def exchange_cxpc_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GameExchangeCXPC', **kwargs)
