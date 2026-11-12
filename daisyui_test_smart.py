"""Smart folders for the compact daisyUI frontend: index, results and editor.

A smart folder is a saved view. The editor reads as a sentence (show these kinds of
items, that have these tags or hashtags, in these folders) and posts its whole state on
every change, so the page can show what the rules match before anything is saved.
Matching itself lives in smart_folder_service and is shared with the original frontend.
"""
from datetime import datetime, timedelta
import re
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from hashtag_index import all_hashtags, ensure_index, extract_hashtags
from helpers import create_journal_entry
from models import Folder, Hashtag, List, SmartFolder, SmartFolderExcludeHashtag, SmartFolderIncludeHashtag, utcnow
from smart_folder_service import SmartFolderService
from daisyui_test_search import results_location
from daisyui_test_http import wants_fragment

MAX_NAME = 255
TRUE = {"1", "true", "yes", "on"}
SECTION_ORDERS = {
    "LET": "Lists, events, tasks", "LTE": "Lists, tasks, events", "ELT": "Events, lists, tasks",
    "ETL": "Events, tasks, lists", "TLE": "Tasks, lists, events", "TEL": "Tasks, events, lists",
}
SORT_FIELDS = {"modified_at": "Modified", "created_at": "Created"}
SCOPES = {"all": "Every folder", "only": "Only these folders", "except": "Every folder except"}
# How subtasks appear -> (root_todos_only, nest_subtodos, strict_nesting)
SUBTASKS = {
    "flat": ("Every matching task on its own row", (False, False, False)),
    "top": ("Top-level tasks only", (True, False, False)),
    "nest": ("Matching parents with all their subtasks", (False, True, False)),
    "nest_matching": ("Only matching subtasks, nested under their parent", (False, True, True)),
}
HASHTAG_NAME = re.compile(r"[^\W\d_]\w{0,99}\Z")
PREVIEW_ROWS = 6


def smart_view(request):
    return request.query_params.get("view") == "smart"


def folder_state(folder):
    """Editor values for an existing smart folder (or defaults for a new one)."""
    if folder is None:
        return dict(name="", show_lists=True, show_todos=True, show_events=True, match="any", include=[], exclude=[],
                    subtodos=False, list_items=False, scope="all", places=[], extra_exclude_places=[], subfolders=True, list_ids=[],
                    uncompleted_only=False, include_archived_lists=False, modified_after="", modified_before="",
                    group_todos_by_list=True, subtasks="flat", section_order="LET",
                    list_sort_field="modified_at", list_sort_dir="desc", todo_sort_field="modified_at", todo_sort_dir="desc",
                    dokuwiki_links_enabled=False, dokuwiki_date_field="created_at")
    includes, excludes = folder.include_hashtags_assoc, folder.exclude_hashtags_assoc
    only = [f"folder:{f.id}" for f in folder.include_folders] + (["root"] if folder.include_root_folder else [])
    without = [f"folder:{f.id}" for f in folder.exclude_folders] + (["root"] if folder.exclude_root_folder else [])
    if folder.nest_subtodos:
        subtasks = "nest_matching" if folder.strict_nesting else "nest"
    else:
        subtasks = "top" if folder.root_todos_only else "flat"
    return dict(
        name=folder.name, show_lists=bool(folder.show_lists), show_todos=bool(folder.show_todos),
        show_events=folder.show_events is None or bool(folder.show_events),
        match="all" if folder.match_all else "any",
        include=[f"tag:{a.hashtag_id}" for a in includes] + [f"hash:{name}" for name in folder.include_text_hashtags or []],
        exclude=[f"tag:{a.hashtag_id}" for a in excludes] + [f"hash:{name}" for name in folder.exclude_text_hashtags or []],
        # Older smart folders kept these per tag; the editor shows one switch for the whole folder.
        subtodos=bool(folder.include_subtodos) or any(a.include_subtodos for a in includes + excludes),
        list_items=bool(folder.include_list_items) or any(a.include_list_items for a in includes),
        scope="only" if only else "except" if without else "all", places=only or without,
        extra_exclude_places=without if only else [],
        subfolders=bool(folder.include_subfolders), list_ids=[item.id for item in folder.lists],
        uncompleted_only=bool(folder.uncompleted_only), include_archived_lists=bool(folder.include_archived_lists),
        modified_after=folder.modified_after.strftime("%Y-%m-%d") if folder.modified_after else "",
        modified_before=(folder.modified_before - timedelta(days=1)).strftime("%Y-%m-%d") if folder.modified_before else "",
        group_todos_by_list=bool(folder.group_todos_by_list), subtasks=subtasks,
        section_order=(folder.section_order or "LET").upper() if (folder.section_order or "LET").upper() in SECTION_ORDERS else "LET",
        list_sort_field=folder.list_sort_field if folder.list_sort_field in SORT_FIELDS else "modified_at",
        list_sort_dir="desc" if folder.list_sort_desc is None or folder.list_sort_desc else "asc",
        todo_sort_field=folder.todo_sort_field if folder.todo_sort_field in SORT_FIELDS else "modified_at",
        todo_sort_dir="desc" if folder.todo_sort_desc is None or folder.todo_sort_desc else "asc",
        dokuwiki_links_enabled=bool(folder.dokuwiki_links_enabled),
        dokuwiki_date_field=folder.dokuwiki_date_field if folder.dokuwiki_date_field in SORT_FIELDS else "created_at",
    )


