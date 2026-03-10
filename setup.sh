#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# AWS Clone — One-command deployment for Ubuntu 22.04+
# Usage: bash setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${BLUE}[info]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ok]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
die()   { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

echo ""
echo "  ╔══════════════════════════════════╗"
echo "  ║   AWS Clone — Setup & Deploy     ║"
echo "  ╚══════════════════════════════════╝"
echo ""

# ── 1. Check OS ───────────────────────────────────────────────────────────────
if [[ "$(uname -s)" != "Linux" ]]; then
  die "This script is for Linux (Ubuntu 22.04+). You are on $(uname -s)."
fi

# ── 2. Install system dependencies ───────────────────────────────────────────
info "Checking system dependencies..."

if ! command -v docker &>/dev/null; then
  info "Installing Docker..."
  curl -fsSL https://get.docker.com | bash
  sudo usermod -aG docker "$USER"
  warn "Docker installed. You may need to log out and back in for group changes to take effect."
  warn "If docker commands fail, run: newgrp docker"
fi

if ! docker compose version &>/dev/null 2>&1; then
  info "Installing Docker Compose plugin..."
  sudo apt-get install -y docker-compose-plugin
fi

if ! command -v node &>/dev/null; then
  info "Installing Node.js 20 (LTS)..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

ok "Docker $(docker --version | cut -d' ' -f3 | tr -d ',')"
ok "Node   $(node --version)"

# ── 3. Create data directories ────────────────────────────────────────────────
info "Creating data directories..."
mkdir -p data/caddy/data data/caddy/config data/minio data/lambda_code
ok "Directories ready"

# ── 4. Generate .env if missing ───────────────────────────────────────────────
if [[ ! -f .env ]]; then
  info "Generating .env with random secrets..."
  JWT_SECRET=$(openssl rand -hex 32)
  MINIO_PASS=$(openssl rand -hex 16)
  SERVER_IP=$(curl -sf https://ipinfo.io/ip 2>/dev/null || hostname -I | awk '{print $1}')
  cat > .env <<EOF
JWT_SECRET=${JWT_SECRET}
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=${MINIO_PASS}
SERVER_PUBLIC_IP=${SERVER_IP}
EOF
  ok ".env generated (JWT secret + MinIO password randomized)"
else
  warn ".env already exists — skipping generation. Edit it if needed."
  # Auto-fill SERVER_PUBLIC_IP if blank
  if grep -q 'SERVER_PUBLIC_IP=$' .env; then
    SERVER_IP=$(curl -sf https://ipinfo.io/ip 2>/dev/null || hostname -I | awk '{print $1}')
    sed -i "s|SERVER_PUBLIC_IP=|SERVER_PUBLIC_IP=${SERVER_IP}|" .env
    ok "SERVER_PUBLIC_IP set to ${SERVER_IP}"
  fi
fi

# ── 5. Build frontend ─────────────────────────────────────────────────────────
info "Building frontend..."
(
  cd frontend
  npm install --silent
  npm run build --silent
)
ok "Frontend built → frontend/dist/"

# ── 6. Pull / build Docker images ────────────────────────────────────────────
info "Pulling base images and building backend..."
docker compose pull --quiet caddy minio 2>/dev/null || true
docker compose build --quiet backend
ok "Images ready"

# ── 7. Build Lambda runtime images ───────────────────────────────────────────
info "Building Lambda runtime images..."
if [[ -d backend/lambda_runtimes/python3.11 ]]; then
  docker build -q -t awsclone-lambda-python3.11 backend/lambda_runtimes/python3.11
  ok "Lambda runtime: python3.11"
fi
if [[ -d backend/lambda_runtimes/node20 ]]; then
  docker build -q -t awsclone-lambda-node20 backend/lambda_runtimes/node20
  ok "Lambda runtime: node20"
fi

# ── 8. Start services ─────────────────────────────────────────────────────────
info "Starting services..."
docker compose up -d

# ── 9. Wait for backend health check ─────────────────────────────────────────
info "Waiting for backend to be healthy..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/api/v1/health &>/dev/null; then
    ok "Backend is healthy"
    break
  fi
  if [[ $i -eq 30 ]]; then
    warn "Backend did not respond after 30s. Check logs: docker compose logs backend"
  fi
  sleep 1
done

# ── 10. Print summary ─────────────────────────────────────────────────────────
SERVER_IP=$(grep SERVER_PUBLIC_IP .env | cut -d'=' -f2)
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   AWS Clone is running!                              ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Dashboard:   http://${SERVER_IP}"
echo "  API:         http://${SERVER_IP}/api/v1"
echo "  MinIO UI:    http://${SERVER_IP}:9001"
echo ""
echo "  Logs:        docker compose logs -f"
echo "  Stop:        docker compose down"
echo "  Restart:     docker compose restart"
echo ""
echo "  First time? Visit the dashboard and register — the first"
echo "  user automatically becomes root (admin)."
echo ""
echo -e "${YELLOW}  Firewall reminder: open ports 80, 443, 9000, 9001${NC}"
echo "  sudo ufw allow 80,443,9000,9001/tcp"
echo ""
