from drf_spectacular.extensions import OpenApiSerializerFieldExtension
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.plumbing import ResolvedComponent
from pydantic import BaseModel, ValidationError
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema
from rest_framework import serializers


class PydanticJSONField(serializers.JSONField):
    def __init__(self, pydantic_model: type[BaseModel], exclude_unset: bool = False, **kwargs):
        self.pydantic_model = pydantic_model
        # True: keys the payload left out stay out, instead of stored as their defaults
        self.exclude_unset = exclude_unset
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        # raw JSON to Pydantic Model
        data = super().to_internal_value(data)

        try:
            model_instance = self.pydantic_model.model_validate(data)
            return model_instance.model_dump(exclude_unset=self.exclude_unset)
        except ValidationError as exc:
            # paths and messages only: str(exc) echoes the submitted values back
            raise serializers.ValidationError(
                [f'{".".join(map(str, e["loc"])) or "root"}: {e["msg"]}' for e in exc.errors(include_url=False)]
            ) from exc

    def to_representation(self, value):
        return value


class _OpenAPI30JsonSchema(GenerateJsonSchema):
    """Pydantic writes JSON Schema 2020-12; OpenAPI 3.0 has no null type and no const."""

    def nullable_schema(self, schema: core_schema.NullableSchema) -> JsonSchemaValue:
        inner = self.generate_inner(schema['schema'])
        # a $ref takes no siblings in 3.0
        return {'allOf': [inner], 'nullable': True} if '$ref' in inner else {**inner, 'nullable': True}

    def dict_schema(self, schema: core_schema.DictSchema) -> JsonSchemaValue:
        result = super().dict_schema(schema)
        # the key pattern can't be expressed in 3.0; the values can
        patterns = result.pop('patternProperties', None)
        if patterns:
            result['additionalProperties'] = next(iter(patterns.values()))
        return result

    def literal_schema(self, schema: core_schema.LiteralSchema) -> JsonSchemaValue:
        result = super().literal_schema(schema)
        if 'const' in result:
            result['enum'] = [result.pop('const')]
        return result


class PydanticJSONFieldExtension(OpenApiSerializerFieldExtension):
    """Documents a PydanticJSONField with its model's JSON schema, as a component, instead of any value."""

    target_class = PydanticJSONField

    def map_serializer_field(self, auto_schema: AutoSchema, direction: str) -> dict[str, str]:
        model: type[BaseModel] = self.target.pydantic_model
        schema = model.model_json_schema(
            ref_template='#/components/schemas/{model}', schema_generator=_OpenAPI30JsonSchema
        )
        for name, sub_schema in schema.pop('$defs', {}).items():
            auto_schema.registry.register_on_missing(
                ResolvedComponent(name=name, type=ResolvedComponent.SCHEMA, object=name, schema=sub_schema)
            )
        component = ResolvedComponent(
            name=model.__name__, type=ResolvedComponent.SCHEMA, object=model.__name__, schema=schema
        )
        auto_schema.registry.register_on_missing(component)
        return component.ref


class ClientErrorSerializer(serializers.Serializer):
    """An error report from the browser; every field bounded, since each becomes an Axiom column."""

    kind = serializers.ChoiceField(choices=['validation', 'server', 'network', 'client', 'query_loop'])
    # required for every kind but query_loop (validate), which is about a query, not a call
    method = serializers.ChoiceField(choices=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'], required=False)
    path_template = serializers.RegexField(r'^/', max_length=200, required=False)
    status = serializers.IntegerField(min_value=100, max_value=599, allow_null=True, required=False)
    failed_request_id = serializers.UUIDField(allow_null=True, required=False)
    issues = serializers.ListField(child=serializers.CharField(max_length=200), max_length=20, required=False)
    client_ms = serializers.IntegerField(min_value=0, allow_null=True, required=False)
    release = serializers.CharField(max_length=40)
    route_name = serializers.CharField(max_length=80, allow_null=True, required=False)
    # query_loop: the looping query's definition name and its fetches in the budget window
    query = serializers.CharField(max_length=80, required=False)
    fetches = serializers.IntegerField(min_value=0, max_value=1000, required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        # a plain Serializer ignores unknown keys; reject them so nothing unbounded reaches the log
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: 'Unknown field.' for key in sorted(unknown)})
        if attrs['kind'] != 'query_loop':
            missing = [key for key in ('method', 'path_template') if key not in attrs]
            if missing:
                raise serializers.ValidationError({key: 'This field is required.' for key in missing})
        return attrs
