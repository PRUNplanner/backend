"""planLayout preference values: Standard and its phone stacking as in plan-layouts-01, values at the limits,
and values 01 saved (fxt_plan_layout_variants.json, picked from specs/assets/plan-layouts/variants.gen.test.ts)."""

import copy
import json
from pathlib import Path


def _item(els: list[str], x: int, w: int, y: int, **more: object) -> dict:
    return {'id': f'i-{els[0]}', 'els': els, 'active': els[0], 'x': x, 'w': w, 'y': y, 'hm': 'fit', 'h': 0,
            'pin': False, **more}  # fmt: skip


_BASE = ['empire', 'planet', 'workforce', 'housing', 'experts']
_TOOLS = ['construction-cart', 'supply-cart', 'visitation-frequency', 'repair-analysis', 'profit-breakdown',
          'plan-check', 'popr']  # fmt: skip

# frontend src/features/planning/layout/presets.ts, "standard"
STANDARD_WIDE: dict = {
    'cols': 12,
    'gap': 16,
    'items': [
        _item(['kpi-profit'], 0, 3, 0, pin=True),
        _item(['kpi-payback'], 3, 2, 1, pin=True),
        _item(['kpi-workforce'], 5, 3, 2, pin=True),
        _item(['kpi-area'], 8, 2, 3, pin=True),
        _item(['kpi-storage'], 10, 2, 4, pin=True),
        *(_item([el], 0, 2, 5 + i) for i, el in enumerate(_BASE)),
        _item(['production', *_TOOLS], 2, 7, 10),
        _item(['material-io'], 9, 3, 11),
        _item(['material-io-totals'], 9, 3, 12),
    ],
}
# phoneLayout.ts phoneFromWide: pinned first, reading order, small parts two per row
STANDARD_PHONE: dict = {
    'cols': 2,
    'gap': 16,
    'items': [
        {**it, 'x': i % 2 if it['pin'] else 0, 'w': 1 if it['pin'] else 2, 'y': i}
        for i, it in enumerate(STANDARD_WIDE['items'])
    ],  # fmt: skip
}

DEFAULT: dict = {'v': 1, 'preset': 'standard', 'custom': None, 'folded': {'wide': [], 'phone': []}}

# a customised layout: a below and a beside pair with sizes, follow, bare, a cap and a fixed height, open and table
CUSTOM: dict = {
    'v': 1,
    'preset': 'custom',
    'custom': {
        'wide': {
            'cols': 12,
            'gap': 16,
            'items': [
                _item(['kpi-profit'], 0, 3, 0, pin=True),
                {
                    **_item(['production', 'construction-cart', 'material-io-totals'], 2, 7, 1),
                    'hm': 'fixed',
                    'h': 480,
                    'follow': True,
                    'join': {'material-io-totals': 'below'},
                    'joinSize': {'material-io-totals': 360},
                },  # fmt: skip
                {
                    **_item(['workforce', 'experts'], 0, 4, 2),
                    'join': {'experts': 'beside'},
                    'joinSize': {'experts': 2},
                    'bare': True,
                },
                {**_item(['material-io'], 9, 3, 3), 'hm': 'cap', 'h': 640, 'bare': True},
            ],
            'open': ['experts'],
            'table': ['workforce'],
        },
        'phone': {
            'cols': 2,
            'gap': 8,
            'items': [_item(['kpi-profit'], 0, 1, 0, pin=True), _item(['production'], 0, 2, 1)],
        },
        'known': ['production', 'kpi-profit', 'workforce', 'experts'],
    },
    'folded': {'wide': ['housing', 'experts'], 'phone': ['housing']},
    'tinted': True,
}

_IDS = [f'e{i:02d}' for i in range(64)]


def at_limits_items() -> dict:
    """Wide 24 columns and Phone, 40 items each, 40 element ids, 64 known, h 4000, 40 folded each, tinted."""
    wide = {'cols': 24, 'gap': 24, 'items': [
        {**_item([el], i % 24, 1, i), 'hm': 'fixed', 'h': 4000} for i, el in enumerate(_IDS[:40])]}  # fmt: skip
    phone = {'cols': 2, 'gap': 8, 'items': [_item([el], i % 2, 1, i) for i, el in enumerate(_IDS[:40])]}
    return {'v': 1, 'preset': 'custom', 'custom': {'wide': wide, 'phone': phone, 'known': list(_IDS)},
            'folded': {'wide': _IDS[:40], 'phone': _IDS[:40]}, 'tinted': True}  # fmt: skip


def at_limits_pairs() -> dict:
    """40 element ids as tabs in pairs: below and beside joins with their largest sizes, y at 999."""
    items = []
    for n in range(10):
        a, b, c, d = _IDS[4 * n : 4 * n + 4]
        items.append({**_item([a, b, c, d], 0, 24, 999 - n), 'hm': 'cap', 'h': 4000,
                      'join': {b: 'beside', d: 'below'}, 'joinSize': {b: 23, d: 4000}})  # fmt: skip
    return {'v': 1, 'preset': 'custom', 'custom': {'wide': {'cols': 24, 'gap': 8, 'items': items}},
            'folded': {'wide': [], 'phone': []}}  # fmt: skip


def variants() -> list[dict]:
    """Values plan-layouts-01 saved, from random walks of its editor ops, folds and tab switches."""
    return json.loads((Path(__file__).parent / 'fxt_plan_layout_variants.json').read_text())


def layout(value: dict) -> dict:
    return copy.deepcopy(value)


def at_limits_chains() -> dict:
    """40 element ids in four tabs of ten parts: below at the largest height, beside sharing all but one column."""
    below = [{**_item(_IDS[10 * n : 10 * n + 10], 0, 24, n), 'hm': 'fit', 'h': 0,
              'join': dict.fromkeys(_IDS[10 * n + 1 : 10 * n + 10], 'below'),
              'joinSize': dict.fromkeys(_IDS[10 * n + 1 : 10 * n + 10], 4000)} for n in range(2)]  # fmt: skip
    beside = [{**_item(_IDS[10 * n : 10 * n + 10], 0, 24, n), 'hm': 'fit', 'h': 0,
               'join': dict.fromkeys(_IDS[10 * n + 1 : 10 * n + 10], 'beside'),
               'joinSize': {**dict.fromkeys(_IDS[10 * n + 1 : 10 * n + 9], 2), _IDS[10 * n + 9]: 7}}
              for n in (2, 3)]  # fmt: skip
    return {'v': 1, 'preset': 'custom', 'custom': {'wide': {'cols': 24, 'gap': 8, 'items': below + beside}},
            'folded': {'wide': [], 'phone': []}}  # fmt: skip
