"""Protect billing corrections, removed adjustments and historical forecasts."""
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from main import app, get_db
from models import Base, Bill, BillRecordSubmission, JournalEntry
from import_logic import import_data_from_json
from test_billing_accounts import account, invoice, adjustment, post, day


def correction_fields(inv, endpoint, amount='120', token='first-correction'):
    if endpoint == 'confirm-invoice':
        return dict(invoice_target=str(inv.id), actual_amount=amount, actual_due_date='2026-09-26',
                    invoice_date='2026-09-10', request_token=token)
    return dict(invoice_id=str(inv.id), amount=amount, due_date='2026-09-26',
                invoice_date='2026-09-10', request_token=token)


@pytest.mark.parametrize('endpoint', ['confirm-invoice', 'invoices'])
def test_old_invoice_correction_retry_preserves_newer_charge_and_journal(account, endpoint):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-10', due_date='2026-09-26')
    first = correction_fields(inv, endpoint)
    path = f'/bills/{bill.id}/{endpoint}'
    post(client, path, first)
    post(client, path, correction_fields(inv, endpoint, '130', 'second-correction'))
    bill.expected_amount = 999
    db.commit()
    count = db.query(JournalEntry).count()
    for _ in range(2):
        post(client, path, first)
    assert inv.amount_cents == bill.outstanding_cents == 13000 and len(bill.invoices) == 1
    assert bill.expected_amount == 999 and db.query(JournalEntry).count() == count
    assert db.query(BillRecordSubmission).filter_by(record_type='invoice').count() == 2
    response = client.post(path, data=correction_fields(inv, endpoint, '125'))
    assert response.status_code == 400 and inv.amount_cents == 13000


@pytest.mark.parametrize('endpoint', ['confirm-invoice', 'invoices'])
def test_correction_token_cannot_be_reused_to_create_a_new_invoice(account, endpoint):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-10', due_date='2026-09-26')
    fields = correction_fields(inv, endpoint)
    path = f'/bills/{bill.id}/{endpoint}'
    post(client, path, fields)
    if endpoint == 'confirm-invoice':
        fields['invoice_target'] = 'new'
    else:
        fields.pop('invoice_id')
    response = client.post(path, data=fields)
    assert response.status_code == 400 and len(bill.invoices) == 1


def test_old_net_correction_replay_skips_credit_recalculation(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '50', 'paid_date': '2026-09-01'})
    inv = invoice(client, bill, '150', date='2026-09-10', due_date='2026-09-26')
    first = {**correction_fields(inv, 'confirm-invoice', '100'), 'amount_type': 'net', 'credit_applied': '50'}
    post(client, f'/bills/{bill.id}/confirm-invoice', first)
    post(client, f'/bills/{bill.id}/confirm-invoice', correction_fields(inv, 'confirm-invoice', '180', 'later'))
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '130', 'paid_date': '2026-09-10'})
    post(client, f'/bills/{bill.id}/confirm-invoice', first)
    assert inv.amount_cents == 18000 and bill.outstanding_cents == 0


@pytest.mark.parametrize('kind', ['supplier_credit', 'supplier_debit', 'refund'])
def test_deleted_adjustment_forms_cannot_recreate_financial_events(account, kind):
    db, bill, client = account
    adjustment(client, bill, 'opening_credit', '100', date='2026-10-01')
    fields = dict(kind=kind, amount='20', effective_date='2026-10-03', request_token='deleted-adjustment')
    path = f'/bills/{bill.id}/adjustments'
    post(client, path, fields)
    removed = next(a for a in bill.adjustments if a.kind == kind)
    post(client, f'{path}/{removed.id}/delete', {})
    count = db.query(JournalEntry).count()
    for _ in range(2):
        response = client.post(path, data=fields)
        assert response.status_code == 400 and 'record was deleted' in response.text
    assert bill.credit_cents == 10000 and len(bill.adjustments) == 1
    assert client.get('/expenses').context['all_time_bills'] == 0
    assert db.query(JournalEntry).count() == count
    post(client, path, {**fields, 'request_token': 'new-real-adjustment'})
    assert len(bill.adjustments) == 2
    assert bill.credit_cents == (12000 if kind == 'supplier_credit' else 8000)
    assert db.execute(text('PRAGMA foreign_key_check')).all() == []


