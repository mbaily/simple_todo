import re
import os
import json
import base64
import html
import binascii
import hashlib
import hmac
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from datetime import date as date_type
import calendar
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlencode, urlsplit
import random
import asyncio
import logging
from typing import Optional
from fastapi import FastAPI, Depends, Request, Form, HTTPException, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.orm import Session, aliased, joinedload
from sqlalchemy import or_, func, select

# Load site overrides before modules import configuration constants. Invalid
# external settings must stop startup rather than silently use other defaults.
import config
from storage import data_path, external_data_enabled, initialize_storage
from database import init_db, get_session_maker
from entity_history import maintain_entity_histories
from models import (
    List,
    Todo,
    Note,
    Hashtag,
    NamedSearch,
    CalendarEvent,
    CalendarEventCompletion,
    CalendarDay,
    ListDatetimeHeader,
    format_local_datetime,
    format_local_date,
    SmartFolder,
    SmartFolderIncludeHashtag,
    SmartFolderExcludeHashtag,
    collation_todos,
    DokuWikiConfirmedItem,
    JournalEntry,
    Link,
    UserSettings,
    Folder,
    Category,
    EventGroup,
    IngredientEvent,
    IngredientGroup,
    Bill,
    BillPayment,
    BillPaymentSubmission,
    BillDueDateSubmission,
    BillRecordSubmission,
    BillAdjustment,
    BillAdjustmentAllocation,
    BillStatement,
    BillInvoice,
    BillAllocation,
    Expense,
    utcnow,
    _to_date,
)

# Import helper functions from new modules
from calendar_service import (
    validate_calendar_recurrence, sort_calendar_occurrences,
    CALENDAR_SORT_OPTIONS, DEFAULT_CALENDAR_SORT,
    parse_calendar_start, parse_calendar_duration, calendar_week, calendar_occurrences,
)
from task_moves import move_tagged_records, MoveError
from item_actions import delete_items, pin_items, archive_items
from helpers import (
    get_wday_occurrence,
    generate_dokuwiki_link,
    generate_dokuwiki_item_link,
    random_hex_color,
    _normalize_rrule,
    _event_occurrences,
    _midnight,
    _parse_date_or_datetime,
    _parse_date_yyyy_mm_dd,
    parse_hashtags,
    get_or_create_hashtag,
    linkify_text,
    # Home page helpers
    separate_pinned_items,
    get_home_calendar_occurrences,
    get_active_alarms,
    get_resuming_items,
    get_uncompleted_counts,
    # List navigation
    get_list_navigation,
    # Smart folder helpers
    validate_sort_field,
    apply_dokuwiki_links,
    apply_dokuwiki_links_recursive,
    get_confirmed_items,
    # Smart folder form helpers
    parse_smart_folder_form,
    update_smart_folder_hashtags,
    update_smart_folder_collections,
    get_list_items_with_dividers,
    process_dokuwiki_click,
    get_next_uncompleted_occurrence,
)
from smart_folder_service import SmartFolderService
from billing_service import enable_ledger, money_cents, stored_amount, format_cents, bill_amount, estimate_amount, record_invoice, record_cash_payment, payment_submission, retain_payment_submission, reconcile_allocations, lock_account, sync_invoice_due_date, refresh_invoice_forecast, validate_invoice_account, validate_cash_date, validate_opening_balance, validate_recurrence_settings, DuplicateInvoiceError, advance_due_date as _advance_due_date
from billing_accounts import record_adjustment, record_statement, statement_comparisons
from expense_service import expense_amount, spending_summaries
from billing_submissions import submission_recorded, mark_record_deleted, mark_bill_records_deleted, due_date_submission
from serializers import (
    serialize_list,
    serialize_todo,
    serialize_folder,
    serialize_hashtag,
    serialize_calendar_event,
    serialize_calendar_event_completion,
    serialize_calendar_day,
    serialize_smart_folder,
    serialize_named_search,
    serialize_header,
    serialize_link,
    serialize_ingredient_group,
    serialize_ingredient_event,
    serialize_category,
    serialize_event_group,
    serialize_user_settings,
    serialize_bill,
    serialize_bill_payment,
    serialize_bill_payment_submission,
    serialize_bill_due_date_submission,
    serialize_bill_record_submission,
    serialize_bill_invoice,
    serialize_bill_allocation,
    serialize_bill_adjustment,
    serialize_bill_adjustment_allocation,
    serialize_bill_statement,
    serialize_expense,
)
from auth import (
    BasicAuthMiddleware,
    _basic_auth_enabled,
    get_authenticated_user,
    _load_htpasswd_users,
    _htpasswd_path,
    verify_credentials,
    _sign_session,
    SESSION_COOKIE_NAME,
    _get_session_expiry_days,
)
from sync_apply import apply_modifications
from merkle_sync import build_merkle_tree, compute_diff, extract_hashes

try:
    from config import PINNED_OLD_DAYS
    from config import CALENDAR_LOOKAHEAD_DAYS, CALENDAR_MAX_OCCURRENCES
    from config import HOME_CALENDAR_AROUND_DAYS
    from config import SHOW_COLLATION_COLLECTED_IN_UNCOMPLETED
    from config import COLLATION_UNCOMPLETED_COUNT_INCLUDES_COLLECTED
    from config import ENABLE_DOKUWIKI_LINKS, DOKUWIKI_LINK_FSTRING, DOKUWIKI_BURST_CONFIRMATION
    from config import DOKUWIKI_ITEM_LINK_FSTRING
    from config import LOAD_DEFAULT_SMART_FOLDERS
    from config import DISABLE_OFFLINE_CACHE
    from config import SHOW_NAMED_SEARCHES
    from config import SHOW_CALENDAR_ABOVE_LISTS
    from config import SHOW_TODAY_CALENDAR_ABOVE_PINNED
    from config import PAST_UNCOMPLETED_CALENDAR_DAYS
    from config import LOCALE
    from config import RRULE_COLLAPSE_FUTURE, RRULE_HIDE_FUTURE_OCCURRENCES_LIMIT
    from config import SHOW_QUICK_ADD_FORMS
    from config import SESSION_EXPIRY_DAYS
except Exception:
    # Safe fallback if config is missing or broken.
    PINNED_OLD_DAYS = 30
    CALENDAR_LOOKAHEAD_DAYS = 120
    CALENDAR_MAX_OCCURRENCES = 300
    HOME_CALENDAR_AROUND_DAYS = 7
    SHOW_COLLATION_COLLECTED_IN_UNCOMPLETED = False
    COLLATION_UNCOMPLETED_COUNT_INCLUDES_COLLECTED = False
    ENABLE_DOKUWIKI_LINKS = False
    DOKUWIKI_LINK_FSTRING = ""
    DOKUWIKI_BURST_CONFIRMATION = False
    DOKUWIKI_ITEM_LINK_FSTRING = ""
    LOAD_DEFAULT_SMART_FOLDERS = False
    DISABLE_OFFLINE_CACHE = False
    SHOW_NAMED_SEARCHES = False
    SHOW_CALENDAR_ABOVE_LISTS = False
    SHOW_TODAY_CALENDAR_ABOVE_PINNED = False
    PAST_UNCOMPLETED_CALENDAR_DAYS = 0
    LOCALE = 'en'
    RRULE_COLLAPSE_FUTURE = False
    RRULE_HIDE_FUTURE_OCCURRENCES_LIMIT = 2
    SHOW_QUICK_ADD_FORMS = True

# get_wday_occurrence and generate_dokuwiki_link are imported from helpers.py


def create_journal_entry(db: Session, action: str, details: str = None, entity_type: str = None, entity_id: int = None):
    """Creates a journal entry for a write interaction."""
    entry = JournalEntry(
        action=action,
        details=details,
        entity_type=entity_type,
        entity_id=entity_id
    )
    db.add(entry)


try:
    from dateutil.rrule import rrulestr
except Exception:
    rrulestr = None

# Authentication code is imported from auth.py (BasicAuthMiddleware, _basic_auth_enabled, get_authenticated_user)


def form_text(field_name: str, *, required: bool = False):
    """Keep an explicitly cleared field distinct from an omitted form field."""
    async def read_value(
        request: Request, value: Optional[str] = Form(None, alias=field_name),
    ) -> Optional[str]:
        # FastAPI treats empty form strings as missing when applying defaults.
        form = await request.form()
        if required and field_name not in form:
            raise RequestValidationError([{
                "type": "missing", "loc": ("body", field_name),
                "msg": "Field required", "input": None,
            }])
        return form.get(field_name, value)

    return read_value


def get_db(request: Request):
    """Dependency for getting database session, scoped by authenticated user."""
    user = get_authenticated_user(request)
    SessionLocal = get_session_maker(user)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def apply_default_smart_folders(db: Session, defaults: list):
    """Create default smart folders if they don't exist."""
    for data in defaults:
        name = data.get("name")
        if not name:
            continue
        
        # Check if exists
        existing = db.query(SmartFolder).filter(SmartFolder.name == name).first()
        if existing:
            continue
            
        print(f"Creating default smart folder: {name}")
        
        # Create SmartFolder
        sf = SmartFolder(
            name=name,
            show_lists=data.get("show_lists", True),
            show_todos=data.get("show_todos", True),
            group_todos_by_list=data.get("group_todos_by_list", True),
            root_todos_only=data.get("root_todos_only", False),
            nest_subtodos=data.get("nest_subtodos", False),
            uncompleted_only=data.get("uncompleted_only", False),
            include_archived_lists=data.get("include_archived_lists", False),
            list_sort_field=data.get("list_sort_field", "modified_at"),
            list_sort_desc=data.get("list_sort_desc", True),
            todo_sort_field=data.get("todo_sort_field", "modified_at"),
            todo_sort_desc=data.get("todo_sort_desc", True),
            dokuwiki_links_enabled=data.get("dokuwiki_links_enabled", False),
            dokuwiki_date_field=data.get("dokuwiki_date_field", "created_at"),
            include_root_folder=data.get("include_root_folder", False),
            exclude_root_folder=data.get("exclude_root_folder", False),
            strict_nesting=data.get("strict_nesting", False),
            section_order=data.get("section_order", "LET"),
        )
        
        if data.get("modified_after"):
            try:
                sf.modified_after = datetime.fromisoformat(data["modified_after"])
            except ValueError:
                pass
        if data.get("modified_before"):
            try:
                sf.modified_before = datetime.fromisoformat(data["modified_before"])
            except ValueError:
                pass
                
        db.add(sf)
        db.commit()
        db.refresh(sf)
        
        # Handle hashtags
        for h_data in data.get("include_hashtags", []):
            h_name = h_data.get("name")
            if not h_name: continue
            hashtag = db.query(Hashtag).filter(Hashtag.name == h_name).first()
            if not hashtag:
                hashtag = Hashtag(name=h_name)
                db.add(hashtag)
                db.commit()
            
            assoc = SmartFolderIncludeHashtag(
                smart_folder_id=sf.id,
                hashtag_id=hashtag.id,
                include_subtodos=h_data.get("include_subtodos", False)
            )
            db.add(assoc)

        for h_data in data.get("exclude_hashtags", []):
            h_name = h_data.get("name")
            if not h_name: continue
            hashtag = db.query(Hashtag).filter(Hashtag.name == h_name).first()
            if not hashtag:
                hashtag = Hashtag(name=h_name)
                db.add(hashtag)
                db.commit()
            
            assoc = SmartFolderExcludeHashtag(
                smart_folder_id=sf.id,
                hashtag_id=hashtag.id,
                include_subtodos=h_data.get("include_subtodos", False)
            )
            db.add(assoc)
            
        # Handle lists (by name)
        for l_name in data.get("lists", []):
            lst = db.query(List).filter(List.name == l_name).first()
            if lst:
                sf.lists.append(lst)
        
        db.commit()


def load_default_smart_folders():
    if not LOAD_DEFAULT_SMART_FOLDERS:
        return

    json_path = data_path("default_smart_folders.json")
    if not os.path.exists(json_path):
        return

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            defaults = json.load(f)
    except Exception as e:
        print(f"Error loading default smart folders: {e}")
        return

    if not isinstance(defaults, list):
        return

    # Apply to default DB
    SessionLocal = get_session_maker("default")
    db = SessionLocal()
    try:
        apply_default_smart_folders(db, defaults)
    finally:
        db.close()

    # Apply to all known users if basic auth is enabled
    if _basic_auth_enabled():
        users = _load_htpasswd_users(_htpasswd_path())
        for user in users:
            SessionLocal = get_session_maker(user)
            db = SessionLocal()
            try:
                apply_default_smart_folders(db, defaults)
            finally:
                db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # FastAPI deprecated on_event("startup") in favor of lifespan handlers.
    initialize_storage()
    if external_data_enabled() and os.environ.get("SIMPLE_TODO_DB_PATH"):
        print("INFO:    SIMPLE_TODO_DB_PATH ignored; SIMPLE_TODO_DATA_DIR uses separate user databases.")
    # Validate the auth path even before the first authenticated request.
    _htpasswd_path()
    if _basic_auth_enabled():
        print(f"INFO:    Basic Auth ENABLED. Using .htpasswd file at: {_htpasswd_path()}")
    else:
        print("INFO:    Basic Auth DISABLED.")
        if not os.environ.get('SIMPLE_TODO_BASIC_AUTH'):
            print("         Reason: SIMPLE_TODO_BASIC_AUTH is not set to '1' or 'true'.")
        else:
            print(f"         Reason: .htpasswd file not found at expected path: {_htpasswd_path()}")

    init_db()
    load_default_smart_folders()
    async def history_maintenance():
        while True:
            try:
                await asyncio.to_thread(maintain_entity_histories)
            except Exception:
                logging.getLogger(__name__).exception("Entity history maintenance failed")
            await asyncio.sleep(6 * 60 * 60)

    maintenance_task = asyncio.create_task(history_maintenance())
    try:
        yield
    finally:
        maintenance_task.cancel()
        try:
            await maintenance_task
        except asyncio.CancelledError:
            pass


class CachedStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        # 1 year cache for static assets. We rely on query params ?v=... for cache busting.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


class RootPathRedirectMiddleware(BaseHTTPMiddleware):
    """Normalize paths and redirect headers when served behind a reverse
    proxy that strips a public prefix (e.g. Caddy ``handle_path /simple_todo/*``
    plus ``SIMPLE_TODO_ROOT_PATH=/simple_todo``).

    Starlette's ``request.url_for()`` already accounts for ``root_path``, but plain
    ``RedirectResponse(url="/foo")`` calls do not, so without this the browser
    would be redirected to the domain root instead of back under the mount path.
    """

    async def dispatch(self, request: Request, call_next):
        root_path = request.scope.get("root_path", "").rstrip("/")
        # StaticFiles combines the public root_path with its /static mount.
        # Give it the full public path even when Caddy stripped the prefix.
        # Uvicorn --root-path already supplies this form, so avoid adding it twice.
        path = request.scope["path"]
        if root_path and path != root_path and not path.startswith(root_path + "/"):
            request.scope["path"] = root_path + path
            if "raw_path" in request.scope:
                request.scope["raw_path"] = root_path.encode() + request.scope["raw_path"]
        response = await call_next(request)
        if root_path:
            location = response.headers.get("location")
            location_path = urlsplit(location).path if location else ""
            if (location and location.startswith("/") and not location.startswith("//")
                    and location_path != root_path and not location_path.startswith(root_path + "/")):
                response.headers["location"] = root_path + location
        return response


app = FastAPI(
    title="Simple Todo App",
    lifespan=lifespan,
    root_path=os.getenv("SIMPLE_TODO_ROOT_PATH", "").rstrip("/"),
)

# Optional HTTP Basic Auth (enabled via env vars).
app.add_middleware(BasicAuthMiddleware)
# Rewrites redirect Location headers to include root_path (added after/outside
# BasicAuthMiddleware so it also sees the /login redirects that middleware issues).
app.add_middleware(RootPathRedirectMiddleware)

# Mount static files and templates (absolute paths so the app doesn't depend on cwd)
BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", CachedStaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# i18n setup
try:
    locale_path = BASE_DIR / "locales" / f"{LOCALE}.json"
    if locale_path.exists():
        with open(locale_path, "r", encoding="utf-8") as f:
            _translations = json.load(f)
    else:
        _translations = {}
except Exception as e:
    print(f"Error loading translations: {e}")
    _translations = {}

def trans(key: str, default: str = None) -> str:
    """Translate a key to the current language."""
    return _translations.get(key, default if default is not None else key)

templates.env.globals["trans"] = trans
templates.env.globals["t"] = trans
templates.env.globals['max'] = max
templates.env.globals['min'] = min
templates.env.globals["disable_offline_cache"] = DISABLE_OFFLINE_CACHE
templates.env.globals["basic_auth_enabled"] = _basic_auth_enabled()
templates.env.globals["new_invoice_token"] = lambda: uuid4().hex
templates.env.filters["cents"] = format_cents
templates.env.filters["bill_amount"] = bill_amount

def concat(a, b):
    return (a or []) + (b or [])
templates.env.filters['concat'] = concat

# Date formatting filters using config
from helpers import format_configurable_date, format_configurable_datetime
templates.env.filters['date_format'] = format_configurable_date
templates.env.filters['datetime_format'] = format_configurable_datetime


@app.get("/favicon.ico")
def favicon() -> Response:
    # Avoid Safari/Chrome default favicon probes causing auth prompts and 404s.
    return Response(status_code=204, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/apple-touch-icon.png")
def apple_touch_icon() -> Response:
    return Response(status_code=204, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/apple-touch-icon-precomposed.png")
def apple_touch_icon_precomposed() -> Response:
    return Response(status_code=204, headers={"Cache-Control": "public, max-age=86400"})


# ---------------------------------------------------------------------------
# LOGIN / LOGOUT
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", error: str = ""):
    """Show the login form."""
    # If auth is disabled, just redirect home
    if not _basic_auth_enabled():
        return RedirectResponse(url="/", status_code=302)
    # If already authenticated via cookie, redirect
    from auth import _verify_session
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if _verify_session(cookie):
        return RedirectResponse(url=next or "/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {
        "request": request,
        "next_url": next,
        "error": error,
    })


@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
):
    """Validate credentials and set session cookie."""
    if not _basic_auth_enabled():
        return RedirectResponse(url="/", status_code=302)

    username = username.strip()
    if not username or not password:
        return templates.TemplateResponse(request, "login.html", {
            "request": request,
            "next_url": next,
            "error": "Please enter both username and password.",
        })

    if not verify_credentials(username, password):
        return templates.TemplateResponse(request, "login.html", {
            "request": request,
            "next_url": next,
            "error": "Invalid username or password.",
        })

    # Success — set signed cookie and redirect
    redirect_url = next if next and next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(url=redirect_url, status_code=302)
    # Scope the cookie to this app's mount path (root_path) rather than "/" so it
    # isn't sent to unrelated apps sharing the same domain, and mark it secure
    # whenever the (possibly proxied) request came in over HTTPS.
    cookie_path = request.scope.get("root_path") or "/"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_sign_session(username),
        max_age=_get_session_expiry_days() * 86400,
        httponly=True,
        samesite="lax",
        secure=(request.url.scheme == "https"),
        path=cookie_path,
    )
    return response


@app.get("/logout")
def logout(request: Request):
    """Clear the session cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=302)
    cookie_path = request.scope.get("root_path") or "/"
    response.delete_cookie(key=SESSION_COOKIE_NAME, path=cookie_path)
    return response


# utcnow() is imported from models.py
# Utility functions (random_hex_color, _normalize_rrule, _event_occurrences, _midnight, 
# _parse_date_or_datetime, parse_hashtags, get_or_create_hashtag) are imported from helpers.py


# =============================================================================
# SYNC ENDPOINTS
# =============================================================================

@app.post("/sync/push", response_class=JSONResponse)
async def sync_push(
    request: Request,
    modifications: dict, # Pydantic model would be better but dict is flexible
    db: Session = Depends(get_db)
):
    """
    Receive modifications from a client and apply them.
    Returns: ID mapping for any newly created items.
    """
    # Security: In real world, verify client_id / auth. 
    # Current BasicAuthMiddleware covers this if enabled.
    
    try:
        # modifications is expected to be {"todos": [...], "lists": [...], ...}
        id_map = apply_modifications(db, modifications)
        return {"id_map": id_map}
    except Exception as e:
        print(f"Sync Push Error: {e}")
        # Return 500 or 400?
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sync/pull", response_class=JSONResponse)
async def sync_pull(
    request: Request,
    client_hashes: dict, # {"root": "...", "data": "...", ...}
    db: Session = Depends(get_db)
):
    """
    Receive client's merkle tree hashes.
    Returns: Diff of subtrees that have changed on server.
    """
    try:
        # Build server tree
        template_dir = BASE_DIR / "templates"
        server_tree = build_merkle_tree(db, template_dir)
        
        # Compute diff
        diff = compute_diff(client_hashes, server_tree)
        
        # If diff is None, it means fully in sync
        if diff is None:
            return {"status": "synced", "diff": None}
            
        return {"status": "diff", "diff": diff}
        
    except Exception as e:
        print(f"Sync Pull Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))





# =============================================================================
# MAIN PAGES
# =============================================================================


def get_user_settings(db: Session) -> UserSettings:
    settings = db.query(UserSettings).filter(UserSettings.id == 1).first()
    if not settings:
        settings = UserSettings(id=1, show_completed=True, show_delayed=True)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@app.get("/customization", response_class=HTMLResponse)
def view_customization(request: Request, db: Session = Depends(get_db)):
    """View customization settings page."""
    settings = get_user_settings(db)
    return templates.TemplateResponse(request, "customization.html", {
        "request": request,
        "settings": settings,
        "all_hashtags": db.query(Hashtag).order_by(Hashtag.name).all() # For autocomplete if we want
    })


@app.post("/customization", response_class=HTMLResponse)
def update_customization(
    request: Request,
    filter_inc_hashtags: str = Form(""),
    filter_exc_hashtags: str = Form(""),
    db: Session = Depends(get_db)
):
    """Update user customization settings."""
    settings = get_user_settings(db)
    settings.filter_inc_hashtags = filter_inc_hashtags.strip()
    settings.filter_exc_hashtags = filter_exc_hashtags.strip()
    
    db.commit()
    
    return RedirectResponse(url="/customization?saved=1", status_code=303)


@app.get("/api/export")
def export_data(db: Session = Depends(get_db)):
    """Export all user data as JSON."""
    
    # helper for note dicts
    def note_to_dict(n):
        return {
            "id": n.id,
            "text": n.text,
            "name": n.name,
            "created_at": n.created_at.isoformat() if n.created_at else None,
            "modified_at": n.modified_at.isoformat() if n.modified_at else None,
            "list_id": n.list_id,
            "todo_id": n.todo_id,
            "calendar_event_id": n.calendar_event_id,
            "occurrence_at": n.occurrence_at.isoformat() if n.occurrence_at else None
        }

    # Export collation_todos junction table (has sort_order data)
    from sqlalchemy import select
    collation_rows = db.execute(select(collation_todos)).fetchall()
    collation_todos_data = [
        {
            "collation_list_id": row.collation_list_id,
            "todo_id": row.todo_id,
            "sort_order": row.sort_order,
        }
        for row in collation_rows
    ]

    data = {
        "version": 2,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "data": {
            "folders": [serialize_folder(f) for f in db.query(Folder).all()],
            "lists": [serialize_list(l) for l in db.query(List).all()],
            "todos": [serialize_todo(t) for t in db.query(Todo).all()],
            "notes": [note_to_dict(n) for n in db.query(Note).all()],
            "hashtags": [serialize_hashtag(h) for h in db.query(Hashtag).all()],
            "calendar_events": [serialize_calendar_event(e) for e in db.query(CalendarEvent).all()],
            "calendar_event_completions": [serialize_calendar_event_completion(c) for c in db.query(CalendarEventCompletion).all()],
            "calendar_days": [serialize_calendar_day(day) for day in db.query(CalendarDay).all()],
            "event_groups": [serialize_event_group(g) for g in db.query(EventGroup).all()],
            "headers": [serialize_header(h) for h in db.query(ListDatetimeHeader).all()],
            "smart_folders": [serialize_smart_folder(sf) for sf in db.query(SmartFolder).all()],
            "named_searches": [serialize_named_search(ns) for ns in db.query(NamedSearch).all()],
            "links": [serialize_link(lk) for lk in db.query(Link).all()],
            "ingredient_groups": [serialize_ingredient_group(ig) for ig in db.query(IngredientGroup).all()],
            "ingredient_events": [serialize_ingredient_event(ie) for ie in db.query(IngredientEvent).all()],
            "categories": [serialize_category(c) for c in db.query(Category).all()],
            "user_settings": [serialize_user_settings(s) for s in db.query(UserSettings).all()],
            "collation_todos": collation_todos_data,
            "bills": [serialize_bill(b) for b in db.query(Bill).all()],
            "bill_payments": [serialize_bill_payment(p) for p in db.query(BillPayment).all()],
            'bill_payment_submissions': [serialize_bill_payment_submission(s) for s in db.query(BillPaymentSubmission).all()],
            'bill_record_submissions': [serialize_bill_record_submission(s) for s in db.query(BillRecordSubmission).all()],
            'bill_due_date_submissions': [serialize_bill_due_date_submission(s) for s in db.query(BillDueDateSubmission).all()],
            "bill_invoices": [serialize_bill_invoice(i) for i in db.query(BillInvoice).all()],
            "bill_allocations": [serialize_bill_allocation(a) for a in db.query(BillAllocation).all()],
            'bill_adjustments': [serialize_bill_adjustment(a) for a in db.query(BillAdjustment).all()],
            'bill_adjustment_allocations': [serialize_bill_adjustment_allocation(a) for a in db.query(BillAdjustmentAllocation).all()],
            'bill_statements': [serialize_bill_statement(s) for s in db.query(BillStatement).all()],
            "expenses": [serialize_expense(e) for e in db.query(Expense).all()],
        }
    }
    
    filename = f"simple_todo_export_{datetime.now().strftime('%Y-%m-%d')}.json"
    headers = {"Content-Disposition": f"attachment; filename=\"{filename}\""}
    return JSONResponse(content=data, headers=headers)


from fastapi import UploadFile, File
from import_logic import import_data_from_json

@app.post("/api/import")
async def import_data(
    file: UploadFile = File(...),
    restore_history: bool = Form(False),
    db: Session = Depends(get_db)
):
    """Import data from a JSON file (Upsert only)."""
    try:
        content = await file.read()
        json_data = json.loads(content.decode("utf-8"))
        
        stats = import_data_from_json(db, json_data, restore_history=restore_history)
        
        return RedirectResponse(
            url=f"/customization?import_success=1&created={stats['created']}&updated={stats['updated']}", 
            status_code=303
        )
    except Exception as e:
        db.rollback()
        message = str(e)
        if not message.endswith('No changes were saved.'):
            message = f'{message.rstrip(".")}. No changes were saved.'
        print(f"Import Error: {message}")
        return RedirectResponse(url='/customization?' + urlencode({'import_error': message}), status_code=303)



# =============================================================================
# FOLDERS
# =============================================================================

@app.get("/folders", response_class=HTMLResponse)
def view_folders(request: Request, db: Session = Depends(get_db)):
    """Manage folders."""
    settings = get_user_settings(db)
    
    # Fetch only root nodes for the tree view, relationships will handle the rest
    root_folders = db.query(Folder).filter(Folder.parent_id == None).order_by(Folder.sort_order, Folder.name).all()
    root_lists = db.query(List).filter(List.archived == False, List.folder_id == None).order_by(List.name).all()
    
    # Current active folder
    active_folder = None
    if settings.active_folder_id:
        active_folder = db.query(Folder).filter(Folder.id == settings.active_folder_id).first()

    return templates.TemplateResponse(request, "folders.html", {
        "request": request,
        "root_folders": root_folders,
        "root_lists": root_lists,
        "active_folder": active_folder,
        "settings": settings,
        "all_folders": db.query(Folder).order_by(Folder.parent_id, Folder.sort_order, Folder.name).all(),
        "all_lists": db.query(List).filter(List.archived == False).order_by(List.name).all(),
    })


@app.get("/folders/tree", response_class=HTMLResponse)
def get_folder_tree(request: Request, db: Session = Depends(get_db)):
    """Return the folder tree partial HTML for reactive client updates."""
    settings = get_user_settings(db)
    root_folders = db.query(Folder).filter(Folder.parent_id == None).order_by(Folder.sort_order, Folder.name).all()
    root_lists = db.query(List).filter(List.archived == False, List.folder_id == None).order_by(List.name).all()
    active_folder = None
    if settings.active_folder_id:
        active_folder = db.query(Folder).filter(Folder.id == settings.active_folder_id).first()
    return templates.TemplateResponse(request, "partials/folder_tree.html", {
        "request": request,
        "root_folders": root_folders,
        "root_lists": root_lists,
        "active_folder": active_folder,
        "settings": settings,
    })


@app.post("/folders/reorder")
def reorder_folder(
    request: Request,
    source_id: int = Form(...),
    target_id: int = Form(...),
    db: Session = Depends(get_db)
):
    is_hx = bool(request.headers.get("HX-Request"))
    source = db.query(Folder).filter(Folder.id == source_id).first()
    target = db.query(Folder).filter(Folder.id == target_id).first()
    
    if not source or not target:
        if is_hx:
            return JSONResponse({"detail": "Folder not found"}, status_code=404)
        return RedirectResponse(url="/folders?error=not_found", status_code=303)
        
    if source.id == target.id:
        if is_hx:
            return JSONResponse({"status": "ok"})
        return RedirectResponse(url="/folders", status_code=303)
        
    # User Requirement: "if they are within the same exact parent"
    if source.parent_id != target.parent_id:
        if is_hx:
            return JSONResponse({"detail": "Different parent reorder not supported"}, status_code=400)
        return RedirectResponse(url="/folders?error=different_parent_reorder_not_supported", status_code=303)
        
    # Get all siblings, ordered currently
    limit = 1000 # Safety
    siblings = db.query(Folder).filter(Folder.parent_id == source.parent_id).order_by(Folder.sort_order, Folder.name).limit(limit).all()
    
    # Create a new list order
    new_order = []
    
    # Add all except source
    for f in siblings:
        if f.id != source.id:
            new_order.append(f)
            
    # Find index of target in new_order
    target_index = -1
    for i, f in enumerate(new_order):
        if f.id == target.id:
            target_index = i
            break
            
    if target_index != -1:
        # "moves the source folder to below the dest folder"
        new_order.insert(target_index + 1, source)
    else:
        # Fallback (shouldn't happen if parent check passed)
        new_order.append(source)
        
    # Commit new sort orders (spacing by 10)
    for i, f in enumerate(new_order):
        f.sort_order = i * 10
        
    db.commit()
    
    if is_hx:
        return JSONResponse({"status": "ok"})
    return RedirectResponse(url="/folders", status_code=303)


@app.post("/folders/create")
def create_folder(
    request: Request,
    name: str = Form(...),
    parent_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    if parent_id == 0:
        parent_id = None
        
    folder = Folder(name=name, parent_id=parent_id)
    db.add(folder)
    db.flush()
    create_journal_entry(db, "Created Folder", f"Name: {name}", "folder", folder.id)
    db.commit()
    
    if request.headers.get("HX-Request"):
        return JSONResponse({"status": "ok", "id": folder.id, "name": folder.name})
    return RedirectResponse(url="/folders", status_code=303)


@app.post("/folders/move_list")
def move_list_to_folder(
    request: Request,
    list_id: int = Form(...),
    folder_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    lst = db.query(List).filter(List.id == list_id).first()
    if not lst:
         raise HTTPException(status_code=404, detail="List not found")
         
    if folder_id == 0: # 0 means Root
        folder_id = None
        
    old_folder = lst.folder_id
    lst.folder_id = folder_id
    create_journal_entry(db, "Moved List", f"List {lst.name} to folder {folder_id}", "list", lst.id)
    db.commit()
    
    if request.headers.get("HX-Request"):
        return JSONResponse({"status": "ok"})
    return RedirectResponse(url="/folders", status_code=303)


@app.post("/folders/move")
def move_item(
    request: Request,
    source_type: str = Form(...), # 'list' or 'folder'
    source_id: int = Form(...),
    target_folder_id: Optional[int] = Form(None), # 0 or None means Root
    db: Session = Depends(get_db)
):
    is_hx = bool(request.headers.get("HX-Request"))
    if target_folder_id == 0:
        target_folder_id = None
        
    if target_folder_id is not None:
        target_f = db.query(Folder).filter(Folder.id == target_folder_id).first()
        if not target_f:
            if is_hx:
                return JSONResponse({"detail": f"Target folder {target_folder_id} not found"}, status_code=404)
            return RedirectResponse(url="/folders?error=not_found", status_code=303)

    # Prevent creating cycles if moving folder to folder
    if source_type == 'folder':
        folder = db.query(Folder).filter(Folder.id == source_id).first()
        if not folder:
            if is_hx:
                return JSONResponse({"detail": f"Folder {source_id} not found"}, status_code=404)
            return RedirectResponse(url="/folders?error=not_found", status_code=303)

        if source_id == target_folder_id:
            # Cannot move folder into itself
            if is_hx:
                return JSONResponse({"detail": "Cannot move folder into itself"}, status_code=400)
            return RedirectResponse(url="/folders?error=cycle", status_code=303)
            
        # Check if target is a child of source (recursion)
        if target_folder_id:
            # Traverse up from target to see if we hit source
            curr = db.query(Folder).filter(Folder.id == target_folder_id).first()
            while curr:
                if curr.id == source_id:
                    if is_hx:
                        return JSONResponse({"detail": "Cannot move folder into its own subfolder"}, status_code=400)
                    return RedirectResponse(url="/folders?error=cycle", status_code=303)
                if not curr.parent_id:
                    break
                curr = db.query(Folder).filter(Folder.id == curr.parent_id).first()
        
        folder.parent_id = target_folder_id
        create_journal_entry(db, "Moved Folder", f"Folder {folder.name} to {target_folder_id or 'Root'}", "folder", folder.id)
        db.commit()

    elif source_type == 'list':
        lst = db.query(List).filter(List.id == source_id).first()
        if not lst:
            if is_hx:
                return JSONResponse({"detail": f"List {source_id} not found"}, status_code=404)
            return RedirectResponse(url="/folders?error=not_found", status_code=303)
        lst.folder_id = target_folder_id
        create_journal_entry(db, "Moved List", f"List {lst.name} to {target_folder_id or 'Root'}", "list", lst.id)
        db.commit()
    else:
        if is_hx:
            return JSONResponse({"detail": f"Invalid source_type {source_type}"}, status_code=400)
        return RedirectResponse(url="/folders?error=invalid", status_code=303)

    if is_hx:
        return JSONResponse({"status": "ok"})
    return RedirectResponse(url="/folders", status_code=303)


@app.post("/folders/set_active")
def set_active_folder(
    request: Request,
    folder_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    settings = get_user_settings(db)
    
    if folder_id == 0:
        settings.active_folder_id = None
    else:
        settings.active_folder_id = folder_id
        
    db.commit()
    if request.headers.get("HX-Request"):
        return JSONResponse({"status": "ok", "active_folder_id": settings.active_folder_id})
    return RedirectResponse(url="/folders", status_code=303)


@app.post("/folders/toggle_collapse")
def toggle_folder_collapse(
    request: Request,
    folder_id: int = Form(...),
    db: Session = Depends(get_db)
):
    folder = db.query(Folder).filter(Folder.id == folder_id).first()
    if folder:
        folder.is_collapsed = not folder.is_collapsed
        db.commit()
        
    if request.headers.get("HX-Request"):
        return JSONResponse({"status": "ok", "is_collapsed": folder.is_collapsed if folder else False})
    return RedirectResponse(url="/folders", status_code=303)


@app.post("/folders/rename")
def rename_folder(
    request: Request,
    folder_id: int = Form(...),
    new_name: str = Form(...),
    db: Session = Depends(get_db)
):
    folder = db.query(Folder).filter(Folder.id == folder_id).first()
    if folder and new_name.strip():
        folder.name = new_name.strip()
        create_journal_entry(db, "Renamed Folder", f"Renamed folder to {new_name}", "folder", folder.id)
        db.commit()
        
    if request.headers.get("HX-Request"):
        return JSONResponse({"status": "ok", "name": folder.name if folder else new_name})
    return RedirectResponse(url="/folders", status_code=303)


@app.post("/folders/delete")
def delete_folder(
    request: Request,
    folder_id: int = Form(...),
    db: Session = Depends(get_db)
):
    folder = db.query(Folder).filter(Folder.id == folder_id).first()
    if folder:
        # Move lists to root
        lists = db.query(List).filter(List.folder_id == folder.id).all()
        for lst in lists:
            lst.folder_id = None
        
        # Move subfolders to root
        subfolders = db.query(Folder).filter(Folder.parent_id == folder.id).all()
        for sub in subfolders:
            sub.parent_id = None
            
        # Check if this was the active folder
        settings = get_user_settings(db)
        if settings.active_folder_id == folder.id:
            settings.active_folder_id = None
            
        db.delete(folder)
        create_journal_entry(db, "Deleted Folder", f"Deleted folder {folder.name}", "folder", folder.id)
        db.commit()

    if request.headers.get("HX-Request"):
        return JSONResponse({"status": "ok"})
    return RedirectResponse(url="/folders", status_code=303)


def get_home_context(request: Request, sort: str, folder_id: Optional[int], db: Session):
    """Main page - view all lists (excluding archived)"""
    settings = get_user_settings(db)
    
    query = db.query(List).filter(List.archived == False)

    # Filter by Active Folder or Query Param Override
    effective_folder_id = None
    
    # Query param takes precedence (temporary view)
    if folder_id is not None:
         effective_folder_id = folder_id if folder_id > 0 else None
    # Fallback to persistent setting
    elif settings.active_folder_id:
         effective_folder_id = settings.active_folder_id
         
    if effective_folder_id:
        query = query.filter(List.folder_id == effective_folder_id)
        active_folder_name = db.query(Folder.name).filter(Folder.id == effective_folder_id).scalar()
    else:
        query = query.filter(List.folder_id == None)
        active_folder_name = None
    
    # Apply User Settings Filters to Lists (only if not viewing a specific folder via param)
    inc_tags = parse_hashtags(settings.filter_inc_hashtags)
    exc_tags = parse_hashtags(settings.filter_exc_hashtags)

    if folder_id is None:
        if inc_tags:
            # Lists must have at least one of the tags
            query = query.join(List.hashtags).filter(Hashtag.name.in_(inc_tags))
            
        if exc_tags:
            query = query.filter(~List.hashtags.any(Hashtag.name.in_(exc_tags)))
        
    if settings.hide_empty_lists:
        # User requested to remove this feature, but DB might have it set to True if they customized it earlier.
        # Since we removed ui control, we should arguably ignore it to revert to default behavior,
        # OR keep respecting it if it's set in DB.
        # The prompt says "remove the 'view options' part... I don't like it".
        # This implies "I don't like the functionality being active/messing with my view".
        # So I will stop applying it.
        pass

    if sort == "created":
        lists = query.order_by(List.created_at.desc()).all()
    elif sort == "user":
        lists = query.order_by(List.sort_order.asc(), List.created_at.desc()).all()
    else:
        lists = query.order_by(List.modified_at.desc()).all()

    # Apply User Settings Filters to Pinned Todos
    pinned_query = db.query(Todo).filter(Todo.pinned == True)
    
    # Removed global show_completed/delayed overrides per user request.

    if folder_id is None:
        if inc_tags:
             pinned_query = pinned_query.join(Todo.hashtags).filter(Hashtag.name.in_(inc_tags))
        if exc_tags:
             pinned_query = pinned_query.filter(~Todo.hashtags.any(Hashtag.name.in_(exc_tags)))

    pinned_todos = pinned_query.order_by(Todo.modified_at.desc()).all()
    pinned_lists = db.query(List).filter(List.archived == False, List.pinned == True).order_by(List.modified_at.desc()).all()
    all_hashtags = db.query(Hashtag).order_by(Hashtag.name).all()

    # Uncompleted todo counts per list (using helper)
    uncompleted_counts = get_uncompleted_counts(db, include_collected=COLLATION_UNCOMPLETED_COUNT_INCLUDES_COLLECTED)

    # Home calendar section: occurrences around today (using helper)
    home_events = db.query(CalendarEvent).order_by(CalendarEvent.start_at.asc()).all()
    home_occurrences = get_home_calendar_occurrences(db, home_events, HOME_CALENDAR_AROUND_DAYS)
    home_now = _midnight(datetime.now())
    home_cal_start = home_now - timedelta(days=HOME_CALENDAR_AROUND_DAYS)
    home_cal_end = home_now + timedelta(days=HOME_CALENDAR_AROUND_DAYS)

    today_calendar_occurrences = []
    if SHOW_TODAY_CALENDAR_ABOVE_PINNED:
        today_date = home_now.date()
        today_calendar_occurrences = [
            occ for occ in home_occurrences
            if occ['occurrence_at'].date() == today_date
        ]

        if PAST_UNCOMPLETED_CALENDAR_DAYS > 0:
            past_occurrences = get_home_calendar_occurrences(db, home_events, PAST_UNCOMPLETED_CALENDAR_DAYS)
            past_start_date = today_date - timedelta(days=PAST_UNCOMPLETED_CALENDAR_DAYS)
            
            for occ in past_occurrences:
                occ_date = occ['occurrence_at'].date()
                if past_start_date <= occ_date < today_date and not occ['completed']:
                    today_calendar_occurrences.append(occ)

            today_calendar_occurrences.sort(key=lambda x: x['occurrence_at'])

    # Separate pinned items into old and recent (using helper)
    threshold = utcnow() - timedelta(days=PINNED_OLD_DAYS)
    pinned_todos_old, pinned_todos_recent = separate_pinned_items(pinned_todos, threshold)
    pinned_lists_old, pinned_lists_recent = separate_pinned_items(pinned_lists, threshold)

    # Active alarms (using helper)
    active_alarms = get_active_alarms(db)

    # Resuming items (using helper)
    resuming_todos, resuming_lists = get_resuming_items(db)

    # Group lists by category
    uncategorized_lists = [lst for lst in lists if not lst.category]
    categorized_lists = {}
    for lst in lists:
        if lst.category:
            if lst.category not in categorized_lists:
                categorized_lists[lst.category] = []
            categorized_lists[lst.category].append(lst)
    # Sort category names alphabetically
    sorted_categories = sorted(categorized_lists.keys())

    return {
        "request": request,
        "lists": lists,
        "uncategorized_lists": uncategorized_lists,
        "categorized_lists": categorized_lists,
        "sorted_categories": sorted_categories,
        "sort": sort,
        "pinned_todos": pinned_todos,
        "pinned_lists": pinned_lists,
        "pinned_todos_recent": pinned_todos_recent,
        "pinned_todos_old": pinned_todos_old,
        "pinned_lists_recent": pinned_lists_recent,
        "pinned_lists_old": pinned_lists_old,
        "resuming_todos": resuming_todos,
        "resuming_lists": resuming_lists,
        "pinned_old_days": PINNED_OLD_DAYS,
        "all_hashtags": all_hashtags,
        "uncompleted_counts": uncompleted_counts,
        "home_calendar_occurrences": home_occurrences,
        "home_calendar_days": HOME_CALENDAR_AROUND_DAYS,
        "home_calendar_start": home_cal_start,
        "home_calendar_end": home_cal_end,
        "active_folder_name": active_folder_name,
        "active_alarms": active_alarms,
        "current_folder_id": effective_folder_id,
        "active_folder_id": settings.active_folder_id,
        "show_calendar_above_lists": SHOW_CALENDAR_ABOVE_LISTS,
        "show_today_calendar_above_pinned": SHOW_TODAY_CALENDAR_ABOVE_PINNED,
        "today_calendar_occurrences": today_calendar_occurrences,
        "show_quick_add_forms": SHOW_QUICK_ADD_FORMS,
        "enable_dokuwiki_links": ENABLE_DOKUWIKI_LINKS,
        "dokuwiki_item_link_fn": generate_dokuwiki_item_link,
    }


@app.get("/")
def default_home(request: Request):
    """The daisyUI frontend is the default; the original home is at /classic."""
    query = request.url.query
    return RedirectResponse(url=request.scope.get("root_path", "") + "/app" + (f"?{query}" if query else ""), status_code=302)


@app.get("/calendar")
@app.get("/bills")
def default_calendar_and_bills(request: Request):
    """Keep existing external links pointing at the current frontend."""
    page = request.url.path.rsplit("/", 1)[-1]
    query = request.url.query
    url = request.scope.get("root_path", "") + "/app/" + page
    return RedirectResponse(url=url + (f"?{query}" if query else ""), status_code=302)


@app.api_route("/daisyui_test", methods=["GET", "HEAD", "POST"])
@app.api_route("/daisyui_test/{path:path}", methods=["GET", "HEAD", "POST"])
def legacy_new_frontend_url(request: Request, path: str = ""):
    """The new frontend moved from /daisyui_test to /app; keep old bookmarks and open tabs working."""
    query = request.url.query
    url = request.scope.get("root_path", "") + "/app" + (f"/{path}" if path else "") + (f"?{query}" if query else "")
    # 308 keeps the method and body, so a form submitted from an old open page still works.
    return RedirectResponse(url=url, status_code=308)


@app.get("/classic", response_class=HTMLResponse)
def home(request: Request, sort: str = "user", folder_id: Optional[int] = None, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "index.html", get_home_context(request, sort, folder_id, db))


@app.get("/lists/reorder", response_class=HTMLResponse)
def reorder_lists_page(request: Request, folder_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Page to reorder lists in a user-defined order using drag handles (Alpine.js & HTMX)."""
    settings = get_user_settings(db)
    effective_folder_id = None
    if folder_id is not None:
        effective_folder_id = folder_id if folder_id > 0 else None
    elif settings.active_folder_id:
        effective_folder_id = settings.active_folder_id

    query = db.query(List).filter(List.archived == False)
    if effective_folder_id:
        query = query.filter(List.folder_id == effective_folder_id)
        active_folder_name = db.query(Folder.name).filter(Folder.id == effective_folder_id).scalar()
    else:
        query = query.filter(List.folder_id == None)
        active_folder_name = None

    lists = query.order_by(List.sort_order.asc(), List.created_at.desc()).all()
    uncompleted_counts = get_uncompleted_counts(db, include_collected=COLLATION_UNCOMPLETED_COUNT_INCLUDES_COLLECTED)
    all_folders = db.query(Folder).order_by(Folder.sort_order.asc(), Folder.name.asc()).all()

    return templates.TemplateResponse(request, "reorder_lists.html", {
        "request": request,
        "lists": lists,
        "current_folder_id": effective_folder_id,
        "active_folder_name": active_folder_name,
        "all_folders": all_folders,
        "uncompleted_counts": uncompleted_counts,
    })


