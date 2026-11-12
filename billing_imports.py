"""Validate imported billing history before any user records are changed."""
from datetime import datetime

from models import Bill, BillPayment, BillInvoice, BillAdjustment, BillStatement, utcnow
from billing_service import money_cents, stored_amount, estimate_amount, validate_cash_date, validate_adjustment_date, validate_recurrence_settings


def validate_estimates(data):
    for item in data.get('bills', []):
        if 'expected_amount' in item:
            label = f'Bill #{item["id"]} estimate' if item.get('id') is not None else 'Imported bill estimate'
            item['expected_amount'] = estimate_amount(item['expected_amount'], label)


def validate_ledger_amounts(data):
    """Reject fractional, out-of-range or missing supplied cents before any write."""
    maximum = 9223372036854775807
    rules = [
        ('bill_invoices', 'Invoice', 'amount_cents', 0, False),
        ('bill_invoices', 'Invoice', 'statement_balance_cents', -maximum - 1, True),
        ('bill_adjustments', 'Adjustment', 'amount_cents', 1, False),
        ('bill_statements', 'Supplier balance', 'balance_cents', -maximum - 1, False),
    ]
    for collection, name, field, minimum, nullable in rules:
        for item in data.get(collection, []):
            if field not in item:
                continue
            value = item[field]
            if value is None and nullable:
                continue
            if type(value) is not int or not minimum <= value <= maximum:
                label = f'{name} #{item["id"]}' if item.get('id') is not None else f'Imported {name.lower()}'
                requirement = 'positive' if minimum == 1 else 'non-negative' if minimum == 0 else 'signed'
                raise ValueError(f'{label}: {field} needs a {requirement} integer amount in cents within the supported range.')


def affected_accounts(db, data):
    ids = {_identifier(item['id'], 'Bill') for item in data.get('bills', []) if item.get('id') is not None}
    for collection, model in [('bill_payments', BillPayment), ('bill_invoices', BillInvoice),
                              ('bill_adjustments', BillAdjustment)]:
        for item in data.get(collection, []):
            existing = db.get(model, item['id']) if item.get('id') else None
            if existing:
                ids.add(existing.bill_id)
            if item.get('bill_id'):
                ids.add(int(item['bill_id']))
    return ids


def _identifier(value, label):
    if type(value) is int or (isinstance(value, str) and value.isdecimal()):
        return int(value)
    raise ValueError(f'{label} needs a valid record ID.')


def _merged_records(db, model, items, fields):
    """Apply partial updates in memory, including references omitted by an import."""
    records = {record.id: {field: getattr(record, field) for field in fields}
               for record in db.query(model).all()}
    for index, item in enumerate(items):
        record_id = (_identifier(item['id'], model.__tablename__)
                     if item.get('id') is not None else ('new', index))
        record = records.setdefault(record_id, {})
        for field in fields:
            if field in item:
                value = item[field]
                if field == 'bill_id' or (field == 'invoice_id' and value is not None):
                    value = _identifier(value, model.__tablename__)
                record[field] = value
    return records


def _event_datetime(value, label):
    try:
        value = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        raise ValueError(f'{label} needs a valid event date.') from None
    if not isinstance(value, datetime):
        raise ValueError(f'{label} needs a valid event date.')
    return value


def _event_day(value, label):
    return _event_datetime(value, label).date()


def validate_recurrence(db, data):
    items = data.get('bills', [])
    if not items:
        return
    fields = ['frequency', 'frequency_days', 'anchor_day', 'next_due_date']
    records = _merged_records(db, Bill, items, fields)
    identifiers = {_identifier(item['id'], 'Bill') if item.get('id') is not None else ('new', index)
                   for index, item in enumerate(items)}
    for record_id in identifiers:
        record = records[record_id]
        label = f'Bill #{record_id} recurrence' if isinstance(record_id, int) else 'Imported bill recurrence'
        frequency = record.get('frequency', 'monthly')
        interval = record.get('frequency_days')
        anchor = record.get('anchor_day')
        due = _event_day(record.get('next_due_date') or utcnow(), label)
        validate_recurrence_settings(frequency, interval, anchor,
                                     datetime.combine(due, datetime.min.time()), label)


