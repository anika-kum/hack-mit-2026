#!/usr/bin/env bash
# Doodle Quest — one-command launcher
set -e

cd "$(dirname "$0")/backend"

if [ ! -f .env ]; then
  echo "⚠️  No backend/.env found — running in offline mode (no AI art)."
  echo "   To enable AI: cp backend/.env.example backend/.env and add your key."
  echo
fi

echo "🎨 Doodle Quest starting at http://localhost:8000"
echo
exec python3 -m uvicorn app:app --reload --port 8000
