"""Generate review-only domain candidates from domain_generation_config.json."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


PATTERN_SYMBOLS = {"C", "V"}


def normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.lower()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).lower() for item in value if str(item).strip()]
    return [str(value).lower()]


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

    preferred_starts = set(normalize_list(config.get("preferred_starts", [])))
    preferred_ends = set(normalize_list(config.get("preferred_ends", [])))
    blocked_fragments = {str(value).lower() for value in normalize_list(config.get("blocked_fragments", []))}
    avoid_words = {str(value).lower() for value in normalize_list(config.get("avoid_words", []))}
    brand_keywords = normalize_list(config.get("brand_keywords", []))
    industry_keywords = normalize_list(config.get("industry_keywords", []))
    blocked_brands = {str(value).lower() for value in normalize_list(config.get("blocked_brands", []))}
    blocked_suffixes = [str(value).lower() for value in normalize_list(config.get("blocked_suffixes", []))]
    legal = config.get("legal", {})
    blocked_terms = normalize_list(legal.get("blocked_terms", [])) if isinstance(legal, dict) else []
    blocked_suffixes.extend(normalize_list(legal.get("blocked_suffixes", [])) if isinstance(legal, dict) else [])

    if name[:2] in preferred_starts:
        score += 5
    if name[-2:] in preferred_ends:
        score += 3
    if name[0] == name[2] or name[1] == name[3]:
        score -= 4
    if len(set(name)) == len(name):
        score += 3
    if any(fragment in name for fragment in blocked_fragments):
        score -= 20
    if any(keyword in name for keyword in brand_keywords):
        score += 10
    if any(keyword in name for keyword in industry_keywords):
        score += 8
    if any(word in name for word in avoid_words) or any(term in name for term in blocked_terms):
        score -= 12
    if any(name.endswith(suffix) for suffix in blocked_suffixes):
        score -= 15
    if any(brand in name for brand in blocked_brands):
        score -= 30
    if config.get("prefer_unique_letters", True) and len(set(name)) == len(name):
        score += 2
    if config.get("prefer_easy_pronunciation", True):
        vowel_count = sum(1 for char in name if char in "aeiou")
        if vowel_count >= 2:
            score += 1
        if re.search(r"[bcdfghjklmnprstvwxyz]{3,}", name):
            score -= 2
    return score


def generate_names(config: dict[str, Any]) -> list[str]:
    length = int(config.get("name_length", 4))
    if length != 4:
        raise ValueError("This generator currently supports exactly 4-letter names")
    patterns = config.get("patterns", ["CVCV"])
    consonants = str(config.get("consonants", "bcdfghjklmnprstvwyz")).lower()
    vowels = str(config.get("vowels", "aeiou")).lower()
    blocked = {str(name).lower() for name in normalize_list(config.get("blocked_names", []))}
    blocked_fragments = {str(value).lower() for value in normalize_list(config.get("blocked_fragments", []))}
    avoid_words = {str(value).lower() for value in normalize_list(config.get("avoid_words", []))}
    blocked_brands = {str(value).lower() for value in normalize_list(config.get("blocked_brands", []))}
    blocked_suffixes = [str(value).lower() for value in normalize_list(config.get("blocked_suffixes", []))]
    legal = config.get("legal", {})
    if isinstance(legal, dict):
        blocked.update(normalize_list(legal.get("blocked_terms", [])))
        blocked_suffixes.extend(normalize_list(legal.get("blocked_suffixes", [])))
    target = int(config.get("candidate_count", 100))
    quality_floor = int(config.get("min_quality_score", config.get("quality_floor", -10)))
    premium_gate = config.get("premium_gate", {})
    if isinstance(premium_gate, dict) and premium_gate.get("enabled", False):
        quality_floor = max(quality_floor, int(premium_gate.get("min_score", quality_floor)))
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
                        if any(word in name for word in avoid_words):
                            continue
                        if any(brand in name for brand in blocked_brands):
                            continue
                        if any(name.endswith(suffix) for suffix in blocked_suffixes):
                            continue
                        if not re.fullmatch(r"[a-z]{4}", name):
                            continue
                        if config.get("reject_repeated_letters", True) and (name[0] == name[2] or name[1] == name[3]):
                            continue
                        score = candidate_score(name, pattern, config)
                        if score < quality_floor:
                            continue
                        seen.add(name)
                        candidates.append((score, name))
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
        print(f"Could only generate {len(names)} unique candidates; requested {target}. This may be due to the current quality/legal filters.")
    tlds = [str(tld).lower() for tld in config.get("tlds", [".com"])]
    if not tlds or any(not re.fullmatch(r"\.[a-z]{2,63}", tld) for tld in tlds):
        raise ValueError("tlds must contain values such as .com or .in")
    preferred_tlds = [str(tld).lower() for tld in config.get("preferred_tlds", tlds)]
    if not preferred_tlds or any(tld not in tlds for tld in preferred_tlds):
        raise ValueError("preferred_tlds must contain values from tlds")
    if config.get("expand_tlds", False):
        domains = [name + tld for name in names for tld in preferred_tlds]
    else:
        domains = [name + preferred_tlds[index % len(preferred_tlds)] for index, name in enumerate(names)]
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