def test_legacy_adjustment_deleted_form_survives_record_id_reuse(account):
    db, bill, client = account
    fields = dict(kind='supplier_credit', amount='20', effective_date='2026-10-03', request_token='legacy-credit')
    path = f'/bills/{bill.id}/adjustments'
    post(client, path, fields)
    record_id = bill.adjustments[0].id
    db.query(BillRecordSubmission).delete(); db.commit()
    post(client, path, fields)
    assert len(bill.adjustments) == 1
    post(client, f'{path}/{record_id}/delete', {})
    post(client, path, {**fields, 'amount': '30', 'request_token': 'new-credit'})
    assert bill.adjustments[0].id == record_id
    response = client.post(path, data=fields)
    assert response.status_code == 400 and bill.credit_cents == 3000


@pytest.mark.parametrize('record_type', ['adjustment', 'statement'])
def test_account_record_corrections_remember_each_submission(account, record_type):
    db, bill, client = account
    if record_type == 'adjustment':
        fields = dict(kind='supplier_credit', amount='20', effective_date='2026-10-03')
        path = f'/bills/{bill.id}/adjustments'
        post(client, path, fields)
        record = bill.adjustments[0]
        fields['adjustment_id'] = record.id
        value = lambda: record.amount_cents
    else:
        fields = dict(amount='20', balance_type='credit', as_of_date='2026-10-03')
        path = f'/bills/{bill.id}/statements'
        post(client, path, fields)
        record = bill.statements[0]
        fields['statement_id'] = record.id
        value = lambda: abs(record.balance_cents)
    first = {**fields, 'amount': '30', 'request_token': 'first-edit'}
    post(client, path, first)
    post(client, path, {**fields, 'amount': '40', 'request_token': 'second-edit'})
    count = db.query(JournalEntry).count()
    post(client, path, first)
    assert value() == 4000 and db.query(JournalEntry).count() == count


def test_statement_deletion_retains_original_and_correction_tokens(account):
    db, bill, client = account
    fields = dict(amount='20', balance_type='credit', as_of_date='2026-10-03', request_token='original-statement')
    path = f'/bills/{bill.id}/statements'
    post(client, path, fields)
    record = bill.statements[0]
    post(client, path, {**fields, 'statement_id': record.id, 'amount': '30', 'request_token': 'statement-edit'})
    post(client, f'{path}/{record.id}/delete', {})
    response = client.post(path, data=fields)
    assert response.status_code == 400 and not bill.statements
    assert all(s.is_deleted for s in db.query(BillRecordSubmission).all())


def test_bill_deletion_keeps_all_record_submission_tokens(account):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-10', due_date='2026-09-26', request_token='invoice-original')
    first = correction_fields(inv, 'confirm-invoice')
    post(client, f'/bills/{bill.id}/confirm-invoice', first)
    adjustment(client, bill, 'supplier_credit', '20', request_token='credit-original')
    bid = bill.id
    post(client, f'/bills/{bid}/delete', {})
    assert db.query(BillRecordSubmission).count() == 3
    assert all(s.is_deleted for s in db.query(BillRecordSubmission).all())
    replacement = Bill(id=bid, name='Replacement')
    db.add(replacement); db.commit()
    response = client.post(f'/bills/{bid}/invoices', data={'amount': '100', 'invoice_date': '2026-09-10',
                           'due_date': '2026-09-26', 'request_token': 'invoice-original'})
    assert response.status_code == 400 and not replacement.invoices


def test_correction_submission_roundtrip_preserves_newer_values(account):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-10', due_date='2026-09-26')
    first = correction_fields(inv, 'invoices')
    post(client, f'/bills/{bill.id}/invoices', first)
    post(client, f'/bills/{bill.id}/invoices', correction_fields(inv, 'invoices', '130', 'later-edit'))
    payload = client.get('/api/export').json()
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as restored:
            app.dependency_overrides[get_db] = lambda: restored
            for _ in range(2):
                assert import_data_from_json(restored, copy.deepcopy(payload))['errors'] == 0
                count = restored.query(JournalEntry).count()
                post(client, f'/bills/{bill.id}/invoices', first)
                assert restored.get(Bill, bill.id).invoices[0].amount_cents == 13000
                assert restored.query(JournalEntry).count() == count
                assert restored.query(BillRecordSubmission).count() == 2
    finally:
        app.dependency_overrides[get_db] = lambda: db
        engine.dispose()


def test_deleted_adjustment_export_restore_cannot_clear_deletion_flag(account):
    db, bill, client = account
    fields = dict(kind='supplier_credit', amount='20', effective_date='2026-10-03', request_token='export-credit')
    path = f'/bills/{bill.id}/adjustments'
    post(client, path, fields)
    active = copy.deepcopy(client.get('/api/export').json()['data']['bill_record_submissions'])
    post(client, f'{path}/{bill.adjustments[0].id}/delete', {})
    payload = client.get('/api/export').json()
    db.query(BillRecordSubmission).delete(); db.commit()
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(payload))['errors'] == 0
    assert import_data_from_json(db, {'data': {'bill_record_submissions': active}})['errors'] == 0
    response = client.post(path, data=fields)
    assert response.status_code == 400 and not bill.adjustments and bill.credit_cents == 0
    assert db.query(BillRecordSubmission).one().is_deleted


