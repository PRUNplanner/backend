import importlib

import pytest
from django.apps import apps
from django.db import IntegrityError, connection, transaction
from model_bakery import baker
from user.models import User

pytestmark = pytest.mark.django_db

migration = importlib.import_module('user.migrations.0015_clear_migrated_emails')


def _has_ci_index() -> bool:
    with connection.cursor() as cursor:
        return migration.EMAIL_CI_INDEX in connection.introspection.get_constraints(cursor, User._meta.db_table)


def _user(email: str, verified: bool) -> User:
    user: User = baker.make('user.User')
    # update(): no signal, so no code
    User.objects.filter(pk=user.pk).update(email=email, is_email_verified=verified)
    return user


@pytest.mark.parametrize(
    ('email', 'verified', 'expected'),
    [
        ('migrated_123_a@b.c', False, None),
        ('migrated_123_a@b.c', True, 'migrated_123_a@b.c'),
        ('pilot@example.com', False, 'pilot@example.com'),
    ],
    ids=['migrated-unconfirmed', 'migrated-confirmed', 'normal'],
)
def test_clears_unconfirmed_migrated_emails(email: str, verified: bool, expected: str | None) -> None:
    user = _user(email, verified)

    migration.clear_migrated_emails(apps, connection.schema_editor())

    user.refresh_from_db()
    assert (user.email, user.is_email_verified) == (expected, verified)


def test_adds_the_case_insensitive_index_without_duplicates() -> None:
    # the test database ran the migration already; drop the index to see it come back
    migration.drop_email_ci_index(apps, connection.schema_editor())
    assert not _has_ci_index()
    _user('foo@x.com', verified=True)

    migration.clear_migrated_emails(apps, connection.schema_editor())

    assert _has_ci_index()
    with pytest.raises(IntegrityError), transaction.atomic():
        _user('FOO@x.com', verified=False)


def test_skips_the_index_with_case_duplicates(caplog: pytest.LogCaptureFixture) -> None:
    migration.drop_email_ci_index(apps, connection.schema_editor())
    _user('foo@x.com', verified=True)
    _user('Foo@x.com', verified=False)

    migration.clear_migrated_emails(apps, connection.schema_editor())

    assert not _has_ci_index()
    logged = [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg['event'] == 'email_case_duplicates']
    assert logged and logged[0]['count'] == 1
