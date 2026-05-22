from pydantic import computed_field
from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

from dotenv import load_dotenv

load_dotenv()


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    IS_PROD: bool = False


class CORSConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    CORS_ORIGINS: list[str] = ["*"]
    CORS_METHODS: list[str] = ["*"]
    CORS_HEADERS: list[str] = ["*"]


class PostgresConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    DB_MODERATION_SERVICE_HOST: str
    DB_MODERATION_SERVICE_PORT: int
    DB_MODERATION_SERVICE_NAME: str
    DB_MODERATION_SERVICE_USER: str
    DB_MODERATION_SERVICE_PASSWORD: str
    ECHO: bool = False

    @computed_field
    @property
    def POSTGRES_URL_ASYNC(self) -> MultiHostUrl:
        return MultiHostUrl.build(
            scheme="postgresql+asyncpg",
            username=self.DB_MODERATION_SERVICE_USER,
            password=self.DB_MODERATION_SERVICE_PASSWORD,
            host=self.DB_MODERATION_SERVICE_HOST,
            port=self.DB_MODERATION_SERVICE_PORT,
            path=self.DB_MODERATION_SERVICE_NAME,
        )


class JwtConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30


class ServiceConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    SERVICE_KEY: str
    B2B_URL: str = "http://b2b:8010"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    IS_PROD: bool = False

    DB_MODERATION_SERVICE_HOST: str
    DB_MODERATION_SERVICE_PORT: int
    DB_MODERATION_SERVICE_NAME: str
    DB_MODERATION_SERVICE_USER: str
    DB_MODERATION_SERVICE_PASSWORD: str
    ECHO: bool = False

    CORS_ORIGINS: list[str] = ["*"]
    CORS_METHODS: list[str] = ["*"]
    CORS_HEADERS: list[str] = ["*"]

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    SERVICE_KEY: str
    B2B_URL: str = "http://b2b:8010"

    @computed_field
    @property
    def POSTGRES_URL_ASYNC(self) -> MultiHostUrl:
        return MultiHostUrl.build(
            scheme="postgresql+asyncpg",
            username=self.DB_MODERATION_SERVICE_USER,
            password=self.DB_MODERATION_SERVICE_PASSWORD,
            host=self.DB_MODERATION_SERVICE_HOST,
            port=self.DB_MODERATION_SERVICE_PORT,
            path=self.DB_MODERATION_SERVICE_NAME,
        )


settings = Settings()
