"""Folder management for the compact daisyUI frontend.

Mutations reuse the original folder routes in-process after validating input
here, so journal entries and side effects match the original Folders page.
Lists and folders are moved by picking the destination in the tree itself.
"""
from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from models import Folder, List
from daisyui_test_http import wants_fragment

MAX_NAME = 255


def folders_view(request):
    return request.query_params.get("view") == "folders"


def attach_folder_routes(router, *, templates, get_db, actions, feedback, success):
    def render_folders(request, db, sort="user", folder_id=None, message=None, status_code=200):
        base = request.scope.get("root_path", "") + "/app"
        settings = actions["settings"](db)
        folders = db.query(Folder).order_by(Folder.sort_order, Folder.name).all()
        lists = db.query(List).filter(List.archived == False).order_by(List.name).all()
        lists_by_folder = {}
        for item in lists:
            lists_by_folder.setdefault(item.folder_id, []).append(item)

        def ancestry(folder):
            chain = []
            while folder is not None:
                chain.append(folder)
                folder = folder.parent
            return chain

        context = dict(
            request=request, prototype_url=base, query="?view=folders", sort="user",
            page_url=base + "/folders", page_title="Folders",
            folders_page=True, workspace_template="daisyui_test/_folders_workspace.html",
            root_folders=[folder for folder in folders if folder.parent_id is None],
            all_folders=sorted(folders, key=lambda folder: folder.path_name.lower()),
            lists_by_folder=lists_by_folder, list_total=len(lists),
            folder_paths={folder.id: " ".join(str(item.id) for item in ancestry(folder)) for folder in folders},
            active_folder_id=settings.active_folder_id,
            uncompleted_counts=actions["uncompleted_counts"](db),
            message=message, is_error=status_code >= 400,
        )
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, context["workspace_template"] if partial else "daisyui_test/index.html", context,
            status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store"},
        )

    def folder_or_none(db, folder_id):
        return db.get(Folder, folder_id) if folder_id and folder_id > 0 else None

    def clean_name(name):
        name = name.strip()
        if not name:
            raise HTTPException(400, "Enter a folder name.")
        if len(name) > MAX_NAME:
            raise HTTPException(400, f"Folder names can be at most {MAX_NAME} characters.")
        return name

    def respond(request, db, call):
        # Folder actions are also offered in the overview, which is shown again with its own folder and sort.
        sort = request.query_params.get("sort", "user")
        raw = request.query_params.get("folder_id", "")
        folder_id = int(raw) if raw.isdecimal() else None
        try:
            message = call()
        except HTTPException as exc:
            return feedback(request, db, sort, folder_id, str(exc.detail), exc.status_code)
        return success(request, db, sort, folder_id, message)

    @router.get("/folders", response_class=HTMLResponse)
    def folders(request: Request, db: Session = Depends(get_db)):
        return render_folders(request, db)

    @router.post("/folders", response_class=HTMLResponse)
    def create(request: Request, name: str = Form(""), parent_id: int = Form(0), db: Session = Depends(get_db)):
        def call():
            value = clean_name(name)
            if parent_id and folder_or_none(db, parent_id) is None:
                raise HTTPException(404, "The parent folder no longer exists.")
            actions["create"](request=request, name=value, parent_id=parent_id or None, db=db)
            return f"Created folder {value}."
        return respond(request, db, call)

    def move_to_folder(request, db, items, target_id):
        """Move lists and folders into a folder (0 = top level); returns the message. Shared with /app/move."""
        groups = {"list": set(), "folder": set()}
        try:
            for key in items:
                kind, raw_id = key.split(":", 1)
                if kind not in groups or int(raw_id) <= 0:
                    raise ValueError
                groups[kind].add(int(raw_id))
        except ValueError:
            raise HTTPException(400, "The selection is invalid. Select the items again.")
        if not any(groups.values()):
            raise HTTPException(400, "Select at least one list or folder to move.")
        if target_id < 0:
            raise HTTPException(400, "Choose where to move the selection.")
        target = folder_or_none(db, target_id)
        if target_id and target is None:
            raise HTTPException(404, "The destination folder no longer exists.")
        if (db.query(List).filter(List.id.in_(groups["list"])).count() != len(groups["list"])
                or db.query(Folder).filter(Folder.id.in_(groups["folder"])).count() != len(groups["folder"])):
            raise HTTPException(404, "Some selected items no longer exist. Refresh and select again.")
        # A folder cannot move into itself or anything below it.
        current = target
        while current is not None:
            if current.id in groups["folder"]:
                raise HTTPException(400, f"{current.name} cannot be moved into itself or one of its subfolders.")
            current = current.parent
        for folder_id in sorted(groups["folder"]):
            actions["move"](request=request, source_type="folder", source_id=folder_id, target_folder_id=target_id or None, db=db)
        for list_id in sorted(groups["list"]):
            actions["move"](request=request, source_type="list", source_id=list_id, target_folder_id=target_id or None, db=db)
        count = len(groups["list"]) + len(groups["folder"])
        return f"Moved {count} {'item' if count == 1 else 'items'} to {target.path_name if target else 'the top level'}."

    @router.post("/folders/move-items", response_class=HTMLResponse)
    def move_items(request: Request, items: list[str] = Form([]), target_id: int = Form(-1), db: Session = Depends(get_db)):
        response = respond(request, db, lambda: move_to_folder(request, db, items, target_id))
        if response.status_code < 400:
            response.headers["HX-Trigger"] = "daisy:selection-cleared"
        return response

    def existing(db, folder_id):
        folder = folder_or_none(db, folder_id)
        if folder is None:
            raise HTTPException(404, "This folder no longer exists.")
        return folder

    @router.post("/folders/{folder_id}/rename", response_class=HTMLResponse)
    def rename(request: Request, folder_id: int, name: str = Form(""), db: Session = Depends(get_db)):
        def call():
            existing(db, folder_id)
            value = clean_name(name)
            actions["rename"](request=request, folder_id=folder_id, new_name=value, db=db)
            return "Folder renamed."
        return respond(request, db, call)

    @router.post("/folders/{folder_id}/order/{direction}", response_class=HTMLResponse)
    def order(request: Request, folder_id: int, direction: str, db: Session = Depends(get_db)):
        def call():
            folder = existing(db, folder_id)
            if direction not in {"up", "down"}:
                raise HTTPException(400, "Choose a valid direction.")
            siblings = db.query(Folder).filter(Folder.parent_id == folder.parent_id).order_by(Folder.sort_order, Folder.name).all()
            index = siblings.index(folder)
            other = index - 1 if direction == "up" else index + 1
            if 0 <= other < len(siblings):
                siblings[index], siblings[other] = siblings[other], siblings[index]
            # Renumber with the same spacing as the original reorder route.
            for position, sibling in enumerate(siblings):
                sibling.sort_order = position * 10
            db.commit()
            return "Folder order updated."
        return respond(request, db, call)

    @router.post("/folders/{folder_id}/default", response_class=HTMLResponse)
    def default(request: Request, folder_id: int, enabled: bool = Form(False), db: Session = Depends(get_db)):
        def call():
            existing(db, folder_id)
            settings = actions["settings"](db)
            if enabled:
                actions["set_active"](request=request, folder_id=folder_id, db=db)
                return "Overview now opens this folder."
            if settings.active_folder_id == folder_id:
                actions["set_active"](request=request, folder_id=0, db=db)
            return "Overview now opens the default view."
        return respond(request, db, call)

    @router.post("/folders/{folder_id}/collapse")
    def collapse(folder_id: int, collapsed: bool = Form(False), db: Session = Depends(get_db)):
        folder = folder_or_none(db, folder_id)
        if folder is None:
            return Response(status_code=404)
        if bool(folder.is_collapsed) != collapsed:
            folder.is_collapsed = collapsed
            db.commit()
        return Response(status_code=204)

    @router.post("/folders/{folder_id}/delete", response_class=HTMLResponse)
    def delete(request: Request, folder_id: int, db: Session = Depends(get_db)):
        def call():
            folder = existing(db, folder_id)
            name = folder.name
            actions["delete"](request=request, folder_id=folder_id, db=db)
            return f"Deleted folder {name}."
        return respond(request, db, call)

    return render_folders, move_to_folder
