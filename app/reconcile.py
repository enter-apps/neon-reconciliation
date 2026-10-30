"""Reconciliation decisions, isolated from the HTTP and storage layers."""

from __future__ import annotations

from datetime import date, datetime

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
