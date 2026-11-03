from datetime import timedelta

import pytest
from django.utils import timezone
from gamedata.models import GamePlanet
from model_bakery import baker


@pytest.mark.django_db
class TestUpdateRefreshResult:
    @pytest.mark.parametrize(
        'since_success, delay',
        [
            (timedelta(minutes=1), timedelta(minutes=GamePlanet.RETRY_DELAY_MINUTES)),
            (timedelta(minutes=40), timedelta(minutes=40)),
            (timedelta(days=3), GamePlanet.TRANSIENT_RETRY_CAP),
        ],
        ids=['floor', 'outage', 'cap'],
    )
    def test_a_transient_error_backs_off_without_counting(self, since_success: timedelta, delay: timedelta) -> None:
        planet = baker.make(GamePlanet, automation_last_refreshed_at=timezone.now() - since_success)

        for _ in range(GamePlanet.MAX_RETRIES + 1):
            planet.update_refresh_result(error=TimeoutError('slow'), transient=True)

        planet.refresh_from_db()
        assert (planet.automation_refresh_status, planet.automation_error_count) == ('retrying', 0)
        assert planet.automation_error == 'slow'
        assert abs(planet.automation_next_retry_at - timezone.now() - delay) < timedelta(seconds=5)

    def test_other_errors_still_fail_after_max_retries(self) -> None:
        planet = baker.make(GamePlanet)

        for _ in range(GamePlanet.MAX_RETRIES):
            planet.update_refresh_result(error=ValueError('bad'))

        planet.refresh_from_db()
        assert (planet.automation_refresh_status, planet.automation_error_count) == ('failed', GamePlanet.MAX_RETRIES)

    def test_success_after_transient_errors_resets(self) -> None:
        planet = baker.make(GamePlanet)
        planet.update_refresh_result(error=TimeoutError('slow'), transient=True)
        planet.update_refresh_result()

        planet.refresh_from_db()
        assert (planet.automation_refresh_status, planet.automation_next_retry_at) == ('ok', None)
