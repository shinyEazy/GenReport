#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$ROOT_DIR/backend/.env"
COMPOSE_FILE="$ROOT_DIR/docker/docker-compose.yaml"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ROOT_DIR/backend/.env.example" "$ENV_FILE"
  echo "Created backend/.env. Set model credentials before starting GenReport."
  exit 1
fi

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d api
echo "GenReport Engine is starting on http://localhost:8011"
