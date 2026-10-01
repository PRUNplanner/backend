from pydantic import BaseModel
from rest_framework import serializers


class PydanticJSONField(serializers.JSONField):
    def __init__(self, pydantic_model: type[BaseModel], **kwargs):
        self.pydantic_model = pydantic_model
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        # raw JSON to Pydantic Model
        data = super().to_internal_value(data)

        try:
            model_instance = self.pydantic_model.model_validate(data)
            return model_instance.model_dump()
        except Exception as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def to_representation(self, value):
        return value


class ClientErrorSerializer(serializers.Serializer):
    """An error report from the browser; every field bounded, since each becomes an Axiom column."""

    kind = serializers.ChoiceField(choices=['validation', 'server', 'network', 'client'])
    method = serializers.ChoiceField(choices=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
    path_template = serializers.RegexField(r'^/', max_length=200)
    status = serializers.IntegerField(min_value=100, max_value=599, allow_null=True, required=False)
    failed_request_id = serializers.UUIDField(allow_null=True, required=False)
    issues = serializers.ListField(child=serializers.CharField(max_length=200), max_length=20, required=False)
    client_ms = serializers.IntegerField(min_value=0, allow_null=True, required=False)
    release = serializers.CharField(max_length=40)
    route_name = serializers.CharField(max_length=80, allow_null=True, required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        # a plain Serializer ignores unknown keys; reject them so nothing unbounded reaches the log
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: 'Unknown field.' for key in sorted(unknown)})
        return attrs