def test_submission_import_handles_id_collision_and_rejects_changed_binding(account):
    db, bill, client = account
    adjustment(client, bill, 'supplier_credit', '20', request_token='first-credit')
    metadata = client.get('/api/export').json()['data']['bill_record_submissions'][0]
    assert import_data_from_json(db, {'data': {'bill_record_submissions': [dict(metadata, request_token='other-credit')]}})['errors'] == 0
    assert db.query(BillRecordSubmission).count() == 2
    changed = dict(metadata, record_id=metadata['record_id'] + 1)
    with pytest.raises(ValueError, match='bill_record_submissions.*No changes were saved'):
        import_data_from_json(db, {'data': {'bill_record_submissions': [changed]}})
    assert db.query(BillRecordSubmission).filter_by(request_token='first-credit').one().record_id == metadata['record_id']


@pytest.mark.parametrize('older_due', ['2026-02-26', ''])
@pytest.mark.parametrize('confirmed_future', [False, True])
def test_backfilled_settled_invoice_preserves_latest_forecast_or_confirmation(account, older_due, confirmed_future):
    db, bill, client = account
    recent = invoice(client, bill, '100', date='2026-04-01', due_date='2026-04-26')
    post(client, f'/bills/{bill.id}/pay', {'invoice_id': recent.id, 'amount_paid': '200', 'paid_date': '2026-04-10'})
    assert bill.next_due_date == day('2026-07-26')
    if confirmed_future:
        post(client, f'/bills/{bill.id}/confirm-invoice', {'actual_due_date': '2026-10-15'})
    expected_due = bill.next_due_date
    invoice(client, bill, '50', date='2026-02-01', due_date=older_due)
    assert bill.next_due_date == expected_due and bill.is_due_date_confirmed == confirmed_future
    assert bill.credit_cents == 5000 and bill.outstanding_cents == 0
    assert all(i.schedule_advanced for i in bill.invoices)


def test_newest_invoice_covered_by_credit_still_advances_forecast(account):
    db, bill, client = account
    adjustment(client, bill, 'opening_credit', '200', date='2026-01-01')
    invoice(client, bill, '50', date='2026-02-01', due_date='2026-02-26')
    invoice(client, bill, '100', date='2026-04-01', due_date='2026-04-26')
    assert bill.next_due_date == day('2026-07-26') and bill.credit_cents == 5000


def test_reopened_old_debt_settlement_restores_forecast_after_later_zero_charge(account):
    db, bill, client = account
    older = invoice(client, bill, '100', date='2026-01-01', due_date='2026-01-26')
    post(client, f'/bills/{bill.id}/pay', {'invoice_id': older.id, 'amount_paid': '100', 'paid_date': '2026-01-10'})
    invoice(client, bill, '0', date='2026-04-01', due_date='2026-04-26')
    assert bill.next_due_date == day('2026-07-26')
    post(client, f'/bills/{bill.id}/invoices', {'invoice_id': older.id, 'amount': '150',
         'invoice_date': '2026-01-01', 'due_date': '2026-01-26'})
    assert bill.next_due_date == day('2026-01-26') and bill.outstanding_cents == 5000
    post(client, f'/bills/{bill.id}/pay', {'invoice_id': older.id, 'amount_paid': '50', 'paid_date': '2026-05-10'})
    assert bill.next_due_date == day('2026-07-26') and bill.outstanding_cents == 0


def test_concurrent_correction_retries_apply_once(monkeypatch, tmp_path):
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH', '0')
    engine = create_engine(f'sqlite:///{tmp_path / "corrections.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        bill = Bill(id=1, name='Concurrent correction')
        db.add(bill); db.commit()
        from billing_service import record_invoice
        inv = record_invoice(db, bill, '100', day('2026-09-26'), invoice_date=day('2026-09-10'))
        db.commit()
        fields = correction_fields(inv, 'confirm-invoice')
    def connection():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = connection
    def submit(_):
        client = TestClient(app)
        try:
            return client.post('/bills/1/confirm-invoice', data=fields, follow_redirects=False).status_code
        finally:
            client.close()
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            assert list(workers.map(submit, range(2))) == [303, 303]
        with Session(engine) as db:
            assert db.get(Bill, 1).outstanding_cents == 12000
            assert db.query(BillRecordSubmission).count() == 1
            assert db.query(JournalEntry).filter_by(action='Confirmed Bill Due Date').count() == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
