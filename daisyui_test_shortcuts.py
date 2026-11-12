"""Favourites, recent places and the jump box for the compact daisyUI frontend.

Favourites (chosen by the user) and recents (recorded when a folder, list,
smart folder or hashtag page is opened) are kept in UserSettings.metadata_json,
so they follow the user between devices without a schema change. Both are lists
of keys such as "folder:3" or "list:12".
"""
import re
from urllib.parse import parse_qs, urlsplit

from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from models import Folder, Hashtag, List, SmartFolder

MODELS = {"folder": Folder, "list": List, "smart": SmartFolder, "hashtag": Hashtag}
MAX_FAVOURITES = 40
MAX_RECENT = 12
SHOWN_RECENT = 8
PLACE = re.compile(r"/app(?:/(list|smart-folder|hashtag)/(\d+))?/?$")
PAGES = [
    ("", "home", "Overview"), ("/calendar", "calendar", "Calendar"), ("/search", "search", "Search"),
    ("/folders", "folder", "Folders"), ("/smart-folders", "smart", "Smart folders"), ("/hashtags", "hash", "Hashtags"),
    ("/uncompleted", "check", "Open tasks"), ("/categories", "list", "Categories"),
    ("/collations", "layers", "Collations"), ("/archived", "archive", "Archived"), ("/bills", "money", "Bills"),
    ("/expenses", "list", "Quick expenses"), ("/journal", "book", "Journal"), ("/customization", "settings", "Customize"),
]


def key_from_url(url):
    """The favourite/recent key for a page address, or None for other pages."""
    parts = urlsplit(url or "")
    match = PLACE.search(parts.path)
    if not match:
        return None
    if match.group(1):
        return f"{'smart' if match.group(1) == 'smart-folder' else match.group(1)}:{int(match.group(2))}"
    folder = parse_qs(parts.query).get("folder_id", [""])[0]
    return f"folder:{int(folder)}" if folder.isdecimal() and int(folder) > 0 else None


def parse_key(key):
    kind, _, raw = (key or "").partition(":")
    if kind not in MODELS or not raw.isdecimal() or int(raw) <= 0:
        return None
    return kind, int(raw)


def stored(settings):
    data = (settings.metadata_json or {}).get("shortcuts") or {}
    clean = lambda keys: [key for key in keys if isinstance(key, str) and parse_key(key)] if isinstance(keys, list) else []
    return {"favourites": clean(data.get("favourites")), "recent": clean(data.get("recent"))}


def save(db, settings, data):
    # Assign a new dict: in-place changes to a JSON column are not detected.
    settings.metadata_json = {**(settings.metadata_json or {}), "shortcuts": data}
    db.commit()


def record_visit(db, settings, key):
    parsed = parse_key(key)
    if not parsed or db.get(MODELS[parsed[0]], parsed[1]) is None:
        return
    data = stored(settings)
    recent = [key] + [item for item in data["recent"] if item != key]
    if recent[:MAX_RECENT] != data["recent"]:
        save(db, settings, {**data, "recent": recent[:MAX_RECENT]})


def place(kind, item, base):
    key = f"{kind}:{item.id}"
    if kind == "folder":
        parent = item.parent.path_name if item.parent else ""
        return dict(key=key, kind=kind, icon="folder", label=item.name, context=parent, url=f"{base}?folder_id={item.id}")
    if kind == "list":
        folder = item.folder.path_name if item.folder else ""
        return dict(key=key, kind=kind, icon="list", label=item.name, context=folder, url=f"{base}/list/{item.id}")
    if kind == "smart":
        return dict(key=key, kind=kind, icon="smart", label=item.name, context="Smart folder", url=f"{base}/smart-folder/{item.id}")
    return dict(key=key, kind=kind, icon="hash", label="#" + item.name, context="Hashtag", url=f"{base}/hashtag/{item.id}")


def resolve(db, keys, base):
    """Turn stored keys into links, silently dropping records that were deleted."""
    wanted = {}
    for key in keys:
        kind, item_id = parse_key(key)
        wanted.setdefault(kind, set()).add(item_id)
    found = {}
    for kind, ids in wanted.items():
        model = MODELS[kind]
        for item in db.query(model).filter(model.id.in_(ids)).all():
            found[f"{kind}:{item.id}"] = place(kind, item, base)
    return [found[key] for key in dict.fromkeys(keys) if key in found]


