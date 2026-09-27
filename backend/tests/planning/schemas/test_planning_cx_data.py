import pytest
from planning.schemas.planning_cx_data import CXExchangeTickerPreferences_V1
from pydantic import ValidationError


def test_rejects_empty_ticker() -> None:
    with pytest.raises(ValidationError):
        CXExchangeTickerPreferences_V1.model_validate({'ticker_empire': [{'ticker': '', 'type': 'BUY', 'value': 1}]})


def test_accepts_ticker() -> None:
    data = CXExchangeTickerPreferences_V1.model_validate(
        {'ticker_empire': [{'ticker': 'DW', 'type': 'BUY', 'value': 1}]}
    )

    assert data.ticker_empire[0].ticker == 'DW'
