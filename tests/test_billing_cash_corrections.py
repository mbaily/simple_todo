"""Preserve receipt dates, imported reminders and each cash correction."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from main import app, get_db
from models import Base, Bill, BillPayment, BillPaymentSubmission, JournalEntry
from billing_service import record_invoice, record_cash_payment
from import_logic import import_data_from_json
from test_billing_accounts import account, adjustment, invoice, post, day


@pytest.fixture(autouse=True)
def cash_day(monkeypatch):
    class ReviewDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 8)
    monkeypatch.setattr('billing_service.date_type', ReviewDate)
    monkeypatch.setattr('main.date_type', ReviewDate)


def settled(account, token=None):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-10', due_date='2026-09-26')
    fields = dict(invoice_id=inv.id, amount_paid='100', paid_date='2026-09-20')
    if token:
        fields['request_token'] = token
    post(client, f'/bills/{bill.id}/pay', fields)
    return inv, bill.payments[0]


def edit_fields(token='first-edit', amount='90', **extra):
    return dict(paid_date='2026-09-20', due_date='2026-09-26', amount_paid=amount,
                note='First correction', request_token=token, **extra)


@pytest.mark.parametrize('amounts', [{'amount_paid':90}, {'amount_cents':9000}])
@pytest.mark.parametrize('omitted', ['both', 'paid', 'due', 'blank'])
def test_partial_cash_import_preserves_dates_and_restores_debt_reminder(account, amounts, omitted):
    db, bill, client = account
    inv, receipt = settled(account)
    before = (receipt.paid_date, receipt.due_date, receipt.created_at)
    fields = dict(id=receipt.id, **amounts)
    if omitted == 'paid':
        fields['due_date'] = '2026-09-26'
    elif omitted == 'due':
        fields['paid_date'] = '2026-09-20'
    elif omitted == 'blank':
        fields.update(paid_date='', due_date=None)
    for _ in range(2):
        assert import_data_from_json(db, {'data':{'bill_payments':[copy.deepcopy(fields)]}})['errors'] == 0
        assert (receipt.paid_date, receipt.due_date, receipt.created_at) == before
        assert inv.outstanding_cents == bill.outstanding_cents == 1000
        assert bill.next_due_date == day('2026-09-26') and bill.is_due_date_confirmed
    assert import_data_from_json(db, {'data':{'bill_payments':[dict(id=receipt.id, amount_paid=100)]}})['errors'] == 0
    assert bill.outstanding_cents == 0 and bill.next_due_date == day('2026-12-26')
    assert not bill.is_due_date_confirmed
    assert import_data_from_json(db, {'data':{'bill_payments':[dict(id=receipt.id, amount_paid=100)]}})['errors'] == 0
    assert bill.next_due_date == day('2026-12-26')


@pytest.mark.parametrize('collection', ['bill_invoices', 'bill_adjustments'])
def test_imported_charge_or_credit_correction_updates_invoice_reminder(account, collection):
    db, bill, client = account
    if collection == 'bill_invoices':
        inv, receipt = settled(account)
        change = dict(id=inv.id, amount_cents=11000)
    else:
        adjustment(client, bill, 'opening_credit', '100', date='2026-09-01')
        inv = invoice(client, bill, '100', date='2026-09-10', due_date='2026-09-26')
        change = dict(id=bill.adjustments[0].id, amount_cents=9000)
    assert bill.next_due_date == day('2026-12-26')
    assert import_data_from_json(db, {'data':{collection:[change]}})['errors'] == 0
    assert bill.outstanding_cents == 1000
    assert bill.next_due_date == day('2026-09-26') and bill.is_due_date_confirmed


def test_export_import_preserves_confirmed_future_date_until_debt_reopens(account):
    db, bill, client = account
    inv, receipt = settled(account)
    post(client, f'/bills/{bill.id}/confirm-invoice', dict(actual_due_date='2027-01-15'))
    exported = client.get('/api/export').json()
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(exported))['errors'] == 0
        assert bill.next_due_date == day('2027-01-15') and bill.is_due_date_confirmed
    exported['data']['bill_payments'][0].update(amount_paid=90, amount_cents=9000)
    assert import_data_from_json(db, exported)['errors'] == 0
    assert bill.next_due_date == inv.due_date == day('2026-09-26') and bill.is_due_date_confirmed


def test_unrelated_import_keeps_invoice_schedule_and_explicit_payment_date_change(account):
    db, bill, client = account
    inv, receipt = settled(account)
    post(client, f'/bills/{bill.id}/confirm-invoice', dict(actual_due_date='2027-01-15'))
    assert import_data_from_json(db, {'data':{'expenses':[dict(id=1,amount=5,date='2026-09-01')]}})['errors'] == 0
    assert bill.next_due_date == day('2027-01-15') and bill.is_due_date_confirmed
    assert import_data_from_json(db, {'data':{'bill_payments':[dict(id=receipt.id,paid_date='2026-09-21')]}})['errors'] == 0
    assert receipt.paid_date == day('2026-09-21') and receipt.due_date == day('2026-09-26')
    assert receipt.amount_paid == 100 and receipt.amount_cents == 10000
    assert bill.next_due_date == day('2027-01-15')


def test_payment_edit_retry_preserves_newer_cash_dates_notes_and_reminder(account):
    db, bill, client = account
    inv, receipt = settled(account, token='original-receipt')
    original = (receipt.request_token, receipt.request_fingerprint)
    path = f'/bills/payments/{receipt.id}/edit'
    first = edit_fields()
    post(client, path, first)
    second = {**first, 'request_token':'later-edit', 'amount_paid':'95', 'paid_date':'2026-09-21',
              'due_date':'2026-09-27', 'note':'Latest correction'}
    post(client, path, second)
    before = client.get('/api/export').json()['data']
    for _ in range(2):
        post(client, path, first)
    assert client.get('/api/export').json()['data'] == before
    assert receipt.amount_cents == 9500 and inv.outstanding_cents == 500
    assert receipt.paid_date == day('2026-09-21')
    assert bill.next_due_date == inv.due_date == day('2026-09-27')
    assert receipt.display_note == 'Latest correction'
    assert (receipt.request_token, receipt.request_fingerprint) == original
    assert db.query(JournalEntry).filter_by(action='Edited Bill Payment').count() == 2
    assert db.query(BillPaymentSubmission).count() == 3


@pytest.mark.parametrize('changed', [dict(amount_paid='95'), dict(paid_date='2026-09-21'),
                                    dict(due_date='2026-09-27'), dict(note='Changed'), dict(clear_note='true')])
def test_payment_edit_token_cannot_be_reused_with_different_details(account, changed):
    db, bill, client = account
    inv, receipt = settled(account)
    path = f'/bills/payments/{receipt.id}/edit'
    first = edit_fields()
    post(client, path, first)
    before = client.get('/api/export').json()['data']
    assert client.post(path,data={**first,**changed}).status_code == 400
    assert client.get('/api/export').json()['data'] == before


def test_payment_edit_tokens_cannot_cross_receipts_accounts_or_operations(account):
    db, bill, client = account
    inv, receipt = settled(account, token='created-once')
    fields = edit_fields()
    path = f'/bills/payments/{receipt.id}/edit'
    assert client.post(path,data={**fields,'request_token':'created-once'}).status_code == 400
    post(client,path,fields)
    post(client,f'/bills/{bill.id}/extra-payment',dict(amount_paid='10',paid_date='2026-09-20'))
    other_receipt = next(p for p in bill.payments if p.id != receipt.id)
    assert client.post(f'/bills/payments/{other_receipt.id}/edit',data=fields).status_code == 400
    other = Bill(name='Other account')
    db.add(other); db.commit()
    post(client,f'/bills/{other.id}/extra-payment',dict(amount_paid='10',paid_date='2026-09-20'))
    assert client.post(f'/bills/payments/{other.payments[0].id}/edit',data=fields).status_code == 400
    assert client.post(f'/bills/{bill.id}/extra-payment',data=dict(amount_paid='90',paid_date='2026-09-20',
                       request_token='first-edit')).status_code == 400
    assert receipt.amount_cents == 9000 and other_receipt.amount_cents == other.payments[0].amount_cents == 1000


@pytest.mark.parametrize('delete_account', [False, True])
def test_deleted_payment_edit_tokens_survive_receipt_and_account_id_reuse(account, delete_account):
    db, bill, client = account
    inv, receipt = settled(account)
    bid, pid = bill.id, receipt.id
    fields = edit_fields()
    path = f'/bills/payments/{pid}/edit'
    post(client,path,fields)
    active = client.get('/api/export').json()['data']['bill_payment_submissions']
    if delete_account:
        post(client,f'/bills/{bid}/delete',{})
        replacement = Bill(id=bid,name='Replacement',ledger_enabled=True)
        db.add(replacement); db.commit()
    else:
        post(client,f'/bills/payments/{pid}/delete',{})
        assert client.post(path,data=fields).status_code == 400
        replacement = bill
    assert db.query(BillPaymentSubmission).filter_by(request_token='first-edit').one().is_deleted
    new_receipt = BillPayment(id=pid,bill=replacement,amount_paid=50,amount_cents=5000,
                             paid_date=day('2026-09-20'),due_date=day('2026-09-26'))
    db.add(new_receipt); db.commit()
    assert import_data_from_json(db,{'data':{'bill_payment_submissions':active}})['errors'] == 0
    assert client.post(path,data=fields).status_code == 400 and new_receipt.amount_cents == 5000
    assert db.execute(text('PRAGMA foreign_key_check')).all() == []


def test_payment_edit_export_restore_and_submission_binding(account):
    db, bill, client = account
    inv, receipt = settled(account)
    path = f'/bills/payments/{receipt.id}/edit'
    first = edit_fields()
    post(client,path,first)
    post(client,path,edit_fields('later-edit','95'))
    exported = client.get('/api/export').json()
    engine = create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as restored:
            for _ in range(2):
                assert import_data_from_json(restored,copy.deepcopy(exported))['errors'] == 0
            app.dependency_overrides[get_db] = lambda: restored
            post(client,path,first)
            assert restored.get(BillPayment,receipt.id).amount_cents == 9500
            metadata = copy.deepcopy(exported['data']['bill_payment_submissions'][0])
            metadata['payment_id'] += 1
            with pytest.raises(ValueError, match='bill_payment_submissions.*No changes were saved'):
                import_data_from_json(restored,{'data':{'bill_payment_submissions':[metadata]}})
            assert restored.query(BillPaymentSubmission).filter_by(request_token='first-edit').one().payment_id == receipt.id
    finally:
        app.dependency_overrides[get_db] = lambda: db
        engine.dispose()


def test_legacy_unknown_receipt_can_be_corrected_once_and_then_deleted(account):
    db, bill, client = account
    receipt = BillPayment(bill=bill,paid_date=day('2026-09-20'),due_date=day('2026-09-26'),amount_paid=None)
    db.add(receipt); db.commit()
    fields = edit_fields(amount='')
    path = f'/bills/payments/{receipt.id}/edit'
    post(client,path,fields)
    post(client,path,{**fields,'request_token':'later','amount_paid':'20'})
    post(client,path,fields)
    assert receipt.amount_paid == 20 and receipt.amount_cents is None and not bill.ledger_enabled
    post(client,f'/bills/payments/{receipt.id}/delete',{})
    assert all(s.is_deleted for s in db.query(BillPaymentSubmission))


def test_legacy_unknown_receipt_correction_updates_ledger_and_old_retry_stays_safe(account):
    db,bill,client = account
    receipt = BillPayment(bill=bill,paid_date=day('2026-09-20'),due_date=day('2026-09-26'),amount_paid=None)
    db.add(receipt); db.commit()
    first = edit_fields(amount='')
    path = f'/bills/payments/{receipt.id}/edit'
    post(client,path,first)
    inv = invoice(client,bill,'100',date='2026-10-02',due_date='2026-10-26')
    post(client,path,edit_fields('known-amount','20'))
    assert receipt.amount_paid == 20 and receipt.amount_cents == 2000
    assert inv.outstanding_cents == bill.outstanding_cents == 8000
    post(client,path,first)
    assert receipt.amount_cents == 2000 and bill.outstanding_cents == 8000


@pytest.mark.parametrize('changed',[dict(paid_date='bad'),dict(due_date='bad'),dict(amount_paid='1.001'),
                                  dict(amount_paid='-1'),dict(amount_paid='NaN'),dict(amount_paid='')])
def test_invalid_payment_edit_is_atomic(account,changed):
    db,bill,client = account
    inv,receipt = settled(account)
    before = client.get('/api/export').json()['data']
    assert client.post(f'/bills/payments/{receipt.id}/edit',data={**edit_fields(),**changed}).status_code == 400
    assert client.get('/api/export').json()['data'] == before


@pytest.mark.parametrize('operation',['pay','extra-payment','edit'])
def test_future_cash_is_rejected_without_settling_invoice(account,operation):
    db,bill,client = account
    inv = invoice(client,bill,'100',date='2026-09-10',due_date='2026-09-26')
    if operation == 'edit':
        post(client,f'/bills/{bill.id}/pay',dict(invoice_id=inv.id,amount_paid='10',paid_date='2026-09-20'))
        path = f'/bills/payments/{bill.payments[0].id}/edit'
    else:
        path = f'/bills/{bill.id}/{operation}'
    fields = dict(invoice_id=inv.id,amount_paid='100',paid_date='2026-10-09',request_token='future-cash')
    before = client.get('/api/export').json()['data']
    response = client.post(path,data=fields)
    assert response.status_code == 400 and 'cannot be dated in the future' in response.text
    assert client.get('/api/export').json()['data'] == before


def test_paid_today_before_future_due_date_is_valid(account):
    db,bill,client = account
    inv = invoice(client,bill,'100',date='2026-10-08',due_date='2026-10-26')
    post(client,f'/bills/{bill.id}/pay',dict(invoice_id=inv.id,amount_paid='100',paid_date='2026-10-08T23:59:59'))
    assert bill.outstanding_cents == 0 and bill.next_due_date == day('2027-01-26')
    assert client.get('/expenses').context['all_time_bills'] == 100


def test_new_imported_cash_allows_future_due_date_and_keeps_cash_date(account):
    db,bill,client = account
    payload = {'data':{'bill_payments':[dict(id=1,bill_id=bill.id,amount_paid=10,
                                          paid_date='2026-10-08',due_date='2026-10-26')]}}
    assert import_data_from_json(db,payload)['errors'] == 0
    receipt = db.get(BillPayment,1)
    assert receipt.paid_date == day('2026-10-08') and receipt.due_date == day('2026-10-26')


def test_partial_refund_import_checks_original_kind_before_writing(account):
    db,bill,client = account
    adjustment(client,bill,'supplier_credit','100',date='2026-10-01')
    adjustment(client,bill,'refund','10',date='2026-10-08')
    refund = next(a for a in bill.adjustments if a.kind == 'refund')
    before = client.get('/api/export').json()['data']
    with pytest.raises(ValueError,match='future'):
        import_data_from_json(db,{'data':{'bill_adjustments':[dict(id=refund.id,effective_date='2026-10-09')]}})
    db.rollback()
    assert client.get('/api/export').json()['data'] == before


@pytest.mark.parametrize('editing',[False,True])
def test_future_refund_is_rejected_atomically(account,editing):
    db,bill,client = account
    adjustment(client,bill,'supplier_credit','100',date='2026-10-01')
    fields = dict(kind='refund',amount='20',effective_date='2026-10-09',request_token='future-refund')
    if editing:
        adjustment(client,bill,'refund','10',date='2026-10-08')
        fields['adjustment_id'] = next(a.id for a in bill.adjustments if a.kind == 'refund')
    before = client.get('/api/export').json()['data']
    assert client.post(f'/bills/{bill.id}/adjustments',data=fields).status_code == 400
    assert client.get('/api/export').json()['data'] == before


@pytest.mark.parametrize('collection,changed',[
    ('bill_payments',dict(amount_paid=100,paid_date='2026-10-09')),
    ('bill_payments',dict(paid_date='2026-10-09')),
    ('bill_payments',dict(paid_date='invalid')),
    ('bill_payments',dict(due_date='invalid')),
    ('bill_adjustments',dict(kind='refund',amount_cents=1000,effective_date='2026-10-09')),
])
def test_future_or_invalid_imported_cash_is_rejected_before_any_write(account,collection,changed):
    db,bill,client = account
    inv,receipt = settled(account)
    before = client.get('/api/export').json()['data']
    payload = {'data':{'folders':[dict(id=99,name='Do not write')],
                       'bills':[dict(id=bill.id,name='Do not rename')],
                       collection:[dict(id=receipt.id,bill_id=bill.id,**changed)]}}
    with pytest.raises(ValueError):
        import_data_from_json(db,copy.deepcopy(payload))
    db.rollback()
    assert client.get('/api/export').json()['data'] == before
    response = client.post('/api/import',files={'file':('bad.json',json.dumps(payload),'application/json')},follow_redirects=False)
    assert response.status_code == 303 and 'import_error=' in response.headers['location']
    assert client.get('/api/export').json()['data'] == before


def test_concurrent_cash_corrections_save_once(monkeypatch,tmp_path):
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH','0')
    engine = create_engine(f'sqlite:///{tmp_path / "cash-edits.db"}',connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        bill = Bill(id=1,name='Concurrent cash',frequency='quarterly')
        db.add(bill); db.commit()
        inv = record_invoice(db,bill,'100',day('2026-09-26'),invoice_date=day('2026-09-10'))
        receipt = record_cash_payment(db,bill,10000,day('2026-09-20'),inv)
        db.commit()
        path = f'/bills/payments/{receipt.id}/edit'
    def connection():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = connection
    def submit(_):
        client = TestClient(app)
        try:
            return client.post(path,data=edit_fields(),follow_redirects=False).status_code
        finally:
            client.close()
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            assert list(workers.map(submit,range(2))) == [303,303]
        with Session(engine) as db:
            assert db.get(BillPayment,1).amount_cents == 9000
            assert db.query(BillPaymentSubmission).count() == 1
            assert db.query(JournalEntry).filter_by(action='Edited Bill Payment').count() == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
