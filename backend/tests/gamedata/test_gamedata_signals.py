from collections.abc import Callable

import pytest
from core.services.cache_manager import CacheManager
from gamedata.gamedata_cache_manager import PLANET, STORAGE
from gamedata.models import GameFIOPlayerData, GamePlanet

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('locmem_cache')]


def _save_lease(planet: GamePlanet) -> None:
    planet.automation_refresh_status = 'pending'
    planet.save(update_fields=['automation_refresh_status', 'automation_next_retry_at'])


@pytest.mark.parametrize(
    ('act', 'invalidates'),
    [
        (lambda p: p.update_refresh_result(), False),
        (lambda p: p.update_refresh_result(error=Exception('boom')), False),
        (_save_lease, False),
        (lambda p: p.save(), True),
        (lambda p: p.save(update_fields=['gravity']), True),
        (lambda p: p.save(update_fields=['gravity', 'automation_error']), True),
        (lambda p: p.delete(), True),
    ],
    ids=['refresh-ok', 'refresh-error', 'pending-lease', 'full-save', 'data-field', 'mixed-fields', 'delete'],
)
def test_planet_cache_is_invalidated_unless_only_automation_fields_were_saved(
    planet_factory, django_capture_on_commit_callbacks, act: Callable[[GamePlanet], object], invalidates: bool
):
    planet = planet_factory(planet_natural_id='OT-580b')
    before = CacheManager.key(PLANET, 'retrieve', scope='OT-580b')

    with django_capture_on_commit_callbacks(execute=True):
        act(planet)

    assert (CacheManager.key(PLANET, 'retrieve', scope='OT-580b') != before) is invalidates


@pytest.mark.parametrize(
    ('act', 'invalidates'),
    [
        (lambda d: d.update_refresh_result(), False),
        (lambda d: d.update_refresh_result(error=Exception('boom')), False),
        (lambda d: d.save(), True),
        (lambda d: d.save(update_fields=['storage_data', 'automation_last_refreshed_at']), True),
        (lambda d: d.delete(), True),
    ],
    ids=['refresh-ok', 'refresh-error', 'full-save', 'mixed-fields', 'delete'],
)
def test_storage_cache_is_invalidated_unless_only_automation_fields_were_saved(
    fio_playerdata_factory,
    django_capture_on_commit_callbacks,
    act: Callable[[GameFIOPlayerData], object],
    invalidates: bool,
):
    data: GameFIOPlayerData = fio_playerdata_factory()
    user_id: int = data.user_id
    before = CacheManager.key(STORAGE, 'retrieve', scope=user_id)

    with django_capture_on_commit_callbacks(execute=True):
        act(data)

    assert (CacheManager.key(STORAGE, 'retrieve', scope=user_id) != before) is invalidates
