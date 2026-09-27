from decimal import ROUND_HALF_UP, Decimal

from analytics.models import AnalyticsEmpireMaterialSnapshot
from django.db import transaction
from django.utils import timezone
from planning.models import PlanningEmpire


class EmpireStateService:
    @staticmethod
    def update_state(empire: PlanningEmpire, state_data: dict) -> None:
        """Updates the empires state JSON dict only and sets the needs_state_sync flag to True"""

        empire.empire_state = state_data
        empire.needs_state_sync = True
        empire.modified_at = timezone.now()
        # queryset update fires no post_save: the state is in no cached payload
        PlanningEmpire.objects.filter(pk=empire.pk).update(
            empire_state=empire.empire_state, needs_state_sync=True, modified_at=empire.modified_at
        )

    @staticmethod
    def sync_snapshot(empire: PlanningEmpire) -> None:
        """Performs snapshot sync into AnalyticsEmpireMaterialSnapshot object"""

        state_data = empire.empire_state or {}
        empire_total = state_data.get('empire_total', {})

        # upsert / delete stale from EmpireMaterialSnapshot
        active_tickers = []
        snapshot_objs = []

        # pre-defined quantizer
        quantizer = Decimal('0.000001')

        for material_ticker, stats in empire_total.items():
            # decimal conversion
            p = Decimal(str(stats.get('p', 0))).quantize(quantizer, rounding=ROUND_HALF_UP)
            c = Decimal(str(stats.get('c', 0))).quantize(quantizer, rounding=ROUND_HALF_UP)

            d = p - c

            if p != 0 or c != 0 or d != 0:
                active_tickers.append(material_ticker)

                snapshot_objs.append(
                    AnalyticsEmpireMaterialSnapshot(
                        empire=empire, material_ticker=material_ticker, production=p, consumption=c, delta=d
                    )
                )

        with transaction.atomic():
            # only remove materials no longer in the empire total delta
            AnalyticsEmpireMaterialSnapshot.objects.filter(empire=empire).exclude(
                material_ticker__in=active_tickers
            ).delete()

            # perform the upsert
            AnalyticsEmpireMaterialSnapshot.objects.bulk_create(
                snapshot_objs,
                update_conflicts=True,
                unique_fields=['empire', 'material_ticker'],
                update_fields=['production', 'consumption', 'delta'],
            )
