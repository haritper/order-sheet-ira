# Order Sheet Web App (Flask)

Internal order-sheet system to replace manual Word process with structured data entry, review workflow, player CSV import, and PDF generation.

## Stack
- Flask, SQLAlchemy, Flask-Migrate (Alembic)
- Flask-Login, Flask-WTF
- PostgreSQL (production), SQLite (test/dev fallback)
- WeasyPrint for PDF rendering
- Docker Compose: web + postgres + nginx + backup

## Setup (Local)
```bash
cd /Users/sudhan/Order\ Sheet
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
export FLASK_APP=run.py
flask db upgrade
flask create-admin
flask run
```

Login route: `/login`

## Core Routes
- `/login`, `/logout`
- `/orders`, `/orders/new`, `/orders/<id>`, `/orders/<id>/edit`
- `/orders/<id>/players/import-csv`
- `/orders/<id>/export/pdf`
- `/orders/<id>/approve`
- `/admin/users`

## CSV Contract
Required headers:
- `player_name`
- `number`
- `sleeve_type` (`HALF` or `FULL`)
- `tshirt_size` (`XS,S,M,L,XL,2XL,3XL,4XL`)
- `tshirt_qty` (>0)
- `trouser_size` (`XS,S,M,L,XL,2XL,3XL,4XL`)
- `trouser_qty` (>0)
- optional: `row_number` (for duplicate/update behavior)

## Run Tests
```bash
source .venv/bin/activate
pytest -q
```

## Production-Safe Order Pipeline
Canonical flow:
`raw input -> normalized rows -> packing list -> overview -> validation -> approval`

Generate pipeline outputs:
```bash
source .venv/bin/activate
python scripts/run_order_pipeline.py \
  --input /path/to/input.xlsx \
  --output-dir /path/to/output \
  --team-name "Team Name"
```

Outputs:
- `normalized_rows.json`
- `packing_list.json`
- `order_overview.json`
- `validation_report.json`
- `approval_decision.json`

Optional:
- `--use-llm-normalizer` to run the strict normalization prompt via OpenAI
- default deterministic validator is always enforced
- AI validator can be skipped with `--skip-ai-validator`
- model routing mode can be selected with `--mode safe_gpt54|hybrid|cost_optimized`

## Pricing Calculator
Pricing module location:
- `pricing/pricing_config.json`
- `pricing/cost_calculator.py`
- `pricing/model_routing.json`

Run preset comparison table:
```bash
source .venv/bin/activate
python pricing/cost_calculator.py --compare
```

Run custom estimate:
```bash
source .venv/bin/activate
python pricing/cost_calculator.py \
  --sheet-count 200 \
  --stage input_normalization:gpt-5.4:3500:0:900 \
  --stage generator:gpt-5.4:2500:0:1200 \
  --stage ai_validator:gpt-5.4:1800:0:600
```

## Docker Deploy
```bash
cp .env.example .env
# edit SECRET_KEY and DATABASE_URL as needed

docker compose up --build -d
```

## S3 Storage On EC2
- Set `STORAGE_BACKEND=s3`
- Set `S3_BUCKET`, optional `S3_PREFIX`, and `AWS_REGION`
- Attach an IAM role to the EC2 instance with S3 read/write/delete access for that bucket prefix
- New orders create an order-scoped prefix in S3:
  - `orders/<order_id>/uploaded_images/`
  - `orders/<order_id>/uploaded_documents/`
  - `orders/<order_id>/generated_order_sheets/`
  - `orders/<order_id>/meta/`
- Exported order sheets and production/customer plan PDFs are stored in `generated_order_sheets/`
- Main order-sheet exports are versioned like `ORDER-ID-V1.pdf`, `ORDER-ID-V2.pdf`

## Notes
- Generated PDFs are stored in `data/pdfs/` and indexed in `attachments`.
- Daily SQL backups are written to `data/backups/` and retained for 14 days.
- WeasyPrint requires OS-level rendering libraries in runtime environment.
# order-sheet-ira
