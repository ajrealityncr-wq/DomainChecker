"""Generate review-only domain candidates from domain_generation_config.json.

Free-text keywords (for example "rental property, urban homes") are matched against the
industry profiles to infer what the user is building. The inferred industry and the keywords
themselves steer the scoring, and a report records exactly what was inferred and why.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PATTERN_SYMBOLS = {"C", "V"}
MIN_TERM_LENGTH = 3
REPORT_TOP_N = 20


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def tokenize_keywords(text: str | None) -> list[str]:
    """Split free text into lowercase terms, keeping the first occurrence of each term."""
    if not text:
        return []
    terms = [term for term in re.split(r"[^a-z]+", text.lower()) if len(term) >= MIN_TERM_LENGTH]
    return list(dict.fromkeys(terms))


def term_matches_stem(term: str, stem: str) -> bool:
    return len(stem) >= MIN_TERM_LENGTH and (stem in term or term in stem)


def infer_intent(text: str | None, config: dict[str, Any]) -> dict[str, Any]:
    """Decide which industry profile the keywords point to, and say why.

    The profile matching the most keywords wins. Ties go to the configured default industry.
    With no matching keyword the configured industry is kept and the report says so.
    """
    default_industry = str(config.get("industry", "general"))
    profiles = config.get("industry_profiles", {})
    terms = tokenize_keywords(text)
    matches: dict[str, list[str]] = {}
    for name, stems in profiles.items():
        hit_terms = [term for term in terms if any(term_matches_stem(term, stem) for stem in normalize_list(stems))]
        if hit_terms:
            matches[name] = hit_terms
    if matches:
        industry = max(matches, key=lambda name: (len(matches[name]), name == default_industry))
        source = "keywords"
    else:
        industry = default_industry
        source = "config (no keyword matched an industry profile)"
    return {
        "input_terms": terms,
        "industry": industry,
        "source": source,
        "matched_terms": matches.get(industry, []),
        "profile_matches": matches,
        "unmatched_terms": [term for term in terms if term not in matches.get(industry, [])],
        "brand_seeds": terms,
    }


def apply_intent(config: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the config with the inferred industry and the keywords added as brand seeds."""
    applied = dict(config)
    applied["industry"] = intent["industry"]
    applied["brand_keywords"] = list(dict.fromkeys(normalize_list(config.get("brand_keywords", [])) + intent["brand_seeds"]))
    return applied


def industry_stems(config: dict[str, Any]) -> list[str]:
    profiles = config.get("industry_profiles", {})
    return normalize_list(profiles.get(config.get("industry"), []))


def candidate_score(name: str, pattern: str, config: dict[str, Any]) -> int:
    """Positive-only score. Names that break a blocklist never reach scoring; generate_names filters them."""
    score = {"CVCV": 8, "CVVC": 5}.get(pattern, 0)
    if name[:2] in set(normalize_list(config.get("preferred_starts", []))):
        score += 5
    if name[-2:] in set(normalize_list(config.get("preferred_ends", []))):
        score += 3
    if config.get("prefer_unique_letters", True) and len(set(name)) == len(name):
        score += 3
    if any(keyword in name for keyword in normalize_list(config.get("brand_keywords", []))):
        score += 10
    if any(keyword in name for keyword in industry_stems(config)):
        score += 8
    return score


def blocked_substrings(config: dict[str, Any]) -> list[str]:
    legal = config.get("legal", {})
    terms = normalize_list(config.get("avoid_words", [])) + normalize_list(config.get("blocked_fragments", []))
    if isinstance(legal, dict):
        terms += normalize_list(legal.get("blocked_terms", []))
    terms += normalize_list(config.get("blocked_brands", []))
    return list(dict.fromkeys(terms))


def blocked_suffixes(config: dict[str, Any]) -> list[str]:
    legal = config.get("legal", {})
    suffixes = normalize_list(config.get("blocked_suffixes", []))
    if isinstance(legal, dict):
        suffixes += normalize_list(legal.get("blocked_suffixes", []))
    return list(dict.fromkeys(suffixes))


