"""Recipe pages for the compact daisyUI frontend: shopping list, ingredient report, pantry and history.

Page data comes from main.py's shopping_list_data, ingredient_report_data and
pantry_data (shared with the original pages). Every action calls the original
JSON ingredient API in-process through daisyui_test_list.json_request, so
journal entries and validation are unchanged.
"""
import json
import re
from datetime import datetime

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from daisyui_test_list import json_request
from models import IngredientGroup, List, Todo
from daisyui_test_http import wants_fragment

BACK = re.compile(r"^(shopping|ingredients|history):\d+$|^pantry$")
STATUS = {"ran_out": "Ran out", "likely_low": "Running low", "new": "Never bought", "in_stock": "In stock"}


def stats_text(stats):
    """Plain-language summary of ingredient stats."""
    parts = []
    if stats.get("days_since_bought") is not None:
        days = stats["days_since_bought"]
        parts.append("bought today" if days == 0 else f"bought {days} {'day' if days == 1 else 'days'} ago")
    if stats.get("average_cycle"):
        parts.append(f"usually lasts {stats['average_cycle']:g} days")
    if stats.get("total_bought"):
        parts.append(f"bought {stats['total_bought']} {'time' if stats['total_bought'] == 1 else 'times'}")
    if stats.get("total_ran_out"):
        parts.append(f"ran out {stats['total_ran_out']} {'time' if stats['total_ran_out'] == 1 else 'times'}")
    return " · ".join(parts) or "No purchases recorded yet"


