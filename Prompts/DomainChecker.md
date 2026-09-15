# MASTER PROMPT — BUILD A FULLY CONFIGURABLE PYTHON GODADDY DOMAIN AVAILABILITY CHECKER

You are a **senior Python developer, API integration engineer, and domain-registration automation specialist**.

Build a **production-quality, fully configurable Python application** that reads a large list of domain names from JSON, checks their **real-time availability through the official GoDaddy Domains API**, and produces a clean list containing **ONLY domains that GoDaddy reports as available**.

The primary objective is:

> **I do NOT want to waste time checking domains manually on GoDaddy. The program must automatically verify every candidate through the official GoDaddy API and show me only domains that are currently reported as available.**

---

# 1. ABSOLUTE REQUIREMENTS

These requirements are mandatory.

## 1.1 Official GoDaddy API ONLY

Use the official GoDaddy Domains API.

Availability endpoint:

`POST https://api.godaddy.com/v3/domains/check-availability`

Do NOT use:

* Selenium
* browser automation
* HTML scraping
* Google search
* Bing search
* DNS lookup as an availability check
* WHOIS as the primary availability check
* third-party domain APIs
* GoDaddy website scraping
* login automation
* cookies/session automation

The application must communicate directly with the GoDaddy API.

---

# 2. AUTHENTICATION

Use a GoDaddy **Personal Access Token (PAT)**.

Authentication must use:

`Authorization: Bearer <GODADDY_PAT>`

Never ask the user for:

* GoDaddy password
* GoDaddy username/password combination
* cookies
* browser session
* MFA code

The API key/token must never be hardcoded inside Python source code.

Support:

1. Environment variable
2. `.env` file
3. `config.json`

Recommended priority:

```text
CLI argument
    ↓
Environment variable
    ↓
.env
    ↓
config.json
    ↓
default configuration
```

Never print the complete PAT in logs.

---

# 3. DOMAIN INPUT

The application must read domains from a JSON file.

Default:

```text
domains.json
```

Example:

```json
[
    "exampleproperty.com",
    "examplehomes.com",
    "exampleestate.com",
    "exampleland.com"
]
```

Also support this format:

```json
{
    "domains": [
        "exampleproperty.com",
        "examplehomes.com",
        "exampleestate.com"
    ]
}
```

The program must automatically detect both formats.

---

# 4. LARGE INPUT SUPPORT

The JSON file may contain:

* 10 domains
* 100 domains
* 1,000 domains
* 10,000 domains
* 100,000+ domains

Do not assume a small input file.

The program must process domains efficiently in batches.

---

# 5. STRICT BATCH LIMIT

GoDaddy availability requests must contain **NO MORE THAN 25 domains per API request**.

Therefore:

```text
batch_size <= 25
```

If the user configures:

```json
"batch_size": 100
```

the application must automatically reject it or safely cap it at:

```text
25
```

Never send more than 25 domains in one request.

Example:

```text
100 domains

Batch 1 → 25
Batch 2 → 25
Batch 3 → 25
Batch 4 → 25
```

---

# 6. DOMAIN NORMALIZATION

Before sending domains to GoDaddy, normalize them.

Perform:

* lowercase conversion
* trim whitespace
* remove accidental leading/trailing spaces
* remove duplicate domains
* remove empty values
* validate domain syntax
* optionally remove accidental `http://`
* optionally remove accidental `https://`
* optionally remove leading `www.` according to configuration

Example:

```text
 ExampleProperty.COM
 exampleproperty.com
EXAMPLEPROPERTY.COM
```

must become one domain:

```text
exampleproperty.com
```

Do NOT silently modify unusual but potentially valid domains without reporting the normalization.

Keep an optional normalization log.

---

# 7. INVALID DOMAIN HANDLING

Invalid domains must NOT be sent to GoDaddy.

Create:

```text
invalid_domains.json
invalid_domains.csv
```

Example:

```json
[
    "invalid domain",
    "http://",
    "abc",
    ""
]
```

The program should continue processing valid domains.

An invalid domain must never stop the complete job.

---

# 8. AVAILABILITY MODE

Use:

```json
"optimizeFor": "ACCURACY"
```

