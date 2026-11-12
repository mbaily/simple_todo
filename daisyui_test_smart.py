"""Smart folders for the compact daisyUI frontend: index, results and editor.

Results and saving reuse main.py's smart_folder_results and save_smart_folder,
so both frontends produce identical matches and records. The editor posts
readable per-hashtag and per-folder fields, which are validated here and then
translated into the original form's field names.
"""
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import ImmutableMultiDict

from models import Folder, Hashtag, List, SmartFolder
from daisyui_test_search import results_location
from daisyui_test_http import wants_fragment

MAX_NAME = 255
TRUE = {"1", "true", "yes", "on"}
SECTION_ORDERS = {
    "LET": "Lists, events, tasks", "LTE": "Lists, tasks, events", "ELT": "Events, lists, tasks",
    "ETL": "Events, tasks, lists", "TLE": "Tasks, lists, events", "TEL": "Tasks, events, lists",
}
FLAGS = {
    "uncompleted_only": "Open items only", "include_archived_lists": "Include archived lists",
    "group_todos_by_list": "Group tasks by list", "root_todos_only": "Top-level tasks only",
    "nest_subtodos": "Nest subtasks under parents", "strict_nesting": "Only nest subtasks that match",
}
SORT_FIELDS = {"modified_at": "Modified", "created_at": "Created"}


def smart_view(request):
    return request.query_params.get("view") == "smart"


def folder_state(folder):
    """Editor values for an existing smart folder (or defaults for a new one)."""
    if folder is None:
        return dict(name="", show_lists=True, show_todos=True, section_order="LET",
                    flags={"group_todos_by_list": True}, modified_after="", modified_before="",
                    list_sort_field="modified_at", list_sort_dir="desc", todo_sort_field="modified_at", todo_sort_dir="desc",
                    dokuwiki_links_enabled=False, dokuwiki_date_field="created_at",
                    hashtags={}, folders={}, list_ids=set())
    hashtags = {a.hashtag_id: {"mode": "include", "subtodos": bool(a.include_subtodos), "list_items": bool(a.include_list_items)}
                for a in folder.include_hashtags_assoc}
    hashtags.update({a.hashtag_id: {"mode": "exclude", "subtodos": bool(a.include_subtodos), "list_items": False}
                     for a in folder.exclude_hashtags_assoc})
    folders = {f.id: "include" for f in folder.include_folders}
    folders.update({f.id: "exclude" for f in folder.exclude_folders})
    if folder.include_root_folder:
        folders["root"] = "include"
    elif folder.exclude_root_folder:
        folders["root"] = "exclude"
    return dict(
        name=folder.name, show_lists=bool(folder.show_lists), show_todos=bool(folder.show_todos),
        section_order=(folder.section_order or "LET").upper() if (folder.section_order or "LET").upper() in SECTION_ORDERS else "LET",
        flags={name: bool(getattr(folder, name)) for name in FLAGS},
        modified_after=folder.modified_after.strftime("%Y-%m-%d") if folder.modified_after else "",
        modified_before=(folder.modified_before - timedelta(days=1)).strftime("%Y-%m-%d") if folder.modified_before else "",
        list_sort_field=folder.list_sort_field if folder.list_sort_field in SORT_FIELDS else "modified_at",
        list_sort_dir="desc" if folder.list_sort_desc is None or folder.list_sort_desc else "asc",
        todo_sort_field=folder.todo_sort_field if folder.todo_sort_field in SORT_FIELDS else "modified_at",
        todo_sort_dir="desc" if folder.todo_sort_desc is None or folder.todo_sort_desc else "asc",
        dokuwiki_links_enabled=bool(folder.dokuwiki_links_enabled),
        dokuwiki_date_field=folder.dokuwiki_date_field if folder.dokuwiki_date_field in SORT_FIELDS else "created_at",
        hashtags=hashtags, folders=folders, list_ids={item.id for item in folder.lists},
    )


