import structlog
from structlog.typing import FilteringBoundLogger

from gamedata.fio.schemas.fio_webhook import FIOWebhookRootSchema
from gamedata.services.fio_webhook_handlers import BaseFIOWebhookHandler, FIOCXWebhookHandler

logger: FilteringBoundLogger = structlog.get_logger(__name__)


class FIOWebhookDispatcher:
    """
    Orchestrates the routing and lifecycle of incoming FIO Webhook messages.

    This dispatcher decouples the high-level Celery task from specific business
    logic. It maps FIO 'Endpoints' to dedicated Handler classes.

    LIFECYCLE & ISOLATION:
    1. The registry stores HANDLER CLASSES (Type[BaseFIOWebhookHandler]), not instances.
    2. Each message in a batch triggers a fresh instantiation (e.g., `handler_class()`).
    3. This ensures strict context isolation: state or log-bindings from one
    message cannot "leak" into the processing of the next.

    REGISTERED PROCESSORS:
    - '/cx': FIOCXWebhookHandler
        Handles Commodity Exchange (CX) updates. Performs ticker-based deduplication,
        database synchronization via bulk updates, and enriches data for downstream
        consumption in Redis Streams.

    EXTENSION:
    To support new FIO endpoints, implement
    a new handler inheriting from BaseFIOWebhookHandler and add it to `_registry`.
    """

    _registry: dict[str, type[BaseFIOWebhookHandler]] = {
        '/cx': FIOCXWebhookHandler,
    }

    @classmethod
    def dispatch(cls, validated_data: FIOWebhookRootSchema):
        for msg in validated_data.Data:
            handler_class = cls._registry.get(msg.Endpoint)

            if handler_class:
                handler = handler_class()
                handler.process(msg.Data)
            else:
                logger.warning('unknown_fio_endpoint_received', endpoint=msg.Endpoint)
