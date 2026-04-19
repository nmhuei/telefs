
import os
import sys
import asyncio
import tempfile
import hashlib
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

# Add project root to path
sys.path.append(str(Path.cwd()))

from telefs.fs_manager import FSManager

async def test_chunked_logic():
    print("--- TESTING CHUNKED TRANSFER LOGIC (RESUME & CONCURRENCY) ---\n")
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    local_file = "chunk_test.bin"
    downloaded_file = "chunk_test_dl.bin"
    
    try:
        # 1. Create a 45MB file (2 chunks of 20MB + 5MB)
        print("Creating 45MB dummy file...")
        content = os.urandom(45 * 1024 * 1024)
        with open(local_file, "wb") as f:
            f.write(content)
        
        manager = FSManager(db_path=db_path)
        manager.chunk_size = 20 * 1024 * 1024 # Force 20MB chunks
        
        # Mock Telegram Cloud
        cloud_storage = {} # msg_id -> bytes
        msg_counter = 1000

        async def mock_upload_bytes(data, filename, progress_callback=None):
            nonlocal msg_counter
            msg_id = msg_counter
            msg_counter += 1
            cloud_storage[msg_id] = data
            return msg_id

        async def mock_download_bytes(msg_id, progress_callback=None):
            return cloud_storage[msg_id]

        manager.tg = MagicMock()
        manager.tg._upload_bytes = mock_upload_bytes
        manager.tg._download_bytes = mock_download_bytes
        manager.tg._run_async = lambda coro: asyncio.run(coro)

        # 2. Test Partial Upload (Simulate Failure)
        print("\n[Step 2] Simulating partial upload failure...")
        original_worker = manager._upload_chunked
        
        # We manually create a session and upload only 1 chunk
        manager.connect()
        # Mocking the upload worker to fail on chunk 1
        real_upload_bytes = manager.tg._upload_bytes
        async def failing_upload(data, filename, progress=None):
            if "part1" in filename:
                raise Exception("Network failure on chunk 1")
            return await real_upload_bytes(data, filename, progress)
        
        manager.tg._upload_bytes = failing_upload
        
        success = await manager._upload_chunked(local_file, "/")
        print(f"Initial upload success (expected False): {success}")
        
        # Check DB for active session
        session = manager.storage.get_active_session("/chunk_test.bin")
        if session:
            chunks = manager.storage.get_chunks(session["id"])
            done_chunks = [c for c in chunks if c["status"] == "done"]
            print(f"Session found. Chunks done: {len(done_chunks)}/{len(chunks)}")
        else:
            print("FAILURE: No active session found in DB after interrupted upload.")
            return

        # 3. Test Resume
        print("\n[Step 3] Resuming upload...")
        manager.tg._upload_bytes = real_upload_bytes # Fix the mock
        success = await manager._upload_chunked(local_file, "/")
        print(f"Resume success: {success}")
        
        if not success:
            print("FAILURE: Resume failed.")
            return

        # 4. Test Download
        print("\n[Step 4] Downloading and verifying...")
        success = await manager._download_chunked("chunk_test.bin", downloaded_file)
        
        if not success:
            print("FAILURE: Download failed.")
            return

        # Verify hash
        def get_sha256(path):
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(4000), b""):
                    h.update(chunk)
            return h.hexdigest()

        orig_hash = get_sha256(local_file)
        dl_hash = get_sha256(downloaded_file)
        
        print(f"Original SHA256: {orig_hash}")
        print(f"Download SHA256: {dl_hash}")
        
        if orig_hash == dl_hash:
            print("\nSUCCESS: Chunked transfer, Encryption, and Resume verified!")
        else:
            print("\nFAILURE: Hash mismatch!")

    finally:
        # Cleanup
        for f in [local_file, downloaded_file, db_path]:
            if os.path.exists(f):
                os.remove(f)

if __name__ == "__main__":
    asyncio.run(test_chunked_logic())
