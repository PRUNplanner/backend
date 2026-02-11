from typing import Any, Literal


class LegacyRouter:
    """
    Routes database operations for models that are part of the legacy DB.
    Ensures all reads go to the legacy DB and prevents writes.
    """

    legacy_app_labels = {'legacy_migration'}

    def db_for_read(self, model: Any, **hints: Any) -> Literal['legacy'] | None:
        if model._meta.app_label in self.legacy_app_labels:
            return 'legacy'
        return None

    def db_for_write(self, model: Any, **hints: Any) -> Literal['default'] | None:
        if model._meta.app_label in self.legacy_app_labels:
            # Prevent writes to legacy DB
            return None
        return 'default'

    def allow_relation(self, obj1: Any, obj2: Any, **hints: Any) -> bool | None:
        db_list = {'default', 'legacy'}
        if obj1._state.db in db_list and obj2._state.db in db_list:
            return True
        return None

    def allow_migrate(
        self, db: Any, app_label: str, model_name: str | None = None, **hints: Any
    ) -> Literal['legacy', 'default']:
        # Don't allow migrations on the legacy DB
        if app_label in self.legacy_app_labels:
            return db == 'legacy'
        return db == 'default'
