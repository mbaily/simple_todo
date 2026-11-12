"""Customize page: global hashtag filters, export link and import in the daisyUI frontend."""
import json

from playwright.sync_api import expect

from models import Hashtag, List, UserSettings
from test_daisyui_test import HX, prototype, prototype_server


def settings(db):
    db.expire_all()
    return db.query(UserSettings).first()


def test_filters_save_round_trip_and_apply_to_overview(prototype):
    db, client, ids = prototype
    work = Hashtag(name='work')
    db.add(work)
    db.commit()
    page = client.get('/app/customization')
    assert page.status_code == 200 and page.context['tag_modes'] == {} and 'aria-current="page"' in page.text
    response = client.post('/app/customization/filters?view=customize', headers=HX,
                           data={f'tag_{ids["tag"]}': 'exclude', f'tag_{work.id}': ''})
    assert response.status_code == 200 and 'Filters saved: hiding 1.' in response.text
    assert settings(db).filter_exc_hashtags == '#personal' and settings(db).filter_inc_hashtags == ''
    # The original overview applies the stored filter: the #personal task is hidden.
    assert ids['todo'] not in [item.id for item in client.get('/classic').context['pinned_todos_recent']]
    assert client.get('/customization').status_code == 200
    page = client.get('/app/customization')
    assert page.context['tag_modes'] == {'personal': 'exclude'}
    bad = client.post('/app/customization/filters?view=customize', headers=HX, data={f'tag_{work.id}': 'maybe'})
    assert bad.status_code == 400 and bad.headers['HX-Retarget'] == '#page-feedback'


def test_unknown_saved_tags_are_kept_until_removed(prototype):
    db, client, ids = prototype
    row = db.query(UserSettings).first() or UserSettings()
    db.add(row)
    row.filter_inc_hashtags = 'personal, someday'
    db.commit()
    page = client.get('/app/customization')
    assert page.context['tag_modes'] == {'personal': 'include', 'someday': 'include'}
    assert page.context['unknown_include'] == ['someday'] and 'Only #someday' in page.text
    client.post('/app/customization/filters?view=customize', headers=HX,
                data={f'tag_{ids["tag"]}': 'include', 'keep_include': 'someday'})
    assert settings(db).filter_inc_hashtags == '#personal #someday'
    client.post('/app/customization/filters?view=customize', headers=HX, data={f'tag_{ids["tag"]}': 'include'})
    assert settings(db).filter_inc_hashtags == '#personal'


def test_import_success_and_failure_redirect_with_messages(prototype):
    db, client, ids = prototype
    exported = client.get('/api/export')
    assert exported.status_code == 200
    data = exported.json()
    for item in data['data']['lists']:
        if item['id'] == ids['list']:
            item['name'] = 'Renamed by import'
    response = client.post('/app/customization/import', files={'file': ('backup.json', json.dumps(data), 'application/json')},
                           follow_redirects=False)
    assert response.status_code == 303 and 'import_success=1' in response.headers['location']
    page = client.get(response.headers['location'])
    assert 'Import finished:' in page.text
    db.expire_all()
    assert db.get(List, ids['list']).name == 'Renamed by import'
    bad = client.post('/app/customization/import', files={'file': ('broken.json', '{not json', 'application/json')},
                      follow_redirects=False)
    assert bad.status_code == 303 and 'import_error' in bad.headers['location']
    page = client.get(bad.headers['location'])
    assert page.status_code == 400 and 'not a valid JSON export. No changes were saved.' in page.text


def test_browser_customize_filters(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app')
    page.locator('summary[aria-label="More navigation"]').click()
    page.get_by_role('link', name='Customize').click()
    group = page.get_by_role('radiogroup', name='Filter for #personal')
    group.get_by_role('radio', name='Hide').check()
    page.get_by_role('button', name='Save filters').click()
    expect(page.locator('#page-feedback')).to_contain_text('Filters saved: hiding 1.')
    expect(page.get_by_role('radiogroup', name='Filter for #personal').get_by_role('radio', name='Hide')).to_be_checked()
    expect(page.get_by_role('link', name='Export all data (JSON)')).to_be_visible()
    assert not errors


def test_confirmation_messages_can_be_turned_off(prototype):
    db, client, ids = prototype
    add = lambda title: client.post(f'/app/list/{ids["list"]}/todos?view_list_id={ids["list"]}', headers=HX, data={'title': title})
    page = client.get('/app/customization')
    assert 'id="show-confirmations"' in page.text and page.context['confirmations_on']
    assert 'alert-success' in add('First').text

    off = client.post('/app/customization/display?view=customize', headers=HX, data={})
    assert off.status_code == 200 and not off.context['confirmations_on'] and 'alert-success' not in off.text
    db.expire_all()
    assert db.query(UserSettings).one().metadata_json['show_confirmations'] is False
    quiet = add('Second')
    # The page still updates; only the confirmation is left out.
    assert quiet.status_code == 200 and 'Second' in quiet.text and 'alert-success' not in quiet.text
    assert 'alert-success' not in client.post('/app/lists', headers=HX, data={'name': 'Quiet list'}).text
    # Errors and an import's result are always shown.
    error = add('   ')
    assert error.status_code >= 400 and 'alert-error' in error.text
    assert 'Import finished: 2 created' in client.get('/app/customization?import_success=1&created=2&updated=0').text

    on = client.post('/app/customization/display?view=customize', headers=HX, data={'show_confirmations': 'true'})
    assert 'Confirmation messages are on.' in on.text and on.context['confirmations_on']
    assert 'alert-success' in add('Third').text
    plain = client.post('/app/customization/display', data={}, follow_redirects=False)
    assert plain.status_code == 303 and plain.headers['location'] == '/app/customization'


def test_browser_confirmation_toggle(prototype_server, page):
    origin, (_, _, ids) = prototype_server
    page.goto(origin + '/app/customization')
    box = page.get_by_role('checkbox', name='Show confirmation messages')
    expect(box).to_be_checked()
    box.uncheck()
    expect(box).not_to_be_checked()
    page.goto(origin + f'/app/list/{ids["list"]}')
    title = page.get_by_role('textbox', name='Task title')
    title.fill('Added quietly')
    title.press('Enter')
    expect(page.locator('.task-row').filter(has_text='Added quietly')).to_have_count(1)
    expect(page.locator('#page-feedback .alert')).to_have_count(0)
    page.goto(origin + '/app/customization')
    expect(box).not_to_be_checked()
    box.check()
    expect(page.locator('#page-feedback')).to_contain_text('Confirmation messages are on.')
