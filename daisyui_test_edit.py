"""List, task and note editors for the compact daisyUI frontend."""
from datetime import datetime, timedelta
import re
from urllib.parse import urlencode, urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from markupsafe import Markup
from sqlalchemy.orm import Session

from daisyui_test_list import detail_selection
from daisyui_test_search import results_location
from models import Hashtag, Link, List, Note, Todo
from daisyui_test_http import wants_fragment


def plain_note_text(value):
    return re.sub(r'<a\s+[^>]*>(.*?)</a>', r'\1', value, flags=re.IGNORECASE | re.DOTALL)


def render_note_text(value):
    """Rebuild note links while escaping all other stored content."""
    plain = plain_note_text(value)
    parts, offset = [], 0
    for match in re.finditer(r'https?://\S+', plain):
        parts.append(plain[offset:match.start()])
        parts.append(Markup('<a href="{0}" target="_blank" rel="noopener noreferrer">{0}</a>').format(match.group()))
        offset = match.end()
    parts.append(plain[offset:])
    return Markup('').join(parts)


def attach_edit_routes(router, *, templates, get_db, actions, render_destination):
    models = {"list": List, "todo": Todo, "note": Note}
    templates.env.filters["daisy_note"] = render_note_text

    def context(request, db, kind, item_id, sort, folder_id, view_list_id, back):
        model = models.get(kind)
        item = db.get(model, item_id) if model else None
        if item is None:
            raise HTTPException(404, "Item not found")
        if back not in {"home", "list", "results"}:
            raise HTTPException(400, "Invalid return view")
        if view_list_id is not None and item_id not in detail_selection(db, view_list_id).get(kind, set()):
            raise HTTPException(400, "This item is no longer in the originating list.")
        own_list = item.id if kind == "list" else item.list_id
        if own_list is None and kind == "note" and item.todo:
            own_list = item.todo.list_id
        target_list = (view_list_id or own_list) if back == "list" else None
        navigation = {"sort": sort}
        if folder_id is not None:
            navigation["folder_id"] = folder_id
        if target_list is not None and request.query_params.get("highlight"):
            try:
                navigation["highlight"] = int(request.query_params["highlight"])
            except ValueError:
                raise HTTPException(400, "Invalid highlighted task")
        params = {**navigation, "back": back}
        if view_list_id is not None or target_list is not None:
            params["view_list_id"] = view_list_id if view_list_id is not None else target_list
        return_path = f"/list/{target_list}" if target_list is not None else ""
        render_params = navigation
        if back == "results":
            if request.query_params.get("view") not in {"search", "hashtag", "smart"}:
                raise HTTPException(400, "Invalid return view")
            return_path, navigation, render_params = results_location(request, request.query_params["view"], sort, folder_id)
            params = {**render_params, "back": back}
        base = request.scope.get("root_path", "") + "/app"
        query = "?" + urlencode(params)
        values = {"name": item.name or ""} if kind != "todo" else {"title": item.title, "note": item.note or "", "color": item.color or "", "use_color": bool(item.color)}
        if kind == "note":
            values["text"] = plain_note_text(item.text)
        else:
            values.update(hashtags=" ".join("#" + tag.name for tag in item.hashtags),
                          is_delayed=bool(item.delayed_until),
                          delayed_until=item.delayed_until.strftime("%Y-%m-%d") if item.delayed_until else "")
        if kind == "todo":
            values.update(alarm_mode="range" if item.alarm_start else "none",
                          alarm_start_range=item.alarm_start.strftime("%Y-%m-%dT%H:%M") if item.alarm_start else "",
                          alarm_end_range=item.alarm_end.strftime("%Y-%m-%dT%H:%M") if item.alarm_end else "",
                          alarm_target_time="", alarm_minutes="120")
            if item.alarm_start and item.alarm_end:
                middle = item.alarm_start + (item.alarm_end - item.alarm_start) / 2
                values.update(alarm_target_time=middle.strftime("%Y-%m-%dT%H:%M"),
                              alarm_minutes=str(max(0, int((item.alarm_end - item.alarm_start).total_seconds() / 120))))
        return dict(
            request=request, item=item, item_kind=kind, item_label="task" if kind == "todo" else kind,
            item_title=item.title if kind == "todo" else item.name or "Untitled note",
            prototype_url=base, editor_url=f"{base}/{kind}/{item_id}/edit", query=query,
            return_list_id=target_list, return_params=navigation, render_params=render_params, return_path=return_path,
            return_url=base + return_path + "?" + urlencode(navigation),
            all_hashtags=db.query(Hashtag).order_by(Hashtag.name).all(),
            editor_page=True, workspace_template="daisyui_test/_edit_workspace.html",
            values=values,
            picker_color=item.color if kind == "todo" and item.color and re.fullmatch(r"#[0-9a-fA-F]{6}", item.color) else "#6fe0b1",
        )

    def render(request, ctx, message=None, status_code=200):
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, ctx["workspace_template"] if partial else "daisyui_test/index.html",
            {**ctx, "message": message, "is_error": status_code >= 400}, status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store"},
        )

    def error(request, ctx, exc):
        if request.headers.get("HX-Request") == "true":
            return templates.TemplateResponse(request, "daisyui_test/_feedback.html",
                {"message": str(exc.detail), "is_error": True}, status_code=exc.status_code,
                headers={"HX-Retarget": "#page-feedback", "HX-Reswap": "innerHTML", "Cache-Control": "no-store"})
        return render(request, ctx, str(exc.detail), exc.status_code)

    def destination(request, db, ctx, sort, folder_id, message, *, deleted=False):
        list_id = None if deleted and ctx["item_kind"] == "list" else ctx["return_list_id"]
        params = ctx["return_params"]
        path = "" if list_id is None and ctx["return_list_id"] is not None else ctx["return_path"]
        url = ctx["prototype_url"] + path + "?" + urlencode(params)
        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(url, status_code=303)
        # Render the destination using the same authenticated session, with its
        # canonical route context rather than the editor's return parameters.
        scope = dict(request.scope)
        scope.update(path_params={"list_id": list_id} if list_id is not None else {},
                     query_string=urlencode(ctx["render_params"] if list_id is None else params).encode())
        response = render_destination(Request(scope), db, sort, folder_id, message)
        response.headers["HX-Push-Url"] = url
        return response

    def text(form, name, *, required=False):
        if name not in form:
            if required:
                raise HTTPException(400, "The form is incomplete. Reload the editor and try again.")
            return ""
        value = form[name]
        if not isinstance(value, str):
            raise HTTPException(400, "Invalid form value")
        return value

    def checked(form, name):
        value = text(form, name)
        if value not in {"", "false", "true", "on", "0", "1"}:
            raise HTTPException(400, "Invalid checkbox value")
        return value in {"true", "on", "1"}

    def defer_values(form):
        enabled = checked(form, "is_delayed")
        value = text(form, "delayed_until")
        if enabled:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, "Choose a valid deferral date.")
        return dict(is_delayed=enabled, delayed_until=value if enabled else "")

    def alarm_values(form):
        mode = text(form, "alarm_mode", required=True)
        start, end = text(form, "alarm_start_range"), text(form, "alarm_end_range")
        target = text(form, "alarm_target_time")
        try:
            minutes = int(text(form, "alarm_minutes") or "0")
            if mode == "range":
                if datetime.strptime(end, "%Y-%m-%dT%H:%M") < datetime.strptime(start, "%Y-%m-%dT%H:%M"):
                    raise ValueError
            elif mode == "tolerance":
                if not 0 <= minutes <= 525600:
                    raise ValueError
                instant = datetime.strptime(target, "%Y-%m-%dT%H:%M")
                instant - timedelta(minutes=minutes)
                instant + timedelta(minutes=minutes)
            elif mode != "none":
                raise ValueError
        except (ValueError, OverflowError):
            raise HTTPException(400, "Enter a valid alarm range, or a target time and a nonnegative tolerance up to 525600 minutes.")
        return dict(alarm_mode=mode, alarm_start_range=start, alarm_end_range=end,
                    alarm_target_time=target, alarm_minutes=minutes)

    @router.get("/{kind}/{item_id}/edit", response_class=HTMLResponse)
    def edit(request: Request, kind: str, item_id: int, sort: str = "user", folder_id: int | None = None,
             view_list_id: int | None = None, back: str = "list", db: Session = Depends(get_db)):
        return render(request, context(request, db, kind, item_id, sort, folder_id, view_list_id, back))

    @router.post("/{kind}/{item_id}/edit", response_class=HTMLResponse)
    async def save(request: Request, kind: str, item_id: int, sort: str = "user", folder_id: int | None = None,
                   view_list_id: int | None = None, back: str = "list", db: Session = Depends(get_db)):
        ctx = context(request, db, kind, item_id, sort, folder_id, view_list_id, back)
        try:
            form = await request.form()
            ctx["values"].update({key: value for key, value in form.items() if isinstance(value, str)})
            ctx["values"].update(is_delayed=checked(form, "is_delayed"), use_color=checked(form, "use_color"))
            if kind == "list":
                name = text(form, "name", required=True).strip()
                if not name:
                    raise HTTPException(400, "Enter a list name.")
                values = dict(name=name, hashtags=text(form, "hashtags", required=True), **defer_values(form))
                actions[kind](list_id=item_id, db=db, **values)
            elif kind == "todo":
                title = text(form, "title", required=True).strip()
                if not title:
                    raise HTTPException(400, "Enter a task title.")
                color = text(form, "color").strip() if checked(form, "use_color") else ""
                if checked(form, "use_color") and not (re.fullmatch(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", color) or color == ctx["item"].color):
                    raise HTTPException(400, "Choose a hex colour such as #6fe0b1, or disable custom colour.")
                values = dict(title=title, note=text(form, "note", required=True), color=color,
                              hashtags=text(form, "hashtags", required=True), **defer_values(form), **alarm_values(form))
                actions[kind](todo_id=item_id, db=db, **values)
            else:
                value = text(form, "text", required=True)
                if not value.strip():
                    raise HTTPException(400, "Enter some note text.")
                actions[kind](request=request, note_id=item_id, text=value,
                              name=text(form, "name", required=True).strip() or None, db=db)
            return destination(request, db, ctx, sort, folder_id, "Changes saved.")
        except HTTPException as exc:
            return error(request, ctx, exc)

    @router.post("/{kind}/{item_id}/edit/links", response_class=HTMLResponse)
    async def add_link(request: Request, kind: str, item_id: int, sort: str = "user", folder_id: int | None = None,
                       view_list_id: int | None = None, back: str = "list", db: Session = Depends(get_db)):
        ctx = context(request, db, kind, item_id, sort, folder_id, view_list_id, back)
        try:
            form = await request.form()
            url = text(form, "url", required=True).strip()
            try:
                parsed = urlsplit(url)
                valid = parsed.scheme in {"http", "https", "ftp"} and bool(parsed.hostname)
                valid = valid or parsed.scheme == "mailto" and bool(parsed.path) and "@" in parsed.path
                if not valid:
                    raise ValueError
            except ValueError:
                raise HTTPException(400, "Enter a valid http, https, ftp, or mailto URL.")
            parents = dict(list_id=None, todo_id=None, note_id=None)
            parents[kind + "_id"] = item_id
            actions["create_link"](url=url, title=text(form, "title"), db=db, **parents)
            if request.headers.get("HX-Request") != "true":
                return RedirectResponse(ctx["editor_url"] + ctx["query"], status_code=303)
            return render(request, ctx, "Link added.")
        except HTTPException as exc:
            return error(request, ctx, exc)

    @router.post("/{kind}/{item_id}/edit/links/{link_id}/delete", response_class=HTMLResponse)
    def remove_link(request: Request, kind: str, item_id: int, link_id: int, sort: str = "user", folder_id: int | None = None,
                    view_list_id: int | None = None, back: str = "list", db: Session = Depends(get_db)):
        ctx = context(request, db, kind, item_id, sort, folder_id, view_list_id, back)
        try:
            link = db.get(Link, link_id)
            if link is None or getattr(link, kind + "_id") != item_id:
                raise HTTPException(404, "This link does not belong to the current item.")
            actions["delete_link"](link_id=link_id, db=db)
            if request.headers.get("HX-Request") != "true":
                return RedirectResponse(ctx["editor_url"] + ctx["query"], status_code=303)
            return render(request, ctx, "Link removed.")
        except HTTPException as exc:
            return error(request, ctx, exc)

    @router.post("/{kind}/{item_id}/edit/delete", response_class=HTMLResponse)
    def delete(request: Request, kind: str, item_id: int, sort: str = "user", folder_id: int | None = None,
               view_list_id: int | None = None, back: str = "list", db: Session = Depends(get_db)):
        ctx = context(request, db, kind, item_id, sort, folder_id, view_list_id, back)
        kwargs = {kind + "_id": item_id, "db": db}
        if kind == "note":
            kwargs.update(request=request, return_to=None)
        try:
            actions["delete_" + kind](**kwargs)
            return destination(request, db, ctx, sort, folder_id, "Item deleted.", deleted=True)
        except HTTPException as exc:
            return error(request, ctx, exc)
