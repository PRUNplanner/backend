from django.apps import AppConfig


class PlanningConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'planning'

    def ready(self) -> None:
        import planning.signals  # noqa: F401
