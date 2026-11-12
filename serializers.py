"""
JSON serialization helpers for the Simple Todo application.

These functions convert SQLAlchemy model instances to JSON-serializable dictionaries.
"""
from models import format_local_date, format_local_datetime
from helpers import generate_dokuwiki_link


def serialize_list(l):
    """Serialize a List model to a dictionary."""
    return {
        "id": l.id,
        "name": l.name,
        "folder_id": l.folder_id,
        "completed": l.completed,
        "pinned": l.pinned,
        "pinned_at": l.pinned_at.isoformat() if l.pinned_at else None,
        "is_collation": l.is_collation,
        "collation_color": l.collation_color,
        "archived": l.archived,
        "show_completed": l.show_completed,
        "sort_ascending": l.sort_ascending,
        "partition_by_datetime_headers": l.partition_by_datetime_headers,
        "intersperse_collated_todos": l.intersperse_collated_todos,
        "manual_sorting": l.manual_sorting,
        "is_recipe": l.is_recipe,
        "show_delayed": l.show_delayed,
        "show_dokuwiki_item_links": l.show_dokuwiki_item_links,
        "dokuwiki_page_enabled": l.dokuwiki_page_enabled,
        "category": l.category,
        "sort_order": getattr(l, "sort_order", 0),
        "delayed_until": l.delayed_until.isoformat() if l.delayed_until else None,
        "created_at": l.created_at.isoformat() if l.created_at else None,
        "modified_at": l.modified_at.isoformat() if l.modified_at else None,
        "hashtag_ids": [h.id for h in l.hashtags],
        "hashtags": [{"id": h.id, "name": h.name} for h in l.hashtags],
        "links": [{"url": link.url, "title": link.title} for link in l.links],
        # Extra fields for UI convenience
        "created_at_formatted": format_local_date(l.created_at) if l.created_at else "",
        "modified_at_formatted": format_local_date(l.modified_at) if l.modified_at else "",
    }


def serialize_todo(t):
    """Serialize a Todo model to a dictionary."""
    return {
        "id": t.id,
        "title": t.title,
        "note": t.note,
        "color": t.color,
        "completed": t.completed,
        "pinned": t.pinned,
        "pinned_at": t.pinned_at.isoformat() if t.pinned_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "modified_at": t.modified_at.isoformat() if t.modified_at else None,
        "list_id": t.list_id,
        "parent_id": t.parent_id,
        "sort_order": t.sort_order,
        "delayed_until": t.delayed_until.isoformat() if t.delayed_until else None,
        "alarm_start": t.alarm_start.isoformat() if t.alarm_start else None,
        "alarm_end": t.alarm_end.isoformat() if t.alarm_end else None,
        "dokuwiki_page_enabled": t.dokuwiki_page_enabled,
        "ingredient_group_id": t.ingredient_group_id,
        "hashtag_ids": [h.id for h in t.hashtags],
        "hashtags": [{"id": h.id, "name": h.name} for h in t.hashtags],
        "links": [{"url": link.url, "title": link.title} for link in t.links],
        "collation_list_ids": [l.id for l in t.collation_lists],
        "has_note": bool(t.note),
        "created_at_formatted": format_local_datetime(t.created_at) if t.created_at else "",
        "modified_at_formatted": format_local_datetime(t.modified_at) if t.modified_at else "",
    }


