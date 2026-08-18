#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$ROOT_DIR/backend/.env"
COMPOSE_FILE="$ROOT_DIR/docker/docker-compose.yaml"

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down
echo "GenReport Engine stopped. The uv cache volume was retained."
