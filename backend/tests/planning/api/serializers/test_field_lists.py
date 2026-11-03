import pytest
from model_bakery import baker
from planning.api.serializers.cx import PlanningCXDetailSerializer
from planning.api.serializers.empire import PlanningEmpireDetailSerializer
from planning.api.serializers.minimal import PlanningCXMinimal

pytestmark = pytest.mark.django_db


# the exclude-based Meta each serializer had before its fields were listed explicitly
class _OldEmpireDetail(PlanningEmpireDetailSerializer):
    class Meta(PlanningEmpireDetailSerializer.Meta):
        fields = None
        read_only_fields = None
        exclude = ['created_at', 'config_modified_at', 'user', 'empire_state']


class _OldCXMinimal(PlanningCXMinimal):
    class Meta(PlanningCXMinimal.Meta):
        fields = None
        exclude = ['created_at', 'modified_at', 'schema_version', 'user']


class _OldCXDetail(PlanningCXDetailSerializer):
    class Meta(PlanningCXDetailSerializer.Meta):
        fields = None
        exclude = ['created_at', 'schema_version', 'user']


@pytest.mark.parametrize(
    ('new', 'old'),
    [
        (PlanningEmpireDetailSerializer, _OldEmpireDetail),
        (PlanningCXMinimal, _OldCXMinimal),
        (PlanningCXDetailSerializer, _OldCXDetail),
    ],
)
def test_explicit_fields_match_the_old_exclude(new, old):
    assert list(new().fields) == list(old().fields)


def test_empire_detail_output_unchanged():
    cx = baker.make('planning.PlanningCX', cx_data={})
    empire = baker.make(
        'planning.PlanningEmpire', cx=cx, empire_permits_used=1, empire_permits_total=2, empire_state={'x': 1}
    )
    assert PlanningEmpireDetailSerializer(empire).data == _OldEmpireDetail(empire).data


def test_empire_detail_needs_state_sync_is_read_only():
    fields = PlanningEmpireDetailSerializer().fields
    assert fields['needs_state_sync'].read_only


def test_plan_detail_documents_no_cx():
    # PlanningPlan has no cx, so the field was never sent; it only showed in the schema
    from planning.api.serializers.plan import PlanningPlanDetailSerializer

    assert 'cx' not in PlanningPlanDetailSerializer().fields
