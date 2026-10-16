from gamedata.models import GameMaterial
from planning.models import PlanningEmpire

# CSV column order; the JSON rows carry the same keys
MATERIAL_IO_HEADER = ['ticker', 'material_name', 'production', 'consumption', 'delta', 'updated_at']
PLAN_MATERIAL_IO_HEADER = [
    'plan_uuid',
    'plan_name',
    'planet_natural_id',
    'cogc',
    'ticker',
    'production',
    'consumption',
    'delta',
    'updated_at',
]
PLAN_LIST_HEADER = [
    'plan_uuid',
    'plan_name',
    'planet_natural_id',
    'plan_cogc',
    'plan_permits_used',
    'plan_corphq',
    'modified_at',
]

type Row = dict[str, str | float | int | bool | None]


def _io(stats: dict[str, float]) -> Row:
    p, c = stats.get('p', 0), stats.get('c', 0)
    # rounded as in EmpireStateService.sync_snapshot, so sheet cells don't show float noise (95.5 - 80.3)
    return {'production': p, 'consumption': c, 'delta': round(p - c, 6)}


class EmpireExportService:
    """Rows of an empire's saved state (empire_state, as last synced by the frontend), per day."""

    @staticmethod
    def updated_at(empire: PlanningEmpire) -> str | None:
        return (empire.empire_state or {}).get('metadata', {}).get('timestamp')

    @classmethod
    def material_io_rows(cls, empire: PlanningEmpire) -> list[Row]:
        totals: dict[str, dict[str, float]] = (empire.empire_state or {}).get('empire_total', {})
        names = dict(GameMaterial.objects.filter(ticker__in=totals).values_list('ticker', 'name'))
        updated_at = cls.updated_at(empire)

        return [
            {'ticker': ticker, 'material_name': names.get(ticker), **_io(totals[ticker]), 'updated_at': updated_at}
            for ticker in sorted(totals)
        ]

    @classmethod
    def plan_material_io_rows(cls, empire: PlanningEmpire) -> list[Row]:
        details: dict[str, dict] = (empire.empire_state or {}).get('plan_details', {})
        # plans unlinked since the last sync are left out
        plan_names = {str(uuid): name for uuid, name in empire.plans.values_list('uuid', 'plan_name')}
        updated_at = cls.updated_at(empire)

        rows: list[Row] = []
        for plan_uuid in sorted(plan_names.keys() & details.keys(), key=lambda u: (plan_names[u], u)):
            metadata = details[plan_uuid].get('metadata', {})
            deltas: dict[str, dict[str, float]] = details[plan_uuid].get('deltas', {})
            rows.extend(
                {
                    'plan_uuid': plan_uuid,
                    'plan_name': plan_names[plan_uuid],
                    'planet_natural_id': metadata.get('planet_natural_id'),
                    'cogc': metadata.get('cogc'),
                    'ticker': ticker,
                    **_io(deltas[ticker]),
                    'updated_at': updated_at,
                }
                for ticker in sorted(deltas)
            )
        return rows

    @staticmethod
    def plan_list_rows(plans: list[dict]) -> list[Row]:
        """Projects PlanningPlanListSerializer data to PLAN_LIST_HEADER."""
        return [{'plan_uuid': plan['uuid'], **{k: plan[k] for k in PLAN_LIST_HEADER[1:]}} for plan in plans]