@app.post("/lists/reorder")
async def save_list_order(request: Request, db: Session = Depends(get_db)):
    """Save the user-defined order for lists."""
    content_type = request.headers.get("content-type", "")
    ordered_ids = []
    if "application/json" in content_type:
        try:
            data = await request.json()
            ordered_ids = data.get("list_ids", [])
        except Exception:
            ordered_ids = []
    else:
        form = await request.form()
        raw_ids = form.getlist("list_ids")
        if len(raw_ids) == 1 and "," in str(raw_ids[0]):
            raw_ids = str(raw_ids[0]).split(",")
        for val in raw_ids:
            try:
                ordered_ids.append(int(val))
            except (ValueError, TypeError):
                pass

    for index, list_id in enumerate(ordered_ids):
        db.query(List).filter(List.id == list_id).update({List.sort_order: index})
    if ordered_ids:
        create_journal_entry(db, "Reordered Lists", f"Updated sort order for {len(ordered_ids)} lists")
    db.commit()

    if request.headers.get("HX-Request"):
        return HTMLResponse('<span style="color: #28a745; font-size: 0.9rem;">✓ Saved</span>')

    folder_param = request.query_params.get("folder_id")
    redirect_url = f"{request.scope.get('root_path', '')}/lists/reorder"
    if folder_param:
        redirect_url += f"?folder_id={folder_param}"
    return RedirectResponse(url=redirect_url, status_code=303)


@app.get("/archived", response_class=HTMLResponse)
def view_archived(request: Request, db: Session = Depends(get_db)):
    """View all archived lists"""
    lists = db.query(List).filter(List.archived == True).order_by(List.modified_at.desc()).all()
    all_hashtags = db.query(Hashtag).order_by(Hashtag.name).all()
    return templates.TemplateResponse(request, "archived.html", {
        "request": request,
        "lists": lists,
        "all_hashtags": all_hashtags
    })


@app.get("/uncompleted", response_class=HTMLResponse)
def view_uncompleted(request: Request, db: Session = Depends(get_db)):
    """View all uncompleted todos organized into a tree structure."""
    # Get all uncompleted todos from active lists
    todos = (
        db.query(Todo)
        .join(Todo.list)
        .filter(Todo.completed == False)
        .filter(List.archived == False)
        .order_by(List.name, Todo.modified_at.desc())
        .all()
    )
    
    from grouping_service import organize_todos_into_tree
    todo_tree = organize_todos_into_tree(db, todos) if todos else None
    
    return templates.TemplateResponse(request, "uncompleted.html", {
        "request": request,
        "todo_tree": todo_tree,
    })


def get_list_context(request: Request, list_id: int, folder_id: Optional[int], sort: str, highlight: Optional[int], db: Session):
    """View todos for a specific list"""
    settings = get_user_settings(db)

    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        raise HTTPException(status_code=404, detail="List not found")

    # Navigation across active lists (using helper)
    # Fix for "? / Total" bug: If no folder specified, default to list's own folder
    # to ensure the list is actually in the navigation set.
    nav_ctx_id = folder_id
    if nav_ctx_id is None:
        nav_ctx_id = todo_list.folder_id if todo_list.folder_id is not None else 0
        
    nav = get_list_navigation(db, list_id, settings, nav_ctx_id, sort)
    first_list_id = nav["first_id"]
    prev_list_id = nav["prev_id"]
    next_list_id = nav["next_id"]
    active_list_index = nav["current_index"]
    total_active_lists = nav["total_count"]
    
    # Only get root-level todos (no parent)
    todos_query = db.query(Todo).filter(Todo.list_id == list_id, Todo.parent_id == None)
    
    # Smart Highlight: If highlighting an item, we MUST show completed items to ensure visibility
    force_show_completed = bool(highlight)
    
    # Removed global settings overrides for show_completed/delayed per user request.
    
    if not todo_list.show_completed and not force_show_completed:
        todos_query = todos_query.filter(Todo.completed == False)
    
    # Filter delayed todos if show_delayed is False
    if not todo_list.show_delayed:
        now = utcnow()
        # We want to exclude todos where delayed_until > now
        # So we include todos where delayed_until is None OR delayed_until <= now
        todos_query = todos_query.filter(
            or_(
                Todo.delayed_until == None,
                Todo.delayed_until <= now
            )
        )

    # Apply Hashtag Filters
    inc_tags = parse_hashtags(settings.filter_inc_hashtags)
    if inc_tags:
        todos_query = todos_query.filter(Todo.hashtags.any(Hashtag.name.in_(inc_tags)))

    exc_tags = parse_hashtags(settings.filter_exc_hashtags)
    if exc_tags:
        todos_query = todos_query.filter(~Todo.hashtags.any(Hashtag.name.in_(exc_tags)))

    if todo_list.manual_sorting:
        todos = todos_query.order_by(Todo.sort_order.asc(), Todo.created_at.desc()).all()
    elif todo_list.sort_ascending:
        todos = todos_query.order_by(Todo.created_at.asc()).all()
    else:
        todos = todos_query.order_by(Todo.created_at.desc()).all()

    datetime_headers = (
        db.query(ListDatetimeHeader)
        .filter(ListDatetimeHeader.list_id == list_id)
        .order_by(ListDatetimeHeader.created_at.desc())
        .all()
    )
    has_datetime_headers = bool(datetime_headers)

    # Fetch Notes
    if todo_list.sort_ascending:
         notes = db.query(Note).filter(Note.list_id == list_id, Note.todo_id == None).order_by(Note.created_at.asc()).all()
    else:
         notes = db.query(Note).filter(Note.list_id == list_id, Note.todo_id == None).order_by(Note.created_at.desc()).all()

    collected_todos = []
    if todo_list.is_collation:
        # Fetch collected todos with their sort_order from the junction table
        if todo_list.manual_sorting:
            # Query with sort_order from junction table
            collected_query = (
                db.query(Todo, collation_todos.c.sort_order)
                .join(collation_todos, collation_todos.c.todo_id == Todo.id)
                .filter(collation_todos.c.collation_list_id == list_id)
            )
            if not todo_list.show_completed:
                collected_query = collected_query.filter(Todo.completed == False)
            collected_query = collected_query.order_by(collation_todos.c.sort_order.asc())
            collected_with_order = collected_query.all()
            # Extract todos and mark them as collected for template styling
            collected = []
            for row in collected_with_order:
                todo = row[0]
                todo._is_collected = True  # Mark for template
                todo._collation_sort_order = row[1]
                collected.append(todo)
            
            # When manual_sorting is on, ALWAYS merge collected into main list for unified ordering
            # Mark native todos too for unified sorting
            for t in todos:
                t._is_collected = False
                t._collation_sort_order = t.sort_order  # Use native sort_order
            
            todos.extend(collected)
            # Sort all by their respective sort_orders
            todos.sort(key=lambda t: t._collation_sort_order)
        else:
            collected = list(todo_list.collated_todos)
            if not todo_list.show_completed:
                collected = [t for t in collected if not t.completed]
            
            if todo_list.intersperse_collated_todos:
                # Merge into main list (chronological)
                todos.extend(collected)
            else:
                # Keep separate and sort by modified_at (default behavior)
                collected.sort(key=lambda t: t.modified_at, reverse=True)
                collected_todos = collected

    # Divider/Item generation (using helper)
    reverse_sort = not todo_list.sort_ascending
    items = get_list_items_with_dividers(
        db, todos, notes, datetime_headers, todo_list, reverse_sort
    )

    all_hashtags = db.query(Hashtag).order_by(Hashtag.name).all()

    collation_lists = db.query(List).filter(List.archived == False, List.is_collation == True).order_by(List.name.asc()).all()
    
    # Get all distinct categories for the category combobox
    all_categories = [cat.name for cat in db.query(Category).order_by(Category.name).all()]
    
    # Calculate total todo count (ignoring filters and completion) to detect "hidden items" state
    total_todo_count = db.query(func.count(Todo.id)).filter(
        Todo.list_id == list_id
    ).scalar()

    # Generate DokuWiki item link for the list
    list_dokuwiki_item_link = generate_dokuwiki_item_link("list", todo_list.id) if todo_list.dokuwiki_page_enabled else None

    return {
        "request": request,
        "list": todo_list,
        "todos": todos,
        "datetime_headers": datetime_headers,
        "has_datetime_headers": has_datetime_headers,
        "items": items,
        "prev_list_id": prev_list_id,
        "next_list_id": next_list_id,
        "first_list_id": first_list_id,
        "active_list_index": active_list_index,
        "total_active_lists": total_active_lists,
        "all_hashtags": all_hashtags,
        "show_completed": todo_list.show_completed or force_show_completed,
        "collation_lists": collation_lists,
        "collected_todos": collected_todos,
        "settings": settings,
        "folder_id": folder_id,
        "sort": sort,
        "highlight_id": highlight,
        "total_todo_count": total_todo_count,
        "show_quick_add_forms": SHOW_QUICK_ADD_FORMS,
        "all_categories": all_categories,
        "enable_dokuwiki_links": ENABLE_DOKUWIKI_LINKS,
        "list_dokuwiki_item_link": list_dokuwiki_item_link,
        "dokuwiki_item_link_fn": generate_dokuwiki_item_link,
    }


@app.get("/list/{list_id}", response_class=HTMLResponse)
def view_list(request: Request, list_id: int, folder_id: Optional[int] = None, sort: str = "user", highlight: Optional[int] = None, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "view_list.html", get_list_context(request, list_id, folder_id, sort, highlight, db))


@app.post("/datetime-header/create")
def create_datetime_header(
    list_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """Create a datetime header in a list."""
    now = utcnow()
    header = ListDatetimeHeader(list_id=list_id, created_at=now)
    db.add(header)
    db.flush()

    todo_list = db.query(List).filter(List.id == list_id).first()
    if todo_list:
        todo_list.modified_at = now

    create_journal_entry(db, "Created Datetime Header", f"Created datetime header in list {list_id}", "list_datetime_header", header.id)
    db.commit()
    return RedirectResponse(url=f"/list/{list_id}", status_code=303)


# linkify_text is imported from helpers.py


@app.post("/note/create", response_class=HTMLResponse)
def create_note(
    request: Request,
    list_id: int = Form(None),
    text: str = Form(...),
    name: str = Form(None),
    todo_id: int = Form(None),
    calendar_event_id: int = Form(None),
    occurrence_at: str = Form(None),
    return_to: str = Form(None),
    db: Session = Depends(get_db)
):
    # Parse occurrence_at if provided
    parsed_occurrence = None
    if occurrence_at and occurrence_at.strip():
        try:
            parsed_occurrence = datetime.strptime(occurrence_at.strip(), "%Y-%m-%d")
        except ValueError:
            pass
    
    note = Note(
        list_id=list_id,
        todo_id=todo_id,
        calendar_event_id=calendar_event_id,
        occurrence_at=parsed_occurrence,
        text=linkify_text(text),
        name=name
    )
    db.add(note)
    
    # Update modification timestamp on parent
    if list_id:
        todo_list = db.query(List).filter(List.id == list_id).first()
        if todo_list:
            todo_list.modified_at = utcnow()
    if calendar_event_id:
        event = db.query(CalendarEvent).filter(CalendarEvent.id == calendar_event_id).first()
        if event:
            event.modified_at = utcnow()
        
    db.commit()
    
    if return_to and return_to.startswith("/"):
        return RedirectResponse(url=return_to, status_code=303)

    # Redirect based on context
    if calendar_event_id:
        return RedirectResponse(url="/classic/calendar", status_code=303)
    return RedirectResponse(url=f"/list/{list_id}", status_code=303)


@app.get("/note/{note_id}/edit", response_class=HTMLResponse)
def edit_note_view(request: Request, note_id: int, db: Session = Depends(get_db)):
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "edit_note.html", {"request": request, "note": note})


@app.post("/note/{note_id}/edit", response_class=HTMLResponse)
def edit_note(
    request: Request,
    note_id: int,
    text: str = Form(...),
    name: str = Form(None),
    db: Session = Depends(get_db)
):
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404)
    
    # Strip existing anchors to avoid double linking, then re-linkify
    clean_text = re.sub(r'<a\s+[^>]*>(.*?)</a>', r'\1', text, flags=re.IGNORECASE | re.DOTALL)
    note.text = linkify_text(clean_text)
    note.name = name
    
    if note.list:
        note.list.modified_at = utcnow()
        
    db.commit()
    return RedirectResponse(url=f"/list/{note.list_id}", status_code=303)


@app.post("/note/{note_id}/delete", response_class=HTMLResponse)
def delete_note(
    request: Request,
    note_id: int,
    return_to: str = Form(None),
    db: Session = Depends(get_db)
):
    note = db.query(Note).filter(Note.id == note_id).first()
    if note:
        list_id = note.list_id
        calendar_event_id = note.calendar_event_id
        db.delete(note)
        
        if list_id:
            todo_list = db.query(List).filter(List.id == list_id).first()
            if todo_list:
                todo_list.modified_at = utcnow()
        if calendar_event_id:
            event = db.query(CalendarEvent).filter(CalendarEvent.id == calendar_event_id).first()
            if event:
                event.modified_at = utcnow()
            
        db.commit()
        
        if return_to and return_to.startswith("/"):
            return RedirectResponse(url=return_to, status_code=303)

        # Redirect based on context
        if calendar_event_id:
            return RedirectResponse(url="/classic/calendar", status_code=303)
        if list_id:
            return RedirectResponse(url=f"/list/{list_id}", status_code=303)
    if return_to and return_to.startswith("/"):
        return RedirectResponse(url=return_to, status_code=303)
    return RedirectResponse(url="/classic", status_code=303)


@app.post("/api/list/{list_id}/toggle-collation")
def toggle_list_collation(list_id: int, db: Session = Depends(get_db)):
    """Toggle whether a list is a collation list (AJAX)."""
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        return JSONResponse({"error": "List not found"}, status_code=404)

    todo_list.is_collation = not todo_list.is_collation
    if todo_list.is_collation and not todo_list.collation_color:
        todo_list.collation_color = random_hex_color()
    todo_list.modified_at = utcnow()
    create_journal_entry(db, "Toggled List Collation", f"Toggled collation for list '{todo_list.name}' to {todo_list.is_collation}", "list", todo_list.id)
    db.commit()
    return JSONResponse({
        "is_collation": todo_list.is_collation,
        "collation_color": todo_list.collation_color,
    })


@app.post("/api/collation/{collation_list_id}/todo/{todo_id}/toggle")
def toggle_todo_in_collation(collation_list_id: int, todo_id: int, db: Session = Depends(get_db)):
    """Toggle membership of a todo in a collation list."""
    collation_list = db.query(List).filter(List.id == collation_list_id).first()
    if not collation_list or not collation_list.is_collation:
        return JSONResponse({"error": "Collation list not found"}, status_code=404)

    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        return JSONResponse({"error": "Todo not found"}, status_code=404)

    # Rule: you can't toggle a collation list's own todos for other collation lists.
    if todo.list and todo.list.is_collation and todo.list_id != collation_list_id:
        return JSONResponse({"error": "Cannot collate todos that belong to a collation list"}, status_code=400)

    # Don't allow adding a collation list's own todos into its own collation set (they already appear there).
    if todo.list_id == collation_list_id:
        return JSONResponse({"included": False, "noop": True})

    included = any(l.id == collation_list_id for l in todo.collation_lists)
    if included:
        todo.collation_lists = [l for l in todo.collation_lists if l.id != collation_list_id]
        included = False
    else:
        todo.collation_lists.append(collation_list)
        included = True

    collation_list.modified_at = utcnow()
    create_journal_entry(db, f"{'Added to' if included else 'Removed from'} Collation", f"{'Added' if included else 'Removed'} todo '{todo.title}' {'to' if included else 'from'} collation list '{collation_list.name}'", "todo", todo.id)
    db.commit()
    return JSONResponse({"included": included})


@app.get("/todo/{todo_id}/edit", response_class=HTMLResponse)
def edit_todo_page(request: Request, todo_id: int, db: Session = Depends(get_db)):
    """Edit todo title and note page"""
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    
    all_hashtags = db.query(Hashtag).order_by(Hashtag.name).all()
    return templates.TemplateResponse(request, "edit_todo.html", {
        "request": request,
        "todo": todo,
        "all_hashtags": all_hashtags
    })


@app.get("/hashtags", response_class=HTMLResponse)
def view_hashtags(request: Request, db: Session = Depends(get_db)):
    """View all hashtags and named searches"""
    hashtags = db.query(Hashtag).order_by(Hashtag.name).all()
    named_searches = db.query(NamedSearch).order_by(NamedSearch.name).all()
    
    return templates.TemplateResponse(request, "hashtags.html", {
        "request": request,
        "hashtags": hashtags,
        "named_searches": named_searches,
        "show_named_searches": SHOW_NAMED_SEARCHES,
    })


@app.get("/collations", response_class=HTMLResponse)
def view_collations(request: Request, db: Session = Depends(get_db)):
    """View all collation lists."""
    collation_lists = db.query(List).filter(List.archived == False, List.is_collation == True).order_by(List.name.asc()).all()
    return templates.TemplateResponse(request, "collations.html", {
        "request": request,
        "collation_lists": collation_lists,
    })


@app.get("/categories", response_class=HTMLResponse)
def view_categories(request: Request, db: Session = Depends(get_db)):
    """View and manage all list categories."""
    # Get all categories from the Category table
    all_cats = db.query(Category).order_by(Category.name).all()
    
    # Get list counts for each category
    list_counts = {}
    for row in db.query(List.category, func.count(List.id)).filter(List.category != None).group_by(List.category).all():
        list_counts[row[0]] = row[1]
    
    categories = [{"name": cat.name, "list_count": list_counts.get(cat.name, 0)} for cat in all_cats]
    
    return templates.TemplateResponse(request, "categories.html", {
        "request": request,
        "categories": categories,
    })


@app.post("/categories/create", response_class=HTMLResponse)
def create_category(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db)
):
    """Create a new category."""
    name = name.strip()
    if not name:
        return RedirectResponse(url="/categories", status_code=303)
    
    # Check if category already exists
    existing = db.query(Category).filter(Category.name == name).first()
    if existing:
        return RedirectResponse(url="/categories", status_code=303)
    
    # Create the category
    cat = Category(name=name)
    db.add(cat)
    create_journal_entry(db, "Category Created", f"Created category '{name}'", "category", None)
    db.commit()
    
    return RedirectResponse(url="/categories", status_code=303)


@app.post("/categories/rename", response_class=HTMLResponse)
def rename_category(
    request: Request,
    old_name: str = Form(...),
    new_name: str = Form(...),
    db: Session = Depends(get_db)
):
    """Rename a category across all lists that use it."""
    old_name = old_name.strip()
    new_name = new_name.strip()
    
    if not old_name or not new_name:
        return RedirectResponse(url="/categories", status_code=303)
    
    # Update the Category table
    cat = db.query(Category).filter(Category.name == old_name).first()
    if cat:
        cat.name = new_name
    
    # Update all lists with the old category name
    lists_updated = db.query(List).filter(List.category == old_name).all()
    for lst in lists_updated:
        lst.category = new_name
        lst.modified_at = utcnow()
    
    create_journal_entry(db, "Category Renamed", f"Renamed category '{old_name}' to '{new_name}' ({len(lists_updated)} lists)", "category", None)
    db.commit()
    
    return RedirectResponse(url="/categories", status_code=303)


