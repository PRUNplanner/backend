from analytics.models import AnalyticsEmpireMaterialSnapshot
from django.db import transaction
from planning.models import PlanningEmpire


class EmpireStateService:
    @staticmethod
    @transaction.atomic
    def sync_empire_state(empire: PlanningEmpire, state_data: dict) -> None:

        # update the json field
        empire.empire_state = state_data
        empire.save(update_fields=['empire_state', 'modified_at'])

        # upsert / delete stale from EmpireMaterialSnapshot
        empire_total = state_data.get('empire_total', {})
        active_tickers = []

        snapshot_objs = []
        for material_ticker, stats in empire_total.items():
            p, c, d = stats.get('p', 0), stats.get('c', 0), stats.get('d', 0)
            if p != 0 or c != 0 or d != 0:
                active_tickers.append(material_ticker)

                snapshot_objs.append(
                    AnalyticsEmpireMaterialSnapshot(
                        empire=empire, material_ticker=material_ticker, production=p, consumption=c, delta=d
                    )
                )

        # only remove materials no longer in the empire total delta
        AnalyticsEmpireMaterialSnapshot.objects.filter(empire=empire).exclude(
            material_ticker__in=active_tickers
        ).delete()

        # perform the upsert
        AnalyticsEmpireMaterialSnapshot.objects.bulk_create(
            snapshot_objs,
            update_conflicts=True,
            update_fields=['production', 'consumption', 'delta'],
            unique_fields=['empire', 'material_ticker'],
        )
