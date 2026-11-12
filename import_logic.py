import json
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import DateTime, select, delete
from models import (
    List, Todo, Note, Hashtag, Folder, CalendarEvent,
    CalendarEventCompletion, CalendarDay, ListDatetimeHeader, SmartFolder,
    SmartFolderIncludeHashtag, SmartFolderExcludeHashtag,
    Link, NamedSearch, IngredientGroup, IngredientEvent,
    Category, EventGroup, UserSettings, collation_todos,
    smart_folder_lists, smart_folder_include_folders,
    smart_folder_exclude_folders, named_search_hashtags,
    named_search_excluded_hashtags,
    Bill, BillPayment, BillPaymentSubmission, BillDueDateSubmission, BillRecordSubmission, BillInvoice, BillAllocation, BillAdjustment, BillStatement, Expense,
    utcnow
)
from helpers import get_or_create_hashtag
from billing_imports import (validate_estimates, validate_payment_amounts, validate_ledger_amounts,
                             validate_account_history, affected_accounts, validate_recurrence,
                             validate_opening_balances, validate_invoice_periods)
from billing_service import lock_account, sync_invoice_due_date, refresh_invoice_forecast
from billing_accounts import validate_refund_credit
from expense_service import validate_expense_amounts
from calendar_service import validate_calendar_imports

def parse_iso_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str)
    except Exception:
        return None

def import_data_from_json(db: Session, json_data: dict, *, restore_history: bool = False) -> dict:
    """
    Imports data from a JSON dictionary.
    Strategy: Upsert (Update if ID exists, Insert if not).
    Does NOT delete missing items.
    """
    try:
        if not isinstance(json_data, dict) or not isinstance(json_data.get('data'), dict):
            raise ValueError('Upload a JSON export containing a data object.')
        if restore_history:
            required = ('bills', 'bill_invoices', 'bill_payments', 'bill_adjustments', 'bill_statements')
            if (json_data.get('version') != 2 or not parse_iso_date(json_data.get('exported_at'))
                    or any(not isinstance(json_data['data'].get(key), list) for key in required)):
                raise ValueError('Historical refund restore requires a full JSON backup from Export Data.')
        return _import_records(db, json_data, restore_history=restore_history)
    except Exception as error:
        db.rollback()
        message = str(error).splitlines()[0].rstrip('.') if str(error) else 'Invalid import'
        raise ValueError(f'{message}. No changes were saved.') from error


def _import_error(collection, item, error):
    identity = ''
    if isinstance(item, dict):
        if item.get('id') is not None:
            identity = f' #{item["id"]}'
        elif item.get('name'):
            identity = f' "{str(item["name"])[:80]}"'
    message = str(error).splitlines()[0] if str(error) else 'Invalid record'
    return ValueError(f'Import failed for {collection}{identity}: {message}')


def _import_folders(db, items, stats):
    parent_updates = {}
    for item in items:
        try:
            values = dict(item)
            supplied_parent = 'parent_id' in values
            parent_id = values.pop('parent_id', None)
            folder = process_entity(db, Folder, values, stats)
            if supplied_parent:
                parent_updates[folder.id] = parent_id
        except Exception as error:
            raise _import_error('folders', item, error) from error
    db.flush()
    if not items:
        return

    # Create every folder first so a parent can appear later in an export.
    parents = dict(db.query(Folder.id, Folder.parent_id).all())
    parents.update(parent_updates)
    for folder_id, parent_id in parents.items():
        if parent_id is not None and type(parent_id) is not int:
            raise _import_error('folders', {'id': folder_id}, ValueError('Parent folder must be an integer ID or null.'))
        if parent_id is not None and parent_id not in parents:
            raise _import_error('folders', {'id': folder_id}, ValueError(f'Parent folder #{parent_id} does not exist.'))

    checked = set()
    for folder_id in parents:
        path = set()
        current = folder_id
        while current is not None and current not in checked:
            if current in path:
                raise _import_error('folders', {'id': current},
                    ValueError('A folder cannot be placed inside itself or one of its descendants.'))
            path.add(current)
            current = parents[current]
        checked.update(path)

    for folder_id, parent_id in parent_updates.items():
        db.get(Folder, folder_id).parent_id = parent_id
    db.flush()


