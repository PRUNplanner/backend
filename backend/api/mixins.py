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


class BlankAsNullMixin:
    """
    Mixin to serialize '' as None for nullable fields that may still hold a blank value.
    """

    blank_as_null_fields: tuple[str, ...] = ()

    def to_representation(self, instance: object) -> dict[str, object]:
        data = super().to_representation(instance)  # type: ignore
        for field in self.blank_as_null_fields:
            if data.get(field) == '':
                data[field] = None
        return data
