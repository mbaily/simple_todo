"""List detail HTML routes for daisyui_test, sharing the original list services."""
from datetime import datetime
import inspect
import json
from urllib.parse import urlencode

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from models import List, Todo, Note
from daisyui_test_http import wants_fragment


def detail_id(request):
    raw = request.path_params.get("list_id", request.query_params.get("view_list_id"))
    if raw is None:
        return None
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid list context")


def require_list(db, list_id):
    item = db.get(List, list_id)
    if item is None:
        raise HTTPException(404, "List not found")
    return item


def detail_selection(db, list_id):
    """Include native records plus descendants/notes of collected tasks."""
    item = require_list(db, list_id)
    tasks = {task.id for task in item.todos}
    pending = list(item.collated_todos) if item.is_collation else []
    while pending:
        task = pending.pop()
        if task.id in tasks:
            continue
        tasks.add(task.id)
        pending.extend(task.children)
    notes = {note.id for note in item.notes}
    if tasks:
        notes.update(row[0] for row in db.query(Note.id).filter(Note.todo_id.in_(tasks)).all())
    return {"list": {list_id}, "todo": tasks, "note": notes,
            "datetime_header": {header.id for header in item.datetime_headers}}


def json_request(request, payload):
    """Adapt a validated HTML form to the existing JSON action's Request API.

    This is an in-process call, using the already authenticated user's session;
    no HTTP subrequest or extra database connection is made.
    """
    body = json.dumps(payload).encode()
    scope = dict(request.scope)
    scope["headers"] = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def attach_list_routes(router, *, templates, get_db, list_context, actions, feedback, success):
    def render_list(request, db, sort, folder_id, message=None, status_code=200):
        list_id = detail_id(request)
        highlight = request.query_params.get("highlight")
        try:
            highlight = int(highlight) if highlight else None
        except ValueError:
            raise HTTPException(400, "Invalid highlighted task")
        context = list_context(request, list_id, folder_id, sort, highlight, db)
        prototype_url = request.scope.get("root_path", "") + "/app"
        navigation = {"sort": sort}
        if folder_id is not None:
            navigation["folder_id"] = folder_id
        params = {**navigation, "view_list_id": list_id}
        if highlight is not None:
            params["highlight"] = highlight
        context.update(
            prototype_url=prototype_url, detail_url=f"{prototype_url}/list/{list_id}",
            query="?" + urlencode(params), navigation_query="?" + urlencode(navigation),
            folder_param=folder_id, list_page=True, today=datetime.now().date(),
            workspace_template="daisyui_test/_list_workspace.html", message=message,
            is_error=status_code >= 400,
        )
        partial = wants_fragment(request)
        template = context["workspace_template"] if partial else "daisyui_test/index.html"
        return templates.TemplateResponse(request, template, context, status_code=status_code,
                                          headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store"})

    async def run(action, db, request=None, payload=None, **kwargs):
        if request is not None:
            kwargs["request"] = json_request(request, payload) if payload is not None else request
        result = actions[action](db=db, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        if getattr(result, "status_code", 200) >= 400:
            try:
                message = json.loads(result.body).get("error", "The update could not be saved.")
            except (ValueError, AttributeError):
                message = "The update could not be saved."
            raise HTTPException(result.status_code, message)
        return result

    def selected_tasks(db, list_id, items, *, single=False, native=False):
        try:
            keys = list(dict.fromkeys(items))
            if any(not key.startswith("todo:") for key in keys):
                raise ValueError
            ids = list(dict.fromkeys(int(key.split(":", 1)[1]) for key in keys))
        except (ValueError, TypeError):
            raise HTTPException(400, "Select tasks only for this action.")
        if not ids or (single and len(ids) != 1):
            raise HTTPException(400, "Select exactly one task." if single else "Select at least one task.")
        if not set(ids).issubset(detail_selection(db, list_id)["todo"]):
            raise HTTPException(400, "Some selected tasks are outside the current list.")
        if native and db.query(Todo).filter(Todo.id.in_(ids), Todo.list_id == list_id).count() != len(ids):
            raise HTTPException(400, "Select tasks that belong to this recipe list.")
        return ids

    def error(request, db, sort, folder_id, exc):
        return feedback(request, db, sort, folder_id, str(exc.detail), exc.status_code)

    @router.get("/list/{list_id}", response_class=HTMLResponse)
    def view(request: Request, list_id: int, sort: str = "user", folder_id: int | None = None,
             highlight: int | None = None, db: Session = Depends(get_db)):
        return render_list(request, db, sort, folder_id)

    @router.post("/list/{list_id}/todos", response_class=HTMLResponse)
    async def add_todo(request: Request, list_id: int, title: str = Form(""), hashtags: str = Form(""),
                       parent_id: str = Form(""), sort: str = "user", folder_id: int | None = None,
                       db: Session = Depends(get_db)):
        try:
            require_list(db, list_id)
            if not title.strip():
                raise HTTPException(400, "Enter a task title.")
            parent = None
            if parent_id:
                ids = selected_tasks(db, list_id, ["todo:" + parent_id], single=True)
                parent = db.get(Todo, ids[0])
            await run("create_todo", db, list_id=parent.list_id if parent else list_id,
                      title=title.strip(), hashtags=hashtags, parent_id=parent.id if parent else None)
            return success(request, db, sort, folder_id, "Task added.")
        except HTTPException as exc:
            return error(request, db, sort, folder_id, exc)

    @router.post("/list/{list_id}/notes", response_class=HTMLResponse)
    async def add_note(request: Request, list_id: int, text: str = Form(""), name: str = Form(""),
                       todo_id: str = Form(""), sort: str = "user", folder_id: int | None = None,
                       db: Session = Depends(get_db)):
        try:
            require_list(db, list_id)
            if not text.strip():
                raise HTTPException(400, "Enter some note text.")
            task = None
            if todo_id:
                ids = selected_tasks(db, list_id, ["todo:" + todo_id], single=True)
                task = db.get(Todo, ids[0])
            await run("create_note", db, request=request, list_id=task.list_id if task else list_id,
                      text=text, name=name.strip() or None, todo_id=task.id if task else None,
                      calendar_event_id=None, occurrence_at=None, return_to=None)
            return success(request, db, sort, folder_id, "Note added.")
        except HTTPException as exc:
            return error(request, db, sort, folder_id, exc)

    @router.post("/list/{list_id}/dividers", response_class=HTMLResponse)
    async def add_divider(request: Request, list_id: int, sort: str = "user", folder_id: int | None = None,
                          db: Session = Depends(get_db)):
        try:
            require_list(db, list_id)
            await run("divider", db, list_id=list_id)
            return success(request, db, sort, folder_id, "Date divider added.")
        except HTTPException as exc:
            return error(request, db, sort, folder_id, exc)

    @router.post("/list/{list_id}/options/{option}", response_class=HTMLResponse)
    async def option(request: Request, list_id: int, option: str, enabled: bool = Form(False),
                     sort: str = "user", folder_id: int | None = None, db: Session = Depends(get_db)):
        allowed = {"show_completed", "show_delayed", "is_collation", "manual_sorting", "is_recipe",
                   "show_dokuwiki_item_links", "dokuwiki_page_enabled", "sort_ascending",
                   "partition_by_datetime_headers", "intersperse_collated_todos"}
        try:
            item = require_list(db, list_id)
            if option not in allowed:
                raise HTTPException(400, "Unknown list option.")
            if bool(getattr(item, option)) != enabled:
                kwargs = {"list_id": list_id}
                if option == "manual_sorting":
                    kwargs["manual_sorting"] = enabled
                await run(option, db, **kwargs)
            return success(request, db, sort, folder_id, "List options updated.")
        except HTTPException as exc:
            return error(request, db, sort, folder_id, exc)

    @router.post("/list/{list_id}/category", response_class=HTMLResponse)
    async def category(request: Request, list_id: int, category: str = Form(""), sort: str = "user",
                       folder_id: int | None = None, db: Session = Depends(get_db)):
        try:
            require_list(db, list_id)
            await run("category", db, list_id=list_id, category=category.strip())
            return success(request, db, sort, folder_id, "Category updated.")
        except HTTPException as exc:
            return error(request, db, sort, folder_id, exc)

    @router.post("/list/{list_id}/collation/{collation_id}/{todo_id}", response_class=HTMLResponse)
    async def collation(request: Request, list_id: int, collation_id: int, todo_id: int,
                        enabled: bool = Form(False), sort: str = "user", folder_id: int | None = None,
                        db: Session = Depends(get_db)):
        try:
            ids = selected_tasks(db, list_id, [f"todo:{todo_id}"], single=True)
            task = db.get(Todo, ids[0])
            target = require_list(db, collation_id)
            if not target.is_collation:
                raise HTTPException(400, "Choose a collation list.")
            if (target in task.collation_lists) != enabled:
                await run("collation", db, collation_list_id=collation_id, todo_id=todo_id)
            return success(request, db, sort, folder_id, "Collation membership updated.")
        except HTTPException as exc:
            return error(request, db, sort, folder_id, exc)

    @router.post("/list/{list_id}/selected/{action}", response_class=HTMLResponse)
    async def selected(request: Request, list_id: int, action: str, items: list[str] = Form([]),
                       delayed_until: str = Form(""), hashtags: str = Form(""), alarm_mode: str = Form("none"),
                       alarm_start_range: str = Form(""), alarm_end_range: str = Form(""),
                       alarm_target_time: str = Form(""), alarm_minutes: int = Form(120),
                       sort: str = "user", folder_id: int | None = None, db: Session = Depends(get_db)):
        try:
            item = require_list(db, list_id)
            if action not in {"defer", "alarm", "hashtags", "wiki-enable", "wiki-disable", "bought", "ran_out"}:
                raise HTTPException(400, "Unknown task action.")
            ids = selected_tasks(db, list_id, items, single=action in {"defer", "alarm", "hashtags"},
                                 native=action in {"bought", "ran_out"})
            if action == "defer":
                await run("defer", db, request, {"delayed_until": delayed_until}, todo_id=ids[0])
            elif action == "hashtags":
                await run("hashtags", db, request, {"hashtags": hashtags}, todo_id=ids[0])
            elif action == "alarm":
                if alarm_mode not in {"none", "range", "tolerance"}:
                    raise HTTPException(400, "Choose a valid alarm mode.")
                if alarm_mode == "range":
                    try:
                        start = datetime.strptime(alarm_start_range, "%Y-%m-%dT%H:%M")
                        end = datetime.strptime(alarm_end_range, "%Y-%m-%dT%H:%M")
                        if end < start:
                            raise ValueError
                    except ValueError:
                        raise HTTPException(400, "Enter a valid alarm range with the end after the start.")
                if alarm_mode == "tolerance" and (not alarm_target_time or not 0 <= alarm_minutes <= 525600):
                    raise HTTPException(400, "Enter a target time and a tolerance between 0 and 525600 minutes.")
                await run("alarm", db, request, dict(alarm_mode=alarm_mode, alarm_start_range=alarm_start_range,
                          alarm_end_range=alarm_end_range, alarm_target_time=alarm_target_time,
                          alarm_minutes=alarm_minutes), todo_id=ids[0])
            elif action.startswith("wiki-"):
                await run("wiki_pages", db, request, {"ids": ids, "enable": action == "wiki-enable"})
            else:
                if not item.is_recipe:
                    raise HTTPException(400, "Enable recipe mode first.")
                await run("ingredients", db, request, {"todo_ids": ids, "event_type": action}, list_id=list_id)
            return success(request, db, sort, folder_id, "Selected tasks updated.")
        except HTTPException as exc:
            return error(request, db, sort, folder_id, exc)

    @router.post("/list/{list_id}/reorder", response_class=HTMLResponse)
    async def reorder(request: Request, list_id: int, items: list[str] = Form([]), target_id: int = Form(...),
                      sort: str = "user", folder_id: int | None = None, db: Session = Depends(get_db)):
        try:
            item = require_list(db, list_id)
            if not item.manual_sorting:
                raise HTTPException(400, "Enable manual ordering first.")
            ids = selected_tasks(db, list_id, items)
            selected_tasks(db, list_id, [f"todo:{target_id}"], single=True)
            if target_id in ids:
                raise HTTPException(400, "Choose a destination task outside the selection.")
            await run("reorder", db, source_ids=ids, target_id=target_id, list_id=list_id)
            return success(request, db, sort, folder_id, "Task order updated.")
        except HTTPException as exc:
            return error(request, db, sort, folder_id, exc)

    @router.post("/list/{list_id}/ingredients/reset", response_class=HTMLResponse)
    async def reset(request: Request, list_id: int, sort: str = "user", folder_id: int | None = None,
                    db: Session = Depends(get_db)):
        try:
            require_list(db, list_id)
            await run("reset_ingredients", db, request, {}, list_id=list_id)
            return success(request, db, sort, folder_id, "Ingredients reset.")
        except HTTPException as exc:
            return error(request, db, sort, folder_id, exc)

    @router.post("/list/{list_id}/wiki/{action}", response_class=HTMLResponse)
    async def wiki(request: Request, list_id: int, action: str, todo_ids: list[int] = Form([]),
                   sort: str = "user", folder_id: int | None = None, db: Session = Depends(get_db)):
        try:
            if action not in {"confirm", "unconfirm"}:
                raise HTTPException(400, "Unknown wiki action.")
            ids = selected_tasks(db, list_id, [f"todo:{value}" for value in todo_ids])
            await run("wiki_" + action, db, request, {"type": "header_group", "todo_ids": ids})
            return success(request, db, sort, folder_id, "Wiki confirmation updated.")
        except HTTPException as exc:
            return error(request, db, sort, folder_id, exc)

    return render_list
