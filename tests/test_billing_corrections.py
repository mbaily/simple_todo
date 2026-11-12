"""Regressions for invoice corrections and retries after removing cash."""
import copy

import pytest
from sqlalchemy import text

from test_billing_accounts import account, adjustment, day, invoice, post
from models import Bill, BillPayment, BillPaymentSubmission, JournalEntry
from billing_service import record_invoice
from import_logic import import_data_from_json


@pytest.mark.parametrize('replacement', ['', 'Corrected note'])
def test_invoice_edit_saves_empty_or_replaced_note_without_changing_money(account, replacement):
    db, bill, client = account
    inv = invoice(client, bill, '100', due_date='2026-10-26', note='Original note')
    before = (inv.amount_cents, inv.due_date, inv.invoice_date, bill.outstanding_cents,
              bill.credit_cents, bill.next_due_date)
    post(client, f'/bills/{bill.id}/invoices', dict(invoice_id=inv.id, amount='100',
         due_date='2026-10-26', invoice_date='2026-10-02', note=replacement))
    db.refresh(inv)
    assert inv.note == (replacement or None)
    assert (inv.amount_cents, inv.due_date, inv.invoice_date, bill.outstanding_cents,
            bill.credit_cents, bill.next_due_date) == before
    assert client.get(f'/bills/{bill.id}/history').status_code == 200


def test_corrections_that_omit_invoice_note_preserve_it(account):
    db, bill, client = account
    inv = invoice(client, bill, '100', due_date='2026-10-26', note='Keep this note')
    record_invoice(db, bill, '120', inv.due_date, invoice=inv)
    db.commit()
    assert inv.note == 'Keep this note'
    post(client, f'/bills/{bill.id}/confirm-invoice', dict(invoice_target=inv.id,
         actual_amount='130', actual_due_date='2026-10-26', invoice_date='2026-10-02'))
    assert inv.note == 'Keep this note' and inv.amount_cents == 13000


def test_clearing_invoice_note_retry_preserves_newer_note_and_journal(account):
    db, bill, client = account
    inv = invoice(client, bill, '100', due_date='2026-10-26', note='Original note')
    path = f'/bills/{bill.id}/invoices'
    fields = dict(invoice_id=inv.id, amount='100', due_date='2026-10-26',
                  invoice_date='2026-10-02', note='', request_token='clear-note-once')
    post(client, path, fields)
    assert inv.note is None
    post(client, path, {**fields, 'note': 'Newer note', 'request_token': 'replace-note-once'})
    journal_count = db.query(JournalEntry).count()
    post(client, path, fields)
    assert inv.note == 'Newer note' and db.query(JournalEntry).count() == journal_count
    response = client.post(path, data={**fields, 'note': 'Changed old submission'}, follow_redirects=False)
    assert response.status_code == 400 and inv.note == 'Newer note'
    assert db.query(JournalEntry).count() == journal_count


def test_same_day_extra_cash_does_not_inflate_net_invoice_edit(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '50', 'paid_date': '2026-09-01'})
    inv = invoice(client, bill, '150', date='2026-09-10')
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '100', 'paid_date': '2026-09-10'})
    cash = [(p.id, p.amount_cents, p.paid_date) for p in bill.payments]
    fields = {'invoice_id': inv.id, 'amount': '100', 'amount_type': 'net'}
    response = client.post(f'/bills/{bill.id}/invoices', data=fields)
    assert response.status_code == 400 and 'share the invoice issue date' in response.text
    assert inv.amount_cents == 15000 and bill.outstanding_cents == 0
    post(client, f'/bills/{bill.id}/invoices', {**fields, 'credit_applied': '50'})
    assert inv.amount_cents == 15000 and bill.outstanding_cents == bill.credit_cents == 0
    assert [(p.id, p.amount_cents, p.paid_date) for p in bill.payments] == cash


