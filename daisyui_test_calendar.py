"""Calendar views for the compact daisyUI frontend: agenda, month and week.

The layout data comes from main.calendar_context, shared with the original
calendar page, and view, filter and sort are the same stored preferences.
Mutations carry ``view=calendar`` plus the displayed month, week or offset so
the router re-renders the same period afterwards.
"""
from datetime import datetime, timedelta
import re
from urllib.parse import urlencode

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from models import CalendarDay, CalendarEvent, CalendarEventCompletion, EventGroup, utcnow
from daisyui_test_http import wants_fragment

MAX_DAY_NOTE = 2000


def calendar_view(request):
    return request.query_params.get("view") == "calendar"


def navigation(request):
    """Validated period parameters (only one of week, month or offset)."""
    params = request.query_params
    if params.get("week"):
        try:
            return {"week": datetime.strptime(params["week"], "%Y-%m-%d").strftime("%Y-%m-%d")}
        except ValueError:
            raise HTTPException(400, "Invalid calendar week")
    if params.get("month"):
        try:
            return {"month": datetime.strptime(params["month"], "%Y-%m").strftime("%Y-%m")}
        except ValueError:
            raise HTTPException(400, "Invalid calendar month")
    try:
        offset = int(params.get("offset") or 0)
    except ValueError:
        raise HTTPException(400, "Invalid calendar offset")
    if not -600 <= offset <= 600:
        raise HTTPException(400, "Invalid calendar offset")
    return {"offset": offset} if offset else {}


