"""Hashtags found in text, for the compact daisyUI frontend.

A hashtag is a #word written in a name, title or note. Hashtags are read-only
here: they change by editing the text. Tags, which are attached in an item's
Tags field, live under /app/tags and /app/tag/{id}.
"""
from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from hashtag_index import SOURCES, all_hashtags, extract_hashtags, hashtag_entities, install_hashtag_index, _installed
from models import CalendarEvent, Folder, List, Note, Todo
from daisyui_test_http import wants_fragment

MODELS = {"folders": Folder, "lists": List, "todos": Todo, "notes": Note, "calendar_events": CalendarEvent}
LABELS = {"folders": ("folder", "folders"), "lists": ("list", "lists"), "todos": ("task", "tasks"),
          "notes": ("note", "notes"), "calendar_events": ("event", "events")}


def attach_text_hashtag_routes(router, *, templates, get_db):
    templates.env.globals["text_hashtags"] = extract_hashtags

    def respond(request, db, template, **context):
        base = request.scope.get("root_path", "") + "/app"
        context.update(request=request, prototype_url=base, texttags_page=True, workspace_template=template,
                       labels=LABELS, page_url=base + context.pop("path"))
        return templates.TemplateResponse(
            request, template if wants_fragment(request) else "daisyui_test/index.html", context,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store"},
        )

    def ready(db):
        # Databases opened outside database.get_engine (tests) are indexed on first use.
        if db.bind not in _installed:
            install_hashtag_index(db.bind)

    @router.get("/hashtags", response_class=HTMLResponse)
    def hashtags(request: Request, db: Session = Depends(get_db)):
        ready(db)
        return respond(request, db, "daisyui_test/_texthashtags_workspace.html", path="/hashtags",
                       page_title="Hashtags", hashtags=all_hashtags(db), sources=list(SOURCES))

    @router.get("/hashtag/{name}", response_class=HTMLResponse)
    def hashtag(request: Request, name: str, db: Session = Depends(get_db)):
        if name.isdecimal():
            # Tag pages lived at this address before hashtags were added.
            query = "?" + request.url.query if request.url.query else ""
            return RedirectResponse(f"{request.scope.get('root_path', '')}/app/tag/{name}{query}", status_code=308)
        ready(db)
        name = name.lstrip("#").lower()
        found = {kind: db.query(MODELS[kind]).filter(MODELS[kind].id.in_(ids)).all() if ids else []
                 for kind, ids in hashtag_entities(db, name).items() if kind in MODELS}
        found["folders"].sort(key=lambda folder: folder.path_name.lower())
        found["lists"].sort(key=lambda item: item.name.lower())
        found["todos"].sort(key=lambda todo: (todo.completed, -todo.modified_at.timestamp() if todo.modified_at else 0))
        found["notes"].sort(key=lambda note: -note.created_at.timestamp() if note.created_at else 0)
        found["calendar_events"].sort(key=lambda event: event.start_at)
        return respond(request, db, "daisyui_test/_texthashtag_workspace.html", path="/hashtag/" + name,
                       page_title="#" + name, name=name, found=found, total=sum(len(rows) for rows in found.values()))
