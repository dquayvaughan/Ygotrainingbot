#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-/tmp/ygotrain/edopro-home}"
WORK_DIR="${YGOTRAIN_EDOPRO_BUILD_DIR:-${2:-/tmp/ygotrain/EDOpro-server-ts}}"

REPOS_DIR="$WORK_DIR/repositories"
SCRIPTS_DIR="$REPOS_DIR/edopro-card-scripts"
DB_FILE="$REPOS_DIR/edopro-card-databases/cards.cdb"
BANLIST_FILE="$REPOS_DIR/edopro-banlists-ignis/OCG.lflist.conf"

mkdir -p "$(dirname "$TARGET_DIR")"

if [ ! -d "$WORK_DIR/.git" ]; then
  rm -rf "$WORK_DIR"
  git clone --depth 1 https://github.com/diangogav/EDOpro-server-ts.git "$WORK_DIR"
fi

cd "$WORK_DIR"
if [ ! -d "$REPOS_DIR" ]; then
  # Some environments do not include wget; clone_repositories can partially
  # succeed before failing. We still continue if required repos exist.
  bash clone_repositories.sh || true
fi

if [ ! -d "$SCRIPTS_DIR" ]; then
  echo "Missing scripts repository: $SCRIPTS_DIR" >&2
  exit 1
fi
for prelude in constant.lua utility.lua; do
  if [ ! -f "$SCRIPTS_DIR/$prelude" ]; then
    echo "Missing core Lua prelude: $SCRIPTS_DIR/$prelude" >&2
    exit 1
  fi
done
if [ ! -f "$DB_FILE" ]; then
  echo "Missing database file: $DB_FILE" >&2
  exit 1
fi
if [ ! -f "$BANLIST_FILE" ]; then
  echo "Missing banlist file: $BANLIST_FILE" >&2
  exit 1
fi

rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR/deck"
cp -r "$SCRIPTS_DIR" "$TARGET_DIR/script"
cp "$DB_FILE" "$TARGET_DIR/cards.cdb"
cp "$BANLIST_FILE" "$TARGET_DIR/lflist.conf"

echo "EDOPro home ready at $TARGET_DIR"
