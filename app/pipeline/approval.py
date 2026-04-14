HARD_STOP_CODES = {
    "SILENT_ROW_DROP",
    "OVERVIEW_TSHIRT_TOTAL_MISMATCH",
    "OVERVIEW_TROUSER_TOTAL_MISMATCH",
    "ACCESSORY_TOTAL_MISMATCH",
    "INVALID_SIZE_FOR_CATEGORY",
    "INVALID_CATEGORY_FOR_SECTION",
}


def approval_decision(deterministic_report, ai_report, allow_uncertain_row_override=False):
    critical = deterministic_report.get("critical_errors", [])
    warnings = deterministic_report.get("warnings", [])

    critical_codes = {e.get("code") for e in critical}
    ai_reco = ai_report.get("ai_validation", {}).get("recommendation", "REVIEW")

    blocking_issues = []
    reason_codes = []

    for err in critical:
        code = err.get("code")
        reason_codes.append(code)
        blocking_issues.append(err)

    for code in HARD_STOP_CODES:
        if code in critical_codes:
            reason_codes.append(code)

    if "UNCERTAIN_ROW_PARSE" in critical_codes and not allow_uncertain_row_override:
        reason_codes.append("UNCERTAIN_ROW_PARSE")

    if critical:
        status = "REJECTED"
    elif ai_reco == "REJECT":
        status = "REJECTED"
    elif ai_reco == "REVIEW":
        status = "NEEDS_MANUAL_REVIEW"
    else:
        status = "APPROVED"

    if any(code in HARD_STOP_CODES for code in critical_codes):
        status = "REJECTED"

    if "UNCERTAIN_ROW_PARSE" in critical_codes and not allow_uncertain_row_override:
        status = "REJECTED"

    return {
        "status": status,
        "reason_codes": sorted(set(reason_codes)),
        "blocking_issues": blocking_issues,
        "warning_count": len(warnings),
    }
