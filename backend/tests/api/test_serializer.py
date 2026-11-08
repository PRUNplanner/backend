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


class _Optional(BaseModel):
    amount: int
    note: str | None = None


@pytest.mark.parametrize(('exclude_unset', 'stored'), [(False, {'amount': 1, 'note': None}), (True, {'amount': 1})])
def test_pydantic_json_field_exclude_unset_keeps_left_out_keys_out(exclude_unset: bool, stored: dict):
    field = PydanticJSONField(pydantic_model=_Optional, exclude_unset=exclude_unset)

    assert field.to_internal_value({'amount': 1}) == stored