def _import_records(db: Session, json_data: dict, *, restore_history: bool = False) -> dict:
    stats = {"created": 0, "updated": 0, "errors": 0}
    data = json_data.get("data", {})
    # Serialize preflight and writes so new history cannot bypass tracking checks.
    lock_account(db)
    # Validate monetary and account invariants before mutating any entity.
    validate_recurrence(db, data)
    validate_estimates(data)
    validate_ledger_amounts(data)
    validate_invoice_periods(db, data)
    validate_payment_amounts(db, data)
    validate_account_history(db, data)
    validate_opening_balances(db, data)
    validate_expense_amounts(data)
    validate_calendar_imports(db, data.get('calendar_events', []))
    changed_accounts = affected_accounts(db, data)
    previous_invoice_dues = {i.id: i.due_date for i in db.query(BillInvoice).all()}
    previous_adjustments = {a.id: (a.bill_id, a.kind, a.amount_cents, a.effective_date)
                            for a in db.query(BillAdjustment).all()}
    
    # --- FOLDERS ---
    _import_folders(db, data.get('folders', []), stats)

    # --- CATEGORIES ---
    for item in data.get("categories", []):
        try:
            process_entity(db, Category, item, stats)
        except Exception as e:
            raise _import_error('categories', item, e) from e

    db.flush()

    # --- EVENT GROUPS (before calendar events, since events reference them) ---
    for item in data.get("event_groups", []):
        try:
            process_entity(db, EventGroup, item, stats)
        except Exception as e:
            raise _import_error('event_groups', item, e) from e

    db.flush()

    # --- HASHTAGS (process definitions early so IDs exist for relationships) ---
    for item in data.get("hashtags", []):
        try:
            process_entity(db, Hashtag, item, stats)
        except Exception as e:
            raise _import_error('hashtags', item, e) from e

    db.flush()

    # --- INGREDIENT GROUPS (before todos, since todos reference them) ---
    for item in data.get("ingredient_groups", []):
        try:
            process_entity(db, IngredientGroup, item, stats)
        except Exception as e:
            raise _import_error('ingredient_groups', item, e) from e

    db.flush()

    # --- LISTS ---
    for item in data.get("lists", []):
        try:
            # Handle M2M hashtags manually
            hashtags_data = item.pop("hashtags", None)
            item.pop("hashtag_ids", None)  # Ignore redundant list
            item.pop("links", [])  # Links are stored separately now
            
            # Remove computed fields
            item.pop("created_at_formatted", None)
            item.pop("modified_at_formatted", None)
            
            obj = process_entity(db, List, item, stats)
            
            # Re-attach hashtags
            if obj and hashtags_data is not None:
                new_tags = []
                for h_data in hashtags_data:
                    h = resolve_hashtag(db, h_data)
                    if h: new_tags.append(h)
                obj.hashtags = new_tags
                
        except Exception as e:
            raise _import_error('lists', item, e) from e

    db.flush()

    # --- TODOS ---
    for item in data.get("todos", []):
        try:
            hashtags_data = item.pop("hashtags", None)
            item.pop("hashtag_ids", None)
            item.pop("collation_list_ids", None)  # Restored via collation_todos junction
            item.pop("links", [])  # Links are stored separately now
            item.pop("has_note", None)
            item.pop("created_at_formatted", None)
            item.pop("modified_at_formatted", None)
            
            obj = process_entity(db, Todo, item, stats)
            
            if obj and hashtags_data is not None:
                new_tags = []
                for h_data in hashtags_data:
                    h = resolve_hashtag(db, h_data)
                    if h: new_tags.append(h)
                obj.hashtags = new_tags
                
        except Exception as e:
            raise _import_error('todos', item, e) from e

    db.flush()

    # --- NOTES ---
    for item in data.get("notes", []):
        try:
            process_entity(db, Note, item, stats)
        except Exception as e:
            raise _import_error('notes', item, e) from e

    db.flush()

    # --- LINKS ---
    for item in data.get("links", []):
        try:
            process_entity(db, Link, item, stats)
        except Exception as e:
            raise _import_error('links', item, e) from e

    db.flush()

    # --- CALENDAR EVENTS ---
    for item in data.get("calendar_events", []):
        try:
            hashtags_data = item.pop("hashtags", None)
            item.pop("hashtag_ids", None)
            obj = process_entity(db, CalendarEvent, item, stats)
            
            if obj and hashtags_data is not None:
                new_tags = []
                for h_data in hashtags_data:
                    h = resolve_hashtag(db, h_data)
                    if h: new_tags.append(h)
                obj.hashtags = new_tags
        except Exception as e:
            raise _import_error('calendar_events', item, e) from e

    db.flush()

    # --- CALENDAR DAY NOTES AND PREFERENCES ---
    for item in data.get("calendar_days", []):
        try:
            process_entity(db, CalendarDay, item, stats)
        except Exception as e:
            raise _import_error('calendar_days', item, e) from e

    # --- CALENDAR EVENT COMPLETIONS ---
    for item in data.get("calendar_event_completions", []):
        try:
            process_entity(db, CalendarEventCompletion, item, stats)
        except Exception as e:
            raise _import_error('calendar_event_completions', item, e) from e

    db.flush()

    # --- HEADERS (ListDatetimeHeader) ---
    for item in data.get("headers", []):
        try:
            # Remove computed fields
            item.pop("heading", None)
            item.pop("dokuwiki_link", None)
            process_entity(db, ListDatetimeHeader, item, stats)
        except Exception as e:
            raise _import_error('list_datetime_headers', item, e) from e

    db.flush()

    # --- INGREDIENT EVENTS ---
    for item in data.get("ingredient_events", []):
        try:
            process_entity(db, IngredientEvent, item, stats)
        except Exception as e:
            raise _import_error('ingredient_events', item, e) from e

    db.flush()

    # --- NAMED SEARCHES ---
    for item in data.get("named_searches", []):
        try:
            hashtags_data = item.pop("hashtags", None)
            excluded_hashtags_data = item.pop("excluded_hashtags", None)
            item.pop("hashtag_ids", None)
            item.pop("excluded_hashtag_ids", None)
            
            obj = process_entity(db, NamedSearch, item, stats)
            
            if obj:
                if hashtags_data is not None:
                    new_tags = []
                    for h_data in hashtags_data:
                        h = resolve_hashtag(db, h_data)
                        if h: new_tags.append(h)
                    obj.hashtags = new_tags
                
                if excluded_hashtags_data is not None:
                    exc_tags = []
                    for h_data in excluded_hashtags_data:
                        h = resolve_hashtag(db, h_data)
                        if h: exc_tags.append(h)
                    obj.excluded_hashtags = exc_tags
                    
        except Exception as e:
            raise _import_error('named_searches', item, e) from e

    db.flush()

    # --- SMART FOLDERS ---
    for item in data.get("smart_folders", []):
        try:
            include_hashtags_data = item.pop("include_hashtags", None)
            exclude_hashtags_data = item.pop("exclude_hashtags", None)
            item.pop("include_hashtags_assoc", None)  # Duplicate of include_hashtags
            item.pop("exclude_hashtags_assoc", None)  # Duplicate of exclude_hashtags
            lists_data = item.pop("lists", None)
            include_folders_data = item.pop("include_folders", None)
            exclude_folders_data = item.pop("exclude_folders", None)
            
            obj = process_entity(db, SmartFolder, item, stats)
            
            if obj:
                db.flush()  # Ensure obj.id exists
                
                # Rebuild include hashtag associations
                if include_hashtags_data is not None:
                    # Remove old associations
                    db.query(SmartFolderIncludeHashtag).filter(
                        SmartFolderIncludeHashtag.smart_folder_id == obj.id
                    ).delete()
                    db.flush()
                    
                    for h_data in include_hashtags_data:
                        h = resolve_hashtag(db, h_data)
                        if h:
                            assoc = SmartFolderIncludeHashtag(
                                smart_folder_id=obj.id,
                                hashtag_id=h.id,
                                include_subtodos=h_data.get("include_subtodos", False),
                                include_list_items=h_data.get("include_list_items", False),
                            )
                            db.add(assoc)
                
                # Rebuild exclude hashtag associations
                if exclude_hashtags_data is not None:
                    db.query(SmartFolderExcludeHashtag).filter(
                        SmartFolderExcludeHashtag.smart_folder_id == obj.id
                    ).delete()
                    db.flush()
                    
                    for h_data in exclude_hashtags_data:
                        h = resolve_hashtag(db, h_data)
                        if h:
                            assoc = SmartFolderExcludeHashtag(
                                smart_folder_id=obj.id,
                                hashtag_id=h.id,
                                include_subtodos=h_data.get("include_subtodos", False),
                            )
                            db.add(assoc)
                
                # Rebuild list associations
                if lists_data is not None:
                    obj.lists = []
                    db.flush()
                    for l_data in lists_data:
                        lst = None
                        if isinstance(l_data, dict):
                            lid = l_data.get("id")
                            if lid:
                                lst = db.query(List).get(lid)
                            if not lst:
                                lname = l_data.get("name")
                                if lname:
                                    lst = db.query(List).filter(List.name == lname).first()
                        elif isinstance(l_data, str):
                            lst = db.query(List).filter(List.name == l_data).first()
                        if lst:
                            obj.lists.append(lst)
                
                # Rebuild include folders
                if include_folders_data is not None:
                    obj.include_folders = []
                    db.flush()
                    for f_data in include_folders_data:
                        folder = None
                        if isinstance(f_data, dict):
                            fid = f_data.get("id")
                            if fid:
                                folder = db.query(Folder).get(fid)
                            if not folder:
                                fname = f_data.get("name")
                                if fname:
                                    folder = db.query(Folder).filter(Folder.name == fname).first()
                        if folder:
                            obj.include_folders.append(folder)
                
                # Rebuild exclude folders
                if exclude_folders_data is not None:
                    obj.exclude_folders = []
                    db.flush()
                    for f_data in exclude_folders_data:
                        folder = None
                        if isinstance(f_data, dict):
                            fid = f_data.get("id")
                            if fid:
                                folder = db.query(Folder).get(fid)
                            if not folder:
                                fname = f_data.get("name")
                                if fname:
                                    folder = db.query(Folder).filter(Folder.name == fname).first()
                        if folder:
                            obj.exclude_folders.append(folder)
                    
        except Exception as e:
            raise _import_error('smart_folders', item, e) from e

    db.flush()

    # --- USER SETTINGS ---
    for item in data.get("user_settings", []):
        try:
            process_entity(db, UserSettings, item, stats)
        except Exception as e:
            raise _import_error('user_settings', item, e) from e

    db.flush()

    # --- COLLATION TODOS (junction table with sort_order) ---
    for item in data.get("collation_todos", []):
        try:
            clist_id = item.get("collation_list_id")
            todo_id = item.get("todo_id")
            sort_order = item.get("sort_order", 0)
            
            if not clist_id or not todo_id:
                raise ValueError('A collected task link requires collation_list_id and todo_id.')
            
            # Check the referenced list and todo exist
            if db.get(List, clist_id) is None:
                raise ValueError(f'Collected task link refers to missing list #{clist_id}.')
            if db.get(Todo, todo_id) is None:
                raise ValueError(f'Collected task link refers to missing task #{todo_id}.')
            
            # Check if already exists
            existing = db.execute(
                select(collation_todos).where(
                    collation_todos.c.collation_list_id == clist_id,
                    collation_todos.c.todo_id == todo_id,
                )
            ).first()
            
            if existing:
                # Update sort_order
                db.execute(
                    collation_todos.update().where(
                        collation_todos.c.collation_list_id == clist_id,
                        collation_todos.c.todo_id == todo_id,
                    ).values(sort_order=sort_order)
                )
                stats["updated"] += 1
            else:
                db.execute(
                    collation_todos.insert().values(
                        collation_list_id=clist_id,
                        todo_id=todo_id,
                        sort_order=sort_order,
                    )
                )
                stats["created"] += 1
                
        except Exception as e:
            raise _import_error('collation_todos', item, e) from e

    # --- BILLS ---
    for item in data.get("bills", []):
        try:
            item.pop("created_at_formatted", None)
            item.pop("modified_at_formatted", None)
            item.pop("next_due_date_formatted", None)
            item.pop("next_due_date_iso", None)
            item.pop("frequency_label", None)
            item.pop("currency_symbol", None)
            item.pop("estimated_amount", None)
            item.pop("effective_amount", None)
            item.pop("is_estimated", None)
            item.pop("last_payment", None)
            item.pop("payments", None)
            for field in ("invoices", "ordered_invoices", "credit_cents", "outstanding_cents", "open_invoice", "adjustments", "statements"):
                item.pop(field, None)
            item.pop("days_until_due", None)
            process_entity(db, Bill, item, stats)
        except Exception as e:
            raise _import_error('bills', item, e) from e

    db.flush()

    # Invoices precede payments, because payments may refer to an invoice.
    for item in data.get("bill_invoices", []):
        try:
            if 'invoice_number' in item:
                item['invoice_number'] = (item['invoice_number'] or '').strip() or None
                item['invoice_number_key'] = item['invoice_number'].casefold() if item['invoice_number'] else None
            process_entity(db, BillInvoice, item, stats)
        except Exception as e:
            raise _import_error('bill_invoices', item, e) from e
    db.flush()

    # --- BILL PAYMENTS ---
    for item in data.get("bill_payments", []):
        try:
            item.pop("due_date_formatted", None)
            item.pop("paid_date_formatted", None)
            item.pop("due_date_iso", None)
            item.pop("paid_date_iso", None)
            item.pop("timing_label", None)
            item.pop("is_late", None)
            item.pop("display_note", None)
            item.pop("notes_list", None)
            item.pop("days_difference", None)
            item.pop("currency_symbol", None)
            item.pop("bill", None)
            item.pop("invoice", None)
            item.pop("allocations", None)
            item.pop("unallocated_cents", None)
            item.pop('effective_due_date', None)
            item.pop('adjustment_allocations', None)
            process_entity(db, BillPayment, item, stats)
        except Exception as e:
            raise _import_error('bill_payments', item, e) from e

    db.flush()

    # Match tokens rather than metadata IDs when merging exports. Deleting cash
    # or its account must not allow a retry to restore the receipt.
    for item in data.get('bill_payment_submissions', []):
        try:
            token = item.get('request_token')
            if not isinstance(token, str) or not token or len(token) > 64:
                raise ValueError('Invalid payment submission token.')
            bill_id = int(item['bill_id'])
            fingerprint = item.get('request_fingerprint')
            deleted = item.get('is_deleted', False)
            if not isinstance(deleted, bool):
                raise ValueError('Invalid payment submission deletion flag.')
            existing = db.query(BillPaymentSubmission).filter_by(request_token=token).first()
            if existing:
                if existing.bill_id != bill_id or existing.request_fingerprint != fingerprint:
                    raise ValueError('Payment submission belongs to different details.')
                if item.get('payment_id') is not None and existing.payment_id not in (None, item['payment_id']):
                    raise ValueError('Payment submission belongs to a different receipt.')
                if item.get('payment_id') is not None:
                    existing.payment_id = item['payment_id']
                existing.is_deleted = existing.is_deleted or deleted
                stats['updated'] += 1
            else:
                values = dict(bill_id=bill_id, payment_id=item.get('payment_id'), request_token=token,
                              request_fingerprint=fingerprint, is_deleted=deleted)
                if item.get('created_at'):
                    values['created_at'] = parse_iso_date(item['created_at'])
                if item.get('id') and db.get(BillPaymentSubmission, item['id']) is None:
                    values['id'] = item['id']
                db.add(BillPaymentSubmission(**values))
                stats['created'] += 1
            db.flush()
        except (ValueError, TypeError, KeyError) as error:
            raise _import_error('bill_payment_submissions', item, error) from error

    imported_adjustments = {}
    for collection, model in [('bill_adjustments', BillAdjustment), ('bill_statements', BillStatement)]:
        for item in data.get(collection, []):
            try:
                record = process_entity(db, model, item, stats)
                if model is BillAdjustment:
                    db.flush()
                    imported_adjustments[record.id] = record
            except Exception as e:
                raise _import_error(collection, item, e) from e
    db.flush()

    for item in data.get('bill_record_submissions', []):
        try:
            token = item.get('request_token')
            record_type = item.get('record_type')
            if not isinstance(token, str) or not token or len(token) > 64:
                raise ValueError('Invalid billing submission token.')
            if record_type not in ('invoice', 'adjustment', 'statement'):
                raise ValueError('Invalid billing submission record type.')
            bill_id, record_id = int(item['bill_id']), int(item['record_id'])
            fingerprint = item.get('request_fingerprint')
            deleted = item.get('is_deleted', False)
            if not isinstance(deleted, bool):
                raise ValueError('Invalid billing submission deletion flag.')
            existing = db.query(BillRecordSubmission).filter_by(record_type=record_type, request_token=token).first()
            if existing:
                if (existing.bill_id != bill_id or existing.record_id != record_id
                        or existing.request_fingerprint != fingerprint):
                    raise ValueError('Billing submission belongs to different details.')
                existing.is_deleted = existing.is_deleted or deleted
                stats['updated'] += 1
            else:
                values = dict(bill_id=bill_id, record_type=record_type, record_id=record_id,
                              request_token=token, request_fingerprint=fingerprint, is_deleted=deleted)
                if item.get('created_at'):
                    values['created_at'] = parse_iso_date(item['created_at'])
                if item.get('id') and db.get(BillRecordSubmission, item['id']) is None:
                    values['id'] = item['id']
                db.add(BillRecordSubmission(**values))
                stats['created'] += 1
            db.flush()
        except (ValueError, TypeError, KeyError) as error:
            raise _import_error('bill_record_submissions', item, error) from error

    for item in data.get('bill_due_date_submissions', []):
        try:
            token, fingerprint = item.get('request_token'), item.get('request_fingerprint')
            if not isinstance(token, str) or not token or len(token) > 64:
                raise ValueError('Invalid date confirmation token.')
            if not isinstance(fingerprint, str) or len(fingerprint) != 64:
                raise ValueError('Invalid date confirmation details.')
            bill_id = int(item['bill_id'])
            deleted = item.get('is_deleted', False)
            if not isinstance(deleted, bool):
                raise ValueError('Invalid date confirmation deletion flag.')
            existing = db.query(BillDueDateSubmission).filter_by(request_token=token).first()
            if existing:
                if existing.bill_id != bill_id or existing.request_fingerprint != fingerprint:
                    raise ValueError('Date confirmation belongs to different details.')
                existing.is_deleted = existing.is_deleted or deleted
                stats['updated'] += 1
            else:
                values = dict(bill_id=bill_id, request_token=token, request_fingerprint=fingerprint, is_deleted=deleted)
                if item.get('created_at'):
                    values['created_at'] = parse_iso_date(item['created_at'])
                if item.get('id') and db.get(BillDueDateSubmission, item['id']) is None:
                    values['id'] = item['id']
                db.add(BillDueDateSubmission(**values))
                stats['created'] += 1
            db.flush()
        except (ValueError, TypeError, KeyError) as error:
            raise _import_error('bill_due_date_submissions', item, error) from error

    # --- EXPENSES ---
    # Allocations are derived; rebuilding also removes stale allocations on re-import.
    from billing_service import reconcile_allocations
    db.flush()
    # Verify the saved relationships before rebuilding their allocations.
    validate_account_history(db, {'bills': []})
    imported_schedules = {int(item['id']) for item in data.get('bills', [])
                          if item.get('id') is not None and
                          ('next_due_date' in item or 'is_due_date_confirmed' in item)}
    for bill in db.query(Bill).filter(Bill.ledger_enabled == True):
        db.expire(bill, ['invoices', 'payments', 'adjustments', 'statements'])
        saved_due = bill.next_due_date
        saved_confirmed = bill.is_due_date_confirmed
        saved_modified = bill.modified_at
        if not restore_history:
            for adjustment in imported_adjustments.values():
                details = (adjustment.bill_id, adjustment.kind, adjustment.amount_cents, adjustment.effective_date)
                if (adjustment.bill_id == bill.id and adjustment.kind == 'refund'
                        and previous_adjustments.get(adjustment.id) != details):
                    try:
                        validate_refund_credit(bill, adjustment.amount_cents, adjustment.effective_date, adjustment)
                    except ValueError as error:
                        raise _import_error('bill_adjustments', {'id': adjustment.id}, error) from error
        for invoice in bill.invoices:
            if invoice.id in previous_invoice_dues and invoice.due_date != previous_invoice_dues[invoice.id]:
                sync_invoice_due_date(bill, invoice, invoice.due_date)
        reconcile_allocations(db, bill, update_schedule=bill.id in changed_accounts)
        if (bill.id in changed_accounts and not bill.open_invoice and saved_confirmed
                and saved_due and bill.id in imported_schedules):
            # Restore an explicit supplier date from the snapshot for settled accounts.
            bill.next_due_date = saved_due
            bill.is_due_date_confirmed = True
        if bill.id in changed_accounts and bill.invoices:
            latest = bill.ordered_invoices[-1]
            if latest.id in previous_invoice_dues:
                refresh_invoice_forecast(bill, latest, previous_invoice_dues[latest.id])
        bill.modified_at = saved_modified

    for item in data.get("expenses", []):
        try:
            item.pop("date_formatted", None)
            item.pop("date_iso", None)
            item.pop("currency_symbol", None)
            if not item.get("date"):
                existing = db.get(Expense, item['id']) if item.get('id') else None
                if existing is not None:
                    item.pop('date', None)
                else:
                    item["date"] = datetime.now().isoformat()
            process_entity(db, Expense, item, stats)
        except Exception as e:
            raise _import_error('expenses', item, e) from e

    db.flush()

    db.commit()
    return stats

