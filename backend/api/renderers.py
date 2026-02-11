import decimal
import uuid

import orjson
from rest_framework.renderers import JSONRenderer


class OrjsonRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b''

        def default(obj):
            if isinstance(obj, decimal.Decimal):
                return float(obj)
            if isinstance(obj, uuid.UUID):
                return str(obj)
            raise TypeError(f'Object of type {obj.__class__.__name__} is not JSON serializable')

        return orjson.dumps(
            data, default=default, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_UTC_Z | orjson.OPT_SERIALIZE_UUID
        )
