import pytest
from django.db import connection
from model_bakery import baker
from user.models import User

pytestmark = pytest.mark.django_db


@pytest.mark.skipif(connection.vendor != 'postgresql', reason='Postgres only')
@pytest.mark.parametrize(
    ('lookup', 'index'),
    [('email__iexact', 'user_email_upper_idx'), ('username__iexact', 'user_username_upper_idx')],
)
def test_iexact_lookups_use_the_upper_index(lookup: str, index: str) -> None:
    baker.make('user.User', _quantity=3)
    with connection.cursor() as cursor:
        # a few rows: the planner would scan anyway, so make the scan cost what it does on a full table
        cursor.execute('SET LOCAL enable_seqscan = off')
        plan = User.objects.filter(**{lookup: 'Pilot@Example.com'}).explain()

    assert index in plan
