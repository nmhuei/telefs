
import asyncio
import os
import hashlib
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# --- STANDALONE DOWNLOAD LOGIC (Extracted from fs_manager.py) ---
class StandaloneDownloader:
    def __init__(self, storage, tg):
        self.storage = storage
        self.tg = tg
        self.max_concurrent = 5
        self.MAX_CHUNK_RETRIES = 3

    async def _download_chunked(self, remote_path_str, local_dest, progress=False):
        item = self.storage.get_item(remote_path_str)
        session_id = item["session_id"]
        local_path = Path(local_dest)
        
        enc_key = item["encryption_key"]
        aesgcm = AESGCM(enc_key)
        chunks = self.storage.get_chunks(session_id)
        chunk_size = self.storage.get_chunk_size(session_id)

        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        # Pre-allocate file
        with open(local_path, "wb") as f:
            f.truncate(item["size"])

        failed_chunks = []

        async def download_worker(chunk_info):
            async with semaphore:
                idx = chunk_info["chunk_index"]
                msg_id = chunk_info["message_id"]
                for attempt in range(self.MAX_CHUNK_RETRIES):
                    try:
                        # Mock the Telegram download
                        data = await self.tg._download_bytes(msg_id)
                        
                        # Decrypt
                        nonce = data[:12]
                        ciphertext = data[12:]
                        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
                        
                        # Write to pre-allocated file
                        offset = idx * chunk_size
                        with open(local_path, "r+b") as f:
                            f.seek(offset)
                            f.write(plaintext)
                        return True
                    except Exception as e:
                        if attempt < self.MAX_CHUNK_RETRIES - 1:
                            await asyncio.sleep(0.1) # Small sleep for test
                        else:
                            failed_chunks.append(idx)
                            return False

        tasks = [download_worker(c) for c in chunks]
        results = await asyncio.gather(*tasks)

        if all(results):
            return True
        else:
            try:
                local_path.unlink()
            except OSError:
                pass
            return False

# --- MOCK OBJECTS ---
class MockStorage:
    def __init__(self, file_data, chunk_size, enc_key):
        self.file_data = file_data
        self.chunk_size = chunk_size
        self.enc_key = enc_key

    def get_item(self, path):
        return {
            "name": "test.txt",
            "size": len(self.file_data),
            "session_id": 1,
            "encryption_key": self.enc_key
        }

    def get_chunks(self, session_id):
        num_chunks = (len(self.file_data) + self.chunk_size - 1) // self.chunk_size
        return [{"chunk_index": i, "message_id": i + 100} for i in range(num_chunks)]
    
    def get_chunk_size(self, session_id):
        return self.chunk_size

class MockTG:
    def __init__(self, file_data, chunk_size, enc_key):
        self.file_data = file_data
        self.chunk_size = chunk_size
        self.aesgcm = AESGCM(enc_key)

    async def _download_bytes(self, msg_id):
        idx = msg_id - 100
        offset = idx * self.chunk_size
        size = min(self.chunk_size, len(self.file_data) - offset)
        chunk_data = self.file_data[offset:offset+size]
        
        nonce = os.urandom(12)
        ciphertext = self.aesgcm.encrypt(nonce, chunk_data, None)
        return nonce + ciphertext

# --- TEST RUNNER ---
async def run_test():
    print("🔬 Simulation: Independent Download Logic Verification")
    
    # Setup
    original_content = b"TELEFS-TEST-DATA-" * 500 # ~8.5KB
    chunk_size = 512
    enc_key = AESGCM.generate_key(bit_length=256)
    
    storage = MockStorage(original_content, chunk_size, enc_key)
    tg = MockTG(original_content, chunk_size, enc_key)
    downloader = StandaloneDownloader(storage, tg)
    
    test_file = Path("scratch/test_verify.bin")
    
    # Run
    print(f"⌛ Downloading {len(original_content)} bytes in {len(storage.get_chunks(1))} chunks...")
    success = await downloader._download_chunked("/remote/test", str(test_file))
    
    if success:
        print("✅ Logic Check: SUCCESS")
        with open(test_file, "rb") as f:
            final_data = f.read()
            
        if final_data == original_content:
            print("💎 INTEGRITY: File matches original data perfectly.")
            sha_orig = hashlib.sha256(original_content).hexdigest()
            sha_down = hashlib.sha256(final_data).hexdigest()
            print(f"   SHA256: {sha_orig}")
        else:
            print("❌ INTEGRITY: Data mismatch found!")
    else:
        print("❌ Logic Check: FAILED")

if __name__ == "__main__":
    asyncio.run(run_test())