def validate_invoice_periods(db, data):
    items = data.get('bill_invoices', [])
    if not items:
        return
    records = _merged_records(db, BillInvoice, items, ['period_start', 'period_end'])
    identifiers = {_identifier(item['id'], 'Invoice') if item.get('id') is not None else ('new', index)
                   for index, item in enumerate(items)}
    for record_id in identifiers:
        label = f'Invoice #{record_id}' if isinstance(record_id, int) else 'Imported invoice'
        record = records[record_id]
        dates = {field: _event_datetime(value, f'{label} {field}').replace(tzinfo=None) if value is not None else None
                 for field, value in record.items()}
        start, end = dates.get('period_start'), dates.get('period_end')
        if start is not None and end is not None and end < start:
            raise ValueError(f'{label}: The service period end must be on or after its start.')


def validate_opening_balances(db, data):
    if not any(key in data for key in ('bill_invoices', 'bill_payments', 'bill_adjustments')):
        return
    adjustments = _merged_records(db, BillAdjustment, data.get('bill_adjustments', []),
                                  ['bill_id', 'kind', 'effective_date'])
    openings = {}
    for record_id, record in adjustments.items():
        if record.get('kind') not in ('opening_credit', 'opening_debt'):
            continue
        bill_id = record['bill_id']
        if bill_id in openings:
            raise ValueError(f'Bill #{bill_id} can have only one opening balance. Edit the existing opening balance instead.')
        label = f'Opening balance #{record_id}' if isinstance(record_id, int) else 'Imported opening balance'
        openings[bill_id] = (label, _event_day(record.get('effective_date'), label))
    if not openings:
        return

    earliest = {}
    def remember(bill_id, value, label):
        if bill_id in openings:
            event_date = _event_day(value, label)
            earliest[bill_id] = min(earliest.get(bill_id, event_date), event_date)

    invoices = _merged_records(db, BillInvoice, data.get('bill_invoices', []),
                               ['bill_id', 'invoice_date', 'due_date', 'created_at'])
    for record_id, record in invoices.items():
        # Use the same issue-date, due-date and creation-date fallbacks as the ledger.
        remember(record['bill_id'], record.get('invoice_date') or record.get('due_date')
                 or record.get('created_at') or utcnow(), f'Invoice #{record_id}')
    payments = _merged_records(db, BillPayment, data.get('bill_payments', []), ['bill_id', 'paid_date'])
    for record_id, record in payments.items():
        remember(record['bill_id'], record.get('paid_date'), f'Payment #{record_id}')
    for record_id, record in adjustments.items():
        if record.get('kind') not in ('opening_credit', 'opening_debt'):
            remember(record['bill_id'], record.get('effective_date'), f'Adjustment #{record_id}')
    for bill_id, (label, opening_date) in openings.items():
        if bill_id in earliest and opening_date > earliest[bill_id]:
            raise ValueError(f'{label} for bill #{bill_id} must be dated on or before the first recorded account event.')


def validate_account_history(db, data):
    """Check the resulting account ownership and tracking state before any upsert."""
    collections = [('bill_invoices', BillInvoice), ('bill_payments', BillPayment),
                   ('bill_adjustments', BillAdjustment), ('bill_statements', BillStatement)]
    if not any(key in data for key in ['bills', *(key for key, _ in collections)]):
        return
    current_tracking = {bill.id: bill.ledger_enabled for bill in db.query(Bill).all()}
    bills = _merged_records(db, Bill, data.get('bills', []), ['ledger_enabled'])
    for bill_id, bill in bills.items():
        enabled = bill.get('ledger_enabled', False)
        if type(enabled) is not bool:
            raise ValueError(f'Bill #{bill_id} needs a boolean invoice tracking setting.')

    records = {}
    history, tracked_history = set(), set()
    for key, model in collections:
        fields = ['bill_id', 'invoice_id', 'amount_cents'] if model is BillPayment else ['bill_id']
        records[key] = _merged_records(db, model, data.get(key, []), fields)
        for record_id, record in records[key].items():
            bill_id = record.get('bill_id')
            if bill_id not in bills:
                raise ValueError(f'{key} #{record_id} refers to a missing bill account.')
            history.add(bill_id)
            if model is not BillPayment or record.get('invoice_id') is not None or record.get('amount_cents') is not None:
                tracked_history.add(bill_id)

    for payment_id, payment in records['bill_payments'].items():
        invoice_id = payment.get('invoice_id')
        if invoice_id is None:
            continue
        invoice = records['bill_invoices'].get(invoice_id)
        if invoice is None:
            raise ValueError(f'Payment #{payment_id} refers to a missing invoice.')
        if invoice['bill_id'] != payment['bill_id']:
            raise ValueError(f'Payment #{payment_id} and invoice #{invoice_id} must belong to the same bill account.')

    for bill_id, bill in bills.items():
        if not bill.get('ledger_enabled', False) and (
                bill_id in tracked_history or (current_tracking.get(bill_id) and bill_id in history)):
            raise ValueError(f'Bill #{bill_id} has recorded account history. Invoice tracking must stay enabled.')


