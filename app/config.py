from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Core API
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_LOG_LEVEL: str = "info"

    # Postgres
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "openbrain"
    POSTGRES_USER: str = "openbrain"
    POSTGRES_PASSWORD: str = "password"

    # Qdrant
    QDRANT_HOST: str = "qdrant"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION: str = "openbrain"
    QDRANT_API_KEY: str | None = None

    # Minio / S3
    S3_ENDPOINT_URL: str = "http://minio:9000"
    S3_REGION: str = "us-east-1"
    S3_ACCESS_KEY_ID: str = "minioadmin"
    S3_SECRET_ACCESS_KEY: str = "minioadmin"
    S3_BUCKET_FILES: str = "files"

    # Embedding
    EMBEDDING_BASE_URL: str = "http://ollama:11434"
    EMBEDDING_MODEL: str = "nomic-embed-text"
    EMBEDDING_TIMEOUT_SECONDS: int = 15

    # Transcription
    WHISPER_BASE_URL: str = "http://whisper:9001"
    WHISPER_MODEL: str = "medium"
    WHISPER_TIMEOUT_SECONDS: int = 60

    # Chunking / vectorisation
    CHUNK_SIZE: int = 600
    CHUNK_OVERLAP: int = 100
    CONFIDENCE_THRESHOLD: float = 0.8

    # Misc
    ENVIRONMENT: str = "local"

    class Config:
        env_file = ".env"


settings = Settings()

