from typing import Any

from rest_framework.renderers import JSONRenderer
from rest_framework.utils import json as drf_json


class JSONSafeSerializerMixin:
    """
    Mixin to convert complex Python objects into JSON-primitive types.
    """

    def validate(self, attrs: Any) -> Any:
        data = super().validate(attrs)  # type: ignore

        encoder_class = JSONRenderer.encoder_class
        string_version = drf_json.dumps(data, cls=encoder_class)
        return drf_json.loads(string_version)