def validate_payment_amounts(db, data):
    incoming_bills = _merged_records(db, Bill, data.get('bills', []), ['ledger_enabled'])
    payment_dates = {}
    for item in data.get('bill_payments', []):
        payment_id = _identifier(item['id'], 'Payment') if item.get('id') is not None else None
        existing = db.get(BillPayment, payment_id) if payment_id else None
        label = f'Payment #{payment_id}' if payment_id else 'Imported payment'
        previous_paid, previous_due = payment_dates.get(payment_id, (
            existing.paid_date if existing else None, existing.due_date if existing else None))
        paid = item.get('paid_date') or previous_paid
        due = item.get('due_date') or previous_due
        paid = paid or due or datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        due = due or paid
        try:
            paid = datetime.fromisoformat(paid) if isinstance(paid, str) else paid
            due = datetime.fromisoformat(due) if isinstance(due, str) else due
        except ValueError:
            raise ValueError(f'{label} needs valid payment and due dates.') from None
        validate_cash_date(paid, label)
        if not isinstance(due, datetime):
            raise ValueError(f'{label} needs a valid due date.')
        item['paid_date'], item['due_date'] = paid, due
        if payment_id is not None:
            payment_dates[payment_id] = (paid, due)
        if 'amount_paid' not in item and 'amount_cents' not in item:
            continue
        cents = item.get('amount_cents')
        if cents is not None and (type(cents) is not int or cents < 0 or cents > 9223372036854775807):
            raise ValueError(f'{label} needs a non-negative integer amount in cents.')
        paid = item.get('amount_paid')
        if paid is not None:
            paid_cents = money_cents(paid)
            if cents is not None and cents != paid_cents:
                raise ValueError(f'{label} has conflicting monetary amounts. Correct the import file and try again.')
            item['amount_paid'] = stored_amount(paid_cents, label)
            bill_id = item.get('bill_id', existing.bill_id if existing else None)
            if bill_id is not None:
                bill_id = _identifier(bill_id, label)
            bill = db.get(Bill, bill_id) if bill_id else None
            incoming_ledger = incoming_bills.get(bill_id, {}).get('ledger_enabled', False)
            if cents is not None or incoming_ledger or (bill and bill.ledger_enabled) or (existing and existing.amount_cents is not None):
                item['amount_cents'] = paid_cents
        elif cents is not None and 'amount_paid' not in item:
            # A cents-only record has one monetary value, so derive its cash field.
            item['amount_paid'] = stored_amount(cents, label)
        elif cents is not None or (existing and existing.amount_cents is not None):
            raise ValueError(f'{label} needs an amount paid consistent with its recorded cents.')
    adjustment_dates = {}
    for item in data.get('bill_adjustments', []):
        existing = db.get(BillAdjustment, item['id']) if item.get('id') else None
        adjustment_id = _identifier(item['id'], 'Adjustment') if item.get('id') is not None else None
        previous_kind, previous_date = adjustment_dates.get(adjustment_id, (
            existing.kind if existing else None, existing.effective_date if existing else None))
        kind = item.get('kind', previous_kind)
        label = 'Imported cash refund' if kind == 'refund' else (
            f'Adjustment #{item["id"]}' if item.get('id') is not None else 'Imported adjustment')
        effective = item.get('effective_date', previous_date)
        try:
            effective = datetime.fromisoformat(effective) if isinstance(effective, str) else effective
        except ValueError:
            raise ValueError(f'{label} needs a valid effective date.') from None
        if kind == 'refund':
            validate_cash_date(effective, label)
        else:
            validate_adjustment_date(effective, label)
        item['effective_date'] = effective
        if adjustment_id is not None:
            adjustment_dates[adjustment_id] = (kind, effective)
