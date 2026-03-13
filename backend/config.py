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
def _detect_host_ip() -> str:
    """Detect the host machine's real LAN IP (not Docker bridge)."""
    import socket
    # Prefer socket probe — connects to 8.8.8.8 without sending data,
    # revealing which outbound interface is used (gives real LAN IP like 192.168.x.x)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        # Reject Docker bridge IPs (172.x.x.x or 10.x.x.x virtual ranges)
        if not ip.startswith("172.") and not ip.startswith("10."):
            return ip
    except Exception:
        pass
    # Fallback: host.docker.internal (works if extra_hosts: host-gateway is set)
    try:
        ip = socket.gethostbyname("host.docker.internal")
        if not ip.startswith("172.") and not ip.startswith("10."):
            return ip
    except Exception:
        pass
    return ""

SERVER_PUBLIC_IP = os.getenv("SERVER_PUBLIC_IP", "") or _detect_host_ip()

# Base domain for custom subdomains (e.g. mysite.cloudfabric.duckdns.org)
BASE_DOMAIN = os.getenv("BASE_DOMAIN", "cloudfabric.duckdns.org")

# GitHub OAuth
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
# Public base URL for OAuth callback and webhooks (e.g. http://your-server-ip)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://{SERVER_PUBLIC_IP}" if SERVER_PUBLIC_IP else "http://localhost")
