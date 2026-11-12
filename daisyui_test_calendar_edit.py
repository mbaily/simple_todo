"""Calendar event editor and event groups for the compact daisyUI frontend.

Saving uses main.save_calendar_event (shared with the original routes). The
recurrence builder produces the same rules as the original page's JavaScript
builder; rules it cannot represent stay editable as text, so saving never
drops parts such as BYMONTHDAY or COUNT.
"""
from datetime import date, datetime, timedelta
import re
from urllib.parse import urlencode

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from daisyui_test_calendar import navigation
from models import CalendarEvent, EventGroup, Hashtag, Note, utcnow
from daisyui_test_http import wants_fragment

FREQUENCIES = {"DAILY": "Daily", "WEEKLY": "Weekly", "MONTHLY": "Monthly", "YEARLY": "Yearly"}
WEEKDAYS = [("MO", "Mon"), ("TU", "Tue"), ("WE", "Wed"), ("TH", "Thu"), ("FR", "Fri"), ("SA", "Sat"), ("SU", "Sun")]
MAX_TITLE = 500
MAX_NOTE = 10000


def rule_values(rule):
    """Split a stored rule into builder fields, or fall back to custom text."""
    values = dict(repeat="", interval="1", byday=set(), until="", custom_rule="")
    if not rule:
        return values
    parts = {}
    for part in rule.upper().removeprefix("RRULE:").split(";"):
        if "=" not in part:
            return {**values, "repeat": "custom", "custom_rule": rule}
        key, value = part.split("=", 1)
        parts[key.strip()] = value.strip()
    simple = (
        set(parts) <= {"FREQ", "INTERVAL", "BYDAY", "UNTIL"} and parts.get("FREQ") in FREQUENCIES
        and re.fullmatch(r"[1-9][0-9]{0,2}", parts.get("INTERVAL", "1"))
        and ("BYDAY" not in parts or (parts["FREQ"] == "WEEKLY" and all(day in dict(WEEKDAYS) for day in parts["BYDAY"].split(","))))
        and ("UNTIL" not in parts or re.fullmatch(r"\d{8}(T\d{6}Z?)?", parts["UNTIL"]))
    )
    if not simple:
        return {**values, "repeat": "custom", "custom_rule": rule}
    until = parts.get("UNTIL", "")
    return dict(repeat=parts["FREQ"], interval=parts.get("INTERVAL", "1"),
                byday=set(parts["BYDAY"].split(",")) if parts.get("BYDAY") else set(),
                until=f"{until[:4]}-{until[4:6]}-{until[6:8]}" if until else "", custom_rule="")


def build_rule(values, start_date):
    """Build a rule the same way as the original page's builder."""
    repeat = values["repeat"]
    if repeat == "custom":
        return values["custom_rule"].strip()
    if not repeat:
        return ""
    if repeat not in FREQUENCIES:
        raise ValueError("Choose how often the event repeats.")
    if not re.fullmatch(r"[1-9][0-9]{0,2}", values["interval"] or "1"):
        raise ValueError("Repeat every 1 to 999 periods.")
    parts = [f"FREQ={repeat}"]
    if values["interval"] not in {"", "1"}:
        parts.append(f"INTERVAL={int(values['interval'])}")
    if repeat == "WEEKLY":
        days = [code for code, _ in WEEKDAYS if code in values["byday"]]
        if not days and start_date:
            days = [WEEKDAYS[start_date.weekday()][0]]
        if days:
            parts.append("BYDAY=" + ",".join(days))
    if values["until"]:
        try:
            until = datetime.strptime(values["until"], "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Choose a valid end date for the repeat.")
        if start_date and until < start_date:
            raise ValueError("The repeat end date must be on or after the event date.")
        parts.append(f"UNTIL={until:%Y%m%d}T235959")
    return ";".join(parts)


def event_values(event, day=None):
    if event is None:
        return dict(title="", start_at=day or date.today().isoformat(), start_time="", duration_minutes="", note="",
                    hashtags="", color="", use_color=False, event_group_id="", important_if_late=False,
                    **rule_values(""))
    return dict(
        title=event.title, start_at=event.start_at.strftime("%Y-%m-%d"),
        start_time="" if event.all_day else event.start_at.strftime("%H:%M"),
        duration_minutes=str(event.duration_minutes or ""), note=event.note or "",
        hashtags=" ".join("#" + tag.name for tag in event.hashtags), color=event.color or "",
        use_color=bool(event.color), event_group_id=str(event.event_group_id or ""),
        important_if_late=bool(event.important_if_late), **rule_values(event.rrule),
    )


