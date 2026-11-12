"""Hashtags found in text: extraction rules, the SQLite index and the Hashtags pages."""
from sqlalchemy import text

from hashtag_index import all_hashtags, extract_hashtags, hashtag_entities, install_hashtag_index
from models import CalendarEvent, Folder, List, Note, Todo
from test_daisyui_test import HX, prototype


def test_extraction_rules():
    assert extract_hashtags('Call the plumber #home, then #Work (#next) #home') == ['home', 'Work', 'next']
    assert extract_hashtags('#Work', None, 'again #work and #école_2') == ['Work', 'école_2']
    # Not hashtags: numbers, colours, fragments, entities, a # inside a word, a bare or doubled #.
    for plain in ('#3 on the list', 'paint it #ff0000 or #0af', 'see page#section', 'http://example.com/#about',
                  'https://example.com/a.html#part', 'an &#x27; entity', '# heading', '##double', 'x' * 5 + '#' + 'y' * 101):
        assert extract_hashtags(plain) == [], plain
    # Letters-only hex words are ordinary words.
    assert extract_hashtags('#decade #fff') == ['decade', 'fff']


def test_index_follows_every_kind_of_write(prototype):
    db, client, ids = prototype
    db.get(Todo, ids['todo']).note = 'Started before the index existed #early'
    db.commit()
    install_hashtag_index(db.bind)
    entities = lambda name: {kind: sorted(found) for kind, found in hashtag_entities(db, name).items() if found}
    assert entities('early') == {'todos': [ids['todo']]}
    # ORM writes to each indexed table, in any case.
    folder, todo = db.get(Folder, ids['folder']), db.get(Todo, ids['todo'])
    folder.name = 'Projects #Home'
    db.get(List, ids['list']).name = 'A place to start #home'
    todo.title = 'Book a quiet afternoon #home #early'
    note = Note(text='Remember the keys #HOME', list_id=ids['list'], todo_id=ids['todo'])
    event = CalendarEvent(title='Dentist', note='Bring the form #home', start_at=todo.created_at, all_day=True)
    db.add_all([note, event])
    db.commit()
    assert entities('home') == {'folders': [ids['folder']], 'lists': [ids['list']], 'todos': [ids['todo']],
                                'notes': [note.id], 'calendar_events': [event.id]}
    assert entities('HOME') == entities('home') and db.execute(text('SELECT count(*) FROM hashtag_dirty')).scalar() == 0
    assert all_hashtags(db)[0] == ('home', {'folders': 1, 'lists': 1, 'todos': 1, 'notes': 1, 'calendar_events': 1})
    # Editing the text removes entries; edits that do not touch text are not queued at all.
    todo.title = 'Book a quiet afternoon'
    todo.note = None
    db.commit()
    assert 'todos' not in entities('home') and entities('early') == {}
    todo.completed = True
    db.flush()
    assert db.execute(text('SELECT count(*) FROM hashtag_dirty')).scalar() == 0
    db.commit()
    # Raw SQL is queued by the triggers and indexed at the next commit; deletes are removed at once.
    db.execute(text("UPDATE todos SET title = 'Raw #sql edit' WHERE id = :id"), {'id': ids['old']})
    db.execute(text('DELETE FROM notes WHERE id = :id'), {'id': note.id})
    assert db.execute(text('SELECT entity_type, entity_id FROM hashtag_dirty')).fetchall() == [('todos', ids['old'])]
    db.commit()
    db.expire_all()
    assert entities('sql') == {'todos': [ids['old']]} and 'notes' not in entities('home')
    # A rebuild at start-up gives the same index.
    before = db.execute(text('SELECT * FROM hashtag_index ORDER BY 1, 2, 3')).fetchall()
    db.execute(text('DELETE FROM hashtag_index'))
    db.commit()
    install_hashtag_index(db.bind)
    assert db.execute(text('SELECT * FROM hashtag_index ORDER BY 1, 2, 3')).fetchall() == before


def test_hashtag_pages_pills_and_moved_tag_addresses(prototype):
    db, client, ids = prototype
    # Created through the app before the Hashtags page was ever opened.
    assert client.post(f'/app/list/{ids["list"]}/todos', headers=HX, data={'title': 'Fix the tap #Home #urgent'}).status_code == 200
    index = client.get('/app/hashtags')
    assert index.status_code == 200 and index.headers['cache-control'] == 'no-store'
    assert [name for name, _ in index.context['hashtags']] == ['home', 'urgent']
    assert '<h1>Hashtags</h1>' in index.text and 'href="/app/hashtag/home">#home</a>' in index.text and '<span>1 task</span>' in index.text
    assert '<!DOCTYPE html>' not in client.get('/app/hashtags', headers=HX).text
    # Written afterwards: the index keeps up.
    folder = db.get(Folder, ids['folder'])
    folder.name = 'Projects #home'
    db.commit()
    page = client.get('/app/hashtag/Home')
    task = db.query(Todo).filter(Todo.title.like('Fix the tap%')).one()
    assert page.status_code == 200 and page.context['total'] == 2 and '<h1>#home</h1>' in page.text
    assert [f.id for f in page.context['found']['folders']] == [ids['folder']] and [t.id for t in page.context['found']['todos']] == [task.id]
    assert f'/app/list/{ids["list"]}?highlight={task.id}' in page.text and 'href="/app/hashtag/urgent"' in page.text
    assert 'Nothing contains #nothing.' in client.get('/app/hashtag/nothing').text
    # Pills: tags stay plain, hashtags carry the # and link to their own page.
    row = client.get(f'/app/list/{ids["list"]}').text.split(f'id="todo-row-{task.id}"')[1].split('</li>')[0]
    assert 'class="badge badge-outline tag text-hashtag" href="/app/hashtag/home">#Home</a>' in row and '>#urgent</a>' in row
    tagged = client.get(f'/app/list/{ids["list"]}').text.split(f'id="todo-row-{ids["todo"]}"')[1].split('</li>')[0]
    assert f'href="/app/tag/{ids["tag"]}">personal</a>' in tagged and 'text-hashtag' not in tagged
    home = client.get('/app').text
    assert 'href="/app/hashtag/home">#home</a>' in home.split(f'data-item-key="folder:{ids["folder"]}"')[1].split('row-actions')[0]
    # Tags moved to /app/tags and /app/tag/{id}; old tag links still arrive, and both are in the navigation.
    assert client.get('/app/tags').status_code == 200 and '<h1>Tags</h1>' in client.get('/app/tags').text
    assert client.get(f'/app/tag/{ids["tag"]}').context['hashtag'].id == ids['tag']
    old = client.get(f'/app/hashtag/{ids["tag"]}?sort=user', follow_redirects=False)
    assert old.status_code == 308 and old.headers['location'] == f'/app/tag/{ids["tag"]}?sort=user'
    bar = home.split('class="toolbar-navigation"')[1].split('</nav>')[0]
    assert 'href="/app/tags"' in bar and 'href="/app/hashtags"' in bar and bar.index('href="/app/tags"') < bar.index('href="/app/hashtags"')
    assert 'aria-current="page" title="Hashtags"' in index.text and 'aria-current="page" title="Tags"' in client.get('/app/tags').text
