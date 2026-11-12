import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, joinedload
from models import Base, List, Todo, Hashtag, list_hashtags

# Setup in-memory DB
engine = create_engine('sqlite:///:memory:')
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
db = Session()

try:
    # 1. Setup Data
    tag = Hashtag(name="shopping")
    # List 1
    lst1 = List(name="Supermarket")
    db.add(tag)
    db.add(lst1)
    db.commit() # Assign IDs

    # Link list 1 to hashtag (implicit source)
    # Using explicit SQL because manual append sometimes tricky in test env with rels
    db.execute(list_hashtags.insert().values(list_id=lst1.id, hashtag_id=tag.id))

    # List 2 (Same name)
    lst2 = List(name="Supermarket")
    db.add(lst2)
    db.commit()

    # Create items
    # T1 in List 2 (explicitly tagged)
    t1 = Todo(title="Red lentils", list_id=lst2.id)
    t1.hashtags.append(tag)
    db.add(t1)

    # T2 in List 1 (implicit)
    t2 = Todo(title="Onions", list_id=lst1.id)
    db.add(t2)
    db.commit()

    # CLEAR SESSION
    db.expire_all()

    # 2. Query Logic
    hashtag_id = tag.id
    hashtag = db.query(Hashtag).options(
        joinedload(Hashtag.todos).joinedload(Todo.list), #.joinedload(List.folder),
        joinedload(Hashtag.lists) #.joinedload(List.folder)
    ).filter(Hashtag.id == hashtag_id).first()

    # Cascade logic
    hashtag.include_list_items = True

    if hashtag.show_completed:
        target_todos = list(hashtag.todos)
    else:
        target_todos = [t for t in hashtag.todos if not t.completed]

    if hashtag.include_list_items:
        existing_ids = {t.id for t in target_todos}
        for lst_obj in hashtag.lists:
            # Manually load todos if lazy?
            for t in lst_obj.todos:
                if t.id not in existing_ids:
                    if hashtag.show_completed or not t.completed:
                        target_todos.append(t)
                        existing_ids.add(t.id)

    # 3. Test Grouping
    from grouping_service import organize_todos_into_tree
    root = organize_todos_into_tree(db, target_todos)

    print("\n--- Tree Structure ---")
    for group in root['lists']:
        print(f"List Group: {group['list'].name} (ID: {group['list'].id})")
        for t in group['todos']:
            print(f"  - {t.title}")

except Exception as e:
    print(e)