def strip_context(db, settings, base, current):
    data = stored(settings)
    favourites = resolve(db, data["favourites"], base)
    starred = {item["key"] for item in favourites}
    # The page you are on is not a useful "recent" link, so the first one is the previous place.
    recent = [key for key in data["recent"] if key not in starred and key != current][:SHOWN_RECENT]
    current_place = resolve(db, [current], base) if parse_key(current) else []
    return dict(
        favourites=favourites, recent=resolve(db, recent, base),
        current=current_place[0] if current_place else None,
        current_starred=bool(current_place) and current in starred,
    )


def attach_shortcut_routes(router, *, templates, get_db, settings):
    def base(request):
        return request.scope.get("root_path", "") + "/app"

    def track(request: Request, db: Session = Depends(get_db)):
        """Runs for every /app request: shares the session with templates and records visits."""
        request.state.shortcut_db = db
        if request.method == "GET" and request.headers.get("HX-History-Restore-Request") != "true":
            key = key_from_url(str(request.url))
            if key:
                record_visit(db, settings(db), key)

    # Appended before any route is added, so it applies to the whole router.
    router.dependencies.append(Depends(track))

    def shortcuts(request, at=None):
        """Template global: the strip's links for the page being rendered."""
        db = getattr(request.state, "shortcut_db", None)
        if db is None:
            return None
        current = key_from_url(at if at is not None else str(request.url))
        return dict(strip_context(db, settings(db), base(request), current), prototype_url=base(request))

    templates.env.globals["shortcuts"] = shortcuts

    def render_strip(request, at):
        return templates.TemplateResponse(
            request, "daisyui_test/_shortcut_strip.html",
            {"request": request, "strip": shortcuts(request, at)}, headers={"Cache-Control": "no-store"},
        )

    @router.get("/shortcuts", response_class=HTMLResponse)
    def strip(request: Request, at: str = ""):
        return render_strip(request, at)

    @router.post("/favourite", response_class=HTMLResponse)
    def favourite(request: Request, key: str = Form(""), starred: bool = Form(False), at: str = Form(""),
                  db: Session = Depends(get_db)):
        parsed = parse_key(key)
        item = db.get(MODELS[parsed[0]], parsed[1]) if parsed else None
        if item is not None or not starred:
            user = settings(db)
            data = stored(user)
            favourites = [entry for entry in data["favourites"] if entry != key]
            if starred:
                favourites = (favourites + [key])[-MAX_FAVOURITES:]
            if favourites != data["favourites"]:
                save(db, user, {**data, "favourites": favourites})
        if request.headers.get("HX-Request") != "true":
            target = place(parsed[0], item, base(request))["url"] if item is not None else base(request)
            return RedirectResponse(target, status_code=303)
        return render_strip(request, at)

    @router.get("/jump", response_class=HTMLResponse)
    def jump(request: Request, db: Session = Depends(get_db)):
        """Every place you can go, favourites and recents first; filtered in the browser."""
        prefix = base(request)
        data = stored(settings(db))
        starred = set(data["favourites"])
        folders = sorted(db.query(Folder).all(), key=lambda folder: folder.path_name.lower())
        lists = db.query(List).filter(List.archived == False).order_by(List.name).all()
        smart = db.query(SmartFolder).order_by(SmartFolder.name).all()
        tags = db.query(Hashtag).order_by(Hashtag.name).all()
        places = {}
        for kind, items in (("folder", folders), ("list", lists), ("smart", smart), ("hashtag", tags)):
            for item in items:
                entry = place(kind, item, prefix)
                places[entry["key"]] = entry
        first = resolve(db, list(dict.fromkeys(data["favourites"] + data["recent"])), prefix)
        seen = {entry["key"] for entry in first}
        rows = first + [entry for key, entry in places.items() if key not in seen]
        for entry in rows:
            entry["starred"] = entry["key"] in starred
            entry["recent"] = not entry["starred"] and entry["key"] in data["recent"]
        pages = [dict(key="page:" + path, kind="page", icon=icon, label=label, context="Page", url=prefix + path,
                      starred=False, recent=False) for path, icon, label in PAGES]
        return templates.TemplateResponse(
            request, "daisyui_test/_jump_results.html",
            {"request": request, "rows": rows + pages, "prototype_url": prefix}, headers={"Cache-Control": "no-store"},
        )