@pytest.mark.parametrize('event', ['cash', 'adjustment', 'invoice'])
def test_net_creation_requires_explicit_credit_for_same_day_events(account, event):
    db, bill, client = account
    if event == 'cash':
        post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '50', 'paid_date': '2026-09-10'})
    elif event == 'adjustment':
        adjustment(client, bill, 'supplier_credit', '50', date='2026-09-10')
    else:
        invoice(client, bill, '20', date='2026-09-10', invoice_number='Other')
    fields = {'amount': '100', 'amount_type': 'net', 'invoice_date': '2026-09-10', 'invoice_number': 'New'}
    count = len(bill.invoices)
    response = client.post(f'/bills/{bill.id}/invoices', data=fields)
    assert response.status_code == 400 and len(bill.invoices) == count
    post(client, f'/bills/{bill.id}/invoices', {**fields, 'credit_applied': '0'})
    assert bill.invoices[-1].amount_cents == 10000


@pytest.mark.parametrize('credit,amount_type', [('-1', 'net'), ('nan', 'net'), ('1.001', 'net'),
                                             ('10', 'gross'), ('92233720368547758.07', 'net')])
def test_invalid_explicit_credit_is_atomic(account, credit, amount_type):
    db, bill, client = account
    response = client.post(f'/bills/{bill.id}/invoices', data={'amount': '1', 'amount_type': amount_type,
                           'credit_applied': credit, 'invoice_date': '2026-09-10'})
    assert response.status_code == 400 and not bill.invoices and not bill.payments
    db.refresh(bill)
    assert not bill.ledger_enabled


def test_manual_credit_requires_issue_date_and_does_not_fabricate_cash(account):
    db, bill, client = account
    fields = {'amount': '100', 'amount_type': 'net', 'credit_applied': '50'}
    response = client.post(f'/bills/{bill.id}/invoices', data=fields)
    assert response.status_code == 400 and not bill.invoices
    post(client, f'/bills/{bill.id}/invoices', {**fields, 'invoice_date': '2026-09-10'})
    assert bill.invoices[0].amount_cents == 15000 and bill.outstanding_cents == 15000
    assert not bill.payments and not bill.adjustments


def test_manual_credit_retry_stays_stable_when_history_changes(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '50', 'paid_date': '2026-09-10'})
    fields = {'amount': '100', 'amount_type': 'net', 'credit_applied': '50',
              'invoice_date': '2026-09-10', 'request_token': 'net-explicit-once'}
    post(client, f'/bills/{bill.id}/invoices', fields)
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '100', 'paid_date': '2026-09-20'})
    post(client, f'/bills/{bill.id}/invoices', fields)
    assert len(bill.invoices) == 1 and bill.invoices[0].amount_cents == 15000
    assert bill.outstanding_cents == bill.credit_cents == 0
    response = client.post(f'/bills/{bill.id}/invoices', data={**fields, 'credit_applied': '51'})
    assert response.status_code == 400 and bill.invoices[0].amount_cents == 15000


def test_new_confirm_with_shared_due_date_preserves_existing_debt(account):
    db, bill, client = account
    path = f'/bills/{bill.id}/confirm-invoice'
    post(client, path, {'actual_amount': '100', 'invoice_date': '2026-09-10', 'actual_due_date': '2026-09-26'})
    first = bill.invoices[0]
    post(client, path, {'actual_amount': '200', 'invoice_date': '2026-09-20', 'actual_due_date': '2026-09-26'})
    assert len(bill.invoices) == 2 and first.amount_cents == 10000
    assert bill.outstanding_cents == 30000


def test_confirm_can_explicitly_correct_an_older_invoice(account):
    db, bill, client = account
    older = invoice(client, bill, '100', date='2026-09-10', due_date='2026-09-26', invoice_number='A')
    newer = invoice(client, bill, '200', date='2026-10-10', due_date='2026-10-26', invoice_number='B')
    post(client, f'/bills/{bill.id}/confirm-invoice', {'invoice_target': older.id,
         'actual_amount': '120', 'invoice_date': '2026-09-10', 'actual_due_date': '2026-09-27', 'invoice_number': 'A'})
    assert len(bill.invoices) == 2 and older.amount_cents == 12000 and newer.amount_cents == 20000
    assert older.due_date == day('2026-09-27') and newer.due_date == day('2026-10-26')
    assert bill.outstanding_cents == 32000


