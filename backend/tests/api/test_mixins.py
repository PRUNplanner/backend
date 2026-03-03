from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from api.mixins import JSONSafeSerializerMixin
from rest_framework import serializers


class DummySafeSerializer(JSONSafeSerializerMixin, serializers.Serializer):
    id = serializers.UUIDField()
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    created_at = serializers.DateTimeField()


class BaseToMock:
    def validate(self, attrs):
        return attrs


class MockedSafeSerializer(JSONSafeSerializerMixin, BaseToMock):
    pass


def test_json_safe_serializer_mixin_conversion():
    raw_uuid = uuid4()
    raw_date = '2024-01-01T12:00:00Z'
    data = {'id': str(raw_uuid), 'price': '19.99', 'created_at': raw_date}

    serializer = DummySafeSerializer(data=data)
    assert serializer.is_valid()

    safe_data = serializer.validated_data

    assert isinstance(safe_data['id'], str)
    assert isinstance(safe_data['price'], (str, float))
    assert float(safe_data['price']) == 19.99
    assert isinstance(safe_data['created_at'], str)


def test_json_safe_serializer_handles_nested_data():

    mixin_instance = MockedSafeSerializer()

    data = {'metadata': {'score': Decimal('10.5'), 'timestamp': datetime(2024, 1, 1)}}

    result = mixin_instance.validate(data)

    assert not isinstance(result['metadata']['score'], Decimal)
    assert isinstance(result['metadata']['score'], (float, str))
    assert isinstance(result['metadata']['timestamp'], str)
