from datetime import UTC, datetime

import pytest
from og import texts
from planning.schemas.planning_plan_summary import PlanSummary_V1
from tests.fixtures.planning.fxt_plan_summary import plan_summary
from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis

AT = datetime(2026, 11, 8, 12, tzinfo=UTC)


def test_description_with_summary() -> None:
    text = texts.plan_description(
        username='scorpio',
        summary=PlanSummary_V1.model_validate(plan_summary),
        summary_at=AT,
        cogc='METALLURGY',
        planet='Montem (OT-580b)',
        building_count=0,
        outputs=[],
    )

    assert text == (
        'Shared by scorpio · 27,740 ȼ/day · pays back in 45 days · Metallurgy COGC · makes FE, LST, FLX · '
        'priced with CI1 30-day average, as of 8 Nov 2026. ' + texts.CALL_TO_ACTION
    )


def test_description_without_profit_or_basis() -> None:
    summary = PlanSummary_V1.model_validate({**plan_summary, 'profit': -5, 'payback_days': None, 'price_basis': None})
    text = texts.plan_description(
        username='a', summary=summary, summary_at=AT, cogc='---', planet='X', building_count=0, outputs=[]
    )

    assert text.startswith('Shared by a · -5 ȼ/day · makes FE, LST, FLX · as of 8 Nov 2026.')


def test_description_without_summary() -> None:
    text = texts.plan_description(
        username='a',
        summary=None,
        summary_at=None,
        cogc='PIONEERS',
        planet='Vallis (UV-351a)',
        building_count=1,
        outputs=['A', 'B', 'C', 'D', 'E', 'F', 'G'],
    )

    assert text.startswith('Shared by a · Vallis (UV-351a) · Pioneers COGC · 1 building · makes A, B, C, D, E, F… Open')
    assert 'ȼ' not in text


def test_plan_data_facts() -> None:
    count, outputs = texts.plan_data_facts(plan_data_vallis)

    assert count > 0
    assert outputs
    assert all(1 <= len(ticker) <= 3 for ticker in outputs)


def test_plan_data_facts_parses_recipe_ids() -> None:
    plan_data = {
        'experts': [],
        'workforce': [],
        'infrastructure': [],
        'buildings': [
            {
                'name': 'FP',
                'amount': 2,
                'active_recipes': [
                    {'recipeid': 'FP#1xCAF 3xDW=>3xCOF', 'amount': 1},
                    {'recipeid': 'FP#10xH2O=>7xDW', 'amount': 1},
                ],
            },
            {'name': 'COL', 'amount': 1, 'active_recipes': [{'recipeid': 'COL#O', 'amount': 1}]},
            {'name': 'EXT', 'amount': 0, 'active_recipes': [{'recipeid': 'EXT#FEO', 'amount': 1}]},
        ],
    }

    assert texts.plan_data_facts(plan_data) == (3, ['COF', 'DW', 'O'])
    assert texts.plan_data_facts({'nonsense': True}) == (0, [])


@pytest.mark.parametrize(
    ('code', 'label'),
    [
        ('CI1_30D', 'CI1 30-day average'),
        ('UNIVERSE_7D', 'Universe 7-day average'),
        ('AI1_ASK', 'AI1 ask'),
        ('CUSTOM', 'own prices'),
        (None, None),
        ('XX_7D', None),
    ],
)
def test_price_basis_label(code: str | None, label: str | None) -> None:
    assert texts.price_basis_label(code) == label


@pytest.mark.parametrize(('value', 'label'), [(0.2, '1 day'), (1.4, '1 day'), (45.2, '45 days'), (1234, '1,234 days')])
def test_days(value: float, label: str) -> None:
    assert texts.days(value) == label


def test_planet_label() -> None:
    assert texts.planet_label('Montem', 'OT-580b') == 'Montem (OT-580b)'
    assert texts.planet_label('', 'OT-580b') == 'OT-580b'
    assert texts.planet_label('OT-580b', 'OT-580b') == 'OT-580b'
