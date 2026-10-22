# DomainChecker

Find domain names that are available to register, from a keyword.

Enter a keyword such as `automation` or `real estate`, pick the extensions (`.com`, `.in`, `.ai`, `.co`), and set a maximum price. The app builds readable names that contain your keyword, checks them with GoDaddy, and lists the ones that are available.

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

## Use the web app

```powershell
python run_server.py
```

Open http://127.0.0.1:8000. The server listens on this computer only, and the app has no login.

For each search the app:

1. Builds names from your keyword. Every name contains a word from it, and the exact keyword comes first.
2. Adds industry words when the keyword matches a known industry (real estate, proptech, technology, finance).
3. Combines each name with the extensions you picked.
4. Skips names the free RDAP registry reports as registered.
5. Checks the rest with GoDaddy in batches of 25.
6. Lists the available names, in-budget ones first and the strongest brand names next, with the first-year and renewal price.

**Brand score.** Every name gets a score from 0 to 100 for how well it works as a brand, with the reasons shown next to it. Names lose points for being long, for sounds that are hard to say (such as four consonants in a row), for many syllables, for stacked add-ons (`automationplusnow`), and for filler words such as "world" or "online". Names below 60 are not shown. Short keywords keep thousands of names; long keywords such as "automation" keep about 100, because most longer combinations score too low.

Results are kept in `output/app.sqlite3`, so a finished search is still there after a restart.

**Every name is unique, and checked only once.** Each name the app generates is recorded in `output/app.sqlite3`, and a later search or generator run never produces it again. When GoDaddy answers for a name, available or taken, the answer is stored with it, and that name is never sent to GoDaddy again: not by a later web search, and not by the command-line checker. The checker reports such names with the reason `CHECKED_EARLIER` and the stored answer. A name is released only when it was generated but never answered (the check failed or the run stopped), so it can be tried later. Each keyword has a finite set of names that pass the brand score (about 100 for a long keyword like "automation", over 1,000 for a short one like "tag"). When they are all used, the search says that no new names are left. Use a different keyword or extension to continue.

## Command line

```powershell
python generate_domains.py --keywords "automation"    # writes candidate names, no API request
python godaddy_checker.py --input domains.json          # checks a list of domains with GoDaddy
python godaddy_checker.py --dry-run                     # validates the list only
python godaddy_checker.py --test                        # one real API call; prints the raw item
python godaddy_checker.py --force-recheck               # ignores the run checkpoint; answered names are still not re-sent
```

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
