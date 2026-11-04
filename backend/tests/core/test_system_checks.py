import pytest
from django.core.management import call_command


def test_system_checks_need_no_database() -> None:
    # no django_db mark: a check that queries fails here, as it would before migrate on a fresh database
    call_command('check')


@pytest.mark.django_db
def test_models_need_no_new_migration() -> None:
    # exits 1 when a model change has no migration (it reads the migration history, hence the db mark)
    call_command('makemigrations', '--check', '--dry-run', verbosity=0)
