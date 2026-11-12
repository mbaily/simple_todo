"""
Merkle tree synchronization module.

Provides efficient partial sync by comparing tree hashes between client and server.
Only changed subtrees are transmitted, minimizing bandwidth.
"""
import hashlib
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from models import (
    List as ListModel,
    Todo,
    Hashtag,
    SmartFolder,
    NamedSearch,
    CalendarEvent,
    CalendarEventCompletion,
    CalendarDay,
    ListDatetimeHeader,
    DokuWikiConfirmedItem,
    Folder,
    collation_todos,
)
from config import PINNED_OLD_DAYS, HOME_CALENDAR_AROUND_DAYS
from serializers import (
    serialize_calendar_day,
    serialize_list,
    serialize_todo,
    serialize_hashtag,
    serialize_smart_folder,
    serialize_named_search,
    serialize_calendar_event,
    serialize_header,
    serialize_folder,
)


def _hash(data: str) -> str:
    """Compute a short SHA256 hash of a string."""
    return hashlib.sha256(data.encode('utf-8')).hexdigest()[:16]


def _hash_entity(entity: dict) -> str:
    """Hash a single entity dict."""
    # Sort keys for consistent hashing
    return _hash(json.dumps(entity, sort_keys=True, default=str))


def _hash_collection(entities: List[dict]) -> str:
    """Hash a collection of entities by hashing their sorted individual hashes."""
    if not entities:
        return _hash("[]")
    entity_hashes = sorted([_hash_entity(e) for e in entities])
    return _hash(json.dumps(entity_hashes))


def build_data_tree(db: Session) -> Dict[str, Any]:
    """
    Build the data portion of the Merkle tree.
    
    Returns a dict with hash and children for each collection.
    """
    # Fetch all data
    lists = [serialize_list(l) for l in db.query(ListModel).all()]
    todos = [serialize_todo(t) for t in db.query(Todo).all()]
    hashtags = [serialize_hashtag(h) for h in db.query(Hashtag).all()]
    smart_folders = [serialize_smart_folder(sf) for sf in db.query(SmartFolder).all()]
    named_searches = [serialize_named_search(ns) for ns in db.query(NamedSearch).all()]
    calendar_events = [serialize_calendar_event(ev) for ev in db.query(CalendarEvent).all()]
    calendar_days = [serialize_calendar_day(day) for day in db.query(CalendarDay).all()]
    calendar_completions = [
        {"event_id": c.event_id, "occurrence_at": c.occurrence_at.isoformat()}
        for c in db.query(CalendarEventCompletion).all()
    ]
    headers = [serialize_header(h) for h in db.query(ListDatetimeHeader).all()]
    confirmed_items = [
        {"id": c.id, "item_type": c.item_type, "item_id": c.item_id}
        for c in db.query(DokuWikiConfirmedItem).all()
    ]
    folders = [serialize_folder(f) for f in db.query(Folder).all()]
    
    # Collation associations
    collation_rows = db.query(collation_todos).all()
    collation_associations = [
        {"collation_list_id": r.collation_list_id, "todo_id": r.todo_id}
        for r in collation_rows
    ]
    
    
    # Config values
    from config import PINNED_OLD_DAYS, HOME_CALENDAR_AROUND_DAYS, DATE_FORMAT, DATETIME_FORMAT
    config_data = {
        "pinned_old_days": PINNED_OLD_DAYS,
        "home_calendar_days": HOME_CALENDAR_AROUND_DAYS,
        "date_format": DATE_FORMAT,
        "datetime_format": DATETIME_FORMAT,
    }

    # Build children dict with hash and data for each collection
    children = {
        "lists": {"hash": _hash_collection(lists), "data": lists},
        "todos": {"hash": _hash_collection(todos), "data": todos},
        "hashtags": {"hash": _hash_collection(hashtags), "data": hashtags},
        "smart_folders": {"hash": _hash_collection(smart_folders), "data": smart_folders},
        "named_searches": {"hash": _hash_collection(named_searches), "data": named_searches},
        "calendar_events": {"hash": _hash_collection(calendar_events), "data": calendar_events},
        "calendar_days": {"hash": _hash_collection(calendar_days), "data": calendar_days},
        "calendar_completions": {"hash": _hash_collection(calendar_completions), "data": calendar_completions},
        "headers": {"hash": _hash_collection(headers), "data": headers},
        "confirmed_items": {"hash": _hash_collection(confirmed_items), "data": confirmed_items},
        "folders": {"hash": _hash_collection(folders), "data": folders},
        "collation_associations": {"hash": _hash_collection(collation_associations), "data": collation_associations},
        "config": {"hash": _hash_entity(config_data), "data": config_data},
    }
    
    # Compute parent hash from all child hashes
    child_hashes = sorted([f"{k}:{v['hash']}" for k, v in children.items()])
    data_hash = _hash(json.dumps(child_hashes))
    
    return {"hash": data_hash, "children": children}


