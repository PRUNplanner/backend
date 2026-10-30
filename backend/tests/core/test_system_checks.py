from django.core.management import call_command


def test_system_checks_need_no_database() -> None:
    # no django_db mark: a check that queries fails here, as it would before migrate on a fresh database
    call_command('check')