def form_state(db, form):
    """Read the editor form; return (editor values, validation errors).

    Each chosen tag, hashtag, folder or list is a ticked chip; the "add" menus name one more.
    """
    errors = []

    def text(name, default=""):
        value = form.get(name, default)
        if not isinstance(value, str):
            raise HTTPException(400, "Invalid form value")
        return value.strip()

    def flag(name):
        return text(name).lower() in TRUE

    def choice(name, allowed, default):
        value = text(name, default) or default
        if value not in allowed:
            raise HTTPException(400, "The form is invalid. Reload the editor and try again.")
        return value

    def chosen(name):
        values = [value for value in form.getlist(name) if isinstance(value, str)]
        added = text("add_" + name)
        return list(dict.fromkeys(values + ([added] if added else []))), added

    tag_ids = {row[0] for row in db.query(Hashtag.id).all()}

    def rule(key):
        kind, _, value = key.partition(":")
        if kind == "tag" and value.isdecimal() and int(value) in tag_ids:
            return f"tag:{int(value)}"
        if kind == "hash" and HASHTAG_NAME.match(value.lstrip("#")):
            return "hash:" + value.lstrip("#").lower()
        return None

    include, added_include = chosen("include")
    exclude, added_exclude = chosen("exclude")
    pending_hashtags = {}
    new_includes = {rule(added_include or "")}
    for key, selected in (("include", include), ("exclude", exclude)):
        typed = text("new_" + key)
        valid = True
        for value in re.split(r"[\s,]+", typed):
            if not value:
                continue
            name = value.lstrip("#")
            if not HASHTAG_NAME.fullmatch(name) or not extract_hashtags("#" + name):
                errors.append(f'“{value}” is not a valid hashtag. Use a word such as #home.')
                valid = False
            else:
                selected.append("hash:" + name.lower())
                if key == "include":
                    new_includes.add("hash:" + name.lower())
        pending_hashtags["new_" + key] = "" if valid else typed
    include = list(dict.fromkeys(key for key in map(rule, include) if key))
    exclude = list(dict.fromkeys(key for key in map(rule, exclude) if key))
    # A tag cannot be both wanted and unwanted: the side it was just added to wins.
    exclude = [key for key in exclude if key not in new_includes]
    include = [key for key in include if key not in exclude]

    folder_ids = {row[0] for row in db.query(Folder.id).all()}
    places, _ = chosen("places")
    places = [key for key in places if key == "root" or (key.startswith("folder:") and key[7:].isdecimal() and int(key[7:]) in folder_ids)]
    extra_exclude_places, _ = chosen("extra_exclude_places")
    extra_exclude_places = [key for key in extra_exclude_places if key == "root" or (key.startswith("folder:") and key[7:].isdecimal() and int(key[7:]) in folder_ids)]
    scope = choice("scope", SCOPES, "all")
    try:
        requested = list(dict.fromkeys(int(value) for value in chosen("lists")[0]))
    except (TypeError, ValueError):
        raise HTTPException(400, "The list selection is invalid.")
    known = {row[0] for row in db.query(List.id).filter(List.id.in_(requested)).all()} if requested else set()

    state = dict(
        name=text("name"), show_lists=flag("show_lists"), show_todos=flag("show_todos"), show_events=flag("show_events"),
        match=choice("match", {"any", "all"}, "any"), include=include, exclude=exclude,
        subtodos=flag("subtodos"), list_items=flag("list_items"),
        scope=scope, places=places if scope != "all" else [],
        extra_exclude_places=extra_exclude_places if scope == "only" else [],
        # The switch is only on the page once folders are being chosen; it starts ticked.
        subfolders=flag("subfolders") if text("scope_shown") else True,
        list_ids=[list_id for list_id in requested if list_id in known],
        uncompleted_only=flag("uncompleted_only"), include_archived_lists=flag("include_archived_lists"),
        modified_after=text("modified_after"), modified_before=text("modified_before"),
        group_todos_by_list=flag("group_todos_by_list"), subtasks=choice("subtasks", SUBTASKS, "flat"),
        section_order=choice("section_order", SECTION_ORDERS, "LET"),
        list_sort_field=choice("list_sort_field", SORT_FIELDS, "modified_at"), list_sort_dir=choice("list_sort_dir", {"asc", "desc"}, "desc"),
        todo_sort_field=choice("todo_sort_field", SORT_FIELDS, "modified_at"), todo_sort_dir=choice("todo_sort_dir", {"asc", "desc"}, "desc"),
        dokuwiki_links_enabled=flag("dokuwiki_links_enabled"),
        dokuwiki_date_field=choice("dokuwiki_date_field", SORT_FIELDS, "created_at"),
        **pending_hashtags,
    )

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
    if len(known) != len(requested):
        errors.append("Some selected lists no longer exist. Review the list selection.")
    if not (state["show_lists"] or state["show_todos"] or state["show_events"]):
        errors.append("Show lists, tasks or calendar events.")
    if state["scope"] != "all" and not state["places"]:
        errors.append("Choose at least one folder, or look in every folder.")
    return state, errors


