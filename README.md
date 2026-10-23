# DomainChecker

Find domain names that are available to register, from a keyword.

Enter a keyword such as `automation` or `real estate`, pick the extensions (`.com`, `.in`, `.ai`, `.co`, `.io`, `.app`, `.dev`), and set a maximum price. The app builds readable names that contain your keyword, checks them with GoDaddy, and lists the ones that are available.

Nothing is purchased. A domain counts as available only when GoDaddy returns `available: true`.

## Setup

```powershell
python --version            # 3.10 or newer
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Put your GoDaddy Personal Access Token in `.env` as `GODADDY_PAT=...`, or set it as an environment variable. The token is never written to files or logs. Do not commit `.env`.

**The token is checked first.** When the app starts and when the page opens, it sends one small availability request to GoDaddy with your token. If the token is missing, or GoDaddy rejects it, the page says what to fix and Search stays disabled. Fix the token in `.env` and press **Check again**; there is no need to restart the app.

## Use the web app

```powershell
python run_server.py
```

On Windows you can double-click `start_app.bat` instead. The app opens http://127.0.0.1:8000 in your browser. The server listens on this computer only, and the app has no login. Close the window to stop it.

**With Docker** (no Python setup needed):

```powershell
docker build -t domain-search .
docker run --rm -p 127.0.0.1:8000:8000 --env-file .env -v "${PWD}/output:/app/output" domain-search
```

Then open http://127.0.0.1:8000. Keep `127.0.0.1:` in the `-p` option, so the app stays reachable from this computer only. The `output` folder keeps your searches and checked domains between runs.

`.app` and `.dev` domains only work over HTTPS; browsers require it for these extensions.

For each search the app:

1. Builds names from your keyword. Every name contains a word from it, and the exact keyword comes first.
2. Adds industry words when the keyword matches a known industry (real estate, proptech, technology, finance).
3. Combines each name with the extensions you picked.
4. Skips names the free RDAP registry reports as registered.
5. Checks the rest with GoDaddy in batches of 25, 50 names per extension at a time.
6. Lists every name it generated and checked as soon as GoDaddy answers: **available** names first (in-budget ones first, then the strongest brand names, with the first-year and renewal price), then the names that are **not available** (taken, or reported as registered).
7. Keeps going with new names until you press **Stop searching** or the keyword runs out of names. Stop when you see the domain you want: the names found so far are kept, and names that were not checked yet stay available for a later search. (A search left running stops on its own after 5,000 names per extension, to protect your GoDaddy quota.)

**Brand score.** Every name gets a score from 0 to 100 for how well it works as a brand, with the reasons shown next to it. Names lose points for being long, for sounds that are hard to say (such as four consonants in a row), for many syllables, for stacked add-ons (`automationplusnow`), and for filler words such as "world" or "online". Names below 60 are not shown. Names with one add-on (`taghub`, `gettag`) are always used before names with two (`gettaghub`), so stacked names only appear once the simpler ones run out. Short keywords such as "tag" have over 4,000 names; long keywords such as "automation" have about 160, because most longer combinations score too low.

Results are kept in `output/app.sqlite3`, so a finished search is still there after a restart. If the server stops during a search, the search is shown as **Interrupted** with the names it had found, and the names it had not checked yet go back into the pool.

**When a keyword runs out of names**, the page offers one-click searches for related keywords (for example "autopilot" or "workflow" for "automation") and for extensions you have not searched yet that still have names. Related keywords are listed in `settings.py`.

**GoDaddy's rate limit.** All searches and rechecks share one pace of 50 requests a minute, under GoDaddy's limit of 60, however many run at once.

**Reviewing results.** The page has three tabs:

- **Search** runs a search and shows every name it generated and checked, available or not.
- **History** lists every search you have run, repeats included, with how many names were available, how many were not available, how many were within budget, and the cheapest price. A running search shows its progress. Open a past search to see its domains again, find one by keyword, or delete it (its domains stay in All domains). Searches stay in History for a year.
- **All domains** lists every domain the app has checked, available or taken, with the keyword it came from, the prices, the brand score, and when it was checked. It loads the newest 1,000 first; **Load more** fetches the next 1,000.

Every results table shows the first-year price, the renewal price, and the **3-year cost** (first year plus two renewals), which shows when a cheap first year hides an expensive renewal. Each table has the same filters: **Show** (all checked names, available only, or not available only), price (any, free first year, first year under an amount, or 3-year cost under an amount), extension, minimum brand score, a name search, starred only, and sort order (cheapest first year, lowest 3-year cost, best brand, name, or newest). The page remembers your filter choices. **Export CSV** downloads the rows that match the filters.

On every row:

- **☆** adds the domain to your shortlist. Use **Starred only** to compare your shortlist across searches. A starred domain that is taken is marked "recheck later", so you can use **Recheck** to see whether it has come free.
- **Buy ↗** opens the domain on GoDaddy.
- **Recheck** (All domains only) asks GoDaddy again for the domain's current availability and price, and stores the new answer. This is the only way a domain is sent to GoDaddy a second time, and it only happens when you click it.

**Every name is unique, and checked only once.** Each name a search uses is recorded in `output/app.sqlite3`, and a later search never produces it again. When GoDaddy answers for a name, available or taken, the answer is stored with it, and that name is never sent to GoDaddy again: not by a later web search, and not by the command-line checker. The checker reports such names with the reason `CHECKED_EARLIER` and the stored answer. A name is released only when it was generated but never answered (the check failed or the run stopped), so it can be tried later. Each keyword has a finite set of names that pass the brand score (about 160 for a long keyword like "automation", over 4,000 for a short one like "tag"). When they are all used, the search says that no new names are left. Use a different keyword or extension to continue.

## Command line

```powershell
python generate_domains.py --keywords "automation"    # previews the next names a search would check; no API request
python godaddy_checker.py --input domains.json          # checks a list of domains with GoDaddy
python godaddy_checker.py --dry-run                     # validates the list only
python godaddy_checker.py --test                        # one real API call; prints the raw item
python godaddy_checker.py --force-recheck               # ignores the run checkpoint; answered names are still not re-sent
```

The generator only previews names: it does not use them up, so running it does not take names away from the web search.

Results from the checker are written to `output/`. They include `BUY_NOW.txt`, a list of available domains within the price limit.

## Settings

All settings are in `settings.py`: extensions, price default, batch size, retries, word lists, industry vocabulary, and blocked terms. There are no configuration files to edit. The only values users change are the keyword, the extensions and the price.

## Before you rely on the results

- **Price units are not yet confirmed.** Run `python godaddy_checker.py --test` with a real token and check the printed price against the GoDaddy site. `--test` exits with an error if a price looks like micro-units.
- **Trademarks are not checked.** A name can be available and still be someone's trademark. Check before you use one.
- **Availability and prices change.** GoDaddy's results are for search. Confirm availability and price at checkout.
- **Renewal prices are shown, not filtered.** The price limit applies to the first year only.

## Tests

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

The tests run offline. They replace the GoDaddy and RDAP calls with fakes.