def build_templates_tree(template_dir: Path) -> Dict[str, Any]:
    """
    Build the templates portion of the Merkle tree.
    
    Returns a dict with hash and children for each template file.
    """
    children = {}
    
    if template_dir.exists():
        for f in sorted(template_dir.glob("*.html")):
            try:
                content = f.read_text(encoding="utf-8")
                children[f.name] = {
                    "hash": _hash(content),
                    "data": content
                }
            except Exception:
                pass
    
    # Also include macros from subdirectories if any
    macros_dir = template_dir / "macros"
    if macros_dir.exists():
        for f in sorted(macros_dir.glob("*.html")):
            try:
                content = f.read_text(encoding="utf-8")
                key = f"macros/{f.name}"
                children[key] = {
                    "hash": _hash(content),
                    "data": content
                }
            except Exception:
                pass
    
    # Compute parent hash
    child_hashes = sorted([f"{k}:{v['hash']}" for k, v in children.items()])
    templates_hash = _hash(json.dumps(child_hashes)) if child_hashes else _hash("[]")
    
    return {"hash": templates_hash, "children": children}


def build_merkle_tree(db: Session, template_dir: Path) -> Dict[str, Any]:
    """
    Build the complete Merkle tree for sync.
    
    Structure:
    {
        "root": "abc123",
        "children": {
            "data": {"hash": "...", "children": {...}},
            "templates": {"hash": "...", "children": {...}}
        }
    }
    """
    data_tree = build_data_tree(db)
    templates_tree = build_templates_tree(template_dir)
    
    # Compute root hash
    root_components = [f"data:{data_tree['hash']}", f"templates:{templates_tree['hash']}"]
    root_hash = _hash(json.dumps(sorted(root_components)))
    
    return {
        "root": root_hash,
        "children": {
            "data": data_tree,
            "templates": templates_tree
        }
    }


def compute_diff(client_hashes: Dict[str, Any], server_tree: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Compare client hashes with server tree and return only changed subtrees.
    
    client_hashes format:
    {
        "root": "abc123",
        "data": "def456",
        "templates": "ghi789",
        "data_children": {"lists": "...", "todos": "...", ...},
        "templates_children": {"index.html": "...", ...}
    }
    
    Returns None if everything matches (root hash same).
    Returns dict with only changed subtrees otherwise.
    """
    # Quick check: if root matches, nothing changed
    if client_hashes.get("root") == server_tree["root"]:
        return None
    
    result = {"root": server_tree["root"], "children": {}}
    
    # Check data subtree
    client_data_hash = client_hashes.get("data", "")
    server_data = server_tree["children"]["data"]
    
    if client_data_hash != server_data["hash"]:
        # Data changed - check which collections changed
        client_data_children = client_hashes.get("data_children", {})
        changed_collections = {}
        
        for coll_name, coll_data in server_data["children"].items():
            client_coll_hash = client_data_children.get(coll_name, "")
            if client_coll_hash != coll_data["hash"]:
                # This collection changed, include it
                changed_collections[coll_name] = coll_data
        
        if changed_collections:
            result["children"]["data"] = {
                "hash": server_data["hash"],
                "children": changed_collections
            }
    
    # Check templates subtree
    client_templates_hash = client_hashes.get("templates", "")
    server_templates = server_tree["children"]["templates"]
    
    if client_templates_hash != server_templates["hash"]:
        # Templates changed - check which files changed
        client_templates_children = client_hashes.get("templates_children", {})
        changed_templates = {}
        
        for tpl_name, tpl_data in server_templates["children"].items():
            client_tpl_hash = client_templates_children.get(tpl_name, "")
            if client_tpl_hash != tpl_data["hash"]:
                changed_templates[tpl_name] = tpl_data
        
        if changed_templates:
            result["children"]["templates"] = {
                "hash": server_templates["hash"],
                "children": changed_templates
            }
    
    return result


def extract_hashes(tree: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract just the hashes from a full tree (for client storage).
    
    Returns a flat structure suitable for sending back to server.
    """
    result = {
        "root": tree["root"],
        "data": tree["children"]["data"]["hash"],
        "templates": tree["children"]["templates"]["hash"],
        "data_children": {},
        "templates_children": {}
    }
    
    # Extract data collection hashes
    for coll_name, coll_data in tree["children"]["data"]["children"].items():
        result["data_children"][coll_name] = coll_data["hash"]
    
    # Extract template file hashes
    for tpl_name, tpl_data in tree["children"]["templates"]["children"].items():
        result["templates_children"][tpl_name] = tpl_data["hash"]
    
    return result
