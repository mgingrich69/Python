#!/usr/bin/env bash
# Called by the poller after each poll (in GitHub Actions). Replaces the `data`
# branch with a single commit containing state/data/results.json, then asks
# deploy.yml to publish the site. Failures are logged, never fatal to the loop.
set -uo pipefail
cd "$(dirname "$0")/.."
python -c "import json,sys; json.load(open('state/data/results.json'))" || { echo "results.json invalid; not publishing"; exit 1; }
tmp=$(mktemp -d)
mkdir -p "$tmp/data" && cp state/data/results.json "$tmp/data/results.json"
(
  cd "$tmp" && git init -q && git checkout -q -b data &&
  git -c user.name="election-poller[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
      add data/results.json &&
  git -c user.name="election-poller[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
      commit -q -m "results $(date -u +%Y-%m-%dT%H:%M:%SZ)" &&
  git push -q --force "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" data:data
) || { echo "push to data branch failed"; rm -rf "$tmp"; exit 1; }
rm -rf "$tmp"
gh workflow run deploy.yml --ref "${DEFAULT_BRANCH:-main}" -R "$GITHUB_REPOSITORY" || echo "could not trigger deploy"
