from __future__ import annotations

import re


def parse_abbreviated_number(value: str) -> int | None:
    """Convert values like 1.2K, 3.4M, 5B to an integer when possible."""
    cleaned = value.strip().upper()
    cleaned = cleaned.replace("\u00A0", " ").replace("\u202F", " ")
    cleaned = cleaned.replace("MILLION", "M").replace("THOUSAND", "K").replace("BILLION", "B")
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = cleaned.replace(",", "").replace("+", "")
    match = re.match(r"^(\d+(?:\.\d+)?)([KMB])?$", cleaned)
    if not match:
        return None

    number = float(match.group(1))
    suffix = match.group(2)
    multiplier = 1
    if suffix == "K":
        multiplier = 1_000
    elif suffix == "M":
        multiplier = 1_000_000
    elif suffix == "B":
        multiplier = 1_000_000_000

    return int(number * multiplier)
