# DomainChecker

It will get used to check domain is available or not.

# GoDaddy Domain Availability Checker

This Windows-friendly Python 3.10+ application reads domains from JSON and checks them through the official GoDaddy Domains API. It never purchases domains. A domain is classified as available only when GoDaddy returns the boolean value `available: true`.

## Setup

```powershell
python --version
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Create a GoDaddy Personal Access Token with permission to check domains, then put it in `.env` as `GODADDY_PAT=...`. The token is never written to outputs or logs. Do not commit `.env`.

Credential precedence is CLI `--pat`, environment variable `GODADDY_PAT`, `.env`, `GODADDY_PAT.json`, then the optional `godaddy_pat` config value. Put a token in the ignored `GODADDY_PAT.json` file using `{ "GODADDY_PAT": "your_real_token" }` if you want the project to preserve it locally. Never commit `GODADDY_PAT.json` or `.env`. `GODADDY_API_URL` can override the endpoint for controlled environments.

The default input is `domains.json`. It can be an array or `{"domains": [...]}`. Edit `config.json` for batching, retry, output, TLD, and price settings.

## Generate Candidate Domains

Edit `domain_generation_config.json` to control the business-oriented candidate pool. Set `industry` to a key in `industry_profiles` to change the keyword weighting used by the generator. You can also edit or add profiles, or omit the selector and use the legacy `industry_keywords` list. The generator creates review-only names and does not claim availability.

The premium candidate gate requires a score of at least 18 from the phonetic, spelling, uniqueness, brand, industry, and legal filters. The strict purchase filter is `INR 5,000` for first-year registration and `INR 6,000` for renewal. A domain must be confirmed available by GoDaddy, include a returned price, use INR, and meet the configured budget. Renewal price is preserved when the API supplies it; it can be made mandatory with `budget.require_renewal_price`.

```powershell
python generate_domains.py
```

This writes the configured output file, normally `domains.json`. To keep the current list and create a separate review file:

```powershell
python generate_domains.py --output generated_review_domains.json
```

Review the generated file first. Only after approval should you run `python godaddy_checker.py --force-recheck` to check availability through GoDaddy.

## Commands

```powershell
python godaddy_checker.py --dry-run
python godaddy_checker.py
python godaddy_checker.py --test
python godaddy_checker.py --input domains.json --batch-size 25
python godaddy_checker.py --force-recheck
python godaddy_checker.py --no-resume
```

Dry run performs no network request. Test mode checks one valid domain through the API. Normal runs save a checkpoint after each successful batch, so interrupted work can resume. The API limit is enforced locally at 25 domains per request even if a larger value is configured.

## Outputs

Results are written under `output/`: available, taken, invalid, error, unchecked, CSV/JSON, `domain_results.md`, `available_domains.md`, `budget_available_domains.md`, `BUY_NOW.txt`, `summary.json`, and `checker.log`. Reports preserve price, renewal price, currency, premium, definitive, and status fields when GoDaddy supplies them. `available_domains.md` contains all API-confirmed available domains. `budget_available_domains.md` and `BUY_NOW.txt` contain only API-confirmed available domains with a known API price in the required currency and at or below the configured budget. Missing prices and currency mismatches are excluded. Prices are indicative API prices and must be verified at checkout. Excel output is disabled by default and can be re-enabled with `save_excel`.

Authentication failures stop clearly; temporary network failures, HTTP 408/429/5xx responses, and timeouts retry with exponential backoff. A failed batch is never marked as successfully processed. Malformed or incomplete API responses are treated as errors, never as availability.

GoDaddy availability can change between this check and registration. Always re-check the final price and availability at checkout.