def attach_calendar_edit_routes(router, *, templates, get_db, actions, feedback, render_calendar):
    def base(request):
        return request.scope.get("root_path", "") + "/app"

    def page(request, ctx, message=None, status_code=200, headers=None):
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, ctx["workspace_template"] if partial else "daisyui_test/index.html",
            {**ctx, "message": message, "is_error": status_code >= 400}, status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store", **(headers or {})},
        )

    def occurrence_param(request):
        value = request.query_params.get("occurrence_at") or ""
        if value:
            try:
                return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, "Invalid occurrence date")
        return ""

    def render_editor(request, db, event, values, message=None, status_code=200, extra=None):
        nav = navigation(request)
        occurrence = occurrence_param(request)
        query = {**nav, **({"occurrence_at": occurrence} if occurrence else {})}
        url = base(request)
        editor_url = url + (f"/calendar/event/{event.id}/edit" if event else "/calendar/event/new")
        return page(request, dict(
            request=request, prototype_url=url, calendar_page=True, editor_page=False,
            workspace_template="daisyui_test/_calendar_event_workspace.html",
            page_title=("Edit " + event.title if event else "New event") + "",
            page_url=editor_url + ("?" + urlencode(query) if query else ""),
            event=event, values=values, query="?" + urlencode(query) if query else "",
            editor_url=editor_url, return_url=url + "/calendar" + ("?" + urlencode(nav) if nav else ""),
            occurrence_at=occurrence, frequencies=FREQUENCIES, weekdays=WEEKDAYS,
            all_hashtags=db.query(Hashtag).order_by(Hashtag.name).all(),
            event_groups=db.query(EventGroup).order_by(EventGroup.name).all(),
            today=date.today().isoformat(), **(extra or {}),
        ), message, status_code)

    def back_to_calendar(request, db, message):
        nav = navigation(request)
        url = base(request) + "/calendar" + ("?" + urlencode(nav) if nav else "")
        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(url, status_code=303)
        scope = dict(request.scope)
        scope.update(path_params={}, query_string=urlencode({**nav, "view": "calendar"}).encode())
        response = render_calendar(Request(scope), db, message=message)
        response.headers["HX-Push-Url"] = url
        return response

    def error(request, db, event, values, message, status_code=400):
        if request.headers.get("HX-Request") == "true":
            return feedback(request, db, "user", None, message, status_code)
        return render_editor(request, db, event, values, message, status_code)

    def existing(db, event_id):
        event = db.get(CalendarEvent, event_id)
        if event is None:
            raise HTTPException(404, "Event not found")
        return event

    async def save(request, db, event):
        form = await request.form()

        def text(name):
            value = form.get(name, "")
            if not isinstance(value, str):
                raise HTTPException(400, "Invalid form value")
            return value

        values = dict(
            title=text("title").strip(), start_at=text("start_at").strip(), start_time=text("start_time").strip(),
            duration_minutes=text("duration_minutes").strip(), note=text("note"), hashtags=text("hashtags"),
            color=text("color").strip(), use_color=text("use_color") in {"true", "on", "1"},
            event_group_id=text("event_group_id").strip(), important_if_late=text("important_if_late") in {"true", "on", "1"},
            repeat=text("repeat").strip().upper() if text("repeat").strip() != "custom" else "custom",
            interval=text("interval").strip() or "1", byday=set(form.getlist("byday")), until=text("until").strip(),
            custom_rule=text("custom_rule").strip(),
        )
        try:
            if not values["title"]:
                raise ValueError("Enter a title for the event.")
            if len(values["title"]) > MAX_TITLE:
                raise ValueError(f"Event titles can be at most {MAX_TITLE} characters.")
            if len(values["note"]) > MAX_NOTE:
                raise ValueError(f"Event notes can be at most {MAX_NOTE} characters.")
            if values["use_color"] and not re.fullmatch(r"#[0-9a-fA-F]{6}", values["color"]):
                raise ValueError("Choose a colour such as #6fe0b1, or turn off the custom colour.")
            try:
                start_date = datetime.strptime(values["start_at"], "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("Choose a valid date for the event.")
            if values["start_time"] and not re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", values["start_time"]):
                raise ValueError("Enter a start time such as 09:30, or leave it blank for an all-day event.")
            rule = build_rule(values, start_date)
            saved = actions["save"](
                db, event, title=values["title"], start_at=values["start_at"], start_time=values["start_time"],
                duration_minutes=values["duration_minutes"], rrule=rule, note=values["note"], hashtags=values["hashtags"],
                event_group_id=values["event_group_id"], important_if_late="1" if values["important_if_late"] else None,
                color=values["color"] if values["use_color"] else "",
            )
        except ValueError as exc:
            db.rollback()
            return error(request, db, event, values, str(exc))
        return back_to_calendar(request, db, f"Saved {saved.title}.")

    @router.get("/calendar/event/new", response_class=HTMLResponse)
    def new(request: Request, db: Session = Depends(get_db)):
        day = request.query_params.get("date") or ""
        if day:
            try:
                day = datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, "Invalid date")
        return render_editor(request, db, None, event_values(None, day))

    @router.post("/calendar/events", response_class=HTMLResponse)
    async def create(request: Request, db: Session = Depends(get_db)):
        return await save(request, db, None)

    @router.get("/calendar/event/{event_id}/edit", response_class=HTMLResponse)
    def edit(request: Request, event_id: int, db: Session = Depends(get_db)):
        event = existing(db, event_id)
        return render_editor(request, db, event, event_values(event))

    @router.post("/calendar/event/{event_id}/edit", response_class=HTMLResponse)
    async def update(request: Request, event_id: int, db: Session = Depends(get_db)):
        event = db.get(CalendarEvent, event_id)
        if event is None:
            return feedback(request, db, "user", None, "This event no longer exists.", 404)
        return await save(request, db, event)

    @router.post("/calendar/event/{event_id}/delete", response_class=HTMLResponse)
    def delete(request: Request, event_id: int, db: Session = Depends(get_db)):
        event = db.get(CalendarEvent, event_id)
        if event is None:
            return feedback(request, db, "user", None, "This event no longer exists.", 404)
        title = event.title
        actions["delete"](event_id=event_id, view_month="", view_week="", db=db)
        return back_to_calendar(request, db, f"Deleted {title}.")

    @router.post("/calendar/event/{event_id}/duplicate", response_class=HTMLResponse)
    def duplicate(request: Request, event_id: int, target_date: str = Form(""), copy_mode: str = Form("details"),
                  source_occurrence: str = Form(""), db: Session = Depends(get_db)):
        event = db.get(CalendarEvent, event_id)
        if event is None:
            return feedback(request, db, "user", None, "This event no longer exists.", 404)
        try:
            day = datetime.strptime(target_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            return feedback(request, db, "user", None, "Choose a valid date for the copy.")
        if copy_mode not in {"details", "occurrence"}:
            return feedback(request, db, "user", None, "Choose how to copy the event.")
        source = None
        if copy_mode == "occurrence":
            if not event.rrule:
                return feedback(request, db, "user", None, "This event no longer repeats. Use the basic copy instead.")
            try:
                source = datetime.strptime(source_occurrence, "%Y-%m-%d").date()
            except ValueError:
                return feedback(request, db, "user", None, "Choose the occurrence to copy from the calendar.")
        try:
            copy = actions["duplicate"](db, event, day, **({"source_occurrence": source} if source else {}))
        except ValueError as exc:
            return feedback(request, db, "user", None, str(exc))
        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(base(request) + f"/calendar/event/{event.id}/edit", status_code=303)
        message = f"Copied occurrence from {source:%-d %b %Y} to {day:%-d %b %Y}, including its notes." if source else f"Copied to {day:%-d %b %Y}."
        return render_editor(request, db, event, event_values(event), message,
                             extra={"copied_event": copy})

    @router.post("/calendar/event/{event_id}/end-recurrence", response_class=HTMLResponse)
    def end_recurrence(request: Request, event_id: int, db: Session = Depends(get_db)):
        event = db.get(CalendarEvent, event_id)
        if event is None:
            return feedback(request, db, "user", None, "This event no longer exists.", 404)
        if not event.rrule:
            return feedback(request, db, "user", None, "This event does not repeat.")
        yesterday = date.today() - timedelta(days=1)
        if yesterday < event.start_at.date():
            return feedback(request, db, "user", None, "This event has not started yet. Delete it instead of ending it.")
        parts = [part for part in event.rrule.removeprefix("RRULE:").split(";") if not part.upper().startswith(("UNTIL=", "COUNT="))]
        rule = ";".join(parts + [f"UNTIL={yesterday:%Y%m%d}T235959"])
        try:
            event.rrule = actions["validate_rule"](rule, event.start_at)
        except ValueError as exc:
            return feedback(request, db, "user", None, str(exc))
        event.modified_at = utcnow()
        actions["journal"](db, "Updated Calendar Event", f"Ended recurrence of '{event.title}'", "calendar_event", event.id)
        db.commit()
        return render_editor(request, db, event, event_values(event), f"Ended the repeat after {yesterday:%-d %b %Y}.")

    @router.post("/calendar/event/{event_id}/notes", response_class=HTMLResponse)
    def add_note(request: Request, event_id: int, text: str = Form(""), name: str = Form(""),
                 occurrence_only: bool = Form(False), occurrence_at: str = Form(""), db: Session = Depends(get_db)):
        event = db.get(CalendarEvent, event_id)
        if event is None:
            return feedback(request, db, "user", None, "This event no longer exists.", 404)
        if not text.strip():
            return feedback(request, db, "user", None, "Enter some note text.")
        if len(text) > MAX_NOTE:
            return feedback(request, db, "user", None, f"Notes can be at most {MAX_NOTE} characters.")
        when = ""
        if occurrence_only:
            try:
                when = datetime.strptime(occurrence_at, "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                return feedback(request, db, "user", None, "Choose the occurrence date for this note.")
        actions["create_note"](request=request, list_id=None, text=text.strip(), name=name.strip() or None, todo_id=None,
                               calendar_event_id=event_id, occurrence_at=when or None, return_to=None, db=db)
        db.refresh(event)
        return render_editor(request, db, event, event_values(event), "Note added.")

    @router.post("/calendar/event/{event_id}/notes/{note_id}/delete", response_class=HTMLResponse)
    def delete_note(request: Request, event_id: int, note_id: int, db: Session = Depends(get_db)):
        event = db.get(CalendarEvent, event_id)
        note = db.get(Note, note_id)
        if event is None or note is None or note.calendar_event_id != event_id:
            return feedback(request, db, "user", None, "This note no longer belongs to the event.", 404)
        actions["delete_note"](request=request, note_id=note_id, return_to=None, db=db)
        db.refresh(event)
        return render_editor(request, db, event, event_values(event), "Note deleted.")

    # ---- Event groups ----
    def render_groups(request, db, message=None, status_code=200):
        url = base(request)
        counts = dict(db.query(CalendarEvent.event_group_id, func.count(CalendarEvent.id))
                      .filter(CalendarEvent.event_group_id != None).group_by(CalendarEvent.event_group_id).all())
        return page(request, dict(
            request=request, prototype_url=url, calendar_page=True, query="?view=event-groups",
            workspace_template="daisyui_test/_event_groups_workspace.html", page_title="Event groups",
            page_url=url + "/calendar/groups", groups=db.query(EventGroup).order_by(EventGroup.name).all(), counts=counts,
        ), message, status_code)

    @router.get("/calendar/groups", response_class=HTMLResponse)
    def groups(request: Request, db: Session = Depends(get_db)):
        return render_groups(request, db)

    @router.post("/calendar/groups", response_class=HTMLResponse)
    def create_group(request: Request, name: str = Form(""), db: Session = Depends(get_db)):
        name = name.strip()
        if not name or len(name) > 255:
            return feedback(request, db, "user", None, "Enter a group name of up to 255 characters.")
        if db.query(EventGroup).filter(EventGroup.name == name).first():
            return feedback(request, db, "user", None, f"The group {name} already exists.", 409)
        actions["create_group"](name=name, db=db)
        return groups_result(request, db, f"Created group {name}.")

    @router.post("/calendar/groups/{group_id}/delete", response_class=HTMLResponse)
    def delete_group(request: Request, group_id: int, db: Session = Depends(get_db)):
        group = db.get(EventGroup, group_id)
        if group is None:
            return feedback(request, db, "user", None, "This group no longer exists.", 404)
        name = group.name
        actions["delete_group"](group_id=group_id, db=db)
        return groups_result(request, db, f"Deleted group {name}. Its events were kept.")

    def groups_result(request, db, message):
        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(base(request) + "/calendar/groups", status_code=303)
        return render_groups(request, db, message)

    return render_groups
