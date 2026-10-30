from __future__ import annotations

import logging

import re
import math

import unicodedata
from difflib import SequenceMatcher

import pandas as pd
import datetime as dt

from typing import Any, Iterable


logger = logging.getLogger(__name__)

CNJ_LENGTH = 20
CPF_LENGTH = 11
CNPJ_LENGTH = 14
DEFAULT_FUZZY_THRESHOLD = 0.8

_PATTERN_NON_DIGIT = re.compile(r"\D")
_PATTERN_NON_LETTER = re.compile(r"[^A-Z]")
_PATTERN_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")

_PATTERN_WHITESPACE = re.compile(r"\s+")
_PATTERN_INLINE_SPACE = re.compile(r"[^\S\n]+")
_PATTERN_BLANK_LINES = re.compile(r"\n{3,}")

_CPF_FIRST_WEIGHTS = tuple(range(10, 1, -1))
_CPF_SECOND_WEIGHTS = tuple(range(11, 1, -1))
_CNPJ_FIRST_WEIGHTS = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_CNPJ_SECOND_WEIGHTS = (6,) + _CNPJ_FIRST_WEIGHTS


def is_blank(value: Any) -> bool:
    """Return ``True`` for the many shapes of "no value" pandas can produce."""
    if value is None or value is pd.NaT:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def strip_accents(text: str) -> str:
    """Drop diacritics, keeping the base characters."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def digits_only(value: Any) -> str:
    """Keep only the decimal digits of ``value``'s string form."""
    return "" if is_blank(value) else _PATTERN_NON_DIGIT.sub("", str(value))


def normalize_text(value: Any) -> str | None:
    """Collapse whitespace and trim. Empty results become ``None``."""
    if is_blank(value):
        return None
    return _PATTERN_WHITESPACE.sub(" ", str(value)).strip() or None


def normalize_multiline_text(value: Any) -> str | None:
    """Trim a long free-text field while keeping its paragraph breaks.

    ``normalize_text`` would flatten a whole case summary onto one line; here
    only the whitespace *within* each line is collapsed.
    """
    if is_blank(value):
        return None
    lines = [_PATTERN_INLINE_SPACE.sub(" ", line).strip() for line in str(value).splitlines()]
    return _PATTERN_BLANK_LINES.sub("\n\n", "\n".join(lines).strip()) or None


def normalize_name(value: Any) -> str | None:
    """Upper-case, accent-free, whitespace-normalized name."""
    text = normalize_text(value)
    return strip_accents(text).upper() if text else None


def to_integer(value: Any) -> int | None:
    """Coerce to ``int``, tolerating the floats openpyxl yields for integers."""
    if is_blank(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        logger.debug("Not an integer: %r", value)
        return None
    if math.isnan(number) or not number.is_integer():
        logger.debug("Not an integer: %r", value)
        return None
    return int(number)


def to_iso_date(value: Any) -> str | None:
    """Return an ``YYYY-MM-DD`` string, or ``None`` when not a date.

    Bare numbers are rejected on purpose: an Excel serial and a business
    identifier are indistinguishable at this layer, and silently inventing a
    date is worse than nulling one.
    """
    if is_blank(value):
        return None
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, str):
        parsed = _parse_date_string(value)
        if parsed is None:
            logger.debug("Not a date: %r", value)
        return parsed
    logger.debug("Not a date: %r", value)
    return None


def _parse_date_string(value: str) -> str | None:
    """Parse a date string as ISO first, then as Brazilian ``dd/mm/yyyy``.

    Order matters: ``pd.to_datetime("2026-04-08", dayfirst=True)`` silently
    yields 4 August, so ISO-looking input must be handled before the
    day-first fallback.
    """
    text = value.strip()
    if _PATTERN_ISO_DATE.match(text):
        parsed = pd.to_datetime(text, format="ISO8601", errors="coerce")
    else:
        parsed = pd.to_datetime(text, dayfirst=True, errors="coerce")
    return None if pd.isna(parsed) else parsed.date().isoformat()


