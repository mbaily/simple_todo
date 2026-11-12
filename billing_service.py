"""Invoice allocation in integer cents. Callers commit the complete transaction."""
import calendar
import hashlib
import json
from datetime import datetime, timedelta
from datetime import date as date_type
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_
from models import BillAllocation, BillInvoice, BillPayment, BillPaymentSubmission, BillAdjustmentAllocation, utcnow
from helpers import _midnight
from billing_submissions import submitted_record, retain_record_submission

_UNSET = object()


class DuplicateInvoiceError(ValueError):
    def __init__(self, invoice, same_number=False):
        self.invoice_id = invoice.id
        self.same_number = same_number
        super().__init__('An invoice with these dates or details is already recorded.')


def validate_invoice_account(bill, invoice):
    if invoice is not None and invoice.bill_id != bill.id:
        raise ValueError('The payment and invoice must belong to the same bill account.')


def sync_invoice_due_date(bill, invoice, due):
    validate_invoice_account(bill, invoice)
    previous_due = invoice.due_date
    invoice.due_date = due
    if due is not None:
        for payment in bill.payments:
            if payment.invoice_id == invoice.id:
                payment.due_date = due
    return previous_due


def refresh_invoice_forecast(bill, invoice, previous_due):
    if (previous_due == invoice.due_date or bill.open_invoice
            or bill.ordered_invoices[-1] != invoice or bill.is_due_date_confirmed):
        return
    if invoice.due_date:
        if not bill.anchor_day or (previous_due and bill.anchor_day == previous_due.day):
            bill.anchor_day = invoice.due_date.day
        bill.next_due_date = advance_due_date(invoice.due_date, bill.frequency,
                                              bill.frequency_days, bill.anchor_day)
    else:
        bill.next_due_date = None
    bill.modified_at = utcnow()


def _invoice_fingerprint(cents, due, amount_type, note, metadata):
    values = {'amount': cents, 'due': due.isoformat() if due else None,
              'type': amount_type, 'note': note.strip()}
    values.update({field: (None if value is _UNSET else value.isoformat()
                          if isinstance(value, datetime) else value)
                   for field, value in metadata.items()})
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _possible_duplicate(bill, cents, due, metadata, fingerprint):
    issued = metadata['invoice_date']
    start, end = metadata['period_start'], metadata['period_end']
    for existing in bill.invoices:
        number_key = metadata.get('invoice_number_key')
        if number_key and existing.invoice_number_key and existing.invoice_number_key != number_key:
            continue
        if fingerprint and existing.request_fingerprint == fingerprint:
            return existing
        if issued is not _UNSET and issued is not None:
            matches = existing.invoice_date == issued
        elif start is not _UNSET and end is not _UNSET and start and end:
            matches = existing.period_start == start and existing.period_end == end
        elif due:
            matches = existing.due_date == due
        else:
            matches = (not existing.invoice_date and not existing.due_date
                       and existing.amount_cents == cents)
        if matches:
            if start is not _UNSET and start and existing.period_start and existing.period_start != start:
                continue
            if end is not _UNSET and end and existing.period_end and existing.period_end != end:
                continue
            return existing
    return None

def money_cents(value):
    try:
        amount = Decimal(str(value).strip().replace('$', '').replace(',', ''))
        if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal('0.01')):
            raise ValueError('Enter a non-negative amount with at most two decimal places.')
        cents = int(amount * 100)
        if cents > 9223372036854775807:
            raise ValueError('Amount is too large.')
        return cents
    except (InvalidOperation, TypeError):
        raise ValueError('Enter a valid monetary amount.') from None


def stored_amount(cents, label):
    """Keep dollar fields consistent with the validated integer cents."""
    amount = cents / 100
    if money_cents(amount) != cents or money_cents(f'{amount:.2f}') != cents:
        raise ValueError(f'{label} cannot be stored precisely. Enter a smaller amount.')
    return amount


def format_cents(cents: int) -> str:
    whole, fraction = divmod(abs(cents), 100)
    return f'{"-" if cents < 0 else ""}{whole}.{fraction:02d}'


def bill_amount(bill) -> str:
    if bill.ledger_enabled and bill.outstanding_cents:
        return format_cents(bill.outstanding_cents)
    amount = bill.effective_amount
    return f'{amount:.2f}' if amount is not None else ''


def monthly_budget(bill):
    amount = bill.budget_amount
    if amount is None or amount < 0:
        return 0.0
    if bill.frequency in ('weekly', 'fortnightly'):
        return amount * {'weekly': 4.33, 'fortnightly': 2.17}[bill.frequency]
    if bill.frequency == 'custom' and bill.frequency_days and bill.frequency_days > 0:
        return amount * (30.0 / bill.frequency_days)
    return amount / {'bimonthly': 2, 'quarterly': 3, 'yearly': 12}.get(bill.frequency, 1)


