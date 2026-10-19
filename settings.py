"""Every product setting lives here. Users only choose a keyword, extensions and prices.

Nothing in this file is read from a config file or environment variable, except the GoDaddy
token, which is read from the GODADDY_PAT environment variable or the .env file.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT_DIRECTORY = ROOT / "output"

# ---- Search inputs --------------------------------------------------------
ALLOWED_TLDS = (".com", ".in", ".ai", ".co")
DEFAULT_TLDS = (".com", ".in")
CURRENCY = "INR"
DEFAULT_MAX_PRICE = 5000
MAX_PRICE_LIMIT = 1_000_000
MAX_KEYWORD_LENGTH = 60
CANDIDATES_PER_SEARCH = 50
# Names generated per keyword before already-used names are removed. Large enough that
# a repeat search still finds new names.
CANDIDATE_POOL_PER_KEYWORD = 5000

# ---- Web app limits and access --------------------------------------------
# The app runs on this computer only. Requests must come from these hosts and origins.
ALLOWED_HOSTS = ("127.0.0.1:8000", "localhost:8000")
ALLOWED_ORIGINS = ("http://127.0.0.1:8000", "http://localhost:8000")
MAX_CONCURRENT_SEARCHES = 2
MAX_SEARCHES_PER_DAY = 50

# ---- GoDaddy availability -------------------------------------------------
GODADDY_API_URL = "https://api.godaddy.com/v3/domains/check-availability"
OPTIMIZE_FOR = "ACCURACY"
BATCH_SIZE = 25
TIMEOUT_SECONDS = 30
DELAY_BETWEEN_BATCHES_SECONDS = 1
MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = 2
MAX_RETRY_DELAY_SECONDS = 120

# ---- RDAP pre-check (free). Only a clear "registered" answer skips a name. ----
RDAP_URL = "https://rdap.org/domain/{domain}"
RDAP_TIMEOUT_SECONDS = 10
RDAP_WORKERS = 10

# ---- Results cache and storage --------------------------------------------
# Availability and registration answers are reused for this long before they are checked again.
CACHE_TTL_SECONDS = 24 * 60 * 60
JOB_RETENTION_DAYS = 14
APP_DATABASE = OUTPUT_DIRECTORY / "app.sqlite3"
CHECKPOINT_FILE = OUTPUT_DIRECTORY / "checkpoint.json"
SEARCH_LOG_FILE = OUTPUT_DIRECTORY / "search.log"

# ---- Name generation ------------------------------------------------------
MAX_NAME_LENGTH = 18  # long keywords such as "automation" still get room for affixes
MIN_NAME_LENGTH = 4

# Affixes that read naturally in a domain name. Prefixes rank below suffixes.
PREFIXES = ("my", "get", "best", "smart", "city", "global", "digital", "pro", "top", "join", "try")
SUFFIXES = (
    "hub", "pro", "zone", "spot", "now", "plus", "direct", "point", "app", "ly",
    "ify", "hq", "io", "site", "world", "online", "guru", "box", "bay", "wise",
    "works", "desk", "space", "cloud", "flow", "net", "link", "max", "one", "way",
    "kit", "shop", "store", "mart", "wave", "hive", "spark", "bridge", "crew",
    "team", "central", "genius", "expert", "lab", "stream", "scout", "rocket", "pal",
)

# Plural endings: automation -> automations. Other endings read badly ("automationer").
INFLECTIONS = ("s", "ers")

# Industry vocabulary is used only when a keyword matches one of these profiles.
# Stems are three letters so that they can match inside a name.
INDUSTRY_PROFILES = {
    "real_estate": ("nes", "urb", "roo", "lan", "rea", "hom", "ten", "pro"),
    "proptech": ("pro", "dat", "gri", "hom", "urb", "log", "nod"),
    "technology": ("byt", "dat", "cod", "gri", "log", "nod", "nov", "pea"),
    "finance": ("fun", "gai", "cas", "ris", "pea", "vau", "nes", "fin"),
}
INDUSTRY_VOCABULARY = {
    "real_estate": (
        "realty", "estate", "homes", "home", "property", "properties", "land", "rent",
        "house", "houses", "urban", "key", "roof", "brick", "nest", "space", "living",
        "broker", "listing", "tenant", "lease", "villa", "plot",
    ),
    "proptech": ("prop", "data", "grid", "home", "urban", "logic", "node", "realty", "estate"),
    "technology": ("byte", "data", "code", "grid", "logic", "node", "stack", "cloud", "labs"),
    "finance": ("fund", "capital", "cash", "vault", "credit", "loan", "wealth", "gain"),
}

# ---- Name screening -------------------------------------------------------
# A name is excluded when it equals a BLOCKED_EXACT word, or contains a BLOCKED_SUBSTRINGS entry.
# Short generic words are exact-only, so that "essex" or "barcode" are not excluded.
BLOCKED_EXACT = ("test", "demo", "sex", "zero", "code", "meta", "uber", "apple")
BLOCKED_SUBSTRINGS = ("amazon", "google", "microsoft", "paypal", "netflix", "airbnb", "dropbox", "admin", "spam", "fake", "hate")
BLOCKED_SUFFIXES = ("inc", "llc", "corp", "tech", "media", "group", "solutions", "labs")

# ---- Checker-only settings used by the command line tool -------------------
INPUT_FILE = "domains.json"
