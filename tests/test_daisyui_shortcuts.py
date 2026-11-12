"""Favourites, recent places, the shortcut strip and the jump box."""
from playwright.sync_api import expect

from models import Folder, List, SmartFolder, UserSettings
from daisyui_test_shortcuts import MAX_RECENT, key_from_url
from test_daisyui_test import HX, prototype, prototype_server


def shortcuts(db):
    db.expire_all()
    return (db.query(UserSettings).one().metadata_json or {}).get('shortcuts', {})


def test_key_from_url():
    assert key_from_url('/app/list/7?sort=user') == 'list:7'
    assert key_from_url('http://host/prefix/app/smart-folder/3') == 'smart:3'
    assert key_from_url('/app/hashtag/2') == 'hashtag:2'
    assert key_from_url('/app?sort=user&folder_id=5') == 'folder:5'
    for other in ['/app', '/app?folder_id=0', '/app/list/7/edit', '/app/list/7/shopping', '/app/calendar', '/app/folders', '']:
        assert key_from_url(other) is None


def test_visits_are_recorded_most_recent_first(prototype):
    db, client, ids = prototype
    smart = SmartFolder(name='Soon')
    db.add(smart)
    db.commit()
    client.get(f'/app/list/{ids["list"]}')
    client.get(f'/app?folder_id={ids["folder"]}', headers=HX)
    client.get(f'/app/hashtag/{ids["tag"]}')
    client.get(f'/app/smart-folder/{smart.id}')
    client.get(f'/app/list/{ids["list"]}')
    assert shortcuts(db)['recent'] == [f'list:{ids["list"]}', f'smart:{smart.id}', f'hashtag:{ids["tag"]}', f'folder:{ids["folder"]}']
    # Not recorded: missing records, other pages, saving, and going back in history.
    client.get('/app/list/9999')
    client.get('/app/calendar')
    client.get(f'/app/list/{ids["list"]}/edit')
    client.post(f'/app/list/{ids["other"]}/todos', headers=HX, data={'title': 'New'})
    client.get(f'/app/list/{ids["other"]}', headers={**HX, 'HX-History-Restore-Request': 'true'})
    assert shortcuts(db)['recent'][0] == f'list:{ids["list"]}' and len(shortcuts(db)['recent']) == 4
    for index in range(MAX_RECENT + 3):
        item = List(name=f'Extra {index}')
        db.add(item)
        db.commit()
        client.get(f'/app/list/{item.id}')
    assert len(shortcuts(db)['recent']) == MAX_RECENT


def test_strip_shows_favourites_then_recents_without_the_current_page(prototype):
    db, client, ids = prototype
    # With nothing to show yet, the hint says where the star is (it only exists on a folder or list page).
    assert 'a star appears here' in client.get('/app').text and 'class="strip-star"' not in client.get('/app').text
    first = client.get(f'/app/list/{ids["other"]}').text
    assert 'Use the star to keep “Weekend projects” here' in first and 'class="strip-star"' in first
    client.get(f'/app/list/{ids["other"]}')
    client.get(f'/app?folder_id={ids["folder"]}')
    star = client.post('/app/favourite', headers=HX, data={'key': f'folder:{ids["folder"]}', 'starred': 'true', 'at': f'/app?folder_id={ids["folder"]}'})
    assert star.status_code == 200 and '<!DOCTYPE html>' not in star.text and 'aria-pressed="true"' in star.text
    assert shortcuts(db)['favourites'] == [f'folder:{ids["folder"]}']
    page = client.get(f'/app/list/{ids["list"]}')
    strip = page.text.split('id="shortcut-strip"')[1].split('</nav>')[0]
    assert strip.index('is-favourite') < strip.index('is-recent')
    assert f'data-key="folder:{ids["folder"]}"' in strip and f'data-key="list:{ids["other"]}"' in strip
    # The open list is offered for starring, not as a link to itself; a favourite is not repeated as a recent.
    assert f'data-key="list:{ids["list"]}"' not in strip and 'aria-pressed="false"' in strip
    assert strip.count(f'data-key="folder:{ids["folder"]}"') == 1
    assert 'id="shortcut-strip"' in client.get('/app/calendar').text
    # Fragments leave the strip alone; the browser asks for it separately.
    assert 'shortcut-strip' not in client.get(f'/app/list/{ids["list"]}', headers=HX).text
    refreshed = client.get('/app/shortcuts', params={'at': f'/app?sort=user&folder_id={ids["folder"]}'}, headers=HX)
    assert 'aria-pressed="true"' in refreshed.text and 'aria-current="page"' in refreshed.text

    unstar = client.post('/app/favourite', headers=HX, data={'key': f'folder:{ids["folder"]}', 'at': ''})
    assert shortcuts(db)['favourites'] == [] and 'is-favourite' not in unstar.text
    assert client.post('/app/favourite', headers=HX, data={'key': 'list:9999', 'starred': 'true'}).status_code == 200
    assert client.post('/app/favourite', headers=HX, data={'key': 'bogus', 'starred': 'true'}).status_code == 200
    assert shortcuts(db)['favourites'] == []
    plain = client.post('/app/favourite', data={'key': f'list:{ids["list"]}', 'starred': 'true'}, follow_redirects=False)
    assert plain.status_code == 303 and plain.headers['location'] == f'/app/list/{ids["list"]}'


