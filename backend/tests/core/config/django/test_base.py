import pytest
from django.test import Client
from django.urls import reverse
from model_bakery import baker

pytestmark = pytest.mark.django_db


class TestResponseCompression:
    def test_large_json_responses_are_gzipped(self, client: Client) -> None:
        baker.make('gamedata.GamePlanet', _quantity=5, make_m2m=True)

        response = client.get(reverse('data:planet-list'), HTTP_ACCEPT_ENCODING='gzip')

        assert response.status_code == 200
        assert response.get('Content-Encoding') == 'gzip'
