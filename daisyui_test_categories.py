"""Open tasks across all lists, and list categories, for the compact daisyUI frontend.

Category changes call the original routes in-process after validation. The
category page also shows names used by lists that are missing from the
Category table, so every category in use can be renamed or deleted.
"""
from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Category, List, Todo
from daisyui_test_http import wants_fragment

MAX_NAME = 255


def page_kind(request):
    view = request.query_params.get("view")
    return view if view in {"uncompleted", "categories"} else None


def attach_category_routes(router, *, templates, get_db, actions, feedback, success):
    def base(request):
        return request.scope.get("root_path", "") + "/app"

    def respond(request, ctx, message=None, status_code=200):
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, ctx["workspace_template"] if partial else "daisyui_test/index.html",
            {**ctx, "message": message, "is_error": status_code >= 400}, status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store"},
        )

    def render_uncompleted(request, db, message=None, status_code=200):
        todos = (db.query(Todo).join(Todo.list).filter(Todo.completed == False, List.archived == False)
                 .order_by(List.name, Todo.modified_at.desc()).all())
        tree = actions["tree"](db, todos) if todos else None
        groups = []

        def walk(node, path=()):
            for group in node.get("lists", []):
                groups.append(dict(path=" › ".join(path), list=group["list"], todos=group["todos"]))
            for child in node.get("folder_nodes", []):
                walk(child, path + (child["folder"].name,))

        if tree:
            walk(tree)
        url = base(request)
        return respond(request, dict(
            request=request, prototype_url=url, query="?sort=user&view=uncompleted", sort="user", current_folder_id=None,
            uncompleted_page=True, workspace_template="daisyui_test/_uncompleted_workspace.html",
            page_title="Open tasks", page_url=url + "/uncompleted", groups=groups, total=len(todos),
            hide_list_context=True, uncompleted_counts={}, enable_dokuwiki_links=False, dokuwiki_item_link_fn=None,
        ), message, status_code)

    def category_rows(db):
        counts = dict(db.query(List.category, func.count(List.id)).filter(List.category != None, List.category != "")
                      .group_by(List.category).all())
        saved = {row.name for row in db.query(Category).all()}
        return [dict(name=name, list_count=counts.get(name, 0), saved=name in saved)
                for name in sorted(saved | set(counts), key=str.lower)]

    def render_categories(request, db, message=None, status_code=200):
        url = base(request)
        return respond(request, dict(
            request=request, prototype_url=url, query="?view=categories", categories_page=True,
            workspace_template="daisyui_test/_categories_workspace.html", page_title="Categories",
            page_url=url + "/categories", categories=category_rows(db),
        ), message, status_code)

    @router.get("/uncompleted", response_class=HTMLResponse)
    def uncompleted(request: Request, db: Session = Depends(get_db)):
        return render_uncompleted(request, db)

    @router.get("/categories", response_class=HTMLResponse)
    def categories(request: Request, db: Session = Depends(get_db)):
        return render_categories(request, db)

    def known(db):
        return {row["name"] for row in category_rows(db)}

    def clean(name):
        name = name.strip()
        if not name or len(name) > MAX_NAME:
            raise HTTPException(400, f"Enter a category name of up to {MAX_NAME} characters.")
        return name

    def run(request, db, call):
        try:
            message = call()
        except HTTPException as exc:
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        return success(request, db, "user", None, message)

    @router.post("/categories/create", response_class=HTMLResponse)
    def create(request: Request, name: str = Form(""), db: Session = Depends(get_db)):
        def call():
            value = clean(name)
            if db.query(Category).filter(Category.name == value).first():
                raise HTTPException(409, f"The category {value} already exists.")
            actions["create"](request=request, name=value, db=db)
            return f"Created category {value}."
        return run(request, db, call)

    @router.post("/categories/rename", response_class=HTMLResponse)
    def rename(request: Request, old_name: str = Form(""), new_name: str = Form(""), db: Session = Depends(get_db)):
        def call():
            old, new = old_name.strip(), clean(new_name)
            if old not in known(db):
                raise HTTPException(404, "This category no longer exists. Refresh the page.")
            if new == old:
                return "Category unchanged."
            if new in known(db):
                raise HTTPException(409, f"The category {new} already exists. Choose another name.")
            actions["rename"](request=request, old_name=old, new_name=new, db=db)
            return f"Renamed {old} to {new}."
        return run(request, db, call)

    @router.post("/categories/delete", response_class=HTMLResponse)
    def delete(request: Request, name: str = Form(""), db: Session = Depends(get_db)):
        def call():
            value = name.strip()
            if value not in known(db):
                raise HTTPException(404, "This category no longer exists. Refresh the page.")
            actions["delete"](request=request, name=value, db=db)
            return f"Deleted category {value}. Its lists are kept without a category."
        return run(request, db, call)

    return render_uncompleted, render_categories