def form_state(db, form):
    """Validate the daisyUI editor form; return (editor values, original-style form)."""
    errors = []

    def text(name, default=""):
        value = form.get(name, default)
        if not isinstance(value, str):
            raise HTTPException(400, "Invalid form value")
        return value.strip()

    def choice(name, allowed, default):
        value = text(name, default) or default
        if value not in allowed:
            raise HTTPException(400, "The form is invalid. Reload the editor and try again.")
        return value

    state = dict(
        name=text("name"), show_lists=text("show_lists").lower() in TRUE, show_todos=text("show_todos").lower() in TRUE,
        section_order=choice("section_order", SECTION_ORDERS, "LET"),
        flags={name: text(name).lower() in TRUE for name in FLAGS},
        modified_after=text("modified_after"), modified_before=text("modified_before"),
        list_sort_field=choice("list_sort_field", SORT_FIELDS, "modified_at"), list_sort_dir=choice("list_sort_dir", {"asc", "desc"}, "desc"),
        todo_sort_field=choice("todo_sort_field", SORT_FIELDS, "modified_at"), todo_sort_dir=choice("todo_sort_dir", {"asc", "desc"}, "desc"),
        dokuwiki_links_enabled=text("dokuwiki_links_enabled").lower() in TRUE,
        dokuwiki_date_field=choice("dokuwiki_date_field", SORT_FIELDS, "created_at"),
        hashtags={}, folders={}, list_ids=set(),
    )
    for tag_id, in db.query(Hashtag.id).all():
        mode = choice(f"hashtag_mode_{tag_id}", {"", "include", "exclude"}, "")
        if mode:
            state["hashtags"][tag_id] = {"mode": mode, "subtodos": text(f"subtodos_{tag_id}").lower() in TRUE,
                                         "list_items": mode == "include" and text(f"list_items_{tag_id}").lower() in TRUE}
    for folder_id in [row[0] for row in db.query(Folder.id).all()] + ["root"]:
        mode = choice(f"folder_mode_{folder_id}", {"", "include", "exclude"}, "")
        if mode:
            state["folders"][folder_id] = mode
    try:
        requested = {int(value) for value in form.getlist("list_ids")}
    except (TypeError, ValueError):
        raise HTTPException(400, "The list selection is invalid.")
    state["list_ids"] = {row[0] for row in db.query(List.id).filter(List.id.in_(requested)).all()} if requested else set()

    if not state["name"]:
        errors.append("Enter a name for the smart folder.")
    elif len(state["name"]) > MAX_NAME:
        errors.append(f"Smart folder names can be at most {MAX_NAME} characters.")
    dates = {}
    for key, label in (("modified_after", "Modified from"), ("modified_before", "Modified until")):
        if state[key]:
            try:
                dates[key] = datetime.strptime(state[key], "%Y-%m-%d")
            except ValueError:
                errors.append(f"{label} must be a valid date.")
    if len(dates) == 2 and dates["modified_after"] > dates["modified_before"]:
        errors.append("Modified from must be on or before Modified until.")
    if requested and len(state["list_ids"]) != len(requested):
        errors.append("Some selected lists no longer exist. Review the list selection.")
    if not state["show_lists"] and not state["show_todos"]:
        errors.append("Show lists, tasks, or both.")

    pairs = [(key, "true") for key in ("show_lists", "show_todos", "dokuwiki_links_enabled") if state[key]]
    pairs += [(name, "true") for name, enabled in state["flags"].items() if enabled]
    pairs += [(key, state[key]) for key in ("name", "section_order", "modified_after", "modified_before", "list_sort_field",
                                            "list_sort_dir", "todo_sort_field", "todo_sort_dir", "dokuwiki_date_field")]
    for tag_id, rule in sorted(state["hashtags"].items()):
        prefix = "include" if rule["mode"] == "include" else "exclude"
        pairs.append(("hashtag_ids" if prefix == "include" else "exclude_hashtag_ids", str(tag_id)))
        if rule["subtodos"]:
            pairs.append((f"{prefix}_subtodos_{tag_id}", "true"))
        if rule["list_items"]:
            pairs.append((f"include_list_items_{tag_id}", "true"))
    for folder_id, mode in state["folders"].items():
        pairs += [("folder_filter_mode", mode), ("folder_filter_id", str(folder_id))]
    pairs += [("list_ids", str(list_id)) for list_id in sorted(state["list_ids"])]
    return state, ImmutableMultiDict(pairs), errors


