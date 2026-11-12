"""Archived lists, collation lists and the journal for the compact daisyUI frontend."""
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from models import JournalEntry, List, Todo, _as_utc_aware, collation_todos
from daisyui_test_http import wants_fragment

JOURNAL_PAGE = 100
JOURNAL_TYPES = {
    "list": "Lists", "todo": "Tasks", "note": "Notes", "folder": "Folders", "smart_folder": "Smart folders",
    "hashtag": "Hashtags", "calendar_event": "Calendar events", "calendar_day": "Calendar days", "bill": "Bills",
    "expense": "Expenses", "ingredient_group": "Ingredients",
}


def page_view(request):
    view = request.query_params.get("view")
    return view if view in {"archived", "collations"} else None


def attach_page_routes(router, *, templates, get_db, actions):
    def base(request):
        return request.scope.get("root_path", "") + "/app"

    def respond(request, ctx, message=None, status_code=200):
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, ctx["workspace_template"] if partial else "daisyui_test/index.html",
            {**ctx, "message": message, "is_error": status_code >= 400}, status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store"},
        )

    def render_archived(request, db, message=None, status_code=200):
        url = base(request)
        return respond(request, dict(
            request=request, prototype_url=url, query="?sort=user&view=archived", sort="user", current_folder_id=None,
            pages_page="archived", workspace_template="daisyui_test/_archived_workspace.html",
            page_title="Archived", page_url=url + "/archived",
            lists=db.query(List).filter(List.archived == True).order_by(List.modified_at.desc()).all(),
            uncompleted_counts=actions["uncompleted_counts"](db), enable_dokuwiki_links=actions["enable_dokuwiki_links"],
            dokuwiki_item_link_fn=actions["dokuwiki_item_link_fn"],
        ), message, status_code)

    def render_collations(request, db, message=None, status_code=200):
        url = base(request)
        lists = (db.query(List).filter(List.archived == False, List.is_collation == True)
                 .order_by(List.name.asc()).all())
        collected = dict(db.query(collation_todos.c.collation_list_id, func.count())
                         .join(Todo, Todo.id == collation_todos.c.todo_id).filter(Todo.completed == False)
                         .group_by(collation_todos.c.collation_list_id).all())
        return respond(request, dict(
            request=request, prototype_url=url, query="?view=collations", pages_page="collations",
            workspace_template="daisyui_test/_collations_workspace.html",
            page_title="Collations", page_url=url + "/collations", collation_lists=lists,
            open_collected=collected, uncompleted_counts=actions["uncompleted_counts"](db),
        ), message, status_code)

    @router.get("/archived", response_class=HTMLResponse)
    def archived(request: Request, db: Session = Depends(get_db)):
        return render_archived(request, db)

    @router.get("/collations", response_class=HTMLResponse)
    def collations(request: Request, db: Session = Depends(get_db)):
        return render_collations(request, db)

    @router.get("/journal", response_class=HTMLResponse)
    def journal(request: Request, page: int = 1, kind: str = "", q: str = "", db: Session = Depends(get_db)):
        if page < 1 or page > 100000:
            raise HTTPException(400, "Invalid page")
        if kind and kind not in JOURNAL_TYPES and kind != "other":
            raise HTTPException(400, "Invalid entry type")
        q = q.strip()[:200]
        query = db.query(JournalEntry)
        if kind == "other":
            query = query.filter(or_(JournalEntry.entity_type == None, ~JournalEntry.entity_type.in_(JOURNAL_TYPES)))
        elif kind:
            query = query.filter(JournalEntry.entity_type == kind)
        if q:
            pattern = f"%{q}%"
            query = query.filter(or_(JournalEntry.action.ilike(pattern), JournalEntry.details.ilike(pattern)))
        total = query.count()
        entries = (query.order_by(JournalEntry.created_at.desc(), JournalEntry.id.desc())
                   .offset((page - 1) * JOURNAL_PAGE).limit(JOURNAL_PAGE).all())
        days = []
        for entry in entries:
            local = _as_utc_aware(entry.created_at).astimezone() if entry.created_at else None
            key = local.date() if local else None
            if not days or days[-1]["date"] != key:
                days.append({"date": key, "entries": []})
            days[-1]["entries"].append((entry, local))
        filters = {key: value for key, value in (("kind", kind), ("q", q)) if value}
        url = base(request) + "/journal"
        link = lambda number: url + "?" + urlencode({**filters, **({"page": number} if number > 1 else {})}) if filters or number > 1 else url
        pages = max(1, -(-total // JOURNAL_PAGE))
        return respond(request, dict(
            request=request, prototype_url=url.rsplit("/journal", 1)[0], pages_page="journal",
            workspace_template="daisyui_test/_journal_workspace.html",
            page_title="Journal", page_url=link(page), days=days, total=total, page=page, pages=pages,
            newer_url=link(page - 1) if page > 1 else None, older_url=link(page + 1) if page < pages else None,
            kind=kind, q=q, journal_types=JOURNAL_TYPES, journal_url=url,
        ))

    return render_archived, render_collations
