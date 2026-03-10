import os
from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# Database
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{DATA_DIR}/awsclone.db")

# JWT
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production-please")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24

# MinIO
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin123")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# Caddy
CADDY_ADMIN_URL = os.getenv("CADDY_ADMIN_URL", "http://localhost:2019")

# EC2 port allocation range
EC2_PORT_RANGE_START = int(os.getenv("EC2_PORT_RANGE_START", "49152"))
EC2_PORT_RANGE_END = int(os.getenv("EC2_PORT_RANGE_END", "65535"))

# Lambda
LAMBDA_MAX_CONCURRENT = int(os.getenv("LAMBDA_MAX_CONCURRENT", "2"))
LAMBDA_CODE_DIR = DATA_DIR / "lambda"

# CloudWatch
METRICS_COLLECT_INTERVAL = int(os.getenv("METRICS_COLLECT_INTERVAL", "60"))
METRICS_RETENTION_DAYS = int(os.getenv("METRICS_RETENTION_DAYS", "7"))

# Server
SERVER_PUBLIC_IP = os.getenv("SERVER_PUBLIC_IP", "")
