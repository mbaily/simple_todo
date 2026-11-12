"""App configuration.

Keep this file simple: plain Python constants that can be imported by the server.
"""

# Pinned items older than this many days will be shown in a separate section at
# the bottom of the home page.
PINNED_OLD_DAYS = 10

# Calendar view settings
CALENDAR_LOOKAHEAD_DAYS = 120
CALENDAR_MAX_OCCURRENCES = 300

# Home page: show calendar occurrences within +/- this many days from today.
HOME_CALENDAR_AROUND_DAYS = 1

# Whether to show the calendar section above the lists section on the home page.
SHOW_CALENDAR_ABOVE_LISTS = False

# Whether to show today's calendar events above the pinned section on the home page.
SHOW_TODAY_CALENDAR_ABOVE_PINNED = True

# Number of days of past uncompleted calendar events to show above the pinned section.
# This is related to the feature enabled with SHOW_TODAY_CALENDAR_ABOVE_PINNED
# If 0, this feature is disabled. 1 = yesterday.
PAST_UNCOMPLETED_CALENDAR_DAYS = 1

# Hueristic to reduce clutter from recurring events in calendar view.
# If True, only the next N upcoming occurrences of a recurring event will be shown.
RRULE_COLLAPSE_FUTURE = True
RRULE_HIDE_FUTURE_OCCURRENCES_LIMIT = 2

# Uncompleted page: whether to include uncompleted "collected" todos under
# collation lists.
SHOW_COLLATION_COLLECTED_IN_UNCOMPLETED = True

# Home page: whether the uncompleted count shown next to collation list names
# should include uncompleted todos that are "collected" into that collation list.
COLLATION_UNCOMPLETED_COUNT_INCLUDES_COLLECTED = True

# DokuWiki integration
ENABLE_DOKUWIKI_LINKS = False
# Available variables: year, month_short (jan), month_long (january), wday_occurrence (fri, fri1, fri2...)
DOKUWIKI_LINK_FSTRING = ""

# Whether to require multiple clicks (burst) to confirm a DokuWiki link.
# If False, a single click confirms it immediately.
DOKUWIKI_BURST_CONFIRMATION = False

# Per-item DokuWiki page links (unique page per todo or list)
# Available variables: item_type (todo/list), item_id (database ID)
DOKUWIKI_ITEM_LINK_FSTRING = ""

# Whether to load default smart folders from default_smart_folders.json on startup
LOAD_DEFAULT_SMART_FOLDERS = True

# Disable client-side IndexedDB offline caching
DISABLE_OFFLINE_CACHE = False

# Whether to show the Named Searches section on the /hashtags page.
# Named Searches are somewhat redundant with Smart Folders.
# Whether to show the Named Searches section on the /hashtags page.
# Named Searches are somewhat redundant with Smart Folders.
SHOW_NAMED_SEARCHES = False

# Sync Configuration
# Mode: 'server' or 'client'
SIMPLE_TODO_MODE = "server"
# URL of the main server (for client mode)
SYNC_SERVER_URL = "http://localhost:8000"
# Client ID (optional, for tracking sources)
CLIENT_ID = None

# Date Format Configuration
# Use Python strftime format codes: https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes
# Common examples:
# "%Y-%m-%d %H:%M"  -> 2025-01-31 14:30
# "%d/%m/%Y %I:%M %p" -> 31/01/2025 02:30 PM
#
# To strip leading zeros (e.g. "Jan 5" instead of "Jan 05"):
# On Linux/macOS, use the "-" modifier: "%-d", "%-m", "%-I"
# On Windows, use the "#" modifier: "%#d", "%#m", "%#I"
#
# Examples:
# American (US):
# DATETIME_FORMAT = "%m/%d/%Y %I:%M %p"  # e.g., 01/31/2025 02:30 PM
# DATE_FORMAT = "%m/%d/%Y"               # e.g., 01/31/2025
#
# UK / Australian:
# DATETIME_FORMAT = "%d/%m/%Y %H:%M"     # e.g., 31/01/2025 14:30
# DATE_FORMAT = "%d/%m/%Y"               # e.g., 31/01/2025
#
# Default Date+Time: "%Y-%m-%d %H:%M" (ISO-like)
DATETIME_FORMAT = "%Y-%m-%d %H:%M"

# Default Date Only: "%Y-%m-%d"
DATE_FORMAT = "%Y-%m-%d"

# Localization configuration
# The default language to use for the UI.
# Available options: 'en' (English), 'es' (Spanish)
LOCALE = 'en'

# List of supported locales
SUPPORTED_LOCALES = ['en', 'es', 'fr', 'de', 'ja', 'pl']

# Quick Add Forms
# Whether to show the compact, always-visible quick-add forms for creating lists
# (on the home page) and todos (on the list view page).
SHOW_QUICK_ADD_FORMS = False

# Session-based authentication
# How many days a login session cookie lasts before the user must re-authenticate.
# Only relevant when SIMPLE_TODO_BASIC_AUTH is enabled.
SESSION_EXPIRY_DAYS = 30


# Site-specific overrides belong to the data directory, not this source file.
# Built-in constants above remain the defaults. Per-user preferences are stored
# in each user's SQLite database.
def _load_site_settings():
    import json
    from storage import data_path, external_data_enabled

    if not external_data_enabled():
        return
    path = data_path("settings.json")
    if not path.exists():
        return
    overrides = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(overrides, dict):
        raise ValueError(f"{path} must contain a JSON object")
    for key, value in overrides.items():
        if not key.isupper() or key not in globals():
            raise ValueError(f"Unknown site setting in {path}: {key}")
        default = globals()[key]
        if default is not None and type(value) is not type(default):
            raise ValueError(f"Invalid type for site setting in {path}: {key}")
        globals()[key] = value


_load_site_settings()
