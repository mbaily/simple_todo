"""Hashtag index in the daisyUI frontend."""
from playwright.sync_api import expect

from models import Hashtag, JournalEntry, List, Todo
from test_daisyui_test import HX, prototype, prototype_server

Q = '?view=hashtags'


def add_tags(db, ids):
    tag = db.get(Hashtag, ids['tag'])
    db.get(List, ids['other']).hashtags.append(tag)
    spare = Hashtag(name='errands')
    db.add(spare)
    db.commit()
    return {**ids, 'spare': spare.id}


def test_index_documents_counts_and_navigation(prototype):
    db, client, ids = prototype
    add_tags(db, ids)
    full = client.get('/app/hashtags')
    fragment = client.get('/app/hashtags', headers=HX)
    assert full.status_code == fragment.status_code == 200
    assert '<!DOCTYPE html>' in full.text and '<!DOCTYPE html>' not in fragment.text
    assert [tag.name for tag in full.context['hashtags']] == ['errands', 'personal']
    assert full.context['list_counts'][ids['tag']] == 1 and full.context['todo_counts'][ids['tag']] == 1
    assert 'Unused' in full.text and 'aria-current="page"' in full.text
    assert 'href="/app/hashtag/' in full.text


def test_rename_validation_options_and_delete(prototype):
    db, client, ids = prototype
    ids = add_tags(db, ids)
    url = f'/app/hashtag/{ids["tag"]}/rename{Q}'
    response = client.post(url, headers=HX, data={'name': '#home_life'})
    assert response.status_code == 200 and 'Renamed #personal to #home_life.' in response.text
    db.expire_all()
    assert db.get(Hashtag, ids['tag']).name == 'home_life'
    assert [t.name for t in db.get(Todo, ids['todo']).hashtags] == ['home_life']
    assert db.query(JournalEntry).filter_by(action='Renamed Hashtag', entity_id=ids['tag']).count() == 1
    for name, status in [('two words', 400), ('bad-name', 400), ('', 400), ('x' * 101, 400), ('errands', 409)]:
        bad = client.post(url, headers=HX, data={'name': name})
        assert bad.status_code == status and bad.headers['HX-Retarget'] == '#page-feedback'
    assert 'Hashtag unchanged.' in client.post(url, headers=HX, data={'name': 'home_life'}).text
    assert client.post(f'/app/hashtag/9999/rename{Q}', headers=HX, data={'name': 'x'}).status_code == 404

    option = client.post(f'/app/hashtag/{ids["tag"]}/options/include_list_items{Q}', headers=HX, data={'enabled': 'true'})
    assert option.status_code == 200 and 'Hashtag view updated.' in option.text and 'Rename' in option.text
    db.expire_all()
    assert db.get(Hashtag, ids['tag']).include_list_items

    deleted = client.post(f'/app/hashtag/{ids["spare"]}/delete{Q}', headers=HX)
    assert deleted.status_code == 200 and 'Deleted #errands. Tagged items were kept.' in deleted.text
    db.expire_all()
    assert db.get(Hashtag, ids['spare']) is None
    assert db.query(JournalEntry).filter_by(action='Deleted Hashtag', entity_id=ids['spare']).count() == 1
    redirect = client.post(f'/app/hashtag/{ids["tag"]}/delete{Q}', follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers['location'] == '/app/hashtags'
    db.expire_all()
    assert db.get(Todo, ids['todo']) is not None and not db.get(Todo, ids['todo']).hashtags


def test_browser_hashtag_index(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    add_tags(db, ids)
    for index in range(9):
        db.add(Hashtag(name=f'extra{index}'))
    db.commit()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app')
    page.locator('.toolbar-navigation').get_by_role('link', name='Hashtags').click()
    expect(page).to_have_url(origin + '/app/hashtags')
    page.get_by_role('searchbox', name='Filter hashtags').fill('#pers')
    expect(page.get_by_role('link', name='#errands')).to_be_hidden()
    expect(page.get_by_role('link', name='#personal')).to_be_visible()
    page.locator('summary[aria-label="Actions for #personal"]').click()
    page.get_by_role('checkbox', name='Tasks in tagged lists').check()
    expect(page.locator('#page-feedback')).to_contain_text('Hashtag view updated.')
    # The filter survives the re-render.
    expect(page.get_by_role('link', name='#errands')).to_be_hidden()
    page.locator('summary[aria-label="Actions for #personal"]').click()
    page.get_by_role('textbox', name='Rename').fill('home')
    page.get_by_role('textbox', name='Rename').press('Enter')
    expect(page.locator('#page-feedback')).to_contain_text('Renamed #personal to #home.')
    page.get_by_role('searchbox', name='Filter hashtags').fill('')
    page.locator('summary[aria-label="Actions for #errands"]').click()
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='Delete hashtag').click()
    expect(page.locator('#page-feedback')).to_contain_text('Deleted #errands.')
    page.get_by_role('link', name='#home', exact=True).click()
    expect(page.get_by_role('heading', name='#home')).to_be_visible()
    assert not errors
