from __future__ import annotations

import json
from pathlib import Path

from functools import partial
from dataclasses import dataclass

from typing import Any, Callable

from . import cleaners


DEFAULT_MAPPING_PATH = Path(__file__).with_name("mapping.json")

#: Cleaner name (as used in ``mapping.json``) -> callable.
CLEANERS: dict[str, Callable[[Any], Any]] = {
    "cnj": cleaners.clean_cnj,
    "date": cleaners.to_iso_date,
    "integer": cleaners.to_integer,
    "text": cleaners.normalize_text,
    "multiline_text": cleaners.normalize_multiline_text,
    "name": cleaners.normalize_name,
    "document": cleaners.clean_document,
}

#: Cleaners that need extra configuration from ``canonical_values``.
PARAMETRIC_CLEANERS = {"canonical"}


@dataclass(frozen=True)
class Field:
    """One source column and the target it feeds."""

    source: str
    target: str
    clean: str | None = None
    occurrence: int = 1
    options: str | None = None
    attribute: str = "name"
    prefix: str = ""

    @property
    def column(self) -> str:
        """Intermediate column name, namespaced to avoid nested-group clashes."""
        return f"{self.prefix}{self.target}"


@dataclass(frozen=True)
class NestedGroup:
    """A set of columns collapsed into a list of objects (JSONB) per case."""

    target: str
    fields: tuple[Field, ...]
    sort_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class Mapping:
    """The whole de-para: flat fields, nested groups and canonical vocabularies."""

    sheet: str | int
    group_by: str
    fields: tuple[Field, ...]
    nested: tuple[NestedGroup, ...]
    canonical_values: dict[str, tuple[dict[str, Any], ...]]

    @property
    def all_fields(self) -> tuple[Field, ...]:
        """Flat fields followed by every nested field, in declaration order."""
        nested = tuple(field for group in self.nested for field in group.fields)
        return self.fields + nested

    @property
    def output_columns(self) -> tuple[str, ...]:
        """Column order of the consolidated DataFrame."""
        return tuple(field.target for field in self.fields) + tuple(
            group.target for group in self.nested
        )

    def cleaner_for(self, field: Field) -> Callable[[Any], Any] | None:
        """Resolve the callable that cleans ``field``, or ``None`` for passthrough."""
        if field.clean is None:
            return None
        if field.clean in PARAMETRIC_CLEANERS:
            return self._parametric_cleaner(field)
        try:
            return CLEANERS[field.clean]
        except KeyError:
            raise KeyError(
                f"Unknown cleaner {field.clean!r} for target {field.target!r}"
            ) from None

    def _parametric_cleaner(self, field: Field) -> Callable[[Any], Any]:
        if field.options is None:
            raise ValueError(f"Field {field.target!r} needs an 'options' key")
        try:
            options = self.canonical_values[field.options]
        except KeyError:
            raise KeyError(
                f"No canonical_values entry named {field.options!r}"
            ) from None
        missing = [record for record in options if field.attribute not in record]
        if missing:
            raise KeyError(
                f"Field {field.target!r} reads attribute {field.attribute!r}, absent "
                f"from canonical_values[{field.options!r}]: {missing}"
            )
        return partial(
            cleaners.canonicalize, options=options, attribute=field.attribute
        )


def _build_field(payload: dict[str, Any], prefix: str = "") -> Field:
    return Field(
        source=payload["source"],
        target=payload["target"],
        clean=payload.get("clean"),
        occurrence=payload.get("occurrence", 1),
        options=payload.get("options"),
        attribute=payload.get("attribute", "name"),
        prefix=prefix,
    )


def load_mapping(path: str | Path = DEFAULT_MAPPING_PATH) -> Mapping:
    """Read and validate the mapping file."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    nested = tuple(
        NestedGroup(
            target=group["target"],
            fields=tuple(
                _build_field(field, prefix=f"{group['target']}.")
                for field in group["fields"]
            ),
            sort_by=tuple(group.get("sort_by", ())),
        )
        for group in payload.get("nested", ())
    )
    mapping = Mapping(
        sheet=payload.get("sheet", 0),
        group_by=payload["group_by"],
        fields=tuple(_build_field(field) for field in payload["fields"]),
        nested=nested,
        canonical_values={
            name: tuple(values)
            for name, values in payload.get("canonical_values", {}).items()
        },
    )
    _validate(mapping)
    return mapping


def _validate(mapping: Mapping) -> None:
    """Fail loudly on a malformed mapping rather than midway through a load."""
    targets = [field.target for field in mapping.fields]
    duplicates = {target for target in targets if targets.count(target) > 1}
    if duplicates:
        raise ValueError(f"Duplicate flat targets in mapping: {sorted(duplicates)}")
    if mapping.group_by not in targets:
        raise ValueError(
            f"group_by {mapping.group_by!r} is not one of the flat targets"
        )
    for field in mapping.all_fields:
        mapping.cleaner_for(field)  # raises on unknown or misconfigured cleaners
