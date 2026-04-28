from datetime import timedelta

import structlog
from celery import shared_task
from django.db.models import Count, Q
from django.utils import timezone
from gamedata.models import (
    GameBuilding,
    GameBuildingCost,
    GameExchangeAnalytics,
    GameExchangeCXPC,
    GamePlanet,
    GamePlanetCOGCProgram,
    GamePlanetProductionFee,
    GamePlanetResource,
    GameRecipe,
    GameRecipeInput,
    GameRecipeOutput,
)
from planning.models import PlanningCX, PlanningEmpire, PlanningEmpirePlan, PlanningPlan
from planning.services.empire_state_service import EmpireStateService
from user.models import User

from analytics.models import AppStatistic
from analytics.services.planinsight_aggregator_service import PlanInsightAggregatorService

logger = structlog.get_logger(__name__)


@shared_task(name='update_daily_stats')
def update_daily_stats():
    structlog.contextvars.bind_contextvars(
        task_category='update_daily_stats',
    )

    now = timezone.now()
    today_date = now.date()
    yesterday_date = today_date - timedelta(days=1)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    thirty_days_ago = now - timedelta(days=30)

    log = logger.bind(name='update_daily_stats', today_date=today_date)

    try:
        yesterday = AppStatistic.objects.filter(date=yesterday_date).first()

        user_stats = User.objects.aggregate(
            total=Count('id'),
            active_today=Count('id', filter=Q(last_login__gte=today_start)),
            active_30d=Count('id', filter=Q(last_login__gte=thirty_days_ago)),
        )

        current_counts = {
            # user
            'user_count': user_stats['total'],
            # planning
            'plan_count': PlanningPlan.objects.count(),
            'empire_count': PlanningEmpire.objects.count(),
            'cx_count': PlanningCX.objects.count(),
        }

        deltas = {}
        for key, value in current_counts.items():
            prev_value = getattr(yesterday, key, 0) if yesterday else 0
            deltas[f'{key}_delta'] = value - prev_value

        _stats_obj, _created = AppStatistic.objects.update_or_create(
            date=today_date,
            defaults={
                # user
                'users_active_today': user_stats['active_today'],
                'users_active_30d': user_stats['active_30d'],
                # planning
                'plan_empire_junctions_count': PlanningEmpirePlan.objects.count(),
                # other currents
                **current_counts,
                # deltas
                **deltas,
                # game data
                'building_count': GameBuilding.objects.count(),
                'building_cost_count': GameBuildingCost.objects.count(),
                'exchange_analytics_count': GameExchangeAnalytics.objects.count(),
                'exchange_cxpc_count': GameExchangeCXPC.objects.count(),
                'planet_count': GamePlanet.objects.count(),
                'planet_cogc_count': GamePlanetCOGCProgram.objects.count(),
                'planet_productionfee_count': GamePlanetProductionFee.objects.count(),
                'planet_resource_count': GamePlanetResource.objects.count(),
                'recipe_count': GameRecipe.objects.count(),
                'recipe_input_count': GameRecipeInput.objects.count(),
                'recipe_output_count': GameRecipeOutput.objects.count(),
            },
        )

        return f'Stats updated successfully for {now.date()}'

    except Exception as exc:
        log.error('Failed to update daily statistics', exc_info=exc)
        return f'Failed to update stats at {now.date()}'


@shared_task(name='analytics_update_plan_insight_aggregates')
def analytics_update_plan_insight_aggregates():
    structlog.contextvars.bind_contextvars(
        task_category='analytics_update_plan_insight_aggregates',
    )

    log = logger.bind(name='analytics_update_plan_insight_aggregates')

    try:
        aggregator = PlanInsightAggregatorService()
        processed, deleted = aggregator.aggregate_all_plans()

        log.info('completed', processed=processed, deleted=deleted)

    except Exception as exc:
        log.error('exception', exc_info=exc)


@shared_task(name='analytics_bulk_materialize_empire_snapshots')
def analytics_bulk_materialize_empire_snapshots():
    structlog.contextvars.bind_contextvars(
        task_category='analytics_bulk_materialize_empire_snapshots',
    )

    log = logger.bind(name='analytics_bulk_materialize_empire_snapshots')

    # find all dirty PlanningEmpire and process in chunks
    dirty_empires = PlanningEmpire.objects.filter(needs_state_sync=True).iterator(chunk_size=100)

    processed_count = 0
    error_count = 0

    for empire in dirty_empires:
        try:
            EmpireStateService.sync_snapshot(empire)

            # clear and update flag only
            empire.needs_state_sync = False
            empire.save(update_fields=['needs_state_sync'])

            processed_count += 1

        except Exception as exc:
            error_count += 1
            log.error('exception', exc_info=exc)

    log.info('completed', processed=processed_count, errors=error_count)
