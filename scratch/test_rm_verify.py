import sys
import os
import sqlite3
import asyncio
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from telefs.fs_manager import FSManager

async def run_test():
    print("--- Starting RM Verification Test ---")
    
    with patch('telefs.fs_manager.TelegramFSClient') as MockTG:
        with patch('telefs.fs_manager.Storage') as MockStorage:
            
            mock_tg = MockTG.return_value
            mock_storage = MockStorage.return_value
            
            # Setup bridge mock
            mock_tg._run_async = lambda coro: asyncio.run(coro) if not asyncio.get_event_loop().is_running() else coro
            
            deleted_ids = []
            async def mock_batch_delete(msg_ids):
                print(f"MOCK: Deleting message IDs from Telegram: {msg_ids}")
                deleted_ids.extend(msg_ids)
            
            # 1. Test case: Single File RM
            print("\n- Test Case 1: RM Single File -")
            fs = FSManager()
            fs.storage = mock_storage
            fs.tg = mock_tg
            
            # Mock single item retrieval
            # Storage.get_item is called in rm()
            mock_storage.exists.return_value = True
            mock_storage.is_folder.return_value = False
            mock_storage.get_item.return_value = {"path": "/file1.txt", "message_id": 111, "type": "file", "session_id": None}
            mock_storage.normalize_path.side_effect = lambda x: x
            
            with patch.object(FSManager, '_batch_delete_async', side_effect=mock_batch_delete):
                fs.rm("/file1.txt")
                
                if 111 in deleted_ids:
                    print("✅ SUCCESS: Message ID 111 was deleted for single file.")
                else:
                    print("❌ FAILURE: Message ID 111 was NOT deleted.")
                
                if mock_storage.delete_item.called:
                    print("✅ SUCCESS: storage.delete_item() was called.")
            
            # 2. Test case: Recursive Folder RM
            print("\n- Test Case 2: RM Recursive Folder -")
            deleted_ids.clear()
            mock_storage.is_folder.return_value = True
            
            # Tree contains folder/file2.txt and folder/folder2/file3.txt
            mock_storage.get_tree.return_value = [
                {"path": "/folder/file2.txt", "name": "file2.txt", "type": "file"},
                {"path": "/folder/folder2", "name": "folder2", "type": "folder"},
                {"path": "/folder/folder2/file3.txt", "name": "file3.txt", "type": "file"}
            ]
            
            # Mock get_item for each path in tree
            items_map = {
                "/folder/file2.txt": {"path": "/folder/file2.txt", "message_id": 222, "type": "file", "session_id": None},
                "/folder/folder2": {"path": "/folder/folder2", "message_id": None, "type": "folder", "session_id": None},
                "/folder/folder2/file3.txt": {"path": "/folder/folder2/file3.txt", "message_id": 333, "type": "file", "session_id": None}
            }
            mock_storage.get_item.side_effect = lambda p: items_map.get(p)
            
            with patch.object(FSManager, '_batch_delete_async', side_effect=mock_batch_delete):
                fs.rm("/folder", recursive=True)
                
                actual_ids = set(deleted_ids)
                expected_ids = {222, 333}
                
                if actual_ids == expected_ids:
                    print(f"✅ SUCCESS: Collected all message IDs in folder: {actual_ids}")
                else:
                    print(f"❌ FAILURE: Expected IDs {expected_ids}, got {actual_ids}")
                
                if mock_storage.delete_recursive.called:
                    print("✅ SUCCESS: storage.delete_recursive() was called.")

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(run_test())