def _check_digit(digits: str, weights: Iterable[int]) -> int:
    """Mod-11 check digit shared by the CPF and CNPJ algorithms."""
    remainder = sum(int(digit) * weight for digit, weight in zip(digits, weights)) % 11
    return 0 if remainder < 2 else 11 - remainder


def is_valid_cnj(digits: str) -> bool:
    """Validate a CNJ number against the mod-97 rule of CNJ Resolution 65/2008."""
    if len(digits) != CNJ_LENGTH:
        return False
    sequential, verifier = digits[:7], digits[7:9]
    remainder = digits[9:]
    expected = 98 - int(f"{sequential}{remainder}00") % 97
    return int(verifier) == expected


def clean_cnj(value: Any) -> str | None:
    """Return a formatted ``NNNNNNN-DD.AAAA.J.TR.OOOO`` CNJ, or ``None``."""
    digits = digits_only(value)
    if not is_valid_cnj(digits):
        return None
    return (
        f"{digits[:7]}-{digits[7:9]}.{digits[9:13]}"
        f".{digits[13]}.{digits[14:16]}.{digits[16:]}"
    )


def is_valid_cpf(digits: str) -> bool:
    """Validate the two mod-11 check digits of a CPF."""
    if len(digits) != CPF_LENGTH or len(set(digits)) == 1:
        return False
    first = _check_digit(digits[:9], _CPF_FIRST_WEIGHTS)
    second = _check_digit(digits[:9] + str(first), _CPF_SECOND_WEIGHTS)
    return digits[9:] == f"{first}{second}"


def is_valid_cnpj(digits: str) -> bool:
    """Validate the two mod-11 check digits of a CNPJ."""
    if len(digits) != CNPJ_LENGTH or len(set(digits)) == 1:
        return False
    first = _check_digit(digits[:12], _CNPJ_FIRST_WEIGHTS)
    second = _check_digit(digits[:12] + str(first), _CNPJ_SECOND_WEIGHTS)
    return digits[12:] == f"{first}{second}"


def clean_document(value: Any) -> str | None:
    """Return a formatted CPF or CNPJ, picked by digit count, or ``None``."""
    digits = digits_only(value)
    if is_valid_cpf(digits):
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    if is_valid_cnpj(digits):
        return (
            f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}"
            f"/{digits[8:12]}-{digits[12:]}"
        )
    return None


def _fuzzy_key(value: Any) -> str:
    """Reduce a string to its letters only, for comparison purposes.

    Digits are dropped along with punctuation because the ERP sometimes appends
    the CNPJ to the company name. Those 18 characters are noise against a name
    of ~45, and keeping them cost 0.15 of similarity -- enough to push a valid
    spelling under the threshold. What identifies the entity is the name; its
    document already lives in ``canonical_values``.
    """
    return _PATTERN_NON_LETTER.sub("", strip_accents(str(value)).upper())


def canonicalize(
    value: Any,
    options: Iterable[dict[str, Any]],
    attribute: str = "name",
    threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> Any:
    """Match ``value`` against ``options`` by name and return one attribute.

    Each option is a record whose ``name`` is what gets compared; the match then
    yields ``record[attribute]``. That is how one fuzzy match on a messy company
    name feeds both the canonical name and its CNPJ.

    Comparison ignores punctuation, accents and case, so
    ``"NEON FINANCEIRA CREDITO ... SA"`` and
    ``"Neon Financeira - Crédito, ... S.A."`` collapse onto the same option.
    """
    key = _fuzzy_key(value) if not is_blank(value) else ""
    if not key:
        return None
    best, score = max(
        (
            (option, SequenceMatcher(None, key, _fuzzy_key(option["name"])).ratio())
            for option in options
        ),
        key=lambda pair: pair[1],
    )
    if score < threshold:
        logger.warning(
            "No canonical match for %r (closest %r, score %.2f)",
            value,
            best["name"],
            score,
        )
        return None
    return best.get(attribute)
