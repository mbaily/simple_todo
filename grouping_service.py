from typing import List, Dict, Any
from sqlalchemy.orm import Session
from models import Todo, Folder, List as TodoList

def organize_todos_into_tree(
    db: Session, 
    todos: List[Todo],
    sort_by_todo_date: bool = False,
    sort_field: str = 'modified_at',
    sort_desc: bool = True
) -> Dict[str, Any]:
    """
    Groups a list of todos into a hierarchical tree based on their List and Folder structure.
    
    Args:
        db: Database session
        todos: List of Todo objects
        sort_by_todo_date: If True, sort groups/folders recursively by their aggregated todo sort_field
        sort_field: Field to sort by ('modified_at' or 'created_at')
        sort_desc: True for descending (newest), False for ascending (oldest)
        
    Returns a root dictionary node:
    {
        'folders': {folder_id: Node},
        'lists': [ { 'list': ListObj, 'todos': [TodoObj, ...], 'sort_date': datetime }, ... ]
    }
    """
    
    # 1. Group todos by List
    groups = {}
    for t in todos:
        if t.list_id not in groups:
            groups[t.list_id] = {'list': t.list, 'todos': []}
        groups[t.list_id]['todos'].append(t)
    
    # Sort todos within groups via the sort settings passed
    for g in groups.values():
        val_attr = lambda x: getattr(x, sort_field)
        g['todos'].sort(key=val_attr, reverse=sort_desc)
        
        # Calculate group's representative date if sorting by date
        if sort_by_todo_date and g['todos']:
            dates = [val_attr(t) for t in g['todos'] if val_attr(t) is not None]
            if dates:
                g['sort_date'] = max(dates) if sort_desc else min(dates)
            else:
                # Fallback for None dates
                from datetime import datetime
                g['sort_date'] = datetime.min if sort_desc else datetime.max
        else:
             g['sort_date'] = None

    grouped_todos = list(groups.values())
    
    # 2. Build Tree Structure
    # Fetch all folders for efficient lookup
    all_folders = db.query(Folder).order_by(Folder.sort_order, Folder.name).all()
    folder_map = {f.id: f for f in all_folders}

    def get_folder_path(folder_id):
        path = []
        curr_id = folder_id
        while curr_id and curr_id in folder_map:
            f = folder_map[curr_id]
            path.append(f)
            curr_id = f.parent_id
        return list(reversed(path))
    
    root = {'folders': {}, 'lists': [], 'sort_date': None}

    for group in grouped_todos:
        lst = group['list']
        if not lst.folder_id:
            root['lists'].append(group)
        else:
            path = get_folder_path(lst.folder_id)
            current_level = root
            for folder in path:
                if folder.id not in current_level['folders']:
                    current_level['folders'][folder.id] = {
                        'folder': folder,
                        'folders': {},
                        'lists': [],
                        'sort_date': None
                    }
                current_level = current_level['folders'][folder.id]
            current_level['lists'].append(group)

    # 3. Sort nodes recursively
    def sort_node_children(node):
        # First recurse to leaf folders to ensure their sort_dates are calculated
        folder_list = list(node['folders'].values())
        for child in folder_list:
            sort_node_children(child)
            
        # Calculate sort_date for this node if using date sorting
        if sort_by_todo_date:
            child_dates = []
            
            # Dates from child lists
            for l in node['lists']:
                if l.get('sort_date'):
                    child_dates.append(l['sort_date'])
            
            # Dates from child folders
            for f in folder_list:
                if f.get('sort_date'):
                    child_dates.append(f['sort_date'])
                    
            if child_dates:
                # If sorting DESC (Newest), we want the MAX (newest) date of any child
                # If sorting ASC (Oldest), we want the MIN (oldest) date of any child
                node['sort_date'] = max(child_dates) if sort_desc else min(child_dates)
            else:
                 from datetime import datetime
                 node['sort_date'] = datetime.min if sort_desc else datetime.max

            # Now sort children using this sort_date
            def sort_key_with_date(item):
                d = item.get('sort_date')
                if d is None:
                     from datetime import datetime
                     d = datetime.min if sort_desc else datetime.max
                return d

            node['lists'].sort(key=lambda g: (sort_key_with_date(g), g['list'].name.lower()), reverse=sort_desc)
            folder_list.sort(key=lambda n: (sort_key_with_date(n), n['folder'].name.lower()), reverse=sort_desc)
        
        else:
            # Default Sorting
            node['lists'].sort(key=lambda g: g['list'].name.lower())
            folder_list.sort(key=lambda n: (n['folder'].sort_order, n['folder'].name.lower()))
        
        node['folder_nodes'] = folder_list
            
    sort_node_children(root)
    
    return root
