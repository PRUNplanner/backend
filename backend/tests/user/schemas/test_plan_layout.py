import copy
from collections.abc import Callable

import pytest
from pydantic import ValidationError
from tests.fixtures.fxt_plan_layout import (
    CUSTOM,
    DEFAULT,
    STANDARD_PHONE,
    STANDARD_WIDE,
    at_limits_chains,
    at_limits_items,
    at_limits_pairs,
    variants,
)
from user.schemas.plan_layout import PlanLayout_V1


def _unchanged(value: dict) -> None:
    assert PlanLayout_V1.model_validate(value).model_dump(exclude_unset=True) == value


@pytest.mark.parametrize(
    'value',
    [
        DEFAULT,
        CUSTOM,
        {'v': 1, 'preset': 'custom', 'custom': {'wide': STANDARD_WIDE, 'phone': STANDARD_PHONE}},
        at_limits_items(),
        at_limits_pairs(),
        at_limits_chains(),
        # Standard with folds and Tinted parts, no Custom Layout yet
        {'v': 1, 'preset': 'standard', 'custom': None, 'folded': {'wide': ['production'], 'phone': []}, 'tinted': True},
        # Standard active, Custom kept for switching back
        {**CUSTOM, 'preset': 'standard'},
    ],
)
def test_valid_values_pass_unchanged(value: dict):
    _unchanged(value)


@pytest.mark.parametrize('value', variants(), ids=lambda v: f'{v["preset"]}-{len(str(v))}')
def test_every_value_plan_layouts_01_saved_passes_unchanged(value: dict):
    _unchanged(value)


def test_the_same_element_in_wide_and_phone_is_accepted():
    wide_els = {el for it in CUSTOM['custom']['wide']['items'] for el in it['els']}
    phone_els = {el for it in CUSTOM['custom']['phone']['items'] for el in it['els']}
    assert wide_els & phone_els
    _unchanged(CUSTOM)


def test_missing_folded_lists_read_as_empty():
    model = PlanLayout_V1.model_validate({'v': 1, 'preset': 'standard', 'folded': {'wide': ['housing']}})
    assert model.folded.phone == []
    assert PlanLayout_V1.model_validate({'v': 1, 'preset': 'standard'}).folded.wide == []


def _wide(v: dict) -> dict:
    return v['custom']['wide']


def _it(v: dict, i: int = 1) -> dict:
    return _wide(v)['items'][i]


def _set(target: Callable[[dict], dict], **changes: object) -> Callable[[dict], None]:
    return lambda v: target(v).update(changes)


def _limit(base: Callable[[], dict], change: Callable[[dict], object]) -> Callable[[], dict]:
    def make() -> dict:
        value = base()
        change(value)
        return value

    return make


def _from_custom(change: Callable[[dict], object]) -> Callable[[], dict]:
    return _limit(lambda: copy.deepcopy(CUSTOM), change)


