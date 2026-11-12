"""Experimental daisyUI frontend: HTML and HTMX routes, without importing main.

The router factory receives the existing app's dependencies and mutations. The
home query and bulk services are shared with the original frontend.
"""
from datetime import datetime, timedelta
import json
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from item_actions import archive_items, delete_items, pin_items
from models import Folder, List, Todo, Note, ListDatetimeHeader
from daisyui_test_list import attach_list_routes, detail_id, detail_selection
from daisyui_test_edit import attach_edit_routes
from daisyui_test_search import attach_search_routes, results_kind, results_location
from daisyui_test_folders import attach_folder_routes, folders_view
from daisyui_test_smart import attach_smart_routes
from daisyui_test_hashtags import attach_hashtag_routes, hashtags_view
from daisyui_test_calendar import attach_calendar_routes, calendar_view, navigation as calendar_navigation
from daisyui_test_calendar_edit import attach_calendar_edit_routes
from daisyui_test_pages import attach_page_routes, page_view
from daisyui_test_customize import attach_customize_routes, customize_view
from daisyui_test_bills import attach_bill_routes, bills_view
from daisyui_test_expenses import attach_expense_routes, expenses_view
from daisyui_test_reorder import attach_reorder_routes, reorder_view
from daisyui_test_recipes import attach_recipe_routes
from daisyui_test_move import attach_move_routes
from daisyui_test_shortcuts import attach_shortcut_routes
from daisyui_test_categories import attach_category_routes, page_kind as category_page_kind
from daisyui_test_http import wants_fragment


