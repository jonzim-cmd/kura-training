#!/bin/bash
# Codex sandbox setup — PostgreSQL + migrations for integration tests
# Configure in Codex as: bash scripts/codex-setup.sh
set -euo pipefail

echo "=== Installing PostgreSQL ==="
apt-get update -qq
apt-get install -y -qq postgresql postgresql-client >/dev/null 2>&1

echo "=== Starting PostgreSQL ==="
pg_ctlcluster $(pg_lsclusters -h | head -1 | awk '{print $1}') main start

echo "=== Creating database + user ==="
su - postgres -c "psql -qc \"CREATE USER kura WITH PASSWORD 'kura_dev_password' SUPERUSER;\""
su - postgres -c "psql -qc \"CREATE DATABASE kura OWNER kura;\""

echo "=== Running init.sql (roles) ==="
# init.sql has CREATE EXTENSION "vector" which will fail without pgvector —
# that's fine, the migration handles it gracefully with a fallback.
su - postgres -c "psql -U kura -d kura -f docker/postgres/init.sql" 2>&1 || true

echo "=== Running migrations ==="
for f in migrations/*.sql; do
  echo "  -> $(basename "$f")"
  su - postgres -c "psql -U kura -d kura -f $f" 2>&1 | grep -v "^$" || true
done

echo "=== Setting DATABASE_URL ==="
export DATABASE_URL="postgresql://kura:kura_dev_password@localhost:5432/kura"
echo "DATABASE_URL=$DATABASE_URL" >> "$HOME/.bashrc"
echo "DATABASE_URL=$DATABASE_URL" >> "$GITHUB_ENV" 2>/dev/null || true

echo "=== Done. Integration tests ready. ==="
