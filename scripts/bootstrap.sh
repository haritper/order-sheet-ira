#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example .env || true
export FLASK_APP=run.py
flask db upgrade
echo "Bootstrap complete. Run: source .venv/bin/activate && flask create-admin && flask run"