def attach_recipe_routes(router, *, templates, get_db, actions, feedback):
    templates.env.filters["ingredient_stats"] = stats_text

    def base(request):
        return request.scope.get("root_path", "") + "/app"

    def page(request, ctx, message=None, status_code=200, headers=None):
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, ctx["workspace_template"] if partial else "daisyui_test/index.html",
            {**ctx, "message": message, "is_error": status_code >= 400}, status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store", **(headers or {})},
        )

    def recipe_list(db, list_id):
        item = db.get(List, list_id)
        if item is None:
            raise HTTPException(404, "List not found")
        if not item.is_recipe:
            raise HTTPException(400, "This list is not in recipe mode. Turn on recipe mode in the list's options.")
        return item

    def common(request, kind, back, title, url):
        return dict(request=request, prototype_url=base(request), recipe_page=kind, back=back,
                    workspace_template=f"daisyui_test/_recipe_{kind}_workspace.html",
                    page_title=title + "", page_url=base(request) + url, statuses=STATUS)

    def render_shopping(request, db, list_id, message=None, status_code=200):
        item = recipe_list(db, list_id)
        rows = actions["shopping"](db, item)
        groups = {key: [row for row in rows if row["status"] == key] for key in STATUS}
        return page(request, dict(common(request, "shopping", f"shopping:{item.id}", f"Shopping list for {item.name}",
                                         f"/list/{item.id}/shopping"),
                                  list=item, groups=groups, needed=groups["ran_out"] + groups["likely_low"]), message, status_code)

    def render_ingredients(request, db, list_id, message=None, status_code=200):
        item = recipe_list(db, list_id)
        return page(request, dict(common(request, "ingredients", f"ingredients:{item.id}", f"Ingredients in {item.name}",
                                         f"/list/{item.id}/ingredients"),
                                  list=item, ingredients=actions["report"](db, item)), message, status_code)

    def render_pantry(request, db, message=None, status_code=200):
        data = actions["pantry"](db)
        return page(request, dict(common(request, "pantry", "pantry", "Pantry", "/pantry"), **data,
                                  all_groups=db.query(IngredientGroup).order_by(IngredientGroup.canonical_name).all()),
                    message, status_code)

    def render_history(request, db, todo_id, message=None, status_code=200):
        todo = db.get(Todo, todo_id)
        if todo is None:
            raise HTTPException(404, "Ingredient not found")
        sources = todo.ingredient_group.todos if todo.ingredient_group_id else [todo]
        events = sorted([event for source in sources for event in source.ingredient_events],
                        key=lambda event: event.event_date, reverse=True)
        return page(request, dict(common(request, "history", f"history:{todo.id}", f"History of {todo.title}",
                                         f"/ingredient/{todo.id}/history"),
                                  todo=todo, events=events, group=todo.ingredient_group, today_iso=datetime.now().strftime("%Y-%m-%d")),
                    message, status_code)

    def render_back(request, db, back, message=None, status_code=200):
        kind, _, raw = back.partition(":")
        if kind == "shopping":
            return render_shopping(request, db, int(raw), message, status_code)
        if kind == "ingredients":
            return render_ingredients(request, db, int(raw), message, status_code)
        if kind == "history":
            return render_history(request, db, int(raw), message, status_code)
        return render_pantry(request, db, message, status_code)

    def back_url(request, back):
        kind, _, raw = back.partition(":")
        path = {"shopping": f"/list/{raw}/shopping", "ingredients": f"/list/{raw}/ingredients",
                "history": f"/ingredient/{raw}/history"}.get(kind, "/pantry")
        return base(request) + path

    @router.get("/list/{list_id}/shopping", response_class=HTMLResponse)
    def shopping(request: Request, list_id: int, db: Session = Depends(get_db)):
        return render_shopping(request, db, list_id)

    @router.get("/list/{list_id}/ingredients", response_class=HTMLResponse)
    def ingredients(request: Request, list_id: int, db: Session = Depends(get_db)):
        return render_ingredients(request, db, list_id)

    @router.get("/pantry", response_class=HTMLResponse)
    def pantry(request: Request, db: Session = Depends(get_db)):
        return render_pantry(request, db)

    @router.get("/ingredient/{todo_id}/history", response_class=HTMLResponse)
    def history(request: Request, todo_id: int, db: Session = Depends(get_db)):
        return render_history(request, db, todo_id)

    def recipe_todo(db, raw):
        try:
            todo = db.get(Todo, int(raw))
        except (TypeError, ValueError):
            todo = None
        if todo is None or not todo.list or not todo.list.is_recipe:
            raise HTTPException(404, "This ingredient is no longer in a recipe list. Refresh the page.")
        return todo

    def group(db, raw):
        try:
            item = db.get(IngredientGroup, int(raw))
        except (TypeError, ValueError):
            item = None
        if item is None:
            raise HTTPException(404, "This ingredient group no longer exists. Refresh the page.")
        return item

    async def perform(request, db, operation, form):
        """Validate one action and call the original JSON API; return a success message."""
        text = lambda name: str(form.get(name, "")).strip()
        label = {"bought": "bought", "ran_out": "ran out", "reset": "restocked"}
        if operation == "event":
            todo = recipe_todo(db, text("todo_id"))
            kind = text("event_type")
            day = text("event_date")
            if day:
                try:
                    datetime.strptime(day, "%Y-%m-%d")
                except ValueError:
                    raise HTTPException(400, "Choose a valid date.")
            payload = {"event_type": kind, "quantity": 1, "note": text("note"), **({"event_date": day} if day else {})}
            if todo.ingredient_group_id and not day and kind in {"bought", "ran_out"} and text("scope") == "group":
                fn = actions["group_buy"] if kind == "bought" else actions["group_ran_out"]
                result = await fn(group_id=todo.ingredient_group_id, request=json_request(request, {"quantity": 1}), db=db)
            else:
                result = await actions["event"](todo_id=todo.id, request=json_request(request, payload), db=db)
            return result, f"Marked {todo.title} as {label.get(kind, kind)}."
        if operation == "event-delete":
            todo = recipe_todo(db, text("todo_id"))
            sources = todo.ingredient_group.todos if todo.ingredient_group_id else [todo]
            event_ids = {event.id for source in sources for event in source.ingredient_events}
            try:
                event_id = int(text("event_id"))
            except ValueError:
                event_id = None
            if event_id not in event_ids:
                raise HTTPException(404, "This event no longer belongs to the ingredient. Refresh the page.")
            return await actions["event_delete"](event_id=event_id, db=db), "Event deleted."
        if operation in {"bulk-bought", "reset"}:
            item = recipe_list(db, int(text("list_id") or 0))
            if operation == "reset":
                return await actions["reset"](list_id=item.id, request=json_request(request, {"quantity": 1}), db=db), \
                    "Marked every open ingredient as just bought."
            ids = sorted({int(value) for value in form.getlist("todo_ids") if str(value).isdigit()})
            if not ids:
                raise HTTPException(400, "Nothing needs buying.")
            payload = {"todo_ids": ids, "event_type": "bought", "quantity": 1}
            return await actions["bulk"](list_id=item.id, request=json_request(request, payload), db=db), \
                f"Marked {len(ids)} {'item' if len(ids) == 1 else 'items'} as bought."
        if operation == "group-create":
            ids = sorted({int(value) for value in form.getlist("todo_ids") if str(value).isdigit()})
            for raw in ids:
                recipe_todo(db, raw)
            name = text("canonical_name")
            if not name or len(name) > 255:
                raise HTTPException(400, "Enter a name for the ingredient group.")
            payload = {"canonical_name": name, "todo_ids": ids}
            return await actions["group_create"](request=json_request(request, payload), db=db), f"Grouped {len(ids)} ingredients as {name}."
        if operation == "group-link":
            todo, target = recipe_todo(db, text("todo_id")), group(db, text("group_id"))
            payload = {"group_id": target.id, "todo_id": todo.id}
            return await actions["group_link"](request=json_request(request, payload), db=db), f"Added {todo.title} to {target.canonical_name}."
        if operation == "group-unlink":
            todo = recipe_todo(db, text("todo_id"))
            return await actions["group_unlink"](request=json_request(request, {"todo_id": todo.id}), db=db), \
                f"Removed {todo.title} from its group."
        if operation == "group-rename":
            target, name = group(db, text("group_id")), text("canonical_name")
            if not name or len(name) > 255:
                raise HTTPException(400, "Enter a name for the ingredient group.")
            payload = {"group_id": target.id, "canonical_name": name}
            return await actions["group_rename"](request=json_request(request, payload), db=db), f"Renamed the group to {name}."
        if operation in {"group-bought", "group-ran-out"}:
            target = group(db, text("group_id"))
            fn = actions["group_buy"] if operation == "group-bought" else actions["group_ran_out"]
            result = await fn(group_id=target.id, request=json_request(request, {"quantity": 1}), db=db)
            return result, f"Marked {target.canonical_name} as {'bought' if operation == 'group-bought' else 'ran out'}."
        raise HTTPException(404, "Unknown recipe action.")

    @router.post("/recipes/do/{operation}", response_class=HTMLResponse)
    async def operate(request: Request, operation: str, db: Session = Depends(get_db)):
        form = await request.form()
        back = str(form.get("back", ""))
        if not BACK.match(back):
            return feedback(request, db, "user", None, "The form is invalid. Reload the page and try again.")
        try:
            result, message = await perform(request, db, operation, form)
        except HTTPException as exc:
            db.rollback()
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        if result.status_code >= 400:
            db.rollback()
            detail = json.loads(result.body).get("error", "The action could not be completed.")
            return feedback(request, db, "user", None, detail, result.status_code)
        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(back_url(request, back), status_code=303)
        try:
            return render_back(request, db, back, message)
        except HTTPException:
            # The page it came from no longer applies (for example, an emptied group); show the pantry.
            response = render_pantry(request, db, message)
            response.headers["HX-Push-Url"] = base(request) + "/pantry"
            return response

    return render_back