def parse_day(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid calendar date")


def note_previews(row, limit=100):
    """One preview and edit-page anchor per note that applies to this date."""
    event, day = row["event"], row["occurrence_at"].date()
    previews = []

    def add(text, anchor):
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if text:
            previews.append({"text": text if len(text) <= limit else text[:limit - 1].rstrip() + "…",
                             "anchor": anchor})

    add(event.note or "", "event-note")
    for note in sorted(event.notes, key=lambda note: (note.created_at, note.id)):
        if note.occurrence_at is None or note.occurrence_at.date() == day:
            add(f"{note.name}: {note.text}" if note.name else note.text, f"event-note-{note.id}")
    return previews


def attach_calendar_routes(router, *, templates, get_db, actions, feedback, success):
    def base(request):
        return request.scope.get("root_path", "") + "/app"

    def render_calendar(request, db, sort="user", folder_id=None, message=None, status_code=200):
        nav = navigation(request)
        selected_day = request.query_params.get("day") or None
        if selected_day:
            selected_day = parse_day(selected_day).strftime("%Y-%m-%d")
        context = actions["context"](db, nav.get("offset", 0), nav.get("month"), nav.get("week"))
        view = context["view"]
        # Keep only the parameter that matches the active layout.
        if view == "week":
            nav = {"week": context["current_week_str"]}
        elif view == "month":
            nav = {"month": context["current_month_str"]}
        else:
            nav = {"offset": context["offset"]} if context["offset"] else {}
        context["selected"] = None
        if view == "list":
            selected_day = None
            contains_today = context["range_start"] <= context["today_local"] <= context["range_end"]
            days = []
            for row in context["occurrences"]:
                key = row["occurrence_at"].date()
                if not days or days[-1]["date"] != key:
                    days.append({"date": key, "rows": []})
                days[-1]["rows"].append(row)
            context["agenda_days"] = days
            prev_nav = {"offset": context["prev_offset"]} if context["prev_offset"] else {}
            next_nav = {"offset": context["next_offset"]} if context["next_offset"] else {}
            start_format = "%-d %b" if context["range_start"].year == context["range_end"].year else "%-d %b %Y"
            title = f"{context['range_start']:{start_format}} – {context['range_end']:%-d %b %Y}"
        elif view == "month":
            contains_today = context["current_month_str"] == context["today_local"].strftime("%Y-%m")
            prev_nav, next_nav = {"month": context["prev_month_str"]}, {"month": context["next_month_str"]}
            title = f"{context['month_name']} {context['year']}"
            if selected_day and not selected_day.startswith(context["current_month_str"]):
                selected_day = None
            context["selected"] = next((day for week in context["weeks"] for day in week
                                        if day["day"] and day["date"] == selected_day), None)
        else:
            contains_today = context["week_start"] <= context["today_local"] <= context["week_end"]
            prev_nav, next_nav = {"week": context["prev_week_str"]}, {"week": context["next_week_str"]}
            title = f"{context['week_start']:%-d %b} – {context['week_end']:%-d %b %Y}"
            first = context["week_start"].replace(hour=0, minute=0, second=0, microsecond=0)
            states = {state.day.date(): state for state in db.query(CalendarDay).filter(
                CalendarDay.day >= first, CalendarDay.day < first + timedelta(days=7))}
            for day in context["week_days"]:
                day["rows"] = day["all_day"] + sorted(
                    [row for row in day["timed"] if not row["continues_before"]], key=lambda row: row["start_minute"])
                # Day notes and the hide-done preference are shared with the month grid.
                state = states.get(day["date"].date())
                day["note"] = (state.note or "") if state else ""
                day["hide_completed"] = bool(state and state.hide_completed)
                day["remaining"] = sum(not row["completed"] for row in day["rows"])
            # The day dialog takes the same fields as a month cell.
            context["selected"] = next((
                {"date": selected_day, "events": day["rows"], "note": day["note"],
                 "hide_completed": day["hide_completed"], "remaining": day["remaining"]}
                for day in context["week_days"] if f"{day['date']:%Y-%m-%d}" == selected_day), None)
            if not context["selected"]:
                selected_day = None
        if view == "list":
            rows = context["occurrences"]
        elif view == "month":
            rows = [row for week in context["weeks"] for day in week for row in day["events"]]
        else:
            rows = [row for day in context["week_days"] for row in day["rows"]]
        for row in rows:
            row["note_previews"] = note_previews(row)
        url = base(request) + "/calendar"
        mutation = {**nav, "view": "calendar"}
        if selected_day:
            mutation["day"] = selected_day
        context.update(
            request=request, prototype_url=base(request), calendar_page=True,
            workspace_template="daisyui_test/_calendar_workspace.html",
            page_title="Calendar", page_url=url + ("?" + urlencode(nav) if nav else ""),
            query="?" + urlencode(mutation), nav_query=urlencode(nav), calendar_url=url,
            prev_url=url + ("?" + urlencode(prev_nav) if prev_nav else ""),
            next_url=url + ("?" + urlencode(next_nav) if next_nav else ""),
            today_url=url, calendar_contains_today=contains_today, period_title=title, selected_day=selected_day,
            day_label=f"{parse_day(selected_day):%A %-d %B %Y}" if selected_day else "",
            message=message, is_error=status_code >= 400,
        )
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, context["workspace_template"] if partial else "daisyui_test/index.html", context,
            status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store"},
        )

    def respond(request, db, call):
        try:
            message = call()
        except HTTPException as exc:
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        return success(request, db, "user", None, message)

    @router.get("/calendar", response_class=HTMLResponse)
    def calendar(request: Request, db: Session = Depends(get_db)):
        return render_calendar(request, db)

    @router.post("/calendar/view", response_class=HTMLResponse)
    def set_view(request: Request, layout: str = Form(""), db: Session = Depends(get_db)):
        if layout not in {"list", "month", "week"}:
            return feedback(request, db, "user", None, "Choose agenda, month or week.")
        settings = actions["settings"](db)
        settings.calendar_weekly_view = layout == "week"
        settings.calendar_traditional_view = layout == "month"
        db.commit()
        # Open the new layout at the current period rather than the old position.
        if request.headers.get("HX-Request") == "true":
            scope = dict(request.scope)
            scope["query_string"] = b"view=calendar"
            response = render_calendar(Request(scope), db)
            response.headers["HX-Push-Url"] = base(request) + "/calendar"
            return response
        return success(request, db, "user", None, "Calendar layout changed.")

    @router.post("/calendar/filter", response_class=HTMLResponse)
    def set_filter(request: Request, calendar_event_filter: str = Form("all"), db: Session = Depends(get_db)):
        def call():
            valid = {"all", "non_recurring", "recurring"}
            if calendar_event_filter.startswith("group:"):
                try:
                    if db.get(EventGroup, int(calendar_event_filter.removeprefix("group:"))):
                        valid.add(calendar_event_filter)
                except ValueError:
                    pass
            if calendar_event_filter not in valid:
                raise HTTPException(400, "Choose a valid event filter.")
            actions["settings"](db).calendar_event_filter = calendar_event_filter
            db.commit()
            return "Calendar filter updated."
        return respond(request, db, call)

    @router.post("/calendar/sort", response_class=HTMLResponse)
    def set_sort(request: Request, calendar_sort_order: str = Form(""), db: Session = Depends(get_db)):
        def call():
            if calendar_sort_order not in actions["sort_options"]:
                raise HTTPException(400, "Choose a valid sort order.")
            actions["settings"](db).calendar_sort_order = calendar_sort_order
            db.commit()
            return "Calendar sort updated."
        return respond(request, db, call)

    @router.post("/calendar/display/{name}", response_class=HTMLResponse)
    def set_display(request: Request, name: str, enabled: bool = Form(False), db: Session = Depends(get_db)):
        def call():
            if name not in {"calendar_word_wrap", "calendar_show_counts"}:
                raise HTTPException(400, "Choose a valid display option.")
            setattr(actions["settings"](db), name, enabled)
            db.commit()
            return "Calendar display updated."
        return respond(request, db, call)

    @router.post("/calendar/occurrence/{event_id}", response_class=HTMLResponse)
    def occurrence(request: Request, event_id: int, occurrence_at: str = Form(""), completed: bool = Form(False),
                   db: Session = Depends(get_db)):
        def call():
            event = db.get(CalendarEvent, event_id)
            if event is None:
                raise HTTPException(404, "This event no longer exists.")
            try:
                when = datetime.fromisoformat(occurrence_at).replace(tzinfo=None)
            except ValueError:
                raise HTTPException(400, "Invalid occurrence")
            tick = timedelta(microseconds=1)
            if when not in actions["occurrences"](event, when - tick, when + tick):
                raise HTTPException(400, "This occurrence is no longer part of the event. Refresh the calendar.")
            existing = db.query(CalendarEventCompletion).filter_by(event_id=event_id, occurrence_at=when).first()
            # Set the requested state so a retried request cannot flip it back.
            if bool(existing) != completed:
                if existing:
                    db.delete(existing)
                else:
                    db.add(CalendarEventCompletion(event_id=event_id, occurrence_at=when))
                event.modified_at = utcnow()
                actions["journal"](db, "Toggled Calendar Occurrence",
                                   f"Toggled occurrence for event '{event.title}' on {occurrence_at} to {completed}",
                                   "calendar_event", event.id)
                db.commit()
            return "Occurrence completed." if completed else "Occurrence reopened."
        return respond(request, db, call)

    @router.post("/calendar/day/{day}/note", response_class=HTMLResponse)
    def day_note(request: Request, day: str, note: str = Form(""), db: Session = Depends(get_db)):
        def call():
            parse_day(day)
            if len(note) > MAX_DAY_NOTE:
                raise HTTPException(400, f"Day notes can be at most {MAX_DAY_NOTE} characters.")
            actions["day_note"](day=day, note=note, db=db)
            return "Day note saved."
        return respond(request, db, call)

    @router.post("/calendar/day/{day}/hide-completed", response_class=HTMLResponse)
    def day_hide(request: Request, day: str, enabled: bool = Form(False), db: Session = Depends(get_db)):
        def call():
            state = actions["day_state"](db, parse_day(day))
            state.hide_completed = enabled
            db.commit()
            return "Day display updated."
        return respond(request, db, call)

    @router.post("/calendar/day/{day}/event", response_class=HTMLResponse)
    def day_event(request: Request, day: str, title: str = Form(""), start_time: str = Form(""), db: Session = Depends(get_db)):
        """Quick add from a month day panel; the full editor offers everything else."""
        def call():
            date = parse_day(day).strftime("%Y-%m-%d")
            name = title.strip()
            if not name:
                raise HTTPException(400, "Enter a title for the event.")
            if len(name) > 500:
                raise HTTPException(400, "Event titles can be at most 500 characters.")
            time = start_time.strip()
            if time and not re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", time):
                raise HTTPException(400, "Enter a start time such as 09:30, or leave it blank for an all-day event.")
            try:
                actions["save_event"](db, title=name, start_at=date, start_time=time)
            except ValueError as exc:
                db.rollback()
                raise HTTPException(400, str(exc))
            return f"Added {name}{' at ' + time if time else ''}."
        return respond(request, db, call)

    @router.post("/calendar/day/{day}/complete", response_class=HTMLResponse)
    def day_complete(request: Request, day: str, db: Session = Depends(get_db)):
        def call():
            parse_day(day)
            actions["complete_day"](day=day, db=db)
            return "Completed every event on this day."
        return respond(request, db, call)

    return render_calendar
