"""Search and hashtag result pages for the compact daisyUI frontend.

Result queries are shared with the original frontend. Mutations made from a
results page (completion, bulk actions, editors) carry ``view=search`` or
``view=hashtag`` so the router re-renders the same results afterwards.
"""
from datetime import datetime
from urllib.parse import urlencode

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from daisyui_test_http import wants_fragment

MAX_QUERY = 200
HASHTAG_OPTIONS = {
    "show_completed": "Show completed items",
    "include_list_items": "Include tasks from tagged lists",
    "hide_completed_list_items": "Skip tasks in completed tagged lists",
}


def results_kind(request):
    kind = request.query_params.get("view")
    return kind if kind in {"search", "hashtag", "smart"} else None


def results_id(request, key, label):
    raw = request.path_params.get(key, request.query_params.get(key))
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise HTTPException(400, f"Invalid {label}")


def results_hashtag_id(request):
    return results_id(request, "hashtag_id", "hashtag")


def results_location(request, kind, sort, folder_id):
    """Return (canonical path, URL parameters, mutation parameters)."""
    navigation = {"sort": sort}
    if folder_id is not None:
        navigation["folder_id"] = folder_id
    if kind == "search":
        q = (request.query_params.get("q") or "").strip()[:MAX_QUERY]
        if q:
            navigation["q"] = q
        return "/search", navigation, {**navigation, "view": "search"}
    if kind == "smart":
        smart_id = results_id(request, "smart_folder_id", "smart folder")
        return f"/smart-folder/{smart_id}", navigation, {**navigation, "view": "smart", "smart_folder_id": smart_id}
    hashtag_id = results_hashtag_id(request)
    return f"/hashtag/{hashtag_id}", navigation, {**navigation, "view": "hashtag", "hashtag_id": hashtag_id}


def attach_search_routes(router, *, templates, get_db, actions, feedback, success):
    def render_results(request, db, sort, folder_id, message=None, status_code=200, kind=None):
        kind = kind or results_kind(request)
        path, navigation, mutation = results_location(request, kind, sort, folder_id)
        base = request.scope.get("root_path", "") + "/app"
        q = navigation.get("q", "")
        hashtag = None
        if kind == "search":
            results = actions["search"](db, q)
            title = f"{q} · Search" if q else "Search"
        else:
            hashtag = actions["load_hashtag"](db, mutation["hashtag_id"])
            if hashtag is None:
                raise HTTPException(404, "Hashtag not found")
            results = actions["hashtag"](db, hashtag)
            title = f"#{hashtag.name}"
        todos = sorted(results["todos"], key=lambda todo: todo.modified_at, reverse=True)
        context = dict(
            request=request, prototype_url=base, query="?" + urlencode(mutation),
            page_url=base + path + "?" + urlencode(navigation), page_title=title + "",
            results_page=True, results_kind=kind, workspace_template="daisyui_test/_results_workspace.html",
            q=q, hashtag=hashtag, hashtag_options=HASHTAG_OPTIONS,
            lists=results["lists"], todos=todos, events=results["events"],
            sort=sort, current_folder_id=folder_id, folder_param=folder_id, edit_back="results",
            uncompleted_counts=actions["uncompleted_counts"](db),
            enable_dokuwiki_links=actions["enable_dokuwiki_links"],
            dokuwiki_item_link_fn=actions["dokuwiki_item_link_fn"],
            today_year=datetime.now().year, message=message, is_error=status_code >= 400,
        )
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, context["workspace_template"] if partial else "daisyui_test/index.html", context,
            status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store"},
        )

    @router.get("/search", response_class=HTMLResponse)
    def search(request: Request, sort: str = "user", folder_id: int | None = None, db: Session = Depends(get_db)):
        return render_results(request, db, sort, folder_id, kind="search")

    @router.get("/hashtag/{hashtag_id}", response_class=HTMLResponse)
    def hashtag(request: Request, hashtag_id: int, sort: str = "user", folder_id: int | None = None,
                db: Session = Depends(get_db)):
        return render_results(request, db, sort, folder_id, kind="hashtag")

    @router.post("/hashtag/{hashtag_id}/options/{name}", response_class=HTMLResponse)
    def hashtag_option(request: Request, hashtag_id: int, name: str, enabled: bool = Form(False),
                       sort: str = "user", folder_id: int | None = None, db: Session = Depends(get_db)):
        item = actions["load_hashtag"](db, hashtag_id)
        if item is None:
            return feedback(request, db, sort, folder_id, "This hashtag no longer exists.", 404)
        if name not in HASHTAG_OPTIONS:
            return feedback(request, db, sort, folder_id, "Choose a valid hashtag option.")
        # Set the requested state so a retried request cannot flip it back.
        if bool(getattr(item, name)) != enabled:
            setattr(item, name, enabled)
            if name == "show_completed":
                actions["journal"](db, "Toggled Hashtag Show Completed",
                                   f"Toggled show completed for hashtag '#{item.name}' to {enabled}", "hashtag", item.id)
            db.commit()
        return success(request, db, sort, folder_id, "Hashtag view updated.")

    return render_results