by default.

Make it configurable:

```json
"optimize_for": "ACCURACY"
```

Allowed values:

```text
ACCURACY
SPEED
```

Default:

```text
ACCURACY
```

Because the primary objective is to minimize false availability results.

---

# 9. MOST IMPORTANT RULE — AVAILABLE MEANS AVAILABLE

The application must have a strict definition of an available domain.

A domain should appear in:

```text
available_domains.json
available_domains.csv
BUY_NOW.txt
```

ONLY when the GoDaddy API explicitly reports:

```json
"available": true
```

Do NOT infer availability.

Do NOT classify a domain as available because:

* DNS does not exist
* WHOIS does not show information
* Google does not find it
* another registrar says it is available
* the domain website does not load
* the domain looks unused
* the domain looks abandoned

Only the GoDaddy availability API result can qualify a domain as available.

---

# 10. IMPORTANT — NO FALSE POSITIVES

This is a critical requirement.

If GoDaddy returns:

```json
"available": false
```

the domain MUST NOT appear in the available list.

If the API returns:

```text
unknown
null
timeout
error
rate limited
temporary failure
```

the domain MUST NOT be classified as available.

Instead place it into:

```text
errors.json
```

or:

```text
unchecked_domains.json
```

depending on configuration.

Never guess.

---

# 11. FINAL PURCHASE LIST

Create:

```text
BUY_NOW.txt
```

This file must contain ONLY domains confirmed as available by the API.

Example:

```text
exampleproperty.com
examplehomes.com
exampleestate.com
```

Do not include:

* taken domains
* unknown domains
* errors
* invalid domains
* pending domains
* unavailable domains

---

# 12. PRICE INFORMATION

When GoDaddy returns pricing information, capture it.

Store:

```text
domain
available
price
currency
```

Example:

```json
{
    "domain": "exampleproperty.com",
    "available": true,
    "price": 999,
    "currency": "USD"
}
```

Clearly label pricing as:

```text
Indicative API price
```

Do not claim that the displayed price is the final checkout price.

---

# 13. OUTPUT FILES

Generate configurable output files.

Default:

```text
output/
├── available_domains.json
├── available_domains.csv
├── taken_domains.json
├── taken_domains.csv
├── invalid_domains.json
├── invalid_domains.csv
├── error_domains.json
├── error_domains.csv
├── unchecked_domains.json
├── summary.json
├── BUY_NOW.txt
└── checker.log
```

Allow filenames and output directory to be configured.

---

# 14. AVAILABLE JSON FORMAT

Example:

```json
[
    {
        "domain": "exampleproperty.com",
        "available": true,
        "price": 999,
        "currency": "USD",
        "checked_at": "2026-09-13T22:00:00Z"
    }
]
```

---

# 15. TAKEN DOMAINS

Store domains where GoDaddy explicitly reports:

```text
available = false
```

Example:

```json
[
    {
        "domain": "example.com",
        "available": false,
        "checked_at": "2026-09-13T22:00:00Z"
    }
]
```

---

# 16. ERROR HANDLING

Handle at minimum:

* HTTP 400
* HTTP 401
* HTTP 403
* HTTP 404
* HTTP 408
* HTTP 429
* HTTP 500
* HTTP 502
* HTTP 503
* HTTP 504
* connection errors
* DNS/network errors
* request timeout
* malformed API response
* invalid JSON
* missing response fields

The application must never crash because one request fails.

---

# 17. HTTP 429 RATE LIMIT HANDLING

If GoDaddy responds with:

```text
429 Too Many Requests
```

implement automatic retry.

Use configurable:

```json
"max_retries": 5
```

and exponential backoff.

Example:

```text
Retry 1 → 2 seconds
Retry 2 → 4 seconds
Retry 3 → 8 seconds
Retry 4 → 16 seconds
Retry 5 → 32 seconds
```

Make these values configurable.

If the API provides a `Retry-After` header, respect it.

---

# 18. NETWORK RETRIES

Retry temporary failures such as:

```text
408
429
500
502
503
504
connection timeout
connection reset
temporary network failure
```

Do NOT repeatedly retry permanent authentication errors such as:

```text
401
403
```