# (case, value, a part of the error path or message)
INVALID = [
    # each limit + 1
    ('wide cols 25', _from_custom(_set(_wide, cols=25)), 'custom.wide.cols'),
    ('wide cols 3', _from_custom(_set(_wide, cols=3)), 'custom.wide.cols'),
    (
        '41 items',
        _limit(
            at_limits_items,
            lambda v: _wide(v)['items'].append({**_wide(v)['items'][0], 'id': 'i-x', 'els': ['x'], 'active': 'x'}),
        ),
        'custom.wide',
    ),  # fmt: skip
    ('41 element ids', _limit(at_limits_pairs, lambda v: _wide(v)['items'][0]['els'].append('x')), 'custom.wide'),
    (
        '41 tabs',
        _limit(
            at_limits_pairs,
            lambda v: _wide(v).update(
                items=[
                    {
                        **_wide(v)['items'][0],
                        'els': [f'e{i}' for i in range(41)],
                        'active': 'e0',
                        'join': {},
                        'joinSize': {},
                    }
                ]
            ),
        ),
        'custom.wide.items.0.els',
    ),  # fmt: skip
    ('65 known', _limit(at_limits_items, lambda v: v['custom']['known'].append('x')), 'custom.known'),
    ('h 4001', _from_custom(_set(_it, h=4001)), 'custom.wide.items.1.h'),
    ('41 folded', _limit(at_limits_items, lambda v: v['folded']['wide'].append('x')), 'folded.wide'),
    ('41 open', _from_custom(_set(_wide, open=[f'e{i}' for i in range(41)])), 'custom.wide.open'),
    ('y 1000', _from_custom(_set(_it, y=1000)), 'custom.wide.items.1.y'),
    ('joinSize beside = w', _from_custom(lambda v: _it(v, 2)['joinSize'].update(experts=4)), 'joinSize.experts'),
    (
        'joinSize below 4001',
        _from_custom(lambda v: _it(v)['joinSize'].update({'material-io-totals': 4001})),
        'joinSize.material-io-totals',
    ),  # fmt: skip
    (
        'joinSize below 63',
        _from_custom(lambda v: _it(v)['joinSize'].update({'material-io-totals': 63})),
        'joinSize.material-io-totals',
    ),  # fmt: skip
    ('joinSize beside 0', _from_custom(lambda v: _it(v, 2)['joinSize'].update(experts=0)), 'joinSize.experts'),
    (
        'joinSize beside chain over w - 1',
        _limit(at_limits_chains, lambda v: _wide(v)['items'][2]['joinSize'].update(e29=8)),
        'joinSize.e29: a tab',
    ),  # fmt: skip
    # shape
    ('unknown top key', _from_custom(lambda v: v.update(extra=1)), 'extra'),
    ('unknown item key', _from_custom(_set(_it, extra=1)), 'custom.wide.items.1.extra'),
    ('v 2', _from_custom(lambda v: v.update(v=2)), 'v'),
    ('number as string', _from_custom(_set(_it, w='7')), 'custom.wide.items.1.w'),
    ('bad element id', _from_custom(lambda v: _it(v)['els'].append('Bad Id')), 'custom.wide.items.1.els.3'),
    ('bad item id', _from_custom(_set(_it, id='I X')), 'custom.wide.items.1.id'),
    ('duplicate item id', _from_custom(_set(_it, id='i-kpi-profit')), 'item id at most once'),
    ('duplicate element', _from_custom(lambda v: _it(v, 3)['els'].append('production')), 'each element at most once'),
    ('active not in els', _from_custom(_set(_it, active='housing')), 'active must be one of els'),
    ('active joined', _from_custom(_set(_it, active='material-io-totals')), 'active must not be a joined'),
    ('x + w > cols', _from_custom(_set(_it, x=6)), 'items.1: x + w'),
    ('h 63 with fit', _from_custom(_set(_it, hm='fit', h=63)), 'h must be 64-4000'),
    ('h 0 with fixed', _from_custom(_set(_it, h=0)), 'h must be 64-4000'),
    ('h 0 with cap', _from_custom(_set(_it, hm='cap', h=0)), 'h must be 64-4000'),
    ('follow with pin', _from_custom(_set(_it, pin=True)), 'follow is not allowed with pin'),
    ('follow false', _from_custom(_set(_it, follow=False)), 'custom.wide.items.1.follow'),
    (
        'join on the first',
        _from_custom(lambda v: (_it(v)['join'].update(production='below'), _it(v).update(active='construction-cart'))),
        'join.production',
    ),
    (
        'join mixed sides in a tab',
        _from_custom(lambda v: _it(v)['join'].update({'construction-cart': 'beside'})),
        'join.material-io-totals',
    ),  # fmt: skip
    ('join not in els', _from_custom(lambda v: _it(v)['join'].update(housing='below')), 'join.housing'),
    ('join bad side', _from_custom(lambda v: _it(v)['join'].update({'material-io-totals': 'above'})), 'join'),
    (
        'joinSize without join',
        _from_custom(lambda v: _it(v)['joinSize'].update({'construction-cart': 100})),
        'joinSize.construction-cart',
    ),  # fmt: skip
    ('gap 10', _from_custom(_set(_wide, gap=10)), 'custom.wide.gap'),
    ('phone cols 3', _from_custom(lambda v: v['custom']['phone'].update(cols=3)), 'custom.phone.cols'),
    ('custom without wide', _from_custom(lambda v: v['custom'].pop('wide')), 'custom.wide'),
    ('custom preset without custom', _from_custom(lambda v: v.update(custom=None)), 'preset custom needs custom'),
    ('no items', _from_custom(_set(_wide, items=[])), 'custom.wide.items'),
    ('no els', _from_custom(_set(_it, els=[])), 'custom.wide.items.1.els'),
    ('duplicate folded', _from_custom(lambda v: v['folded']['phone'].append('housing')), 'folded.phone'),
    ('duplicate open', _from_custom(lambda v: _wide(v)['open'].append('experts')), 'custom.wide.open'),
    ('duplicate table', _from_custom(lambda v: _wide(v)['table'].append('workforce')), 'custom.wide.table'),
    ('duplicate known', _from_custom(lambda v: v['custom']['known'].append('production')), 'custom.known'),
    ('tinted as string', _from_custom(lambda v: v.update(tinted='yes')), 'tinted'),
]


@pytest.mark.parametrize(('make', 'expected'), [(m, e) for _, m, e in INVALID], ids=[c for c, _, _ in INVALID])
def test_invalid_values_name_the_path(make: Callable[[], dict], expected: str):
    with pytest.raises(ValidationError) as exc_info:
        PlanLayout_V1.model_validate(make())

    errors = [f'{".".join(map(str, e["loc"]))}: {e["msg"]}' for e in exc_info.value.errors()]
    assert any(expected in error for error in errors), errors
