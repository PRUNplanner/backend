from unittest.mock import patch

import pytest
from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from model_bakery import baker
from user.admin import UserAdmin
from user.models import User, VerificationCode
from user.models.verification_codes import VerificationeCodeChoices

CHANGELIST = reverse('admin:user_user_changelist')
SECRET = 'SECRET-fio-key-9876'


def change_url(user: User) -> str:
    return reverse('admin:user_user_change', args=[user.pk])


def listed(admin_client: Client, query: dict[str, str]) -> set[int]:
    response = admin_client.get(CHANGELIST, query)
    assert response.status_code == 200
    return {user.pk for user in response.context['cl'].result_list}


@pytest.mark.django_db
class TestSecrets:
    """AC3: no stored FIO key on the user page, no verification code anywhere in the admin."""

    def test_user_page_masks_the_fio_key(self, admin_client: Client) -> None:
        user = baker.make(User, prun_username='Somebody', fio_apikey=SECRET)

        html = admin_client.get(change_url(user)).content.decode()

        assert SECRET not in html
        assert '••••9876' in html
        assert 'name="fio_apikey"' not in html

    def test_verification_code_is_never_shown(self, admin_client: Client) -> None:
        code = baker.make(
            VerificationCode, user=baker.make(User), code='QZX7K2PA', purpose=VerificationeCodeChoices.PASSWORD_RESET
        )
        pages = [
            reverse('admin:user_verificationcode_changelist'),
            reverse('admin:user_verificationcode_change', args=[code.pk]),
        ]

        assert 'QZX7K2PA' not in str(code)
        for page in pages:
            response = admin_client.get(page)
            assert response.status_code == 200
            assert 'QZX7K2PA' not in response.content.decode()

    def test_verification_code_search_works(self, admin_client: Client) -> None:
        """A1: searching used to 500 on the FK."""
        baker.make(VerificationCode, user=baker.make(User, username='findme'))

        response = admin_client.get(reverse('admin:user_verificationcode_changelist'), {'q': 'findme'})

        assert response.status_code == 200
        assert len(response.context['cl'].result_list) == 1


@pytest.mark.django_db
class TestUserList:
    """AC11: filters and counts are right, and the query count doesn't grow with the rows."""

    def test_filters(self, admin_client: Client, superuser: User) -> None:
        fio = baker.make(User, prun_username='a', fio_apikey='k')
        half = baker.make(User, prun_username='b', fio_apikey='')
        verified = baker.make(User, is_email_verified=True)
        inactive = baker.make(User, is_active=False)

        assert listed(admin_client, {'fio': 'yes'}) == {fio.pk}
        assert fio.pk not in listed(admin_client, {'fio': 'no'}) and half.pk in listed(admin_client, {'fio': 'no'})
        assert listed(admin_client, {'is_email_verified__exact': '1'}) == {verified.pk}
        assert listed(admin_client, {'is_active__exact': '0'}) == {inactive.pk}
        assert listed(admin_client, {'is_staff__exact': '1'}) == {superuser.pk}

    def test_plan_and_empire_counts(self, admin_client: Client) -> None:
        user = baker.make(User)
        baker.make('planning.PlanningPlan', user=user, plan_permits_used=1, _quantity=3)
        baker.make('planning.PlanningEmpire', user=user, empire_permits_used=1, empire_permits_total=2, _quantity=2)

        response = admin_client.get(CHANGELIST, {'q': user.username})
        (row,) = response.context['cl'].result_list

        assert (row.plan_total, row.empire_total) == (3, 2)

    def test_query_count_is_constant(self, admin_client: Client) -> None:
        def make_users(count: int) -> None:
            for _ in range(count):
                user = baker.make(User)
                baker.make('planning.PlanningPlan', user=user, plan_permits_used=1, _quantity=2)
                baker.make('planning.PlanningEmpire', user=user, empire_permits_used=1, empire_permits_total=2)

        def count_queries() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert admin_client.get(CHANGELIST).status_code == 200
            return len(queries)

        make_users(2)
        few = count_queries()
        make_users(6)

        assert count_queries() == few

    def test_changelist_selects_no_json(self, admin_client: Client) -> None:
        user = baker.make(User)
        baker.make('planning.PlanningPlan', user=user, plan_permits_used=1)

        with CaptureQueriesContext(connection) as queries:
            admin_client.get(CHANGELIST)

        assert not any('"plan_data"' in q['sql'] or '"empire_state"' in q['sql'] for q in queries)


@pytest.mark.django_db
class TestDateJoined:
    """AC12"""

    def test_new_users_get_a_join_date(self) -> None:
        assert baker.make(User).date_joined is not None

    def test_existing_rows_show_a_dash(self, admin_client: Client) -> None:
        old = baker.make(User, username='old-timer', date_joined=None)

        header = admin_client.get(change_url(old)).context['adminform'].model_admin.get_header(None, old)

        assert {'label': 'Joined', 'value': '—'} in header
        assert '—' in admin_client.get(CHANGELIST, {'q': 'old-timer'}).content.decode()