Instead stop with a clear configuration/authentication error.

---

# 19. REQUEST TIMEOUT

Make timeout configurable.

Example:

```json
"timeout_seconds": 30
```

Default:

```text
30 seconds
```

---

# 20. DELAY BETWEEN REQUESTS

Support configurable delay:

```json
"delay_between_batches_seconds": 1
```

The program should not unnecessarily hammer the API.

Make this configurable.

---

# 21. RESUME SUPPORT

This is mandatory for large domain lists.

If the program processes:

```text
10,000 domains
```

and stops after:

```text
4,500 domains
```

running it again should NOT automatically re-check all 10,000 domains.

Create a checkpoint/state file:

```text
checkpoint.json
```

The program must remember:

* processed domains
* successful batches
* failed batches
* available domains
* taken domains
* invalid domains
* unchecked domains

When resumed, it should continue from where it stopped.

Configuration:

```json
"resume_enabled": true
```

---

# 22. SAFE RESUME

Do not mark a domain as processed until the corresponding API result has been successfully handled.

If a batch fails completely:

```text
Batch 15
```

do not mark those domains as successfully checked.

They should remain retryable.

---

# 23. DRY RUN MODE

Support:

```bash
python godaddy_checker.py --dry-run
```

Dry run must:

* load JSON
* validate domains
* normalize domains
* remove duplicates
* show batch structure
* show number of API requests expected

BUT:

**DO NOT CALL THE GO DADDY API.**

---

# 24. TEST MODE

Support:

```bash
python godaddy_checker.py --test
```

Test mode should verify:

* configuration
* input file
* JSON structure
* domain validation
* PAT availability
* API connectivity
* authentication

Do not process the entire input list in test mode.

---

# 25. CONFIG.JSON

Create a fully configurable:

```text
config.json
```

Example:

```json
{
    "input_file": "domains.json",

    "output_directory": "output",

    "batch_size": 25,

    "optimize_for": "ACCURACY",

    "timeout_seconds": 30,

    "delay_between_batches_seconds": 1,

    "max_retries": 5,

    "retry_backoff_seconds": 2,

    "resume_enabled": true,

    "checkpoint_file": "checkpoint.json",

    "normalize_domains": true,

    "remove_duplicates": true,

    "validate_domains": true,

    "save_available": true,

    "save_taken": true,

    "save_invalid": true,

    "save_errors": true,

    "save_unchecked": true,

    "save_csv": true,

    "save_json": true,

    "save_buy_now": true,

    "save_logs": true,

    "log_level": "INFO"
}
```

Every reasonable behavior should be configurable.

---

# 26. COMMAND LINE CONFIGURATION

Support CLI overrides.

Examples:

```bash
python godaddy_checker.py
```

```bash
python godaddy_checker.py --input domains.json
```

```bash
python godaddy_checker.py --batch-size 25
```

```bash
python godaddy_checker.py --optimize-for ACCURACY
```

```bash
python godaddy_checker.py --timeout 30
```

```bash
python godaddy_checker.py --delay 1
```

```bash
python godaddy_checker.py --retries 5
```

```bash
python godaddy_checker.py --dry-run
```

```bash
python godaddy_checker.py --test
```

```bash
python godaddy_checker.py --resume
```

```bash
python godaddy_checker.py --no-resume
```

CLI options must override `config.json`.

---

# 27. ENVIRONMENT VARIABLES

Support:

```text
GODADDY_PAT
```

Example:

```text
GODADDY_PAT=your_personal_access_token
```

Also support:

```text
GODADDY_API_URL
```

with the default official endpoint.

Never print secrets.

---

# 28. .ENV SUPPORT

Support `.env`.

Example:

```text
GODADDY_PAT=xxxxxxxxxxxxxxxx
```

Create:

```text
.env.example
```

but NEVER put a real API token inside it.

---

# 29. REQUIREMENTS.TXT

Create:

```text
requirements.txt
```

Use only necessary dependencies.

Prefer a lightweight implementation based on:

```text
requests
python-dotenv
```

Avoid unnecessary frameworks.

---

# 30. LOGGING

Implement professional logging.

Example:

```text
2026-09-13 22:00:01 INFO Starting domain checker
2026-09-13 22:00:01 INFO Loaded 5000 domains
2026-09-13 22:00:02 INFO 4821 valid unique domains
2026-09-13 22:00:02 INFO Processing batch 1/193
2026-09-13 22:00:03 INFO Available: exampleproperty.com
2026-09-13 22:00:03 INFO Taken: examplehomes.com
```

Never log the PAT.

---

# 31. PROGRESS DISPLAY

Show useful progress:

```text
Checking domains...

Batch: 25/200
Processed: 625/5000
Available: 42
Taken: 570
Errors: 13
Progress: 12.5%
```

Update progress without flooding the console.

---

# 32. SUMMARY.JSON

Generate:

```text
summary.json
```

Example:

```json
{
    "total_input": 5000,
    "unique_domains": 4821,
    "invalid_domains": 179,
    "processed": 4821,
    "available": 42,
    "taken": 4766,
    "errors": 13,
    "unchecked": 0,
    "api_requests": 193,
    "started_at": "...",
    "finished_at": "..."
}
```

---

# 33. STRICT AVAILABLE-ONLY MODE

Add an optional configuration:

```json
"available_only": true
```

When enabled, the final console output should show ONLY:

```text
AVAILABLE DOMAINS
-----------------
exampleproperty.com
exampleestate.com
examplehomes.com
```

This should be the default behavior.

---

# 34. BUY-NOW FILTER

Create a configurable filter for domains suitable for immediate consideration.

Example:

```json
"buy_now": {
    "enabled": true,
    "require_available": true,
    "require_api_success": true
}
```

The BUY_NOW file must NEVER contain an unchecked or uncertain domain.

---

# 35. FINAL VERIFICATION WARNING

At the end of the program display:

```text
IMPORTANT:
GoDaddy availability results are intended for search/availability purposes.
A domain's final availability and registration price should be re-verified at checkout before purchase.
```

Do NOT claim:

```text
100% guaranteed purchasable
```

because availability can change between the API check and checkout.

However, the program itself must maintain a strict rule:

> **Only an explicit GoDaddy API `available=true` response qualifies as AVAILABLE inside this application.**

---

# 36. SECURITY

Never:

* hardcode PAT
* print PAT
* save PAT into output JSON
* save PAT into logs
* commit `.env`
* expose authentication headers

Create:

```text
.gitignore
```

containing:

```text
.env
__pycache__/
*.pyc
checkpoint.json
output/
```

---

# 37. WINDOWS SUPPORT

The application must work correctly on Windows 10/11.

Provide:

```text
run.bat
```

Example:

```bat
@echo off
python godaddy_checker.py
pause
```

Also provide clear PowerShell instructions.

---

# 38. PYTHON VERSION

Target:

```text
Python 3.10+
```

Prefer standard-library functionality where practical.

Use type hints.

Use clean functions/classes.

---

# 39. CODE QUALITY

The application must be:

* modular
* readable
* maintainable
* documented
* strongly validated
* exception-safe
* configurable
* production-oriented

Do NOT put the entire application into one giant function.

Use logical components such as:

```text
load_config()
load_domains()
normalize_domain()
validate_domain()
chunk_domains()
check_batch()
process_response()
save_results()
load_checkpoint()
save_checkpoint()
print_summary()
```

---

# 40. API RESPONSE VALIDATION

Do not blindly trust malformed responses.

Validate that:

* response is JSON
* expected fields exist
* domain matches requested domain
* availability field has a valid value
* price fields are handled safely

If the response cannot be confidently interpreted:

```text
DO NOT mark the domain available.
```

Place it into the error/unchecked category.

---

# 41. DOMAIN MATCHING

If GoDaddy returns domains in a different order than submitted, correctly map every response to the correct domain.

Never assume:

```text
response[0] == request[0]
```

unless the API contract guarantees it.

Use the returned domain name as the key.

---

# 42. DUPLICATE PROTECTION

If the input contains:

```text
example.com
Example.com
 EXAMPLE.COM
```

only one API check should be performed.

The output must contain only one record.

---

# 43. OPTIONAL TLD FILTER

Make optional TLD filtering configurable.

Example:

```json
"allowed_tlds": [
    ".com",
    ".net",
    ".org",
    ".in",
    ".co"
]
```

If empty:

```json
"allowed_tlds": []
```

allow all supported domains.

---

# 44. OPTIONAL PRICE FILTER

Support optional filtering such as:

```json
"max_price": null
```

or:

```json
"max_price": 50
```

If configured, only domains whose returned indicative price is at or below the configured threshold should appear in the filtered BUY_NOW output.

Do NOT use missing price as proof of affordability.

---

# 45. API REQUEST COUNT

Before processing, calculate:

```text
number_of_requests = ceil(number_of_valid_unique_domains / batch_size)
```

Display:

```text
Domains: 5,000
Batch size: 25
Expected API requests: 200
```

---

# 46. NO UNNECESSARY API REQUESTS

The application should avoid checking the same domain multiple times during the same run.

With resume enabled, it should avoid rechecking successfully processed domains unless explicitly requested.

Provide:

```bash
--force-recheck
```

to intentionally recheck previously processed domains.

---

# 47. FORCE RECHECK

Example:

```bash
python godaddy_checker.py --force-recheck
```

This must ignore successful checkpoint entries and perform fresh availability checks.

---

# 48. OUTPUT SORTING

Allow configuration:

```json
"sort_available_by": "domain"
```

Possible values:

```text
domain
price
none
```

For price sorting, lowest price should appear first.

---

# 49. CSV FORMAT

Available CSV should contain:

```text
domain,available,price,currency,checked_at
```

Example:

```text
exampleproperty.com,true,999,USD,2026-09-13T22:00:00Z
```

---

# 50. FINAL CONSOLE OUTPUT

At completion show:

```text
========================================
GO DADDY DOMAIN CHECK COMPLETE
========================================

Input domains       : 5000
Unique valid        : 4821
Invalid             : 179
Checked             : 4821
Available           : 42
Taken               : 4766
Errors              : 13

API requests        : 193

Available domains saved to:
output/available_domains.json

BUY NOW list:
output/BUY_NOW.txt
========================================
```

---

# 51. ERROR RECOVERY

If the program is interrupted with:

```text
CTRL+C
```

handle it gracefully.

Before exiting:

* save checkpoint
* save current results
* display resume information

Example:

```text
Process interrupted.
Progress has been saved.

Run the program again to resume.
```

---

# 52. API CONFIGURATION

Put API settings into a dedicated configuration section:

```json
"godaddy": {
    "base_url": "https://api.godaddy.com",
    "availability_endpoint": "/v3/domains/check-availability",
    "optimize_for": "ACCURACY"
}
```

Do not scatter API URLs throughout the code.

---

# 53. ENVIRONMENT SELECTION

Allow optional configuration for:

```text
production
ote
```

but default to the official production API.

If GoDaddy provides separate environments, clearly separate them.

Never accidentally run production registration operations.

This application is an **availability checker only**.

---

# 54. NO PURCHASE AUTOMATION

Do NOT automatically purchase domains.

Do NOT execute registration.

Do NOT modify domains.

Do NOT initiate payment.

The program only:

```text
READ → CHECK → CLASSIFY → SAVE
```

---

# 55. OPTIONAL API QUOTE INFORMATION

If the API provides additional information useful for registration decisions, store it in the result without assuming it guarantees successful purchase.

Preserve useful fields such as:

* availability
* price
* currency
* definitive
* domain
* premium information
* restrictions
* status

when provided.

---

# 56. TEST DATA

Create:

```text
sample_domains.json
```

with at least 20 sample domains for testing the application structure.

Do not falsely label sample domains as available.

---

# 57. README

Create a professional:

```text
README.md
```

Explain:

1. Requirements
2. Python installation
3. GoDaddy PAT creation
4. API permissions required
5. `.env` setup
6. JSON input format
7. config.json
8. running the application
9. dry run
10. test mode
11. resume
12. force recheck
13. output files
14. error handling
15. rate limiting
16. availability limitations
17. final purchase verification

---

# 58. PROJECT STRUCTURE

Deliver the project approximately as:

