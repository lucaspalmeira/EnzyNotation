"""Parsing and normalization of Enzyme Commission numbers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from enzynotation.exceptions import ECNumberError

_EC_PREFIX = re.compile(r"^EC\s*:?[\s]*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ECNumber:
    """A normalized four-level EC number, possibly incomplete."""

    parts: tuple[str, str, str, str]

    @property
    def is_complete(self) -> bool:
        """Whether all four EC levels are specified."""

        return "-" not in self.parts

    @property
    def depth(self) -> int:
        """Number of specified hierarchical levels."""

        return next((index for index, part in enumerate(self.parts) if part == "-"), 4)

    def __str__(self) -> str:
        return ".".join(self.parts)


def parse_ec(value: str, *, allow_partial: bool = True) -> ECNumber:
    """Parse an EC number and return its normalized four-part representation.

    The optional ``EC`` or ``EC:`` prefix is accepted. Short hierarchical forms
    such as ``1.2`` are padded to ``1.2.-.-`` when partial numbers are allowed.
    """

    if not isinstance(value, str):
        raise ECNumberError("EC number must be a string")

    cleaned = _EC_PREFIX.sub("", value.strip())
    if not cleaned:
        raise ECNumberError("EC number is empty")

    raw_parts = cleaned.split(".")
    if len(raw_parts) > 4:
        raise ECNumberError(f"EC number has more than four levels: {value!r}")
    if any(not part for part in raw_parts):
        raise ECNumberError(f"EC number contains an empty level: {value!r}")

    normalized: list[str] = []
    missing_seen = False
    for part in raw_parts:
        if part == "-":
            missing_seen = True
            normalized.append(part)
            continue
        if missing_seen:
            raise ECNumberError(
                f"EC number specifies a level after an unknown level: {value!r}"
            )
        if not part.isascii() or not part.isdecimal():
            raise ECNumberError(f"EC level must be a positive integer: {part!r}")
        number = int(part)
        if number < 1:
            raise ECNumberError(f"EC level must be a positive integer: {part!r}")
        normalized.append(str(number))

    if len(normalized) < 4:
        if not allow_partial:
            raise ECNumberError(f"EC number is incomplete: {value!r}")
        normalized.extend("-" for _ in range(4 - len(normalized)))

    if normalized[0] == "-":
        raise ECNumberError(f"EC number must specify its top-level class: {value!r}")

    parts = (normalized[0], normalized[1], normalized[2], normalized[3])
    ec = ECNumber(parts)
    if not allow_partial and not ec.is_complete:
        raise ECNumberError(f"EC number is incomplete: {value!r}")
    return ec


def normalize_ec(value: str, *, allow_partial: bool = True) -> str:
    """Return a canonical EC-number string."""

    return str(parse_ec(value, allow_partial=allow_partial))
