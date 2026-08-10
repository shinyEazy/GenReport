#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

terminate_pid_file() {
  local pid_file="$1"
  local pid

  pid="$(cat "$pid_file")"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    rm -f "$pid_file"
    return
  fi

  if kill -0 "-$pid" 2>/dev/null; then
    kill -TERM "-$pid" 2>/dev/null || true
  else
    pkill -TERM -P "$pid" 2>/dev/null || true
    kill -TERM "$pid" 2>/dev/null || true
  fi

  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null && ! kill -0 "-$pid" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done

  if kill -0 "-$pid" 2>/dev/null; then
    kill -KILL "-$pid" 2>/dev/null || true
  elif kill -0 "$pid" 2>/dev/null; then
    pkill -KILL -P "$pid" 2>/dev/null || true
    kill -KILL "$pid" 2>/dev/null || true
  fi

  rm -f "$pid_file"
}

port_is_open() {
  local port="$1"
  timeout 1 bash -c ":</dev/tcp/127.0.0.1/$port" >/dev/null 2>&1
}

terminate_port_listeners() {
  local name="$1"
  local port="$2"
  local pids pid

  if [[ "${LAMBDA_STOP_PORTS:-0}" != "1" ]]; then
    return
  fi

  if ! command -v lsof >/dev/null 2>&1; then
    echo "Cannot inspect $name port $port; lsof is not installed."
    return
  fi

  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return
  fi

  while IFS= read -r pid; do
    if [[ "$pid" =~ ^[0-9]+$ ]]; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done <<< "$pids"

  sleep 0.5

  while IFS= read -r pid; do
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done <<< "$pids"
}

for pid_file in "$ROOT_DIR"/backend*.pid "$ROOT_DIR"/frontend*.pid; do
  if [[ -f "$pid_file" ]]; then
    terminate_pid_file "$pid_file"
  fi
done

terminate_port_listeners "backend" "$BACKEND_PORT"
terminate_port_listeners "frontend" "$FRONTEND_PORT"

echo "LAMBDA Local stopped."

if port_is_open "$BACKEND_PORT"; then
  echo "Warning: backend port $BACKEND_PORT still responds; listener was not tracked by $ROOT_DIR/backend.pid."
  echo "Inspect with: lsof -nP -iTCP:$BACKEND_PORT -sTCP:LISTEN"
fi

if port_is_open "$FRONTEND_PORT"; then
  echo "Warning: frontend port $FRONTEND_PORT still responds; listener was not tracked by $ROOT_DIR/frontend.pid."
  echo "Inspect with: lsof -nP -iTCP:$FRONTEND_PORT -sTCP:LISTEN"
fi