```text
godaddy-domain-checker/
│
├── godaddy_checker.py
├── config.json
├── domains.json
├── sample_domains.json
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
├── run.bat
│
└── output/
    ├── available_domains.json
    ├── available_domains.csv
    ├── taken_domains.json
    ├── taken_domains.csv
    ├── invalid_domains.json
    ├── invalid_domains.csv
    ├── error_domains.json
    ├── error_domains.csv
    ├── unchecked_domains.json
    ├── unchecked_domains.csv
    ├── summary.json
    └── BUY_NOW.txt
```

---

# 59. INSTALLATION

README should explain:

```powershell
python --version
```

Then:

```powershell
python -m venv .venv
```

Windows activation:

```powershell
.venv\Scripts\Activate.ps1
```

Install:

```powershell
pip install -r requirements.txt
```

Then configure:

```text
.env
```

and run:

```powershell
python godaddy_checker.py
```

---

# 60. FINAL QUALITY CHECK

Before delivering the code, verify all of the following:

* [ ] Uses official GoDaddy API
* [ ] Uses POST availability endpoint
* [ ] Maximum 25 domains/request
* [ ] PAT authentication
* [ ] PAT never hardcoded
* [ ] JSON input
* [ ] Duplicate removal
* [ ] Domain normalization
* [ ] Domain validation
* [ ] Batch processing
* [ ] ACCURACY mode
* [ ] Retry handling
* [ ] HTTP 429 handling
* [ ] Timeout handling
* [ ] Resume/checkpoint
* [ ] CTRL+C handling
* [ ] Dry-run mode
* [ ] Test mode
* [ ] CLI configuration
* [ ] config.json
* [ ] `.env`
* [ ] Logging
* [ ] CSV output
* [ ] JSON output
* [ ] BUY_NOW.txt
* [ ] Available-only filtering
* [ ] Invalid-domain handling
* [ ] Error-domain handling
* [ ] Summary
* [ ] Windows support
* [ ] README
* [ ] requirements.txt
* [ ] No purchase automation
* [ ] No false-positive availability classification

---

# 61. MOST IMPORTANT BUSINESS RULE

The developer must understand this requirement above all others:

## I ONLY WANT DOMAINS THAT ARE CONFIRMED AVAILABLE BY GODADDY.

Therefore:

```text
GoDaddy API says AVAILABLE
        ↓
AVAILABLE
        ↓
BUY_NOW.txt
```

But:

```text
GoDaddy API says TAKEN
        ↓
TAKEN
```

and:

```text
API ERROR
        ↓
ERROR / UNCHECKED
```

and:

```text
TIMEOUT
        ↓
ERROR / UNCHECKED
```

and:

```text
UNKNOWN
        ↓
ERROR / UNCHECKED
```

and:

```text
available according to DNS/WHOIS/Google
        ↓
NOT ENOUGH
        ↓
DO NOT SHOW AS AVAILABLE
```

---

# 62. IMPORTANT AVAILABILITY DISCLAIMER

The application must distinguish between:

### API-confirmed availability

```text
GoDaddy API → available=true
```

and:

### Guaranteed successful registration

```text
Checkout → final availability confirmed
```

The program must never claim that an API result guarantees that the domain will still be available at the exact moment of purchase.

The correct wording is:

> **“Available according to the latest GoDaddy API availability check.”**

---

# 63. FINAL DELIVERABLE

Return the complete working project, not pseudocode.

Provide:

```text
godaddy_checker.py
config.json
.env.example
requirements.txt
sample_domains.json
README.md
.gitignore
run.bat
```

The Python application must be immediately usable after the user:

1. installs Python
2. installs dependencies
3. adds their GoDaddy PAT
4. places domains into `domains.json`
5. runs the program

Do not leave TODO placeholders for core functionality.

Do not provide pseudocode.

Do not omit error handling.

Do not omit resume support.

Do not omit batch-size enforcement.

Do not classify uncertain domains as available.

## FINAL OBJECTIVE

Build a **reliable, fully configurable GoDaddy domain availability checker for large domain lists** where the user can put thousands of candidate domains into one JSON file and receive a clean:

```text
BUY_NOW.txt
```

containing **ONLY domains that the GoDaddy API has explicitly reported as available at the time of checking.**
