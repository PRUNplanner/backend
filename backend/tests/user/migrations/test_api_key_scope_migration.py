import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from model_bakery import baker
from user.models import UserAPIKey


def migrate(targets: list[tuple[str, str]]) -> None:
    MigrationExecutor(connection).migrate(targets)


@pytest.mark.django_db(transaction=True)
def test_keys_from_before_are_read_write_and_new_ones_read() -> None:
    latest = MigrationExecutor(connection).loader.graph.leaf_nodes('user')
    user = baker.make('user.User')
    try:
        migrate([('user', '0018_user_email_username_upper_idx')])
        # the model at 0018 has no scope column: insert through the historical model
        old = MigrationExecutor(connection).loader.project_state(('user', '0018_user_email_username_upper_idx'))
        OldKey = old.apps.get_model('user', 'UserAPIKey')  # noqa: N806
        OldKey.objects.create(id='old.key', prefix='old', hashed_key='x', name='sheet', user_id=user.pk)

        migrate(latest)

        assert UserAPIKey.objects.get(pk='old.key').scope == 'read_write'
        assert UserAPIKey.objects.create_key(name='new', user=user)[0].scope == 'read'
    finally:
        migrate(latest)