@app.post("/categories/delete", response_class=HTMLResponse)
def delete_category(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db)
):
    """Delete a category by clearing it from all lists and removing from Category table."""
    name = name.strip()
    if not name:
        return RedirectResponse(url="/categories", status_code=303)
    
    # Delete from Category table
    cat = db.query(Category).filter(Category.name == name).first()
    if cat:
        db.delete(cat)
    
    # Clear category from all lists that use it
    lists_updated = db.query(List).filter(List.category == name).all()
    for lst in lists_updated:
        lst.category = None
        lst.modified_at = utcnow()
    
    create_journal_entry(db, "Category Deleted", f"Deleted category '{name}' (cleared from {len(lists_updated)} lists)", "category", None)
    db.commit()
    
    return RedirectResponse(url="/categories", status_code=303)


def find_search_matches(db, q):
    """Return unarchived lists, todos and calendar events matching every search term.

    A term matches an item through its own text, its hashtags or (for todos)
    the name of its list, so "shopping milk" finds "milk" in the Shopping list.
    """
    from search_index import all_terms, index_installed, text_match

    indexed = index_installed(db)

    def tagged(model, term):
        return model.hashtags.any(Hashtag.name.ilike(f"%{term}%"))

    lists = (
        db.query(List)
        .filter(
            List.archived == False,
            all_terms(q, lambda term: or_(text_match(List, term, indexed), tagged(List, term))),
        )
        .order_by(List.modified_at.desc())
        .all()
    )
    todos = (
        db.query(Todo)
        .filter(all_terms(q, lambda term: or_(
            text_match(Todo, term, indexed),
            Todo.list_id.in_(select(List.id).where(text_match(List, term, indexed))),
            tagged(Todo, term),
        )))
        .order_by(Todo.modified_at.desc())
        .all()
    )
    events = (
        db.query(CalendarEvent)
        .filter(all_terms(q, lambda term: or_(text_match(CalendarEvent, term, indexed), tagged(CalendarEvent, term))))
        .all()
    )
    return lists, todos, events


def find_search_results(db, q):
    """Return unarchived lists, todos and calendar events matching a search."""
    from search_index import search_terms

    if not search_terms(q):
        return {"lists": [], "todos": [], "events": []}
    lists, todos, events_raw = find_search_matches(db, q)

    today = _midnight(datetime.now())
    completion_rows = db.query(CalendarEventCompletion).all()
    completed_set = {(c.event_id, c.occurrence_at) for c in completion_rows}

    # Search behaves like "Show All": if specific query matches, show it.
    # But still useful to show the relevant date.
    events = []
    for ev in events_raw:
        next_occ = get_next_uncompleted_occurrence(ev, completed_set, today)
        ev.display_start_at = next_occ if next_occ else ev.start_at
        events.append(ev)
    events.sort(key=lambda x: x.display_start_at)
    return {"lists": lists, "todos": todos, "events": events}


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, db: Session = Depends(get_db)):
    """Search across lists and todos (including todo notes)."""
    q = (request.query_params.get("q") or "").strip()
    results = find_search_results(db, q)
    todos = results["todos"]

    from grouping_service import organize_todos_into_tree
    todo_tree = organize_todos_into_tree(db, todos) if todos else None

    return templates.TemplateResponse(request, "search.html", {
        "request": request,
        "q": q,
        "lists": results["lists"],
        "todo_tree": todo_tree,
        "events": results["events"],
    })


def _filtered_calendar_events(db, event_filter):
    query = db.query(CalendarEvent)
    if event_filter == "non_recurring":
        query = query.filter(or_(CalendarEvent.rrule == None, CalendarEvent.rrule == ""))
    elif event_filter == "recurring":
        query = query.filter(CalendarEvent.rrule != None, CalendarEvent.rrule != "")
    elif event_filter.startswith("group:"):
        try:
            group_id = int(event_filter.removeprefix("group:"))
        except ValueError:
            group_id = -1
        query = query.filter(CalendarEvent.event_group_id == group_id)
    return query


def calendar_context(db, offset=0, month=None, week=None, duplicated=None):
    """Build the calendar layout chosen in user settings (list, month or week)."""
    duplicated = duplicated or {}
    import calendar as cal_module
    from collections import defaultdict

    settings = get_user_settings(db)
    weekly_view = settings.calendar_weekly_view
    traditional_view = settings.calendar_traditional_view and not weekly_view
    calendar_sort_order = settings.calendar_sort_order
    if calendar_sort_order not in CALENDAR_SORT_OPTIONS:
        calendar_sort_order = DEFAULT_CALENDAR_SORT
    calendar_sort_context = {
        "calendar_sort_order": calendar_sort_order,
        "calendar_sort_options": CALENDAR_SORT_OPTIONS,
        "calendar_word_wrap": bool(settings.calendar_word_wrap),
        "calendar_show_counts": bool(settings.calendar_show_counts),
        "weekly_view": bool(weekly_view),
    }
    event_groups = db.query(EventGroup).order_by(EventGroup.name).all()
    event_group_ids = {group.id for group in event_groups}
    calendar_event_filter = settings.calendar_event_filter or "all"
    group_filter_id = None
    if calendar_event_filter.startswith("group:"):
        try:
            group_filter_id = int(calendar_event_filter.removeprefix("group:"))
        except ValueError:
            pass
    if (
        calendar_event_filter not in {"all", "non_recurring", "recurring"}
        and group_filter_id not in event_group_ids
    ):
        calendar_event_filter = "all"
        settings.calendar_event_filter = calendar_event_filter
        db.commit()

    # Use local midnight for past/future splitting to match user's timezone perspective
    now = _midnight(datetime.now())
    today_local = _midnight(datetime.now())
    all_hashtags = db.query(Hashtag).order_by(Hashtag.name).all()

    events_query = _filtered_calendar_events(db, calendar_event_filter)
    events = events_query.order_by(CalendarEvent.start_at.asc()).all()
    invalid_recurrences = []
    for event in events:
        try:
            validate_calendar_recurrence(event.rrule, event.start_at)
        except ValueError as error:
            invalid_recurrences.append({'event': event, 'error': str(error)})
    completion_rows = db.query(CalendarEventCompletion).all()
    completed_set = {(c.event_id, c.occurrence_at) for c in completion_rows}

    if weekly_view:
        try:
            anchor = datetime.strptime(week, "%Y-%m-%d") if week else now + timedelta(weeks=offset)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid calendar week")
        week_start = anchor - timedelta(days=anchor.weekday())
        week_days = calendar_week(events, week_start, completed_set, calendar_sort_order)
        return {
            "view": "week", "traditional_view": False, **calendar_sort_context,
            "invalid_recurrences": invalid_recurrences, "week_days": week_days,
            "week_start": week_start, "week_end": week_start + timedelta(days=6),
            "current_week_str": week_start.strftime("%Y-%m-%d"),
            "prev_week_str": (week_start - timedelta(weeks=1)).strftime("%Y-%m-%d"),
            "next_week_str": (week_start + timedelta(weeks=1)).strftime("%Y-%m-%d"),
            "slots": [f"{hour:02d}:00" for hour in range(24)],
            "today_local": today_local, "all_hashtags": all_hashtags,
            "event_groups": event_groups, "calendar_event_filter": calendar_event_filter,
            "offset": offset,
            "duplicated_id": duplicated.get("duplicated_id"),
            "duplicated_title": duplicated.get("duplicated_title"),
            "duplicated_date": duplicated.get("duplicated_date"),
        }

    if traditional_view:
        # ---- Traditional monthly grid view ----
        # Determine which month to display
        if month:
            try:
                year, mon = map(int, month.split("-"))
            except Exception:
                year, mon = now.year, now.month
        else:
            # Apply offset to current month
            year, mon = now.year, now.month
            mon += offset
            while mon > 12:
                mon -= 12
                year += 1
            while mon < 1:
                mon += 12
                year -= 1

        # Compute prev/next month strings for navigation
        prev_year, prev_mon = year, mon - 1
        if prev_mon < 1:
            prev_mon = 12
            prev_year -= 1
        next_year, next_mon = year, mon + 1
        if next_mon > 12:
            next_mon = 1
            next_year += 1

        prev_month_str = f"{prev_year}-{prev_mon:02d}"
        next_month_str = f"{next_year}-{next_mon:02d}"
        current_month_str = f"{year}-{mon:02d}"

        # Number of days in the month (handles leap years via calendar module)
        days_in_month = cal_module.monthrange(year, mon)[1]

        # Compute the month start/end as datetime for occurrence lookup
        month_start = datetime(year, mon, 1)
        month_end = datetime(year, mon, days_in_month, 23, 59, 59)
        day_settings = {row.day.day: row for row in db.query(CalendarDay).filter(
            CalendarDay.day >= month_start, CalendarDay.day <= month_end,
        ).all()}

        # Gather all occurrences for this month
        occurrences_by_day = defaultdict(list)
        for ev in events:
            ev_occs = _event_occurrences(ev, month_start, month_end)
            for occ_at in ev_occs:
                day_key = occ_at.day
                is_completed = (ev.id, occ_at) in completed_set
                is_late = (occ_at.date() < today_local.date()) and not is_completed
                is_important = is_late and bool(ev.important_if_late)
                occurrences_by_day[day_key].append({
                    "event": ev,
                    "occurrence_at": occ_at,
                    "completed": is_completed,
                    "is_late": is_late,
                    "is_important": is_important,
                })

        # Build calendar grid (list of weeks, each week is list of 7 day-dicts)
        # Monday = 0 start (ISO standard). Use calendar module for proper grid.
        month_cal = cal_module.monthcalendar(year, mon)
        weeks = []
        for week in month_cal:
            week_data = []
            for day_num in week:
                if day_num == 0:
                    week_data.append({"day": 0, "events": [], "is_today": False})
                else:
                    is_today = (year == today_local.year and mon == today_local.month and day_num == today_local.day)
                    day_events = occurrences_by_day.get(day_num, [])
                    sort_calendar_occurrences(day_events, calendar_sort_order)
                    state = day_settings.get(day_num)
                    week_data.append({
                        "day": day_num, "date": f"{year}-{mon:02d}-{day_num:02d}",
                        "events": day_events, "is_today": is_today,
                        "note": (state.note or "") if state else "",
                        "hide_completed": bool(state and state.hide_completed),
                        "remaining": sum(not row['completed'] for row in day_events),
                    })
            weeks.append(week_data)

        month_name = cal_module.month_name[mon]

        return {
            "view": "month",
            "traditional_view": True,
            **calendar_sort_context,
            "invalid_recurrences": invalid_recurrences,
            "weeks": weeks,
            "month_name": month_name,
            "year": year,
            "mon": mon,
            "prev_month_str": prev_month_str,
            "next_month_str": next_month_str,
            "current_month_str": current_month_str,
            "now": now,
            "today_local": today_local,
            "all_hashtags": all_hashtags,
            "event_groups": event_groups,
            "calendar_event_filter": calendar_event_filter,
            "offset": offset,
            "duplicated_id": duplicated.get("duplicated_id"),
            "duplicated_title": duplicated.get("duplicated_title"),
            "duplicated_date": duplicated.get("duplicated_date"),
        }
    else:
        # ---- Original list view ----
        # Navigation: Shift window by 30 days per offset
        shift_days = offset * 30
        start = now - timedelta(days=7) + timedelta(days=shift_days)
        end = now + timedelta(days=CALENDAR_LOOKAHEAD_DAYS + shift_days + 1) - timedelta(microseconds=1)

        occurrences = []
        for ev in events:
            ev_occs = _event_occurrences(ev, start, end)

            # Heuristic: Filter future occurrences if enabled
            if RRULE_COLLAPSE_FUTURE and ev.rrule:
                past_occs = [o for o in ev_occs if o < now]
                future_occs = [o for o in ev_occs if o >= now]

                completed_future = [o for o in future_occs if (ev.id, o) in completed_set]
                uncompleted_future = [o for o in future_occs if (ev.id, o) not in completed_set]
                uncompleted_future = uncompleted_future[:RRULE_HIDE_FUTURE_OCCURRENCES_LIMIT]

                ev_occs = past_occs + completed_future + uncompleted_future

            for occ_at in ev_occs:
                is_completed = (ev.id, occ_at) in completed_set
                is_late = (occ_at.date() < today_local.date()) and not is_completed
                is_important = is_late and bool(ev.important_if_late)
                occurrences.append({
                    "event": ev,
                    "occurrence_at": occ_at,
                    "completed": is_completed,
                    "is_late": is_late,
                    "is_important": is_important,
                })

        sort_calendar_occurrences(occurrences, calendar_sort_order)

        return {
            "view": "list",
            "traditional_view": False,
            **calendar_sort_context,
            "invalid_recurrences": invalid_recurrences,
            "occurrences": occurrences,
            "now": now,
            "today_local": today_local,
            "range_start": start,
            "range_end": end,
            "all_hashtags": all_hashtags,
            "event_groups": event_groups,
            "calendar_event_filter": calendar_event_filter,
            "offset": offset,
            "prev_offset": offset - 1,
            "next_offset": offset + 1,
            "duplicated_id": duplicated.get("duplicated_id"),
            "duplicated_title": duplicated.get("duplicated_title"),
            "duplicated_date": duplicated.get("duplicated_date"),
        }



