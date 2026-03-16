import os


class Settings:
    # Core API
    APP_HOST: str = os.getenv("APP_HOST", "http://localhost")
    APP_PORT: int = int(os.getenv("APP_PORT", "7788"))
    APP_LOG_LEVEL: str = os.getenv("APP_LOG_LEVEL", "info")

    # Postgres
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "postgres")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "openbrain")
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "openbrain")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "openbrain")

    # Qdrant
    QDRANT_HOST: str = os.getenv("QDRANT_HOST", "qdrant")
    QDRANT_PORT: int = int(os.getenv("QDRANT_PORT", "6333"))
    QDRANT_COLLECTION: str = os.getenv("QDRANT_COLLECTION", "openbrain")
    QDRANT_API_KEY: str | None = os.getenv("QDRANT_API_KEY") or None

    # Minio / S3
    S3_ENDPOINT_URL: str = os.getenv("S3_ENDPOINT_URL", "http://minio:9000")
    S3_REGION: str = os.getenv("S3_REGION", "us-east-1")
    S3_ACCESS_KEY_ID: str = os.getenv("S3_ACCESS_KEY_ID", "openbrain")
    S3_SECRET_ACCESS_KEY: str = os.getenv("S3_SECRET_ACCESS_KEY", "openbrain_secret")
    S3_BUCKET_FILES: str = os.getenv("S3_BUCKET_FILES", "files")

    # Embedding (Ollama)
    EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", "http://localhost:11434")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
    EMBEDDING_TIMEOUT_SECONDS: int = int(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "15"))

    # Transcription (Whisper)
    WHISPER_BASE_URL: str = os.getenv("WHISPER_BASE_URL", "http://localhost:7777")
    WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "medium")
    WHISPER_TIMEOUT_SECONDS: int = int(os.getenv("WHISPER_TIMEOUT_SECONDS", "60"))

    # Chunking
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "600"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "100"))
    CONFIDENCE_THRESHOLD: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.8"))

    # Misc
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")


settings = Settings()
