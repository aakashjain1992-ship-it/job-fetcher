#!/bin/bash
# ─────────────────────────────────────────────────────────────
# Job Intelligence Dashboard — Quick Start
# Usage: ./start.sh
# ─────────────────────────────────────────────────────────────
set -e
cd "$(dirname "$0")"

# Activate or create virtualenv
if [ -f "venv/bin/activate" ]; then
  source venv/bin/activate
elif [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
else
  echo "Creating virtual environment..."
  python3 -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt -q
fi

# Load .env — handles values with spaces correctly
if [ -f ".env" ]; then
  while IFS='=' read -r key value; do
    # Skip comments and blank lines
    [[ "$key" =~ ^#.*$ ]] && continue
    [[ -z "$key" ]] && continue
    # Strip inline comments and surrounding quotes
    value="${value%%#*}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value#\"}" ; value="${value%\"}"
    value="${value#\'}" ; value="${value%\'}"
    export "$key=$value"
  done < .env
fi

echo ""
echo "  ┌─────────────────────────────────────────┐"
echo "  │   Job Intelligence Dashboard            │"
echo "  │   http://localhost:8000                 │"
echo "  │   Ctrl+C to stop                        │"
echo "  └─────────────────────────────────────────┘"
echo ""

python3 main.py --serve
