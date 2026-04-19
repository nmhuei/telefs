import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from telefs.fs_manager import FSManager
from telefs.storage import Storage
import nest_asyncio
from unittest.mock import MagicMock, AsyncMock

# Allow nesting of event loops for testing in this environment
nest_asyncio.apply()

async def test_upload_logic():
    print("--- Testing Upload Logic ---")
    
    # 1. Setup temporary environment
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_upload.db")
        local_dir = os.path.join(tmp_dir, "local_files")
        os.makedirs(local_dir)
        
        # Create a test file
        test_file_path = os.path.join(local_dir, "hello.txt")
        with open(test_file_path, "w") as f:
            f.write("Hello TeleFS!")
            
        # 2. Mock Telegram Client to avoid real network traffic
        mock_tg = MagicMock()
        mock_tg.is_connected.return_value = True
        mock_tg.upload_file = AsyncMock(return_value=12345) # Mock message ID
        # Mock the bridge
        mock_tg._run_async = lambda coro: asyncio.get_event_loop().run_until_complete(coro)
        
        # 3. Initialize FSManager with mocks
        fs = FSManager(db_path=db_path)
        fs.tg = mock_tg
        
        print(f"Uploading {test_file_path} to /test/ ...")
        
        # 4. Perform upload
        try:
            fs.upload(test_file_path, "/test/")
            print("✅ Upload command executed successfully.")
        except Exception as e:
            print(f"❌ Upload command failed: {e}")
            return
            
        # 5. Verify Metadata
        item = fs.storage.get_item("/test/hello.txt")
        if item:
            print(f"✅ Metadata created for /test/hello.txt (ID: {item['id']})")
            if item['message_id'] == 12345:
                print("✅ Message ID correctly recorded.")
            else:
                print(f"❌ Incorrect Message ID: {item['message_id']}")
        else:
            print("❌ Metadata NOT found for uploaded file.")

        # 6. Test Directory Upload
        sub_dir = os.path.join(local_dir, "folder")
        os.makedirs(sub_dir)
        with open(os.path.join(sub_dir, "nested.txt"), "w") as f:
            f.write("I am nested")
            
        print(f"Uploading directory {local_dir} to /root_test/ ...")
        fs.upload(local_dir, "/root_test/", recursive=True)
        
        # Wait a bit for async tasks to finish if any (though we mocked synchronously)
        
        nested_item = fs.storage.get_item("/root_test/folder/nested.txt")
        if nested_item:
            print("✅ Recursive upload metadata verified.")
        else:
            # Check paths in storage
            print("Checking all paths in DB:")
            items = fs.storage.list_folder("/")
            for it in items:
                 print(f" - {it['path']}")
            print("❌ Recursive upload failed to find nested.txt")

if __name__ == "__main__":
    asyncio.run(test_upload_logic())