@pytest.mark.parametrize('target', ['bad', '0', '99999', 'foreign'])
def test_confirm_rejects_invalid_correction_selection(account, target):
    db, bill, client = account
    inv = invoice(client, bill, '100')
    if target == 'foreign':
        other = Bill(name='Other')
        db.add(other); db.commit()
        target = invoice(client, other, '50').id
    response = client.post(f'/bills/{bill.id}/confirm-invoice', data={'invoice_target': target,
                           'actual_amount': '200', 'actual_due_date': '2026-10-26'})
    assert response.status_code == 400 and inv.amount_cents == 10000 and len(bill.invoices) == 1


def test_default_new_confirm_warns_for_unknown_issue_date_shared_due(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/invoices', {'amount': '100', 'due_date': '2026-10-26'})
    response = client.post(f'/bills/{bill.id}/confirm-invoice', data={'actual_amount': '200',
                           'actual_due_date': '2026-10-26', 'credit_applied': '', 'request_token': 'new-confirm'})
    assert response.status_code == 409 and 'name="invoice_target" value="new"' in response.text
    assert bill.invoices[0].amount_cents == 10000 and len(bill.invoices) == 1


def test_confirm_cannot_move_due_date_away_from_outstanding_debt_without_amount(account):
    db, bill, client = account
    inv = invoice(client, bill, '100', due_date='2026-10-26')
    response = client.post(f'/bills/{bill.id}/confirm-invoice', data={'actual_due_date': '2027-01-26'})
    assert response.status_code == 400 and inv.due_date == day('2026-10-26')
    assert bill.next_due_date == day('2026-10-26') and bill.outstanding_cents == 10000


def test_confirm_correction_accepts_credit_and_updates_linked_cash_due_date(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '50', 'paid_date': '2026-09-01'})
    inv = invoice(client, bill, '150', date='2026-09-10', due_date='2026-09-26')
    post(client, f'/bills/{bill.id}/pay', {'invoice_id': inv.id, 'amount_paid': '100', 'paid_date': '2026-09-10'})
    fields = {'invoice_target': inv.id, 'actual_amount': '100', 'amount_type': 'net',
              'invoice_date': '2026-09-10', 'actual_due_date': '2026-09-27'}
    response = client.post(f'/bills/{bill.id}/confirm-invoice', data=fields)
    assert response.status_code == 400 and inv.due_date == day('2026-09-26')
    post(client, f'/bills/{bill.id}/confirm-invoice', {**fields, 'credit_applied': '50'})
    assert inv.amount_cents == 15000 and len(bill.invoices) == 1 and bill.outstanding_cents == 0
    linked = next(p for p in bill.payments if p.invoice_id == inv.id)
    assert linked.due_date == day('2026-09-27')


@pytest.mark.parametrize('endpoint', ['pay', 'extra-payment'])
def test_deleted_payment_cannot_return_from_old_form_but_new_receipt_can(account, endpoint):
    db, bill, client = account
    inv = invoice(client, bill, '100')
    fields = {'amount_paid': '100', 'paid_date': '2026-10-03', 'request_token': 'removed-receipt'}
    if endpoint == 'pay':
        fields['invoice_id'] = inv.id
    path = f'/bills/{bill.id}/{endpoint}'
    post(client, path, fields)
    receipt = bill.payments[0]
    post(client, f'/bills/payments/{receipt.id}/delete', {})
    journal_count = db.query(JournalEntry).count()
    for _ in range(2):
        response = client.post(path, data=fields)
        assert response.status_code == 400 and 'payment was deleted' in response.text
    assert not bill.payments and bill.outstanding_cents == 10000
    assert db.query(JournalEntry).count() == journal_count
    assert db.query(BillPaymentSubmission).filter_by(request_token='removed-receipt').one().is_deleted
    post(client, path, {**fields, 'request_token': 'genuine-new-receipt'})
    assert len(bill.payments) == 1 and bill.outstanding_cents == 0
    assert db.execute(text('PRAGMA foreign_key_check')).all() == []


