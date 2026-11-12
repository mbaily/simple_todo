"""List ordering for the compact daisyUI frontend.

Lists are reordered within one folder scope (a folder, or unfiled lists) with
top, up, down and bottom buttons that save immediately. The order is stored in
List.sort_order exactly as the original drag-and-drop page does.
"""
from urllib.parse import urlencode

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from models import Folder, List
from daisyui_test_http import wants_fragment

MOVES = {"top", "up", "down", "bottom"}


def reorder_view(request):
    return request.query_params.get("view") == "reorder"


def attach_reorder_routes(router, *, templates, get_db, actions, feedback, success):
    def base(request):
        return request.scope.get("root_path", "") + "/app"

    def scope_folder(request, db):
        """Folder whose lists are ordered: ?folder_id (0 = unfiled), else the default view's folder."""
        raw = request.query_params.get("folder_id")
        if raw is not None and raw != "":
            try:
                folder_id = int(raw)
            except ValueError:
                raise HTTPException(400, "Invalid folder")
        else:
            folder_id = actions["settings"](db).active_folder_id or 0
        if folder_id and db.get(Folder, folder_id) is None:
            raise HTTPException(404, "This folder no longer exists.")
        return folder_id

    def scoped_lists(db, folder_id):
        query = db.query(List).filter(List.archived == False)
        query = query.filter(List.folder_id == folder_id) if folder_id else query.filter(List.folder_id == None)
        return query.order_by(List.sort_order.asc(), List.created_at.desc()).all()

    def render_reorder(request, db, message=None, status_code=200):
        folder_id = scope_folder(request, db)
        url = base(request)
        folder = db.get(Folder, folder_id) if folder_id else None
        return_url = url + "?" + urlencode({"sort": "user", "folder_id": folder_id})
        context = dict(
            request=request, prototype_url=url, reorder_page=True, query="?" + urlencode({"folder_id": folder_id, "view": "reorder"}),
            workspace_template="daisyui_test/_reorder_workspace.html", page_title="Reorder lists",
            page_url=url + "/lists/reorder?" + urlencode({"folder_id": folder_id}), folder=folder, folder_id=folder_id,
            lists=scoped_lists(db, folder_id), folders=sorted(db.query(Folder).all(), key=lambda item: item.path_name.lower()),
            uncompleted_counts=actions["uncompleted_counts"](db), return_url=return_url,
            message=message, is_error=status_code >= 400,
        )
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, context["workspace_template"] if partial else "daisyui_test/index.html", context,
            status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store"},
        )

    @router.get("/lists/reorder", response_class=HTMLResponse)
    def reorder(request: Request, db: Session = Depends(get_db)):
        return render_reorder(request, db)

    @router.post("/lists/reorder/move", response_class=HTMLResponse)
    def move(request: Request, list_id: int = Form(0), to: str = Form(""), db: Session = Depends(get_db)):
        try:
            folder_id = scope_folder(request, db)
        except HTTPException as exc:
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        if to not in MOVES:
            return feedback(request, db, "user", None, "Choose where to move the list.")
        lists = scoped_lists(db, folder_id)
        item = next((entry for entry in lists if entry.id == list_id), None)
        if item is None:
            return feedback(request, db, "user", None, "This list is no longer in this folder. Refresh the page.", 404)
        index = lists.index(item)
        target = {"top": 0, "up": max(0, index - 1), "down": min(len(lists) - 1, index + 1), "bottom": len(lists) - 1}[to]
        if target != index:
            lists.insert(target, lists.pop(index))
            # Same storage as the original reorder page: positions within the folder scope.
            for position, entry in enumerate(lists):
                entry.sort_order = position
            actions["journal"](db, "Reordered Lists", f"Updated sort order for {len(lists)} lists")
            db.commit()
        place = {"top": "to the top", "bottom": "to the bottom", "up": "up", "down": "down"}[to]
        return success(request, db, "user", None, f"Moved {item.name} {place}.")

    return render_reorder
