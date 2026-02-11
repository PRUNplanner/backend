from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Search for .env in current or parent folders
BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = next((p for p in [BASE_DIR / '.env', BASE_DIR.parent / '.env'] if p.exists()), None)


class CacheSettings(BaseSettings):
    default_location: str = Field(...)
    max_connections: int = Field(default=20)

    model_config = SettingsConfigDict(extra='ignore', env_file=str(ENV_FILE) if ENV_FILE else None, env_prefix='CACHE_')


class DatabaseSettings(BaseSettings):
    name: str = Field(...)
    user: str = Field(...)
    password: str = Field(...)
    host: str = Field(...)
    port: int = Field(...)

    legacy_name: str | None = Field(default=None)
    legacy_user: str | None = Field(default=None)
    legacy_password: str | None = Field(default=None)
    legacy_host: str | None = Field(default=None)
    legacy_port: int | None = Field(default=None)

    model_config = SettingsConfigDict(
        extra='ignore', env_file=str(ENV_FILE) if ENV_FILE else None, env_prefix='DATABASE_'
    )


class CelerySettings(BaseSettings):
    broker_url: str = Field(...)
    result_backend: str = Field(...)

    model_config = SettingsConfigDict(
        extra='ignore', env_file=str(ENV_FILE) if ENV_FILE else None, env_prefix='CELERY_'
    )


class RestFrameworkSettings(BaseSettings):
    access_token_lifetime: int = Field()
    refresh_token_lifetime: int = Field()

    model_config = SettingsConfigDict(
        extra='ignore', env_file=str(ENV_FILE) if ENV_FILE else None, env_prefix='REST_FRAMEWORK_'
    )


class EmailSettings(BaseSettings):
    resend_api_key: str = Field()
    from_email: str = Field()
    verification_expiry_minutes: int = Field(default=30)
    password_reset_expiry_minutes: int = Field(default=30)
    model_config = SettingsConfigDict(extra='ignore', env_file=str(ENV_FILE) if ENV_FILE else None, env_prefix='EMAIL_')


class Settings(BaseSettings):
    django_settings_module: str = Field(default='core.config.django.local')
    django_secret_key: str = Field(default='---')
    django_allowed_hosts: str = Field(default='127.0.0.1,localhost')

    database: DatabaseSettings = DatabaseSettings()
    cache: CacheSettings = CacheSettings()
    celery: CelerySettings = CelerySettings()
    email: EmailSettings = EmailSettings()  # type: ignore
    rest_framework: RestFrameworkSettings = RestFrameworkSettings()  # type: ignore

    model_config = SettingsConfigDict(env_file=str(ENV_FILE) if ENV_FILE else None, extra='ignore')


settings = Settings()
