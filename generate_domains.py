"""Write review-only domain candidates for a keyword, without calling GoDaddy.

    python generate_domains.py --keywords "automation"

Writes generated_review_domains.json and output/generation_report.json. The same name rules as
the web search apply, and the report says which industry (if any) the keyword matched.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import search_service as ss
import settings
from history import DomainHistory


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description="Write review-only domain candidates for a keyword")
    parser.add_argument("--keywords", required=True, help='Keyword, e.g. "automation" or "real estate"')
    parser.add_argument("--extensions", nargs="+", default=list(settings.DEFAULT_TLDS), help="Extensions to use (default: .com .in)")
    args = parser.parse_args()

    try:
        request = ss.SearchRequest(keyword=args.keywords, tlds=tuple(args.extensions))
    except ValueError as exc:
        print(f"Input error: {exc}")
        return 2

    # A preview only: the names are not reserved, so the web search can still check them.
    industry, domains = ss.preview_domains(request, DomainHistory())
    if not domains:
        print("Stopped: No new names are left for this keyword. Try a different keyword or extension.")
        return 1
    output = settings.OUTPUT_DIRECTORY / "generated_review_domains.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(domains, indent=2) + "\n", encoding="utf-8")

    report = {
        "generated_at": utc_now(),
        "keyword": request.keyword.strip(),
        "keyword_words": ss.tokenize_keywords(request.keyword),
        "industry": industry,
        "industry_note": "Industry vocabulary was used." if industry else "No industry profile matched; names use the keyword and affixes only.",
        "extensions": list(request.tlds),
        "written_count": len(domains),
        "output_file": str(output),
        "note": "Review-only. No availability request was made.",
    }
    report_path = settings.OUTPUT_DIRECTORY / "generation_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"Industry: {industry or 'none matched'}")
    print(f"Generated {len(domains)} review-only domains in {output}")
    print(f"Report: {report_path}")
    print("No availability request was made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