def apply_state(db, folder, state):
    """Write validated editor values to a smart folder (a new one when folder is None)."""
    if folder is None:
        folder = SmartFolder(name=state["name"])
        db.add(folder)
    root_only, nest, strict = SUBTASKS[state["subtasks"]][1]
    after = datetime.strptime(state["modified_after"], "%Y-%m-%d") if state["modified_after"] else None
    # Stored as the midnight after the day the user chose, so that day is included.
    before = datetime.strptime(state["modified_before"], "%Y-%m-%d") + timedelta(days=1) if state["modified_before"] else None
    values = dict(
        name=state["name"], show_lists=state["show_lists"], show_todos=state["show_todos"], show_events=state["show_events"],
        match_all=state["match"] == "all", include_subtodos=state["subtodos"], include_list_items=state["list_items"],
        include_subfolders=state["subfolders"], uncompleted_only=state["uncompleted_only"],
        include_archived_lists=state["include_archived_lists"], modified_after=after, modified_before=before,
        group_todos_by_list=state["group_todos_by_list"], root_todos_only=root_only, nest_subtodos=nest, strict_nesting=strict,
        section_order=state["section_order"], list_sort_field=state["list_sort_field"], list_sort_desc=state["list_sort_dir"] == "desc",
        todo_sort_field=state["todo_sort_field"], todo_sort_desc=state["todo_sort_dir"] == "desc",
        dokuwiki_links_enabled=state["dokuwiki_links_enabled"], dokuwiki_date_field=state["dokuwiki_date_field"],
        include_text_hashtags=[key[5:] for key in state["include"] if key.startswith("hash:")],
        exclude_text_hashtags=[key[5:] for key in state["exclude"] if key.startswith("hash:")],
    )
    for key, value in values.items():
        setattr(folder, key, value)
    folder.modified_at = utcnow()

    def tags(collection, model, keys, **flags):
        wanted = [int(key[4:]) for key in keys if key.startswith("tag:")]
        for assoc in [assoc for assoc in collection if assoc.hashtag_id not in wanted]:
            collection.remove(assoc)
        existing = {assoc.hashtag_id: assoc for assoc in collection}
        for tag_id in wanted:
            assoc = existing.get(tag_id)
            if assoc is None:
                assoc = model(hashtag_id=tag_id)
                collection.append(assoc)
            for name, value in flags.items():
                setattr(assoc, name, value)

    tags(folder.include_hashtags_assoc, SmartFolderIncludeHashtag, state["include"],
         include_subtodos=state["subtodos"], include_list_items=state["list_items"])
    tags(folder.exclude_hashtags_assoc, SmartFolderExcludeHashtag, state["exclude"], include_subtodos=state["subtodos"])

    chosen = [int(key[7:]) for key in state["places"] if key != "root"]
    folders = db.query(Folder).filter(Folder.id.in_(chosen)).all() if chosen else []
    root = "root" in state["places"]
    folder.include_folders = folders if state["scope"] == "only" else []
    if state["scope"] == "only":
        extra_ids = [int(key[7:]) for key in state["extra_exclude_places"] if key != "root"]
        folder.exclude_folders = db.query(Folder).filter(Folder.id.in_(extra_ids)).all() if extra_ids else []
    else:
        folder.exclude_folders = folders if state["scope"] == "except" else []
    folder.include_root_folder = root and state["scope"] == "only"
    folder.exclude_root_folder = (root and state["scope"] == "except") or "root" in state["extra_exclude_places"]
    folder.lists = db.query(List).filter(List.id.in_(state["list_ids"])).all() if state["list_ids"] else []
    db.flush()
    return folder


