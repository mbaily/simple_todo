"""Hashtag index for the compact daisyUI frontend: usage, options, rename, delete.

Per-tag view options reuse the /hashtag/{id}/options routes from
daisyui_test_search with ``view=hashtags`` so they re-render this page.
"""
from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from helpers import parse_hashtags
from models import Hashtag, calendar_event_hashtags, list_hashtags, todo_hashtags
from daisyui_test_http import wants_fragment

MAX_NAME = 100


def hashtags_view(request):
    return request.query_params.get("view") == "hashtags"


def usage(db, table):
    return dict(db.query(table.c.hashtag_id, func.count()).group_by(table.c.hashtag_id).all())


def attach_hashtag_routes(router, *, templates, get_db, actions, feedback, success):
    def render_hashtags(request, db, message=None, status_code=200):
        base = request.scope.get("root_path", "") + "/app"
        context = dict(
            request=request, prototype_url=base, query="?view=hashtags", hashtags_page=True,
            workspace_template="daisyui_test/_hashtags_workspace.html",
            page_title="Hashtags", page_url=base + "/hashtags",
            hashtags=db.query(Hashtag).order_by(func.lower(Hashtag.name)).all(),
            list_counts=usage(db, list_hashtags), todo_counts=usage(db, todo_hashtags),
            event_counts=usage(db, calendar_event_hashtags),
            message=message, is_error=status_code >= 400,
        )
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, context["workspace_template"] if partial else "daisyui_test/index.html", context,
            status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store"},
        )

    @router.get("/hashtags", response_class=HTMLResponse)
    def hashtags(request: Request, db: Session = Depends(get_db)):
        return render_hashtags(request, db)

    @router.post("/hashtag/{hashtag_id}/rename", response_class=HTMLResponse)
    def rename(request: Request, hashtag_id: int, name: str = Form(""), db: Session = Depends(get_db)):
        tag = db.get(Hashtag, hashtag_id)
        if tag is None:
            return feedback(request, db, "user", None, "This hashtag no longer exists.", 404)
        parsed = parse_hashtags(name)
        # Accept exactly one tag, written with or without a leading '#'.
        if len(parsed) != 1 or parsed[0] != name.strip().lstrip("#"):
            return feedback(request, db, "user", None, "Use one hashtag made of letters, numbers and underscores.")
        new_name = parsed[0]
        if len(new_name) > MAX_NAME:
            return feedback(request, db, "user", None, f"Hashtags can be at most {MAX_NAME} characters.")
        if new_name == tag.name:
            return success(request, db, "user", None, "Hashtag unchanged.")
        if db.query(Hashtag).filter(Hashtag.name == new_name, Hashtag.id != tag.id).first():
            return feedback(request, db, "user", None, f"#{new_name} already exists. Choose another name.", 409)
        old_name = tag.name
        tag.name = new_name
        actions["journal"](db, "Renamed Hashtag", f"Renamed hashtag '#{old_name}' to '#{new_name}'", "hashtag", tag.id)
        db.commit()
        return success(request, db, "user", None, f"Renamed #{old_name} to #{new_name}.")

    @router.post("/hashtag/{hashtag_id}/delete", response_class=HTMLResponse)
    def delete(request: Request, hashtag_id: int, db: Session = Depends(get_db)):
        tag = db.get(Hashtag, hashtag_id)
        if tag is None:
            return feedback(request, db, "user", None, "This hashtag no longer exists.", 404)
        name = tag.name
        actions["delete"](hashtag_id=hashtag_id, db=db)
        return success(request, db, "user", None, f"Deleted #{name}. Tagged items were kept.")

    return render_hashtags
