#!/usr/bin/env bash
# Usage: ./scripts/release.sh 0.2.0
set -euo pipefail

VERSION="${1:?Usage: release.sh <version>}"

# Sanity checks
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Uncommitted changes — commit or stash first." && exit 1
fi

# Bump version in pyproject.toml
sed -i '' "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml

git add pyproject.toml
git commit -m "chore: release v$VERSION"
git push origin main

git tag "v$VERSION"
git push origin "v$VERSION"

echo "Released v$VERSION — pipeline running at https://github.com/ykoellmann/obsidian-mcp/actions"
