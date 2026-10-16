import csv
import gzip
import io

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from model_bakery import baker
from planning.models import PlanningCOGCChoices, PlanningFactionChoices, PlanningPlan
from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis
from user.models import UserAPIKey

pytestmark = pytest.mark.django_db


def _empire_payload(**overrides):
    payload = {
        'empire_name': 'My Empire',
        'empire_faction': PlanningFactionChoices.ANTARES,
        'empire_permits_used': 1,
        'empire_permits_total': 2,
    }
    payload.update(overrides)
    return payload


class TestEmpireViewSetCrud:
    def test_list_requires_auth(self, api_client, user_factory, empire_factory):
        url = reverse('planning:empire')

        response_noauth = api_client.get(url)
        assert response_noauth.status_code == 401

        user = user_factory(id=1)
        empire_factory(user=user, empire_name='My Empire')

        response = api_client.as_user(user).get(url)
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]['empire_name'] == 'My Empire'

    def test_retrieve_404_and_200(self, api_client, user_factory, empire_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user, empire_name='My Empire')

        url_404 = reverse('planning:empire-detail', kwargs={'pk': '356da85a-494a-45a9-b20e-16d0f128c5b8'})
        response_404 = api_client.as_user(user).get(url_404)
        assert response_404.status_code == 404

        url = reverse('planning:empire-detail', kwargs={'pk': str(empire.uuid)})
        response = api_client.as_user(user).get(url)
        assert response.status_code == 200
        assert response.data['uuid'] == str(empire.uuid)

    def test_retrieve_plans(self, api_client, user_factory, empire_factory, plan_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user)
        plan = plan_factory(user=user, plan_data=plan_data_vallis)
        empire.plans.add(plan, through_defaults={'user': user})

        url = reverse('planning:empire-plan-list', kwargs={'pk': str(empire.uuid)})

        response_noauth = api_client.get(url)
        assert response_noauth.status_code == 401

        response = api_client.as_user(user).get(url)
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]['uuid'] == str(plan.uuid)

    def test_create(self, api_client, user_factory):
        user = user_factory(id=1)
        url = reverse('planning:empire')

        response_noauth = api_client.post(url, data=_empire_payload(), format='json')
        assert response_noauth.status_code == 401

        response = api_client.as_user(user).post(url, data=_empire_payload(), format='json')
        assert response.status_code == 201
        assert response.data['empire_name'] == 'My Empire'

    def test_update(self, api_client, user_factory, empire_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user, empire_name='Old Name')

        url = reverse('planning:empire-detail', kwargs={'pk': str(empire.uuid)})
        response = api_client.as_user(user).put(url, data=_empire_payload(empire_name='New Name'), format='json')

        assert response.status_code == 200
        assert response.data['empire_name'] == 'New Name'

    def test_destroy(self, api_client, user_factory, empire_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user)

        url = reverse('planning:empire-detail', kwargs={'pk': str(empire.uuid)})

        response_noauth = api_client.delete(url)
        assert response_noauth.status_code == 401

        response = api_client.as_user(user).delete(url)
        assert response.status_code == 204


