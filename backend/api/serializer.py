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
