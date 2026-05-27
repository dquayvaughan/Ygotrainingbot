#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-/tmp/ygotrain/edopro-home}"
WORK_DIR="${2:-/tmp/ygotrain/EDOpro-server-ts}"

mkdir -p "$(dirname "$TARGET_DIR")"

if [ ! -d "$WORK_DIR/.git" ]; then
  rm -rf "$WORK_DIR"
  git clone --depth 1 https://github.com/diangogav/EDOpro-server-ts.git "$WORK_DIR"
fi

cd "$WORK_DIR"
if [ ! -d repositories ]; then
  bash clone_repositories.sh
fi
bash setup_resources.sh

rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR/deck"
cp -r "$WORK_DIR/resources/ygopro/base/script" "$TARGET_DIR/script"
cp "$WORK_DIR/resources/ygopro/base/cards.cdb" "$TARGET_DIR/cards.cdb"
cp "$WORK_DIR/resources/ygopro/base/lflist.conf" "$TARGET_DIR/lflist.conf"

echo "EDOPro home ready at $TARGET_DIR"
