"""Customize page for the compact daisyUI frontend: global hashtag filters, export and import.

Filters are stored in the same text fields as the original page, so both
frontends apply them identically; names that do not match an existing tag are
kept rather than silently dropped.
"""
import json
from urllib.parse import urlencode

from fastapi import Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from helpers import parse_hashtags
from models import Hashtag
from daisyui_test_http import wants_fragment

MAX_IMPORT_BYTES = 50 * 1024 * 1024


def customize_view(request):
    return request.query_params.get("view") == "customize"


def attach_customize_routes(router, *, templates, get_db, actions, feedback):
    def base(request):
        return request.scope.get("root_path", "") + "/app"

    def show_confirmations(request):
        """Template global: whether success messages such as "Task added." are shown (errors always are)."""
        db = getattr(request.state, "shortcut_db", None)
        if db is None:
            return True
        return (actions["settings"](db).metadata_json or {}).get("show_confirmations", True) is not False

    templates.env.globals["show_confirmations"] = show_confirmations

    def render_customize(request, db, message=None, status_code=200):
        settings = actions["settings"](db)
        tags = db.query(Hashtag).order_by(Hashtag.name).all()
        names = {tag.name for tag in tags}
        include = parse_hashtags(settings.filter_inc_hashtags or "")
        exclude = parse_hashtags(settings.filter_exc_hashtags or "")
        params = request.query_params
        imported = message is None and bool(params.get("import_success"))
        if imported:
            message = f"Import finished: {params.get('created', '0')} created, {params.get('updated', '0')} updated."
        if message is None and params.get("import_error"):
            message, status_code = params["import_error"], 400
        url = base(request)
        context = dict(
            request=request, prototype_url=url, query="?view=customize", customize_page=True,
            workspace_template="daisyui_test/_customize_workspace.html",
            page_title="Customize", page_url=url + "/customization",
            hashtags=tags, tag_modes={**{name: "include" for name in include}, **{name: "exclude" for name in exclude}},
            unknown_include=[name for name in include if name not in names],
            unknown_exclude=[name for name in exclude if name not in names],
            message=message, is_error=status_code >= 400,
            # An import's result is information, not just a confirmation, so it is always shown.
            message_always=imported,
            confirmations_on=(settings.metadata_json or {}).get("show_confirmations", True) is not False,
        )
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, context["workspace_template"] if partial else "daisyui_test/index.html", context,
            status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store"},
        )

    @router.get("/customization", response_class=HTMLResponse)
    def customize(request: Request, db: Session = Depends(get_db)):
        return render_customize(request, db)

    @router.post("/customization/filters", response_class=HTMLResponse)
    async def save_filters(request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        include, exclude = [], []
        for tag in db.query(Hashtag).order_by(Hashtag.name).all():
            mode = form.get(f"tag_{tag.id}", "")
            if mode not in {"", "include", "exclude"}:
                return feedback(request, db, "user", None, "The filter form is invalid. Reload the page and try again.")
            (include if mode == "include" else exclude if mode == "exclude" else []).append(tag.name)
        # Keep saved names that have no tag yet, unless the user removed them.
        for field, target in (("keep_include", include), ("keep_exclude", exclude)):
            target.extend(name for name in parse_hashtags(" ".join(form.getlist(field))) if name not in target)
        settings = actions["settings"](db)
        settings.filter_inc_hashtags = " ".join("#" + name for name in include)
        settings.filter_exc_hashtags = " ".join("#" + name for name in exclude)
        db.commit()
        summary = []
        if include:
            summary.append(f"showing only {len(include)} {'tag' if len(include) == 1 else 'tags'}")
        if exclude:
            summary.append(f"hiding {len(exclude)}")
        message = "Filters saved: " + (", ".join(summary) if summary else "no filters") + "."
        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(base(request) + "/customization", status_code=303)
        return render_customize(request, db, message)

    @router.post("/customization/display", response_class=HTMLResponse)
    def save_display(request: Request, show_confirmations: bool = Form(False), db: Session = Depends(get_db)):
        settings = actions["settings"](db)
        # Assign a new dict: in-place changes to a JSON column are not detected.
        settings.metadata_json = {**(settings.metadata_json or {}), "show_confirmations": show_confirmations}
        db.commit()
        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(base(request) + "/customization", status_code=303)
        return render_customize(request, db, "Confirmation messages are on." if show_confirmations else None)

    @router.post("/customization/import")
    async def import_file(request: Request, file: UploadFile = File(...), restore_history: bool = Form(False),
                          db: Session = Depends(get_db)):
        url = base(request) + "/customization?"
        try:
            content = await file.read(MAX_IMPORT_BYTES + 1)
            if len(content) > MAX_IMPORT_BYTES:
                raise ValueError("The file is larger than 50 MB.")
            try:
                data = json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("The file is not a valid JSON export.")
            stats = actions["import"](db, data, restore_history=restore_history)
        except Exception as exc:
            db.rollback()
            message = str(exc).rstrip(".") or "The import failed"
            if not message.endswith("No changes were saved"):
                message += ". No changes were saved"
            return RedirectResponse(url + urlencode({"import_error": message + "."}), status_code=303)
        return RedirectResponse(url + urlencode({"import_success": 1, "created": stats["created"], "updated": stats["updated"]}),
                                status_code=303)

    return render_customize
