#!/usr/bin/env bash
# Local backend setup: venv + install + migrate.
# Usage: bash scripts/dev_setup.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Note: create backend/.env from .env.example (SECRET_KEY, DATABASE_URL, Supabase JWT settings, …)"
fi

python3 -m venv .venv
# shellcheck source=/dev/null
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python manage.py migrate
echo ""
echo "Activate before runserver:"
echo "  source $ROOT/.venv/bin/activate && python manage.py runserver"
