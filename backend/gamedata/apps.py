from django.apps import AppConfig


class GamedataConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gamedata'

    def ready(self) -> None:
        import gamedata.signals
