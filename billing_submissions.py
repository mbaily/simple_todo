"""Remember billing form submissions without retaining deleted financial records."""
import hashlib
import json
from datetime import datetime

from models import BillInvoice, BillAdjustment, BillStatement, BillRecordSubmission, BillDueDateSubmission

RECORD_MODELS = {'invoice': BillInvoice, 'adjustment': BillAdjustment, 'statement': BillStatement}


def submission_recorded(db, record_type, token):
    if not token:
        return False
    return (db.query(BillRecordSubmission.id).filter_by(record_type=record_type, request_token=token).first() is not None
            or db.query(RECORD_MODELS[record_type].id).filter_by(request_token=token).first() is not None)


def submitted_record(db, record_type, bill, token, fingerprint, existing=None):
    if not token:
        return None
    if len(token) > 64:
        raise ValueError('Reload the form and try again.')
    if record_type == 'invoice' and db.query(BillDueDateSubmission.id).filter_by(request_token=token).first():
        raise ValueError('This form was already submitted with different details. Reload the page or edit the record.')
    model = RECORD_MODELS[record_type]
    submission = db.query(BillRecordSubmission).filter_by(record_type=record_type, request_token=token).first()
    previous = submission or db.query(model).filter_by(request_token=token).first()
    if previous is None:
        return None
    record_id = submission.record_id if submission else previous.id
    if (previous.bill_id != bill.id or previous.request_fingerprint != fingerprint
            or (existing is not None and record_id != existing.id)):
        raise ValueError('This form was already submitted with different details. Reload the page or edit the record.')
    record = db.get(model, record_id) if submission else previous
    if (submission and submission.is_deleted) or record is None or record.bill_id != bill.id:
        raise ValueError('This record was deleted. Reload History and use a new form to record a new entry.')
    return record


def retain_record_submission(db, record_type, record, token=None, fingerprint=None):
    token = token or record.request_token
    if not token:
        return
    fingerprint = record.request_fingerprint if fingerprint is None else fingerprint
    previous = db.query(BillRecordSubmission).filter_by(record_type=record_type, request_token=token).first()
    if previous:
        if (previous.bill_id != record.bill_id or previous.record_id != record.id
                or previous.request_fingerprint != fingerprint):
            raise ValueError('This form was already used for different details.')
        return
    db.add(BillRecordSubmission(record_type=record_type, bill_id=record.bill_id, record_id=record.id,
        request_token=token, request_fingerprint=fingerprint, is_deleted=False))


def mark_record_deleted(db, record_type, record):
    # Original forms from older app versions may not have a separate submission.
    retain_record_submission(db, record_type, record)
    db.flush()
    db.query(BillRecordSubmission).filter_by(record_type=record_type, bill_id=record.bill_id,
        record_id=record.id).update({'is_deleted': True}, synchronize_session='fetch')


def mark_bill_records_deleted(db, bill):
    for record_type, records in [('invoice', bill.invoices), ('adjustment', bill.adjustments), ('statement', bill.statements)]:
        for record in records:
            mark_record_deleted(db, record_type, record)
    db.query(BillRecordSubmission).filter_by(bill_id=bill.id).update({'is_deleted': True}, synchronize_session='fetch')
    db.query(BillDueDateSubmission).filter_by(bill_id=bill.id).update({'is_deleted': True}, synchronize_session='fetch')


def due_date_submission(db, bill, token, **submitted):
    if not token:
        return None, None
    if len(token) > 64:
        raise ValueError('Reload the form and try again.')
    if submission_recorded(db, 'invoice', token):
        raise ValueError('This form was already submitted with different details. Reload the page or edit the record.')
    values = {key: value.isoformat() if isinstance(value, datetime) else value for key, value in submitted.items()}
    fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    previous = db.query(BillDueDateSubmission).filter_by(request_token=token).first()
    if previous:
        if previous.bill_id != bill.id or previous.request_fingerprint != fingerprint:
            raise ValueError('This form was already submitted with different details. Reload the page to confirm another date.')
        if previous.is_deleted:
            raise ValueError('This bill was deleted. Reload the page and use a new form.')
    return previous, fingerprint
