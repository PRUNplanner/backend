from datetime import timedelta
from typing import Literal

import structlog
from django.db import transaction
from django.utils import timezone

from gamedata.gamedata_cache_manager import GamedataCacheManager

logger = structlog.get_logger(__name__)

type FioRefreshReason = Literal['login', 'credentials', 'token_refresh']
type FioRefreshSkip = Literal['no_credentials', 'failed', 'backoff', 'interval', 'lock']

# a token refresh asks for new FIO data at most this often
TOKEN_REFRESH_INTERVAL = timedelta(minutes=15)


def _skip(user_id: int, reason: FioRefreshReason) -> FioRefreshSkip | None:
    from user.models import User

    from gamedata.models import GameFIOPlayerData

    user = User.objects.only('prun_username', 'fio_apikey').filter(pk=user_id).first()
    if user is None or not user._has_fio_credentials():
        return 'no_credentials'

    row = (
        GameFIOPlayerData.objects.only(
            'automation_refresh_status',
            'automation_error_count',
            'automation_next_retry_at',
            'automation_last_refreshed_at',
        )
        .filter(user_id=user_id)
        .first()
    )
    # no row yet: never refreshed, only the lock can hold it back
    if row is not None:
        now = timezone.now()
        if row.is_permanently_failed or row.automation_refresh_status == 'failed':
            return 'failed'
        if row.automation_next_retry_at and row.automation_next_retry_at > now:
            return 'backoff'
        if reason == 'token_refresh' and row.automation_last_refreshed_at > now - TOKEN_REFRESH_INTERVAL:
            return 'interval'

    # a read only, the task itself takes the lock
    if GamedataCacheManager.has_fio_refresh_lock(user_id):
        return 'lock'

    return None


def request_fio_refresh(user_id: int, reason: FioRefreshReason) -> bool:
    """The one place that decides whether a user's FIO refresh is queued. True if it was."""
    from gamedata.tasks import gamedata_refresh_user_fiodata

    skip = _skip(user_id, reason)
    if skip:
        logger.info('fio_refresh_skipped', user_id=user_id, reason=reason, skip=skip)
        return False

    logger.info('fio_refresh_queued', user_id=user_id, reason=reason)
    transaction.on_commit(lambda: gamedata_refresh_user_fiodata.delay(user_id))
    return True