def resolve_hashtag(db, h_data):
    """Finds or creates a hashtag based on data."""
    if not h_data: return None
    
    # Try ID match first
    hid = h_data.get("id")
    if hid:
        h = db.query(Hashtag).get(hid)
        if h: return h
        
    # Try Name match
    name = h_data.get("name")
    if name:
        h = db.query(Hashtag).filter(Hashtag.name == name).first()
        if h: return h
        
        # Create new
        h = Hashtag(name=name)
        db.add(h)
        db.flush()  # Need ID immediately for relationships
        return h
    return None

def process_entity(db, ModelClass, item_data, stats):
    """Generic upsert for a simple dictionary -> Model"""
    item_id = item_data.get("id")
    
    # Convert every mapped date field, including delays and alarm dates.
    date_fields = {column.key for column in ModelClass.__table__.columns
                   if isinstance(column.type, DateTime)}
    for k, v in item_data.items():
        if k in date_fields and isinstance(v, str):
            parsed = parse_iso_date(v)
            if parsed is None:
                raise ValueError(f'Enter a valid ISO date or datetime for {k}.')
            item_data[k] = parsed
    
    # 1. Update existing
    if item_id:
        existing = db.get(ModelClass, item_id)
        if existing:
            # Update fields
            for k, v in item_data.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
            stats["updated"] += 1
            return existing
            
    # 2. Insert new
    obj = ModelClass(**item_data)
    
    if item_id:
        obj.id = item_id
        
    db.add(obj)
    db.flush()
    stats["created"] += 1
    return obj
