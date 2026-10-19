"""Every product setting lives here. Users only choose a keyword, extensions and a price.

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
CANDIDATES_PER_SEARCH = 50
MAX_KEYWORD_LENGTH = 60

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

# ---- Persistence ----------------------------------------------------------
CHECKPOINT_FILE = OUTPUT_DIRECTORY / "checkpoint.json"
JOBS_DATABASE = OUTPUT_DIRECTORY / "jobs.sqlite3"

# ---- Name generation ------------------------------------------------------
MAX_NAME_LENGTH = 18  # long keywords such as "automation" still get room for affixes
MIN_NAME_LENGTH = 4

PREFIXES = (
    "my", "the", "go", "get", "smart", "city", "best", "try", "join", "open",
    "true", "top", "new", "next", "daily", "easy", "global", "digital", "online",
    "mini", "super", "neo", "ultra", "first", "prime", "pro",
)
SUFFIXES = (
    "hub", "pro", "zone", "spot", "now", "plus", "direct", "point", "app", "ly",
    "ify", "hq", "io", "site", "world", "online", "guru", "box", "bay", "wise",
    "works", "desk", "space", "cloud", "flow", "net", "link", "max", "one", "way",
    "kit", "shop", "store", "mart", "wave", "hive", "spark", "bridge", "crew",
    "team", "central", "genius", "expert", "lab", "stream", "scout", "rocket", "pal",
)

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
# Legal and brand terms are excluded. Industry words such as "home" or "rent" are not.
BLOCKED_TERMS = (
    "admin", "demo", "spam", "fake", "test", "hate", "sex", "zero", "code",
    "amazon", "google", "microsoft", "meta", "apple", "paypal", "netflix", "uber",
    "airbnb", "dropbox",
)
BLOCKED_SUFFIXES = ("inc", "llc", "corp", "tech", "media", "group", "solutions", "labs")

# ---- Checker-only settings used by the command line tool -------------------
INPUT_FILE = "domains.json"
