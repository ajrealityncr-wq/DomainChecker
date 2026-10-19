# DomainChecker

Finds domain names that are available to register. It has two parts:

- `generate_domains.py` builds review-only candidate names. It reads free-text keywords, works out what kind of business they describe, and writes a report explaining that inference.
- `godaddy_checker.py` checks candidates through the official GoDaddy Domains API. It never purchases domains. A domain is classified as available only when GoDaddy returns `available: true`.

## Setup

```powershell
python --version            # 3.10+
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

For tests, also run `pip install -r requirements-dev.txt`.

Create a GoDaddy Personal Access Token with permission to check domains, then put it in `.env` as `GODADDY_PAT=...`. Do not commit `.env`.

Credential precedence is `--pat` (warned about, since it can end up in shell history), then the `GODADDY_PAT` environment variable (including `.env`), then `GODADDY_PAT.json`, then `godaddy_pat` in `config.json`. Using `config.json` prints a warning because that file is tracked. `GODADDY_API_URL` can override the endpoint for controlled environments.

## Step 1: Generate candidates from keywords

Describe the business in free text. Keywords are matched against the industry profiles in `domain_generation_config.json`.

```powershell
python generate_domains.py --keywords "rental property, urban homes"
```

The command prints what it inferred:

```
Industry inferred: real_estate (keywords)
Matched keywords: property, urban, homes
Keywords used only as brand seeds: rental
```

The rules are:

- The profile that matches the most keywords wins. Ties go to the `industry` set in the config.
- With no matching keyword, the configured `industry` is kept and the report says so.
- Every keyword of three or more letters is also added as a brand seed, which raises the score of names containing it.

Without `--keywords`, the configured `industry` and `brand_keywords` are used.

Output goes to `generated_review_domains.json`, so the existing `domains.json` is not overwritten. The report is written to `output/generation_report.json` and records the keywords received, the inferred industry, which profiles matched and on which terms, the brand seeds, and the top 20 scored names. Use `--output` and `--report` to change either path.

Review the candidates first. Only after approval, copy the list to `domains.json` or pass it with `--input`.

## Step 2: Check availability

```powershell
python godaddy_checker.py --dry-run                 # no network request
python godaddy_checker.py --test                    # one real API call; prints the raw item
python godaddy_checker.py --input generated_review_domains.json
python godaddy_checker.py --force-recheck           # ignore the checkpoint
python godaddy_checker.py --no-resume               # do not read or write the checkpoint
python godaddy_checker.py --prefilter               # skip names RDAP reports as registered
```

### Budget

`budget.max_yearly_price` caps the first-year price. `budget.max_renewal_price` caps the renewal price. Both apply only to API-confirmed available domains in `budget.currency`.

- A domain with no API price, or a price in another currency, is excluded.
- A known renewal price above the cap excludes the domain.
- An unknown renewal price passes unless `budget.require_renewal_price` is `true`.

### Prefilter (optional)

With `prefilter.enabled` (or `--prefilter`), each pending name is first looked up in RDAP through `rdap.org`. Only a clear "registered" answer (HTTP 200) is used, and it marks the name as taken without spending a GoDaddy request. Any other answer, including rate limits and network errors, leaves the decision to GoDaddy. Availability always comes from GoDaddy. This is not yet tested against live RDAP from every TLD in the list.

### Price units

Prices and currency come straight from the API response. `--test` prints the raw item. Check its price units and currency before relying on the budget filters, because the budget compares those numbers directly with the limits.

## Outputs

Results are written under `output/`:

- `available_domains.*`, `taken_domains.*`, `invalid_domains.*`, `error_domains.*`, `unchecked_domains.*` (JSON and CSV)
- `domain_results.md`, `available_domains.md`, `budget_available_domains.md`
- `BUY_NOW.txt`, which lists only API-confirmed available domains that meet the budget
- `summary.json` and `checker.log`
- `domain_results.xlsx` and related Excel files, only with `save_excel: true`

Each domain has one status: `AVAILABLE`, `TAKEN`, `ERROR`, `INVALID`, or `UNCHECKED`. `UNCHECKED` means the run stopped before reaching the domain.

## Failure behaviour

- **Authentication (401/403):** the run stops immediately with exit code 2, and the remaining domains are recorded as unchecked. Continuing would only spend requests.
- **Temporary failures** (408, 429, 5xx, timeouts, network errors): retried with exponential backoff. A numeric `Retry-After` header is honoured, capped at 120 seconds.
- **Batch failures** that are not retryable are recorded as errors. The domains are retried on the next run, and a later success replaces the error.
- **Malformed or incomplete responses** are treated as errors, never as availability.
- **Interrupts** (Ctrl+C) save progress and write reports. Run again to resume.

A checkpoint is saved after every batch, so a resumed run does not re-check domains that already have an answer.

GoDaddy availability can change between this check and registration. Always re-check the final price and availability at checkout.

## Tests

```powershell
python -m pytest
```

The tests cover domain normalisation, budget rules, retry and `Retry-After` handling, auth stop, resume bookkeeping, Excel output, RDAP decisions, and keyword intent detection.