def create_router(*, templates, get_db, home_context, create_list, toggle_list, toggle_todo, list_context, list_actions, edit_actions, search_actions, folder_actions, smart_actions, hashtag_actions, calendar_actions, calendar_edit_actions, page_actions, customize_actions, bill_actions, expense_actions, reorder_actions, recipe_actions, category_actions):
    router = APIRouter(prefix="/app", tags=["daisyui_test"])
    # First: it adds a router-wide dependency, which only applies to routes added after it.
    attach_shortcut_routes(router, templates=templates, get_db=get_db, settings=folder_actions["settings"])

    def page_url(request, sort, folder_id):
        if folders_view(request):
            return request.scope.get("root_path", "") + "/app/folders"
        if request.query_params.get("view") == "smart-index":
            return request.scope.get("root_path", "") + "/app/smart-folders"
        if hashtags_view(request):
            return request.scope.get("root_path", "") + "/app/hashtags"
        if request.query_params.get("view") == "event-groups":
            return request.scope.get("root_path", "") + "/app/calendar/groups"
        if page_view(request):
            return request.scope.get("root_path", "") + "/app/" + page_view(request)
        if customize_view(request):
            return request.scope.get("root_path", "") + "/app/customization"
        if category_page_kind(request):
            return request.scope.get("root_path", "") + "/app/" + category_page_kind(request)
        if reorder_view(request):
            return request.scope.get("root_path", "") + "/app/lists/reorder?" + urlencode({"folder_id": request.query_params.get("folder_id", 0)})
        if expenses_view(request):
            keep = {key: value for key, value in request.query_params.items() if key != "view"}
            return request.scope.get("root_path", "") + "/app/expenses" + ("?" + urlencode(keep) if keep else "")
        if bills_view(request):
            if request.query_params.get("view") == "bill":
                return request.scope.get("root_path", "") + "/app/bills/" + str(int(request.query_params.get("bill_id", 0)))
            suffix = "?show_inactive=1" if request.query_params.get("show_inactive") == "1" else ""
            return request.scope.get("root_path", "") + "/app/bills" + suffix
        if calendar_view(request):
            params = calendar_navigation(request)
            return request.scope.get("root_path", "") + "/app/calendar" + ("?" + urlencode(params) if params else "")
        if results_kind(request):
            path, navigation, _ = results_location(request, results_kind(request), sort, folder_id)
            return request.scope.get("root_path", "") + "/app" + path + "?" + urlencode(navigation)
        params = {"sort": sort}
        if folder_id is not None:
            params["folder_id"] = folder_id
        path = "/app"
        if detail_id(request) is not None:
            path += f"/list/{detail_id(request)}"
        return request.scope.get("root_path", "") + path + "?" + urlencode(params)

    def folder_trail(folder):
        """A folder's ancestors and itself, outermost first, for breadcrumbs."""
        trail = []
        while folder is not None:
            trail.insert(0, folder)
            folder = folder.parent
        return trail

    templates.env.globals["folder_trail"] = folder_trail

    def render(request, db, sort, folder_id, message=None, status_code=200):
        if results_kind(request) == "smart":
            return render_smart(request, db, sort, folder_id, message, status_code)
        if request.query_params.get("view") == "smart-index":
            return render_smart_index(request, db, message, status_code)
        if hashtags_view(request):
            return render_hashtags(request, db, message, status_code)
        if calendar_view(request):
            return render_calendar(request, db, message=message, status_code=status_code)
        if request.query_params.get("view") == "event-groups":
            return render_event_groups(request, db, message, status_code)
        if page_view(request) == "archived":
            return render_archived(request, db, message, status_code)
        if page_view(request) == "collations":
            return render_collations(request, db, message, status_code)
        if customize_view(request):
            return render_customize(request, db, message, status_code)
        if bills_view(request):
            return render_bills(request, db, message, status_code)
        if expenses_view(request):
            return render_expenses(request, db, message, status_code)
        if reorder_view(request):
            return render_reorder(request, db, message, status_code)
        if category_page_kind(request) == "uncompleted":
            return render_uncompleted(request, db, message, status_code)
        if category_page_kind(request) == "categories":
            return render_categories(request, db, message, status_code)
        if results_kind(request):
            return render_results(request, db, sort, folder_id, message, status_code)
        if folders_view(request):
            return render_folders(request, db, message=message, status_code=status_code)
        if detail_id(request) is not None:
            return render_list(request, db, sort, folder_id, message, status_code)
        context = home_context(request, sort, folder_id, db)
        params = {"sort": sort}
        if folder_id is not None:
            params["folder_id"] = folder_id
        today = datetime.now().date()
        # The overview is also the folder browser: the folder being viewed and the folders directly inside it.
        current_folder = db.get(Folder, context["current_folder_id"]) if context.get("current_folder_id") else None
        subfolders = (db.query(Folder).filter(Folder.parent_id == (current_folder.id if current_folder else None))
                      .order_by(Folder.sort_order, Folder.name).all())
        context.update(
            current_folder=current_folder, subfolders=subfolders,
            prototype_url=request.scope.get("root_path", "") + "/app",
            query="?" + urlencode(params),
            folder_param=folder_id,
            folders=db.query(Folder).order_by(Folder.name).all(),
            today=today,
            daily_dates=[today + timedelta(days=i) for i in range(10)],
            message=message,
            is_error=status_code >= 400,
        )
        partial = wants_fragment(request)
        template = "daisyui_test/_workspace.html" if partial else "daisyui_test/index.html"
        response = templates.TemplateResponse(request, template, context, status_code=status_code)
        # A full document and a fragment share the same URL.
        response.headers["Vary"] = "HX-Request, HX-History-Restore-Request"
        response.headers["Cache-Control"] = "no-store"
        return response

    def feedback(request, db, sort, folder_id, message, status_code=400):
        if request.headers.get("HX-Request") != "true":
            return render(request, db, sort, folder_id, message, status_code)
        return templates.TemplateResponse(
            request, "daisyui_test/_feedback.html", {"message": message, "is_error": True},
            status_code=status_code,
            headers={"HX-Retarget": "#page-feedback", "HX-Reswap": "innerHTML", "Cache-Control": "no-store"},
        )

    def success(request, db, sort, folder_id, message):
        if request.headers.get("HX-Request") == "true":
            return render(request, db, sort, folder_id, message)
        return RedirectResponse(page_url(request, sort, folder_id), status_code=303)

    @router.get("", response_class=HTMLResponse)
    def index(request: Request, sort: str = "user", folder_id: int | None = None, db: Session = Depends(get_db)):
        return render(request, db, sort, folder_id)

    @router.post("/lists", response_class=HTMLResponse)
    def add_list(
        request: Request, name: str = Form(""), hashtags: str = Form(""),
        hashtag_ids: list[int] = Form([]), sort: str = "user", folder_id: int | None = None,
        db: Session = Depends(get_db),
    ):
        if not name.strip():
            return feedback(request, db, sort, folder_id, "Enter a name for your list.")
        if folder_id and not db.get(Folder, folder_id):
            return feedback(request, db, sort, folder_id, "This folder no longer exists.", 404)
        create_list(name=name.strip(), hashtags=hashtags, hashtag_ids=hashtag_ids,
                    folder_id=folder_id, manual_sorting=False, db=db)
        return success(request, db, sort, folder_id, "List created.")

    @router.post("/daily-plan", response_class=HTMLResponse)
    def daily_plan(
        request: Request, offset: int = Form(0), sort: str = "user",
        folder_id: int | None = None, db: Session = Depends(get_db),
    ):
        if not 0 <= offset <= 9:
            return feedback(request, db, sort, folder_id, "Choose a date between today and nine days ahead.")
        if folder_id and not db.get(Folder, folder_id):
            return feedback(request, db, sort, folder_id, "This folder no longer exists.", 404)
        day = datetime.now().date() + timedelta(days=offset)
        name = f"Daily Plan {day.day}/{day.month}/{day.year}"
        create_list(name=name, hashtags="", hashtag_ids=[], folder_id=folder_id, manual_sorting=True, db=db)
        return success(request, db, sort, folder_id, f"Created {name}.")

    @router.post("/complete/{item_type}/{item_id}", response_class=HTMLResponse)
    def complete(
        request: Request, item_type: str, item_id: int, completed: bool = Form(False),
        sort: str = "user", folder_id: int | None = None, view_list_id: int | None = None,
        db: Session = Depends(get_db),
    ):
        model = {"list": List, "todo": Todo}.get(item_type)
        item = db.get(model, item_id) if model else None
        if item is None:
            return feedback(request, db, sort, folder_id, "This item no longer exists.", 404)
        if view_list_id is not None and item_id not in detail_selection(db, view_list_id).get(item_type, set()):
            return feedback(request, db, sort, folder_id, "This item is outside the current list.")
        # Set the requested state rather than blindly toggling on a retried request.
        if item.completed != completed:
            (toggle_list if item_type == "list" else toggle_todo)(item_id, db=db)
        return success(request, db, sort, folder_id, "Completion updated.")

    @router.post("/bulk", response_class=HTMLResponse)
    def bulk(
        request: Request, action: str = Form(""), items: list[str] = Form([]),
        sort: str = "user", folder_id: int | None = None, view_list_id: int | None = None,
        db: Session = Depends(get_db),
    ):
        groups = {"list": set(), "todo": set()}
        if view_list_id is not None:
            groups.update(note=set(), datetime_header=set())
        try:
            for key in items:
                kind, raw_id = key.split(":", 1)
                item_id = int(raw_id)
                if kind not in groups or item_id <= 0:
                    raise ValueError
                groups[kind].add(item_id)
        except (ValueError, TypeError):
            return feedback(request, db, sort, folder_id, "The selection is invalid. Select the items again.")
        if not any(groups.values()):
            return feedback(request, db, sort, folder_id, "Select at least one item.")
        if action not in {"pin", "unpin", "archive", "unarchive", "delete"}:
            return feedback(request, db, sort, folder_id, "Choose a valid action.")
        if action in {"archive", "unarchive"} and any(ids for kind, ids in groups.items() if kind != "list"):
            return feedback(request, db, sort, folder_id, "Only lists can be archived or unarchived. Select lists only.")
        if action in {"pin", "unpin"} and any(groups.get(kind) for kind in ("note", "datetime_header")):
            return feedback(request, db, sort, folder_id, "Select tasks only to pin or unpin.")
        if view_list_id is not None:
            allowed = detail_selection(db, view_list_id)
            if groups["list"] or any(not ids.issubset(allowed.get(kind, set())) for kind, ids in groups.items()):
                return feedback(request, db, sort, folder_id, "Some selected items are outside the current list.")
        # Reject a stale selection before mutating any records.
        for kind, ids in groups.items():
            model = {"list": List, "todo": Todo, "note": Note, "datetime_header": ListDatetimeHeader}[kind]
            if ids and db.query(model).filter(model.id.in_(ids)).count() != len(ids):
                return feedback(request, db, sort, folder_id, "Some selected items no longer exist. Refresh and select again.", 404)
        # Delete tasks before lists so a mixed selection can contain a task's list.
        for kind in ("note", "datetime_header", "todo", "list"):
            ids = sorted(groups.get(kind, set()))
            if not ids:
                continue
            if action in {"pin", "unpin"}:
                result = pin_items(db, kind, ids, pin=action == "pin")
            elif action in {"archive", "unarchive"}:
                result = archive_items(db, kind, ids, archived=action == "archive")
            else:
                result = delete_items(db, kind, ids)
            if result.status_code >= 400:
                return feedback(request, db, sort, folder_id, json.loads(result.body)["error"], result.status_code)
        messages = {"pin": "Selection pinned.", "unpin": "Selection unpinned.", "archive": "Selection archived.",
                    "unarchive": "Selection unarchived.", "delete": "Selection deleted."}
        response = success(request, db, sort, folder_id, messages[action])
        response.headers["HX-Trigger"] = "daisy:selection-cleared"
        return response

    render_list = attach_list_routes(
        router, templates=templates, get_db=get_db, list_context=list_context,
        actions=list_actions, feedback=feedback, success=success,
    )
    render_results = attach_search_routes(
        router, templates=templates, get_db=get_db, actions=search_actions, feedback=feedback, success=success,
    )
    render_folders, move_to_folder = attach_folder_routes(
        router, templates=templates, get_db=get_db, actions=folder_actions, feedback=feedback, success=success,
    )
    # Registered before the generic /{kind}/{item_id}/edit editor routes.
    render_smart, render_smart_index = attach_smart_routes(
        router, templates=templates, get_db=get_db, actions=smart_actions, feedback=feedback, success=success,
    )
    render_hashtags = attach_hashtag_routes(
        router, templates=templates, get_db=get_db, actions=hashtag_actions, feedback=feedback, success=success,
    )
    render_calendar = attach_calendar_routes(
        router, templates=templates, get_db=get_db, actions=calendar_actions, feedback=feedback, success=success,
    )
    render_event_groups = attach_calendar_edit_routes(
        router, templates=templates, get_db=get_db, actions=calendar_edit_actions, feedback=feedback,
        render_calendar=render_calendar,
    )
    render_archived, render_collations = attach_page_routes(router, templates=templates, get_db=get_db, actions=page_actions)
    render_customize = attach_customize_routes(router, templates=templates, get_db=get_db, actions=customize_actions, feedback=feedback)
    render_bills = attach_bill_routes(router, templates=templates, get_db=get_db, actions=bill_actions,
                                      feedback=feedback, success=success)
    render_expenses = attach_expense_routes(router, templates=templates, get_db=get_db, actions=expense_actions,
                                            feedback=feedback, success=success)
    render_reorder = attach_reorder_routes(router, templates=templates, get_db=get_db, actions=reorder_actions,
                                           feedback=feedback, success=success)
    render_uncompleted, render_categories = attach_category_routes(
        router, templates=templates, get_db=get_db, actions=category_actions, feedback=feedback, success=success,
    )
    attach_move_routes(router, templates=templates, get_db=get_db, feedback=feedback, render=render,
                       move_to_folder=move_to_folder, settings=folder_actions["settings"])
    attach_recipe_routes(router, templates=templates, get_db=get_db, actions=recipe_actions, feedback=feedback)
    attach_edit_routes(router, templates=templates, get_db=get_db, actions=edit_actions, render_destination=render)
    return router
