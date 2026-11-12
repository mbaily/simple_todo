"""Tests for reactive folder view and tree endpoints."""
import pytest
from models import Folder, List, UserSettings
from test_billing_accounts import account


def test_reactive_folder_view_and_endpoints(account):
    db, _, client = account

    # 1. Initial page load
    res_page = client.get("/folders")
    assert res_page.status_code == 200
    assert "Manage Folders" in res_page.text
    assert "folderTreeHandler" in res_page.text
    assert "tree-view-wrapper" in res_page.text

    # 2. Test Partial tree endpoint GET /folders/tree
    res_tree = client.get("/folders/tree", headers={"HX-Request": "true"})
    assert res_tree.status_code == 200
    assert "tree-root-container" in res_tree.text

    # 3. Create Root Folders via POST /folders/create (with HX-Request)
    res_create1 = client.post("/folders/create", data={"name": "Work", "parent_id": 0}, headers={"HX-Request": "true"})
    assert res_create1.status_code == 200
    assert res_create1.json()["status"] == "ok"
    folder_work_id = res_create1.json()["id"]

    res_create2 = client.post("/folders/create", data={"name": "Personal", "parent_id": 0}, headers={"HX-Request": "true"})
    assert res_create2.status_code == 200
    folder_personal_id = res_create2.json()["id"]

    # Create subfolder inside Work
    res_sub = client.post("/folders/create", data={"name": "Projects", "parent_id": folder_work_id}, headers={"HX-Request": "true"})
    assert res_sub.status_code == 200
    folder_projects_id = res_sub.json()["id"]

    # Verify tree partial has Work, Personal, Projects
    res_tree2 = client.get("/folders/tree", headers={"HX-Request": "true"})
    assert "Work" in res_tree2.text
    assert "Personal" in res_tree2.text
    assert "Projects" in res_tree2.text

    # 4. Rename Folder
    res_rename = client.post("/folders/rename", data={"folder_id": folder_work_id, "new_name": "Work Office"}, headers={"HX-Request": "true"})
    assert res_rename.status_code == 200
    assert res_rename.json()["name"] == "Work Office"

    f_work = db.query(Folder).filter(Folder.id == folder_work_id).first()
    assert f_work.name == "Work Office"

    # 5. Toggle Folder Collapse
    assert f_work.is_collapsed == False
    res_collapse = client.post("/folders/toggle_collapse", data={"folder_id": folder_work_id}, headers={"HX-Request": "true"})
    assert res_collapse.status_code == 200
    assert res_collapse.json()["is_collapsed"] == True
    db.refresh(f_work)
    assert f_work.is_collapsed == True

    # Toggle back
    res_uncollapse = client.post("/folders/toggle_collapse", data={"folder_id": folder_work_id}, headers={"HX-Request": "true"})
    assert res_uncollapse.status_code == 200
    assert res_uncollapse.json()["is_collapsed"] == False

    # 6. Create Lists and Move them
    lst1 = List(name="Task List 1", folder_id=None)
    lst2 = List(name="Task List 2", folder_id=folder_work_id)
    db.add_all([lst1, lst2])
    db.commit()

    # Move lst1 into Work Office
    res_move_list = client.post("/folders/move", data={"source_type": "list", "source_id": lst1.id, "target_folder_id": folder_work_id}, headers={"HX-Request": "true"})
    assert res_move_list.status_code == 200
    db.refresh(lst1)
    assert lst1.folder_id == folder_work_id

    # Move lst1 back to Root
    res_move_root = client.post("/folders/move", data={"source_type": "list", "source_id": lst1.id, "target_folder_id": 0}, headers={"HX-Request": "true"})
    assert res_move_root.status_code == 200
    db.refresh(lst1)
    assert lst1.folder_id is None

    # Test error cases for move
    res_err_list = client.post("/folders/move", data={"source_type": "list", "source_id": 99999, "target_folder_id": 0}, headers={"HX-Request": "true"})
    assert res_err_list.status_code == 404

    res_err_target = client.post("/folders/move", data={"source_type": "list", "source_id": lst1.id, "target_folder_id": 99999}, headers={"HX-Request": "true"})
    assert res_err_target.status_code == 404

    # 7. Move Folders & Cycle Detection
    # Try to move Work into its own subfolder Projects (Cycle!)
    res_cycle = client.post("/folders/move", data={"source_type": "folder", "source_id": folder_work_id, "target_folder_id": folder_projects_id}, headers={"HX-Request": "true"})
    assert res_cycle.status_code == 400
    assert "Cannot move folder into its own subfolder" in res_cycle.json()["detail"]

    # Try to move Work into Work (Self cycle!)
    res_self_cycle = client.post("/folders/move", data={"source_type": "folder", "source_id": folder_work_id, "target_folder_id": folder_work_id}, headers={"HX-Request": "true"})
    assert res_self_cycle.status_code == 400
    assert "Cannot move folder into itself" in res_self_cycle.json()["detail"]

    # Valid move: move Projects out of Work into Personal
    res_valid_move = client.post("/folders/move", data={"source_type": "folder", "source_id": folder_projects_id, "target_folder_id": folder_personal_id}, headers={"HX-Request": "true"})
    assert res_valid_move.status_code == 200
    f_proj = db.query(Folder).filter(Folder.id == folder_projects_id).first()
    assert f_proj.parent_id == folder_personal_id

    # 8. Reorder siblings
    res_reorder = client.post("/folders/reorder", data={"source_id": folder_work_id, "target_id": folder_personal_id}, headers={"HX-Request": "true"})
    assert res_reorder.status_code == 200
    db.refresh(f_work)
    f_pers = db.query(Folder).filter(Folder.id == folder_personal_id).first()
    assert f_work.sort_order > f_pers.sort_order

    # 9. Set Active Folder
    res_active = client.post("/folders/set_active", data={"folder_id": folder_work_id}, headers={"HX-Request": "true"})
    assert res_active.status_code == 200
    settings = db.query(UserSettings).first()
    assert settings.active_folder_id == folder_work_id

    # Clear Active Folder
    res_clear_active = client.post("/folders/set_active", data={"folder_id": 0}, headers={"HX-Request": "true"})
    assert res_clear_active.status_code == 200
    db.refresh(settings)
    assert settings.active_folder_id is None

    # 10. Delete Folder
    # Put lst2 in Projects
    lst2.folder_id = folder_projects_id
    db.commit()

    # Delete Personal folder: Projects should be moved to Root, not deleted
    res_delete = client.post("/folders/delete", data={"folder_id": folder_personal_id}, headers={"HX-Request": "true"})
    assert res_delete.status_code == 200

    db.refresh(f_proj)
    assert f_proj.parent_id is None  # Moved to root!
    deleted_f = db.query(Folder).filter(Folder.id == folder_personal_id).first()
    assert deleted_f is None

