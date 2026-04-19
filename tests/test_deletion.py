import pytest
from unittest.mock import MagicMock, patch
import asyncio

@pytest.mark.asyncio
async def test_rm_file(fs_manager):
    # Create fake folder and file in DB
    fs_manager.storage.create_folder("/to_delete")
    fs_manager.cd("/to_delete")
    
    # Use real storage.add_file method
    fs_manager.storage.add_file("/to_delete/file.txt", 123, "me", size=100)
    
    with patch.object(fs_manager, "_batch_delete_async", return_value=None) as mock_del:
        fs_manager.rm("file.txt")
        mock_del.assert_called()
        
    items = fs_manager.ls()
    names = [row['name'] for row in items]
    assert "file.txt" not in names

def test_purge_all(fs_manager):
    # Populate DB with multiple items
    fs_manager.storage.add_file("/f1.txt", 1, "me", size=100)
    fs_manager.storage.add_file("/f2.txt", 2, "me", size=200)
    
    # Create a session to respect foreign keys
    fs_manager.storage.conn.execute(
        "INSERT INTO upload_sessions (id, file_path, total_chunks, chunk_size) VALUES (?, ?, ?, ?)",
        (10, "dummy", 1, 100)
    )
    
    # Manually add a session chunk
    fs_manager.storage.conn.execute(
        "INSERT INTO chunks (session_id, chunk_index, message_id) VALUES (?, ?, ?)",
        (10, 0, 3)
    )
    fs_manager.storage.conn.commit()
    
    with patch.object(fs_manager, "_batch_delete_async", return_value=None) as mock_del:
        fs_manager.purge()
        
        # Verify collection of IDs
        args, _ = mock_del.call_args
        sent_ids = set(args[0])
        assert sent_ids == {1, 2, 3}
        
    # Verify metadata is wiped
    assert fs_manager.ls() == []