def test_deleted_places_disappear_and_other_settings_are_kept(prototype):
    db, client, ids = prototype
    db.add(UserSettings(id=1, metadata_json={'other': 1}))
    doomed = Folder(name='Doomed')
    db.add(doomed)
    db.commit()
    client.get(f'/app?folder_id={doomed.id}')
    client.post('/app/favourite', headers=HX, data={'key': f'folder:{doomed.id}', 'starred': 'true'})
    db.delete(db.get(Folder, doomed.id))
    db.commit()
    assert 'Doomed' not in client.get('/app').text
    assert 'Doomed' not in client.get('/app/jump', headers=HX).text
    db.expire_all()
    assert db.query(UserSettings).one().metadata_json['other'] == 1


def test_jump_lists_every_place_with_favourites_first(prototype):
    db, client, ids = prototype
    child = Folder(name='Garden', parent_id=ids['folder'])
    archived = List(name='Put away', archived=True)
    db.add_all([child, archived])
    db.flush()
    db.get(List, ids['other']).folder_id = child.id
    db.commit()
    client.get(f'/app/list/{ids["list"]}')
    client.post('/app/favourite', headers=HX, data={'key': f'list:{ids["other"]}', 'starred': 'true'})
    text = client.get('/app/jump', headers=HX).text
    assert '<!DOCTYPE html>' not in text and 'Put away' not in text
    assert text.index('Weekend projects') < text.index('A place to start') < text.index('>Garden<')
    assert 'data-name="projects &gt; garden weekend projects"' in text
    assert 'data-name="hashtag #personal"' in text and '/app/calendar' in text and 'data-jump-search="/app/search"' in text


def test_browser_strip_star_and_jump(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + f'/app/list/{ids["other"]}')
    strip = page.locator('#shortcut-strip')
    star = strip.get_by_role('button', name='Favourite: Weekend projects')
    expect(star).to_have_attribute('aria-pressed', 'false')
    star.click()
    expect(star).to_have_attribute('aria-pressed', 'true')
    expect(strip.locator('.strip-chip.is-favourite')).to_have_text('Weekend projects')

    # Ctrl+K, type, Enter: lands on the other list, and the first list becomes a link.
    page.keyboard.press('Control+k')
    box = page.get_by_role('searchbox', name='Jump to')
    expect(box).to_be_focused()
    dialog = page.locator('#jump-dialog')
    expect(dialog.locator('.jump-item.is-active')).to_contain_text('Weekend projects')
    box.fill('place st')
    expect(dialog.locator('.jump-item:visible')).to_have_count(2)
    expect(dialog.locator('.jump-item.is-active')).to_contain_text('A place to start')
    page.keyboard.press('Enter')
    expect(page).to_have_url(origin + f'/app/list/{ids["list"]}')
    expect(page.get_by_role('heading', level=1)).to_have_text('A place to start')
    expect(dialog).not_to_be_visible()
    expect(strip.get_by_role('button', name='Favourite: A place to start')).to_have_attribute('aria-pressed', 'false')

    # "/" opens it too; arrow keys choose; the last row searches for the text.
    page.locator('body').press('/')
    box.fill('zzz')
    expect(dialog.locator('.jump-item:visible')).to_have_count(1)
    page.keyboard.press('Enter')
    expect(page).to_have_url(origin + '/app/search?q=zzz')

    # Changing folder inside the overview refreshes the strip for the new place.
    page.goto(origin + '/app')
    page.locator('.folder-dropdown summary').click()
    page.locator('.folder-dropdown').get_by_role('link', name='Projects', exact=True).click()
    expect(strip.get_by_role('button', name='Favourite: Projects')).to_be_visible()
    strip.get_by_role('link', name='Weekend projects').click()
    expect(page.get_by_role('heading', level=1)).to_have_text('Weekend projects')
    expect(strip.locator('.strip-chip.is-recent').first).to_have_text('Projects')
    # Typing "/" in a field does not open the jump box.
    page.get_by_role('textbox', name='Task title').press('/')
    expect(dialog).not_to_be_visible()
    page.set_viewport_size({'width': 360, 'height': 700})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert errors == []