def estimate_amount(value, label='Recurring estimate'):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        cents = money_cents(value)
    except (ValueError, TypeError) as error:
        raise ValueError(f'{label}: {error}') from None
    return stored_amount(cents, label)


def validate_cash_date(value, label='Payment'):
    """Cash received or paid is a historical event, not a scheduled transfer."""
    if not isinstance(value, datetime):
        raise ValueError(f'{label} needs a valid date.')
    if value.date() > date_type.today():
        raise ValueError(f'{label} cannot be dated in the future. Record it after the money has been received or paid.')


def validate_adjustment_date(value, label='Account adjustment'):
    if not isinstance(value, datetime):
        raise ValueError(f'{label} needs a valid effective date.')
    if value.date() > date_type.today():
        raise ValueError(f'{label} cannot be dated in the future. Record it once the adjustment takes effect.')


def lock_account(db):
    """Serialize balance-changing requests before reading an account's receipts."""
    if db.get_bind().dialect.name == 'sqlite':
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN IMMEDIATE')


def advance_due_date(current_due, frequency, frequency_days=None, anchor_day=None):
    current_due = _midnight(current_due) or _midnight(datetime.now())
    days = {'weekly': 7, 'fortnightly': 14}.get(frequency)
    if frequency == 'custom' and frequency_days and frequency_days > 0:
        days = frequency_days
    if days:
        try:
            return current_due + timedelta(days=days)
        except OverflowError:
            raise ValueError('The billing recurrence advances beyond the supported date range.') from None
    months = {'bimonthly': 2, 'quarterly': 3, 'yearly': 12}.get(frequency, 1)
    total = current_due.year * 12 + current_due.month - 1 + months
    year, month = total // 12, total % 12 + 1
    if year > datetime.max.year:
        raise ValueError('The billing recurrence advances beyond the supported date range.')
    anchor = anchor_day if anchor_day and 1 <= anchor_day <= 31 else current_due.day
    return datetime(year, month, min(anchor, calendar.monthrange(year, month)[1]))


def validate_recurrence_settings(frequency, interval, anchor, due=None, label='Bill recurrence'):
    frequencies = {'weekly', 'fortnightly', 'monthly', 'bimonthly', 'quarterly', 'yearly', 'custom'}
    maximum_days = (datetime.max - datetime.min).days
    if not isinstance(frequency, str) or frequency not in frequencies:
        raise ValueError(f'{label}: Choose a supported billing frequency.')
    if interval is not None and (type(interval) is not int or not 1 <= interval <= maximum_days):
        raise ValueError(f'{label}: Custom days must be a positive integer within the supported date range.')
    if anchor is not None and (type(anchor) is not int or not 1 <= anchor <= 31):
        raise ValueError(f'{label}: Anchor day must be an integer from 1 to 31 or null.')
    if frequency == 'custom' and interval is None:
        raise ValueError(f'{label}: Enter a positive integer custom interval in days.')
    try:
        advance_due_date(due, frequency, interval, anchor)
    except ValueError as error:
        raise ValueError(f'{label}: {error}') from None


def enable_ledger(db, bill):
    """Preserve old settled payment history without inventing an opening credit."""
    if bill.ledger_enabled:
        return
    bill.ledger_enabled = True
    for payment in sorted(bill.payments, key=lambda p: (p.due_date, p.id)):
        if payment.amount_paid is None:
            continue
        payment.amount_cents = money_cents(round(payment.amount_paid, 2))
        invoice = BillInvoice(
            bill=bill, due_date=payment.due_date, amount_cents=payment.amount_cents,
            is_legacy=True, settled_at=payment.paid_date, schedule_advanced=True,
            note='Imported from payment history; original invoice charge was not recorded.',
        )
        db.add(invoice)
        db.flush()
        payment.invoice_id = invoice.id
    reconcile_allocations(db, bill, update_schedule=False)


def invoice_event_date(invoice):
    return _midnight(invoice.invoice_date or invoice.due_date or invoice.created_at or utcnow())


def validate_opening_balance(bill):
    openings = [a for a in bill.adjustments if a.kind.startswith('opening_')]
    if len(openings) > 1:
        raise ValueError('An opening balance already exists. Edit that record to correct it.')
    if not openings:
        return
    dates = [invoice_event_date(i) for i in bill.invoices]
    dates += [_midnight(p.paid_date) for p in bill.payments]
    dates += [_midnight(a.effective_date) for a in bill.adjustments if a not in openings]
    if dates and _midnight(openings[0].effective_date) > min(dates):
        raise ValueError('An opening balance must be dated on or before the first recorded account event.')