@pytest.mark.django_db
class TestUserActions:
    """Each action writes exactly one LogEntry."""

    def _run(self, admin_client: Client, action: str, users: list[User]) -> None:
        admin_client.post(CHANGELIST, {'action': action, '_selected_action': [u.pk for u in users]})

    def test_clear_fio(self, admin_client: Client) -> None:
        users = [
            baker.make(User, prun_username='a', fio_apikey='k1'),
            baker.make(User, prun_username='b', fio_apikey='k2'),
        ]

        with patch('gamedata.tasks.gamedata_clean_user_fiodata.delay'):
            self._run(admin_client, 'action_clear_fio', users)

        assert User.objects.filter(pk__in=[u.pk for u in users], fio_apikey__isnull=True).count() == 2
        assert User.objects.get(pk=users[0].pk).prun_username == 'a'
        assert LogEntry.objects.count() == 1

    def test_clear_fio_detail_needs_a_post(self, admin_client: Client) -> None:
        user = baker.make(User, prun_username='a', fio_apikey='k1')
        url = f'{CHANGELIST}{user.pk}/detail-clear-fio/'

        confirm = admin_client.get(url)
        user.refresh_from_db()
        assert confirm.status_code == 200 and user.fio_apikey == 'k1'
        assert LogEntry.objects.count() == 0

        with patch('gamedata.tasks.gamedata_clean_user_fiodata.delay'):
            assert admin_client.post(url).status_code == 302
        user.refresh_from_db()
        assert user.fio_apikey is None
        assert LogEntry.objects.get().object_id == str(user.pk)

    def test_deactivate_skips_yourself(self, admin_client: Client, superuser: User) -> None:
        other = baker.make(User)

        self._run(admin_client, 'action_deactivate', [other, superuser])

        other.refresh_from_db()
        superuser.refresh_from_db()
        assert (other.is_active, superuser.is_active) == (False, True)
        assert LogEntry.objects.count() == 1

    def test_reactivate(self, admin_client: Client) -> None:
        other = baker.make(User, is_active=False)

        self._run(admin_client, 'action_reactivate', [other])

        other.refresh_from_db()
        assert other.is_active
        assert LogEntry.objects.count() == 1

    def test_refresh_fio_is_queued(self, admin_client: Client) -> None:
        linked, unlinked = baker.make(User, prun_username='a', fio_apikey='k'), baker.make(User)

        with patch('gamedata.tasks.gamedata_refresh_user_fiodata.delay') as delay:
            self._run(admin_client, 'action_refresh_fio', [linked, unlinked])

        delay.assert_called_once_with(linked.pk)
        assert LogEntry.objects.count() == 1

    def test_resend_verification(self, admin_client: Client) -> None:
        # creating a user with an email also sends a code (user signal): patched for the whole test, so no broker call
        with patch('user.services.verification_service.VerificationService.create_and_send_code') as send:
            unverified = baker.make(User, email='a@example.com', is_email_verified=False)
            verified = baker.make(User, email='b@example.com', is_email_verified=True)
            send.reset_mock()

            self._run(admin_client, 'action_resend_verification', [unverified, verified])

        send.assert_called_once_with(unverified, VerificationeCodeChoices.EMAIL_VERIFICATION)
        assert LogEntry.objects.count() == 1


@pytest.mark.django_db
class TestUserPages:
    def test_header_and_strip(self, admin_client: Client) -> None:
        user = baker.make(User, prun_username='a', fio_apikey='k')
        baker.make('planning.PlanningPlan', user=user, plan_permits_used=1, _quantity=2)
        baker.make(
            'gamedata.GameFIOPlayerData', user=user, automation_refresh_status='failed', automation_error='bad key'
        )

        page = admin_client.get(change_url(user)).content.decode()
        summary = admin_client.get(CHANGELIST).context['cl'].model_admin.get_summary(None)

        assert 'bad key' in page
        # the status the user's profile shows: no error count and no FIO answer yet reads as syncing
        facts = UserAdmin(User, admin.site).get_header(None, user)  # ty: ignore[invalid-argument-type]
        assert {'label': 'FIO status (user sees)', 'value': 'syncing', 'badge': 'info'} in facts
        assert 'Last plan edit' in page
        assert f'user__id__exact={user.pk}' in page
        assert {tile['label'] for tile in summary['tiles']} == {
            'Users',
            'Active in 30 d',
            'FIO linked',
            'Email verified',
        }
        assert summary['charts'][0]['title'] == 'Signups per day, last 90 days'