def score_candidates(config: dict[str, Any]) -> list[tuple[int, str]]:
    """Every acceptable 4-letter name with its score, best first."""
    length = int(config.get("name_length", 4))
    if length != 4:
        raise ValueError("This generator currently supports exactly 4-letter names")
    patterns = config.get("patterns", ["CVCV"])
    consonants = str(config.get("consonants", "lmnrstvzkpbdghcfwy")).lower()
    vowels = str(config.get("vowels", "aeiou")).lower()
    blocked = set(normalize_list(config.get("blocked_names", [])))
    substrings = blocked_substrings(config)
    suffixes = blocked_suffixes(config)
    quality_floor = int(config.get("min_quality_score", -10))
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
                        name = first + second + third + fourth
                        if name in blocked or name in seen:
                            continue
                        if any(fragment in name for fragment in substrings):
                            continue
                        if any(name.endswith(suffix) for suffix in suffixes):
                            continue
                        if config.get("reject_repeated_letters", True) and (name[0] == name[2] or name[1] == name[3]):
                            continue
                        score = candidate_score(name, pattern, config)
                        if score < quality_floor:
                            continue
                        seen.add(name)
                        candidates.append((score, name))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates


def generate_names(config: dict[str, Any]) -> list[str]:
    target = int(config.get("candidate_count", 100))
    return [name for _, name in score_candidates(config)[:target]]


def build_domains(names: list[str], config: dict[str, Any]) -> list[str]:
    tlds = [str(tld).lower() for tld in config.get("tlds", [".com"])]
    if not tlds or any(not re.fullmatch(r"\.[a-z]{2,63}", tld) for tld in tlds):
        raise ValueError("tlds must contain values such as .com or .in")
    preferred_tlds = [str(tld).lower() for tld in config.get("preferred_tlds", tlds)]
    if not preferred_tlds or any(tld not in tlds for tld in preferred_tlds):
        raise ValueError("preferred_tlds must contain values from tlds")
    if config.get("expand_tlds", False):
        return [name + tld for name in names for tld in preferred_tlds]
    return [name + preferred_tlds[index % len(preferred_tlds)] for index, name in enumerate(names)]


def build_report(args: argparse.Namespace, intent: dict[str, Any], config: dict[str, Any], scored: list[tuple[int, str]], domains: list[str], output: Path) -> dict[str, Any]:
    return {
        "generated_at": utc_now(),
        "keywords_input": args.keywords,
        "intent": intent,
        "industry": config.get("industry"),
        "industry_stems": industry_stems(config),
        "brand_keywords": config.get("brand_keywords", []),
        "candidate_pool_size": len(scored),
        "written_count": len(domains),
        "output_file": str(output),
        "top_candidates": [{"domain": domains[index] if index < len(domains) else None, "name": name, "score": score} for index, (score, name) in enumerate(scored[:REPORT_TOP_N])],
        "note": "Review-only. No availability API request was made.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate review-only domain candidates")
    parser.add_argument("--config", default="domain_generation_config.json")
    parser.add_argument("--output", help="Override the config output_file")
    parser.add_argument("--keywords", help='Free-text keywords describing the business, e.g. "rental property, urban homes"')
    parser.add_argument("--report", help="Path for the JSON report (default: output/generation_report.json)")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent

    config = load_generation_config(root / args.config)
    intent = infer_intent(args.keywords, config)
    config = apply_intent(config, intent)

    print(f"Industry inferred: {intent['industry']} ({intent['source']})")
    if intent["matched_terms"]:
        print(f"Matched keywords: {', '.join(intent['matched_terms'])}")
    if intent["unmatched_terms"]:
        print(f"Keywords used only as brand seeds: {', '.join(intent['unmatched_terms'])}")

    scored = score_candidates(config)
    names = [name for _, name in scored[: int(config.get("candidate_count", 100))]]
    target = int(config.get("candidate_count", 100))
    if len(names) < target:
        print(f"Could only generate {len(names)} unique candidates; requested {target}. This may be due to the current quality/legal filters.")
    domains = build_domains(names, config)

    output = Path(args.output or config.get("output_file", "generated_review_domains.json"))
    if not output.is_absolute():
        output = root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(domains, handle, indent=2)
        handle.write("\n")

    report_path = Path(args.report) if args.report else root / "output" / "generation_report.json"
    if not report_path.is_absolute():
        report_path = root / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(build_report(args, intent, config, scored, domains, output), handle, indent=2)
        handle.write("\n")

    print(f"Generated {len(domains)} review-only domains in {output}")
    print(f"Report of what was inferred and why: {report_path}")
    print("No availability API request was made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
