import pytest
import os
import asyncio

def test_initial_state(fs_manager):
    assert fs_manager.pwd() == "/"
    assert fs_manager.ls() == []

def test_mkdir(fs_manager):
    fs_manager.mkdir("test_dir")
    items = fs_manager.ls()
    assert len(items) == 1
    # items[0] is a sqlite3.Row, access by key
    assert items[0]['name'] == "test_dir"

def test_cd_and_pwd(fs_manager):
    fs_manager.mkdir("subdir")
    fs_manager.cd("subdir")
    assert fs_manager.pwd() == "/subdir"
    
    fs_manager.cd("..")
    assert fs_manager.pwd() == "/"

@pytest.mark.asyncio
async def test_upload_mocked(fs_manager):
    # Create a small dummy file
    with open("dummy_test.txt", "w") as f:
        f.write("test data")
    
    try:
        # FSManager.upload is async. We need to await it.
        await fs_manager.upload("dummy_test.txt", "/")
        items = fs_manager.ls()
        names = [row['name'] for row in items]
        assert "dummy_test.txt" in names
    finally:
        if os.path.exists("dummy_test.txt"):
            os.remove("dummy_test.txt")

def test_tree_view(fs_manager):
    fs_manager.mkdir("level1")
    fs_manager.cd("level1")
    fs_manager.mkdir("level2")
    
    tree_output = fs_manager.tree()
    # tree_output is a list of strings formatted for display
    full_text = "\n".join(tree_output)
    assert "level1" in full_text
    assert "level2" in full_text

def test_linux_layout(fs_manager):
    created, total = fs_manager.init_linux_layout()
    assert total >= 17
    assert created == total
    
    items = fs_manager.ls("/")
    names = [row['name'] for row in items]
    assert "etc" in names
    assert "bin" in names
    assert "home" in names
    
    # Check idempotency
    created2, total2 = fs_manager.init_linux_layout()
    assert total2 == total
    assert created2 == 0 # None should be "created" if they already exist

def test_dot_entries(fs_manager):
    # Initial setup
    fs_manager.mkdir("/test_dot")
    
    # Check with all=False (standard)
    items = fs_manager.ls("/test_dot", all=False)
    names = [row['name'] for row in items]
    assert "." not in names
    assert ".." not in names
    
    # Check with all=True (Linux -a)
    items_all = fs_manager.ls("/test_dot", all=True)
    names_all = [row['name'] for row in items_all]
    assert "." in names_all
    assert ".." in names_all
    
    # Verify . points to current
    dot_entry = next(i for i in items_all if i['name'] == ".")
    assert dot_entry['path'] == "/test_dot"
    
    # Verify .. points to parent
    dot_dot_entry = next(i for i in items_all if i['name'] == "..")
    assert dot_dot_entry['path'] == "/"