def balance_as_of(bill, as_of, exclude_adjustment=None):
    """Return the dated account balance and whether all charge dates are known."""
    cutoff = _midnight(as_of)
    balance = sum(i.amount_cents for i in bill.invoices if invoice_event_date(i) <= cutoff)
    balance -= sum((p.amount_cents if p.amount_cents is not None else round((p.amount_paid or 0) * 100))
                   for p in bill.payments if _midnight(p.paid_date) <= cutoff)
    balance += sum(a.balance_delta_cents for a in bill.adjustments
                   if a != exclude_adjustment and _midnight(a.effective_date) <= cutoff)
    return balance, all(i.invoice_date is not None for i in bill.invoices)


def credit_before_invoice(bill, issued, invoice=None):
    """Recover credit only when date-level history establishes transaction order."""
    if not isinstance(issued, datetime):
        raise ValueError('Enter the invoice issue date to calculate the charge from an amount owing after credit, or enter the charge before credit.')
    others = [i for i in bill.invoices if i != invoice]
    if any(i.invoice_date is None for i in others):
        raise ValueError('Enter missing invoice issue dates before calculating historical credit, or enter the invoice charge before credit.')
    cutoff = _midnight(issued)
    if (any(p.amount_cents and _midnight(p.paid_date) == cutoff for p in bill.payments)
            or any(_midnight(a.effective_date) == cutoff for a in bill.adjustments)
            or any(i.amount_cents and _midnight(i.invoice_date) == cutoff for i in others)):
        raise ValueError('Transactions share the invoice issue date. Enter the credit applied shown on the invoice, including zero, or enter the charge before credit.')
    charges = sum(i.amount_cents for i in others if _midnight(i.invoice_date) < cutoff)
    receipts = sum(p.amount_cents or 0 for p in bill.payments if _midnight(p.paid_date) < cutoff)
    adjustments = sum(a.balance_delta_cents for a in bill.adjustments if _midnight(a.effective_date) < cutoff)
    return max(0, receipts - charges - adjustments)


def payment_submission(db, bill, request_token, **submitted):
    """Check the submitted fields before deriving an amount, date or invoice from current state."""
    if not request_token:
        return None, None
    if len(request_token) > 64:
        raise ValueError('Reload the payment form and try again.')
    values = {key: value.isoformat() if isinstance(value, datetime) else value for key, value in submitted.items()}
    fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    previous = db.query(BillPaymentSubmission).filter_by(request_token=request_token).first()
    if previous is None:
        # Forms recorded before durable submission records were introduced.
        previous = db.query(BillPayment).filter_by(request_token=request_token).first()
    if previous and (previous.bill_id != bill.id or previous.request_fingerprint != fingerprint):
        raise ValueError('This payment form was already submitted with different details. Reload the page or edit the recorded payment.')
    if isinstance(previous, BillPaymentSubmission) and previous.is_deleted:
        raise ValueError('This payment was deleted. Reload History and use a new form to record a new payment.')
    if (previous and submitted.get('operation') == 'edit'
            and getattr(previous, 'payment_id', previous.id) != submitted['payment']):
        raise ValueError('This form belongs to a different payment. Reload History and use a new form.')
    return previous, fingerprint


def retain_payment_submission(db, payment, deleted=False, request_token=None, request_fingerprint=None):
    if deleted:
        db.query(BillPaymentSubmission).filter_by(bill_id=payment.bill_id, payment_id=payment.id).update(
            {'is_deleted': True}, synchronize_session='fetch')
    token = request_token or payment.request_token
    fingerprint = payment.request_fingerprint if request_fingerprint is None else request_fingerprint
    if not token:
        return
    previous = db.query(BillPaymentSubmission).filter_by(request_token=token).first()
    if previous is None:
        previous = BillPaymentSubmission(bill_id=payment.bill_id, payment_id=payment.id,
            request_token=token, request_fingerprint=fingerprint)
        db.add(previous)
    elif (previous.bill_id != payment.bill_id or previous.request_fingerprint != fingerprint
            or previous.payment_id not in (None, payment.id)):
        raise ValueError('This payment form was already used for a different payment.')
    elif previous.is_deleted and not deleted:
        raise ValueError('This payment form belongs to a deleted payment. Use a new form.')
    previous.payment_id = payment.id
    previous.is_deleted = bool(deleted or previous.is_deleted)


