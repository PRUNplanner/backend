import structlog
from core.admin import changelist_url, log_admin_action
from django.contrib import messages
from django.db.models import Model
from django.http import HttpRequest, HttpResponseRedirect

from gamedata.tasks import AdminImportKind, gamedata_admin_import

logger = structlog.get_logger(__name__)


def queue_fio_import(request: HttpRequest, model: type[Model], kind: AdminImportKind) -> HttpResponseRedirect:
    """List action body for "Import from FIO": enqueue, audit, and tell the operator it runs in the worker."""
    try:
        gamedata_admin_import.delay(kind)
        log_admin_action(request, model, f'Queued FIO import: {kind}')
        messages.success(request, f'Queued FIO import of {kind}. Counts land in the worker log.')
    except Exception:
        logger.exception('admin_fio_import_queue_failed', kind=kind)
        messages.error(request, f'Could not queue the FIO import of {kind}.')
    return HttpResponseRedirect(changelist_url(model))
