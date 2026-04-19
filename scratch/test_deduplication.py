
import os
import sys
import tempfile
import hashlib
from unittest.mock import MagicMock
from pathlib import Path

# Add project root to path
sys.path.append(str(Path.cwd()))

from telefs.fs_manager import FSManager
from telefs.storage import Storage

def calculate_sha256(path):
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

def run_test():
    print("--- TESTING DEDUPLICATION & REFERENCE COUNTING ---\n")
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        manager = FSManager(db_path=db_path)
        manager.tg = MagicMock()
        manager.tg.connect.return_value = True
        
        # Mock upload to return message IDs
        msg_id_counter = 1000
        async def mock_upload_bytes(data, filename, progress_callback=None):
            nonlocal msg_id_counter
            msg_id_counter += 1
            return msg_id_counter
        
        manager.tg._upload_bytes = mock_upload_bytes
        manager.tg._run_async = lambda coro: manager.tg._get_loop().run_until_complete(coro)
        
        import asyncio
        manager.tg._get_loop = lambda: asyncio.get_event_loop()

        # 1. Create a dummy file
        src_file = "dedup_test.bin"
        with open(src_file, "wb") as f:
            f.write(os.urandom(25 * 1024 * 1024)) # 25MB (2 chunks)
        
        orig_hash = calculate_sha256(src_file)
        
        # 2. First Upload
        print("[Step 1] Uploading original file...")
        manager.upload(src_file, "/")
        
        # 3. Second Upload (Identical file)
        print("\n[Step 2] Uploading identical file (should deduplicate)...")
        clone_file = "dedup_test_clone.bin"
        import shutil
        shutil.copy(src_file, clone_file)
        
        # Track if _upload_bytes is called
        upload_call_count_before = msg_id_counter
        manager.upload(clone_file, "/")
        upload_call_count_after = msg_id_counter
        
        if upload_call_count_after == upload_call_count_before:
            print("SUCCESS: No new chunks uploaded. Deduplication worked!")
        else:
            print(f"FAILURE: {upload_call_count_after - upload_call_count_before} new chunks uploaded.")

        # 4. Verify Reference Counting
        print("\n[Step 3] Verifying Reference Counting...")
        item_orig = manager.storage.get_item("/dedup_test.bin")
        session_id = item_orig["session_id"]
        usage_count = manager.storage.get_session_usage_count(session_id)
        print(f"Usage count for session {session_id}: {usage_count} (Expected: 2)")
        
        # 5. Safe Delete
        print("\n[Step 4] Deleting the clone (messages should NOT be deleted)...")
        # Mock tg.delete_messages to track calls
        manager.tg.delete_messages = MagicMock()
        manager.rm("/dedup_test_clone.bin")
        
        if not manager.tg.delete_messages.called:
            print("SUCCESS: Telegram messages preserved because original file still exists.")
        else:
            print("FAILURE: Telegram messages were deleted prematurely!")

        # 6. Final Delete
        print("\n[Step 5] Deleting the original file (messages SHOULD be deleted)...")
        manager.rm("/dedup_test.bin")
        
        if manager.tg.delete_messages.called:
            print("SUCCESS: Telegram messages deleted after last reference removed.")
        else:
            print("FAILURE: Telegram messages were NOT deleted.")

        # Cleanup
        os.remove(src_file)
        os.remove(clone_file)
        print("\n--- PHASE 3 VERIFICATION COMPLETE ---")
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)

if __name__ == "__main__":
    run_test()
