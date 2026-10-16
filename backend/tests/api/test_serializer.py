import pytest
from api.serializer import PydanticJSONField
from pydantic import BaseModel
from rest_framework import serializers


class _Model(BaseModel):
    amount: int


def test_pydantic_json_field_errors_name_the_path_not_the_value():
    field = PydanticJSONField(pydantic_model=_Model)

    with pytest.raises(serializers.ValidationError) as exc_info:
        field.to_internal_value({'amount': 'secret-value'})

    messages = [str(m) for m in exc_info.value.detail]
    assert len(messages) == 1
    assert messages[0].startswith('amount: ')
    assert 'secret-value' not in messages[0]
