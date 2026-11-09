"""English texts of the link preview cards. Crawlers send no locale, so the cards aren't localised."""

from datetime import datetime

from planning.schemas.planning_plan_data import PlanningPlanData_V1
from planning.schemas.planning_plan_summary import PlanSummary_V1
from pydantic import ValidationError

CURRENCY = 'ȼ'

_EXCHANGE_NAMES = {'AI1': 'AI1', 'CI1': 'CI1', 'IC1': 'IC1', 'NC1': 'NC1', 'UNIVERSE': 'Universe'}
_BASIS_NAMES = {'7D': '7-day average', '30D': '30-day average', 'ASK': 'ask', 'BID': 'bid'}

# the planet's COGC program as players say it
_COGC_NAMES = {
    'AGRICULTURE': 'Agriculture',
    'CHEMISTRY': 'Chemistry',
    'CONSTRUCTION': 'Construction',
    'ELECTRONICS': 'Electronics',
    'FOOD_INDUSTRIES': 'Food Industries',
    'FUEL_REFINING': 'Fuel Refining',
    'MANUFACTURING': 'Manufacturing',
    'METALLURGY': 'Metallurgy',
    'RESOURCE_EXTRACTION': 'Resource Extraction',
    'PIONEERS': 'Pioneers',
    'SETTLERS': 'Settlers',
    'TECHNICIANS': 'Technicians',
    'ENGINEERS': 'Engineers',
    'SCIENTISTS': 'Scientists',
}

CALL_TO_ACTION = 'Open it to see the whole base and try changes, no account needed.'


def money(value: float) -> str:
    """27,740 ȼ"""
    return f'{round(value):,} {CURRENCY}'


def days(value: float) -> str:
    rounded = max(round(value), 1)
    return f'{rounded} day' if rounded == 1 else f'{rounded:,} days'


def date_label(value: datetime) -> str:
    """8 Nov 2026"""
    return f'{value.day} {value:%b %Y}'


def cogc_label(cogc: str | None) -> str | None:
    """'METALLURGY' → 'Metallurgy COGC'; None for none ('---')"""
    name = _COGC_NAMES.get(cogc or '')
    return f'{name} COGC' if name else None


def price_basis_label(code: str | None) -> str | None:
    """'CI1_30D' → 'CI1 30-day average', 'CUSTOM' → 'your own prices'"""
    if code is None:
        return None
    if code == 'CUSTOM':
        return 'own prices'
    exchange, _, basis = code.partition('_')
    if exchange not in _EXCHANGE_NAMES or basis not in _BASIS_NAMES:
        return None
    return f'{_EXCHANGE_NAMES[exchange]} {_BASIS_NAMES[basis]}'


def planet_label(planet_name: str, planet_natural_id: str) -> str:
    """'Montem (OT-580b)', or the natural id alone for an unnamed planet"""
    if planet_name and planet_name != planet_natural_id:
        return f'{planet_name} ({planet_natural_id})'
    return planet_natural_id


def plan_title(plan_name: str, planet: str) -> str:
    return f'{plan_name} · {planet}'


def plan_data_facts(plan_data: object) -> tuple[int, list[str]]:
    """Building count and output tickers of a stored plan_data, for the card without a summary"""
    try:
        data = PlanningPlanData_V1.model_validate(plan_data)
    except ValidationError:
        return 0, []
    count = 0
    outputs: list[str] = []
    for building in data.buildings:
        if not building.amount:
            continue
        count += building.amount
        for recipe in building.active_recipes:
            # "COL#O" (extraction) or "FP#1xCAF 3xDW=>3xCOF"
            produced = recipe.recipeid.partition('#')[2].rpartition('=>')[2]
            for token in produced.split():
                ticker = token.partition('x')[2] if token[0].isdigit() else token
                if ticker and ticker not in outputs:
                    outputs.append(ticker)
    return count, outputs


def plan_description(
    *,
    username: str,
    summary: PlanSummary_V1 | None,
    summary_at: datetime | None,
    cogc: str | None,
    planet: str,
    building_count: int,
    outputs: list[str],
) -> str:
    """With a summary: its numbers. Without: what plan_data says (planet, COGC, buildings, outputs), no numbers."""
    parts = [f'Shared by {username}']
    cogc_text = cogc_label(cogc)

    if summary is not None:
        parts.append(f'{money(summary.profit)}/day')
        if summary.payback_days is not None:
            parts.append(f'pays back in {days(summary.payback_days)}')
        if cogc_text:
            parts.append(cogc_text)
        if summary.outputs:
            parts.append('makes ' + ', '.join(o.ticker for o in summary.outputs))
        basis = price_basis_label(summary.price_basis)
        when = f'as of {date_label(summary_at)}' if summary_at else None
        if basis and when:
            parts.append(f'priced with {basis}, {when}')
        elif when:
            parts.append(when)
    else:
        parts.append(planet)
        if cogc_text:
            parts.append(cogc_text)
        if building_count:
            parts.append(f'{building_count} building' + ('' if building_count == 1 else 's'))
        if outputs:
            parts.append('makes ' + ', '.join(outputs[:6]) + ('…' if len(outputs) > 6 else ''))

    text = ' · '.join(parts)
    return f'{text} {CALL_TO_ACTION}' if text.endswith('…') else f'{text}. {CALL_TO_ACTION}'
