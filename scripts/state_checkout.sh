#!/usr/bin/env bash
# Put the latest published results (data branch) in ./state so the poller can
# keep last-good data across runs. Creates an empty state dir on first run.
set -euo pipefail
rm -rf state
if git fetch --depth=1 origin data 2>/dev/null; then
  git worktree add --detach state FETCH_HEAD
else
  mkdir -p state/data
  git -C state init -q
fi
mkdir -p state/data
