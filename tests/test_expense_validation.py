"""Expense writes must keep invalid money out of saved data and exports."""
import copy
import json
from urllib.parse import parse_qs, urlparse

import pytest

from import_logic import import_data_from_json
from models import Expense
from test_billing_accounts import account, day


BAD_AMOUNTS = ['inf', '-inf', 'NaN', '1.001', '-1', '0', 'not-money',
               '92233720368547758.08', '90071992547409.93']


def expense(db):
    item = Expense(amount=15.25, description='Original expense', category='Food',
                   date=day('2026-09-20'), currency='AUD', payment_method='Card', note='Original note')
    db.add(item)
    db.commit()
    return item


@pytest.mark.parametrize('amount', BAD_AMOUNTS + ['', '   '])
@pytest.mark.parametrize('editing', [False, True])
def test_invalid_expense_forms_preserve_every_field_and_journal(account, amount, editing):
    db, bill, client = account
    item = expense(db)
    before = client.get('/api/export').json()['data']
    path = f'/expenses/{item.id}/update' if editing else '/expenses/create'
    response = client.post(path, data=dict(amount=amount, description='Must not save',
                           category='Other', note='Must not replace', currency='USD',
                           payment_method='Cash', date='2026-10-01'), follow_redirects=False)
    assert response.status_code == 400
    assert client.get('/api/export').json()['data'] == before
    assert client.get('/expenses').status_code == client.get('/expenses/list').status_code == 200


@pytest.mark.parametrize('amount,expected', [('0.01', 0.01), ('12.30', 12.3),
                                          (' $1,234.56 ', 1234.56), ('100', 100)])
def test_valid_expense_forms_save_precise_amounts_and_export(account, amount, expected):
    db, bill, client = account
    item = expense(db)
    response = client.post('/expenses/create', data=dict(amount=amount, description='New valid expense'),
                           follow_redirects=False)
    assert response.status_code == 303
    assert db.query(Expense).filter_by(description='New valid expense').one().amount == expected
    response = client.post(f'/expenses/{item.id}/update', data=dict(amount=amount, description='Valid correction'),
                           follow_redirects=False)
    assert response.status_code == 303 and item.amount == expected
    assert client.get('/api/export').status_code == 200


@pytest.mark.parametrize('amount', BAD_AMOUNTS + [None, '', '  ', float('inf'), float('nan'),
                                                -2, 0, 1.001, True, {}, []])
@pytest.mark.parametrize('new_expense', [False, True])
def test_invalid_import_expense_rolls_back_all_records_and_has_a_clear_error(account, amount, new_expense):
    db, bill, client = account
    item = expense(db)
    client.get('/customization')
    before = client.get('/api/export').json()['data']
    identifier = 99 if new_expense else item.id
    payload = {'data': {'folders': [dict(id=99, name='Must not create')],
                        'bills': [dict(id=bill.id, name='Must not rename')],
                        'expenses': [dict(id=identifier, amount=amount, description='Must not change',
                                          date='2026-10-01')]}}
    with pytest.raises(ValueError, match=f'Expense #{identifier} amount.*No changes were saved'):
        import_data_from_json(db, copy.deepcopy(payload))
    assert client.get('/api/export').json()['data'] == before
    response = client.post('/api/import', files={'file': ('invalid-expense.json', json.dumps(payload), 'application/json')},
                           follow_redirects=False)
    query = parse_qs(urlparse(response.headers['location']).query)
    assert 'import_success' not in query and f'Expense #{identifier} amount' in query['import_error'][0]
    assert 'No changes were saved' in query['import_error'][0]
    assert client.get('/api/export').json()['data'] == before


@pytest.mark.parametrize('amount,expected', [(0.01, 0.01), (100, 100), ('12.30', 12.3), ('$1,234.56', 1234.56)])
def test_valid_expense_imports_roundtrip_and_preserve_omitted_amount(account, amount, expected):
    db, bill, client = account
    item = expense(db)
    payload = {'data': {'expenses': [dict(id=item.id, amount=amount, date=item.date.isoformat()),
                                     dict(id=99, amount=amount, date='2026-10-01')]}}
    assert import_data_from_json(db, payload) == dict(created=1, updated=1, errors=0)
    assert item.amount == db.get(Expense, 99).amount == expected
    payload = client.get('/api/export').json()
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(payload))['errors'] == 0
        assert item.amount == db.get(Expense, 99).amount == expected
    assert import_data_from_json(db, {'data': {'expenses': [dict(id=item.id, description='Only description',
                                                               date=item.date.isoformat())]}})['errors'] == 0
    assert item.amount == expected


def test_missing_amount_on_new_expense_rolls_back_preceding_rows(account):
    db, bill, client = account
    before = client.get('/api/export').json()['data']
    with pytest.raises(ValueError, match='expenses #99.*No changes were saved'):
        import_data_from_json(db, {'data': {'folders': [dict(id=99, name='Must not persist')],
                                          'expenses': [dict(id=99, date='2026-10-01')]}})
    assert client.get('/api/export').json()['data'] == before


@pytest.mark.parametrize('date_fields', [{}, {'date': None}, {'date': ''}])
def test_partial_expense_import_preserves_original_date_and_spending_period(account, date_fields):
    db, bill, client = account
    item = expense(db)
    original_date = item.date
    spending = client.get('/expenses').context
    payload = {'data': {'expenses': [dict(id=item.id, description='Only description', **date_fields)]}}
    response = client.post('/api/import', files={'file': ('partial-expense.json', json.dumps(payload), 'application/json')},
                           follow_redirects=False)
    assert response.status_code == 303 and 'import_success=1' in response.headers['location']
    db.refresh(item)
    assert item.date == original_date and item.description == 'Only description' and item.amount == 15.25
    after = client.get('/expenses').context
    for key in ('past_7_days_total', 'this_month_total', 'ytd_total', 'total_spending',
                'weekly_avg', 'monthly_avg', 'yearly_avg'):
        assert after[key] == spending[key]


@pytest.mark.parametrize('date_fields', [{}, {'date': None}, {'date': ''}])
def test_new_imported_expense_defaults_date_only_when_missing(account, date_fields):
    from datetime import datetime
    db, bill, client = account
    before = datetime.now()
    assert import_data_from_json(db, {'data': {'expenses': [dict(id=99, amount=5, **date_fields)]}})['created'] == 1
    assert before <= db.get(Expense, 99).date <= datetime.now()


def test_explicit_expense_date_correction_and_repeated_export_restore(account):
    db, bill, client = account
    item = expense(db)
    assert import_data_from_json(db, {'data': {'expenses': [dict(id=item.id, date='2025-12-20')]}})['updated'] == 1
    assert item.date == day('2025-12-20')
    exported = client.get('/api/export').json()
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(exported))['errors'] == 0
        assert item.date == day('2025-12-20')
