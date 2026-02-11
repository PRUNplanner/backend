from django.apps import AppConfig


class LegacyMigrationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'legacy_migration'
