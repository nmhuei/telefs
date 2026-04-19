import sys
import os
import sqlite3
import asyncio
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from telefs.fs_manager import FSManager

async def run_test():
    print("--- Starting Purge Verification Test (V3) ---")
    
    # 1. Mock TelegramFSClient and Storage
    with patch('telefs.fs_manager.TelegramFSClient') as MockTG:
        with patch('telefs.fs_manager.Storage') as MockStorage:
            
            mock_tg = MockTG.return_value
            mock_storage = MockStorage.return_value
            
            # Setup mock data retrieval
            # FSManager.purge calls storage.conn.execute(...)
            # We will mock the .conn.execute return value
            mock_cursor_chunks = MagicMock()
            mock_cursor_chunks.fetchall.return_value = [(456,), (789,)]
            
            mock_cursor_items = MagicMock()
            mock_cursor_items.fetchall.return_value = [(123,)]
            
            # This is a bit tricky since FSManager uses storage.conn.execute directly
            mock_storage.conn.execute.side_effect = [mock_cursor_chunks, mock_cursor_items]
            
            deleted_ids = []
            async def mock_batch_delete(msg_ids):
                print(f"MOCK: Deleting message IDs: {msg_ids}")
                deleted_ids.extend(msg_ids)
            
            # Bridge mock
            mock_tg._run_async = lambda coro: asyncio.run(coro) if not asyncio.get_event_loop().is_running() else coro
            
            # 2. Init FSManager
            fs = FSManager()
            fs.storage = mock_storage
            fs.tg = mock_tg
            
            # Mock the internal delete method
            with patch.object(FSManager, '_batch_delete_async', side_effect=mock_batch_delete):
                print("Executing fs.purge()...")
                fs.purge()
                
                # 3. Verification
                print("\n--- Results ---")
                expected_ids = {123, 456, 789}
                actual_ids = set(deleted_ids)
                
                if expected_ids == actual_ids:
                    print("✅ SUCCESS: FSManager collected all message IDs and sent them to Telegram.")
                else:
                    print(f"❌ FAILURE: Expected collected IDs {expected_ids}, but got {actual_ids}")
                
                # Verify storage was wiped
                if mock_storage.wipe_all_metadata.called:
                    print("✅ SUCCESS: Storage.wipe_all_metadata() was called.")
                else:
                    print("❌ FAILURE: Storage.wipe_all_metadata() was NOT called.")

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(run_test())
