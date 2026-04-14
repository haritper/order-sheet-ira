#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

mkdir -p data/uploads data/pdfs

export DATABASE_URL="sqlite:///ordersheet.db"
export UPLOAD_DIR="$(pwd)/data/uploads"
export PDF_OUTPUT_DIR="$(pwd)/data/pdfs"

# Optional for AI import
# export OPENAI_API_KEY="your_key_here"

./.venv/bin/python -c "from app import create_app; app=create_app(); app.run(host='0.0.0.0', port=5001, use_reloader=False)"
