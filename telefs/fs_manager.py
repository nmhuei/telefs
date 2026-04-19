import os
import mmap
import hashlib
import asyncio
import tempfile
import shutil
from pathlib import Path
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor

from tqdm import tqdm
from .storage import Storage
from .telegram_client import TelegramFSClient
from .config import get_encryption_key

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class FSManager:
    def __init__(self, db_path: Optional[str] = None):
        self.storage = Storage(db_path=db_path)
        self.tg = TelegramFSClient()
        self.cwd = "/"
        self.chunk_size = 20 * 1024 * 1024 # default
        self.max_concurrent = 3

    def _get_optimal_chunk_size(self, file_size: int) -> int:
        """Calculate optimal chunk size based on total file size."""
        if file_size < 1 * 1024 * 1024 * 1024:  # < 1GB
            return 20 * 1024 * 1024             # 20MB
        elif file_size < 5 * 1024 * 1024 * 1024: # < 5GB
            return 50 * 1024 * 1024             # 50MB
        else:
            return 100 * 1024 * 1024            # 100MB

    def connect(self):
        self.tg.connect()

    def disconnect(self):
        self.tg.disconnect()

    def pwd(self) -> str:
        return self.cwd

    def cd(self, path: str) -> bool:
        """Change current directory. Return success."""
        if path == "/":
            self.cwd = "/"
            return True
        if path == "..":
            if self.cwd == "/":
                return True
            parent = str(Path(self.cwd).parent)
            self.cwd = self.storage.normalize_path(parent)
            return True
            
        if path.startswith("/"):
            target = self.storage.normalize_path(path)
        else:
            target = self.storage.normalize_path(os.path.join(self.cwd, path))
            
        if self.storage.is_folder(target):
            self.cwd = target
            return True
        return False

    def ls(self, path: Optional[str] = None) -> List[str]:
        """List directory contents."""
        if path is None:
            target = self.cwd
        elif path.startswith("/"):
            target = self.storage.normalize_path(path)
        else:
            target = self.storage.normalize_path(os.path.join(self.cwd, path))

        if not self.storage.is_folder(target):
            return [f"ls: {path}: No such directory"]

        items = self.storage.list_folder(target)
        lines = []
        for item in items:
            prefix = "[DIR] " if item["type"] == "folder" else "[FILE]"
            size_str = self._format_size(item["size"]) if item["type"] == "file" else ""
            enc_str = " (Encrypted)" if item["encrypted"] else ""
            name = item["name"] + ("/" if item["type"] == "folder" else "")
            lines.append(f"{prefix} {name:<20} {size_str:>10} {enc_str}")
        return lines if lines else ["(empty)"]

    def tree(self) -> List[str]:
        """Return pretty tree representation."""
        items = self.storage.get_tree("/")
        if not items:
            return ["/"]
        lines = []
        for item in items:
            indent = "  " * item["level"]
            if item["path"] == "/":
                name = "/"
                suffix = ""
            else:
                name = Path(item["path"]).name
                suffix = "/" if item["type"] == "folder" else ""
            lines.append(f"{indent}{name}{suffix}")
        return lines

    def mkdir(self, path: str) -> bool:
        """Create a directory."""
        if path.startswith("/"):
            full = self.storage.normalize_path(path)
        else:
            full = self.storage.normalize_path(os.path.join(self.cwd, path))
        return self.storage.create_folder(full)

    def upload(self, local_path_str: str, remote_folder: str, progress=True) -> bool:
        """Wrapper to run async chunked upload."""
        return self.tg._run_async(self._upload_chunked(local_path_str, remote_folder, progress))

    async def _upload_chunked(self, local_path_str: str, remote_folder: str, progress=True) -> bool:
        """Upload a file in chunks with resume support and concurrency."""
        local_path = Path(local_path_str).expanduser().resolve()
        if not local_path.is_file():
            print(f"Error: '{local_path_str}' is not a file.")
            return False

        dest_folder = self.storage.normalize_path(remote_folder) if remote_folder.startswith("/") else \
                      self.storage.normalize_path(os.path.join(self.cwd, remote_folder))

        if not self.storage.is_folder(dest_folder):
            print(f"Error: Destination folder '{remote_folder}' not found.")
            return False

        remote_path = self.storage.normalize_path(os.path.join(dest_folder, local_path.name))
        file_size = local_path.stat().st_size
        
        # Calculate hash early for deduplication
        print(f"Calculating hash for {local_path.name}...")
        sha256 = hashlib.sha256()
        with open(local_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        file_hash = sha256.hexdigest()

        # Deduplication Check
        existing_file = self.storage.find_file_by_hash(file_hash)
        if existing_file:
            print(f"[Deduplication] Content already exists on Telegram. Linking...")
            # We must use exactly the same parameters as the existing file
            return self.storage.add_file(
                remote_path, None, 'me', file_size, 
                encrypted=existing_file["encrypted"],
                session_id=existing_file["session_id"],
                file_hash=file_hash,
                encryption_key=existing_file["encryption_key"]
            )

        chunk_size = self._get_optimal_chunk_size(file_size)
        total_chunks = (file_size + chunk_size - 1) // chunk_size

        # Check for resume
        session = self.storage.get_active_session(remote_path)
        if session:
            print(f"Resuming existing session for {remote_path}...")
            session_id = session["id"]
            enc_key = session["encryption_key"]
            file_hash = session["file_hash"]
            chunk_size = session["chunk_size"] # Must use the session's chunk size
        else:
            if self.storage.exists(remote_path):
                print(f"Error: '{remote_path}' already exists.")
                return False
            
            # Start new session
            print(f"Starting new upload for {local_path.name} ({total_chunks} chunks, {chunk_size//1024//1024}MB each)...")
            enc_key = os.urandom(32)
            session_id = self.storage.create_upload_session(
                remote_path, total_chunks, chunk_size, enc_key, file_hash
            )

        aesgcm = AESGCM(enc_key)
        chunks = self.storage.get_chunks(session_id)
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        pbar = None
        if progress:
            pbar = tqdm(total=total_chunks, unit='chunk', desc=f"Uploading {local_path.name}")

        async def upload_worker(chunk_info):
            async with semaphore:
                idx = chunk_info["chunk_index"]
                if chunk_info["status"] == "done":
                    if pbar: pbar.update(1)
                    return True

                offset = idx * chunk_size
                size = min(chunk_size, file_size - offset)
                
                # Use mmap for efficient reading
                with open(local_path, "rb") as f:
                    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                        data = mm[offset:offset+size]
                
                # Encrypt chunk
                nonce = os.urandom(12)
                ciphertext = aesgcm.encrypt(nonce, data, None)
                final_data = nonce + ciphertext
                
                try:
                    msg_id = await self.tg._upload_bytes(final_data, f"{local_path.name}.part{idx}")
                    self.storage.update_chunk(session_id, idx, msg_id, "done")
                    if pbar: pbar.update(1)
                    return True
                except Exception as e:
                    print(f"\nChunk {idx} failed: {e}")
                    return False

        tasks = [upload_worker(c) for c in chunks]
        results = await asyncio.gather(*tasks)
        
        if pbar: pbar.close()

        if all(results):
            self.storage.finalize_session(session_id)
            # Add to main items table
            self.storage.add_file(
                remote_path, None, 'me', file_size, True, 
                session_id=session_id, file_hash=file_hash, encryption_key=enc_key
            )
            print(f"Uploaded: {remote_path}")
            return True
        else:
            print(f"Upload incomplete. You can resume later.")
            return False

    def download(self, remote_path_str: str, local_dest: Optional[str] = None, progress=True) -> bool:
        """Wrapper to run async chunked download."""
        return self.tg._run_async(self._download_chunked(remote_path_str, local_dest, progress))

    async def _download_chunked(self, remote_path_str: str, local_dest: Optional[str] = None, progress=True) -> bool:
        """Download file chunks and reconstruct with concurrency."""
        full_remote = self.storage.normalize_path(remote_path_str) if remote_path_str.startswith("/") else \
                      self.storage.normalize_path(os.path.join(self.cwd, remote_path_str))

        item = self.storage.get_item(full_remote)
        if not item or item["type"] != "file":
            print(f"Error: '{remote_path_str}' not found or is a directory.")
            return False

        session_id = item["session_id"]
        if not session_id:
            print("Error: This file is in an old format and cannot be downloaded with chunked downloader.")
            return False

        enc_key = item["encryption_key"]
        aesgcm = AESGCM(enc_key)
        chunks = self.storage.get_chunks(session_id)
        local_path = Path(local_dest) if local_dest else Path.cwd() / item["name"]
        
        semaphore = asyncio.Semaphore(self.max_concurrent)
        pbar = None
        if progress:
            pbar = tqdm(total=len(chunks), unit='chunk', desc=f"Downloading {item['name']}")

        # Pre-allocate file
        with open(local_path, "wb") as f:
            f.truncate(item["size"])

        # Need chunk_size
        cur = self.storage.conn.execute("SELECT chunk_size FROM upload_sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
        chunk_size = row["chunk_size"]

        async def download_worker(chunk_info):
            async with semaphore:
                idx = chunk_info["chunk_index"]
                msg_id = chunk_info["message_id"]
                try:
                    data = await self.tg._download_bytes(msg_id)
                    nonce = data[:12]
                    ciphertext = data[12:]
                    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
                    
                    offset = idx * chunk_size
                    # Write at offset without reading entire file into memory
                    with open(local_path, "r+b") as f:
                        f.seek(offset)
                        f.write(plaintext)
                    
                    if pbar: pbar.update(1)
                    return True
                except Exception as e:
                    print(f"\nDownload chunk {idx} failed: {e}")
                    return False

        tasks = [download_worker(c) for c in chunks]
        results = await asyncio.gather(*tasks)
        
        if pbar: pbar.close()
        
        if all(results):
            print(f"Downloaded to: {local_path}")
            return True
        return False

    def rm(self, path: str, recursive=False) -> bool:
        """Remove file or directory and delete from Telegram."""
        full = self.storage.normalize_path(path) if path.startswith("/") else \
               self.storage.normalize_path(os.path.join(self.cwd, path))
        
        if not self.storage.exists(full):
            print(f"rm: {path}: No such file or directory")
            return False
            
        if self.storage.is_folder(full):
            if not recursive:
                children = self.storage.list_folder(full)
                if children:
                    print(f"rm: {path}: Directory not empty (use -r)")
                    return False
            
            tree = self.storage.get_tree(full)
            # Collect all message IDs that should be deleted
            msg_ids_to_delete = []
            
            for t_item in tree:
                actual_item = self.storage.get_item(t_item["path"])
                if actual_item["type"] == "file" and actual_item["session_id"]:
                    # Reference Count Check
                    if self.storage.get_session_usage_count(actual_item["session_id"]) <= 1:
                        chunks = self.storage.get_chunks(actual_item["session_id"])
                        msg_ids_to_delete.extend([c["message_id"] for c in chunks if c["message_id"]])
                elif actual_item["type"] == "file" and actual_item["message_id"]:
                    # Old format file - no ref counting for simple messages yet
                    msg_ids_to_delete.append(actual_item["message_id"])
            
            count = self.storage.delete_recursive(full)
            if msg_ids_to_delete:
                try:
                    for i in range(0, len(msg_ids_to_delete), 100):
                        self.tg.delete_messages('me', msg_ids_to_delete[i:i+100])
                except Exception: pass
            print(f"Removed folder and {count-1} items.")
            return True
        else:
            item = self.storage.get_item(full)
            msg_ids_to_delete = []
            if item["session_id"]:
                # Reference Count Check
                if self.storage.get_session_usage_count(item["session_id"]) <= 1:
                    chunks = self.storage.get_chunks(item["session_id"])
                    msg_ids_to_delete = [c["message_id"] for c in chunks if c["message_id"]]
            elif item["message_id"]:
                msg_ids_to_delete = [item["message_id"]]
            
            self.storage.delete_item(full)
            if msg_ids_to_delete:
                try:
                    for i in range(0, len(msg_ids_to_delete), 100):
                        self.tg.delete_messages('me', msg_ids_to_delete[i:i+100])
                except Exception: pass
            print(f"Removed file.")
            return True

    def get_completions(self, text: str) -> List[str]:
        """Get possible completions for a path prefix."""
        if "/" in text:
            dir_part, prefix = text.rsplit("/", 1)
            dir_part = dir_part if dir_part else "/"
        else:
            dir_part, prefix = ".", text

        if dir_part.startswith("/"):
            target = self.storage.normalize_path(dir_part)
        else:
            target = self.storage.normalize_path(os.path.join(self.cwd, dir_part))

        if not self.storage.is_folder(target):
            return []

        items = self.storage.list_folder(target)
        matches = []
        for item in items:
            name = item["name"]
            if name.startswith(prefix):
                suffix = "/" if item["type"] == "folder" else ""
                matches.append(name + suffix)
        return matches

    @staticmethod
    def _format_size(num: int) -> str:
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if abs(num) < 1024.0:
                return f"{num:3.1f} {unit}"
            num /= 1024.0
        return f"{num:.1f} PB"
