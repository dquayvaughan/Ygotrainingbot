#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="${YGOTRAIN_DATA_DIR:-/data}"
PORT="${PORT:-8765}"

mkdir -p \
  "$DATA_DIR/jobs" \
  "$DATA_DIR/bots" \
  "$DATA_DIR/custom-decks" \
  "$DATA_DIR/human-duels" \
  "$DATA_DIR/bracket" \
  "$DATA_DIR/edopro-home"

export YGOTRAIN_DATA_DIR="$DATA_DIR"

export YGOTRAIN_EDOPRO_BUILD_DIR="$DATA_DIR/edopro-build"

exec ygotrain-dashboard \
  --host 0.0.0.0 \
  --port "$PORT" \
  --repo-root /app \
  --data-dir "$DATA_DIR" \
  --edopro-home "$DATA_DIR/edopro-home" \
  --human-catalog-dir "$DATA_DIR/human-duels"