def test_legacy_token_retained_on_deletion_and_receipt_id_reuse_is_safe(account):
    db, bill, client = account
    invoice(client, bill, '100')
    fields = {'amount_paid': '100', 'request_token': 'legacy-receipt'}
    path = f'/bills/{bill.id}/pay'
    post(client, path, fields)
    receipt_id = bill.payments[0].id
    db.query(BillPaymentSubmission).delete(); db.commit()
    post(client, path, fields)  # Older receipts still provide replay protection.
    assert len(bill.payments) == 1
    post(client, f'/bills/payments/{receipt_id}/delete', {})
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '10', 'request_token': 'reused-id'})
    assert bill.payments[0].id == receipt_id
    response = client.post(path, data=fields)
    assert response.status_code == 400 and len(bill.payments) == 1 and bill.outstanding_cents == 9000


def test_used_form_survives_account_deletion(account):
    db, bill, client = account
    path = f'/bills/{bill.id}/extra-payment'
    fields = {'amount_paid': '100', 'request_token': 'removed-account'}
    post(client, path, fields)
    original_id = bill.id
    post(client, f'/bills/{bill.id}/delete', {})
    new_bill = Bill(id=original_id, name='Replacement', ledger_enabled=True)
    db.add(new_bill); db.commit()
    response = client.post(path, data=fields)
    assert response.status_code == 400 and not new_bill.payments
    assert db.query(BillPaymentSubmission).one().is_deleted


def test_deleted_submission_export_restore_and_old_metadata_cannot_reenable_it(account):
    db, bill, client = account
    invoice(client, bill, '100')
    fields = {'amount_paid': '100', 'paid_date': '2026-10-03', 'request_token': 'export-deleted'}
    path = f'/bills/{bill.id}/pay'
    post(client, path, fields)
    old_metadata = copy.deepcopy(client.get('/api/export').json()['data']['bill_payment_submissions'])
    post(client, f'/bills/payments/{bill.payments[0].id}/delete', {})
    exported = client.get('/api/export').json()
    assert exported['data']['bill_payment_submissions'][0]['is_deleted'] is True
    assert exported['data']['bill_payments'] == []
    db.query(BillPaymentSubmission).delete(); db.commit()
    for _ in range(2):
        stats = import_data_from_json(db, copy.deepcopy(exported))
        assert stats['errors'] == 0
    stats = import_data_from_json(db, {'data': {'bill_payment_submissions': old_metadata}})
    assert stats['errors'] == 0 and db.query(BillPaymentSubmission).count() == 1
    response = client.post(path, data=fields)
    assert response.status_code == 400 and not bill.payments and bill.outstanding_cents == 10000


def test_submission_import_handles_id_collision_and_rejects_token_mismatch(account):
    db, bill, client = account
    fields = {'amount_paid': '100', 'request_token': 'first-submission'}
    post(client, f'/bills/{bill.id}/extra-payment', fields)
    metadata = client.get('/api/export').json()['data']['bill_payment_submissions'][0]
    other = {**metadata, 'request_token': 'other-submission', 'is_deleted': True}
    stats = import_data_from_json(db, {'data': {'bill_payment_submissions': [other]}})
    assert stats['errors'] == 0 and db.query(BillPaymentSubmission).count() == 2
    with pytest.raises(ValueError, match='bill_payment_submissions.*No changes were saved'):
        import_data_from_json(db, {'data': {'bill_payment_submissions': [{**metadata, 'bill_id': bill.id + 1}]}})
    post(client, f'/bills/{bill.id}/extra-payment', fields)
    assert len(bill.payments) == 1
