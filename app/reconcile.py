"""Reconciliation decisions, isolated from the HTTP and storage layers."""

from __future__ import annotations

from datetime import date, datetime, timezone

from .consolidation.cleaners import is_blank


BUSINESS_FIELDS = (
    "case_number", "case_status", "case_type", "designated_office",
    "plaintiff_name", "plaintiff_document", "defendant_name",
    "defendant_document", "distribution_date", "jurisdiction", "state",
    "root_cause", "product", "business_unit", "summary", "requisitions",
    "documents",
)


def normalize(value):
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if is_blank(value):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def preview(cases: list[dict], report_keys: list[int], existing: list[dict]) -> dict:
    active = {int(item["legal_folder"]): item for item in existing if not item.get("deleted_at")}
    incoming = {int(item["legal_folder"]): item for item in cases}
    present = set(report_keys)
    new, updated, unchanged = [], [], []
    for folder, item in incoming.items():
        old = active.get(folder)
        if old is None:
            new.append(item)
        else:
            changed = [field for field in BUSINESS_FIELDS if normalize(old.get(field)) != normalize(item.get(field))]
            if changed:
                updated.append({"case": item, "changed_fields": changed})
            else:
                unchanged.append(item)
    missing = [item for folder, item in active.items() if folder not in present]
    return {"new": new, "updated": updated, "unchanged": unchanged, "missing": missing}


def validate_decisions(missing: list[dict], decisions: dict) -> dict[int, dict]:
    allowed = {int(item["legal_folder"]) for item in missing}
    if not set(decisions) <= {str(key) for key in allowed}:
        raise ValueError("Decisão enviada para um caso que não está ausente da extração")
    cleaned = {}
    for key, item in decisions.items():
        if not isinstance(item, dict) or item.get("action") not in {"keep", "remove"}:
            raise ValueError(f"Decisão inválida para a pasta {key}")
        if item["action"] == "remove":
            value = item.get("deleted_at")
            try:
                date.fromisoformat(value)
            except (TypeError, ValueError):
                raise ValueError(f"Informe uma data de remoção válida para a pasta {key}") from None
        cleaned[int(key)] = item
    return cleaned


def planned_stats(comparison: dict, decisions: dict) -> dict[str, int]:
    resolved = validate_decisions(comparison["missing"], decisions)
    return {
        "inserted": len(comparison["new"]),
        "updated": len(comparison["updated"]),
        "removed": sum(item["action"] == "remove" for item in resolved.values()),
        "unchanged": len(comparison["unchanged"]),
    }


def apply(cases: list[dict], existing: list[dict], comparison: dict, decisions: dict) -> tuple[list[dict], dict]:
    resolved = validate_decisions(comparison["missing"], decisions)
    now = datetime.now(timezone.utc).isoformat()
    next_cases = [dict(item) for item in existing]
    active_positions = {int(item["legal_folder"]): index for index, item in enumerate(next_cases) if not item.get("deleted_at")}
    inserted = updated = removed = 0
    for item in comparison["new"]:
        next_cases.append({**item, "created_at": now, "updated_at": now, "deleted_at": None})
        inserted += 1
    for entry in comparison["updated"]:
        item = entry["case"]
        index = active_positions[int(item["legal_folder"])]
        next_cases[index] = {**next_cases[index], **item, "updated_at": now}
        updated += 1
    for folder, decision in resolved.items():
        if decision["action"] == "remove":
            index = active_positions[folder]
            next_cases[index] = {**next_cases[index], "deleted_at": decision["deleted_at"], "updated_at": now}
            removed += 1
    return next_cases, {
        "inserted": inserted, "updated": updated,
        "removed": removed, "unchanged": len(comparison["unchanged"]),
    }
