import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from model_bakery import baker
from user.models import User

pytestmark = pytest.mark.django_db


class TestUserPreSaveCost:
    @pytest.mark.xfail(strict=True, reason='audit: pre_save loads the previous user row twice on every save')
    def test_save_reads_previous_row_at_most_once(self) -> None:
        user: User = baker.make('user.User')

        with CaptureQueriesContext(connection) as ctx:
            user.save(update_fields=['last_login'])

        selects = [q for q in ctx.captured_queries if q['sql'].lstrip().upper().startswith('SELECT')]
        assert len(selects) <= 1

    def test_email_change_still_resets_verification(self) -> None:
        user: User = baker.make('user.User', email='old@example.com', is_email_verified=True)

        user.email = 'new@example.com'
        user.save()

        user.refresh_from_db()
        assert user.is_email_verified is False
