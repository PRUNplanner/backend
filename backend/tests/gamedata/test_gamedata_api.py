import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_list_recipes(api_client, recipe_factory):
    recipe_factory(_quantity=3)

    response = api_client.get(reverse('data:recipe-list'))

    assert response.status_code == 200
    assert len(response.data) == 3


@pytest.mark.django_db
def test_list_materials(api_client, material_factory):
    material_factory(_quantity=3)

    response = api_client.get(reverse('data:material-list'))

    assert response.status_code == 200
    assert len(response.data) == 3


@pytest.mark.django_db
def test_list_buildings(api_client, building_factory):
    building_factory(_quantity=3)

    response = api_client.get(reverse('data:building-list'))

    assert response.status_code == 200
    assert len(response.data) == 3


@pytest.mark.django_db
class TestGamePlanetViewSet:
    def test_list(self, api_client, planet_factory):
        planet_factory(_quantity=3)

        response = api_client.get(reverse('data:planet-list'))

        assert response.status_code == 200
        assert len(response.data) == 3

    def test_retrieve(self, api_client, planet_factory):
        planet_natural_id = 'OT-580b'
        planet_factory(planet_natural_id=planet_natural_id)

        url = reverse('data:planet-detail', kwargs={'planet_natural_id': planet_natural_id})
        response = api_client.get(url)

        assert response.status_code == 200
        assert response.data['planet_natural_id'] == planet_natural_id

    def test_multiple(self, api_client, planet_factory):
        planet_natural_ids = ['OT-580b', 'ZV-759b', 'EW-688c']

        for pid in planet_natural_ids:
            planet_factory(planet_natural_id=pid)
        response = api_client.post(reverse('data:planet-multiple'), data=planet_natural_ids, format='json')

        assert response.status_code == 200
        assert len(response.data) == 3
        assert response.data[0]['planet_natural_id'] in planet_natural_ids
        assert response.data[1]['planet_natural_id'] in planet_natural_ids
        assert response.data[2]['planet_natural_id'] in planet_natural_ids
