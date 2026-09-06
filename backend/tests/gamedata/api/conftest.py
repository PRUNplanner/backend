from collections.abc import Callable

import pytest
from gamedata.models.game_exchange import GameExchange
from model_bakery import baker
from user.models.configs import GlobalConfigWebhook


@pytest.fixture()
def exchange_factory() -> Callable[..., GameExchange]:
    return lambda **kwargs: baker.make('gamedata.GameExchange', **kwargs)


@pytest.fixture()
def webhook_config_factory() -> Callable[..., GlobalConfigWebhook]:
    return lambda **kwargs: baker.make('user.GlobalConfigWebhook', **kwargs)