class TestEmpireViewSetSyncJunctions:
    def test_sync_junctions_requires_auth(self, api_client):
        url = reverse('planning:empire-junctions')

        response = api_client.post(url, data=[], format='json')
        assert response.status_code == 401

    @pytest.mark.usefixtures('locmem_cache')
    def test_sync_junctions_creates_and_removes_links(
        self, api_client, user_factory, empire_factory, plan_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory(id=1)
        empire = empire_factory(user=user)
        plan_keep = plan_factory(user=user, plan_data=plan_data_vallis)
        plan_new = plan_factory(user=user, plan_data=plan_data_vallis)
        plan_drop = plan_factory(user=user, plan_data=plan_data_vallis)

        empire.plans.add(plan_keep, through_defaults={'user': user})
        empire.plans.add(plan_drop, through_defaults={'user': user})

        url = reverse('planning:empire-junctions')
        payload = [
            {
                'empire_uuid': str(empire.uuid),
                'baseplanners': [
                    {'baseplanner_uuid': str(plan_keep.uuid)},
                    {'baseplanner_uuid': str(plan_new.uuid)},
                ],
            }
        ]

        plan_list_url = reverse('planning:plan')
        api_client.as_user(user).get(plan_list_url)
        assert api_client.as_user(user).get(plan_list_url)['X-Cache-Hit'] == '1'

        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.as_user(user).post(url, data=payload, format='json')

        assert response.status_code == 200
        assert {p['uuid'] for p in response.data[0]['plans']} == {str(plan_keep.uuid), str(plan_new.uuid)}

        linked_plan_uuids = set(empire.plans.values_list('uuid', flat=True))
        assert linked_plan_uuids == {plan_keep.uuid, plan_new.uuid}

        # the cached plan list nests empires, so it must reflect the new links
        plans = {p['uuid']: p for p in api_client.as_user(user).get(plan_list_url).data}
        assert [e['uuid'] for e in plans[str(plan_new.uuid)]['empires']] == [str(empire.uuid)]
        assert plans[str(plan_drop.uuid)]['empires'] == []

    @pytest.mark.usefixtures('locmem_cache')
    def test_sync_junctions_no_changes_keeps_caches(self, api_client, user_factory, empire_factory, plan_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user)
        plan = plan_factory(user=user, plan_data=plan_data_vallis)
        empire.plans.add(plan, through_defaults={'user': user})

        plan_list_url = reverse('planning:plan')
        api_client.as_user(user).get(plan_list_url)

        url = reverse('planning:empire-junctions')
        payload = [{'empire_uuid': str(empire.uuid), 'baseplanners': [{'baseplanner_uuid': str(plan.uuid)}]}]

        response = api_client.as_user(user).post(url, data=payload, format='json')

        assert response.status_code == 200
        assert api_client.as_user(user).get(plan_list_url)['X-Cache-Hit'] == '1'

    def test_sync_junctions_rejects_unowned_references(self, api_client, user_factory, empire_factory, plan_factory):
        user = user_factory(id=1)
        other_user = user_factory(id=2)
        empire = empire_factory(user=user)
        other_plan = plan_factory(user=other_user, plan_data=plan_data_vallis)

        url = reverse('planning:empire-junctions')
        payload = [{'empire_uuid': str(empire.uuid), 'baseplanners': [{'baseplanner_uuid': str(other_plan.uuid)}]}]

        response = api_client.as_user(user).post(url, data=payload, format='json')

        assert response.status_code == 403
        assert response.data['invalid_plans'] == [other_plan.uuid]


class TestEmpireViewSetSyncState:
    def test_sync_state_requires_auth(self, api_client, user_factory, empire_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user)

        url = reverse('planning:empire-sync-state', kwargs={'pk': str(empire.uuid)})
        response = api_client.patch(url, data={}, format='json')

        assert response.status_code == 401

    @pytest.mark.usefixtures('locmem_cache')
    def test_sync_state_updates_empire_state(self, api_client, user_factory, empire_factory, plan_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user, empire_state={}, needs_state_sync=False)
        plan = plan_factory(user=user, plan_data=plan_data_vallis, planet_natural_id='OT-580b')

        url = reverse('planning:empire-sync-state', kwargs={'pk': str(empire.uuid)})
        response = api_client.as_user(user).patch(url, data=_state_payload(str(plan.uuid)), format='json')

        assert response.status_code == 200

        empire.refresh_from_db()
        assert empire.needs_state_sync is True
        assert empire.empire_state['empire_total']['H2O']['p'] == 10.0

    @pytest.mark.usefixtures('locmem_cache')
    def test_sync_state_keeps_planning_caches(
        self, api_client, user_factory, empire_factory, plan_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory(id=1)
        empire = empire_factory(user=user)
        plan = plan_factory(user=user, plan_data=plan_data_vallis, planet_natural_id='OT-580b')

        list_url = reverse('planning:empire')
        api_client.as_user(user).get(list_url)

        url = reverse('planning:empire-sync-state', kwargs={'pk': str(empire.uuid)})
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.as_user(user).patch(url, data=_state_payload(str(plan.uuid)), format='json')

        assert response.status_code == 200
        assert api_client.as_user(user).get(list_url)['X-Cache-Hit'] == '1'


class TestEmpireViewSetQueries:
    def test_list_query_count_is_constant(
        self, api_client, user_factory, empire_factory, plan_factory, cx_factory, django_assert_num_queries
    ):
        user = user_factory(id=1)
        for _ in range(5):
            empire = empire_factory(user=user, cx=cx_factory(user=user))
            empire.plans.add(plan_factory(user=user), through_defaults={'user': user})

        # empires, plans prefetch, cx prefetch
        with django_assert_num_queries(3):
            response = api_client.as_user(user).get(reverse('planning:empire'))

        assert len(response.data) == 5

    def test_list_does_not_load_empire_state(self, api_client, user_factory, empire_factory):
        user = user_factory(id=1)
        empire_factory(user=user)

        with CaptureQueriesContext(connection) as ctx:
            api_client.as_user(user).get(reverse('planning:empire'))

        assert not any('empire_state' in q['sql'] for q in ctx.captured_queries)


def _state_payload(plan_uuid: str) -> dict[str, object]:
    return {
        'metadata': {
            'faction': PlanningFactionChoices.ANTARES,
            'permits_used': 1,
            'permits_total': 2,
            'plan_count': 1,
            'timestamp': '2026-01-01T00:00:00Z',
        },
        'empire_total': {'H2O': {'p': 10.0, 'c': 5.0, 'd': 5.0}},
        'plan_details': {
            plan_uuid: {
                'metadata': {'planet_natural_id': 'OT-580b', 'cogc': PlanningCOGCChoices.NONE},
                'deltas': {'H2O': {'p': 10.0, 'c': 5.0, 'd': 5.0}},
            }
        },
    }


def _state(plan_details: dict[str, object] | None = None) -> dict[str, object]:
    return {
        'metadata': {
            'faction': PlanningFactionChoices.ANTARES,
            'permits_used': 2,
            'permits_total': 3,
            'plan_count': 2,
            'timestamp': '2026-10-16T09:12:44.123Z',
        },
        'empire_total': {'RAT': {'p': 80.0, 'c': 100.0, 'd': -20.0}, 'DW': {'p': 120.0, 'c': 95.5, 'd': 24.5}},
        'plan_details': plan_details or {},
    }


def _csv_rows(response) -> list[list[str]]:
    return list(csv.reader(io.StringIO(response.content.decode())))


class TestEmpireExports:
    @pytest.fixture
    def user(self, user_factory):
        return user_factory()

    @pytest.fixture
    def empire(self, user, empire_factory, plan_factory):
        baker.make('gamedata.GameMaterial', ticker='DW', name='drinkingWater')
        baker.make('gamedata.GameMaterial', ticker='RAT', name='rations')
        empire = empire_factory(user=user)
        linked_b = plan_factory(user=user, plan_name='B base', planet_natural_id='ZV-307c')
        linked_a = plan_factory(user=user, plan_name='A base', planet_natural_id='OT-580b')
        unlinked = plan_factory(user=user, plan_name='Gone')
        for plan in (linked_b, linked_a):
            empire.plans.add(plan, through_defaults={'user': user})

        def details(planet: str, deltas: dict[str, dict[str, float]]) -> dict[str, object]:
            return {'metadata': {'planet_natural_id': planet, 'cogc': PlanningCOGCChoices.NONE}, 'deltas': deltas}

        empire.empire_state = _state(
            {
                str(linked_b.uuid): details('ZV-307c', {'RAT': {'p': 80.0, 'c': 0.0, 'd': 80.0}}),
                str(linked_a.uuid): details(
                    'OT-580b', {'RAT': {'p': 0.0, 'c': 100.0, 'd': -100.0}, 'DW': {'p': 120.0, 'c': 95.5, 'd': 24.5}}
                ),
                str(unlinked.uuid): details('KW-688c', {'H2O': {'p': 1.0, 'c': 0.0, 'd': 1.0}}),
            }
        )
        empire.save()
        return empire

    def _get(self, api_client, user, route: str, empire, **extra):
        return api_client.as_user(user).get(reverse(route, kwargs={'pk': empire.uuid}), **extra)

    def test_material_io_json(self, api_client, user, empire):
        response = self._get(api_client, user, 'planning:empire-material-io', empire)

        assert response.status_code == 200
        assert response.data == {
            'updated_at': '2026-10-16T09:12:44.123Z',
            'rows': [
                {
                    'ticker': 'DW',
                    'material_name': 'drinkingWater',
                    'production': 120.0,
                    'consumption': 95.5,
                    'delta': 24.5,
                    'updated_at': '2026-10-16T09:12:44.123Z',
                },
                {
                    'ticker': 'RAT',
                    'material_name': 'rations',
                    'production': 80.0,
                    'consumption': 100.0,
                    'delta': -20.0,
                    'updated_at': '2026-10-16T09:12:44.123Z',
                },
            ],
        }

    @pytest.mark.parametrize(
        ('json_route', 'csv_route'),
        [
            ('planning:empire-material-io', 'planning:empire-material-io-csv'),
            ('planning:empire-plans-material-io', 'planning:empire-plans-material-io-csv'),
        ],
    )
    def test_csv_matches_json(self, api_client, user, empire, json_route: str, csv_route: str):
        rows = self._get(api_client, user, json_route, empire).data['rows']

        response = self._get(api_client, user, csv_route, empire)

        assert response.status_code == 200
        assert response['Content-Type'] == 'text/csv; charset=utf-8'
        header, *lines = _csv_rows(response)
        assert header == list(rows[0])
        assert lines == [[str(value) for value in row.values()] for row in rows]

    def test_plan_material_io_only_linked_plans_sorted(self, api_client, user, empire):
        rows = self._get(api_client, user, 'planning:empire-plans-material-io', empire).data['rows']

        assert [(r['plan_name'], r['ticker']) for r in rows] == [('A base', 'DW'), ('A base', 'RAT'), ('B base', 'RAT')]
        assert {r['planet_natural_id'] for r in rows} == {'OT-580b', 'ZV-307c'}
        assert all(r['updated_at'] == '2026-10-16T09:12:44.123Z' for r in rows)

    @pytest.mark.parametrize(
        ('json_route', 'csv_route', 'header'),
        [
            (
                'planning:empire-material-io',
                'planning:empire-material-io-csv',
                'ticker,material_name,production,consumption,delta,updated_at',
            ),
            (
                'planning:empire-plans-material-io',
                'planning:empire-plans-material-io-csv',
                'plan_uuid,plan_name,planet_natural_id,cogc,ticker,production,consumption,delta,updated_at',
            ),
        ],
    )
    def test_empire_without_state(self, api_client, user, empire_factory, json_route: str, csv_route: str, header: str):
        empire = empire_factory(user=user, empire_state={})

        response = self._get(api_client, user, json_route, empire)
        csv_response = self._get(api_client, user, csv_route, empire)

        assert response.status_code == 200
        assert response.data == {'updated_at': None, 'rows': []}
        assert csv_response.status_code == 200
        assert csv_response.content.decode() == f'{header}\r\n'

    def test_plan_list_csv_matches_plan_list(self, api_client, user, empire):
        plans = self._get(api_client, user, 'planning:empire-plan-list', empire).data

        response = self._get(api_client, user, 'planning:empire-plan-list-csv', empire)

        assert response.status_code == 200
        header, *lines = _csv_rows(response)
        assert header == [
            'plan_uuid',
            'plan_name',
            'planet_natural_id',
            'plan_cogc',
            'plan_permits_used',
            'plan_corphq',
            'modified_at',
        ]
        assert sorted(line[0] for line in lines) == sorted(p['uuid'] for p in plans)
        by_uuid = {p['uuid']: p for p in plans}
        for line in lines:
            assert line[1] == by_uuid[line[0]]['plan_name']

    @pytest.mark.usefixtures('locmem_cache')
    def test_state_sync_is_not_served_from_cache(self, api_client, user, empire, django_capture_on_commit_callbacks):
        url = reverse('planning:empire-plans-material-io', kwargs={'pk': empire.uuid})
        assert api_client.as_user(user).get(url)['X-Cache-Hit'] == '0'
        assert api_client.as_user(user).get(url)['X-Cache-Hit'] == '1'
        plan_uuid = next(iter(empire.plans.values_list('uuid', flat=True)))

        state = _state(
            {
                str(plan_uuid): {
                    'metadata': {'planet_natural_id': 'OT-580b', 'cogc': PlanningCOGCChoices.NONE},
                    'deltas': {'H2O': {'p': 7.0, 'c': 2.0, 'd': 5.0}},
                }
            }
        )
        sync_url = reverse('planning:empire-sync-state', kwargs={'pk': empire.uuid})
        with django_capture_on_commit_callbacks(execute=True):
            assert api_client.as_user(user).patch(sync_url, data=state, format='json').status_code == 200

        response = api_client.as_user(user).get(url)
        assert response['X-Cache-Hit'] == '0'
        assert [(r['ticker'], r['production'], r['delta']) for r in response.data['rows']] == [('H2O', 7.0, 5.0)]

    @pytest.mark.usefixtures('locmem_cache')
    def test_plan_rename_is_not_served_from_cache(self, api_client, user, empire, django_capture_on_commit_callbacks):
        url = reverse('planning:empire-plans-material-io', kwargs={'pk': empire.uuid})
        api_client.as_user(user).get(url)

        with django_capture_on_commit_callbacks(execute=True):
            plan = PlanningPlan.objects.get(plan_name='A base')
            plan.plan_name = 'Renamed'
            plan.save()

        response = api_client.as_user(user).get(url)
        assert 'Renamed' in {r['plan_name'] for r in response.data['rows']}

    def test_state_endpoints_query_count(self, api_client, user, empire, django_assert_num_queries):
        # empire key parts, deferred empire_state, material names
        with django_assert_num_queries(3):
            self._get(api_client, user, 'planning:empire-material-io', empire)
        # empire key parts, deferred empire_state, linked plan names
        with django_assert_num_queries(3):
            self._get(api_client, user, 'planning:empire-plans-material-io', empire)

    @pytest.mark.parametrize(
        'route',
        [
            'planning:empire-material-io',
            'planning:empire-material-io-csv',
            'planning:empire-plans-material-io',
            'planning:empire-plans-material-io-csv',
            'planning:empire-plan-list-csv',
        ],
    )
    def test_foreign_empire_is_404_with_api_key(self, api_client, user_factory, empire, route: str):
        _, key = UserAPIKey.objects.create_key(name='script', user=user_factory())

        response = api_client.get(reverse(route, kwargs={'pk': empire.uuid}), HTTP_AUTHORIZATION=f'Api-Key {key}')

        assert response.status_code == 404

    def test_sheets_request_with_query_key(self, api_client, user, empire):
        _, key = UserAPIKey.objects.create_key(name='sheet', user=user)

        response = api_client.get(
            reverse('planning:empire-material-io-csv', kwargs={'pk': empire.uuid}),
            {'api_key': key},
            HTTP_USER_AGENT='Mozilla/5.0 (compatible; GoogleDocs; apps-spreadsheets; +http://docs.google.com)',
            HTTP_ACCEPT_ENCODING='gzip',
        )

        assert response.status_code == 200
        assert response['Content-Encoding'] == 'gzip'
        header, *lines = list(csv.reader(io.StringIO(gzip.decompress(response.content).decode())))
        assert header[0] == 'ticker'
        assert [line[0] for line in lines] == ['DW', 'RAT']

    def test_requires_auth(self, api_client, empire):
        response = api_client.get(reverse('planning:empire-material-io-csv', kwargs={'pk': empire.uuid}))

        assert response.status_code == 401

    def test_delta_has_no_float_noise(self, api_client, user, empire_factory):
        state = _state()
        state['empire_total'] = {'DW': {'p': 95.5, 'c': 80.3, 'd': 15.2}}
        empire = empire_factory(user=user, empire_state=state)

        rows = self._get(api_client, user, 'planning:empire-material-io', empire).data['rows']
        csv_response = self._get(api_client, user, 'planning:empire-material-io-csv', empire)

        assert rows[0]['delta'] == 15.2
        assert _csv_rows(csv_response)[1][4] == '15.2'

    @pytest.mark.parametrize(
        'route',
        ['planning:empire-material-io-csv', 'planning:empire-plans-material-io-csv', 'planning:empire-plan-list-csv'],
    )
    def test_csv_routes_accept_text_csv(self, api_client, user, empire, route: str):
        response = self._get(api_client, user, route, empire, HTTP_ACCEPT='text/csv')

        assert response.status_code == 200
        assert response['Content-Type'] == 'text/csv; charset=utf-8'
