from __future__ import annotations

import logging

import json
from pathlib import Path

import pandas as pd

from .cleaners import is_blank
from .mapping import Field, Mapping, NestedGroup, load_mapping


logger = logging.getLogger(__name__)

Record = dict[str, object]


def read_source(path: str | Path, mapping: Mapping) -> pd.DataFrame:
    """Read the spreadsheet and select the mapped columns by position.

    Columns are resolved positionally rather than by pandas' de-duplicated
    labels because the export repeats the header ``Status`` (once for the case,
    once for the requisition). ``occurrence`` in the mapping disambiguates them.

    Columns the mapping does not declare are ignored; a declared column that is
    missing raises, so a typo never passes as an empty column.
    """
    raw = pd.read_excel(path, sheet_name=mapping.sheet, header=None)
    if raw.empty:
        raise ValueError(f"{path} has no header row")
    header = [str(value).strip() for value in raw.iloc[0]]
    body = raw.iloc[1:].reset_index(drop=True)
    return pd.DataFrame(
        {
            field.column: body.iloc[:, _resolve_position(header, field)]
            for field in mapping.all_fields
        }
    )


def _resolve_position(header: list[str], field: Field) -> int:
    """Find the index of the ``occurrence``-th column named ``field.source``."""
    positions = [index for index, name in enumerate(header) if name == field.source]
    if len(positions) < field.occurrence:
        raise KeyError(
            f"Column {field.source!r} (occurrence {field.occurrence}) not found; "
            f"available headers: {header}"
        )
    return positions[field.occurrence - 1]


def apply_cleaners(frame: pd.DataFrame, mapping: Mapping) -> pd.DataFrame:
    """Run each field's cleaner over its column, returning a new DataFrame.

    Results are stored as ``object`` columns on purpose: pandas would otherwise
    re-infer a numeric dtype and turn a cleaned ``int`` id back into a float,
    or a ``None`` back into ``NaN``.
    """
    cleaned = frame.copy()
    for field in mapping.all_fields:
        cleaner = mapping.cleaner_for(field)
        if cleaner is None:
            continue
        cleaned[field.column] = pd.Series(
            [cleaner(value) for value in cleaned[field.column]],
            index=cleaned.index,
            dtype=object,
        )
    return cleaned


def consolidate(frame: pd.DataFrame, mapping: Mapping) -> pd.DataFrame:
    """Collapse the repeated rows into one row per ``mapping.group_by`` value."""
    flat_columns = [field.target for field in mapping.fields]
    missing_key = frame[mapping.group_by].map(is_blank)
    if missing_key.any():
        logger.warning(
            "Dropping %d row(s) with no %s", int(missing_key.sum()), mapping.group_by
        )
        frame = frame.loc[~missing_key]

    rows = [
        {
            **{column: _first_value(group[column]) for column in flat_columns},
            **{
                nested.target: _collect_objects(group, nested)
                for nested in mapping.nested
            },
        }
        for _, group in frame.groupby(mapping.group_by, sort=True)
    ]
    return pd.DataFrame(rows, columns=list(mapping.output_columns), dtype=object)


def _first_value(series: pd.Series) -> object:
    """First non-null value of a case attribute (they repeat across rows)."""
    values = series[~series.map(is_blank)]
    return None if values.empty else values.iloc[0]


def _collect_objects(group: pd.DataFrame, nested: NestedGroup) -> list[Record]:
    """Build the deduplicated list of objects for one nested column."""
    columns = {field.column: field.target for field in nested.fields}
    records: list[Record] = []
    seen: set[str] = set()
    for raw in group[list(columns)].to_dict("records"):
        record = {
            target: None if is_blank(raw[column]) else raw[column]
            for column, target in columns.items()
        }
        if all(value is None for value in record.values()):
            continue
        fingerprint = json.dumps(record, sort_keys=True, default=str)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        records.append(record)
    if nested.sort_by:
        records.sort(key=lambda record: _sort_key(record, nested.sort_by))
    return records


def _sort_key(record: Record, sort_by: tuple[str, ...]) -> tuple[str, ...]:
    """Stable, null-safe sort key (nulls last)."""
    return tuple(
        "" if record.get(key) is None else str(record[key]) for key in sort_by
    )


def transform(
    path: str | Path, mapping: Mapping | None = None,
    source: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Read ``path`` and return the consolidated DataFrame, one row per case."""
    mapping = mapping or load_mapping()
    frame = source if source is not None else read_source(path, mapping)
    logger.info("Read %d source rows from %s", len(frame), path)
    cleaned = apply_cleaners(frame, mapping)
    # Keep report membership before rejecting CNJs: rejected cases are still
    # present in the export and must not be reported as missing from it.
    report_keys = tuple(cleaned.loc[
        ~cleaned[mapping.group_by].map(is_blank), mapping.group_by
    ].drop_duplicates())
    for field in mapping.fields:
        if field.clean != "cnj":
            continue
        invalid = cleaned[field.column].map(is_blank)
        rejected = frame.loc[invalid, [field.column, mapping.group_by]].drop_duplicates()
        for cnj, folder in rejected.itertuples(index=False, name=None):
            logger.warning(
                "CNJ %r e pasta (legal_folder) %r não incluídos: CNJ inválido ou ausente",
                None if is_blank(cnj) else cnj, folder,
            )
        cleaned = cleaned.loc[~invalid]
        frame = frame.loc[~invalid]
    consolidated = consolidate(cleaned, mapping)
    consolidated.attrs["report_keys"] = report_keys
    logger.info("Consolidated into %d cases", len(consolidated))
    return consolidated