@app.get("/classic/calendar", response_class=HTMLResponse)
def calendar_page(
    request: Request,
    offset: int = 0,
    month: str = None,
    week: str = None,
    duplicated_id: Optional[int] = None,
    duplicated_title: Optional[str] = None,
    duplicated_date: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Chronological view of calendar events and their occurrences."""
    context = calendar_context(db, offset, month, week, {
        "duplicated_id": duplicated_id, "duplicated_title": duplicated_title, "duplicated_date": duplicated_date,
    })
    return templates.TemplateResponse(request, "calendar.html", {"request": request, **context})


@app.post("/calendar/toggle-view")
def toggle_calendar_view(db: Session = Depends(get_db)):
    """Toggle between traditional monthly grid and list calendar views."""
    settings = get_user_settings(db)
    settings.calendar_traditional_view = not settings.calendar_traditional_view
    settings.calendar_weekly_view = False
    db.commit()
    return RedirectResponse(url="/classic/calendar", status_code=303)


@app.post("/calendar/set-view")
def set_calendar_view(view: str = Form(...), db: Session = Depends(get_db)):
    if view not in {"list", "month", "week"}:
        raise HTTPException(status_code=400, detail="Invalid calendar view")
    settings = get_user_settings(db)
    settings.calendar_weekly_view = view == "week"
    settings.calendar_traditional_view = view == "month"
    db.commit()
    return RedirectResponse(url="/classic/calendar", status_code=303)


@app.post("/calendar/set-filter")
def set_calendar_filter(
    calendar_event_filter: str = Form("all"),
    week: str = Form(""),
    db: Session = Depends(get_db),
):
    """Persist the event filter shared by calendar layouts."""
    valid_filters = {"all", "non_recurring", "recurring"}
    if calendar_event_filter.startswith("group:"):
        try:
            group_id = int(calendar_event_filter.removeprefix("group:"))
        except ValueError:
            group_id = None
        if group_id and db.get(EventGroup, group_id):
            valid_filters.add(calendar_event_filter)

    if calendar_event_filter not in valid_filters:
        raise HTTPException(status_code=400, detail="Invalid calendar event filter")

    settings = get_user_settings(db)
    settings.calendar_event_filter = calendar_event_filter
    db.commit()
    return RedirectResponse(url=_calendar_event_return_url("", week), status_code=303)


@app.post("/calendar/set-sort")
def set_calendar_sort(
    calendar_sort_order: str = Form(...),
    month: str = Form(""),
    week: str = Form(""),
    offset: int = Form(0),
    db: Session = Depends(get_db),
):
    """Persist the same-day sort shared by calendar layouts."""
    if calendar_sort_order not in CALENDAR_SORT_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid calendar sort order")
    settings = get_user_settings(db)
    settings.calendar_sort_order = calendar_sort_order
    db.commit()
    from urllib.parse import urlencode
    query = urlencode({"week": week}) if week else urlencode({"month": month}) if month else urlencode({"offset": offset})
    return RedirectResponse(url=f"/classic/calendar?{query}", status_code=303)


@app.post("/calendar/toggle-word-wrap")
def toggle_calendar_word_wrap(
    month: str = Form(""),
    offset: int = Form(0),
    db: Session = Depends(get_db),
):
    """Remember whether monthly event titles wrap, preserving the displayed month."""
    settings = get_user_settings(db)
    settings.calendar_word_wrap = not settings.calendar_word_wrap
    db.commit()
    query = urlencode({"month": month}) if month else urlencode({"offset": offset})
    return RedirectResponse(url=f"/classic/calendar?{query}", status_code=303)


@app.post("/calendar/toggle-counts")
def toggle_calendar_counts(
    month: str = Form(""),
    offset: int = Form(0),
    db: Session = Depends(get_db),
):
    """Remember whether the monthly grid shows daily event counts."""
    settings = get_user_settings(db)
    settings.calendar_show_counts = not settings.calendar_show_counts
    db.commit()
    query = urlencode({"month": month}) if month else urlencode({"offset": offset})
    return RedirectResponse(url=f"/classic/calendar?{query}", status_code=303)


def _calendar_day_date(day):
    parsed = _parse_date_yyyy_mm_dd(day)
    if parsed is None:
        raise HTTPException(status_code=400, detail="Invalid calendar date")
    return parsed


def _calendar_day_state(db, day):
    state = db.query(CalendarDay).filter(CalendarDay.day == day).first()
    if state is None:
        state = CalendarDay(day=day, hide_completed=False)
        db.add(state)
    return state


def _calendar_day_redirect(day, *, open_day=False):
    fragment = f"#day-{day:%Y-%m-%d}" if open_day else ""
    return RedirectResponse(url=f"/classic/calendar?month={day:%Y-%m}{fragment}", status_code=303)


@app.post("/calendar/day/{day}/note")
def save_calendar_day_note(day: str, note: str = Form(""), db: Session = Depends(get_db)):
    date = _calendar_day_date(day)
    state = _calendar_day_state(db, date)
    state.note = note.strip() or None
    db.flush()
    create_journal_entry(db, "Updated Calendar Day Note", f"Updated note for {day}", "calendar_day", state.id)
    db.commit()
    return _calendar_day_redirect(date, open_day=True)


@app.post("/calendar/day/{day}/toggle-completed")
def toggle_calendar_day_completed(day: str, db: Session = Depends(get_db)):
    date = _calendar_day_date(day)
    state = _calendar_day_state(db, date)
    state.hide_completed = not state.hide_completed
    db.commit()
    return _calendar_day_redirect(date)


@app.post("/calendar/day/{day}/complete")
def complete_calendar_day(day: str, db: Session = Depends(get_db)):
    date = _calendar_day_date(day)
    end = date + timedelta(days=1) - timedelta(microseconds=1)
    settings = get_user_settings(db)
    completed = {(row.event_id, row.occurrence_at) for row in db.query(CalendarEventCompletion).filter(
        CalendarEventCompletion.occurrence_at >= date, CalendarEventCompletion.occurrence_at <= end,
    ).all()}
    added = 0
    for event in _filtered_calendar_events(db, settings.calendar_event_filter or "all").all():
        for occurrence in _event_occurrences(event, date, end):
            key = (event.id, occurrence)
            if key not in completed:
                db.add(CalendarEventCompletion(event_id=event.id, occurrence_at=occurrence))
                event.modified_at = utcnow()
                completed.add(key)
                added += 1
    if added:
        create_journal_entry(db, "Completed Calendar Day", f"Completed {added} occurrences on {day}")
    db.commit()
    return _calendar_day_redirect(date)


@app.get("/calendar/groups", response_class=HTMLResponse)
def view_event_groups(request: Request, db: Session = Depends(get_db)):
    """Manage user-defined calendar event groups."""
    groups = db.query(EventGroup).order_by(EventGroup.name).all()
    group_counts = dict(
        db.query(CalendarEvent.event_group_id, func.count(CalendarEvent.id))
        .filter(CalendarEvent.event_group_id != None)
        .group_by(CalendarEvent.event_group_id)
        .all()
    )
    return templates.TemplateResponse(request, "event_groups.html", {
        "request": request,
        "groups": groups,
        "group_counts": group_counts,
    })


@app.post("/calendar/groups/create")
def create_event_group(name: str = Form(...), db: Session = Depends(get_db)):
    name = name.strip()
    if name and not db.query(EventGroup).filter(EventGroup.name == name).first():
        db.add(EventGroup(name=name))
        db.commit()
    return RedirectResponse(url="/calendar/groups", status_code=303)


@app.post("/calendar/groups/{group_id}/delete")
def delete_event_group(group_id: int, db: Session = Depends(get_db)):
    group = db.get(EventGroup, group_id)
    if group:
        db.query(CalendarEvent).filter(CalendarEvent.event_group_id == group.id).update({
            CalendarEvent.event_group_id: None,
        })
        settings = get_user_settings(db)
        if settings.calendar_event_filter == f"group:{group.id}":
            settings.calendar_event_filter = "all"
        db.delete(group)
        db.commit()
    return RedirectResponse(url="/calendar/groups", status_code=303)

def _calendar_month_url(view_month):
    try:
        month = datetime.strptime(view_month, "%Y-%m")
        if month.strftime("%Y-%m") == view_month:
            return f"/classic/calendar?month={view_month}"
    except ValueError:
        pass
    return "/classic/calendar"


def _calendar_event_return_url(view_month, view_week):
    if view_week:
        try:
            parsed = datetime.strptime(view_week, "%Y-%m-%d")
            return f"/classic/calendar?week={parsed:%Y-%m-%d}"
        except ValueError:
            pass
    return _calendar_month_url(view_month)


def save_calendar_event(db, event=None, *, title, start_at, start_time=None, duration_minutes="", rrule="",
                        note="", hashtags="", event_group_id="", important_if_late=None, color=None):
    """Validate, then create or update a calendar event. Raises ValueError with a user-facing message."""
    dt, all_day = parse_calendar_start(start_at, start_time)
    duration = parse_calendar_duration(duration_minutes, all_day)
    rule = validate_calendar_recurrence(rrule, dt)
    try:
        group_id = int(event_group_id) if event_group_id else None
    except ValueError:
        raise ValueError("Invalid event group") from None
    if group_id is not None and not db.get(EventGroup, group_id):
        raise ValueError("Event group not found")

    created = event is None
    if created:
        event = CalendarEvent()
        db.add(event)
    event.title = title
    event.start_at = dt
    event.all_day = all_day
    event.duration_minutes = duration
    event.rrule = rule or None
    event.note = (note.strip() or None)
    event.event_group_id = group_id
    event.important_if_late = bool(important_if_late)
    if color is not None:
        # Normalize color – treat empty, black, and white as "no color"
        c = (color or "").strip().lower()
        event.color = c if c and c not in ("#000000", "#ffffff") else None
    if not created:
        event.modified_at = utcnow()
        event.hashtags.clear()
    db.flush()
    for tag_name in parse_hashtags(hashtags):
        event.hashtags.append(get_or_create_hashtag(db, tag_name))

    if created:
        create_journal_entry(db, "Created Calendar Event", f"Created event '{title}'", "calendar_event", event.id)
    else:
        create_journal_entry(db, "Updated Calendar Event", f"Updated event '{title}'", "calendar_event", event.id)
    db.commit()
    return event


@app.post("/calendar/create")
def create_calendar_event(
    title: str = Form(...),
    start_at: str = Form(...),
    start_time: Optional[str] = Form(None),
    duration_minutes: str = Form(""),
    rrule: str = Form(""),
    note: str = Form(""),
    hashtags: str = Form(""),
    event_group_id: str = Form(""),
    important_if_late: Optional[str] = Form(None),
    view_month: str = Form(""),
    view_week: str = Form(""),
    db: Session = Depends(get_db),
):
    """Create a calendar event. RRULE is optional."""
    try:
        save_calendar_event(db, title=title, start_at=start_at, start_time=start_time, duration_minutes=duration_minutes,
                            rrule=rrule, note=note, hashtags=hashtags, event_group_id=event_group_id,
                            important_if_late=important_if_late)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return RedirectResponse(url=_calendar_event_return_url(view_month, view_week), status_code=303)


@app.get("/calendar/event/{event_id}/edit", response_class=HTMLResponse)
def edit_calendar_event_page(
    request: Request,
    event_id: int,
    view_month: str = "",
    view_week: str = "",
    occurrence_at: Optional[str] = None,
    duplicated_id: Optional[int] = None,
    duplicated_title: Optional[str] = None,
    duplicated_date: Optional[str] = None,
    db: Session = Depends(get_db),
):
    ev = db.query(CalendarEvent).filter(CalendarEvent.id == event_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    
    all_hashtags = db.query(Hashtag).order_by(Hashtag.name).all()
    event_groups = db.query(EventGroup).order_by(EventGroup.name).all()
    
    return templates.TemplateResponse(request, "edit_calendar_event.html", {
        "request": request,
        "event": ev,
        "view_month": view_month,
        "view_week": view_week,
        "occurrence_at": occurrence_at,
        "all_hashtags": all_hashtags,
        "event_groups": event_groups,
        "duplicated_id": duplicated_id,
        "duplicated_title": duplicated_title,
        "duplicated_date": duplicated_date,
    })


@app.get("/api/data/calendar/event/{event_id}/edit")
def api_edit_calendar_event_data(event_id: int, db: Session = Depends(get_db)):
    ev = db.query(CalendarEvent).filter(CalendarEvent.id == event_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    return JSONResponse({
        "event": serialize_calendar_event(ev),
    })

@app.post("/calendar/event/{event_id}/update")
def update_calendar_event(
    event_id: int,
    title: str = Form(...),
    start_at: str = Form(...),
    start_time: Optional[str] = Form(None),
    duration_minutes: str = Form(""),
    rrule: str = Form(""),
    note: str = Form(""),
    hashtags: str = Form(""),
    color: str = Form(""),
    event_group_id: str = Form(""),
    important_if_late: Optional[str] = Form(None),
    view_month: str = Form(""),
    view_week: str = Form(""),
    db: Session = Depends(get_db),
):
    ev = db.query(CalendarEvent).filter(CalendarEvent.id == event_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    try:
        save_calendar_event(db, ev, title=title, start_at=start_at, start_time=start_time, duration_minutes=duration_minutes,
                            rrule=rrule, note=note, hashtags=hashtags, event_group_id=event_group_id,
                            important_if_late=important_if_late, color=color)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return RedirectResponse(url=_calendar_event_return_url(view_month, view_week), status_code=303)


@app.post("/calendar/event/{event_id}/delete")
def delete_calendar_event(
    event_id: int,
    view_month: str = Form(""),
    view_week: str = Form(""),
    db: Session = Depends(get_db),
):
    ev = db.get(CalendarEvent, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    create_journal_entry(db, "Deleted Calendar Event", f"Deleted event '{ev.title}'", "calendar_event", ev.id)
    db.delete(ev)
    db.commit()
    return RedirectResponse(url=_calendar_event_return_url(view_month, view_week), status_code=303)


def duplicate_event_to(db, ev, new_date, *, source_occurrence=None):
    """Copy event details, optionally including notes applicable to one occurrence."""
    source_start = ev.start_at
    if source_occurrence is not None:
        start = datetime.combine(source_occurrence, datetime.min.time())
        occurrences = calendar_occurrences(ev, start, start + timedelta(days=1) - timedelta(microseconds=1), 1, strict=True)
        if not occurrences:
            raise ValueError("This date is no longer an occurrence of the event. Refresh the calendar.")
        source_start = occurrences[0]
    if ev.all_day:
        new_start_at = datetime.combine(new_date, datetime.min.time())
    else:
        new_start_at = datetime.combine(new_date, source_start.time())

    new_ev = CalendarEvent(
        title=ev.title,
        note=ev.note,
        start_at=new_start_at,
        all_day=ev.all_day,
        duration_minutes=ev.duration_minutes,
        rrule=None,  # One-time standalone copy
        color=ev.color,
        event_group_id=ev.event_group_id,
        important_if_late=ev.important_if_late,
    )
    db.add(new_ev)
    db.flush()

    new_ev.hashtags = list(ev.hashtags)
    if source_occurrence is not None:
        for note in sorted(ev.notes, key=lambda note: (note.created_at, note.id)):
            if note.occurrence_at is None or note.occurrence_at.date() == source_occurrence:
                copied_note = Note(name=note.name, text=note.text, occurrence_at=None,
                                   metadata_json=deepcopy(note.metadata_json or {}))
                copied_note.links = [Link(url=link.url, title=link.title, metadata_json=deepcopy(link.metadata_json or {}))
                                     for link in note.links]
                new_ev.notes.append(copied_note)
    description = f"Duplicated event '{ev.title}' to {new_date.isoformat()}"
    if source_occurrence is not None:
        description += f" from occurrence {source_occurrence.isoformat()}, including applicable notes"
    create_journal_entry(
        db,
        "Duplicated Calendar Event",
        description,
        "calendar_event",
        new_ev.id,
    )
    db.commit()
    return new_ev


@app.post("/calendar/event/{event_id}/duplicate")
def duplicate_calendar_event(
    event_id: int,
    target_date: str = Form(...),
    return_to: str = Form("edit"),
    view_month: str = Form(""),
    view_week: str = Form(""),
    offset: int = Form(0),
    db: Session = Depends(get_db),
):
    ev = db.get(CalendarEvent, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")

    try:
        new_date = date_type.fromisoformat(target_date.strip())
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid target date format")
    new_ev = duplicate_event_to(db, ev, new_date)

    if return_to == "calendar":
        params = urlencode({
            "duplicated_id": new_ev.id,
            "duplicated_title": new_ev.title,
            "duplicated_date": new_date.isoformat(),
            "offset": offset,
        })
        return RedirectResponse(url=f"/classic/calendar?{params}", status_code=303)
    else:
        params = urlencode({
            "duplicated_id": new_ev.id,
            "duplicated_title": new_ev.title,
            "duplicated_date": new_date.isoformat(),
            "view_month": view_month,
            "view_week": view_week,
        })
        return RedirectResponse(url=f"/calendar/event/{event_id}/edit?{params}", status_code=303)


@app.post("/api/calendar/event/{event_id}/occurrence/toggle")
async def toggle_calendar_occurrence(event_id: int, request: Request, db: Session = Depends(get_db)):
    """Toggle completion of a specific occurrence (AJAX)."""
    ev = db.query(CalendarEvent).filter(CalendarEvent.id == event_id).first()
    if not ev:
        return JSONResponse({"error": "Event not found"}, status_code=404)

    data = await request.json()
    occ_str = (data.get("occurrence_at") or "").strip()
    if not occ_str:
        return JSONResponse({"error": "Missing occurrence_at"}, status_code=400)

    try:
        # Date-only callers resolve to midnight; preserve times for rules
        # that generate multiple occurrences on the same day.
        occ_at = datetime.fromisoformat(occ_str).replace(tzinfo=None)
    except Exception:
        return JSONResponse({"error": "Invalid occurrence_at"}, status_code=400)

    existing = (
        db.query(CalendarEventCompletion)
        .filter(CalendarEventCompletion.event_id == event_id, CalendarEventCompletion.occurrence_at == occ_at)
        .first()
    )

    if existing:
        db.delete(existing)
        completed = False
    else:
        db.add(CalendarEventCompletion(event_id=event_id, occurrence_at=occ_at))
        completed = True

    ev.modified_at = utcnow()
    create_journal_entry(db, "Toggled Calendar Occurrence", f"Toggled occurrence for event '{ev.title}' on {occ_str} to {completed}", "calendar_event", ev.id)
    db.commit()
    return JSONResponse({"completed": completed})


# ---------------------------------------------------------------------------
# Bills
# ---------------------------------------------------------------------------

def _bills_dashboard_data(db: Session, show_inactive: int = 0):
    """Build the shared data shown on the bills dashboard."""
    now = _midnight(datetime.now())

    active_bills = db.query(Bill).filter(Bill.is_active == True).order_by(Bill.next_due_date.asc(), Bill.name.asc()).all()
    inactive_bills = db.query(Bill).filter(Bill.is_active == False).order_by(Bill.name.asc()).all() if show_inactive else []
    inactive_count = db.query(Bill).filter(Bill.is_active == False).count()

    overdue = []
    due_today = []
    upcoming = []
    covered_early = []
    future_estimates = []
    unscheduled = []

    for bill in active_bills:
        if not bill.next_due_date:
            unscheduled.append(bill)
            continue
        # Check if the current cycle was paid early
        if bill.is_covered_early(now):
            covered_early.append(bill)
            continue
        days_away = bill.days_until_due(now)
        if days_away is None:
            unscheduled.append(bill)
            continue
        if days_away < 0:
            overdue.append(bill)
        elif days_away == 0:
            due_today.append(bill)
        elif days_away <= 14 or bill.is_due_date_confirmed:
            upcoming.append(bill)
        else:
            future_estimates.append(bill)

    # Recently paid in the last 30 days (rolling window prevents month-boundary vanishing)
    thirty_days_ago = now - timedelta(days=30)
    recent_payments = (
        db.query(BillPayment)
        .options(joinedload(BillPayment.bill))
        .filter(BillPayment.paid_date >= thirty_days_ago,
                BillPayment.paid_date < _midnight(now) + timedelta(days=1))
        .order_by(BillPayment.paid_date.desc(), BillPayment.id.desc())
        .all()
    )

    # Recurring charges for budgeting, separate from unpaid account balances.
    currency_summaries = spending_summaries([], active_bills, [], [], now.date())
    monthly_total = currency_summaries[0]['bills_monthly'] if len(currency_summaries) == 1 else None

    return {
        "overdue": overdue,
        "due_today": due_today,
        "upcoming": upcoming,
        "covered_early": covered_early,
        "future_estimates": future_estimates,
        "unscheduled": unscheduled,
        "inactive_bills": inactive_bills,
        "inactive_count": inactive_count,
        "recent_payments": recent_payments,
        "monthly_total": monthly_total,
        "currency_summaries": currency_summaries,
        "now": now,
        "show_inactive": show_inactive,
    }


@app.get("/classic/bills", response_class=HTMLResponse)
def bills_dashboard(request: Request, show_inactive: int = 0, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "bills.html", {
        "request": request, **_bills_dashboard_data(db, show_inactive),
    })


@app.get("/api/bills")
def bills_api(show_inactive: int = 0, db: Session = Depends(get_db)):
    """Return the same bill groups and estimates as the bills dashboard."""
    data = _bills_dashboard_data(db, show_inactive)
    now = data["now"]

    def bill_item(bill):
        return {**serialize_bill(bill),
                "next_due_date": bill.next_due_date_iso or None,
                "days_until_due": bill.days_until_due(now),
                "effective_amount": bill.effective_amount,
                "is_estimated": bill.is_estimated,
                "currency_symbol": bill.currency_symbol,
                "credit_cents": bill.credit_cents if bill.ledger_enabled else None,
                "outstanding_cents": bill.outstanding_cents if bill.ledger_enabled else None}

    def payment_item(payment):
        return {"id": payment.id, "bill_id": payment.bill_id,
                "bill_name": payment.bill.name if payment.bill else None,
                "currency": (payment.bill.currency or "AUD") if payment.bill else None,
                "currency_symbol": payment.currency_symbol,
                "amount_paid": payment.amount_paid, "paid_date": payment.paid_date_iso or None,
                "due_date": payment.due_date_iso or None,
                "days_difference": payment.days_difference if payment.effective_due_date else None,
                "timing_label": ("✓ (Override)" if payment.effective_due_date and payment.days_difference < 0 and payment.is_late_overridden else payment.timing_label),
                "is_late_overridden": payment.is_late_overridden,
                "note": payment.display_note}

    groups = ("overdue", "due_today", "upcoming", "covered_early",
              "future_estimates", "unscheduled", "inactive_bills")
    return JSONResponse({
        "as_of": now.date().isoformat(),
        **{group: [bill_item(bill) for bill in data[group]] for group in groups},
        "recent_payments": [payment_item(p) for p in data["recent_payments"]],
        "inactive_count": data["inactive_count"],
        "monthly_estimates": [{"currency": s["currency"], "currency_symbol": s["currency_symbol"],
                               "amount": s["bills_monthly"]} for s in data["currency_summaries"]],
    }, headers={"Cache-Control": "no-store"})


@app.post("/bills/create")
def create_bill(
    name: str = Form(...),
    next_due_date: str = Form(""),
    frequency: str = Form("monthly"),
    expected_amount: str = Form(""),
    currency: str = Form("AUD"),
    amount_strategy: str = Form("fixed"),
    payee: str = Form(""),
    category: str = Form(""),
    note: str = Form(""),
    is_auto_pay: bool = Form(False),
    frequency_days: str = Form(""),
    anchor_day: str = Form(""),
    is_due_date_confirmed: bool = Form(False),
    color: str = Form(""),
    ledger_enabled: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Create a new bill."""
    if not name or not name.strip():
        return RedirectResponse(url="/classic/bills", status_code=303)

    try:
        amt = estimate_amount(expected_amount)
    except (ValueError, TypeError) as error:
        db.rollback()
        raise HTTPException(400, str(error))

    valid_frequencies = {"weekly", "fortnightly", "monthly", "bimonthly", "quarterly", "yearly", "custom"}
    if frequency not in valid_frequencies:
        frequency = "monthly"

    due = None
    if next_due_date and next_due_date.strip():
        try:
            due = _parse_date_or_datetime(next_due_date.strip())
        except (ValueError, TypeError):
            due = _midnight(datetime.now())

    freq_days = None
    if frequency == "custom":
        if frequency_days and frequency_days.strip():
            try:
                val = int(frequency_days.strip())
                if val > 0:
                    freq_days = val
            except ValueError:
                pass
        if not freq_days:
            frequency = "monthly"

    anch_day = None
    if anchor_day and anchor_day.strip():
        try:
            val = int(anchor_day.strip())
            if 1 <= val <= 31:
                anch_day = val
        except ValueError:
            pass
    if not anch_day and due:
        anch_day = due.day

    try:
        validate_recurrence_settings(frequency, freq_days, anch_day, due)
    except ValueError as error:
        db.rollback()
        raise HTTPException(400, str(error))

    strat = (amount_strategy or "fixed").strip().lower()
    if strat not in ("fixed", "rolling_avg", "last_payment"):
        strat = "fixed"

    bill = Bill(
        name=name.strip(),
        payee=payee.strip() or None,
        note=note.strip() or None,
        expected_amount=amt,
        currency=(currency.strip().upper() if currency and currency.strip() else "AUD"),
        amount_strategy=strat,
        next_due_date=due,
        anchor_day=anch_day,
        is_due_date_confirmed=is_due_date_confirmed if due else False,
        frequency=frequency,
        frequency_days=freq_days,
        is_auto_pay=is_auto_pay,
        category=category.strip() or None,
        color=(color.strip() if color and color.strip().lower() != "#000000" else None),
    )
    db.add(bill)
    if ledger_enabled:
        enable_ledger(db, bill)
    if is_due_date_confirmed and amt is not None:
        record_invoice(db, bill, amt, due)
    db.flush()
    create_journal_entry(db, "Created Bill", f"Created bill '{bill.name}'", "bill", bill.id)
    db.commit()
    return RedirectResponse(url="/classic/bills", status_code=303)


@app.get("/bills/{bill_id}/edit", response_class=HTMLResponse)
def edit_bill_page(request: Request, bill_id: int, db: Session = Depends(get_db)):
    """Edit bill form."""
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        return RedirectResponse(url="/classic/bills", status_code=303)
    return templates.TemplateResponse(request, "edit_bill.html", {
        "request": request,
        "bill": bill,
    })


@app.post("/bills/{bill_id}/update")
def update_bill(
    bill_id: int,
    name: str = Form(...),
    next_due_date: str = Depends(form_text("next_due_date", required=True)),
    frequency: str = Form("monthly"),
    expected_amount: str = Form(""),
    currency: Optional[str] = Form(None),
    amount_strategy: Optional[str] = Form(None),
    payee: str = Form(""),
    category: str = Form(""),
    note: str = Form(""),
    is_auto_pay: bool = Form(False),
    frequency_days: str = Form(""),
    anchor_day: str = Form(""),
    is_due_date_confirmed: bool = Form(False),
    color: str = Form(""),
    ledger_enabled: Optional[bool] = Form(None),
    db: Session = Depends(get_db),
):
    """Update a bill."""
    lock_account(db)
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        return RedirectResponse(url="/classic/bills", status_code=303)
    try:
        amt = estimate_amount(expected_amount)
    except (ValueError, TypeError) as error:
        db.rollback()
        raise HTTPException(400, str(error))
    if name and name.strip():
        bill.name = name.strip()
    bill.payee = payee.strip() or None
    bill.note = note.strip() or None
    if currency and currency.strip():
        bill.currency = currency.strip().upper()
    valid_frequencies = {"weekly", "fortnightly", "monthly", "bimonthly", "quarterly", "yearly", "custom"}
    if frequency not in valid_frequencies:
        frequency = "monthly"

    new_due = None
    if next_due_date and next_due_date.strip():
        try:
            new_due = _parse_date_or_datetime(next_due_date.strip())
            bill.next_due_date = new_due
            bill.is_due_date_confirmed = is_due_date_confirmed
        except (ValueError, TypeError):
            new_due = bill.next_due_date
    else:
        bill.next_due_date = None
        bill.is_due_date_confirmed = False

    bill.frequency = frequency
    bill.category = category.strip() or None
    color_val = color.strip() if color else ""
    bill.color = color_val if color_val and color_val.lower() != "#000000" else None
    bill.is_auto_pay = is_auto_pay

    anch_day = None
    if anchor_day and anchor_day.strip():
        try:
            val = int(anchor_day.strip())
            if 1 <= val <= 31:
                anch_day = val
        except ValueError:
            pass
    if not anch_day and new_due:
        anch_day = new_due.day
    bill.anchor_day = anch_day

    bill.expected_amount = amt

    freq_days = None
    if frequency == "custom":
        if frequency_days and frequency_days.strip():
            try:
                val = int(frequency_days.strip())
                if val > 0:
                    freq_days = val
            except ValueError:
                pass
        if not freq_days:
            frequency = "monthly"
    bill.frequency = frequency
    bill.frequency_days = freq_days

    if amount_strategy is not None and amount_strategy.strip():
        strat = amount_strategy.strip().lower()
        if strat in ("fixed", "rolling_avg", "last_payment"):
            bill.amount_strategy = strat

    try:
        validate_recurrence_settings(bill.frequency, bill.frequency_days, bill.anchor_day, bill.next_due_date)
        if ledger_enabled is True:
            enable_ledger(db, bill)
        elif ledger_enabled is False and bill.ledger_enabled:
            if bill.invoices or bill.payments or bill.adjustments or bill.statements:
                raise ValueError('Invoice tracking stays enabled while account history exists.')
            bill.ledger_enabled = False
        open_invoice = bill.open_invoice
        if open_invoice:
            sync_invoice_due_date(bill, open_invoice, bill.next_due_date)
            reconcile_allocations(db, bill)
            bill.is_due_date_confirmed = True
        validate_opening_balance(bill)
    except ValueError as error:
        db.rollback()
        raise HTTPException(400, str(error))
    bill.modified_at = utcnow()
    create_journal_entry(db, "Updated Bill", f"Updated bill '{bill.name}'", "bill", bill.id)
    db.commit()
    return RedirectResponse(url="/classic/bills", status_code=303)


@app.post("/bills/{bill_id}/delete")
def delete_bill(bill_id: int, db: Session = Depends(get_db)):
    """Permanently delete a bill and its payment records."""
    lock_account(db)
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        return RedirectResponse(url="/classic/bills", status_code=303)
    name = bill.name
    for payment in bill.payments:
        retain_payment_submission(db, payment, deleted=True)
    db.query(BillPaymentSubmission).filter_by(bill_id=bill.id).update({'is_deleted': True}, synchronize_session='fetch')
    mark_bill_records_deleted(db, bill)
    db.delete(bill)
    create_journal_entry(db, "Deleted Bill", f"Deleted bill '{name}' and all associated payment history", "bill", bill_id)
    db.commit()
    return RedirectResponse(url="/classic/bills", status_code=303)


def _duplicate_invoice_response(request, db, bill_id, error, action, fields):
    db.rollback()
    fields['request_token'] = fields.get('request_token') or uuid4().hex
    return templates.TemplateResponse(request, 'duplicate_invoice.html', {
        'bill': db.get(Bill, bill_id), 'existing': db.get(BillInvoice, error.invoice_id),
        'action': action, 'fields': fields,
        'same_number': error.same_number,
    }, status_code=409)


@app.post("/bills/{bill_id}/confirm-invoice")
def confirm_bill_invoice(
    request: Request,
    bill_id: int,
    actual_due_date: str = Form(""),
    actual_amount: Optional[str] = Depends(form_text("actual_amount")),
    amount_type: str = Form("gross"),
    request_token: Optional[str] = Form(None),
    allow_duplicate: bool = Form(False),
    invoice_number: Optional[str] = Depends(form_text("invoice_number")),
    invoice_date: Optional[str] = Depends(form_text("invoice_date")),
    invoice_target: str = Form('new'),
    credit_applied: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    lock_account(db)
    bill = db.get(Bill, bill_id)
    if not bill:
        return RedirectResponse(url="/classic/bills", status_code=303)
    try:
        due = _parse_date_or_datetime(actual_due_date.strip()) if actual_due_date.strip() else None
    except (ValueError, TypeError):
        return RedirectResponse(url="/classic/bills", status_code=303)
    try:
        if actual_amount is not None and actual_amount.strip():
            repeated = submission_recorded(db, 'invoice', request_token)
            existing = None
            if invoice_target != 'new':
                if not invoice_target.isdecimal():
                    raise ValueError('Choose a new invoice or the invoice to correct.')
                existing = db.get(BillInvoice, int(invoice_target))
                if not existing or existing.bill_id != bill.id:
                    raise ValueError('The invoice to correct does not belong to this bill.')
                if allow_duplicate:
                    raise ValueError('Choose a new invoice to record a separate charge.')
            number_metadata = {'invoice_number': invoice_number} if invoice_number is not None else {}
            if invoice_date is not None:
                number_metadata['invoice_date'] = _parse_date_or_datetime(invoice_date.strip()) if invoice_date.strip() else None
            if credit_applied is not None and credit_applied.strip():
                number_metadata['credit_applied_cents'] = money_cents(credit_applied)
            invoice = record_invoice(db, bill, actual_amount, due, amount_type, invoice=existing,
                                     request_token=request_token, allow_duplicate=allow_duplicate, **number_metadata)
            if repeated:
                db.rollback()
                return RedirectResponse(url='/classic/bills', status_code=303)
            if invoice == bill.ordered_invoices[-1]:
                bill.expected_amount = stored_amount(invoice.amount_cents, 'Recurring estimate')
            if due and not bill.anchor_day:
                bill.anchor_day = due.day
        elif due:
            if invoice_target != 'new':
                raise ValueError('Enter the invoice amount to record a new invoice or correct an existing invoice.')
            previous, fingerprint = due_date_submission(db, bill, request_token, due=due,
                amount=actual_amount, amount_type=amount_type, invoice_number=invoice_number,
                invoice_date=invoice_date, credit_applied=credit_applied)
            if previous:
                db.rollback()
                return RedirectResponse(url='/classic/bills', status_code=303)
            if bill.open_invoice:
                raise ValueError('Enter the invoice amount to record a new invoice or correct an existing invoice.')
            bill.next_due_date = due
            bill.is_due_date_confirmed = True
            if actual_amount is not None:
                bill.expected_amount = None
            if request_token:
                db.add(BillDueDateSubmission(bill_id=bill.id, request_token=request_token,
                                            request_fingerprint=fingerprint))
        else:
            return RedirectResponse(url="/classic/bills", status_code=303)
    except DuplicateInvoiceError as error:
        return _duplicate_invoice_response(request, db, bill_id, error,
            f'/bills/{bill_id}/confirm-invoice', {'actual_due_date': actual_due_date,
            'actual_amount': actual_amount, 'amount_type': amount_type, 'request_token': request_token,
            'invoice_number': invoice_number, 'invoice_date': invoice_date,
            'invoice_target': invoice_target, 'credit_applied': credit_applied})
    except ValueError as error:
        db.rollback()
        raise HTTPException(400, str(error))
    bill.modified_at = utcnow()
    create_journal_entry(db, "Confirmed Bill Due Date", f"Confirmed invoice for '{bill.name}'", "bill", bill.id)
    db.commit()
    return RedirectResponse(url="/classic/bills", status_code=303)


@app.post("/bills/{bill_id}/invoices")
def save_bill_invoice(
    request: Request,
    bill_id: int, amount: str = Form(""), due_date: str = Form(""),
    amount_type: str = Form("gross"), note: str = Form(""),
    invoice_id: Optional[int] = Form(None),
    invoice_date: Optional[str] = Depends(form_text("invoice_date")),
    period_start: Optional[str] = Depends(form_text("period_start")),
    period_end: Optional[str] = Depends(form_text("period_end")),
    statement_balance: Optional[str] = Depends(form_text("statement_balance")),
    balance_type: str = Form("owing"), db: Session = Depends(get_db),
    request_token: Optional[str] = Form(None), allow_duplicate: bool = Form(False),
    invoice_number: Optional[str] = Depends(form_text("invoice_number")),
    credit_applied: Optional[str] = Form(None),
):
    lock_account(db)
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(404, "Bill not found")
    invoice = db.get(BillInvoice, invoice_id) if invoice_id else None
    if invoice_id and (not invoice or invoice.bill_id != bill.id):
        raise HTTPException(404, "Invoice not found")
    try:
        due = _parse_date_or_datetime(due_date.strip()) if due_date.strip() else None
        metadata = {}
        if invoice_number is not None:
            metadata['invoice_number'] = invoice_number
        if credit_applied is not None and credit_applied.strip():
            metadata['credit_applied_cents'] = money_cents(credit_applied)
        for field, value in [('invoice_date', invoice_date), ('period_start', period_start), ('period_end', period_end)]:
            if value is not None:
                metadata[field] = _parse_date_or_datetime(value.strip()) if value.strip() else None
        if statement_balance is not None:
            if balance_type not in ('owing', 'credit'):
                raise ValueError('Choose owing or credit for the statement balance.')
            metadata['statement_balance_cents'] = (money_cents(statement_balance) * (-1 if balance_type == 'credit' else 1)) if statement_balance.strip() else None
        repeated = submission_recorded(db, 'invoice', request_token)
        record_invoice(db, bill, amount, due, amount_type, note, invoice,
                       request_token=request_token, allow_duplicate=allow_duplicate, **metadata)
        if repeated:
            db.rollback()
            return RedirectResponse(url=f'/bills/{bill_id}/history', status_code=303)
    except DuplicateInvoiceError as error:
        fields = {'amount': amount, 'due_date': due_date, 'amount_type': amount_type, 'note': note,
                  'invoice_date': invoice_date, 'period_start': period_start, 'period_end': period_end,
                  'statement_balance': statement_balance, 'balance_type': balance_type,
                  'request_token': request_token}
        fields['invoice_number'] = invoice_number
        fields['credit_applied'] = credit_applied
        return _duplicate_invoice_response(request, db, bill_id, error, f'/bills/{bill_id}/invoices', fields)
    except (ValueError, TypeError) as error:
        db.rollback()
        raise HTTPException(400, str(error))
    create_journal_entry(db, "Recorded Bill Invoice", f"Recorded invoice for '{bill.name}'", "bill", bill.id)
    db.commit()
    return RedirectResponse(url=f"/bills/{bill.id}/history", status_code=303)


@app.post('/bills/{bill_id}/adjustments')
def save_bill_adjustment(bill_id: int, kind: str = Form(...), amount: str = Form(""),
                         effective_date: str = Form(...), note: str = Form(''),
                         adjustment_id: Optional[int] = Form(None), request_token: Optional[str] = Form(None),
                         db: Session = Depends(get_db)):
    lock_account(db)
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(404, 'Bill not found')
    adjustment = db.get(BillAdjustment, adjustment_id) if adjustment_id else None
    if adjustment_id and (not adjustment or adjustment.bill_id != bill_id):
        raise HTTPException(404, 'Adjustment not found')
    try:
        repeated = submission_recorded(db, 'adjustment', request_token)
        record_adjustment(db, bill, kind, amount, _parse_date_or_datetime(effective_date), note, adjustment, request_token)
        if not repeated:
            create_journal_entry(db, 'Recorded Bill Adjustment', f'{BillAdjustment.LABELS[kind]} for {bill.name}', 'bill', bill_id)
        db.commit()
    except (ValueError, TypeError) as error:
        db.rollback()
        raise HTTPException(400, str(error))
    return RedirectResponse(url=f'/bills/{bill_id}/history#adjustments', status_code=303)


@app.post('/bills/{bill_id}/adjustments/{adjustment_id}/delete')
def delete_bill_adjustment(bill_id: int, adjustment_id: int, db: Session = Depends(get_db)):
    lock_account(db)
    adjustment = db.get(BillAdjustment, adjustment_id)
    if not adjustment or adjustment.bill_id != bill_id:
        raise HTTPException(404, 'Adjustment not found')
    bill = adjustment.bill
    mark_record_deleted(db, 'adjustment', adjustment)
    db.delete(adjustment)
    db.flush()
    db.expire(bill, ['adjustments'])
    reconcile_allocations(db, bill)
    create_journal_entry(db, 'Deleted Bill Adjustment', f'Deleted adjustment for {bill.name}', 'bill', bill_id)
    db.commit()
    return RedirectResponse(url=f'/bills/{bill_id}/history#adjustments', status_code=303)


@app.post('/bills/{bill_id}/statements')
def save_bill_statement(bill_id: int, amount: str = Form(""), balance_type: str = Form('owing'),
                        as_of_date: str = Form(...), note: str = Form(''),
                        statement_id: Optional[int] = Form(None), request_token: Optional[str] = Form(None),
                        db: Session = Depends(get_db)):
    lock_account(db)
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(404, 'Bill not found')
    statement = db.get(BillStatement, statement_id) if statement_id else None
    if statement_id and (not statement or statement.bill_id != bill_id):
        raise HTTPException(404, 'Statement not found')
    try:
        repeated = submission_recorded(db, 'statement', request_token)
        record_statement(db, bill, amount, balance_type, _parse_date_or_datetime(as_of_date), note, statement, request_token)
        if not repeated:
            create_journal_entry(db, 'Recorded Supplier Balance', f'Recorded supplier balance for {bill.name}', 'bill', bill_id)
        db.commit()
    except (ValueError, TypeError) as error:
        db.rollback()
        raise HTTPException(400, str(error))
    return RedirectResponse(url=f'/bills/{bill_id}/history#reconciliation', status_code=303)


@app.post('/bills/{bill_id}/statements/{statement_id}/delete')
def delete_bill_statement(bill_id: int, statement_id: int, db: Session = Depends(get_db)):
    lock_account(db)
    statement = db.get(BillStatement, statement_id)
    if not statement or statement.bill_id != bill_id:
        raise HTTPException(404, 'Statement not found')
    mark_record_deleted(db, 'statement', statement)
    db.delete(statement)
    create_journal_entry(db, 'Deleted Supplier Balance', 'Deleted supplier balance observation', 'bill', bill_id)
    db.commit()
    return RedirectResponse(url=f'/bills/{bill_id}/history#reconciliation', status_code=303)


@app.post("/bills/{bill_id}/extra-payment")
def extra_bill_payment(
    bill_id: int, amount_paid: str = Form(""), paid_date: str = Form(""),
    note: str = Form(""), request_token: Optional[str] = Form(None), db: Session = Depends(get_db),
):
    lock_account(db)
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(404, "Bill not found")
    try:
        cents = money_cents(amount_paid)
        if cents == 0:
            raise ValueError("Enter the amount of money actually paid.")
        requested_paid = _parse_date_or_datetime(paid_date.strip()) if paid_date.strip() else None
        previous, fingerprint = payment_submission(db, bill, request_token, operation='extra', amount=cents,
                                                   paid=requested_paid, note=note.strip())
        if previous:
            db.rollback()
            return RedirectResponse(url=f'/bills/{bill_id}/history', status_code=303)
        paid = requested_paid or _midnight(datetime.now())
        payment_note = json.dumps([{"text": note.strip(), "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")}]) if note.strip() else None
        record_cash_payment(db, bill, cents, paid, note=payment_note,
                            request_token=request_token, request_fingerprint=fingerprint)
    except (ValueError, TypeError) as error:
        db.rollback()
        raise HTTPException(400, str(error))
    create_journal_entry(db, "Extra Bill Payment", f"Paid {bill.currency_symbol}{cents / 100:.2f} to '{bill.name}'", "bill", bill.id)
    db.commit()
    return RedirectResponse(url=f"/bills/{bill.id}/history", status_code=303)


@app.post("/bills/{bill_id}/pay")
def pay_bill(
    bill_id: int, amount_paid: Optional[str] = Depends(form_text("amount_paid")), paid_date: str = Form(""),
    actual_due_date: str = Form(""), note: str = Form(""), next_due_override: str = Form(""),
    invoice_id: Optional[int] = Form(None), request_token: Optional[str] = Form(None), db: Session = Depends(get_db),
):
    lock_account(db)
    bill = db.get(Bill, bill_id)
    if not bill:
        return RedirectResponse(url="/classic/bills", status_code=303)
    try:
        requested_cents = money_cents(amount_paid) if amount_paid is not None else None
        requested_paid = _parse_date_or_datetime(paid_date.strip()) if paid_date.strip() else None
        due = _parse_date_or_datetime(actual_due_date.strip()) if actual_due_date.strip() else None
        override = _parse_date_or_datetime(next_due_override.strip()) if next_due_override.strip() else None
        previous, fingerprint = payment_submission(db, bill, request_token, operation='invoice', amount=requested_cents,
            paid=requested_paid, due=due, invoice=invoice_id, note=note.strip(), override=override)
        if previous:
            db.rollback()
            return RedirectResponse(url='/classic/bills', status_code=303)
        cents = requested_cents
        if cents is None:
            cents = (bill.outstanding_cents if bill.ledger_enabled and bill.outstanding_cents
                     else money_cents(bill.effective_amount))
        paid_amount = stored_amount(cents, 'Payment')
        paid = requested_paid or _midnight(datetime.now())
        was_enabled = bill.ledger_enabled
        enable_ledger(db, bill)
        invoice = db.get(BillInvoice, invoice_id) if invoice_id else bill.open_invoice
        if invoice_id and (not invoice or invoice.bill_id != bill.id):
            raise ValueError("Invoice does not belong to this bill.")
        if invoice is None:
            if was_enabled:
                raise ValueError("Record or confirm the invoice first. Use Record extra payment for an overpayment or duplicate.")
            # Backwards compatibility for an account's first payment without an invoice.
            invoice = record_invoice(db, bill, paid_amount, due or bill.next_due_date)
            invoice.is_legacy = True
        elif due:
            previous_due = sync_invoice_due_date(bill, invoice, due)
            refresh_invoice_forecast(bill, invoice, previous_due)
        payment_note = json.dumps([{"text": note.strip(), "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")}]) if note.strip() else None
        record_cash_payment(db, bill, cents, paid, invoice, payment_note,
                            request_token=request_token, request_fingerprint=fingerprint)
        if bill.expected_amount is None:
            bill.expected_amount = stored_amount(invoice.amount_cents, 'Recurring estimate')
        if invoice.due_date and not bill.anchor_day:
            bill.anchor_day = invoice.due_date.day
        if override and not bill.open_invoice:
            bill.next_due_date = override
            bill.is_due_date_confirmed = True
    except (ValueError, TypeError) as error:
        db.rollback()
        raise HTTPException(400, str(error))
    create_journal_entry(db, "Paid Bill", f"Paid bill '{bill.name}' — {bill.currency_symbol}{cents / 100:.2f}", "bill", bill.id)
    db.commit()
    return RedirectResponse(url="/classic/bills", status_code=303)


@app.post("/bills/{bill_id}/toggle-active")
def toggle_bill_active(bill_id: int, show_inactive: int = 0, db: Session = Depends(get_db)):
    """Deactivate or reactivate a bill."""
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        return RedirectResponse(url="/classic/bills", status_code=303)
    bill.is_active = not bill.is_active
    bill.modified_at = utcnow()
    action = "Reactivated" if bill.is_active else "Deactivated"
    create_journal_entry(db, f"{action} Bill", f"{action} bill '{bill.name}'", "bill", bill.id)
    db.commit()
    redirect_url = "/classic/bills?show_inactive=1" if show_inactive else "/classic/bills"
    return RedirectResponse(url=redirect_url, status_code=303)


@app.get("/bills/{bill_id}/history", response_class=HTMLResponse)
def bill_history(request: Request, bill_id: int, db: Session = Depends(get_db)):
    """View payment history for a specific bill."""
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        return RedirectResponse(url="/classic/bills", status_code=303)

    payments = (
        db.query(BillPayment)
        .filter(BillPayment.bill_id == bill_id)
        .order_by(BillPayment.due_date.desc(), BillPayment.id.desc())
        .all()
    )

    # Calculate stats accurately by only averaging payments where an amount was entered
    paid_amounts = [p.amount_paid for p in payments if p.amount_paid is not None]
    total_paid = sum(paid_amounts)
    avg_amount = total_paid / len(paid_amounts) if paid_amounts else 0.0

    return templates.TemplateResponse(request, "bill_history.html", {
        "request": request,
        "bill": bill,
        "payments": payments,
        "total_paid": total_paid,
        "avg_amount": avg_amount,
        "has_paid_amounts": bool(paid_amounts),
        'refunds_total': sum(a.amount_cents for a in bill.adjustments if a.kind == 'refund') / 100,
        'statement_comparisons': statement_comparisons(bill),
        'adjustment_types': BillAdjustment.LABELS,
        "today_iso": datetime.now().strftime('%Y-%m-%d'),
    })


@app.post("/bills/payments/{payment_id}/edit")
def edit_bill_payment(
    payment_id: int,
    paid_date: str = Form(...),
    due_date: str = Form(""),
    amount_paid: str = Form(""),
    note: Optional[str] = Form(None),
    clear_note: bool = Form(False),
    request_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Edit a historical payment record."""
    lock_account(db)
    payment = db.query(BillPayment).filter(BillPayment.id == payment_id).first()
    if not payment:
        if request_token and db.query(BillPaymentSubmission.id).filter_by(request_token=request_token).first():
            db.rollback()
            raise HTTPException(400, 'This payment was deleted. Reload History and use a new form.')
        return RedirectResponse(url="/classic/bills", status_code=303)
    try:
        validate_invoice_account(payment.bill, payment.invoice)
        paid = _parse_date_or_datetime(paid_date.strip())
        due = _parse_date_or_datetime(due_date.strip()) if due_date.strip() else None
        cents = money_cents(amount_paid) if amount_paid.strip() else None
        previous, fingerprint = payment_submission(db, payment.bill, request_token,
            operation='edit', payment=payment.id, paid=paid, due=due, amount=cents,
            note=note.strip() if note is not None else None, clear_note=clear_note)
        if previous:
            db.rollback()
            return RedirectResponse(url=f'/bills/{payment.bill_id}/history', status_code=303)
        if cents is None and payment.amount_cents is not None:
            raise ValueError('Enter the amount of money actually paid.')
        validate_cash_date(paid)
        paid_amount = stored_amount(cents, 'Payment') if cents is not None else None
        payment.paid_date = paid
        if due:
            if payment.invoice:
                previous_due = sync_invoice_due_date(payment.bill, payment.invoice, due)
                refresh_invoice_forecast(payment.bill, payment.invoice, previous_due)
            else:
                payment.due_date = due
        payment.amount_paid = paid_amount
        if payment.amount_cents is not None or (payment.bill.ledger_enabled and cents is not None):
            payment.amount_cents = cents
        if clear_note:
            payment.note = None
        elif note and note.strip() and note.strip() != payment.display_note.strip():
            payment.note = json.dumps([{"text": note.strip(), "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")}])
        if payment.bill.ledger_enabled:
            reconcile_allocations(db, payment.bill)
        if request_token:
            retain_payment_submission(db, payment, request_token=request_token, request_fingerprint=fingerprint)
    except (ValueError, TypeError) as error:
        db.rollback()
        raise HTTPException(400, str(error))
    bill_name = payment.bill.name if payment.bill else str(payment.bill_id)
    create_journal_entry(db, "Edited Bill Payment", f"Edited payment #{payment.id} for bill '{bill_name}'", "bill", payment.bill_id)
    db.commit()
    return RedirectResponse(url=f"/bills/{payment.bill_id}/history", status_code=303)


@app.post("/bills/payments/{payment_id}/delete")
def delete_bill_payment(
    payment_id: int,
    restore_due_date: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Delete a payment record, with option to restore the bill's next due date."""
    lock_account(db)
    payment = db.query(BillPayment).filter(BillPayment.id == payment_id).first()
    if not payment:
        return RedirectResponse(url="/classic/bills", status_code=303)

    bill_id = payment.bill_id
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if bill and restore_due_date and not bill.ledger_enabled:
        latest = (
            db.query(BillPayment)
            .filter(BillPayment.bill_id == bill_id)
            .order_by(BillPayment.due_date.desc(), BillPayment.id.desc())
            .first()
        )
        if latest and latest.id == payment.id:
            bill.next_due_date = payment.due_date
            bill.is_due_date_confirmed = True
            if not bill.anchor_day and payment.due_date:
                bill.anchor_day = payment.due_date.day
            bill.modified_at = utcnow()

    bill_name = bill.name if bill else str(bill_id)
    retain_payment_submission(db, payment, deleted=True)
    db.delete(payment)
    db.flush()
    if bill and bill.ledger_enabled:
        db.expire(bill, ['payments'])
        reconcile_allocations(db, bill)
    create_journal_entry(db, "Deleted Bill Payment", f"Deleted payment #{payment_id} for bill '{bill_name}'", "bill", bill_id)
    db.commit()
    return RedirectResponse(url=f"/bills/{bill_id}/history", status_code=303)


@app.post("/bills/payments/{payment_id}/toggle-late-override")
def toggle_late_override(payment_id: int, db: Session = Depends(get_db)):
    """Toggle the late-status override for a specific payment."""
    payment = db.query(BillPayment).filter(BillPayment.id == payment_id).first()
    if not payment:
        return RedirectResponse(url="/classic/bills", status_code=303)
    payment.is_late_overridden = not payment.is_late_overridden
    bill_name = payment.bill.name if payment.bill else str(payment.bill_id)
    action = "Marked Bill Payment On Time" if payment.is_late_overridden else "Removed Bill Payment On Time Override"
    create_journal_entry(db, action, f"{action} for payment #{payment.id} of bill '{bill_name}'", "bill", payment.bill_id)
    db.commit()
    return RedirectResponse(url=f"/bills/{payment.bill_id}/history", status_code=303)


@app.post("/bills/payments/{payment_id}/add-note")
def add_payment_note(payment_id: int, note_text: str = Form(...), db: Session = Depends(get_db)):
    """Append a note to an existing bill payment."""
    import json as _json
    payment = db.query(BillPayment).filter(BillPayment.id == payment_id).first()
    if not payment:
        return RedirectResponse(url="/classic/bills", status_code=303)
    if not note_text.strip():
        return RedirectResponse(url=f"/bills/{payment.bill_id}/history", status_code=303)
    # Migrate existing note to list format, then append
    notes = payment.notes_list
    notes.append({"text": note_text.strip(), "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")})
    payment.note = _json.dumps(notes)
    bill_name = payment.bill.name if payment.bill else str(payment.bill_id)
    create_journal_entry(db, "Added Bill Payment Note", f"Added note to payment #{payment.id} for bill '{bill_name}'", "bill", payment.bill_id)
    db.commit()
    return RedirectResponse(url=f"/bills/{payment.bill_id}/history", status_code=303)


