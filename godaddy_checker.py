"""Check domain availability using the official GoDaddy Domains API."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - requirements.txt installs this package
    load_dotenv = None


MAX_BATCH_SIZE = 25
DEFAULT_API_URL = "https://api.godaddy.com/v3/domains/check-availability"
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
CSV_FIELDS = ["domain", "available", "price", "currency", "checked_at"]
EXCEL_HEADERS = ["input_domain", "domain", "status", "reason", "available", "price", "currency", "checked_at", "error"]


DEFAULT_CONFIG: dict[str, Any] = {
    "input_file": "domains.json",
    "output_directory": "output",
    "batch_size": 25,
    "optimize_for": "ACCURACY",
    "timeout_seconds": 30,
    "delay_between_batches_seconds": 1,
    "max_retries": 5,
    "retry_backoff_seconds": 2,
    "resume_enabled": True,
    "checkpoint_file": "checkpoint.json",
    "normalize_domains": True,
    "remove_duplicates": True,
    "validate_domains": True,
    "save_available": True,
    "save_taken": True,
    "save_invalid": True,
    "save_errors": True,
    "save_unchecked": True,
    "save_csv": True,
    "save_json": True,
    "save_buy_now": True,
    "save_logs": True,
    "log_level": "INFO",
    "strip_protocol": True,
    "remove_www": False,
    "allowed_tlds": [],
    "max_price": None,
    "available_only": True,
    "sort_available_by": "domain",
    "error_output": "error_domains",
    "godaddy": {
        "base_url": "https://api.godaddy.com",
        "availability_endpoint": "/v3/domains/check-availability",
        "optimize_for": "ACCURACY",
    },
    "buy_now": {"enabled": True, "require_available": True, "require_api_success": True},
}


@dataclass
class CheckResult:
    domain: str
    available: bool | None
    price: Any = None
    currency: str | None = None
    checked_at: str = ""
    error: str | None = None
    api_success: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise ValueError("config.json must contain a JSON object")
        config = deep_merge(config, loaded)
    config["batch_size"] = min(MAX_BATCH_SIZE, max(1, int(config["batch_size"])))
    config["optimize_for"] = str(config["optimize_for"]).upper()
    if config["optimize_for"] not in {"ACCURACY", "SPEED"}:
        raise ValueError("optimize_for must be ACCURACY or SPEED")
    return config


def apply_cli_overrides(config: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    overrides = {
        "input_file": args.input_file,
        "batch_size": args.batch_size,
        "optimize_for": args.optimize_for,
        "timeout_seconds": args.timeout,
        "delay_between_batches_seconds": args.delay,
        "max_retries": args.retries,
    }
    for key, value in overrides.items():
        if value is not None:
            config[key] = value
    if args.resume is not None:
        config["resume_enabled"] = args.resume
    config["batch_size"] = min(MAX_BATCH_SIZE, max(1, int(config["batch_size"])))
    config["optimize_for"] = str(config["optimize_for"]).upper()
    return config


def load_domains(path: Path) -> list[Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        if all(isinstance(item, dict) and isinstance(item.get("domain"), str) for item in data):
            return [item["domain"] for item in data]
        return data
    if isinstance(data, dict) and isinstance(data.get("domains"), list):
        return data["domains"]
    raise ValueError("Input JSON must be an array or an object containing a 'domains' array")


def normalize_domain(value: Any, config: dict[str, Any]) -> tuple[str | None, str | None]:
    if not isinstance(value, str):
        return None, str(value)
    original = value
    domain = value.strip().lower()
    if config.get("strip_protocol", True):
        domain = re.sub(r"^https?://", "", domain)
        domain = domain.split("/", 1)[0]
    if config.get("remove_www", False):
        domain = re.sub(r"^www\.", "", domain)
    if not domain or not DOMAIN_RE.fullmatch(domain):
        return None, original
    allowed_tlds = config.get("allowed_tlds") or []
    if allowed_tlds and not any(domain.endswith(str(tld).lower()) for tld in allowed_tlds):
        return None, original
    return domain, original if domain != original else None


def prepare_domains(values: Iterable[Any], config: dict[str, Any]) -> tuple[list[str], list[str], list[dict[str, str]]]:
    valid: list[str] = []
    invalid: list[str] = []
    normalization_log: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        domain, original = normalize_domain(value, config)
        if domain is None:
            invalid.append(original or "")
            continue
        if original is not None:
            normalization_log.append({"original": original, "normalized": domain})
        if config.get("remove_duplicates", True) and domain in seen:
            continue
        seen.add(domain)
        valid.append(domain)
    return valid, invalid, normalization_log


def batches(domains: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(domains), size):
        yield domains[index : index + size]


def api_url(config: dict[str, Any]) -> str:
    environment_url = os.getenv("GODADDY_API_URL")
    if environment_url:
        return environment_url.rstrip("/")
    godaddy = config["godaddy"]
    return str(godaddy.get("base_url", "")).rstrip("/") + str(
        godaddy.get("availability_endpoint", "/v3/domains/check-availability")
    )


def load_pat(cli_pat: str | None, config: dict[str, Any], root: Path) -> str | None:
    if load_dotenv:
        load_dotenv()
    pat = cli_pat or os.getenv("GODADDY_PAT")
    if pat:
        return pat
    secrets_path = Path(str(config.get("secrets_file", "GODADDY_PAT.json")))
    if not secrets_path.is_absolute():
        secrets_path = root / secrets_path
    try:
        with secrets_path.open(encoding="utf-8") as handle:
            secrets = json.load(handle)
        if isinstance(secrets, dict) and isinstance(secrets.get("GODADDY_PAT"), str) and secrets["GODADDY_PAT"].strip():
            return secrets["GODADDY_PAT"].strip()
    except (OSError, ValueError):
        pass
    return config.get("godaddy_pat") or config.get("godaddy", {}).get("pat")


def check_batch(batch: list[str], config: dict[str, Any], pat: str, logger: logging.Logger) -> tuple[list[CheckResult], str | None]:
    payload = {"domains": batch, "optimizeFor": config["optimize_for"]}
    headers = {"Authorization": f"Bearer {pat}", "Content-Type": "application/json", "Accept": "application/json"}
    retries = int(config["max_retries"])
    for attempt in range(retries + 1):
        try:
            response = requests.post(api_url(config), headers=headers, json=payload, timeout=float(config["timeout_seconds"]))
        except requests.RequestException as exc:
            if attempt < retries:
                delay = float(config["retry_backoff_seconds"]) * (2**attempt)
                logger.warning("Temporary network error; retrying in %.1f seconds: %s", delay, exc)
                time.sleep(delay)
                continue
            return [], f"network error: {exc}"
        if response.status_code in RETRYABLE_STATUS_CODES and attempt < retries:
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else float(config["retry_backoff_seconds"]) * (2**attempt)
            logger.warning("GoDaddy returned HTTP %s; retrying in %.1f seconds", response.status_code, delay)
            time.sleep(delay)
            continue
        if response.status_code in {401, 403}:
            return [], f"authentication/authorization failed (HTTP {response.status_code})"
        if not response.ok:
            return [], f"GoDaddy returned HTTP {response.status_code}"
        try:
            body = response.json()
        except ValueError:
            return [], "GoDaddy returned invalid JSON"
        if isinstance(body, dict) and isinstance(body.get("items"), list):
            body = body["items"]
        if not isinstance(body, list):
            return [], "GoDaddy response must be an array or an object containing an items array"
        return parse_response(body, batch)
    return [], "request retries exhausted"


def parse_response(items: list[Any], requested: list[str]) -> tuple[list[CheckResult], str | None]:
    requested_set = set(requested)
    results: list[CheckResult] = []
    returned: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("domain"), str):
            return [], "malformed response item"
        domain = item["domain"].strip().lower()
        if domain not in requested_set or domain in returned:
            return [], f"unexpected or duplicate domain in response: {domain}"
        returned.add(domain)
        available = item.get("available")
        if not isinstance(available, bool):
            return [], f"missing or invalid availability for {domain}"
        results.append(CheckResult(domain=domain, available=available, price=item.get("price"), currency=item.get("currency"), checked_at=utc_now(), api_success=True, raw=item))
    missing = requested_set - returned
    if missing:
        return [], f"response omitted domains: {', '.join(sorted(missing))}"
    return results, None


def checkpoint_path(config: dict[str, Any], root: Path) -> Path:
    path = Path(str(config["checkpoint_file"]))
    return path if path.is_absolute() else root / path


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"processed": {}, "api_requests": 0}
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {"processed": {}, "api_requests": 0}
    except (OSError, ValueError):
        return {"processed": {}, "api_requests": 0}


def save_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(checkpoint, handle, indent=2)
    temporary.replace(path)


def record_for_output(result: CheckResult) -> dict[str, Any]:
    return {key: value for key, value in asdict(result).items() if key not in {"error", "api_success", "raw"} and value is not None}


def write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = list(CSV_FIELDS)
    for record in records:
        for field in record:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in fields})


def markdown_value(value: Any) -> str:
    return str(value if value is not None and value != "" else "-").replace("|", "\\|").replace("\n", " ")


def write_markdown(path: Path, title: str, rows: list[dict[str, Any]]) -> None:
    headers = ["Domain", "Status", "Reason", "Available", "Price", "Currency", "Checked At", "Error"]
    lines = [
        f"# {title}",
        "",
        f"Total: **{len(rows)}**",
        "",
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join([
            markdown_value(row.get("domain") or row.get("input_domain")),
            markdown_value(row.get("status")),
            markdown_value(row.get("reason")),
            markdown_value(row.get("available")),
            markdown_value(row.get("price")),
            markdown_value(row.get("currency")),
            markdown_value(row.get("checked_at")),
            markdown_value(row.get("error")),
        ]) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_excel(path: Path, rows: list[dict[str, Any]]) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.worksheet.table import Table, TableStyleInfo
    except ImportError as exc:
        raise RuntimeError("Excel output requires openpyxl; install it with: pip install openpyxl") from exc

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Domain Results"
    sheet.append(EXCEL_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    for row in rows:
        sheet.append([row.get(header, "") for header in EXCEL_HEADERS])
    sheet.freeze_panes = "A2"
    table = Table(displayName="DomainResultsTable", ref=f"A1:I{max(sheet.max_row, 1)}")
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    sheet.add_table(table)
    widths = {"A": 24, "B": 30, "C": 14, "D": 30, "E": 12, "F": 14, "G": 12, "H": 24, "I": 50}
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    for row in sheet.iter_rows(min_row=2):
        status = row[2].value
        fill = {"AVAILABLE": "C6EFCE", "TAKEN": "FFC7CE", "INVALID": "FFEB9C", "ERROR": "F4CCCC", "UNCHECKED": "D9EAF7"}.get(status)
        if fill:
            row[2].fill = PatternFill("solid", fgColor=fill)
    workbook.save(path)


def meets_budget(record: dict[str, Any], config: dict[str, Any]) -> bool:
    max_price = config.get("max_price")
    required_currency = config.get("max_price_currency")
    if max_price is None:
        return True
    price = record.get("price")
    currency = record.get("currency")
    return (
        price is not None
        and currency is not None
        and str(currency).upper() == str(required_currency).upper()
        and isinstance(price, (int, float))
        and price <= float(max_price)
    )


def save_outputs(root: Path, config: dict[str, Any], valid: list[str], invalid: list[str], results: list[CheckResult], errors: list[dict[str, Any]], unchecked: list[str], started_at: str, requests_made: int, normalization_log: list[dict[str, str]]) -> None:
    output = root / str(config["output_directory"])
    output.mkdir(parents=True, exist_ok=True)
    available = [record_for_output(item) for item in results if item.available is True]
    taken = [record_for_output(item) for item in results if item.available is False]
    sort_mode = config.get("sort_available_by", "domain")
    if sort_mode == "domain":
        available.sort(key=lambda item: item["domain"])
    elif sort_mode == "price":
        available.sort(key=lambda item: (item.get("price") is None, item.get("price", 0)))
    invalid = list(dict.fromkeys(invalid))
    if config.get("save_json", True):
        write_json(output / "available_domains.json", available)
        write_json(output / "taken_domains.json", taken)
        write_json(output / "invalid_domains.json", invalid)
        write_json(output / "error_domains.json", errors)
        write_json(output / "unchecked_domains.json", unchecked)
        write_json(output / "normalization_log.json", normalization_log)
    if config.get("save_csv", True):
        write_csv(output / "available_domains.csv", available)
        write_csv(output / "taken_domains.csv", taken)
        write_csv(output / "invalid_domains.csv", [{"domain": value} for value in invalid])
        write_csv(output / "error_domains.csv", errors)
        write_csv(output / "unchecked_domains.csv", [{"domain": value} for value in unchecked])
    excel_rows = []
    result_map = {item.domain: item for item in results}
    error_map = {item.get("domain"): item for item in errors if item.get("domain")}
    for domain in valid:
        result = result_map.get(domain)
        error = error_map.get(domain)
        if result and result.available is True:
            status = "AVAILABLE"
            reason = "GODADDY_CONFIRMED_AVAILABLE"
        elif result and result.available is False:
            status = "TAKEN"
            reason = "GODADDY_CONFIRMED_UNAVAILABLE"
        elif error:
            status = "ERROR"
            reason = error.get("reason", "API_CHECK_FAILED")
        else:
            status = "UNCHECKED"
            reason = "NOT_PROCESSED"
        excel_rows.append({
            "input_domain": domain,
            "domain": domain,
            "status": status,
            "reason": reason,
            "available": result.available if result else "",
            "price": result.price if result else "",
            "currency": result.currency if result else "",
            "checked_at": result.checked_at if result else (error or {}).get("checked_at", ""),
            "error": (error or {}).get("error", ""),
        })
    excel_rows.extend({
        "input_domain": value,
        "domain": "",
        "status": "INVALID",
        "reason": "INVALID_DOMAIN_SYNTAX",
        "available": False,
        "error": "Domain is not a valid domain name and was not sent to GoDaddy.",
    } for value in invalid)
    if config.get("save_markdown", True):
        write_markdown(output / "domain_results.md", "Domain Check Results", excel_rows)
        write_markdown(output / "available_domains.md", "Available Domains", [
            row for row in excel_rows if row["status"] == "AVAILABLE"
        ])
        write_markdown(output / "budget_available_domains.md", "Budget-Qualified Available Domains", [
            row for row in excel_rows
            if row["status"] == "AVAILABLE" and meets_budget(row, config)
        ])
    if config.get("save_excel", True):
        write_excel(output / "domain_results.xlsx", excel_rows)
        write_excel(output / "available_domains.xlsx", [row for row in excel_rows if row["status"] == "AVAILABLE"])
        write_excel(output / "budget_available_domains.xlsx", [
            row for row in excel_rows
            if row["status"] == "AVAILABLE" and meets_budget(row, config)
        ])
    if config.get("save_buy_now", True):
        buy_now = [item for item in available if meets_budget(item, config)]
        with (output / "BUY_NOW.txt").open("w", encoding="utf-8") as handle:
            handle.write("\n".join(item["domain"] for item in buy_now))
            if buy_now:
                handle.write("\n")
    summary = {
        "total_input": len(valid) + len(invalid),
        "unique_domains": len(valid),
        "invalid_domains": len(invalid),
        "processed": len(results),
        "available": len(available),
        "taken": len(taken),
        "errors": len(errors),
        "unchecked": len(unchecked),
        "api_requests": requests_made,
        "started_at": started_at,
        "finished_at": utc_now(),
    }
    write_json(output / "summary.json", summary)


def configure_logging(root: Path, config: dict[str, Any]) -> logging.Logger:
    logger = logging.getLogger("godaddy_checker")
    logger.handlers.clear()
    logger.setLevel(getattr(logging, str(config.get("log_level", "INFO")).upper(), logging.INFO))
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
    if config.get("save_logs", True):
        output = root / str(config["output_directory"])
        output.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(output / "checker.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger


def print_summary(output: Path, summary: dict[str, Any]) -> None:
    print("\n========================================")
    print("GO DADDY DOMAIN CHECK COMPLETE")
    print("========================================")
    for label, key in [("Input domains", "total_input"), ("Unique valid", "unique_domains"), ("Invalid", "invalid_domains"), ("Checked", "processed"), ("Available", "available"), ("Taken", "taken"), ("Errors", "errors"), ("API requests", "api_requests")]:
        print(f"{label:<20}: {summary[key]}")
    print(f"\nAvailable domains saved to:\n{output / 'available_domains.json'}")
    print(f"\nBUY NOW list:\n{output / 'BUY_NOW.txt'}")
    print("========================================")
    print("IMPORTANT:")
    print("GoDaddy availability results are for search purposes. Re-verify availability and price at checkout.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check domain availability with the official GoDaddy API")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--input", dest="input_file")
    parser.add_argument("--pat", help=argparse.SUPPRESS)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--optimize-for", choices=["ACCURACY", "SPEED"])
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--delay", type=float)
    parser.add_argument("--retries", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--force-recheck", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(__file__).resolve().parent
    try:
        config = apply_cli_overrides(load_config(root / args.config), args)
        logger = configure_logging(root, config)
        values = load_domains(root / str(config["input_file"]))
        valid, invalid, normalization_log = prepare_domains(values, config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Configuration/input error: {exc}", file=sys.stderr)
        return 2

    expected = (len(valid) + config["batch_size"] - 1) // config["batch_size"]
    print(f"Domains: {len(valid)} | Batch size: {config['batch_size']} | Expected API requests: {expected}")
    print(f"Valid unique: {len(valid)} | Invalid: {len(invalid)}")
    if args.dry_run:
        save_outputs(root, config, valid, invalid, [], [], [], utc_now(), 0, normalization_log)
        print("Dry run complete. No GoDaddy API request was made.")
        print(f"Markdown report saved to: {root / str(config['output_directory']) / 'domain_results.md'}")
        return 0

    pat = load_pat(args.pat, config, root)
    if not pat:
        missing_pat_errors = [
            {
                "domain": domain,
                "status": "ERROR",
                "reason": "MISSING_GODADDY_PAT",
                "error": "GoDaddy PAT is not configured; API check was not attempted.",
                "checked_at": utc_now(),
            }
            for domain in valid
        ]
        save_outputs(root, config, valid, invalid, [], missing_pat_errors, [], utc_now(), 0, normalization_log)
        print("Missing GoDaddy PAT. Set GODADDY_PAT, add it to .env, or pass --pat.", file=sys.stderr)
        print(f"Results with reasons saved to: {root / str(config['output_directory']) / 'domain_results.xlsx'}", file=sys.stderr)
        return 2
    if args.test:
        if not valid:
            print("Test failed: no valid domain is available in the input file.", file=sys.stderr)
            return 2
        results, error = check_batch(valid[:1], config, pat, logger)
        if error:
            print(f"API test failed: {error}", file=sys.stderr)
            return 1
        print(f"API test succeeded for {results[0].domain}.")
        return 0

    started_at = utc_now()
    checkpoint_file = checkpoint_path(config, root)
    checkpoint = load_checkpoint(checkpoint_file) if config.get("resume_enabled") and not args.force_recheck else {"processed": {}, "api_requests": 0}
    processed: dict[str, dict[str, Any]] = checkpoint.setdefault("processed", {})
    results = [CheckResult(**{key: value for key, value in record.items() if key in {"domain", "available", "price", "currency", "checked_at", "error", "api_success"}}) for record in processed.values() if record.get("available") in {True, False}]
    errors = list(checkpoint.get("errors", []))
    unchecked: list[str] = []
    requests_made = int(checkpoint.get("api_requests", 0))
    try:
        for batch_number, batch in enumerate(batches([domain for domain in valid if domain not in processed], config["batch_size"]), 1):
            logger.info("Processing batch %s | %s domains", batch_number, len(batch))
            batch_results, error = check_batch(batch, config, pat, logger)
            requests_made += 1
            if error:
                logger.error("Batch failed: %s", error)
                errors.extend({
                    "domain": domain,
                    "status": "ERROR",
                    "reason": "API_CHECK_FAILED",
                    "error": error,
                    "checked_at": utc_now(),
                } for domain in batch)
            else:
                for result in batch_results:
                    processed[result.domain] = record_for_output(result) | {"api_success": True}
                    results = [item for item in results if item.domain != result.domain]
                    results.append(result)
                    logger.info("%s: %s", "Available" if result.available else "Taken", result.domain)
                checkpoint["processed"] = processed
                checkpoint["api_requests"] = requests_made
                checkpoint["errors"] = errors
                if config.get("resume_enabled"):
                    save_checkpoint(checkpoint_file, checkpoint)
            if batch_number < expected:
                time.sleep(float(config["delay_between_batches_seconds"]))
    except KeyboardInterrupt:
        checkpoint["processed"] = processed
        checkpoint["api_requests"] = requests_made
        checkpoint["errors"] = errors
        save_checkpoint(checkpoint_file, checkpoint)
        save_outputs(root, config, valid, invalid, results, errors, unchecked, started_at, requests_made, normalization_log)
        print("\nProcess interrupted. Progress has been saved; run again to resume.")
        return 130

    save_outputs(root, config, valid, invalid, results, errors, unchecked, started_at, requests_made, normalization_log)
    summary_path = root / str(config["output_directory"]) / "summary.json"
    with summary_path.open(encoding="utf-8") as handle:
        summary = json.load(handle)
    print_summary(summary_path.parent, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())