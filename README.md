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

## Web app

The web app is the main way to use the product: describe a business, pick extensions, and get the available names within your price.

```powershell
python -m uvicorn web_app:app --reload
```

Open http://127.0.0.1:8000. Enter a keyword such as `real estate`, tick `.com` and/or `.in`, set the maximum first-year and renewal prices, and submit. The page polls the search and shows each available domain with its price and whether it is within budget. The price fields are optional.

How a search runs:

1. Names are built from the keyword and the industry vocabulary in `domain_generation_config.json` (`word_generation`). The exact keyword phrase ranks first, then phrases with affixes, then industry words.
2. Each name is combined with the selected extensions.
3. Optionally (`prefilter.enabled`), RDAP removes names it reports as registered.
4. GoDaddy checks the rest in batches of 25. Only GoDaddy's `available: true` counts as available.
5. Available names are tagged with whether they meet the budget, and sorted with in-budget names first.

Limitations of this version:

- Jobs are held in memory and are lost when the server restarts. A database is the next step.
- There is no trademark screening. Check names before using them.
- Prices are indicative API prices. The renewal price is shown next to the first-year price, and both must be checked at checkout.
- The app has no login. Run it on your own machine, or add authentication before exposing it.

## Tests

```powershell
python -m pytest
```

The tests cover domain normalisation, budget rules, retry and `Retry-After` handling, auth stop, resume bookkeeping, Excel output, RDAP decisions, and keyword intent detection.