def serialize_folder(f):
    """Serialize a Folder model to a dictionary."""
    return {
        "id": f.id,
        "name": f.name,
        "parent_id": f.parent_id,
        "sort_order": f.sort_order,
        "is_collapsed": f.is_collapsed,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def serialize_hashtag(h):
    """Serialize a Hashtag model to a dictionary."""
    return {
        "id": h.id,
        "name": h.name,
        "show_completed": h.show_completed,
        "include_list_items": h.include_list_items,
        "hide_completed_list_items": h.hide_completed_list_items,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }


def serialize_calendar_event(ev):
    """Serialize a CalendarEvent model to a dictionary."""
    return {
        "id": ev.id,
        "title": ev.title,
        "start_at": ev.start_at.isoformat() if ev.start_at else None,
        "all_day": ev.all_day,
        "duration_minutes": ev.duration_minutes,
        "rrule": ev.rrule,
        "note": ev.note,
        "color": ev.color,
        "event_group_id": ev.event_group_id,
        "important_if_late": bool(ev.important_if_late),
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
        "modified_at": ev.modified_at.isoformat() if ev.modified_at else None,
        "hashtag_ids": [h.id for h in ev.hashtags],
        "hashtags": [{"id": h.id, "name": h.name} for h in ev.hashtags],
    }


def serialize_calendar_day(day):
    return {
        "id": day.id,
        "day": day.day.isoformat(),
        "note": day.note,
        "hide_completed": day.hide_completed,
        "modified_at": day.modified_at.isoformat() if day.modified_at else None,
    }


def serialize_event_group(group):
    """Serialize a calendar event group."""
    return {
        "id": group.id,
        "name": group.name,
        "created_at": group.created_at.isoformat() if group.created_at else None,
    }


def serialize_calendar_event_completion(c):
    """Serialize a CalendarEventCompletion model to a dictionary."""
    return {
        "id": c.id,
        "event_id": c.event_id,
        "occurrence_at": c.occurrence_at.isoformat() if c.occurrence_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def serialize_smart_folder(sf):
    """Serialize a SmartFolder model to a dictionary."""
    return {
        "id": sf.id,
        "name": sf.name,
        "show_lists": sf.show_lists,
        "show_todos": sf.show_todos,
        "group_todos_by_list": sf.group_todos_by_list,
        "root_todos_only": sf.root_todos_only,
        "nest_subtodos": sf.nest_subtodos,
        "uncompleted_only": sf.uncompleted_only,
        "include_archived_lists": sf.include_archived_lists,
        "modified_after": sf.modified_after.isoformat() if sf.modified_after else None,
        "modified_before": sf.modified_before.isoformat() if sf.modified_before else None,
        "modified_at": sf.modified_at.isoformat() if sf.modified_at else None,
        "list_sort_field": sf.list_sort_field,
        "list_sort_desc": sf.list_sort_desc,
        "todo_sort_field": sf.todo_sort_field,
        "todo_sort_desc": sf.todo_sort_desc,
        "dokuwiki_links_enabled": sf.dokuwiki_links_enabled,
        "dokuwiki_date_field": sf.dokuwiki_date_field,
        "include_root_folder": sf.include_root_folder,
        "exclude_root_folder": sf.exclude_root_folder,
        "strict_nesting": sf.strict_nesting,
        "section_order": sf.section_order,
        "sort_order": sf.sort_order,
        "created_at": sf.created_at.isoformat() if sf.created_at else None,
        "include_hashtags": [
            {
                "id": assoc.hashtag.id,
                "name": assoc.hashtag.name,
                "include_subtodos": assoc.include_subtodos,
                "include_list_items": assoc.include_list_items,
            }
            for assoc in sf.include_hashtags_assoc if assoc.hashtag
        ],
        "exclude_hashtags": [
            {
                "id": assoc.hashtag.id,
                "name": assoc.hashtag.name,
                "include_subtodos": assoc.include_subtodos,
            }
            for assoc in sf.exclude_hashtags_assoc if assoc.hashtag
        ],
        "include_hashtags_assoc": [
            {
                "id": assoc.hashtag.id,
                "name": assoc.hashtag.name,
                "include_subtodos": assoc.include_subtodos,
                "include_list_items": assoc.include_list_items,
            }
            for assoc in sf.include_hashtags_assoc if assoc.hashtag
        ],
        "exclude_hashtags_assoc": [
            {
                "id": assoc.hashtag.id,
                "name": assoc.hashtag.name,
                "include_subtodos": assoc.include_subtodos,
            }
            for assoc in sf.exclude_hashtags_assoc if assoc.hashtag
        ],
        "lists": [{"id": l.id, "name": l.name} for l in sf.lists],
        "include_folders": [{"id": f.id, "name": f.name} for f in sf.include_folders],
        "exclude_folders": [{"id": f.id, "name": f.name} for f in sf.exclude_folders],
    }


def serialize_named_search(ns):
    """Serialize a NamedSearch model to a dictionary."""
    return {
        "id": ns.id,
        "name": ns.name,
        "uncompleted_only": ns.uncompleted_only,
        "created_at": ns.created_at.isoformat() if ns.created_at else None,
        "hashtag_ids": [h.id for h in ns.hashtags],
        "hashtags": [{"id": h.id, "name": h.name} for h in ns.hashtags],
        "excluded_hashtag_ids": [h.id for h in ns.excluded_hashtags],
        "excluded_hashtags": [{"id": h.id, "name": h.name} for h in ns.excluded_hashtags],
    }


def serialize_header(h):
    """Serialize a ListDatetimeHeader model to a dictionary."""
    return {
        "id": h.id,
        "list_id": h.list_id,
        "created_at": h.created_at.isoformat(),
        "heading": h.created_at_heading,
        "dokuwiki_link": generate_dokuwiki_link(h.created_at)
    }


def serialize_link(link):
    """Serialize a Link model to a dictionary."""
    return {
        "id": link.id,
        "url": link.url,
        "title": link.title,
        "created_at": link.created_at.isoformat() if link.created_at else None,
        "list_id": link.list_id,
        "todo_id": link.todo_id,
        "note_id": link.note_id,
    }


def serialize_ingredient_group(ig):
    """Serialize an IngredientGroup model to a dictionary."""
    return {
        "id": ig.id,
        "canonical_name": ig.canonical_name,
        "created_at": ig.created_at.isoformat() if ig.created_at else None,
    }


def serialize_ingredient_event(ie):
    """Serialize an IngredientEvent model to a dictionary."""
    return {
        "id": ie.id,
        "todo_id": ie.todo_id,
        "event_type": ie.event_type,
        "quantity": ie.quantity,
        "event_date": ie.event_date.isoformat() if ie.event_date else None,
        "note": ie.note,
    }


def serialize_category(cat):
    """Serialize a Category model to a dictionary."""
    return {
        "id": cat.id,
        "name": cat.name,
        "created_at": cat.created_at.isoformat() if cat.created_at else None,
    }


def serialize_user_settings(s):
    """Serialize a UserSettings model to a dictionary."""
    return {
        "id": s.id,
        "filter_inc_hashtags": s.filter_inc_hashtags,
        "filter_exc_hashtags": s.filter_exc_hashtags,
        "hide_empty_lists": s.hide_empty_lists,
        "show_completed": s.show_completed,
        "show_delayed": s.show_delayed,
        "active_folder_id": s.active_folder_id,
        "calendar_traditional_view": s.calendar_traditional_view,
        "calendar_weekly_view": s.calendar_weekly_view,
        "calendar_event_filter": s.calendar_event_filter,
        "calendar_sort_order": s.calendar_sort_order,
        "calendar_word_wrap": s.calendar_word_wrap,
        "calendar_show_counts": s.calendar_show_counts,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def serialize_bill(b):
    """Serialize a Bill model to a dictionary."""
    return {
        "id": b.id,
        "name": b.name,
        "payee": b.payee,
        "expected_amount": b.expected_amount,
        "currency": b.currency or "AUD",
        "amount_strategy": b.amount_strategy or "fixed",
        "ledger_enabled": b.ledger_enabled or False,
        "next_due_date": b.next_due_date.isoformat() if b.next_due_date else None,
        "frequency": b.frequency,
        "frequency_days": b.frequency_days,
        "anchor_day": b.anchor_day,
        "is_due_date_confirmed": b.is_due_date_confirmed,
        "is_auto_pay": b.is_auto_pay,
        "is_active": b.is_active,
        "category": b.category,
        "color": b.color,
        "note": b.note,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "modified_at": b.modified_at.isoformat() if b.modified_at else None,
    }


def serialize_bill_payment(p):
    """Serialize a BillPayment model to a dictionary."""
    return {
        "id": p.id,
        "bill_id": p.bill_id,
        "due_date": p.due_date.isoformat() if p.due_date else None,
        "paid_date": p.paid_date.isoformat() if p.paid_date else None,
        "amount_paid": p.amount_paid,
        "amount_cents": p.amount_cents,
        "invoice_id": p.invoice_id,
        "payment_method": p.payment_method,
        "request_token": p.request_token,
        "request_fingerprint": p.request_fingerprint,
        "note": p.note,
        "is_late_overridden": p.is_late_overridden,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def serialize_bill_payment_submission(s):
    return {'id': s.id, 'bill_id': s.bill_id, 'payment_id': s.payment_id,
            'request_token': s.request_token, 'request_fingerprint': s.request_fingerprint,
            'is_deleted': s.is_deleted, 'created_at': s.created_at.isoformat() if s.created_at else None}


def serialize_bill_due_date_submission(s):
    return {'id': s.id, 'bill_id': s.bill_id, 'request_token': s.request_token,
            'request_fingerprint': s.request_fingerprint, 'is_deleted': s.is_deleted,
            'created_at': s.created_at.isoformat() if s.created_at else None}


def serialize_bill_record_submission(s):
    return {'id': s.id, 'bill_id': s.bill_id, 'record_type': s.record_type, 'record_id': s.record_id,
            'request_token': s.request_token, 'request_fingerprint': s.request_fingerprint,
            'is_deleted': s.is_deleted, 'created_at': s.created_at.isoformat() if s.created_at else None}


def serialize_bill_invoice(i):
    return {"id": i.id, "bill_id": i.bill_id, "amount_cents": i.amount_cents,
            "due_date": i.due_date.isoformat() if i.due_date else None,
            "invoice_date": i.invoice_date.isoformat() if i.invoice_date else None,
            "period_start": i.period_start.isoformat() if i.period_start else None,
            "period_end": i.period_end.isoformat() if i.period_end else None,
            "statement_balance_cents": i.statement_balance_cents,
            "request_token": i.request_token, "request_fingerprint": i.request_fingerprint,
            "invoice_number": i.invoice_number, "invoice_number_key": i.invoice_number_key,
            "note": i.note, "is_legacy": i.is_legacy, "schedule_advanced": i.schedule_advanced,
            "settled_at": i.settled_at.isoformat() if i.settled_at else None,
            "created_at": i.created_at.isoformat() if i.created_at else None}


def serialize_bill_allocation(a):
    return {"id": a.id, "invoice_id": a.invoice_id, "payment_id": a.payment_id,
            "amount_cents": a.amount_cents, "is_credit": a.is_credit}


def serialize_bill_adjustment(a):
    return {'id': a.id, 'bill_id': a.bill_id, 'kind': a.kind, 'amount_cents': a.amount_cents,
            'effective_date': a.effective_date.isoformat(), 'note': a.note,
            'request_token': a.request_token, 'request_fingerprint': a.request_fingerprint,
            'created_at': a.created_at.isoformat() if a.created_at else None}


def serialize_bill_adjustment_allocation(a):
    return {'id': a.id, 'invoice_id': a.invoice_id, 'debit_adjustment_id': a.debit_adjustment_id,
            'payment_id': a.payment_id, 'credit_adjustment_id': a.credit_adjustment_id, 'amount_cents': a.amount_cents}


def serialize_bill_statement(s):
    return {'id': s.id, 'bill_id': s.bill_id, 'as_of_date': s.as_of_date.isoformat(),
            'balance_cents': s.balance_cents, 'note': s.note, 'request_token': s.request_token,
            'request_fingerprint': s.request_fingerprint,
            'created_at': s.created_at.isoformat() if s.created_at else None}


def serialize_expense(e):
    """Serialize an Expense model to a dictionary."""
    return {
        "id": e.id,
        "amount": e.amount,
        "description": e.description,
        "category": e.category,
        "date": e.date.isoformat() if e.date else None,
        "payment_method": e.payment_method,
        "currency": e.currency or "AUD",
        "note": e.note,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "modified_at": e.modified_at.isoformat() if e.modified_at else None,
    }