def reconcile_allocations(db, bill, update_schedule=True):
    """Allocate receipts to invoices in date order, without double spending credit."""
    db.flush()
    validate_opening_balance(bill)
    invoices = bill.ordered_invoices
    payments = sorted(bill.payments, key=lambda p: (p.paid_date, p.id))
    adjustments = list(bill.adjustments)
    if (any(i.amount_cents < 0 for i in invoices) or any((p.amount_cents or 0) < 0 for p in payments)
            or any(a.amount_cents <= 0 for a in adjustments)):
        raise ValueError('Invoice charges and cash payments cannot be negative.')
    ids = [i.id for i in invoices]
    if ids:
        db.query(BillAllocation).filter(BillAllocation.invoice_id.in_(ids)).delete(synchronize_session=False)
    adjustment_ids = [a.id for a in adjustments]
    db.query(BillAdjustmentAllocation).filter(or_(
        BillAdjustmentAllocation.invoice_id.in_(ids),
        BillAdjustmentAllocation.debit_adjustment_id.in_(adjustment_ids),
        BillAdjustmentAllocation.credit_adjustment_id.in_(adjustment_ids),
        BillAdjustmentAllocation.payment_id.in_([p.id for p in payments]),
    )).delete(synchronize_session=False)
    for invoice in invoices:
        db.expire(invoice, ['allocations', 'adjustment_allocations'])
    for payment in payments:
        db.expire(payment, ['allocations', 'adjustment_allocations'])
    for adjustment in adjustments:
        db.expire(adjustment, ['credit_allocations', 'debit_allocations'])
    sources = [(p.paid_date, 'payment', p) for p in payments]
    sources += [(a.effective_date, 'adjustment', a) for a in adjustments if a.is_credit]
    sources.sort(key=lambda row: (_midnight(row[0]), row[1], row[2].id))
    available = {(kind, entry.id): entry.amount_cents or 0 for _, kind, entry in sources}
    targets = [(invoice_event_date(i), 'invoice', i) for i in invoices]
    targets += [(a.effective_date, 'adjustment', a) for a in adjustments if not a.is_credit]
    targets.sort(key=lambda row: (_midnight(row[0]), row[1], row[2].id))
    newly_settled = []
    for _, target_kind, target in targets:
        owing = target.amount_cents
        settlement_dates = [_midnight(target.invoice_date)] if target_kind == 'invoice' and target.invoice_date else []
        for source_date, source_kind, source in sources:
            key = (source_kind, source.id)
            amount = min(owing, available[key])
            if amount:
                if source_kind == 'payment' and target_kind == 'invoice':
                    db.add(BillAllocation(invoice=target, payment=source, amount_cents=amount,
                                          is_credit=source.invoice_id != target.id))
                else:
                    db.add(BillAdjustmentAllocation(amount_cents=amount,
                        invoice=target if target_kind == 'invoice' else None,
                        debit_adjustment=target if target_kind == 'adjustment' else None,
                        payment=source if source_kind == 'payment' else None,
                        credit_adjustment=source if source_kind == 'adjustment' else None))
                available[key] -= amount
                owing -= amount
                settlement_dates.append(_midnight(source_date))
            if owing == 0:
                break
        if target_kind != 'invoice':
            continue
        invoice = target
        if owing == 0:
            invoice.settled_at = (max(settlement_dates) if invoice.invoice_date else
                                  invoice.settled_at or utcnow())
            if not invoice.schedule_advanced:
                newly_settled.append(invoice)
                invoice.schedule_advanced = True
        else:
            invoice.settled_at = None
            invoice.schedule_advanced = False
    db.flush()
    if update_schedule:
        open_invoice = bill.open_invoice
        if open_invoice:
            bill.next_due_date = open_invoice.due_date
            bill.is_due_date_confirmed = True
        elif newly_settled:
            latest = invoices[-1]
            # A backfilled invoice can settle immediately using spare credit.
            # Base the forecast on the latest invoice and preserve an explicitly
            # confirmed future date when only older invoices have settled.
            invoice_due_dates = {i.due_date for i in invoices}
            if (latest in newly_settled or not bill.is_due_date_confirmed
                    or bill.next_due_date in invoice_due_dates):
                bill.next_due_date = advance_due_date(latest.due_date, bill.frequency, bill.frequency_days,
                                                    bill.anchor_day) if latest.due_date else None
                bill.is_due_date_confirmed = False
        bill.modified_at = utcnow()


