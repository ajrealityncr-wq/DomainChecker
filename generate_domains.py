"""Generate review-only domain candidates from domain_generation_config.json."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


PATTERN_SYMBOLS = {"C", "V"}


def load_generation_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("Generation config must be a JSON object")
    return config


def candidate_score(name: str, pattern: str, config: dict[str, Any]) -> int:
    score = 0
    if pattern == "CVCV":
        score += 8
    elif pattern == "CVVC":
        score += 5
    if name[:2] in {str(value).lower() for value in config.get("preferred_starts", [])}:
        score += 5
    if name[-2:] in {str(value).lower() for value in config.get("preferred_ends", [])}:
        score += 3
    if name[0] == name[2] or name[1] == name[3]:
        score -= 4
    if len(set(name)) == len(name):
        score += 3
    if any(fragment in name for fragment in config.get("blocked_fragments", [])):
        score -= 20
    return score


def generate_names(config: dict[str, Any]) -> list[str]:
    length = int(config.get("name_length", 4))
    if length != 4:
        raise ValueError("This generator currently supports exactly 4-letter names")
    patterns = config.get("patterns", ["CVCV"])
    consonants = str(config.get("consonants", "bcdfghjklmnprstvwyz")).lower()
    vowels = str(config.get("vowels", "aeiou")).lower()
    blocked = {str(name).lower() for name in config.get("blocked_names", [])}
    blocked_fragments = {str(value).lower() for value in config.get("blocked_fragments", [])}
    target = int(config.get("candidate_count", 100))
    candidates: list[tuple[int, str]] = []
    seen: set[str] = set()
    for pattern in patterns:
        pattern = str(pattern).upper()
        if len(pattern) != length or set(pattern) - PATTERN_SYMBOLS:
            raise ValueError(f"Invalid phonetic pattern: {pattern}")
        pools = [consonants if symbol == "C" else vowels for symbol in pattern]
        for first in pools[0]:
            for second in pools[1]:
                for third in pools[2]:
                    for fourth in pools[3]:
                        name = (first + second + third + fourth).lower()
                        if name in blocked or name in seen:
                            continue
                        if any(fragment in name for fragment in blocked_fragments):
                            continue
                        if not re.fullmatch(r"[a-z]{4}", name):
                            continue
                        if config.get("reject_repeated_letters", True) and (name[0] == name[2] or name[1] == name[3]):
                            continue
                        seen.add(name)
                        candidates.append((candidate_score(name, pattern, config), name))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in candidates[:target]]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate review-only domain candidates")
    parser.add_argument("--config", default="domain_generation_config.json")
    parser.add_argument("--output", help="Override the config output_file")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    config = load_generation_config(root / args.config)
    names = generate_names(config)
    target = int(config.get("candidate_count", 100))
    if len(names) < target:
        raise ValueError(f"Could only generate {len(names)} unique candidates; requested {target}")
    tlds = [str(tld).lower() for tld in config.get("tlds", [".com"])]
    if not tlds or any(not re.fullmatch(r"\.[a-z]{2,63}", tld) for tld in tlds):
        raise ValueError("tlds must contain values such as .com or .in")
    domains = [name + tlds[0] for name in names]
    output = Path(args.output or config.get("output_file", "domains.json"))
    if not output.is_absolute():
        output = root / output
    with output.open("w", encoding="utf-8") as handle:
        json.dump(domains, handle, indent=2)
        handle.write("\n")
    print(f"Generated {len(domains)} review-only domains in {output}")
    print("No availability API request was made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
