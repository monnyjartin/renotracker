import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    app_env = os.getenv("APP_ENV", "prod")

    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://reno:reno_pass_change_me@db:5432/renotracker",
    )

    session_secret = os.getenv("SESSION_SECRET", "change_me")

    # Internal docker-to-docker MinIO endpoint
    minio_endpoint = os.getenv("MINIO_ENDPOINT", "http://minio:9000").rstrip("/")

    # Public endpoint used in presigned URLs (browser must be able to reach this)
    minio_public_endpoint = os.getenv("MINIO_PUBLIC_ENDPOINT", "").rstrip("/")

    minio_access_key = os.getenv("MINIO_ACCESS_KEY", "")
    minio_secret_key = os.getenv("MINIO_SECRET_KEY", "")
    minio_bucket = os.getenv("MINIO_BUCKET", "renovation")

    admin_email = os.getenv("ADMIN_EMAIL", "jon@local")
    admin_password = os.getenv("ADMIN_PASSWORD", "vampire12")
    admin_name = os.getenv("ADMIN_NAME", "Jon")
    admin_update = os.getenv("ADMIN_UPDATE", "0") == "1"

settings = Settings()

