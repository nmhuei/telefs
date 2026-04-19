
import os
import sys
import tempfile
import time
from unittest.mock import MagicMock
from pathlib import Path

# Add project root to path
sys.path.append(str(Path.cwd()))

from telefs.fs_manager import FSManager

def test_large_file():
    print("--- TESTING LARGE FILE STREAMING ENCRYPTION (>100MB) ---\n")
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    local_file = "large_test_file.bin"
    downloaded_file = "large_test_file_dl.bin"
    
    try:
        # 1. Create a 105MB file (not power of 2 to test partial chunks)
        size_mb = 105
        print(f"Creating {size_mb}MB dummy file...")
        with open(local_file, "wb") as f:
            # Write in chunks to avoid memory issues during creation
            for _ in range(size_mb):
                f.write(os.urandom(1024 * 1024))
        
        manager = FSManager(db_path=db_path)
        
        # Mock Telegram Client
        # We need to mock upload_file to just copy the file somewhere as if it was uploaded
        # and mock download_file to copy it back.
        storage_mock = {} # mock telegram "cloud"
        
        def mock_upload(path, callback=None):
            # In real TeleFS, upload_path can be an encrypted temp file
            file_id = f"file_{int(time.time())}"
            storage_mock[file_id] = path.read_bytes() if hasattr(path, "read_bytes") else open(path, "rb").read()
            if callback:
                callback(len(storage_mock[file_id]), len(storage_mock[file_id]))
            return (999, "me", file_id)

        def mock_download(msg_id, peer_id, dest, callback=None):
            # Find the file_id from message_id? In this test we only have one.
            file_id = list(storage_mock.keys())[0]
            with open(dest, "wb") as f:
                f.write(storage_mock[file_id])
            if callback:
                callback(len(storage_mock[file_id]), len(storage_mock[file_id]))

        manager.tg = MagicMock()
        manager.tg.upload_file.side_effect = mock_upload
        manager.tg.download_file.side_effect = mock_download
        manager.tg.connect.return_value = True

        # 2. Upload
        print("\nUploading (with encryption)...")
        start_time = time.time()
        success = manager.upload(local_file, "/")
        upload_time = time.time() - start_time
        print(f"Upload complete in {upload_time:.2f}s")
        
        if not success:
            print("Upload failed!")
            return

        # 3. Download
        print("\nDownloading (with decryption)...")
        start_time = time.time()
        success = manager.download("large_test_file.bin", downloaded_file)
        download_time = time.time() - start_time
        print(f"Download complete in {download_time:.2f}s")

        if not success:
            print("Download failed!")
            return

        # 4. Verify Integrity
        print("\nVerifying file integrity...")
        import hashlib
        def get_md5(path):
            hash_md5 = hashlib.md5()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()

        original_md5 = get_md5(local_file)
        downloaded_md5 = get_md5(downloaded_file)
        
        print(f"Original MD5:   {original_md5}")
        print(f"Downloaded MD5: {downloaded_md5}")
        
        if original_md5 == downloaded_md5:
            print("\nSUCCESS: Integrity verified! Streaming encryption/decryption works perfectly.")
        else:
            print("\nFAILURE: Integrity check failed! Files do not match.")

    finally:
        # Cleanup
        for f in [local_file, downloaded_file, db_path]:
            if os.path.exists(f):
                os.remove(f)

if __name__ == "__main__":
    test_large_file()