# ============================================================================
# QUICK EXPENSES
# ============================================================================

@app.get("/expenses", response_class=HTMLResponse)
def expenses_dashboard(request: Request, db: Session = Depends(get_db)):
    """Quick Expenses dashboard: real-time entry form, spending averages, and expense list."""
    today = date_type.today()
    today_iso = today.strftime("%Y-%m-%d")

    expenses = db.query(Expense).order_by(Expense.date.desc(), Expense.id.desc()).all()
    recorded_expenses = [e for e in expenses if _to_date(e.date) and _to_date(e.date) <= today]

    active_bills = db.query(Bill).filter(Bill.is_active == True).all()
    payments = db.query(BillPayment).options(joinedload(BillPayment.bill)).all()
    refunds = db.query(BillAdjustment).options(joinedload(BillAdjustment.bill)).filter(BillAdjustment.kind == 'refund').all()
    currency_summaries = spending_summaries(recorded_expenses, active_bills, payments, refunds, today)
    summary = currency_summaries[0] if len(currency_summaries) == 1 else {}
    category_breakdown = [{**category, 'currency': row['currency'], 'currency_symbol': row['currency_symbol']}
                          for row in currency_summaries for category in row['category_breakdown']]
    include_bills = request.query_params.get("include_bills") in ("1", "true", "yes")

    known_categories = sorted(list({e.category.strip() for e in expenses if e.category and e.category.strip()}))
    default_currency_symbol = expenses[0].currency_symbol if expenses else "$"

    return templates.TemplateResponse(request, "expenses.html", {
        **summary,
        "request": request,
        "expenses": expenses,
        "today_iso": today_iso,
        "total_count": len(recorded_expenses),
        "category_breakdown": category_breakdown,
        "known_categories": known_categories,
        "default_currency_symbol": default_currency_symbol,
        "active_bills_count": len(active_bills),
        "currency_summaries": currency_summaries,
        "include_bills": include_bills,
    })