def record_invoice(db, bill, amount, due_date=None, amount_type='gross', note=_UNSET, invoice=None,
                   invoice_date=_UNSET, period_start=_UNSET, period_end=_UNSET,
                   statement_balance_cents=_UNSET, request_token=None, allow_duplicate=False,
                   invoice_number=_UNSET, credit_applied_cents=_UNSET):
    validate_invoice_account(bill, invoice)
    enable_ledger(db, bill)
    if amount_type not in ('gross', 'net'):
        raise ValueError('Choose invoice charge or amount owing after credit.')
    cents = money_cents(amount)
    metadata = {'invoice_date': invoice_date, 'period_start': period_start, 'period_end': period_end,
                'statement_balance_cents': statement_balance_cents}
    number = None if invoice_number is _UNSET else (invoice_number or '').strip() or None
    number_key = number.casefold() if number else None
    if number_key:
        if len(number) > 255 or len(number_key) > 255:
            raise ValueError('Invoice numbers must be at most 255 characters.')
        metadata.update(invoice_number=number, invoice_number_key=number_key)
    if credit_applied_cents is not _UNSET:
        if amount_type != 'net':
            raise ValueError('Credit applied is only used with an amount owing after credit. Enter the charge before credit without this field.')
        if not isinstance(credit_applied_cents, int) or credit_applied_cents < 0:
            raise ValueError('Enter a non-negative credit applied amount.')
        metadata['credit_applied_cents'] = credit_applied_cents
    if invoice is not None:
        metadata['correction_id'] = invoice.id
    submitted_note = '' if note is _UNSET else note or ''
    fingerprint = _invoice_fingerprint(cents, due_date, amount_type, submitted_note, metadata)
    previous = submitted_record(db, 'invoice', bill, request_token, fingerprint, invoice)
    if previous:
        return previous
    if number_key:
        duplicate_number = db.query(BillInvoice).filter_by(bill_id=bill.id, invoice_number_key=number_key).first()
        if duplicate_number and (invoice is None or duplicate_number.id != invoice.id):
            raise DuplicateInvoiceError(duplicate_number, same_number=True)
    if amount_type == 'net':
        issued = invoice.invoice_date if invoice_date is _UNSET and invoice is not None else invoice_date
        if not isinstance(issued, datetime):
            raise ValueError('Enter the invoice issue date to calculate the charge from an amount owing after credit, or enter the charge before credit.')
        cents += credit_before_invoice(bill, issued, invoice) if credit_applied_cents is _UNSET else credit_applied_cents
        if cents > 9223372036854775807:
            raise ValueError('Amount is too large.')
    previous_due = invoice.due_date if invoice else due_date
    if invoice is None:
        duplicate = _possible_duplicate(bill, cents, due_date, metadata, fingerprint)
        if duplicate and not allow_duplicate:
            raise DuplicateInvoiceError(duplicate)
        invoice = BillInvoice(bill=bill, amount_cents=cents, due_date=due_date, note=submitted_note or None)
        invoice.request_token = request_token or None
        invoice.request_fingerprint = fingerprint
        db.add(invoice)
    else:
        invoice.amount_cents = cents
        invoice.due_date = due_date
        if note is not _UNSET:
            invoice.note = note or None
        invoice.is_legacy = False
    if invoice_number is not _UNSET:
        invoice.invoice_number, invoice.invoice_number_key = number, number_key
    for field, value in [('invoice_date', invoice_date), ('period_start', period_start),
                         ('period_end', period_end), ('statement_balance_cents', statement_balance_cents)]:
        if value is not _UNSET:
            setattr(invoice, field, value)
    if invoice.period_start and invoice.period_end and invoice.period_end < invoice.period_start:
        raise ValueError('The service period end must be on or after its start.')
    db.flush()
    if request_token:
        retain_record_submission(db, 'invoice', invoice, request_token, fingerprint)
    sync_invoice_due_date(bill, invoice, due_date)
    reconcile_allocations(db, bill)
    refresh_invoice_forecast(bill, invoice, previous_due)
    return invoice


def record_cash_payment(db, bill, cents, paid_date, invoice=None, note=None,
                        request_token=None, request_fingerprint=None):
    validate_invoice_account(bill, invoice)
    validate_cash_date(paid_date)
    paid_amount = stored_amount(cents, 'Payment')
    enable_ledger(db, bill)
    payment = BillPayment(bill=bill, invoice_id=invoice.id if invoice else None,
                          due_date=(invoice.due_date if invoice else None) or paid_date,
                          paid_date=paid_date, amount_cents=cents, amount_paid=paid_amount, note=note,
                          request_token=request_token or None, request_fingerprint=request_fingerprint)
    db.add(payment)
    if request_token:
        db.flush()
        retain_payment_submission(db, payment)
    reconcile_allocations(db, bill)
    return payment
