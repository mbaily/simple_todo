"""The "Move to…" picker in the compact daisyUI frontend.

Tasks and notes move to a list through task_moves.move_tagged_records (shared
with the original frontend), so a moved task brings its subtasks and attached
notes. Lists and folders move to a folder through the Folders page's own logic.
The page the selection came from is re-rendered afterwards using its own query
string. Destinations the user starred or used recently are listed first.
"""
from urllib.parse import parse_qsl, urlencode

from fastapi import Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from models import Folder, List
from daisyui_test_shortcuts import record_visit, stored
from task_moves import MoveError, move_tagged_records

ALLOWED_RETURN_KEYS = {"sort", "folder_id", "view_list_id", "view", "q", "hashtag_id", "smart_folder_id", "highlight", "back"}


def attach_move_routes(router, *, templates, get_db, feedback, render, move_to_folder, settings):
    def base(request):
        return request.scope.get("root_path", "") + "/app"

    @router.get("/move/targets", response_class=HTMLResponse)
    def targets(request: Request, kind: str = "list", items: list[str] = Query([]), db: Session = Depends(get_db)):
        data = stored(settings(db))
        ranked = data["favourites"] + data["recent"]
        if kind == "folder":
            moving = {int(key[7:]) for key in items if key.startswith("folder:") and key[7:].isdecimal()}

            def movable_into(folder):
                # A folder cannot move into itself or anything below it.
                while folder is not None:
                    if folder.id in moving:
                        return False
                    folder = folder.parent
                return True

            folders = sorted(db.query(Folder).all(), key=lambda folder: folder.path_name.lower())
            rows = [dict(id=folder.id, key=f"folder:{folder.id}", icon="folder", label=folder.name,
                         context=folder.parent.path_name if folder.parent else "")
                    for folder in folders if movable_into(folder)]
        else:
            kind = "list"
            lists = db.query(List).filter(List.archived == False).order_by(List.name).all()
            rows = [dict(id=item.id, key=f"list:{item.id}", icon="list", label=item.name,
                         context=item.folder.path_name if item.folder else "") for item in lists]
        # Favourites and recent places first; the sort is stable, so the rest keep their order.
        rows.sort(key=lambda row: ranked.index(row["key"]) if row["key"] in ranked else len(ranked))
        for row in rows:
            row["starred"] = row["key"] in data["favourites"]
            row["recent"] = not row["starred"] and row["key"] in data["recent"]
        if kind == "folder":
            rows.insert(0, dict(id=0, key="", icon="home", label="Top level", context="Not in any folder", starred=False, recent=False))
        return templates.TemplateResponse(request, "daisyui_test/_move_targets.html", {
            "request": request, "prototype_url": base(request), "rows": rows, "kind": kind,
        }, headers={"Cache-Control": "no-store"})

    @router.post("/move", response_class=HTMLResponse)
    def move(request: Request, items: list[str] = Form([]), target_list_id: int = Form(0),
             target_folder_id: int = Form(-1), return_query: str = Form(""), db: Session = Depends(get_db)):
        # Re-render the originating page from its own (whitelisted) query parameters.
        params = [(key, value) for key, value in parse_qsl(return_query.lstrip("?")) if key in ALLOWED_RETURN_KEYS]
        scope = dict(request.scope)
        scope["query_string"] = urlencode(params).encode()
        origin = Request(scope, request.receive)
        plain = request.headers.get("HX-Request") != "true"

        def done(message, destination, visited):
            if visited:
                record_visit(db, settings(db), visited)
            if plain:
                return RedirectResponse(base(request) + destination, status_code=303)
            response = render(origin, db, origin.query_params.get("sort", "user"),
                              int(origin.query_params["folder_id"]) if origin.query_params.get("folder_id", "").isdecimal() else None,
                              message)
            response.headers["HX-Trigger"] = "daisy:selection-cleared"
            return response

        if target_folder_id >= 0:
            try:
                message = move_to_folder(request, db, items, target_folder_id)
            except HTTPException as error:
                return feedback(origin, db, "user", None, str(error.detail), error.status_code)
            return done(message, f"?folder_id={target_folder_id}", f"folder:{target_folder_id}" if target_folder_id else None)

        todo_ids, note_ids = [], []
        for key in items:
            kind, _, raw = key.partition(":")
            if kind not in {"todo", "note"} or not raw.isdecimal():
                return feedback(origin, db, "user", None, "Select tasks or notes to move. Lists and dividers stay where they are.")
            (todo_ids if kind == "todo" else note_ids).append(raw)
        if not todo_ids and not note_ids:
            return feedback(origin, db, "user", None, "Select at least one task or note to move.")
        target = db.get(List, target_list_id) if target_list_id > 0 else None
        if target is None or target.archived:
            return feedback(origin, db, "user", None, "Choose a list to move the selection to.", 404 if target_list_id else 400)
        try:
            result = move_tagged_records(db, {"todo_ids": todo_ids, "note_ids": note_ids, "target_list_id": target.id})
            db.commit()
        except MoveError as error:
            db.rollback()
            return feedback(origin, db, "user", None, str(error), error.status)
        parts = []
        if result["moved_todos"]:
            parts.append(f"{result['moved_todos']} {'task' if result['moved_todos'] == 1 else 'tasks'}")
        if result["moved_notes"]:
            parts.append(f"{result['moved_notes']} {'note' if result['moved_notes'] == 1 else 'notes'}")
        return done(f"Moved {' and '.join(parts)} to {target.name}.", f"/list/{target.id}", f"list:{target.id}")