def rule_summary(folder):
    """Short phrases describing a saved smart folder: [(text, kind)], kind in plain / tag / hash / not."""
    shown = [label for label, on in (("Lists", folder.show_lists), ("Tasks", folder.show_todos),
                                     ("Events", folder.show_events is None or folder.show_events)) if on]
    parts = [(", ".join(shown) if len(shown) < 3 else "Everything", "plain")]
    include = [(a.hashtag.name, "tag") for a in folder.include_hashtags_assoc if a.hashtag]
    include += [("#" + name, "hash") for name in folder.include_text_hashtags or []]
    if len(include) > 1:
        parts.append(("all of" if folder.match_all else "any of", "plain"))
    parts += include
    parts += [("not " + a.hashtag.name, "not") for a in folder.exclude_hashtags_assoc if a.hashtag]
    parts += [("not #" + name, "not") for name in folder.exclude_text_hashtags or []]
    if folder.include_subtodos or any(a.include_subtodos for a in folder.include_hashtags_assoc):
        parts.append(("with subtasks", "plain"))
    if folder.include_list_items or any(a.include_list_items for a in folder.include_hashtags_assoc):
        parts.append(("with tasks in matching lists", "plain"))
    only = [item.path_name for item in folder.include_folders] + (["Top level"] if folder.include_root_folder else [])
    without = [item.path_name for item in folder.exclude_folders] + (["Top level"] if folder.exclude_root_folder else [])
    parts += [("in " + name, "plain") for name in only] + [("not in " + name, "not") for name in without]
    if (only or without) and folder.include_subfolders:
        parts.append(("including subfolders", "plain"))
    if folder.lists:
        parts.append(("lists: " + ", ".join(item.name for item in folder.lists), "plain"))
    if folder.uncompleted_only:
        parts.append(("open only", "plain"))
    if folder.include_archived_lists:
        parts.append(("with archived", "plain"))
    if folder.modified_after:
        parts.append(("modified from " + folder.modified_after.strftime("%Y-%m-%d"), "plain"))
    if folder.modified_before:
        parts.append(("modified until " + (folder.modified_before - timedelta(days=1)).strftime("%Y-%m-%d"), "plain"))
    return parts


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
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store",
                     **({"HX-Retarget": "#daisy-workspace", "HX-Reswap": "outerMorph"} if partial and status_code >= 400 else {}),
                     **(headers or {})},
        )

    def base(request):
        return request.scope.get("root_path", "") + "/app"

    def ordered(db):
        return db.query(SmartFolder).order_by(SmartFolder.sort_order, SmartFolder.modified_at.desc()).all()

    def render_index(request, db, message=None, status_code=200):
        return page(request, dict(
            request=request, prototype_url=base(request), query="?view=smart-index", smart_page=True,
            workspace_template="daisyui_test/_smart_index_workspace.html", page_title="Smart folders",
            page_url=base(request) + "/smart-folders", smart_folders=ordered(db), rule_summary=rule_summary,
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
            smart_folder=folder, rules=rule_summary(folder), sections=sections, groups=groups, lists=results["lists"], todos=results["todos"],
            events=results["events"], results=results, task_count=sum(len(group["rows"]) for group in groups),
            sort=sort, current_folder_id=folder_id, folder_param=folder_id, edit_back="results",
            hide_list_context=bool(folder.group_todos_by_list), today_year=datetime.now().year,
            uncompleted_counts=actions["uncompleted_counts"](db),
            enable_dokuwiki_links=actions["enable_dokuwiki_links"], dokuwiki_item_link_fn=actions["dokuwiki_item_link_fn"],
        ), message, status_code)

    def preview(db, folder, state):
        """What the rules in the editor match right now. Nothing is kept: the rules are
        written to the session, read back through the normal matching, and rolled back."""
        try:
            draft = apply_state(db, folder, state)
            lists = SmartFolderService.get_lists(db, draft)
            matching_ids = set()
            todos, _ = SmartFolderService.get_todos(db, draft, matching_ids=matching_ids)
            events = SmartFolderService.get_calendar_events(db, draft)
            return dict(
                lists=len(lists), todos=len(matching_ids), events=len(events),
                list_names=[item.name for item in lists[:PREVIEW_ROWS]],
                todo_names=[(todo.title, todo.list.name if todo.list else "") for todo in todos[:PREVIEW_ROWS]],
                event_names=[event.title for event in events[:PREVIEW_ROWS]],
            )
        finally:
            db.rollback()

    def render_editor(request, db, folder, state, message=None, status_code=200, errors=()):
        url = base(request)
        ensure_index(db)
        folder_id, folder_name = (folder.id, folder.name) if folder else (None, "")
        blocking_errors = [error for error in errors if not error.startswith(("Enter a name", "Smart folder names"))]
        matches = preview(db, folder, state) if not blocking_errors else None
        tags = db.query(Hashtag).order_by(Hashtag.name).all()
        tag_names = {f"tag:{tag.id}": tag.name for tag in tags}
        hashtags = sorted({name for name, _ in all_hashtags(db)}
                          | {key[5:] for key in state["include"] + state["exclude"] if key.startswith("hash:")})
        folders = sorted(db.query(Folder).all(), key=lambda item: item.path_name.lower())
        place_names = {"root": "Top level (no folder)", **{f"folder:{item.id}": item.path_name for item in folders}}
        lists = db.query(List).filter((List.archived == False) | List.id.in_(state["list_ids"] or [0])).order_by(List.name).all()
        list_names = {item.id: item.name for item in lists}
        label = lambda key: tag_names.get(key, "#" + key[5:] if key.startswith("hash:") else key)
        used = set(state["include"]) | set(state["exclude"])
        return page(request, dict(
            request=request, prototype_url=url, query="?view=smart-index", smart_page=True,
            workspace_template="daisyui_test/_smart_edit_workspace.html",
            page_title=("Edit " + folder_name if folder_id else "New smart folder") + "",
            page_url=url + (f"/smart-folder/{folder_id}/edit" if folder_id else "/smart-folders/new"),
            smart_folder_id=folder_id, smart_folder_name=folder_name, values=state, matches=matches, problems=list(errors),
            section_orders=SECTION_ORDERS, sort_fields=SORT_FIELDS, scopes=SCOPES,
            subtask_modes={key: text for key, (text, _) in SUBTASKS.items()},
            include_chips=[(key, label(key)) for key in state["include"]],
            exclude_chips=[(key, label(key)) for key in state["exclude"]],
            rule_options=[("Tags", [(key, name) for key, name in tag_names.items() if key not in used]),
                          ("Hashtags in text", [("hash:" + name, "#" + name) for name in hashtags if "hash:" + name not in used])],
            place_chips=[(key, place_names[key]) for key in state["places"] if key in place_names],
            place_options=[("", [(key, name) for key, name in place_names.items() if key not in state["places"]])],
            extra_exclude_chips=[(key, place_names[key]) for key in state["extra_exclude_places"] if key in place_names],
            extra_exclude_options=[("", [(key, name) for key, name in place_names.items() if key not in state["extra_exclude_places"]])],
            list_chips=[(list_id, list_names.get(list_id, "List")) for list_id in state["list_ids"]],
            list_options=[("", [(item.id, item.name) for item in lists if item.id not in state["list_ids"]])],
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

    async def read(request, db):
        return form_state(db, await request.form())

    async def save(request, db, folder):
        try:
            state, errors = await read(request, db)
        except HTTPException as exc:
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        if errors:
            return render_editor(request, db, folder, state, " ".join(errors), 400, errors)
        created = folder is None
        ensure_index(db)
        saved = apply_state(db, folder, state)
        create_journal_entry(db, "Created Smart Folder" if created else "Updated Smart Folder",
                             f"{'Created' if created else 'Updated'} smart folder '{saved.name}'", "smart_folder", saved.id)
        db.commit()
        return destination(request, db, saved, "Smart folder saved.")

    async def draft(request, db, folder):
        """Redraw the editor from the form as it stands, with what it would match; nothing is saved."""
        try:
            state, errors = await read(request, db)
        except HTTPException as exc:
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        # An empty name is expected while the rules are still being put together.
        errors = [error for error in errors if not error.startswith("Enter a name")]
        return render_editor(request, db, folder, state, None, 200, errors)

    @router.post("/smart-folders/draft", response_class=HTMLResponse)
    async def draft_new(request: Request, db: Session = Depends(get_db)):
        return await draft(request, db, None)

    @router.post("/smart-folder/{smart_folder_id}/draft", response_class=HTMLResponse)
    async def draft_existing(request: Request, smart_folder_id: int, db: Session = Depends(get_db)):
        folder = db.get(SmartFolder, smart_folder_id)
        if folder is None:
            return feedback(request, db, "user", None, "This smart folder no longer exists.", 404)
        return await draft(request, db, folder)

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