def flatten_tree(node, path=()):
    """Yield (folder path, list, todos) from organize_todos_into_tree output."""
    for group in node.get("lists", []):
        yield path, group["list"], group["todos"]
    for child in node.get("folder_nodes", []):
        yield from flatten_tree(child, path + (child["folder"].name,))


def attach_smart_routes(router, *, templates, get_db, actions, feedback, success):
    def page(request, ctx, message=None, status_code=200, headers=None):
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, ctx["workspace_template"] if partial else "daisyui_test/index.html",
            {**ctx, "message": message, "is_error": status_code >= 400}, status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store", **(headers or {})},
        )

    def base(request):
        return request.scope.get("root_path", "") + "/app"

    def ordered(db):
        return db.query(SmartFolder).order_by(SmartFolder.sort_order, SmartFolder.modified_at.desc()).all()

    def render_index(request, db, message=None, status_code=200):
        return page(request, dict(
            request=request, prototype_url=base(request), query="?view=smart-index", smart_page=True,
            workspace_template="daisyui_test/_smart_index_workspace.html", page_title="Smart folders",
            page_url=base(request) + "/smart-folders", smart_folders=ordered(db),
        ), message, status_code)

    def rows_for(todos, results, folder):
        rows = []

        def add(todo, depth):
            rows.append((todo, depth))
            if not folder.nest_subtodos:
                return
            children = [child for child in todo.children if not (folder.uncompleted_only and child.completed)]
            children.sort(key=lambda child: getattr(child, results["todo_sort_attr"]), reverse=results["todo_sort_reverse"])
            for child in children:
                if not folder.strict_nesting or not results["matching_ids"] or child.id in results["matching_ids"]:
                    add(child, depth + 1)

        for todo in todos:
            add(todo, 0)
        return rows

    def render_smart(request, db, sort="user", folder_id=None, message=None, status_code=200):
        path, navigation, mutation = results_location(request, "smart", sort, folder_id)
        folder = db.get(SmartFolder, mutation["smart_folder_id"])
        if folder is None:
            raise HTTPException(404, "Smart folder not found")
        results = actions["results"](db, folder)
        if folder.group_todos_by_list and results["todo_tree"]:
            groups = [dict(path=" › ".join(names), list=item, rows=rows_for(todos, results, folder))
                      for names, item, todos in flatten_tree(results["todo_tree"])]
        else:
            groups = [dict(path="", list=None, rows=rows_for(results["todos"], results, folder))] if results["todos"] else []
        sections = [char for char in (folder.section_order or "LET").upper()
                    if (char == "L" and folder.show_lists) or (char == "T" and folder.show_todos) or (char == "E" and results["events"])]
        url = base(request)
        return page(request, dict(
            request=request, prototype_url=url, query="?" + urlencode(mutation), smart_page=True,
            workspace_template="daisyui_test/_smart_results_workspace.html",
            page_title=f"{folder.name} · Smart folders", page_url=url + path + "?" + urlencode(navigation),
            smart_folder=folder, sections=sections, groups=groups, lists=results["lists"], todos=results["todos"],
            events=results["events"], results=results, task_count=sum(len(group["rows"]) for group in groups),
            sort=sort, current_folder_id=folder_id, folder_param=folder_id, edit_back="results",
            hide_list_context=bool(folder.group_todos_by_list), today_year=datetime.now().year,
            uncompleted_counts=actions["uncompleted_counts"](db),
            enable_dokuwiki_links=actions["enable_dokuwiki_links"], dokuwiki_item_link_fn=actions["dokuwiki_item_link_fn"],
        ), message, status_code)

    def render_editor(request, db, folder, state, message=None, status_code=200):
        url = base(request)
        return page(request, dict(
            request=request, prototype_url=url, query="?view=smart-index", smart_page=True,
            workspace_template="daisyui_test/_smart_edit_workspace.html",
            page_title=("Edit " + folder.name if folder else "New smart folder") + "",
            page_url=url + (f"/smart-folder/{folder.id}/edit" if folder else "/smart-folders/new"),
            smart_folder=folder, values=state, section_orders=SECTION_ORDERS, flags=FLAGS, sort_fields=SORT_FIELDS,
            all_hashtags=db.query(Hashtag).order_by(Hashtag.name).all(),
            all_folders=sorted(db.query(Folder).all(), key=lambda item: item.path_name.lower()),
            all_lists=db.query(List).filter((List.archived == False) | List.id.in_(state["list_ids"] or {0})).order_by(List.name).all(),
            enable_dokuwiki_links=actions["enable_dokuwiki_links"],
        ), message, status_code)

    def existing(db, smart_id):
        folder = db.get(SmartFolder, smart_id)
        if folder is None:
            raise HTTPException(404, "Smart folder not found")
        return folder

    def destination(request, db, folder, message):
        url = base(request) + f"/smart-folder/{folder.id}"
        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(url, status_code=303)
        scope = dict(request.scope)
        scope.update(path_params={"smart_folder_id": folder.id}, query_string=b"")
        response = render_smart(Request(scope), db, message=message)
        response.headers["HX-Push-Url"] = url
        return response

    @router.get("/smart-folders", response_class=HTMLResponse)
    def index(request: Request, db: Session = Depends(get_db)):
        return render_index(request, db)

    @router.get("/smart-folders/new", response_class=HTMLResponse)
    def new(request: Request, db: Session = Depends(get_db)):
        return render_editor(request, db, None, folder_state(None))

    async def save(request, db, folder):
        form = await request.form()
        try:
            state, original_form, errors = form_state(db, form)
        except HTTPException as exc:
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        if errors:
            if request.headers.get("HX-Request") == "true":
                return feedback(request, db, "user", None, " ".join(errors), 400)
            return render_editor(request, db, folder, state, " ".join(errors), 400)
        saved = actions["save"](db, original_form, folder)
        return destination(request, db, saved, "Smart folder saved.")

    @router.post("/smart-folders", response_class=HTMLResponse)
    async def create(request: Request, db: Session = Depends(get_db)):
        return await save(request, db, None)

    @router.get("/smart-folder/{smart_folder_id}", response_class=HTMLResponse)
    def results(request: Request, smart_folder_id: int, sort: str = "user", folder_id: int | None = None,
                db: Session = Depends(get_db)):
        return render_smart(request, db, sort, folder_id)

    @router.get("/smart-folder/{smart_folder_id}/edit", response_class=HTMLResponse)
    def edit(request: Request, smart_folder_id: int, db: Session = Depends(get_db)):
        folder = existing(db, smart_folder_id)
        return render_editor(request, db, folder, folder_state(folder))

    @router.post("/smart-folder/{smart_folder_id}/edit", response_class=HTMLResponse)
    async def update(request: Request, smart_folder_id: int, db: Session = Depends(get_db)):
        folder = db.get(SmartFolder, smart_folder_id)
        if folder is None:
            return feedback(request, db, "user", None, "This smart folder no longer exists.", 404)
        return await save(request, db, folder)

    @router.post("/smart-folder/{smart_folder_id}/order/{direction}", response_class=HTMLResponse)
    def order(request: Request, smart_folder_id: int, direction: str, db: Session = Depends(get_db)):
        folders = ordered(db)
        folder = next((item for item in folders if item.id == smart_folder_id), None)
        if folder is None:
            return feedback(request, db, "user", None, "This smart folder no longer exists.", 404)
        if direction not in {"up", "down"}:
            return feedback(request, db, "user", None, "Choose a valid direction.")
        index_ = folders.index(folder)
        other = index_ - 1 if direction == "up" else index_ + 1
        if 0 <= other < len(folders):
            folders[index_], folders[other] = folders[other], folders[index_]
        # Renumber with the same spacing as the original reorder route.
        for position, item in enumerate(folders):
            item.sort_order = position * 10
        db.commit()
        return success(request, db, "user", None, "Smart folder order updated.")

    @router.post("/smart-folder/{smart_folder_id}/delete", response_class=HTMLResponse)
    def delete(request: Request, smart_folder_id: int, db: Session = Depends(get_db)):
        folder = db.get(SmartFolder, smart_folder_id)
        if folder is None:
            return feedback(request, db, "user", None, "This smart folder no longer exists.", 404)
        name = folder.name
        actions["delete"](folder_id=smart_folder_id, db=db)
        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(base(request) + "/smart-folders", status_code=303)
        response = render_index(request, db, f"Deleted smart folder {name}.")
        response.headers["HX-Push-Url"] = base(request) + "/smart-folders"
        return response

    return render_smart, render_index
