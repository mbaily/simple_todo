"""Account adjustments and dated supplier balance comparisons, in integer cents."""
from datetime import datetime
import hashlib
import json

from models import BillAdjustment, BillStatement
from billing_service import enable_ledger, money_cents, reconcile_allocations, invoice_event_date, balance_as_of, validate_cash_date, validate_adjustment_date
from helpers import _midnight
from billing_submissions import submitted_record, retain_record_submission


def _fingerprint(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def validate_refund_credit(bill, cents, effective_date, adjustment=None):
    balance, complete = balance_as_of(bill, effective_date, exclude_adjustment=adjustment)
    if not complete:
        raise ValueError('Enter missing invoice issue dates before recording a refund so the credit available on that date can be checked.')
    if cents > max(0, -balance):
        raise ValueError('The refund exceeds the credit recorded on that date. Record any missing supplier credit or opening balance first.')


def record_adjustment(db, bill, kind, amount, effective_date, note='', adjustment=None, request_token=None):
    if kind not in BillAdjustment.LABELS:
        raise ValueError('Choose a valid adjustment type.')
    cents = money_cents(amount)
    if not cents:
        raise ValueError('Enter an amount greater than zero.')
    if not isinstance(effective_date, datetime):
        raise ValueError('Enter the date the adjustment took effect.')
    effective_date = _midnight(effective_date)
    values = {'kind': kind, 'amount': cents, 'date': effective_date.isoformat(), 'note': note.strip()}
    if adjustment is not None:
        values['correction_id'] = adjustment.id
    fingerprint = _fingerprint(values)
    previous = submitted_record(db, 'adjustment', bill, request_token, fingerprint, adjustment)
    if previous:
        return previous
    if adjustment is not None and adjustment.bill_id != bill.id:
        raise ValueError('Adjustment does not belong to this bill.')
    if kind == 'refund':
        validate_cash_date(effective_date, 'Cash refund')
    else:
        validate_adjustment_date(effective_date, BillAdjustment.LABELS[kind])
    enable_ledger(db, bill)
    if kind.startswith('opening_'):
        if any(a != adjustment and a.kind.startswith('opening_') for a in bill.adjustments):
            raise ValueError('An opening balance already exists. Edit that record to correct it.')
        dates = [invoice_event_date(i) for i in bill.invoices] + [_midnight(p.paid_date) for p in bill.payments]
        dates += [_midnight(a.effective_date) for a in bill.adjustments if a != adjustment]
        if dates and effective_date > min(dates):
            raise ValueError('An opening balance must be dated on or before the first recorded account event.')
    if kind == 'refund':
        validate_refund_credit(bill, cents, effective_date, adjustment)
    if adjustment is None:
        adjustment = BillAdjustment(bill=bill, request_token=request_token or None, request_fingerprint=fingerprint)
        db.add(adjustment)
    adjustment.kind, adjustment.amount_cents = kind, cents
    adjustment.effective_date, adjustment.note = effective_date, note.strip() or None
    db.flush()
    if request_token:
        retain_record_submission(db, 'adjustment', adjustment, request_token, fingerprint)
    reconcile_allocations(db, bill)
    return adjustment


def record_statement(db, bill, amount, balance_type, as_of_date, note='', statement=None, request_token=None):
    if balance_type not in ('owing', 'credit'):
        raise ValueError('Choose owing or credit for the supplier balance.')
    cents = money_cents(amount) * (-1 if balance_type == 'credit' else 1)
    if not isinstance(as_of_date, datetime):
        raise ValueError('Enter the date shown by the supplier.')
    as_of_date = _midnight(as_of_date)
    values = {'balance': cents, 'date': as_of_date.isoformat(), 'note': note.strip()}
    if statement is not None:
        values['correction_id'] = statement.id
    fingerprint = _fingerprint(values)
    previous = submitted_record(db, 'statement', bill, request_token, fingerprint, statement)
    if previous:
        return previous
    if statement is not None and statement.bill_id != bill.id:
        raise ValueError('Statement does not belong to this bill.')
    enable_ledger(db, bill)
    if statement is None:
        statement = BillStatement(bill=bill, request_token=request_token or None, request_fingerprint=fingerprint)
        db.add(statement)
    statement.balance_cents, statement.as_of_date = cents, as_of_date
    statement.note = note.strip() or None
    db.flush()
    if request_token:
        retain_record_submission(db, 'statement', statement, request_token, fingerprint)
    return statement


def statement_comparisons(bill):
    rows = [{'as_of_date': s.as_of_date, 'supplier_cents': s.balance_cents, 'source': 'Account statement',
             'note': s.note, 'statement': s, 'invoice': None} for s in bill.statements]
    rows += [{'as_of_date': i.invoice_date, 'supplier_cents': i.statement_balance_cents,
              'source': 'Invoice ' + i.invoice_number if i.invoice_number else 'Invoice statement', 'note': None,
              'statement': None, 'invoice': i} for i in bill.invoices if i.statement_balance_cents is not None]
    for row in rows:
        if row['as_of_date'] is None:
            row.update(app_cents=None, difference_cents=None, status='Issue date unknown')
        else:
            balance, complete = balance_as_of(bill, row['as_of_date'])
            difference = row['supplier_cents'] - balance
            row.update(app_cents=balance, difference_cents=difference,
                       status='Missing invoice issue dates' if not complete else 'Match' if difference == 0 else 'Difference')
    return sorted(rows, key=lambda row: row['as_of_date'] or datetime.min, reverse=True)
