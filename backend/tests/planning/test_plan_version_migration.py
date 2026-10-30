import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from model_bakery import baker
from planning.models import PlanningPlan
from user.models import User

BEFORE = [('planning', '0009_empire_config_modified_at_backfill')]
AFTER = [('planning', '0010_planningplanversion')]
TABLE = 'prunplanner_planning_plan_versions'


def migrate(targets: list[tuple[str, str]]) -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(targets)


@pytest.mark.django_db(transaction=True)
def test_only_creates_the_table_and_leaves_plans_alone() -> None:
    plan = baker.make(PlanningPlan, user=baker.make(User), plan_permits_used=1, plan_data={'a': 1})
    row = PlanningPlan.objects.filter(pk=plan.pk).values().get()

    try:
        migrate(BEFORE)
        assert TABLE not in connection.introspection.table_names()
        assert PlanningPlan.objects.filter(pk=plan.pk).values().get() == row

        migrate(AFTER)
        assert TABLE in connection.introspection.table_names()
        assert PlanningPlan.objects.filter(pk=plan.pk).values().get() == row
    finally:
        migrate(AFTER)
