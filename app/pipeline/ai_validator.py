import json
import os

from app.pipeline.prompts import AI_VALIDATOR_PROMPT


def run_ai_validator(normalized_rows, packing_list, order_overview, deterministic_report, model=None):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {
            "ai_validation": {
                "critical_findings": [],
                "warnings": [
                    {
                        "code": "AI_VALIDATOR_SKIPPED",
                        "message": "OPENAI_API_KEY not configured; AI validator skipped",
                        "source_row_id": "",
                        "section": "ai_validation",
                    }
                ],
                "recommendation": "REVIEW",
            }
        }

    from openai import OpenAI

    model = model or os.environ.get("OPENAI_VALIDATOR_MODEL", "gpt-4.1")
    client = OpenAI(api_key=api_key)

    payload = {
        "normalized_rows": normalized_rows,
        "packing_list": packing_list,
        "order_overview": order_overview,
        "deterministic_validation_report": deterministic_report,
    }

    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": AI_VALIDATOR_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
        ],
    )

    parsed = json.loads(response.choices[0].message.content or "{}")
    if "ai_validation" not in parsed:
        return {
            "ai_validation": {
                "critical_findings": [],
                "warnings": [
                    {
                        "code": "AI_VALIDATOR_FORMAT_ERROR",
                        "message": "AI validator response missing ai_validation object",
                        "source_row_id": "",
                        "section": "ai_validation",
                    }
                ],
                "recommendation": "REVIEW",
            }
        }
    return parsed
