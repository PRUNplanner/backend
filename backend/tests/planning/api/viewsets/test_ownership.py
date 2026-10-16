import pytest
from django.apps import apps
from django.forms.models import model_to_dict
from django.urls import reverse
from model_bakery import baker

pytestmark = pytest.mark.django_db

# every action on a single owned object; a shared plan's GET and clone are public by design
OWNED_OBJECT_ACTIONS = [
    ('planning.PlanningPlan', 'planning:plan-detail', 'get'),
    ('planning.PlanningPlan', 'planning:plan-detail', 'put'),
    ('planning.PlanningPlan', 'planning:plan-detail', 'delete'),
    ('planning.PlanningPlan', 'planning:plan-clone', 'post'),
    ('planning.PlanningEmpire', 'planning:empire-detail', 'get'),
    ('planning.PlanningEmpire', 'planning:empire-detail', 'put'),
    ('planning.PlanningEmpire', 'planning:empire-detail', 'delete'),
    ('planning.PlanningEmpire', 'planning:empire-plan-list', 'get'),
    ('planning.PlanningEmpire', 'planning:empire-plan-list-csv', 'get'),
    ('planning.PlanningEmpire', 'planning:empire-material-io', 'get'),
    ('planning.PlanningEmpire', 'planning:empire-material-io-csv', 'get'),
    ('planning.PlanningEmpire', 'planning:empire-plans-material-io', 'get'),
    ('planning.PlanningEmpire', 'planning:empire-plans-material-io-csv', 'get'),
    ('planning.PlanningEmpire', 'planning:empire-sync-state', 'patch'),
    ('planning.PlanningCX', 'planning:cx-detail', 'get'),
    ('planning.PlanningCX', 'planning:cx-detail', 'put'),
    ('planning.PlanningCX', 'planning:cx-detail', 'delete'),
    ('planning.PlanningShared', 'planning:shared-detail', 'delete'),
]


@pytest.mark.parametrize(('model_label', 'url_name', 'method'), OWNED_OBJECT_ACTIONS)
def test_another_users_object_is_not_found_and_left_untouched(
    api_client, model_label: str, url_name: str, method: str
) -> None:
    owner, stranger = baker.make('user.User'), baker.make('user.User')
    model = apps.get_model(model_label)
    obj = baker.make(model, user=owner)
    before = model_to_dict(obj)

    client = api_client.as_user(stranger)
    response = getattr(client, method)(reverse(url_name, kwargs={'pk': obj.uuid}), {}, format='json')

    assert response.status_code == 404
    assert model.objects.count() == 1
    obj.refresh_from_db()
    assert model_to_dict(obj) == before