@app.post("/expenses/create")
def create_expense(
    amount: str = Form(""),
    description: str = Form(""),
    category: str = Form(""),
    date: str = Form(""),
    payment_method: str = Form("Card"),
    currency: str = Form("AUD"),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    """Create a new expense from the quick entry form."""
    try:
        amt = expense_amount(amount)
    except (ValueError, TypeError) as error:
        db.rollback()
        raise HTTPException(400, str(error))

    exp_date = None
    if date and date.strip():
        try:
            exp_date = _parse_date_or_datetime(date.strip())
        except (ValueError, TypeError):
            exp_date = _midnight(datetime.now())
    if not exp_date:
        exp_date = _midnight(datetime.now())

    expense = Expense(
        amount=amt,
        description=description.strip() or None,
        category=category.strip() or "Other",
        date=exp_date,
        payment_method=payment_method.strip() or "Card",
        currency=currency.strip().upper() if currency and currency.strip() else "AUD",
        note=note.strip() or None,
    )
    db.add(expense)
    db.commit()

    desc_label = expense.description or expense.category or "Expense"
    create_journal_entry(db, "Added Expense", f"Logged expense '{desc_label}' for {expense.currency_symbol}{expense.amount:.2f}", "expense", expense.id)
    db.commit()

    return RedirectResponse(url="/expenses", status_code=303)


@app.post("/expenses/{expense_id}/delete")
def delete_expense(expense_id: int, return_to: Optional[str] = Form(None), db: Session = Depends(get_db)):
    """Delete an expense."""
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    redirect_url = return_to if return_to and return_to.startswith("/expenses") else "/expenses"
    if not expense:
        return RedirectResponse(url=redirect_url, status_code=303)

    desc_label = expense.description or expense.category or "Expense"
    amt_label = f"{expense.currency_symbol}{expense.amount:.2f}"
    db.delete(expense)
    create_journal_entry(db, "Deleted Expense", f"Deleted expense #{expense_id} '{desc_label}' ({amt_label})", "expense", expense_id)
    db.commit()

    return RedirectResponse(url=redirect_url, status_code=303)


@app.post("/expenses/{expense_id}/update")
def update_expense(
    expense_id: int,
    amount: str = Form(""),
    description: str = Form(""),
    category: str = Form(""),
    date: str = Form(""),
    payment_method: str = Form("Card"),
    currency: Optional[str] = Form(None),
    note: str = Form(""),
    return_to: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Update an existing expense."""
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    redirect_url = return_to if return_to and return_to.startswith("/expenses") else "/expenses"
    if not expense:
        return RedirectResponse(url=redirect_url, status_code=303)

    try:
        amt = expense_amount(amount)
    except (ValueError, TypeError) as error:
        db.rollback()
        raise HTTPException(400, str(error))

    expense.amount = amt

    expense.description = description.strip() or None
    expense.category = category.strip() or "Other"
    expense.payment_method = payment_method.strip() or "Card"
    if currency and currency.strip():
        expense.currency = currency.strip().upper()
    expense.note = note.strip() or None

    if date and date.strip():
        try:
            expense.date = _parse_date_or_datetime(date.strip())
        except (ValueError, TypeError):
            pass

    expense.modified_at = utcnow()
    create_journal_entry(db, "Updated Expense", f"Updated expense #{expense.id} '{expense.description or expense.category}'", "expense", expense.id)
    db.commit()

    return RedirectResponse(url=redirect_url, status_code=303)


@app.get("/expenses/list", response_class=HTMLResponse)
@app.get("/expenses/all", response_class=HTMLResponse)
def expenses_all_list(request: Request, db: Session = Depends(get_db)):
    """Full list ledger view of all expenses with sorting, filtering, and export."""
    expenses = db.query(Expense).order_by(Expense.date.desc(), Expense.id.desc()).all()
    currency_summaries = spending_summaries(expenses, [], [], [], date_type.today())
    summary = currency_summaries[0] if len(currency_summaries) == 1 else {}

    known_categories = sorted(list({e.category.strip() for e in expenses if e.category and e.category.strip()}))
    known_methods = sorted(list({e.payment_method.strip() for e in expenses if e.payment_method and e.payment_method.strip()}))
    default_currency_symbol = expenses[0].currency_symbol if expenses else "$"

    return templates.TemplateResponse(request, "expenses_list.html", {
        **summary,
        "request": request,
        "expenses": expenses,
        "total_count": len(expenses),
        "currency_summaries": currency_summaries,
        "known_categories": known_categories,
        "known_methods": known_methods,
        "default_currency_symbol": default_currency_symbol,
    })


@app.get("/expenses/export/csv")
def export_expenses_csv(db: Session = Depends(get_db)):
    """Export all expenses to a downloadable CSV file."""
    import csv
    import io

    expenses = db.query(Expense).order_by(Expense.date.desc(), Expense.id.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Date", "Description", "Category", "Payment Method", "Amount", "Currency", "Note"])
    for e in expenses:
        d_str = e.date.strftime("%Y-%m-%d") if e.date and hasattr(e.date, "strftime") else str(e.date or "")
        writer.writerow([
            e.id,
            d_str,
            e.description or "",
            e.category or "Other",
            e.payment_method or "",
            f"{e.amount:.2f}" if e.amount is not None else "0.00",
            e.currency or "AUD",
            e.note or "",
        ])
    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=expenses_{date_type.today().strftime('%Y%m%d')}.csv"}
    )




@app.post("/todo/reorder")
def reorder_todos(
    source_ids: list[int] = Form(...),
    target_id: int = Form(...),
    list_id: int = Form(None),  # Optional: the collation list context
    db: Session = Depends(get_db)
):
    # If list_id is provided, use that as the context (for collation lists)
    # Otherwise, use the target's list
    target = db.query(Todo).filter(Todo.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target todo not found")
    
    if list_id:
        todo_list = db.query(List).filter(List.id == list_id).first()
        if not todo_list:
            raise HTTPException(status_code=404, detail="List not found")
    else:
        todo_list = target.list
        list_id = todo_list.id
        
    # Enable manual sorting for the list if not already enabled
    if not todo_list.manual_sorting:
        todo_list.manual_sorting = True
        
        # Initialize sort orders for all native todos in list
        all_todos = db.query(Todo).filter(Todo.list_id == list_id).order_by(Todo.created_at.desc()).all()
        for i, t in enumerate(all_todos):
            t.sort_order = i * 10
        
        # If collation list, also initialize sort orders for collected todos
        if todo_list.is_collation:
            collected_rows = db.execute(
                collation_todos.select().where(collation_todos.c.collation_list_id == list_id)
            ).fetchall()
            base_order = len(all_todos) * 10  # Start after native todos
            for i, row in enumerate(collected_rows):
                db.execute(
                    collation_todos.update()
                    .where(collation_todos.c.collation_list_id == list_id)
                    .where(collation_todos.c.todo_id == row.todo_id)
                    .values(sort_order=base_order + (i * 10))
                )
    
    # Build unified item list (native todos + collected todos for collation lists)
    # Each item is (todo_id, is_collected, current_sort_order)
    items = []
    
    # Native todos
    native_todos = db.query(Todo).filter(Todo.list_id == list_id).order_by(Todo.sort_order.asc(), Todo.created_at.desc()).all()
    native_ids = set(t.id for t in native_todos)
    for t in native_todos:
        items.append((t.id, False, t.sort_order))
    
    # Collected todos (for collation lists)
    collected_ids = set()
    if todo_list.is_collation:
        collected_rows = db.execute(
            collation_todos.select()
            .where(collation_todos.c.collation_list_id == list_id)
            .order_by(collation_todos.c.sort_order.asc())
        ).fetchall()
        for row in collected_rows:
            items.append((row.todo_id, True, row.sort_order))
            collected_ids.add(row.todo_id)
    
    # Sort items by their sort_order
    items.sort(key=lambda x: x[2])
    
    # Validate source IDs - they must exist in our items list
    all_item_ids = {i[0] for i in items}
    valid_source_ids = [sid for sid in source_ids if sid in all_item_ids and sid != target_id]
    
    if not valid_source_ids:
        return RedirectResponse(url=f"/list/{list_id}", status_code=303)
    
    source_id_set = set(valid_source_ids)
    
    # Build the new order: remove sources, find target, insert sources after target
    item_ids_without_sources = [i[0] for i in items if i[0] not in source_id_set]
    
    try:
        tgt_idx = item_ids_without_sources.index(target_id)
        # Insert all sources AFTER target
        item_ids_without_sources[tgt_idx + 1 : tgt_idx + 1] = valid_source_ids
    except ValueError:
        # Target not found (shouldn't happen), append sources
        item_ids_without_sources.extend(valid_source_ids)
    
    new_order = item_ids_without_sources
    
    # Re-assign sort orders for all items
    native_todo_map = {t.id: t for t in native_todos}
    
    for i, tid in enumerate(new_order):
        new_sort_order = i * 10
        
        if tid in native_ids:
            # Update native todo's sort_order
            native_todo_map[tid].sort_order = new_sort_order
        elif tid in collected_ids:
            # Update collation junction table's sort_order
            db.execute(
                collation_todos.update()
                .where(collation_todos.c.collation_list_id == list_id)
                .where(collation_todos.c.todo_id == tid)
                .values(sort_order=new_sort_order)
            )
            
    db.commit()
    response = RedirectResponse(url=f"/list/{list_id}", status_code=303)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.post("/list/{list_id}/toggle_manual_sort")
def toggle_manual_sort(
    list_id: int,
    manual_sorting: bool = Form(...),
    db: Session = Depends(get_db)
):
    lst = db.query(List).filter(List.id == list_id).first()
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")
        
    lst.manual_sorting = manual_sorting
    db.commit()
    response = RedirectResponse(url=f"/list/{list_id}", status_code=303)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.post("/list/{list_id}/toggle_recipe_mode")
def toggle_recipe_mode(
    list_id: int,
    db: Session = Depends(get_db)
):
    """Toggle recipe mode for a list (requires manual_sorting to be enabled)."""
    from models import IngredientEvent
    lst = db.query(List).filter(List.id == list_id).first()
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")
    
    # Recipe mode requires manual sorting
    if not lst.manual_sorting:
        raise HTTPException(status_code=400, detail="Recipe mode requires manual sorting (Daily Plan) to be enabled")
    
    lst.is_recipe = not lst.is_recipe
    create_journal_entry(db, "Toggled Recipe Mode", f"{'Enabled' if lst.is_recipe else 'Disabled'} recipe mode for list '{lst.name}'", "list", list_id)
    db.commit()
    return RedirectResponse(url=f"/list/{list_id}", status_code=303)


@app.post("/api/todo/{todo_id}/ingredient_event")
async def create_ingredient_event(
    todo_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Record an ingredient event (bought, ran_out, reset)."""
    from models import IngredientEvent
    
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        return JSONResponse({"error": "Todo not found"}, status_code=404)
    
    # Check if the list is in recipe mode
    if not todo.list or not todo.list.is_recipe:
        return JSONResponse({"error": "List is not in recipe mode"}, status_code=400)
    
    data = await request.json()
    event_type = data.get("event_type", "").strip()
    quantity = data.get("quantity", 1)
    note = data.get("note", "").strip() or None
    event_date_str = data.get("event_date")
    
    event_date = None
    if event_date_str:
        try:
            # Parse YYYY-MM-DD
            dt = datetime.strptime(event_date_str, "%Y-%m-%d")
            # Set to current time or noon to be safe from timezone shifts if only date provided
            event_date = dt.replace(hour=12, minute=0)
        except ValueError:
            pass # Fallback to now
            
    if event_type not in ["bought", "ran_out", "reset"]:
        return JSONResponse({"error": "Invalid event_type. Must be 'bought', 'ran_out', or 'reset'"}, status_code=400)
    
    event = IngredientEvent(
        todo_id=todo_id,
        event_type=event_type,
        quantity=quantity,
        note=note,
        event_date=event_date or utcnow()
    )
    db.add(event)
    
    create_journal_entry(db, f"Ingredient {event_type.replace('_', ' ').title()}", f"Recorded '{event_type}' event for ingredient '{todo.title}' (qty: {quantity})", "todo", todo_id)
    db.commit()
    
    return JSONResponse({"success": True, "event_id": event.id, "event_type": event_type})


@app.delete("/api/ingredient_event/{event_id}")
async def delete_ingredient_event(
    event_id: int,
    db: Session = Depends(get_db)
):
    """Delete an ingredient event."""
    from models import IngredientEvent
    
    event = db.query(IngredientEvent).filter(IngredientEvent.id == event_id).first()
    if not event:
        return JSONResponse({"error": "Event not found"}, status_code=404)
        
    db.delete(event)
    db.commit()
    
    return JSONResponse({"success": True})


@app.post("/api/list/{list_id}/reset_ingredients")
async def reset_all_ingredients(
    list_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Bulk reset all ingredients in a list (marks them as 'bought now')."""
    from models import IngredientEvent
    
    lst = db.query(List).filter(List.id == list_id).first()
    if not lst:
        return JSONResponse({"error": "List not found"}, status_code=404)
    
    if not lst.is_recipe:
        return JSONResponse({"error": "List is not in recipe mode"}, status_code=400)
    
    data = await request.json()
    quantity = data.get("quantity", 1)
    
    count = 0
    for todo in lst.todos:
        if not todo.completed:  # Only reset uncompleted ingredients
            event = IngredientEvent(
                todo_id=todo.id,
                event_type="reset",
                quantity=quantity
            )
            db.add(event)
            count += 1
    
    create_journal_entry(db, "Reset All Ingredients", f"Reset {count} ingredients in recipe list '{lst.name}'", "list", list_id)
    db.commit()
    
    return JSONResponse({"success": True, "reset_count": count})


@app.post("/api/list/{list_id}/bulk_ingredient_event")
async def bulk_ingredient_event(
    list_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Bulk mark selected todos with an ingredient event."""
    from models import IngredientEvent
    
    lst = db.query(List).filter(List.id == list_id).first()
    if not lst:
        return JSONResponse({"error": "List not found"}, status_code=404)
    
    if not lst.is_recipe:
        return JSONResponse({"error": "List is not in recipe mode"}, status_code=400)
    
    data = await request.json()
    todo_ids = data.get("todo_ids", [])
    event_type = data.get("event_type", "").strip()
    quantity = data.get("quantity", 1)
    
    if event_type not in ["bought", "ran_out"]:
        return JSONResponse({"error": "Invalid event_type"}, status_code=400)
    
    count = 0
    for todo_id in todo_ids:
        todo = db.query(Todo).filter(Todo.id == todo_id, Todo.list_id == list_id).first()
        if todo:
            event = IngredientEvent(
                todo_id=todo_id,
                event_type=event_type,
                quantity=quantity
            )
            db.add(event)
            count += 1
    
    create_journal_entry(db, f"Bulk {event_type.replace('_', ' ').title()}", f"Marked {count} ingredients as '{event_type}' in list '{lst.name}'", "list", list_id)
    db.commit()
    
    return JSONResponse({"success": True, "updated_count": count})


def get_ingredient_stats(todo, now=None):
    """Calculate ingredient statistics for a todo item.
    
    Returns dict with:
    - days_since_bought: days since last 'bought' or 'reset' event
    - days_since_ran_out: days since last 'ran_out' event
    - average_cycle: average days between 'bought' and 'ran_out' events
    - likely_to_run_out: boolean heuristic
    - last_bought_quantity: quantity of last purchase
    """
    from models import IngredientEvent
    
    if now is None:
        now = utcnow()
    
    events = sorted(todo.ingredient_events, key=lambda e: e.event_date, reverse=True)
    
    if not events:
        return {
            "days_since_bought": None,
            "days_since_ran_out": None,
            "average_cycle": None,
            "likely_to_run_out": False,
            "last_bought_quantity": None,
            "total_bought": 0,
            "total_ran_out": 0,
        }
    
    # Find last bought/reset and last ran_out
    last_bought = None
    last_ran_out = None
    last_bought_quantity = None
    
    for e in events:
        if e.event_type in ["bought", "reset"] and last_bought is None:
            last_bought = e.event_date
            last_bought_quantity = e.quantity
        if e.event_type == "ran_out" and last_ran_out is None:
            last_ran_out = e.event_date
        if last_bought and last_ran_out:
            break
    
    days_since_bought = None
    days_since_ran_out = None
    
    if last_bought:
        days_since_bought = (now - last_bought).days
    if last_ran_out:
        days_since_ran_out = (now - last_ran_out).days
    
    # Calculate average cycle (bought -> ran_out pairs)
    bought_events = [e for e in events if e.event_type in ["bought", "reset"]]
    ran_out_events = [e for e in events if e.event_type == "ran_out"]
    
    cycles = []
    for ran_out in ran_out_events:
        # Find the most recent bought event before this ran_out
        prior_bought = None
        for bought in bought_events:
            if bought.event_date < ran_out.event_date:
                prior_bought = bought
                break
        if prior_bought:
            cycle_days = (ran_out.event_date - prior_bought.event_date).days
            if cycle_days > 0:
                cycles.append(cycle_days)
    
    average_cycle = None
    if cycles:
        average_cycle = sum(cycles) / len(cycles)
    
    # Heuristic: likely to run out if days_since_bought > 80% of average_cycle
    likely_to_run_out = False
    if average_cycle and days_since_bought is not None:
        if days_since_bought > 0.8 * average_cycle:
            likely_to_run_out = True
    
    # Also mark as likely if we have ran_out events but no recent bought (> 30 days)
    if not likely_to_run_out and days_since_bought and days_since_bought > 30 and len(ran_out_events) > 0:
        likely_to_run_out = True
    
    return {
        "days_since_bought": days_since_bought,
        "days_since_ran_out": days_since_ran_out,
        "average_cycle": round(average_cycle, 1) if average_cycle else None,
        "likely_to_run_out": likely_to_run_out,
        "last_bought_quantity": last_bought_quantity,
        "total_bought": len(bought_events),
        "total_ran_out": len(ran_out_events),
        "last_event_type": events[0].event_type if events else None,
    }


def ingredient_report_data(db, lst):
    """Open ingredients of a recipe list with stats, likely-to-run-out first."""
    now = utcnow()
    ingredients = []
    
    for todo in lst.todos:
        if todo.completed:
            continue

        if todo.ingredient_group_id:
            stats = get_grouped_ingredient_stats(todo.ingredient_group, now)
        else:
            stats = get_ingredient_stats(todo, now)
            
        ingredients.append({
            "todo": todo,
            "stats": stats,
        })
    
    # Sort by likely_to_run_out first, then by days_since_bought (longest first)
    ingredients.sort(key=lambda x: (
        not x["stats"]["likely_to_run_out"],  # True first
        -(x["stats"]["days_since_bought"] or 0)  # Highest days first
    ))
    
    return ingredients


@app.get("/list/{list_id}/ingredient_report", response_class=HTMLResponse)
def ingredient_report_page(request: Request, list_id: int, db: Session = Depends(get_db)):
    """Display ingredient report for a recipe list."""
    lst = db.query(List).filter(List.id == list_id).first()
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")
    if not lst.is_recipe:
        raise HTTPException(status_code=400, detail="This list is not in recipe mode")
    return templates.TemplateResponse(request, "ingredient_report.html", {
        "request": request,
        "list": lst,
        "ingredients": ingredient_report_data(db, lst),
        "now": utcnow(),
    })


def shopping_list_data(db, lst):
    """Open ingredients of a recipe list in list order, with a buying status."""
    now = utcnow()
    shopping_items = []
    
    # Fetch todos explicitly sorted by sort_order to respect manual ordering
    # (lst.todos relationship doesn't guarantee order)
    todos = db.query(Todo).filter(Todo.list_id == lst.id).order_by(Todo.sort_order.asc(), Todo.id.asc()).all()
    
    for todo in todos:
        if todo.completed:
            continue  # Skip completed items
            
        # Initialize defaults
        needs_buying = False
        status = None

        if todo.ingredient_group_id:
            # Use grouped stats if part of a group
            stats = get_grouped_ingredient_stats(todo.ingredient_group, now)
            # For grouped items, we check the last event on the GROUP (which is aggregated in stats)
            if stats.get("last_event_type") == "ran_out":
                needs_buying = True
                status = "ran_out"
            elif stats.get("likely_to_run_out"):
                needs_buying = True
                status = "likely_low"
        else:
            # ORIGINAL LOGIC for ungrouped items
            stats = get_ingredient_stats(todo, now)
            
            # Check if the most recent event is "ran_out"
            events = sorted(todo.ingredient_events, key=lambda e: e.event_date, reverse=True)
            if events:
                last_event = events[0]
                if last_event.event_type == "ran_out":
                    needs_buying = True
                    status = "ran_out"
            
            # Also include if likely to run out (but not already marked as ran_out)
            if not needs_buying and stats["likely_to_run_out"]:
                needs_buying = True
                status = "likely_low"
        
        if needs_buying:
            pass # status is already set
        elif stats.get("days_since_bought") is None and stats.get("total_bought", 0) == 0:
             status = "new"
        else:
             status = "in_stock"

        shopping_items.append({
            "todo": todo,
            "stats": stats,
            "status": status,
        })
    
    # No sorting - keep original list order
    
    return shopping_items


@app.get("/list/{list_id}/shopping_list", response_class=HTMLResponse)
def shopping_list_page(request: Request, list_id: int, db: Session = Depends(get_db)):
    """Display shopping list for a recipe list - items that have run out or are likely to."""
    lst = db.query(List).filter(List.id == list_id).first()
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")
    if not lst.is_recipe:
        raise HTTPException(status_code=400, detail="This list is not in recipe mode")
    return templates.TemplateResponse(request, "shopping_list.html", {
        "request": request,
        "list": lst,
        "shopping_items": shopping_list_data(db, lst),
        "now": utcnow(),
    })


# ---------------------------------------------------------------------------
# PANTRY / INGREDIENT GROUPS
# ---------------------------------------------------------------------------

def normalize_ingredient_name(name: str) -> str:
    """Normalize an ingredient name for matching purposes.
    
    - Lowercase
    - Strip whitespace
    - Remove common prefixes/suffixes
    - Remove pluralization (simple)
    """
    import re
    
    if not name:
        return ""
    
    # Lowercase and strip
    normalized = name.lower().strip()
    
    # Remove common prefixes
    prefixes = ['fresh ', 'organic ', 'dried ', 'frozen ', 'canned ', 'chopped ', 'diced ', 'sliced ']
    for prefix in prefixes:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    
    # Remove common suffixes
    suffixes = [' (fresh)', ' (frozen)', ' (canned)', ' (dried)']
    for suffix in suffixes:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)]
    
    # Simple pluralization: remove trailing 's' if word is longer than 3 chars
    # (to avoid turning "eggs" into "egg" but keep "peas" as "pea")
    if len(normalized) > 3 and normalized.endswith('s') and not normalized.endswith('ss'):
        normalized = normalized[:-1]
    
    # Remove 'ies' -> 'y' (berries -> berry)
    if normalized.endswith('ie') and len(normalized) > 3:
        normalized = normalized[:-2] + 'y'
    
    return normalized.strip()


def find_similar_ingredients(target_name: str, all_todos: list) -> list:
    """Find todos with similar ingredient names."""
    target_normalized = normalize_ingredient_name(target_name)
    
    matches = []
    for todo in all_todos:
        if normalize_ingredient_name(todo.title) == target_normalized:
            matches.append(todo)
    
    return matches


def get_grouped_ingredient_stats(group, now=None):
    """Calculate ingredient statistics for an ingredient group.
    
    Uses the most recent events across ALL linked todos.
    """
    from models import IngredientEvent
    
    if now is None:
        now = utcnow()
    
    # Collect all events from all todos in the group
    all_events = []
    for todo in group.todos:
        all_events.extend(todo.ingredient_events)
    
    events = sorted(all_events, key=lambda e: e.event_date, reverse=True)
    
    if not events:
        return {
            "days_since_bought": None,
            "days_since_ran_out": None,
            "average_cycle": None,
            "likely_to_run_out": False,
            "last_bought_quantity": None,
            "total_bought": 0,
            "total_ran_out": 0,
            "last_event_type": None,
        }
    
    # Find last bought/reset and last ran_out
    last_bought = None
    last_ran_out = None
    last_bought_quantity = None
    last_event_type = events[0].event_type if events else None
    
    for e in events:
        if e.event_type in ["bought", "reset"] and last_bought is None:
            last_bought = e.event_date
            last_bought_quantity = e.quantity
        if e.event_type == "ran_out" and last_ran_out is None:
            last_ran_out = e.event_date
        if last_bought and last_ran_out:
            break
    
    days_since_bought = None
    days_since_ran_out = None
    
    if last_bought:
        days_since_bought = (now - last_bought).days
    if last_ran_out:
        days_since_ran_out = (now - last_ran_out).days
    
    # Calculate average cycle
    bought_events = [e for e in events if e.event_type in ["bought", "reset"]]
    ran_out_events = [e for e in events if e.event_type == "ran_out"]
    
    cycles = []
    for ran_out in ran_out_events:
        prior_bought = None
        for bought in bought_events:
            if bought.event_date < ran_out.event_date:
                prior_bought = bought
                break
        if prior_bought:
            cycle_days = (ran_out.event_date - prior_bought.event_date).days
            if cycle_days > 0:
                cycles.append(cycle_days)
    
    average_cycle = None
    if cycles:
        average_cycle = sum(cycles) / len(cycles)
    
    # Heuristic: likely to run out
    likely_to_run_out = False
    if average_cycle and days_since_bought is not None:
        if days_since_bought > 0.8 * average_cycle:
            likely_to_run_out = True
    
    if not likely_to_run_out and days_since_bought and days_since_bought > 30 and len(ran_out_events) > 0:
        likely_to_run_out = True
    
    return {
        "days_since_bought": days_since_bought,
        "days_since_ran_out": days_since_ran_out,
        "average_cycle": round(average_cycle, 1) if average_cycle else None,
        "likely_to_run_out": likely_to_run_out,
        "last_bought_quantity": last_bought_quantity,
        "total_bought": len(bought_events),
        "total_ran_out": len(ran_out_events),
        "last_event_type": last_event_type,
    }


def pantry_data(db):
    """Ingredient groups, ungrouped ingredients and linking suggestions across recipe lists."""
    from models import IngredientGroup
    
    # Get all recipe lists
    recipe_lists = db.query(List).filter(List.is_recipe == True, List.archived == False).all()
    
    # Get all ingredient todos from recipe lists
    all_ingredient_todos = []
    for lst in recipe_lists:
        for todo in lst.todos:
            if not todo.completed:
                all_ingredient_todos.append(todo)
    
    # Get all existing ingredient groups
    groups = db.query(IngredientGroup).order_by(IngredientGroup.canonical_name).all()
    
    now = utcnow()
    
    # Build grouped ingredients data
    grouped_ingredients = []
    for group in groups:
        active_todos = [t for t in group.todos if not t.completed and t.list.is_recipe]
        if active_todos:
            stats = get_grouped_ingredient_stats(group, now)
            grouped_ingredients.append({
                "group": group,
                "todos": active_todos,
                "stats": stats,
                "recipes": list(set(t.list.name for t in active_todos)),
            })
    
    # Find ungrouped ingredients (not linked to any group)
    ungrouped_todos = [t for t in all_ingredient_todos if t.ingredient_group_id is None]
    
    # Group ungrouped by normalized name to suggest linking
    ungrouped_by_name = {}
    for todo in ungrouped_todos:
        normalized = normalize_ingredient_name(todo.title)
        if normalized not in ungrouped_by_name:
            ungrouped_by_name[normalized] = []
        ungrouped_by_name[normalized].append(todo)
    
    # Build suggestions for linking
    suggestions = []
    for normalized_name, todos in ungrouped_by_name.items():
        if len(todos) > 1:
            # Multiple todos with same normalized name - suggest linking
            suggestions.append({
                "normalized_name": normalized_name,
                "todos": todos,
                "recipes": list(set(t.list.name for t in todos)),
            })
    
    # Sort suggestions by number of matches (most first)
    suggestions.sort(key=lambda x: len(x["todos"]), reverse=True)
    
    return {
        "recipe_lists": recipe_lists,
        "grouped_ingredients": grouped_ingredients,
        "ungrouped_todos": ungrouped_todos,
        "ungrouped_by_name": ungrouped_by_name,
        "suggestions": suggestions,
        "now": now,
    }


@app.get("/pantry", response_class=HTMLResponse)
def pantry_page(request: Request, db: Session = Depends(get_db)):
    """Display all ingredients across all recipe lists with grouping."""
    return templates.TemplateResponse(request, "pantry.html", {
        "request": request,
        **pantry_data(db),
        "get_ingredient_stats": get_ingredient_stats,
    })


@app.get("/ingredient/{todo_id}/history", response_class=HTMLResponse)
def ingredient_history_page(request: Request, todo_id: int, db: Session = Depends(get_db)):
    """Display history of purchase dates for an ingredient."""
    from models import IngredientEvent
    
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        raise HTTPException(status_code=404, detail="Ingredient not found")
        
    # Gather events (grouped or singular)
    if todo.ingredient_group_id:
        events = []
        for t in todo.ingredient_group.todos:
            events.extend(t.ingredient_events)
    else:
        events = list(todo.ingredient_events)
    
    # Sort events by date descending
    events.sort(key=lambda x: x.event_date, reverse=True)
    
    return templates.TemplateResponse(request, "ingredient_history.html", {
        "request": request,
        "todo": todo,
        "events": events,
        "is_grouped": bool(todo.ingredient_group_id),
        "group_name": todo.ingredient_group.canonical_name if todo.ingredient_group_id else None
    })


@app.post("/api/ingredient/create_group")
async def create_ingredient_group(request: Request, db: Session = Depends(get_db)):
    """Create a new ingredient group and link todos to it."""
    from models import IngredientGroup
    
    data = await request.json()
    canonical_name = data.get("canonical_name", "").strip()
    todo_ids = data.get("todo_ids", [])
    
    if not canonical_name:
        return JSONResponse({"error": "canonical_name is required"}, status_code=400)
    
    if not todo_ids:
        return JSONResponse({"error": "At least one todo_id is required"}, status_code=400)
    
    # Create the group
    group = IngredientGroup(canonical_name=canonical_name)
    db.add(group)
    db.flush()
    
    # Link todos to the group
    count = 0
    for todo_id in todo_ids:
        todo = db.query(Todo).filter(Todo.id == todo_id).first()
        if todo:
            todo.ingredient_group_id = group.id
            count += 1
    
    create_journal_entry(db, "Created Ingredient Group", f"Created group '{canonical_name}' with {count} ingredients", "ingredient_group", group.id)
    db.commit()
    
    return JSONResponse({"success": True, "group_id": group.id, "linked_count": count})


@app.post("/api/ingredient/link_to_group")
async def link_ingredient_to_group(request: Request, db: Session = Depends(get_db)):
    """Link a todo to an existing ingredient group."""
    from models import IngredientGroup
    
    data = await request.json()
    group_id = data.get("group_id")
    todo_id = data.get("todo_id")
    
    if not group_id or not todo_id:
        return JSONResponse({"error": "group_id and todo_id are required"}, status_code=400)
    
    group = db.query(IngredientGroup).filter(IngredientGroup.id == group_id).first()
    if not group:
        return JSONResponse({"error": "Group not found"}, status_code=404)
    
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        return JSONResponse({"error": "Todo not found"}, status_code=404)
    
    todo.ingredient_group_id = group_id
    create_journal_entry(db, "Linked Ingredient", f"Linked '{todo.title}' to group '{group.canonical_name}'", "ingredient_group", group_id)
    db.commit()
    
    return JSONResponse({"success": True})


@app.post("/api/ingredient/unlink")
async def unlink_ingredient(request: Request, db: Session = Depends(get_db)):
    """Unlink a todo from its ingredient group."""
    from models import IngredientGroup
    
    data = await request.json()
    todo_id = data.get("todo_id")
    
    if not todo_id:
        return JSONResponse({"error": "todo_id is required"}, status_code=400)
    
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        return JSONResponse({"error": "Todo not found"}, status_code=404)
    
    old_group_id = todo.ingredient_group_id
    todo.ingredient_group_id = None
    
    # Clean up empty groups
    if old_group_id:
        group = db.query(IngredientGroup).filter(IngredientGroup.id == old_group_id).first()
        if group:
            remaining = db.query(Todo).filter(Todo.ingredient_group_id == old_group_id).count()
            if remaining == 0:
                db.delete(group)
    
    create_journal_entry(db, "Unlinked Ingredient", f"Unlinked '{todo.title}' from its group", "todo", todo_id)
    db.commit()
    
    return JSONResponse({"success": True})


@app.post("/api/ingredient/rename_group")
async def rename_ingredient_group(request: Request, db: Session = Depends(get_db)):
    """Rename an ingredient group."""
    from models import IngredientGroup
    
    data = await request.json()
    group_id = data.get("group_id")
    new_name = data.get("canonical_name", "").strip()
    
    if not group_id or not new_name:
        return JSONResponse({"error": "group_id and canonical_name are required"}, status_code=400)
    
    group = db.query(IngredientGroup).filter(IngredientGroup.id == group_id).first()
    if not group:
        return JSONResponse({"error": "Group not found"}, status_code=404)
    
    old_name = group.canonical_name
    group.canonical_name = new_name
    create_journal_entry(db, "Renamed Ingredient Group", f"Renamed '{old_name}' to '{new_name}'", "ingredient_group", group_id)
    db.commit()
    
    return JSONResponse({"success": True})


@app.post("/api/ingredient/buy_group/{group_id}")
async def buy_ingredient_group(group_id: int, request: Request, db: Session = Depends(get_db)):
    """Mark an ingredient group as bought - records event for all linked todos."""
    from models import IngredientGroup, IngredientEvent
    
    group = db.query(IngredientGroup).filter(IngredientGroup.id == group_id).first()
    if not group:
        return JSONResponse({"error": "Group not found"}, status_code=404)
    
    data = await request.json()
    quantity = data.get("quantity", 1)
    
    # Record bought event for the first active todo (to avoid duplicates)
    # The grouped stats will pick up this event
    active_todos = [t for t in group.todos if not t.completed and t.list.is_recipe]
    
    if not active_todos:
        return JSONResponse({"error": "No active ingredients in group"}, status_code=400)
    
    # Record event for the first todo - this is the "group" event
    event = IngredientEvent(
        todo_id=active_todos[0].id,
        event_type="bought",
        quantity=quantity
    )
    db.add(event)
    
    create_journal_entry(db, "Bought Ingredient (Grouped)", f"Bought '{group.canonical_name}' (qty: {quantity})", "ingredient_group", group_id)
    db.commit()
    
    return JSONResponse({"success": True, "quantity": quantity})


@app.post("/api/ingredient/ran_out_group/{group_id}")
async def ran_out_ingredient_group(group_id: int, request: Request, db: Session = Depends(get_db)):
    """Mark an ingredient group as ran out."""
    from models import IngredientGroup, IngredientEvent
    
    group = db.query(IngredientGroup).filter(IngredientGroup.id == group_id).first()
    if not group:
        return JSONResponse({"error": "Group not found"}, status_code=404)
    
    data = await request.json()
    quantity = data.get("quantity", 1)
    
    active_todos = [t for t in group.todos if not t.completed and t.list.is_recipe]
    
    if not active_todos:
        return JSONResponse({"error": "No active ingredients in group"}, status_code=400)
    
    event = IngredientEvent(
        todo_id=active_todos[0].id,
        event_type="ran_out",
        quantity=quantity
    )
    db.add(event)
    
    create_journal_entry(db, "Ran Out (Grouped)", f"'{group.canonical_name}' ran out", "ingredient_group", group_id)
    db.commit()
    
    return JSONResponse({"success": True})


def find_hashtag_results(db, hashtag):
    """Return the lists, todos and calendar events shown for a hashtag."""
    today = _midnight(datetime.now())
    completion_rows = db.query(CalendarEventCompletion).all()
    completed_set = {(c.event_id, c.occurrence_at) for c in completion_rows}

    processed_events = []
    for ev in hashtag.calendar_events:
        next_occ = get_next_uncompleted_occurrence(ev, completed_set, today)
        if not hashtag.show_completed:
            # When hiding completed items, skip events with no future uncompleted occurrence.
            if not next_occ:
                continue
            ev.display_start_at = next_occ
        else:
            # Prefer the next occurrence; fall back to the original start for exhausted events.
            ev.display_start_at = next_occ if next_occ else ev.start_at
        processed_events.append(ev)
    processed_events.sort(key=lambda x: x.display_start_at)

    if hashtag.show_completed:
        target_todos = list(hashtag.todos)
    else:
        target_todos = [t for t in hashtag.todos if not t.completed]

    # Global cascade: include todos from lists tagged with this hashtag,
    # without duplicating todos that are tagged directly.
    if hashtag.include_list_items:
        existing_ids = {t.id for t in target_todos}
        for lst in hashtag.lists:
            if hashtag.hide_completed_list_items and lst.completed:
                continue
            for t in lst.todos:
                if t.id not in existing_ids:
                    if hashtag.show_completed or not t.completed:
                        target_todos.append(t)
                        existing_ids.add(t.id)

    if hashtag.show_completed:
        lists = sorted(hashtag.lists, key=lambda x: x.modified_at, reverse=True)
    else:
        lists = sorted([l for l in hashtag.lists if not l.completed], key=lambda x: x.modified_at, reverse=True)
    return {"lists": lists, "todos": target_todos, "events": processed_events}


def load_hashtag_for_results(db, hashtag_id):
    # Eager load related data to avoid N+1 queries during grouping/display
    return db.query(Hashtag).options(
        joinedload(Hashtag.todos).joinedload(Todo.list).joinedload(List.folder),
        joinedload(Hashtag.lists).joinedload(List.folder)
    ).filter(Hashtag.id == hashtag_id).first()


@app.get("/hashtag/{hashtag_id}", response_class=HTMLResponse)
def view_hashtag_items(request: Request, hashtag_id: int, db: Session = Depends(get_db)):
    """View all items with a specific hashtag"""
    hashtag = load_hashtag_for_results(db, hashtag_id)
    if not hashtag:
        raise HTTPException(status_code=404, detail="Hashtag not found")
    results = find_hashtag_results(db, hashtag)

    from grouping_service import organize_todos_into_tree
    root = organize_todos_into_tree(db, results["todos"])

    return templates.TemplateResponse(request, "view_hashtag.html", {
        "request": request,
        "hashtag": hashtag,
        "lists": results["lists"],
        "todo_tree": root,
        "calendar_events": results["events"],
    })



@app.post("/hashtag/{hashtag_id}/update")
def update_hashtag(
    hashtag_id: int,
    include_list_items: bool = Form(False),
    hide_completed_list_items: bool = Form(False),
    db: Session = Depends(get_db)
):
    hashtag = db.query(Hashtag).filter(Hashtag.id == hashtag_id).first()
    if not hashtag:
        raise HTTPException(status_code=404, detail="Hashtag not found")
        
    hashtag.include_list_items = include_list_items
    hashtag.hide_completed_list_items = hide_completed_list_items
    db.commit()
    
    return RedirectResponse(url=f"/hashtags", status_code=303)


@app.get("/search/{search_id}", response_class=HTMLResponse)
def view_named_search(request: Request, search_id: int, db: Session = Depends(get_db)):
    """View results of a named search"""
    named_search = db.query(NamedSearch).filter(NamedSearch.id == search_id).first()
    if not named_search:
        raise HTTPException(status_code=404, detail="Named search not found")
    
    # Included/excluded hashtags for this search.
    include_ids = [h.id for h in named_search.hashtags]
    exclude_ids = [h.id for h in getattr(named_search, "excluded_hashtags", [])]

    # Find all lists and todos with any of the included hashtags (if any), then
    # exclude anything that has any excluded hashtag.
    lists_query = db.query(List)
    todos_query = db.query(Todo)
    events_query = db.query(CalendarEvent)

    if include_ids:
        lists_query = lists_query.join(List.hashtags).filter(Hashtag.id.in_(include_ids))
        todos_query = todos_query.join(Todo.hashtags).filter(Hashtag.id.in_(include_ids))
        events_query = events_query.join(CalendarEvent.hashtags).filter(Hashtag.id.in_(include_ids))
    
    if exclude_ids:
        lists_query = lists_query.filter(~List.hashtags.any(Hashtag.id.in_(exclude_ids)))
        todos_query = todos_query.filter(~Todo.hashtags.any(Hashtag.id.in_(exclude_ids)))
        events_query = events_query.filter(~CalendarEvent.hashtags.any(Hashtag.id.in_(exclude_ids)))
    
    if named_search.uncompleted_only:
        lists_query = lists_query.filter(List.completed == False)
        todos_query = todos_query.filter(Todo.completed == False)
        # Calendar events don't have a single "completed" state, so we ignore this filter for them,
        # or we could filter out past events? Let's just return them all for now.
    
    matching_lists = lists_query.distinct().all()
    matching_todos = todos_query.distinct().all()
    today = _midnight(datetime.now())
    completion_rows = db.query(CalendarEventCompletion).all()
    completed_set = {(c.event_id, c.occurrence_at) for c in completion_rows}

    # Process events to find next occurrence
    events_raw = events_query.distinct().all()
    processed_events = []
    
    for ev in events_raw:
        next_occ = get_next_uncompleted_occurrence(ev, completed_set, today)
        
        if named_search.uncompleted_only:
            # If hiding completed, skip if no future uncompleted occurrence
            if not next_occ:
                continue
            ev.display_start_at = next_occ
            processed_events.append(ev)
        else:
            # If showing completed, include. Default to next_occ or start_at
            ev.display_start_at = next_occ if next_occ else ev.start_at
            processed_events.append(ev)

    processed_events.sort(key=lambda x: x.display_start_at)

    # Use shared grouping service
    from grouping_service import organize_todos_into_tree
    todo_tree = organize_todos_into_tree(db, matching_todos)
    
    return templates.TemplateResponse(request, "search_results.html", {
        "request": request,
        "named_search": named_search,
        "lists": matching_lists,
        "todo_tree": todo_tree,
        "events": processed_events,
    })


# =============================================================================
# LIST CRUD OPERATIONS
# =============================================================================

@app.post("/list/create")
def create_list(
    name: str = Form(...),
    hashtags: str = Form(""),
    hashtag_ids: list[int] = Form([]),
    folder_id: Optional[int] = Form(None),
    manual_sorting: bool = Form(False),
    db: Session = Depends(get_db)
):
    """Create a new list"""
    settings = get_user_settings(db)
    
    # Priority: Form folder_id (if explicit) > Persistent Active Folder
    target_folder_id = (folder_id or None) if folder_id is not None else settings.active_folder_id
    
    todo_list = List(name=name, folder_id=target_folder_id, manual_sorting=manual_sorting)
    db.add(todo_list)
    db.flush()
    
    # Add hashtags from text input (parse #tag format)
    for tag_name in parse_hashtags(hashtags):
        hashtag = get_or_create_hashtag(db, tag_name)
        todo_list.hashtags.append(hashtag)
    
    # Add hashtags from multi-select by ID (avoid duplicates)
    existing_ids = {h.id for h in todo_list.hashtags}
    for hashtag_id in hashtag_ids:
        if hashtag_id not in existing_ids:
            hashtag = db.query(Hashtag).filter(Hashtag.id == hashtag_id).first()
            if hashtag:
                todo_list.hashtags.append(hashtag)
                existing_ids.add(hashtag_id)
    
    create_journal_entry(db, "Created List", f"Created list '{name}'", "list", todo_list.id)
    db.commit()
    
    url = "/"
    if folder_id is not None:
        url = f"/?folder_id={folder_id}"
        
    return RedirectResponse(url=url, status_code=303)


@app.post("/list/{list_id}/update")
def update_list(
    list_id: int,
    name: str = Form(...),
    hashtags: str = Form(""),
    is_delayed: bool = Form(False),
    delayed_until: str = Form(""),
    db: Session = Depends(get_db)
):
    """Update a list"""
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        raise HTTPException(status_code=404, detail="List not found")
    
    todo_list.name = name
    todo_list.modified_at = utcnow()
    
    old_delayed_until = todo_list.delayed_until

    # Handle delay
    if is_delayed and delayed_until:
        try:
            todo_list.delayed_until = _parse_date_or_datetime(delayed_until)
        except ValueError:
            pass
    else:
        todo_list.delayed_until = None

    if old_delayed_until != todo_list.delayed_until:
        if todo_list.delayed_until:
            create_journal_entry(db, "Set Deferral", f"Set deferral for list '{name}' until {todo_list.delayed_until}", "list", todo_list.id)
        else:
            create_journal_entry(db, "Cleared Deferral", f"Cleared deferral for list '{name}'", "list", todo_list.id)

    # Clear and re-add hashtags
    todo_list.hashtags.clear()
    current_tags = parse_hashtags(hashtags)
    
    # Manage #deferred tag
    if todo_list.delayed_until:
        if "deferred" not in current_tags:
            current_tags.append("deferred")
    else:
        if "deferred" in current_tags:
            current_tags.remove("deferred")

    for tag_name in current_tags:
        hashtag = get_or_create_hashtag(db, tag_name)
        todo_list.hashtags.append(hashtag)
    
    create_journal_entry(db, "Updated List", f"Updated list '{name}'", "list", todo_list.id)
    db.commit()
    return RedirectResponse(url="/classic", status_code=303)


@app.post("/list/{list_id}/delete")
def delete_list(list_id: int, db: Session = Depends(get_db)):
    """Delete a list and all its todos"""
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        raise HTTPException(status_code=404, detail="List not found")
    
    create_journal_entry(db, "Deleted List", f"Deleted list '{todo_list.name}'", "list", list_id)
    db.delete(todo_list)
    db.commit()
    return RedirectResponse(url="/classic", status_code=303)


@app.post("/list/{list_id}/toggle_sort_order")
def toggle_list_sort_order(list_id: int, db: Session = Depends(get_db)):
    lst = db.query(List).filter(List.id == list_id).first()
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")
    
    lst.sort_ascending = not lst.sort_ascending
    create_journal_entry(db, "Toggled Sort Order", f"Toggled sort order for list '{lst.name}' to {'Ascending' if lst.sort_ascending else 'Descending'}", "list", list_id)
    db.commit()
    return RedirectResponse(url=f"/list/{list_id}", status_code=303)


@app.post("/list/{list_id}/toggle_header_partitioning")
def toggle_list_header_partitioning(list_id: int, db: Session = Depends(get_db)):
    lst = db.query(List).filter(List.id == list_id).first()
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")
    
    lst.partition_by_datetime_headers = not lst.partition_by_datetime_headers
    create_journal_entry(db, "Toggled Header Partitioning", f"Toggled header partitioning for list '{lst.name}' to {'On' if lst.partition_by_datetime_headers else 'Off'}", "list", list_id)
    db.commit()
    return RedirectResponse(url=f"/list/{list_id}", status_code=303)


@app.post("/list/{list_id}/toggle_intersperse_collated")
def toggle_list_intersperse_collated(list_id: int, db: Session = Depends(get_db)):
    lst = db.query(List).filter(List.id == list_id).first()
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")
    
    lst.intersperse_collated_todos = not lst.intersperse_collated_todos
    create_journal_entry(db, "Toggled Intersperse Collated", f"Toggled intersperse collated for list '{lst.name}' to {'On' if lst.intersperse_collated_todos else 'Off'}", "list", list_id)
    db.commit()
    return RedirectResponse(url=f"/list/{list_id}", status_code=303)


@app.get("/list/{list_id}/edit", response_class=HTMLResponse)
def edit_list_page(request: Request, list_id: int, db: Session = Depends(get_db)):
    """Edit list page"""
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        raise HTTPException(status_code=404, detail="List not found")
    
    all_hashtags = db.query(Hashtag).order_by(Hashtag.name).all()
    return templates.TemplateResponse(request, "edit_list.html", {
        "request": request,
        "list": todo_list,
        "all_hashtags": all_hashtags
    })


# =============================================================================
# TODO CRUD OPERATIONS
# =============================================================================

@app.post("/todo/create")
def create_todo(
    list_id: int = Form(...),
    title: str = Form(...),
    hashtags: str = Form(""),
    parent_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    """Create a new todo"""
    # Check for manual sorting on the list
    todo_list = db.query(List).filter(List.id == list_id).first()
    sort_order = 0
    
    if todo_list and todo_list.manual_sorting:
        # Find max sort_order for current level (siblings)
        max_sort = db.query(func.max(Todo.sort_order)).filter(
            Todo.list_id == list_id,
            Todo.parent_id == parent_id
        ).scalar()
        
        if max_sort is not None:
            sort_order = max_sort + 10
        else:
            sort_order = 0

    todo = Todo(title=title, list_id=list_id, parent_id=parent_id, sort_order=sort_order)
    db.add(todo)
    db.flush()
    
    # Parse and add hashtags
    for tag_name in parse_hashtags(hashtags):
        hashtag = get_or_create_hashtag(db, tag_name)
        todo.hashtags.append(hashtag)
    
    # Update list modification date
    if todo_list:
        todo_list.modified_at = utcnow()
    
    create_journal_entry(db, "Created Todo", f"Created todo '{title}'", "todo", todo.id)
    db.commit()
    return RedirectResponse(url=f"/list/{list_id}", status_code=303)


@app.post("/api/todo/create")
async def api_create_todo(request: Request, db: Session = Depends(get_db)):
    """Create a new todo and return JSON (for reactive UI)"""
    data = await request.json()
    list_id = data.get("list_id")
    title = data.get("title", "").strip()
    hashtags_str = data.get("hashtags", "")
    parent_id = data.get("parent_id")
    
    if not list_id or not title:
        return JSONResponse({"error": "list_id and title are required"}, status_code=400)
    
    # Check for manual sorting on the list
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        return JSONResponse({"error": "List not found"}, status_code=404)
    
    sort_order = 0
    if todo_list.manual_sorting:
        max_sort = db.query(func.max(Todo.sort_order)).filter(
            Todo.list_id == list_id,
            Todo.parent_id == parent_id
        ).scalar()
        sort_order = (max_sort + 10) if max_sort is not None else 0

    todo = Todo(title=title, list_id=list_id, parent_id=parent_id, sort_order=sort_order)
    db.add(todo)
    db.flush()
    
    # Parse and add hashtags
    parsed_hashtags = []
    for tag_name in parse_hashtags(hashtags_str):
        hashtag = get_or_create_hashtag(db, tag_name)
        todo.hashtags.append(hashtag)
        parsed_hashtags.append({"id": hashtag.id, "name": hashtag.name})
    
    # Update list modification date
    todo_list.modified_at = utcnow()
    
    create_journal_entry(db, "Created Todo", f"Created todo '{title}'", "todo", todo.id)
    db.commit()
    
    return JSONResponse({
        "id": todo.id,
        "title": todo.title,
        "list_id": todo.list_id,
        "completed": todo.completed,
        "pinned": todo.pinned,
        "color": todo.color,
        "hashtags": parsed_hashtags,
        "created_at": todo.created_at.isoformat() if todo.created_at else None,
    })


@app.post("/todo/{todo_id}/update")
def update_todo(
    todo_id: int,
    title: str = Form(...),
    note: str = Form(""),
    color: str = Form(""),
    hashtags: str = Form(""),
    is_delayed: bool = Form(False),
    delayed_until: str = Form(""),
    # Alarm fields
    alarm_mode: str = Form("none"), # 'none', 'range', 'tolerance'
    alarm_start_range: str = Form(""),
    alarm_end_range: str = Form(""),
    alarm_target_time: str = Form(""),
    alarm_minutes: int = Form(0),
    db: Session = Depends(get_db)
):
    """Update a todo"""
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    
    todo.title = title
    todo.note = note
    todo.color = color.strip() or None
    todo.modified_at = utcnow()
    
    # --- ALARM LOGIC ---
    new_alarm_start = None
    new_alarm_end = None
    
    if alarm_mode == 'range' and alarm_start_range and alarm_end_range:
        try:
            # HTML5 datetime-local format: YYYY-MM-DDTHH:MM
            new_alarm_start = datetime.strptime(alarm_start_range, '%Y-%m-%dT%H:%M')
            new_alarm_end = datetime.strptime(alarm_end_range, '%Y-%m-%dT%H:%M')
        except ValueError:
             pass # Fail silently
    elif alarm_mode == 'tolerance' and alarm_target_time:
        try:
            target = datetime.strptime(alarm_target_time, '%Y-%m-%dT%H:%M')
            delta = timedelta(minutes=alarm_minutes)
            new_alarm_start = target - delta
            new_alarm_end = target + delta
        except ValueError:
            pass
            
    if new_alarm_start != todo.alarm_start or new_alarm_end != todo.alarm_end:
        todo.alarm_start = new_alarm_start
        todo.alarm_end = new_alarm_end
        if new_alarm_start:
             create_journal_entry(db, "Set Alarm", f"Set alarm for todo '{title}'", "todo", todo.id)
        else:
             create_journal_entry(db, "Cleared Alarm", f"Cleared alarm for todo '{title}'", "todo", todo.id)

    old_delayed_until = todo.delayed_until

    # Handle delay
    if is_delayed and delayed_until:
        try:
            todo.delayed_until = _parse_date_or_datetime(delayed_until)
        except ValueError:
            pass
    else:
        todo.delayed_until = None

    if old_delayed_until != todo.delayed_until:
        if todo.delayed_until:
            create_journal_entry(db, "Set Deferral", f"Set deferral for todo '{title}' until {todo.delayed_until}", "todo", todo.id)
        else:
            create_journal_entry(db, "Cleared Deferral", f"Cleared deferral for todo '{title}'", "todo", todo.id)

    # Clear and re-add hashtags
    todo.hashtags.clear()
    current_tags = parse_hashtags(hashtags)
    
    # Manage #deferred tag
    if todo.delayed_until:
        if "deferred" not in current_tags:
            current_tags.append("deferred")
    else:
        if "deferred" in current_tags:
            current_tags.remove("deferred")

    for tag_name in current_tags:
        hashtag = get_or_create_hashtag(db, tag_name)
        todo.hashtags.append(hashtag)
    
    # Update list modification date
    if todo.list:
        todo.list.modified_at = utcnow()
    
    create_journal_entry(db, "Updated Todo", f"Updated todo '{title}'", "todo", todo.id)
    db.commit()
    return RedirectResponse(url=f"/list/{todo.list_id}", status_code=303)


@app.post("/todo/{todo_id}/delete")
def delete_todo(todo_id: int, db: Session = Depends(get_db)):
    """Delete a todo"""
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    
    list_id = todo.list_id
    
    # Update list modification date
    if todo.list:
        todo.list.modified_at = utcnow()
    
    create_journal_entry(db, "Deleted Todo", f"Deleted todo '{todo.title}'", "todo", todo_id)
    db.delete(todo)
    db.commit()
    return RedirectResponse(url=f"/list/{list_id}", status_code=303)


# =============================================================================
# AJAX ENDPOINTS FOR CHECKBOX TOGGLING
# =============================================================================

@app.post("/api/todo/{todo_id}/toggle")
def toggle_todo(todo_id: int, db: Session = Depends(get_db)):
    """Toggle todo completion status (AJAX)"""
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        return JSONResponse({"error": "Todo not found"}, status_code=404)
    
    todo.completed = not todo.completed
    todo.modified_at = utcnow()
    
    if todo.list:
        todo.list.modified_at = utcnow()
    
    create_journal_entry(db, "Toggled Todo Completion", f"Toggled completion for todo '{todo.title}' to {todo.completed}", "todo", todo.id)
    db.commit()
    return JSONResponse({"completed": todo.completed})


@app.post("/api/list/{list_id}/toggle")
def toggle_list(list_id: int, db: Session = Depends(get_db)):
    """Toggle list completion status (AJAX)"""
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        return JSONResponse({"error": "List not found"}, status_code=404)
    
    todo_list.completed = not todo_list.completed
    todo_list.modified_at = utcnow()
    create_journal_entry(db, "Toggled List Completion", f"Toggled completion for list '{todo_list.name}' to {todo_list.completed}", "list", todo_list.id)
    db.commit()
    return JSONResponse({"completed": todo_list.completed})


@app.post("/api/list/{list_id}/toggle-show-completed")
def toggle_show_completed(list_id: int, db: Session = Depends(get_db)):
    """Toggle show_completed setting for a list (AJAX)"""
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        return JSONResponse({"error": "List not found"}, status_code=404)
    
    todo_list.show_completed = not todo_list.show_completed
    create_journal_entry(db, "Toggled Show Completed", f"Toggled show completed for list '{todo_list.name}' to {todo_list.show_completed}", "list", todo_list.id)
    db.commit()
    return JSONResponse({"show_completed": todo_list.show_completed})


@app.post("/api/list/{list_id}/toggle-show-delayed")
def toggle_show_delayed(list_id: int, db: Session = Depends(get_db)):
    """Toggle show_delayed setting for a list (AJAX)"""
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        return JSONResponse({"error": "List not found"}, status_code=404)
    
    todo_list.show_delayed = not todo_list.show_delayed
    create_journal_entry(db, "Toggled Show Delayed", f"Toggled show delayed for list '{todo_list.name}' to {todo_list.show_delayed}", "list", todo_list.id)
    db.commit()
    return JSONResponse({"show_delayed": todo_list.show_delayed})


@app.post("/api/list/{list_id}/toggle-archived")
def toggle_archived(list_id: int, db: Session = Depends(get_db)):
    """Toggle archived setting for a list (AJAX)"""
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        return JSONResponse({"error": "List not found"}, status_code=404)
    
    todo_list.archived = not todo_list.archived
    todo_list.modified_at = utcnow()
    create_journal_entry(db, "Toggled List Archived", f"Toggled archived for list '{todo_list.name}' to {todo_list.archived}", "list", todo_list.id)
    db.commit()
    return JSONResponse({"archived": todo_list.archived})


@app.post("/api/list/{list_id}/update-category")
def update_list_category(list_id: int, category: str = Form(""), db: Session = Depends(get_db)):
    """Update category for a list (AJAX)"""
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        return JSONResponse({"error": "List not found"}, status_code=404)
    
    # Empty string means no category
    new_category = category.strip() if category else None
    todo_list.category = new_category
    todo_list.modified_at = utcnow()
    create_journal_entry(db, "Updated List Category", f"Updated category for list '{todo_list.name}' to '{new_category or '(none)'}'", "list", todo_list.id)
    db.commit()
    return JSONResponse({"category": todo_list.category})


@app.post("/api/list/{list_id}/toggle-show-dokuwiki-item-links")
def toggle_show_dokuwiki_item_links(list_id: int, db: Session = Depends(get_db)):
    """Toggle visibility of per-item DokuWiki links on a list (AJAX)"""
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        return JSONResponse({"error": "List not found"}, status_code=404)
    
    todo_list.show_dokuwiki_item_links = not todo_list.show_dokuwiki_item_links
    create_journal_entry(db, "Toggled DokuWiki Item Links", f"Toggled DokuWiki item links for list '{todo_list.name}' to {todo_list.show_dokuwiki_item_links}", "list", todo_list.id)
    db.commit()
    return JSONResponse({"show_dokuwiki_item_links": todo_list.show_dokuwiki_item_links})


@app.post("/api/list/{list_id}/toggle-dokuwiki-page")
def toggle_list_dokuwiki_page(list_id: int, db: Session = Depends(get_db)):
    """Toggle dokuwiki_page_enabled for a list (AJAX)"""
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        return JSONResponse({"error": "List not found"}, status_code=404)
    
    todo_list.dokuwiki_page_enabled = not todo_list.dokuwiki_page_enabled
    create_journal_entry(db, "Toggled DokuWiki Page", f"Toggled DokuWiki page for list '{todo_list.name}' to {todo_list.dokuwiki_page_enabled}", "list", todo_list.id)
    db.commit()
    return JSONResponse({"dokuwiki_page_enabled": todo_list.dokuwiki_page_enabled})


@app.post("/api/bulk/toggle-dokuwiki-page")
async def bulk_toggle_dokuwiki_page(request: Request, db: Session = Depends(get_db)):
    """Enable or disable dokuwiki_page_enabled for multiple todos (AJAX)"""
    data = await request.json()
    ids = data.get('ids', [])
    enable = data.get('enable', True)
    
    if not ids:
        return JSONResponse({"error": "No items specified"}, status_code=400)
    
    updated = 0
    for todo_id in ids:
        todo = db.query(Todo).filter(Todo.id == int(todo_id)).first()
        if todo:
            todo.dokuwiki_page_enabled = enable
            updated += 1
    
    db.commit()
    return JSONResponse({"success": True, "updated": updated, "enabled": enable})


@app.post("/api/todo/{todo_id}/toggle-pinned")
def toggle_pinned(todo_id: int, db: Session = Depends(get_db)):
    """Toggle pinned status for a todo (AJAX)"""
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        return JSONResponse({"error": "Todo not found"}, status_code=404)
    
    todo.pinned = not todo.pinned
    todo.pinned_at = utcnow() if todo.pinned else None
    todo.modified_at = utcnow()
    
    if todo.list:
        todo.list.modified_at = utcnow()
    
    create_journal_entry(db, "Toggled Todo Pinned", f"Toggled pinned for todo '{todo.title}' to {todo.pinned}", "todo", todo.id)
    db.commit()
    return JSONResponse({"pinned": todo.pinned})


@app.post("/api/todo/{todo_id}/defer")
async def api_defer_todo(todo_id: int, request: Request, db: Session = Depends(get_db)):
    """Set or clear deferral for a single todo (AJAX)"""
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        return JSONResponse({"error": "Todo not found"}, status_code=404)
    
    data = await request.json()
    delayed_until_str = data.get("delayed_until", "")
    
    old_delayed_until = todo.delayed_until
    
    if delayed_until_str:
        try:
            todo.delayed_until = _parse_date_or_datetime(delayed_until_str)
        except ValueError:
            return JSONResponse({"error": "Invalid date format"}, status_code=400)
    else:
        todo.delayed_until = None
    
    todo.modified_at = utcnow()
    if todo.list:
        todo.list.modified_at = utcnow()
    
    # Manage #deferred hashtag
    deferred_tag = get_or_create_hashtag(db, "deferred")
    if todo.delayed_until:
        if deferred_tag not in todo.hashtags:
            todo.hashtags.append(deferred_tag)
        create_journal_entry(db, "Set Deferral", f"Set deferral for todo '{todo.title}' until {todo.delayed_until}", "todo", todo.id)
    else:
        if deferred_tag in todo.hashtags:
            todo.hashtags.remove(deferred_tag)
        if old_delayed_until:
            create_journal_entry(db, "Cleared Deferral", f"Cleared deferral for todo '{todo.title}'", "todo", todo.id)
    
    db.commit()
    return JSONResponse({"success": True, "delayed_until": str(todo.delayed_until) if todo.delayed_until else None})


@app.post("/api/todo/{todo_id}/set-alarm")
async def api_set_alarm_todo(todo_id: int, request: Request, db: Session = Depends(get_db)):
    """Set or clear alarm for a single todo (AJAX)"""
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        return JSONResponse({"error": "Todo not found"}, status_code=404)
    
    data = await request.json()
    alarm_mode = data.get("alarm_mode", "none")  # 'none', 'range', 'tolerance'
    
    new_alarm_start = None
    new_alarm_end = None
    
    if alarm_mode == 'range':
        alarm_start_range = data.get("alarm_start_range", "")
        alarm_end_range = data.get("alarm_end_range", "")
        if alarm_start_range and alarm_end_range:
            try:
                new_alarm_start = datetime.strptime(alarm_start_range, '%Y-%m-%dT%H:%M')
                new_alarm_end = datetime.strptime(alarm_end_range, '%Y-%m-%dT%H:%M')
            except ValueError:
                return JSONResponse({"error": "Invalid datetime format"}, status_code=400)
    elif alarm_mode == 'tolerance':
        alarm_target_time = data.get("alarm_target_time", "")
        alarm_minutes = int(data.get("alarm_minutes", 0))
        if alarm_target_time:
            try:
                target = datetime.strptime(alarm_target_time, '%Y-%m-%dT%H:%M')
                delta = timedelta(minutes=alarm_minutes)
                new_alarm_start = target - delta
                new_alarm_end = target + delta
            except ValueError:
                return JSONResponse({"error": "Invalid datetime format"}, status_code=400)
    
    if new_alarm_start != todo.alarm_start or new_alarm_end != todo.alarm_end:
        todo.alarm_start = new_alarm_start
        todo.alarm_end = new_alarm_end
        todo.modified_at = utcnow()
        if todo.list:
            todo.list.modified_at = utcnow()
        if new_alarm_start:
            create_journal_entry(db, "Set Alarm", f"Set alarm for todo '{todo.title}'", "todo", todo.id)
        else:
            create_journal_entry(db, "Cleared Alarm", f"Cleared alarm for todo '{todo.title}'", "todo", todo.id)
    
    db.commit()
    return JSONResponse({
        "success": True, 
        "alarm_start": new_alarm_start.strftime('%Y-%m-%dT%H:%M') if new_alarm_start else None,
        "alarm_end": new_alarm_end.strftime('%Y-%m-%dT%H:%M') if new_alarm_end else None
    })


@app.post("/api/todo/{todo_id}/update-hashtags")
async def api_update_hashtags_todo(todo_id: int, request: Request, db: Session = Depends(get_db)):
    """Update hashtags for a single todo (AJAX)"""
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        return JSONResponse({"error": "Todo not found"}, status_code=404)
    
    data = await request.json()
    hashtags_str = data.get("hashtags", "")
    
    # Clear and re-add hashtags
    todo.hashtags.clear()
    current_tags = parse_hashtags(hashtags_str)
    
    # Preserve #deferred tag if todo is deferred
    if todo.delayed_until and "deferred" not in current_tags:
        current_tags.append("deferred")
    
    for tag_name in current_tags:
        hashtag = get_or_create_hashtag(db, tag_name)
        todo.hashtags.append(hashtag)
    
    todo.modified_at = utcnow()
    if todo.list:
        todo.list.modified_at = utcnow()
    
    create_journal_entry(db, "Updated Hashtags", f"Updated hashtags for todo '{todo.title}'", "todo", todo.id)
    db.commit()
    
    return JSONResponse({
        "success": True, 
        "hashtags": [{"id": h.id, "name": h.name} for h in todo.hashtags]
    })

@app.post("/api/list/{list_id}/toggle-pinned")
def toggle_list_pinned(list_id: int, db: Session = Depends(get_db)):
    """Toggle pinned status for a list (AJAX)"""
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        return JSONResponse({"error": "List not found"}, status_code=404)

    todo_list.pinned = not todo_list.pinned
    todo_list.pinned_at = utcnow() if todo_list.pinned else None
    todo_list.modified_at = utcnow()
    create_journal_entry(db, "Toggled List Pinned", f"Toggled pinned for list '{todo_list.name}' to {todo_list.pinned}", "list", todo_list.id)
    db.commit()
    return JSONResponse({"pinned": todo_list.pinned})


# =============================================================================
# BULK OPERATIONS
# =============================================================================

@app.post("/api/bulk/delete")
async def bulk_delete(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    return delete_items(db, data.get("type"), data.get("ids", []))


@app.post("/api/bulk/pin")
async def bulk_pin(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    return pin_items(db, data.get("type"), data.get("ids", []), data.get("pin", True))


@app.post("/api/bulk/archive")
async def bulk_archive(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    return archive_items(db, data.get("type"), data.get("ids", []), bool(data.get("archived", True)))


# =============================================================================
# TAGGED ITEMS OPERATIONS (Cross-page move)
# =============================================================================

@app.post("/api/tagged/move-lists")
async def move_tagged_lists(request: Request, db: Session = Depends(get_db)):
    """Move multiple tagged lists to a folder."""
    data = await request.json()
    list_ids = data.get('list_ids', [])
    target_folder_id = data.get('target_folder_id')
    
    if not list_ids:
        return JSONResponse({"error": "No lists specified"}, status_code=400)
    
    # 0 means root (no folder)
    if target_folder_id == 0:
        target_folder_id = None
    
    moved_count = 0
    now = utcnow()
    
    for list_id in list_ids:
        try:
            lst = db.query(List).filter(List.id == int(list_id)).first()
            if lst:
                lst.folder_id = target_folder_id
                lst.modified_at = now
                create_journal_entry(db, "Moved List (Tagged)", f"Moved list '{lst.name}' to folder {target_folder_id or 'Root'}", "list", lst.id)
                moved_count += 1
        except Exception as e:
            print(f"Error moving list {list_id}: {e}")
    
    db.commit()
    return JSONResponse({"success": True, "moved_count": moved_count})


@app.post("/api/tagged/move-items")
async def move_tagged_items(request: Request, db: Session = Depends(get_db)):
    """Move multiple tagged todos and notes to a target list.
    
    When moving a todo, all its subtodos are moved with it (tree preserved).
    When moving a note attached to a todo, it stays attached if the todo is also moved,
    otherwise it becomes a list-level note.
    """
    try:
        data = await request.json()
    except ValueError:
        return JSONResponse({'error': 'Enter a valid move request.'}, status_code=400)
    lock_account(db)
    try:
        result = move_tagged_records(db, data)
        db.commit()
    except MoveError as error:
        db.rollback()
        return JSONResponse({'error': str(error)}, status_code=error.status)
    except Exception:
        db.rollback()
        raise
    return JSONResponse(result)


# =============================================================================
# HASHTAG OPERATIONS
# =============================================================================

@app.post("/api/hashtag/{hashtag_id}/toggle-show-completed")
def toggle_hashtag_show_completed(hashtag_id: int, db: Session = Depends(get_db)):
    """Toggle show_completed setting for a hashtag (AJAX)"""
    hashtag = db.query(Hashtag).filter(Hashtag.id == hashtag_id).first()
    if not hashtag:
        return JSONResponse({"error": "Hashtag not found"}, status_code=404)
    
    hashtag.show_completed = not hashtag.show_completed
    create_journal_entry(db, "Toggled Hashtag Show Completed", f"Toggled show completed for hashtag '#{hashtag.name}' to {hashtag.show_completed}", "hashtag", hashtag.id)
    db.commit()
    return JSONResponse({"show_completed": hashtag.show_completed})


@app.post("/hashtag/{hashtag_id}/delete")
def delete_hashtag(hashtag_id: int, db: Session = Depends(get_db)):
    """Delete a hashtag - removes from all items"""
    hashtag = db.query(Hashtag).filter(Hashtag.id == hashtag_id).first()
    if not hashtag:
        raise HTTPException(status_code=404, detail="Hashtag not found")
    
    create_journal_entry(db, "Deleted Hashtag", f"Deleted hashtag '#{hashtag.name}'", "hashtag", hashtag_id)
    db.delete(hashtag)
    db.commit()
    return RedirectResponse(url="/hashtags", status_code=303)


# =============================================================================
# NAMED SEARCH OPERATIONS
# =============================================================================

@app.post("/search/create")
async def create_named_search(
    request: Request,
    name: str = Form(...),
    uncompleted_only: bool = Form(False),
    db: Session = Depends(get_db)
):
    """Create a named search from selected hashtags"""
    # Get selected hashtag IDs from form
    form_data = await request.form()
    hashtag_ids = form_data.getlist('hashtag_ids')
    exclude_hashtag_ids = form_data.getlist('exclude_hashtag_ids')
    all_items = str(form_data.get('all_items') or '').strip().lower() in ("1", "true", "yes", "on")
    
    if not all_items and not hashtag_ids and not exclude_hashtag_ids:
        return RedirectResponse(url="/hashtags", status_code=303)

    include_ids = {int(x) for x in hashtag_ids if str(x).isdigit()}
    exclude_ids = {int(x) for x in exclude_hashtag_ids if str(x).isdigit()}

    # "All items" means: don't require/include any positive hashtag filters.
    if all_items:
        include_ids = set()
    # Exclude wins if the user checks both for the same tag.
    include_ids -= exclude_ids
    
    named_search = NamedSearch(name=name, uncompleted_only=uncompleted_only)
    db.add(named_search)
    db.flush()
    
    # Add hashtags to the search
    for hashtag_id in sorted(include_ids):
        hashtag = db.query(Hashtag).filter(Hashtag.id == hashtag_id).first()
        if hashtag:
            named_search.hashtags.append(hashtag)

    # Add excluded hashtags to the search
    for hashtag_id in sorted(exclude_ids):
        hashtag = db.query(Hashtag).filter(Hashtag.id == hashtag_id).first()
        if hashtag:
            named_search.excluded_hashtags.append(hashtag)
    
    create_journal_entry(db, "Created Named Search", f"Created named search '{name}'", "named_search", named_search.id)
    db.commit()
    return RedirectResponse(url="/hashtags", status_code=303)


@app.post("/search/{search_id}/delete")
def delete_named_search(search_id: int, db: Session = Depends(get_db)):
    """Delete a named search"""
    named_search = db.query(NamedSearch).filter(NamedSearch.id == search_id).first()
    if not named_search:
        raise HTTPException(status_code=404, detail="Named search not found")
    
    create_journal_entry(db, "Deleted Named Search", f"Deleted named search '{named_search.name}'", "named_search", search_id)
    db.delete(named_search)
    db.commit()
    return RedirectResponse(url="/hashtags", status_code=303)


# =============================================================================
# DOKUWIKI CLICK TRACKING
# =============================================================================

@app.post("/api/dokuwiki/click")
async def track_dokuwiki_click(request: Request, db: Session = Depends(get_db)):
    """Track clicks on DokuWiki links to estimate activity."""
    data = await request.json()
    item_type = data.get('type')
    item_id = data.get('id')
    list_id = data.get('list_id')
    todo_ids = data.get('todo_ids', [])
    
    # Use helper method for logic
    result = process_dokuwiki_click(db, item_type, int(item_id) if item_id else None, todo_ids)
    
    if "error" in result:
        return JSONResponse(result, status_code=400)
        
    return JSONResponse(result)


# =============================================================================
# SMART FOLDERS
# =============================================================================

# _parse_date_yyyy_mm_dd is imported from helpers.py


@app.get("/smart-folders", response_class=HTMLResponse)
def smart_folders_page(request: Request, db: Session = Depends(get_db)):
    folders = db.query(SmartFolder).order_by(SmartFolder.sort_order, SmartFolder.modified_at.desc()).all()
    hashtags = db.query(Hashtag).order_by(Hashtag.name).all()
    lists = (
        db.query(List)
        .filter(List.archived == False)
        .order_by(List.modified_at.desc())
        .all()
    )
    all_folders = db.query(Folder).order_by(Folder.name).all()
    return templates.TemplateResponse(request, "smart_folders.html", {
        "request": request,
        "folders": folders,
        "hashtags": hashtags,
        "lists": lists,
        "all_folders": all_folders,
    })


def save_smart_folder(db, form_data, folder=None):
    """Create or update a smart folder from submitted form data."""
    data = parse_smart_folder_form(form_data)

    hashtag_ids = form_data.getlist('hashtag_ids')
    exclude_hashtag_ids = form_data.getlist('exclude_hashtag_ids')
    list_ids = form_data.getlist('list_ids')

    # Process dynamic folder filters
    folder_ids = []
    exclude_folder_ids = []
    include_root_folder = False
    exclude_root_folder = False
    for mode, fid in zip(form_data.getlist('folder_filter_mode'), form_data.getlist('folder_filter_id')):
        if not fid:
            continue
        if fid == 'root':
            if mode == 'include':
                include_root_folder = True
            elif mode == 'exclude':
                exclude_root_folder = True
        elif str(fid).isdigit():
            if mode == 'include':
                folder_ids.append(int(fid))
            elif mode == 'exclude':
                exclude_folder_ids.append(int(fid))

    include_ids = {int(x) for x in hashtag_ids if str(x).isdigit()}
    exclude_ids = {int(x) for x in exclude_hashtag_ids if str(x).isdigit()}

    all_items = str(form_data.get('all_items') or '').strip().lower() in ("1", "true", "yes", "on")
    if all_items:
        include_ids = set()
    include_ids -= exclude_ids

    list_id_set = {int(x) for x in list_ids if str(x).isdigit()}

    # Default show_todos if nothing selected
    if not data['show_lists'] and not data['show_todos']:
        data['show_todos'] = True

    data['include_root_folder'] = include_root_folder
    data['exclude_root_folder'] = exclude_root_folder

    created = folder is None
    if created:
        folder = SmartFolder(**data)
        db.add(folder)
        db.flush()
    else:
        for k, v in data.items():
            setattr(folder, k, v)
        folder.modified_at = utcnow()

    update_smart_folder_hashtags(db, folder, form_data, sorted(include_ids), sorted(exclude_ids))
    update_smart_folder_collections(db, folder, sorted(list_id_set), sorted(folder_ids), sorted(exclude_folder_ids))

    if created:
        create_journal_entry(db, "Created Smart Folder", f"Created smart folder '{folder.name}'", "smart_folder", folder.id)
    else:
        create_journal_entry(db, "Updated Smart Folder", f"Updated smart folder '{folder.name}'", "smart_folder", folder.id)
    db.commit()
    return folder


@app.post("/smart-folder/create")
async def create_smart_folder(
    request: Request,
    db: Session = Depends(get_db),
):
    save_smart_folder(db, await request.form())
    return RedirectResponse(url="/smart-folders", status_code=303)


@app.get("/smart-folder/{folder_id}/edit", response_class=HTMLResponse)
def edit_smart_folder(request: Request, folder_id: int, db: Session = Depends(get_db)):
    folder = db.query(SmartFolder).filter(SmartFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Smart folder not found")

    hashtags = db.query(Hashtag).order_by(Hashtag.name).all()
    lists = (
        db.query(List)
        .filter(List.archived == False)
        .order_by(List.modified_at.desc())
        .all()
    )
    all_folders = db.query(Folder).order_by(Folder.name).all()

    folder_list_ids = {l.id for l in folder.lists}
    folder_include_ids = {a.hashtag_id for a in folder.include_hashtags_assoc}
    folder_exclude_ids = {a.hashtag_id for a in folder.exclude_hashtags_assoc}
    
    folder_include_subtodos_map = {a.hashtag_id: a.include_subtodos for a in folder.include_hashtags_assoc}
    folder_include_list_items_map = {a.hashtag_id: a.include_list_items for a in folder.include_hashtags_assoc}
    folder_exclude_subtodos_map = {a.hashtag_id: a.include_subtodos for a in folder.exclude_hashtags_assoc}

    folder_folder_ids = {f.id for f in folder.include_folders}
    folder_exclude_folder_ids = {f.id for f in folder.exclude_folders}

    modified_after_val = folder.modified_after.strftime('%Y-%m-%d') if folder.modified_after else ''
    modified_before_val = ''
    if folder.modified_before:
        # stored as exclusive next-midnight; present it as the inclusive date the user typed.
        modified_before_val = (folder.modified_before - timedelta(days=1)).strftime('%Y-%m-%d')

    return templates.TemplateResponse(request, "edit_smart_folder.html", {
        "request": request,
        "folder": folder,
        "hashtags": hashtags,
        "lists": lists,
        "all_folders": all_folders,
        "folder_list_ids": folder_list_ids,
        "folder_include_ids": folder_include_ids,
        "folder_exclude_ids": folder_exclude_ids,
        "folder_folder_ids": folder_folder_ids,
        "folder_exclude_folder_ids": folder_exclude_folder_ids,
        "folder_include_subtodos_map": folder_include_subtodos_map,
        "folder_include_list_items_map": folder_include_list_items_map,
        "folder_exclude_subtodos_map": folder_exclude_subtodos_map,
        "modified_after_val": modified_after_val,
        "modified_before_val": modified_before_val,
    })


@app.get("/api/data/smart-folder/{folder_id}/edit")
def api_edit_smart_folder_data(folder_id: int, db: Session = Depends(get_db)):
    folder = db.query(SmartFolder).filter(SmartFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Smart folder not found")

    hashtags = db.query(Hashtag).order_by(Hashtag.name).all()
    lists = (
        db.query(List)
        .filter(List.archived == False)
        .order_by(List.modified_at.desc())
        .all()
    )

    folder_list_ids = [l.id for l in folder.lists]
    folder_include_ids = [a.hashtag_id for a in folder.include_hashtags_assoc]
    folder_exclude_ids = [a.hashtag_id for a in folder.exclude_hashtags_assoc]
    
    folder_include_subtodos_map = {a.hashtag_id: a.include_subtodos for a in folder.include_hashtags_assoc}
    folder_exclude_subtodos_map = {a.hashtag_id: a.include_subtodos for a in folder.exclude_hashtags_assoc}

    folder_folder_ids = [f.id for f in folder.include_folders]
    folder_exclude_folder_ids = [f.id for f in folder.exclude_folders]

    modified_after_val = folder.modified_after.strftime('%Y-%m-%d') if folder.modified_after else ''
    modified_before_val = ''
    if folder.modified_before:
        # stored as exclusive next-midnight; present it as the inclusive date the user typed.
        modified_before_val = (folder.modified_before - timedelta(days=1)).strftime('%Y-%m-%d')

    def serialize_smart_folder_edit(sf):
        return {
            "id": sf.id,
            "name": sf.name,
            "show_lists": sf.show_lists,
            "show_todos": sf.show_todos,
            "group_todos_by_list": sf.group_todos_by_list,
            "root_todos_only": sf.root_todos_only,
            "nest_subtodos": sf.nest_subtodos,
            "uncompleted_only": sf.uncompleted_only,
            "include_archived_lists": sf.include_archived_lists,
            "dokuwiki_links_enabled": sf.dokuwiki_links_enabled,
            "dokuwiki_date_field": sf.dokuwiki_date_field,
            "list_sort_field": sf.list_sort_field,
            "list_sort_desc": sf.list_sort_desc,
            "todo_sort_field": sf.todo_sort_field,
            "todo_sort_desc": sf.todo_sort_desc,
        }

    return JSONResponse({
        "folder": serialize_smart_folder_edit(folder),
        "hashtags": [serialize_hashtag(h) for h in hashtags],
        "lists": [serialize_list(l) for l in lists],
        "folder_list_ids": folder_list_ids,
        "folder_include_ids": folder_include_ids,
        "folder_exclude_ids": folder_exclude_ids,
        "folder_include_subtodos_map": folder_include_subtodos_map,
        "folder_exclude_subtodos_map": folder_exclude_subtodos_map,
        "folder_folder_ids": folder_folder_ids,
        "folder_exclude_folder_ids": folder_exclude_folder_ids,
        "modified_after_val": modified_after_val,
        "modified_before_val": modified_before_val,
    })

@app.post("/smart-folder/{folder_id}/update")
async def update_smart_folder(
    request: Request,
    folder_id: int,
    db: Session = Depends(get_db),
):
    folder = db.query(SmartFolder).filter(SmartFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Smart folder not found")
    save_smart_folder(db, await request.form(), folder)
    return RedirectResponse(url="/smart-folders", status_code=303)


@app.post("/smart-folder/reorder", response_class=HTMLResponse)
def reorder_smart_folder(
    request: Request,
    source_id: int = Form(...),
    target_id: int = Form(...),
    db: Session = Depends(get_db)
):
    source = db.query(SmartFolder).filter(SmartFolder.id == source_id).first()
    target = db.query(SmartFolder).filter(SmartFolder.id == target_id).first()
    
    if not source or not target:
        return RedirectResponse(url="/smart-folders?error=not_found", status_code=303)
        
    if source.id == target.id:
        return RedirectResponse(url="/smart-folders", status_code=303)
        
    # Get all smart folders, ordered currently
    limit = 1000 # Safety
    folders = db.query(SmartFolder).order_by(SmartFolder.sort_order, SmartFolder.modified_at.desc()).limit(limit).all()
    
    # Create a new list order
    new_order = []
    
    # Add all except source
    for f in folders:
        if f.id != source.id:
            new_order.append(f)
            
    # Find index of target in new_order
    target_index = -1
    for i, f in enumerate(new_order):
        if f.id == target.id:
            target_index = i
            break
            
    if target_index != -1:
        # "moves the source folder to below the dest folder"
        new_order.insert(target_index + 1, source)
    else:
        # Fallback
        new_order.append(source)
        
    # Commit new sort orders (spacing by 10)
    for i, f in enumerate(new_order):
        f.sort_order = i * 10
        
    db.commit()
    
    return RedirectResponse(url="/smart-folders", status_code=303)


@app.post("/smart-folder/{folder_id}/delete")
def delete_smart_folder(folder_id: int, db: Session = Depends(get_db)):
    folder = db.query(SmartFolder).filter(SmartFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Smart folder not found")
    create_journal_entry(db, "Deleted Smart Folder", f"Deleted smart folder '{folder.name}'", "smart_folder", folder_id)
    db.delete(folder)
    db.commit()
    return RedirectResponse(url="/smart-folders", status_code=303)


@app.get("/journal", response_class=HTMLResponse)
def view_journal(request: Request, db: Session = Depends(get_db)):
    """View journal entries."""
    entries = db.query(JournalEntry).order_by(JournalEntry.created_at.desc()).limit(100).all()
    return templates.TemplateResponse(request, "journal.html", {
        "request": request,
        "entries": entries,
    })


@app.post("/api/link/create")
def create_link(
    url: str = Form(...),
    title: str = Form(""),
    list_id: Optional[int] = Form(None),
    todo_id: Optional[int] = Form(None),
    note_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    """Create a link for a list, todo, or note."""
    if not list_id and not todo_id and not note_id:
        raise HTTPException(status_code=400, detail="Must specify list_id, todo_id, or note_id")
    
    link = Link(url=url, title=title.strip() or None, list_id=list_id, todo_id=todo_id, note_id=note_id)
    db.add(link)
    db.flush()
    
    if note_id:
        entity_type = "note"
        entity_id = note_id
    elif list_id:
        entity_type = "list"
        entity_id = list_id
    else:
        entity_type = "todo"
        entity_id = todo_id
    
    # Update modification time
    if list_id:
        lst = db.query(List).filter(List.id == list_id).first()
        if lst: lst.modified_at = utcnow()
    elif todo_id:
        todo = db.query(Todo).filter(Todo.id == todo_id).first()
        if todo:
            todo.modified_at = utcnow()
            if todo.list: todo.list.modified_at = utcnow()
    elif note_id:
        note = db.query(Note).filter(Note.id == note_id).first()
        if note:
            note.modified_at = utcnow()
            if note.list: note.list.modified_at = utcnow()

    create_journal_entry(db, "Created Link", f"Created link '{link.url}'", entity_type, entity_id)
    db.commit()
    
    # Redirect back to edit page
    if list_id:
        return RedirectResponse(url=f"/list/{list_id}/edit", status_code=303)
    elif note_id:
        return RedirectResponse(url=f"/note/{note_id}/edit", status_code=303)
    else:
        return RedirectResponse(url=f"/todo/{todo_id}/edit", status_code=303)


@app.post("/api/link/{link_id}/delete")
def delete_link(link_id: int, db: Session = Depends(get_db)):
    """Delete a link."""
    link = db.query(Link).filter(Link.id == link_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    
    list_id = link.list_id
    todo_id = link.todo_id
    note_id = link.note_id
    
    if note_id:
        entity_type = "note"
        entity_id = note_id
    elif list_id:
        entity_type = "list"
        entity_id = list_id
    else:
        entity_type = "todo"
        entity_id = todo_id
    
    # Update modification time
    if list_id:
        lst = db.query(List).filter(List.id == list_id).first()
        if lst: lst.modified_at = utcnow()
    elif todo_id:
        todo = db.query(Todo).filter(Todo.id == todo_id).first()
        if todo:
            todo.modified_at = utcnow()
            if todo.list: todo.list.modified_at = utcnow()
    elif note_id:
        note = db.query(Note).filter(Note.id == note_id).first()
        if note:
            note.modified_at = utcnow()
            if note.list: note.list.modified_at = utcnow()

    create_journal_entry(db, "Deleted Link", f"Deleted link '{link.url}'", entity_type, entity_id)
    db.delete(link)
    db.commit()
    
    if list_id:
        return RedirectResponse(url=f"/list/{list_id}/edit", status_code=303)
    elif note_id:
        return RedirectResponse(url=f"/note/{note_id}/edit", status_code=303)
    else:
        return RedirectResponse(url=f"/todo/{todo_id}/edit", status_code=303)


def smart_folder_results(db, folder):
    """Return the lists, todos, tree and events a smart folder shows."""
    # Sort fields for template display
    list_sort_field = validate_sort_field(folder.list_sort_field)
    todo_sort_field = validate_sort_field(folder.todo_sort_field)
    todo_sort_desc = bool(folder.todo_sort_desc) if folder.todo_sort_desc is not None else True

    matching_lists = SmartFolderService.get_lists(db, folder)
    matching_todos, todo_tree = SmartFolderService.get_todos(db, folder)
    matching_events = SmartFolderService.get_calendar_events(db, folder)

    modified_after_display = folder.modified_after.strftime('%Y-%m-%d') if folder.modified_after else None
    modified_before_display = None
    if folder.modified_before:
        # stored as exclusive next-midnight; present it as the inclusive date the user typed.
        modified_before_display = (folder.modified_before - timedelta(days=1)).strftime('%Y-%m-%d')

    # Generate DokuWiki links if enabled (using helpers)
    if folder.dokuwiki_links_enabled:
        date_attr = folder.dokuwiki_date_field or 'created_at'
        confirmed_lists, confirmed_todos = get_confirmed_items(db)

        apply_dokuwiki_links(matching_lists, None, date_attr, confirmed_lists)

        if folder.group_todos_by_list and todo_tree:
            # todo_tree is a hierarchical dict: {'folders': {...}, 'lists': [...]}
            def apply_dokuwiki_to_tree_node(node):
                for grp in node.get('lists', []):
                    for t in grp['todos']:
                        apply_dokuwiki_links_recursive(t, date_attr, confirmed_todos)
                for child_node in node.get('folder_nodes', []):
                    apply_dokuwiki_to_tree_node(child_node)
            apply_dokuwiki_to_tree_node(todo_tree)
        else:
            for todo in matching_todos:
                apply_dokuwiki_links_recursive(todo, date_attr, confirmed_todos)

    matching_ids = None
    if folder.strict_nesting:
        matching_ids = {t.id for t in matching_todos}

    return {
        "lists": matching_lists,
        "todos": matching_todos,
        "todo_tree": todo_tree,
        "events": matching_events,
        "modified_after_display": modified_after_display,
        "modified_before_display": modified_before_display,
        "list_sort_attr": list_sort_field,
        "todo_sort_attr": todo_sort_field,
        "todo_sort_reverse": todo_sort_desc,
        "matching_ids": matching_ids,
    }


@app.get("/smart-folder/{folder_id}", response_class=HTMLResponse)
def view_smart_folder(request: Request, folder_id: int, db: Session = Depends(get_db)):
    folder = db.query(SmartFolder).filter(SmartFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Smart folder not found")
    results = smart_folder_results(db, folder)
    return templates.TemplateResponse(request, "smart_folder_results.html", {
        "request": request,
        "folder": folder,
        "lists": results["lists"],
        "todos": results["todos"],
        "todo_tree": results["todo_tree"],
        "modified_after_display": results["modified_after_display"],
        "modified_before_display": results["modified_before_display"],
        "todo_sort_attr": results["todo_sort_attr"],
        "todo_sort_reverse": results["todo_sort_reverse"],
        "events": results["events"],
        "matching_ids": results["matching_ids"],
        "dokuwiki_item_link_fn": generate_dokuwiki_item_link,
    })
@app.post("/api/dokuwiki/unconfirm")
async def untrack_dokuwiki_click(request: Request, db: Session = Depends(get_db)):
    """Remove confirmation for DokuWiki links."""
    data = await request.json()
    item_type = data.get('type')
    item_id = data.get('id')
    todo_ids = data.get('todo_ids', [])
    
    if not item_type:
        return JSONResponse({"error": "Missing type"}, status_code=400)

    def remove_confirmation(item_type, item_id):
        record = db.query(DokuWikiConfirmedItem).filter(
            DokuWikiConfirmedItem.item_type == item_type,
            DokuWikiConfirmedItem.item_id == item_id
        ).first()
        if record:
            db.delete(record)

    if item_type == 'header_group':
        if not todo_ids:
             return JSONResponse({"error": "No todos in group"}, status_code=400)
        for tid in todo_ids:
            remove_confirmation('todo', tid)
            
    elif item_type in ('todo', 'list'):
        remove_confirmation(item_type, int(item_id))
        
    db.commit()
    return JSONResponse({"success": True})


# =============================================================================
# SERIALIZATION HELPERS
# =============================================================================

# All serialize_* functions (serialize_list, serialize_todo, serialize_folder,
# serialize_hashtag, serialize_calendar_event, serialize_smart_folder,
# serialize_named_search, serialize_header) are imported from serializers.py



# =============================================================================
# SPA API ENDPOINTS
# =============================================================================

@app.get("/api/data/home")
def api_home_data(request: Request, sort: str = "user", db: Session = Depends(get_db)):
    """Return data for the home page."""
    """Return data for the home page."""
    settings = get_user_settings(db)
    
    query = db.query(List).filter(List.archived == False)
    
    if sort == "created":
        lists = query.order_by(List.created_at.desc()).all()
    elif sort == "user":
        lists = query.order_by(List.sort_order.asc(), List.created_at.desc()).all()
    else:
        lists = query.order_by(List.modified_at.desc()).all()

    pinned_todos = db.query(Todo).filter(Todo.pinned == True).order_by(Todo.modified_at.desc()).all()
    pinned_lists = db.query(List).filter(List.archived == False, List.pinned == True).order_by(List.modified_at.desc()).all()
    all_hashtags = db.query(Hashtag).order_by(Hashtag.name).all()

    # Use helpers
    uncompleted_counts = get_uncompleted_counts(db, include_collected=COLLATION_UNCOMPLETED_COUNT_INCLUDES_COLLECTED)

    home_events = db.query(CalendarEvent).order_by(CalendarEvent.start_at.asc()).all()
    home_occurrences = get_home_calendar_occurrences(db, home_events, HOME_CALENDAR_AROUND_DAYS)
    
    # Calculate threshold (mirroring home logic)
    threshold = utcnow() - timedelta(days=getattr(settings, 'pinned_old_days', None) or PINNED_OLD_DAYS)

    pinned_todos_old, pinned_todos_recent = separate_pinned_items(pinned_todos, threshold)
    pinned_lists_old, pinned_lists_recent = separate_pinned_items(pinned_lists, threshold)

    # Enrich todos with list info for context
    def enrich_todo(t):
        d = serialize_todo(t)
        if t.list:
            d['list'] = {"id": t.list.id, "name": t.list.name}
        if t.parent:
            d['parent'] = {"id": t.parent.id, "title": t.parent.title}
        # Hashtags are IDs, client needs to map them or we include objects
        # serialize_todo returns IDs. We send all_hashtags so client can map.
        return d

    home_now = _midnight(utcnow())
    home_cal_start = home_now - timedelta(days=HOME_CALENDAR_AROUND_DAYS)
    home_cal_end = home_now + timedelta(days=HOME_CALENDAR_AROUND_DAYS)

    today_calendar_occurrences = []
    if SHOW_TODAY_CALENDAR_ABOVE_PINNED:
        today_date = home_now.date()
        today_calendar_occurrences = [
            occ for occ in home_occurrences
            if occ['occurrence_at'].date() == today_date
        ]

        if PAST_UNCOMPLETED_CALENDAR_DAYS > 0:
            past_occurrences = get_home_calendar_occurrences(db, home_events, PAST_UNCOMPLETED_CALENDAR_DAYS)
            past_start_date = today_date - timedelta(days=PAST_UNCOMPLETED_CALENDAR_DAYS)
            
            for occ in past_occurrences:
                occ_date = occ['occurrence_at'].date()
                if past_start_date <= occ_date < today_date and not occ['completed']:
                    today_calendar_occurrences.append(occ)

            today_calendar_occurrences.sort(key=lambda x: x['occurrence_at'])

    return JSONResponse({
        "lists": [serialize_list(l) for l in lists],
        "pinned_todos_recent": [enrich_todo(t) for t in pinned_todos_recent],
        "pinned_todos_old": [enrich_todo(t) for t in pinned_todos_old],
        "pinned_lists_recent": [serialize_list(l) for l in pinned_lists_recent],
        "pinned_lists_old": [serialize_list(l) for l in pinned_lists_old],
        "all_hashtags": [serialize_hashtag(h) for h in all_hashtags],
        "uncompleted_counts": uncompleted_counts,
        "home_calendar_occurrences": home_occurrences,
        "today_calendar_occurrences": today_calendar_occurrences,
        "show_today_calendar_above_pinned": SHOW_TODAY_CALENDAR_ABOVE_PINNED,
        "home_calendar_days": HOME_CALENDAR_AROUND_DAYS,
        "home_calendar_start": home_cal_start.isoformat(),
        "home_calendar_end": home_cal_end.isoformat(),
        "pinned_old_days": getattr(settings, 'pinned_old_days', None) or PINNED_OLD_DAYS,
        "sort": sort,
    })

@app.get("/api/data/list/{list_id}")
def api_list_data(list_id: int, db: Session = Depends(get_db)):
    """Return data for a specific list."""
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        raise HTTPException(status_code=404, detail="List not found")

    # Navigation logic (simplified for API)
    nav_lists = (
        db.query(List)
        .filter(List.archived == False, List.completed == False)
        .order_by(List.modified_at.desc())
        .all()
    )
    nav_ids = [l.id for l in nav_lists]
    
    todos_query = db.query(Todo).filter(Todo.list_id == list_id, Todo.parent_id == None)
    if not todo_list.show_completed:
        todos_query = todos_query.filter(Todo.completed == False)
    
    if todo_list.sort_ascending:
        todos = todos_query.order_by(Todo.created_at.asc()).all()
    else:
        todos = todos_query.order_by(Todo.created_at.desc()).all()

    datetime_headers = (
        db.query(ListDatetimeHeader)
        .filter(ListDatetimeHeader.list_id == list_id)
        .order_by(ListDatetimeHeader.created_at.desc())
        .all()
    )

    # We need to serialize todos recursively to include children
    def serialize_todo_recursive(t):
        d = serialize_todo(t)
        d['children'] = [serialize_todo_recursive(child) for child in t.children]
        # Sort children by created_at desc (default)
        d['children'].sort(key=lambda x: x['created_at'] or '', reverse=True)
        return d

    serialized_todos = [serialize_todo_recursive(t) for t in todos]
    
    # Headers
    # Pre-fetch confirmed items for this list's todos to determine header confirmation status
    # Note: In view_list, we check if ANY todo in the section is confirmed.
    all_todo_ids = [t.id for t in todos]
    confirmed_todo_ids = []
    confirmed_set = set()
    if all_todo_ids:
        confirmed_rows = db.query(DokuWikiConfirmedItem.item_id).filter(
            DokuWikiConfirmedItem.item_type == 'todo',
            DokuWikiConfirmedItem.item_id.in_(all_todo_ids)
        ).all()
        confirmed_todo_ids = [r[0] for r in confirmed_rows]
        confirmed_set = set(confirmed_todo_ids)

    # Add DokuWiki links to serialized todos (needed for synthetic headers in SPA)
    if ENABLE_DOKUWIKI_LINKS:
        def add_dokuwiki_links(todo_dict):
            # Parse created_at back to datetime or use original object if we had it.
            # But we only have dict here.
            dt_str = todo_dict.get('created_at')
            dt = datetime.fromisoformat(dt_str) if dt_str else None
            todo_dict['dokuwiki_link'] = generate_dokuwiki_link(dt)
            todo_dict['dokuwiki_confirmed'] = (todo_dict['id'] in confirmed_set)
            
            for child in todo_dict.get('children', []):
                add_dokuwiki_links(child)
        
        for t_dict in serialized_todos:
            add_dokuwiki_links(t_dict)

    serialized_headers = [{
        "id": h.id,
        "created_at": h.created_at.isoformat(),
        "heading": h.created_at_heading,
        "dokuwiki_link": generate_dokuwiki_link(h.created_at)
    } for h in datetime_headers]

    all_hashtags = db.query(Hashtag).order_by(Hashtag.name).all()
    
    collation_lists = db.query(List).filter(List.archived == False, List.is_collation == True).order_by(List.name.asc()).all()
    collected_todos = []
    if todo_list.is_collation:
        collected = list(todo_list.collated_todos)
        if not todo_list.show_completed:
            collected = [t for t in collected if not t.completed]
        collected.sort(key=lambda t: t.modified_at, reverse=True)
        collected_todos = [serialize_todo(t) for t in collected]

    return JSONResponse({
        "list": serialize_list(todo_list),
        "todos": serialized_todos,
        "datetime_headers": serialized_headers,
        "all_hashtags": [serialize_hashtag(h) for h in all_hashtags],
        "collation_lists": [serialize_list(l) for l in collation_lists],
        "collected_todos": collected_todos,
        "nav_ids": nav_ids,
        "has_datetime_headers": bool(datetime_headers),
        "confirmed_todo_ids": confirmed_todo_ids,
    })

@app.get("/api/data/archived")
def api_archived_data(db: Session = Depends(get_db)):
    lists = db.query(List).filter(List.archived == True).order_by(List.modified_at.desc()).all()
    all_hashtags = db.query(Hashtag).order_by(Hashtag.name).all()
    return JSONResponse({
        "lists": [serialize_list(l) for l in lists],
        "all_hashtags": [serialize_hashtag(h) for h in all_hashtags]
    })

@app.get("/api/data/uncompleted")
def api_uncompleted_data(db: Session = Depends(get_db)):
    lists_with_uncompleted = db.query(List).join(List.todos).filter(
        List.archived == False,
        Todo.completed == False,
        Todo.parent_id == None
    ).distinct().order_by(List.modified_at.desc()).all()

    if SHOW_COLLATION_COLLECTED_IN_UNCOMPLETED:
        collation_lists_with_uncompleted_collected = (
            db.query(List)
            .join(List.collated_todos)
            .filter(
                List.archived == False,
                List.is_collation == True,
                Todo.completed == False,
                Todo.parent_id == None,
            )
            .distinct()
            .all()
        )
        by_id = {lst.id: lst for lst in lists_with_uncompleted}
        for lst in collation_lists_with_uncompleted_collected:
            by_id.setdefault(lst.id, lst)
        lists_with_uncompleted = sorted(
            by_id.values(),
            key=lambda l: (l.modified_at or datetime.min),
            reverse=True,
        )
    
    lists_data = []
    for lst in lists_with_uncompleted:
        uncompleted_todos = [t for t in lst.todos if not t.completed and t.parent_id is None]
        uncompleted_todos.sort(key=lambda x: x.modified_at, reverse=True)

        collected_todos = []
        if SHOW_COLLATION_COLLECTED_IN_UNCOMPLETED and lst.is_collation:
            collected_todos = [t for t in lst.collated_todos if not t.completed and t.parent_id is None]
            collected_todos.sort(key=lambda x: x.modified_at, reverse=True)

        lists_data.append({
            'list': serialize_list(lst),
            'todos': [serialize_todo(t) for t in uncompleted_todos],
            'collected_todos': [serialize_todo(t) for t in collected_todos],
        })
    
    return JSONResponse({
        "lists_data": lists_data
    })

@app.get("/api/data/smart-folders")
def api_smart_folders_data(db: Session = Depends(get_db)):
    folders = db.query(SmartFolder).order_by(SmartFolder.modified_at.desc()).all()
    hashtags = db.query(Hashtag).order_by(Hashtag.name).all()
    lists = db.query(List).filter(List.archived == False).order_by(List.modified_at.desc()).all()
    
    def serialize_smart_folder(sf):
        return {
            "id": sf.id,
            "name": sf.name,
            "show_lists": sf.show_lists,
            "show_todos": sf.show_todos,
            "group_todos_by_list": sf.group_todos_by_list,
            "root_todos_only": sf.root_todos_only,
            "nest_subtodos": sf.nest_subtodos,
            "uncompleted_only": sf.uncompleted_only,
            "include_archived_lists": sf.include_archived_lists,
            "modified_after": sf.modified_after.isoformat() if sf.modified_after else None,
            "modified_before": sf.modified_before.isoformat() if sf.modified_before else None,
            "modified_at": sf.modified_at.isoformat() if sf.modified_at else None,
            "list_sort_field": sf.list_sort_field,
            "list_sort_desc": sf.list_sort_desc,
            "todo_sort_field": sf.todo_sort_field,
            "todo_sort_desc": sf.todo_sort_desc,
            "dokuwiki_links_enabled": sf.dokuwiki_links_enabled,
            "dokuwiki_date_field": sf.dokuwiki_date_field,
            "include_hashtags": [{"id": a.hashtag.id, "name": a.hashtag.name, "include_subtodos": a.include_subtodos} for a in sf.include_hashtags_assoc if a.hashtag],
            "exclude_hashtags": [{"id": a.hashtag.id, "name": a.hashtag.name, "include_subtodos": a.include_subtodos} for a in sf.exclude_hashtags_assoc if a.hashtag],
            "include_hashtags_assoc": [{"id": a.hashtag.id, "name": a.hashtag.name, "include_subtodos": a.include_subtodos} for a in sf.include_hashtags_assoc if a.hashtag],
            "exclude_hashtags_assoc": [{"id": a.hashtag.id, "name": a.hashtag.name, "include_subtodos": a.include_subtodos} for a in sf.exclude_hashtags_assoc if a.hashtag],
            "list_ids": [l.id for l in sf.lists]
        }

    return JSONResponse({
        "folders": [serialize_smart_folder(sf) for sf in folders],
        "hashtags": [serialize_hashtag(h) for h in hashtags],
        "lists": [serialize_list(l) for l in lists],
    })

@app.get("/api/data/smart-folder/{folder_id}")
def api_smart_folder_view_data(folder_id: int, db: Session = Depends(get_db)):
    folder = db.query(SmartFolder).filter(SmartFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Smart folder not found")

    # Sort fields needed for serialization (recursive sort)
    todo_sort_field = validate_sort_field(folder.todo_sort_field)
    todo_sort_desc = bool(folder.todo_sort_desc) if folder.todo_sort_desc is not None else True

    # 1. Get ORM objects from Service
    matching_lists = SmartFolderService.get_lists(db, folder)
    matching_todos_orm, todo_groups_orm = SmartFolderService.get_todos(db, folder)
    
    # 2. Serialize Todos
    def serialize_todo_recursive(t):
        d = serialize_todo(t)
        d['children'] = [serialize_todo_recursive(child) for child in t.children]
        
        def get_sort_val(x):
            val = x.get(todo_sort_field)
            return val or ''
        d['children'].sort(key=get_sort_val, reverse=todo_sort_desc)
        return d

    matching_todos = [serialize_todo_recursive(t) for t in matching_todos_orm]
    
    todo_groups = []
    if folder.group_todos_by_list:
        for grp in todo_groups_orm:
            todo_groups.append({
                "list": serialize_list(grp['list']),
                "todos": [serialize_todo_recursive(t) for t in grp['todos']]
            })

    modified_after_display = folder.modified_after.strftime('%Y-%m-%d') if folder.modified_after else None
    modified_before_display = None
    if folder.modified_before:
        modified_before_display = (folder.modified_before - timedelta(days=1)).strftime('%Y-%m-%d')

    # DokuWiki links logic
    if folder.dokuwiki_links_enabled:
        date_attr = folder.dokuwiki_date_field or 'created_at'
        confirmed_lists = set()
        confirmed_todos = set()
        all_confirmed = db.query(DokuWikiConfirmedItem).all()
        for item in all_confirmed:
            if item.item_type == 'list':
                confirmed_lists.add(item.item_id)
            elif item.item_type == 'todo':
                confirmed_todos.add(item.item_id)

        for lst in matching_lists:
            dt = getattr(lst, date_attr, None)
            lst.dokuwiki_link = generate_dokuwiki_link(dt)
            lst.dokuwiki_confirmed = (lst.id in confirmed_lists)
        
        # For todos, we need to apply this to serialized objects since we already serialized them above
        # Wait, I serialized them above. I should have done this before serialization or update serialized dicts.
        # Updating serialized dicts is easier now.
        
        def update_dokuwiki(todo_dict):
            # We need the original object to get the date... or we can parse the date string from dict.
            # Let's use the date string from dict.
            dt_str = todo_dict.get(date_attr) # 'created_at' or 'modified_at' (iso strings)
            dt = datetime.fromisoformat(dt_str) if dt_str else None
            # Note: fromisoformat might fail if None.
            
            todo_dict['dokuwiki_link'] = generate_dokuwiki_link(dt)
            todo_dict['dokuwiki_confirmed'] = (todo_dict['id'] in confirmed_todos)
            
            for child in todo_dict.get('children', []):
                update_dokuwiki(child)

        # Apply to both groups and flat list (since they are separate serialized objects now)
        if folder.group_todos_by_list:
            for grp in todo_groups:
                for t in grp['todos']:
                    update_dokuwiki(t)
        
        for t in matching_todos:
            update_dokuwiki(t)

    # Serialize folder
    def serialize_smart_folder_full(sf):
        return {
            "id": sf.id,
            "name": sf.name,
            "show_lists": sf.show_lists,
            "show_todos": sf.show_todos,
            "group_todos_by_list": sf.group_todos_by_list,
            "root_todos_only": sf.root_todos_only,
            "nest_subtodos": sf.nest_subtodos,
            "uncompleted_only": sf.uncompleted_only,
            "include_archived_lists": sf.include_archived_lists,
            "include_hashtags_assoc": [{"hashtag": {"id": a.hashtag.id, "name": a.hashtag.name}, "include_subtodos": a.include_subtodos} for a in sf.include_hashtags_assoc],
            "exclude_hashtags_assoc": [{"hashtag": {"id": a.hashtag.id, "name": a.hashtag.name}, "include_subtodos": a.include_subtodos} for a in sf.exclude_hashtags_assoc],
            "lists": [{"id": l.id, "name": l.name} for l in sf.lists]
        }

    # Serialize lists (matching_lists is list of objects)
    # We need to add dokuwiki props if enabled
    serialized_lists = []
    for l in matching_lists:
        d = serialize_list(l)
        if folder.dokuwiki_links_enabled:
            d['dokuwiki_link'] = getattr(l, 'dokuwiki_link', None)
            d['dokuwiki_confirmed'] = getattr(l, 'dokuwiki_confirmed', False)
        serialized_lists.append(d)

    return JSONResponse({
        "folder": serialize_smart_folder_full(folder),
        "lists": serialized_lists,
        "todos": matching_todos,
        "todo_groups": todo_groups,
        "modified_after_display": modified_after_display,
        "modified_before_display": modified_before_display,
        "todo_sort_attr": todo_sort_field,
        "todo_sort_reverse": todo_sort_desc,
    })

@app.get("/api/data/hashtags")
def api_hashtags_data(db: Session = Depends(get_db)):
    hashtags = db.query(Hashtag).order_by(Hashtag.name).all()
    named_searches = db.query(NamedSearch).order_by(NamedSearch.name).all()
    
    def serialize_named_search(ns):
        return {
            "id": ns.id,
            "name": ns.name,
            "uncompleted_only": ns.uncompleted_only,
            "hashtag_ids": [h.id for h in ns.hashtags],
            "excluded_hashtag_ids": [h.id for h in ns.excluded_hashtags]
        }

    return JSONResponse({
        "hashtags": [serialize_hashtag(h) for h in hashtags],
        "named_searches": [serialize_named_search(ns) for ns in named_searches]
    })

@app.get("/api/data/hashtag/{hashtag_id}")
def api_hashtag_view_data(hashtag_id: int, db: Session = Depends(get_db)):
    hashtag = db.query(Hashtag).filter(Hashtag.id == hashtag_id).first()
    if not hashtag:
        raise HTTPException(status_code=404, detail="Hashtag not found")
    
    if hashtag.show_completed:
        lists = sorted(hashtag.lists, key=lambda x: x.modified_at, reverse=True)
        todos = sorted(hashtag.todos, key=lambda x: x.modified_at, reverse=True)
    else:
        lists = sorted([l for l in hashtag.lists if not l.completed], key=lambda x: x.modified_at, reverse=True)
        todos = sorted([t for t in hashtag.todos if not t.completed], key=lambda x: x.modified_at, reverse=True)
    
    return JSONResponse({
        "hashtag": serialize_hashtag(hashtag),
        "lists": [serialize_list(l) for l in lists],
        "todos": [serialize_todo(t) for t in todos]
    })

@app.get("/api/data/named-search/{search_id}")
def api_named_search_view_data(search_id: int, db: Session = Depends(get_db)):
    named_search = db.query(NamedSearch).filter(NamedSearch.id == search_id).first()
    if not named_search:
        raise HTTPException(status_code=404, detail="Named search not found")
    
    include_ids = [h.id for h in named_search.hashtags]
    exclude_ids = [h.id for h in getattr(named_search, "excluded_hashtags", [])]

    lists_query = db.query(List)
    todos_query = db.query(Todo)
    if include_ids:
        lists_query = lists_query.join(List.hashtags).filter(Hashtag.id.in_(include_ids))
        todos_query = todos_query.join(Todo.hashtags).filter(Hashtag.id.in_(include_ids))
    if exclude_ids:
        lists_query = lists_query.filter(~List.hashtags.any(Hashtag.id.in_(exclude_ids)))
        todos_query = todos_query.filter(~Todo.hashtags.any(Hashtag.id.in_(exclude_ids)))
    
    if named_search.uncompleted_only:
        lists_query = lists_query.filter(List.completed == False)
        todos_query = todos_query.filter(Todo.completed == False)
    
    matching_lists = lists_query.distinct().all()
    matching_todos = todos_query.distinct().all()
    
    def serialize_named_search_full(ns):
        return {
            "id": ns.id,
            "name": ns.name,
            "uncompleted_only": ns.uncompleted_only,
            "hashtags": [serialize_hashtag(h) for h in ns.hashtags],
            "excluded_hashtags": [serialize_hashtag(h) for h in ns.excluded_hashtags]
        }

    return JSONResponse({
        "named_search": serialize_named_search_full(named_search),
        "lists": [serialize_list(l) for l in matching_lists],
        "todos": [serialize_todo(t) for t in matching_todos]
    })

@app.get("/api/data/list/{list_id}/edit")
def api_edit_list_data(list_id: int, db: Session = Depends(get_db)):
    todo_list = db.query(List).filter(List.id == list_id).first()
    if not todo_list:
        raise HTTPException(status_code=404, detail="List not found")
    
    all_hashtags = db.query(Hashtag).order_by(Hashtag.name).all()
    return JSONResponse({
        "list": serialize_list(todo_list),
        "all_hashtags": [serialize_hashtag(h) for h in all_hashtags]
    })

@app.get("/api/data/todo/{todo_id}/edit")
def api_edit_todo_data(todo_id: int, db: Session = Depends(get_db)):
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    
    all_hashtags = db.query(Hashtag).order_by(Hashtag.name).all()
    return JSONResponse({
        "todo": serialize_todo(todo),
        "all_hashtags": [serialize_hashtag(h) for h in all_hashtags]
    })

@app.get("/api/calendar/events")
def calendar_events_api(start_date: str, end_date: str, db: Session = Depends(get_db)):
    """Return calendar occurrences starting in an inclusive date range."""
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_date):
            raise ValueError
        start_day = date_type.fromisoformat(start_date)
        end_day = date_type.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(422, "Dates must be valid YYYY-MM-DD calendar dates.")
    if end_day < start_day:
        raise HTTPException(422, "end_date must not be before start_date.")
    if (end_day - start_day).days >= 366:
        raise HTTPException(422, "The date range must not exceed 366 days.")

    start = datetime.combine(start_day, datetime.min.time())
    end = datetime.combine(end_day, datetime.max.time())
    events = db.query(CalendarEvent).filter(CalendarEvent.start_at <= end).order_by(CalendarEvent.id).all()
    completed = {
        (row.event_id, row.occurrence_at)
        for row in db.query(CalendarEventCompletion).filter(
            CalendarEventCompletion.occurrence_at >= start,
            CalendarEventCompletion.occurrence_at <= end,
        ).all()
    }
    limit = 10000
    occurrences = []
    today = date_type.today()
    for event in events:
        try:
            dates = calendar_occurrences(event, start, end, limit - len(occurrences) + 1, strict=True)
        except ValueError as error:
            raise HTTPException(500, str(error)) from error
        if len(occurrences) + len(dates) > limit:
            raise HTTPException(422, "The date range contains more than 10000 occurrences; request a smaller range.")
        event_data = serialize_calendar_event(event)
        for occurrence in dates:
            is_completed = (event.id, occurrence) in completed
            is_late = (occurrence.date() < today) and not is_completed
            is_important = is_late and bool(event.important_if_late)
            occurrences.append({
                "event": event_data,
                "occurrence_at": occurrence.isoformat(),
                "completed": is_completed,
                "is_late": is_late,
                "is_important": is_important,
            })
    occurrences.sort(key=lambda item: (item["occurrence_at"], item["event"]["id"]))
    return JSONResponse({
        "start_date": start_date, "end_date": end_date, "occurrences": occurrences,
    }, headers={"Cache-Control": "no-store"})


@app.get("/api/data/calendar")
def api_calendar_data(db: Session = Depends(get_db)):
    now = _midnight(utcnow())
    today = now.date()
    start = now - timedelta(days=7)
    end = now + timedelta(days=CALENDAR_LOOKAHEAD_DAYS + 1) - timedelta(microseconds=1)

    events = db.query(CalendarEvent).order_by(CalendarEvent.start_at.asc()).all()
    completion_rows = db.query(CalendarEventCompletion).all()
    completed_set = {(c.event_id, c.occurrence_at) for c in completion_rows}

    occurrences = []
    for ev in events:
        for occ_at in _event_occurrences(ev, start, end):
            is_completed = (ev.id, occ_at) in completed_set
            is_late = (occ_at.date() < today) and not is_completed
            is_important = is_late and bool(ev.important_if_late)
            occurrences.append({
                "event": serialize_calendar_event(ev),
                "occurrence_at": occ_at.isoformat(),
                "completed": is_completed,
                "is_late": is_late,
                "is_important": is_important,
            })

    occurrences.sort(key=lambda x: x["occurrence_at"])

    return JSONResponse({
        "occurrences": occurrences,
        "now": now.isoformat(),
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
    })

@app.get("/api/data/collations")
def api_collations_data(db: Session = Depends(get_db)):
    collation_lists = db.query(List).filter(List.archived == False, List.is_collation == True).order_by(List.name.asc()).all()
    return JSONResponse({
        "collation_lists": [serialize_list(l) for l in collation_lists]
    })

@app.get("/api/data/search")
def api_search_data(q: str = "", db: Session = Depends(get_db)):
    from search_index import search_terms

    q = q.strip()
    lists = []
    todos = []

    if search_terms(q):
        lists, todos, _ = find_search_matches(db, q)

    def enrich_todo(t):
        d = serialize_todo(t)
        if t.list:
            d['list'] = {"id": t.list.id, "name": t.list.name}
        return d

    return JSONResponse({
        "q": q,
        "lists": [serialize_list(l) for l in lists],
        "todos": [enrich_todo(t) for t in todos],
    })

# =============================================================================
# OFFLINE SYNC ENDPOINTS (Merkle Tree)
# =============================================================================

from merkle_sync import build_merkle_tree, compute_diff, extract_hashes

@app.get("/api/sync")
def sync_full(request: Request, db: Session = Depends(get_db)):
    """
    Return full Merkle tree for initial sync (when client has no cache).
    
    Response format:
    {
        "root": "abc123",
        "children": {
            "data": {"hash": "...", "children": {...}},
            "templates": {"hash": "...", "children": {...}}
        }
    }
    """
    template_dir = BASE_DIR / "templates"
    tree = build_merkle_tree(db, template_dir)
    return JSONResponse(content=tree)


@app.post("/api/sync")
async def sync_incremental(request: Request, db: Session = Depends(get_db)):
    """
    Incremental sync using Merkle tree comparison.
    
    Client sends its current hashes:
    {
        "root": "abc123",
        "data": "def456",
        "templates": "ghi789",
        "data_children": {"lists": "...", "todos": "...", ...},
        "templates_children": {"index.html": "...", ...}
    }
    
    Response:
    - 204 No Content if root hash matches (nothing changed)
    - Partial tree with only changed subtrees otherwise
    """
    # Read body properly using await
    body = await request.body()
    
    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        data = {}
    
    # Determine request format (Legacy vs New w/ modifications)
    is_legacy = "client_hashes" not in data and "modifications" not in data
    
    if is_legacy:
        client_hashes = data
        modifications = {}
    else:
        client_hashes = data.get("client_hashes", {})
        modifications = data.get("modifications", {})
        
    # Apply modifications if any (Write)
    id_map = {}
    if modifications:
        id_map = apply_modifications(db, modifications)

    # Build server tree
    template_dir = BASE_DIR / "templates"
    server_tree = build_merkle_tree(db, template_dir)
    
    # If client has no hashes, return full tree
    if not client_hashes or not client_hashes.get("root"):
        if is_legacy:
            return JSONResponse(content=server_tree)
        return JSONResponse(content={"diff": server_tree, "id_map": id_map})
    
    # Compute diff
    diff = compute_diff(client_hashes, server_tree)
    
    if diff is None:
        # Nothing changed in the tree
        if is_legacy:
            return Response(status_code=204)
        return JSONResponse(content={"diff": None, "id_map": id_map})
    
    if is_legacy:
        return JSONResponse(content=diff)
        
    return JSONResponse(content={"diff": diff, "id_map": id_map})



# Keep the experimental HTML/HTMX frontend isolated from the existing routes.
from daisyui_test import create_router as create_daisyui_test_router
from grouping_service import organize_todos_into_tree
app.include_router(create_daisyui_test_router(
    templates=templates, get_db=get_db, home_context=get_home_context,
    create_list=create_list, toggle_list=toggle_list, toggle_todo=toggle_todo,
    list_context=get_list_context,
    list_actions={
        "create_todo": create_todo, "create_note": create_note,
        "divider": create_datetime_header, "category": update_list_category,
        "show_completed": toggle_show_completed, "show_delayed": toggle_show_delayed,
        "is_collation": toggle_list_collation, "manual_sorting": toggle_manual_sort,
        "is_recipe": toggle_recipe_mode, "show_dokuwiki_item_links": toggle_show_dokuwiki_item_links,
        "dokuwiki_page_enabled": toggle_list_dokuwiki_page,
        "sort_ascending": toggle_list_sort_order, "partition_by_datetime_headers": toggle_list_header_partitioning,
        "intersperse_collated_todos": toggle_list_intersperse_collated,
        "collation": toggle_todo_in_collation, "reorder": reorder_todos,
        "defer": api_defer_todo, "alarm": api_set_alarm_todo, "hashtags": api_update_hashtags_todo,
        "wiki_pages": bulk_toggle_dokuwiki_page, "wiki_confirm": track_dokuwiki_click,
        "wiki_unconfirm": untrack_dokuwiki_click,
        "ingredients": bulk_ingredient_event, "reset_ingredients": reset_all_ingredients,
    },
    edit_actions={
        "list": update_list, "todo": update_todo, "note": edit_note,
        "delete_list": delete_list, "delete_todo": delete_todo, "delete_note": delete_note,
        "create_link": create_link, "delete_link": delete_link,
    },
    search_actions={
        "search": find_search_results, "hashtag": find_hashtag_results,
        "load_hashtag": load_hashtag_for_results, "journal": create_journal_entry,
        "uncompleted_counts": lambda db: get_uncompleted_counts(db, include_collected=COLLATION_UNCOMPLETED_COUNT_INCLUDES_COLLECTED),
        "enable_dokuwiki_links": ENABLE_DOKUWIKI_LINKS, "dokuwiki_item_link_fn": generate_dokuwiki_item_link,
    },
    folder_actions={
        "settings": get_user_settings, "create": create_folder, "rename": rename_folder,
        "move": move_item, "set_active": set_active_folder, "delete": delete_folder,
        "uncompleted_counts": lambda db: get_uncompleted_counts(db, include_collected=COLLATION_UNCOMPLETED_COUNT_INCLUDES_COLLECTED),
    },
    smart_actions={
        "results": smart_folder_results, "save": save_smart_folder, "delete": delete_smart_folder,
        "uncompleted_counts": lambda db: get_uncompleted_counts(db, include_collected=COLLATION_UNCOMPLETED_COUNT_INCLUDES_COLLECTED),
        "enable_dokuwiki_links": ENABLE_DOKUWIKI_LINKS, "dokuwiki_item_link_fn": generate_dokuwiki_item_link,
    },
    hashtag_actions={"journal": create_journal_entry, "delete": delete_hashtag},
    calendar_actions={
        "context": calendar_context, "settings": get_user_settings, "sort_options": CALENDAR_SORT_OPTIONS,
        "occurrences": _event_occurrences, "journal": create_journal_entry,
        "day_note": save_calendar_day_note, "day_state": _calendar_day_state, "complete_day": complete_calendar_day,
        "save_event": save_calendar_event,
    },
    calendar_edit_actions={
        "save": save_calendar_event, "delete": delete_calendar_event, "duplicate": duplicate_event_to,
        "validate_rule": validate_calendar_recurrence, "journal": create_journal_entry,
        "create_note": create_note, "delete_note": delete_note,
        "create_group": create_event_group, "delete_group": delete_event_group,
    },
    page_actions={
        "uncompleted_counts": lambda db: get_uncompleted_counts(db, include_collected=COLLATION_UNCOMPLETED_COUNT_INCLUDES_COLLECTED),
        "enable_dokuwiki_links": ENABLE_DOKUWIKI_LINKS, "dokuwiki_item_link_fn": generate_dokuwiki_item_link,
    },
    customize_actions={"settings": get_user_settings, "import": import_data_from_json},
    bill_actions={
        "dashboard": _bills_dashboard_data, "pay": pay_bill, "confirm": confirm_bill_invoice,
        "toggle_active": toggle_bill_active, "create": create_bill, "update": update_bill, "delete": delete_bill,
        "save_invoice": save_bill_invoice, "extra_payment": extra_bill_payment,
        "save_adjustment": save_bill_adjustment, "delete_adjustment": delete_bill_adjustment,
        "save_statement": save_bill_statement, "delete_statement": delete_bill_statement,
        "edit_payment": edit_bill_payment, "delete_payment": delete_bill_payment, "payment_note": add_payment_note,
        "toggle_late": toggle_late_override, "statement_comparisons": statement_comparisons,
        "adjustment_types": BillAdjustment.LABELS,
    },
    expense_actions={
        "summaries": spending_summaries, "create": create_expense, "update": update_expense, "delete": delete_expense,
    },
    recipe_actions={
        "shopping": shopping_list_data, "report": ingredient_report_data, "pantry": pantry_data,
        "event": create_ingredient_event, "event_delete": delete_ingredient_event, "reset": reset_all_ingredients,
        "bulk": bulk_ingredient_event, "group_create": create_ingredient_group, "group_link": link_ingredient_to_group,
        "group_unlink": unlink_ingredient, "group_rename": rename_ingredient_group,
        "group_buy": buy_ingredient_group, "group_ran_out": ran_out_ingredient_group,
    },
    category_actions={
        "tree": organize_todos_into_tree,
        "create": create_category, "rename": rename_category, "delete": delete_category,
    },
    reorder_actions={
        "settings": get_user_settings, "journal": create_journal_entry,
        "uncompleted_counts": lambda db: get_uncompleted_counts(db, include_collected=COLLATION_UNCOMPLETED_COUNT_INCLUDES_COLLECTED),
    },
))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("SIMPLE_TODO_HOST", "127.0.0.1"),
        port=int(os.getenv("SIMPLE_TODO_PORT", "3102")),
        proxy_headers=True,
        timeout_graceful_shutdown=1,
    )
