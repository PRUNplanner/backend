from datetime import datetime

from django.db import models, transaction
from rest_framework import serializers, status
from rest_framework.exceptions import APIException


class SaveConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = 'Changed since it was loaded.'
    default_code = 'conflict'

    def __init__(self, modified_at: datetime) -> None:
        super().__init__(
            {
                'detail': self.default_detail,
                'code': self.default_code,
                'modified_at': serializers.DateTimeField().to_representation(modified_at),
            }
        )


class PlanningSaveConflictSerializer(serializers.Serializer):
    detail = serializers.CharField()
    code = serializers.CharField()
    modified_at = serializers.DateTimeField()


class SaveConflictMixin(serializers.Serializer):
    # rejects an update with 409 when base_modified_at (the version the client edited) is no longer the stored one

    base_modified_at = serializers.DateTimeField(write_only=True, required=False, allow_null=True)

    # model field holding the version exposed as modified_at
    version_field = 'modified_at'

    def save(self, **kwargs: object) -> models.Model:
        # save(), not update(): the subclasses' own create()/update() don't call super()
        base: datetime | None = self.validated_data.pop('base_modified_at', None)
        instance = self.instance
        if instance is None or base is None:
            return super().save(**kwargs)

        with transaction.atomic():
            # the row lock makes two saves from the same base end in one success and one conflict
            current: datetime = (
                type(instance)
                .objects.select_for_update()
                .values_list(self.version_field, flat=True)
                .get(pk=instance.pk)
            )
            if current != base:
                raise SaveConflict(current)
            return super().save(**kwargs)
