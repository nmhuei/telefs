import os
import mmap
import hashlib
import asyncio
import tempfile
import shutil
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.fernet import Fernet
from pathlib import Path
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

from tqdm import tqdm
from .storage import Storage
from .telegram_client import TelegramFSClient, SESSION_PATH
from .config import get_encryption_key, is_configured, get_phone_number, get_cwd, save_cwd

MAX_CHUNK_RETRIES = 3


class FSManager:
    def __init__(self, db_path: Optional[str] = None):
        self.storage = Storage(db_path=db_path)
        self.tg = TelegramFSClient()
        self.cwd = "/"  # Always start at root
        self.chunk_size = 20 * 1024 * 1024  # default
        self.max_concurrent = 3
        
        # Repair any metadata inconsistencies on startup
        try:
            self.storage.repair_metadata()
        except Exception:
            pass

    def _get_optimal_chunk_size(self, file_size: int) -> int:
        if file_size < 1 * 1024 * 1024 * 1024:   # < 1 GB
            return 20 * 1024 * 1024               # 20 MB
        elif file_size < 5 * 1024 * 1024 * 1024:  # < 5 GB
            return 50 * 1024 * 1024               # 50 MB
        else:
            return 100 * 1024 * 1024              # 100 MB

    def connect(self):
        self.tg.connect()

    def disconnect(self):
        self.tg.disconnect()

    def pwd(self) -> str:
        return self.cwd

    def get_status(self) -> dict:
        session_file = Path(f"{SESSION_PATH}.session")
        stats = {"files": 0, "folders": 0, "total_size": 0}
        try:
            cur = self.storage.conn.execute("SELECT COUNT(*) FROM items WHERE type = 'file'")
            stats["files"] = cur.fetchone()[0]
            cur = self.storage.conn.execute("SELECT COUNT(*) FROM items WHERE type = 'folder'")
            stats["folders"] = cur.fetchone()[0]
            cur = self.storage.conn.execute("SELECT SUM(size) FROM items WHERE type = 'file'")
            row = cur.fetchone()
            stats["total_size"] = row[0] if row[0] else 0
        except Exception:
            pass
        return {
            "api_configured": is_configured(),
            "phone": get_phone_number(),
            "session_exists": session_file.exists(),
            "db_stats": stats,
        }

    def _resolve_path(self, path: str) -> str:
        """Resolve a path (absolute or relative to cwd) to an absolute normalized path."""
        if not path or path.strip() == ".":
            return self.cwd
        if path.strip() == "/":
            return "/"
        
        if path.startswith("/"):
            return self.storage.normalize_path(path)
            
        full = os.path.join(self.cwd, path)
        return self.storage.normalize_path(full)

    def cd(self, path: str) -> bool:
        """Change current directory. Return success."""
        # FIX: consolidate path resolution so "cd ." and "cd ~" work sensibly
        if path in ("", "."):
            return True
        if path == "..":
            if self.cwd == "/":
                return True
            parent = str(Path(self.cwd).parent)
            self.cwd = self.storage.normalize_path(parent)
            return True
        target = self._resolve_path(path)
        if self.storage.is_folder(target):
            self.cwd = target
            return True
        return False

    def ls(self, path: str = ".", recursive: bool = False, all: bool = False) -> List[Any]:
        target = self._resolve_path(path)

        if not self.storage.is_folder(target):
            item = self.storage.get_item(target)
            if item:
                return [item]
            return [f"ls: {path}: No such file or directory"]

        if recursive:
            results = []
            folders_to_visit = [target]
            while folders_to_visit:
                current = folders_to_visit.pop(0)
                items = self.storage.list_folder(current, include_hidden=all)
                results.append({"type": "header", "path": current})
                for item in items:
                    results.append(dict(item))
                    if item["type"] == "folder":
                        folders_to_visit.append(item["path"])
            return results

        return [dict(i) for i in self.storage.list_folder(target, include_hidden=all)]

    def cat(self, path: str) -> bool:
        return self.tg._run_async(self._cat_async(path))

    async def _cat_async(self, path: str) -> bool:
        full = self._resolve_path(path)
        item = self.storage.get_item(full)
        if not item or item["type"] != "file":
            print(f"cat: {path}: No such file or directory")
            return False

        session_id = item["session_id"]
        enc_key = item["encryption_key"]

        if not session_id:
            # Legacy single-message format
            msg_id = item["message_id"]
            try:
                data = await self.tg._download_bytes(msg_id)
                if item["encrypted"]:
                    global_key = get_encryption_key()
                    if global_key:
                        f = Fernet(global_key)
                        data = f.decrypt(data)
                    else:
                        print("cat: file is encrypted but no legacy key found.")
                        return False
                if b'\x00' in data[:1024]:
                    print("\n[Warning] Binary file detected. Use 'download' instead.")
                    return False
                print(data.decode('utf-8', errors='replace'), end='')
                print()
                return True
            except Exception as e:
                print(f"\n[Error] Failed to read legacy file: {e}")
                return False

        aesgcm = AESGCM(enc_key)
        chunks = self.storage.get_chunks(session_id)
        for chunk in chunks:
            msg_id = chunk["message_id"]
            try:
                data = await self.tg._download_bytes(msg_id)
                nonce = data[:12]
                ciphertext = data[12:]
                plaintext = aesgcm.decrypt(nonce, ciphertext, None)
                if b'\x00' in plaintext[:1024]:
                    print("\n[Warning] Binary file detected. Use 'download' instead.")
                    return False
                print(plaintext.decode('utf-8', errors='replace'), end='')
            except Exception as e:
                print(f"\n[Error] Failed to read chunk: {e}")
                return False
        print()
        return True

    def du(self, path: str = "/") -> int:
        full = self._resolve_path(path)
        item = self.storage.get_item(full)
        if not item:
            return 0
        if item["type"] == "file":
            return item["size"]
        escaped = full.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        cur = self.storage.conn.execute("""
            SELECT SUM(size) as total FROM items
            WHERE path = ? OR path LIKE ? || '/%' ESCAPE '\\'
        """, (full, escaped))
        row = cur.fetchone()
        return row["total"] if row and row["total"] else 0

    def stat(self, path: str) -> Optional[dict]:
        full = self._resolve_path(path)
        item = self.storage.get_item(full)
        if not item:
            return None
        info = dict(item)
        info["size_h"] = self._format_size(item["size"])
        return info

    def find(self, pattern: str, path: Optional[str] = None) -> List[dict]:
        root = self._resolve_path(path) if path else self.cwd
        items = self.storage.find_items(pattern, root)
        return [dict(i) for i in items]

    def get_checksum(self, path: str) -> Optional[str]:
        full = self._resolve_path(path)
        item = self.storage.get_item(full)
        if not item or item["type"] != "file":
            return None
        return dict(item).get("file_hash")

    def tree(self) -> List[str]:
        items = self.storage.get_tree("/")
        if not items:
            return ["/"]
        lines = []
        for item in items:
            indent = "  " * item["level"]
            name = "/" if item["path"] == "/" else Path(item["path"]).name
            suffix = "/" if item["type"] == "folder" else ""
            lines.append(f"{indent}{name}{suffix}")
        return lines

    def mkdir(self, path: str, parents: bool = False) -> bool:
        full = self._resolve_path(path)
        return self.storage.create_folder(full, parents=parents)

    def upload(self, local_path_str: str, remote_folder: str, recursive=False, progress=True) -> bool:
        local_path = Path(local_path_str).expanduser().resolve()
        if local_path.is_dir():
            if not recursive:
                print(f"Error: '{local_path_str}' is a directory. Use -r for recursive upload.")
                return False
            return self.upload_directory(local_path, remote_folder, progress)
        if not local_path.is_file():
            print(f"Error: '{local_path_str}' is not a file or directory.")
            return False
        return self.tg._run_async(self._upload_chunked(str(local_path), remote_folder, progress))

    def upload_directory(self, local_root: Path, remote_parent: str, progress=True) -> bool:
        print(f"Scanning directory: {local_root}")
        target_remote_root = self.storage.normalize_path(
            os.path.join(self._resolve_path(remote_parent), local_root.name)
        )
        self.mkdir(target_remote_root, parents=True)
        success = True
        for root, dirs, files in os.walk(local_root):
            rel_path = os.path.relpath(root, local_root.parent)
            current_remote_dir = self.storage.normalize_path(
                os.path.join(self._resolve_path(remote_parent), rel_path)
            )
            for d in dirs:
                self.mkdir(os.path.join(current_remote_dir, d), parents=True)
            for f in files:
                local_f = os.path.join(root, f)
                if not self.upload(local_f, current_remote_dir, recursive=False, progress=progress):
                    success = False
        if success:
            print(f"Recursive upload of '{local_root.name}' completed.")
        return success

    async def _upload_chunked(self, local_path_str: str, remote_folder: str, progress=True) -> bool:
        local_path = Path(local_path_str).expanduser().resolve()
        if not local_path.is_file():
            print(f"Error: '{local_path_str}' is not a file.")
            return False

        dest_folder = self._resolve_path(remote_folder)
        if not self.storage.is_folder(dest_folder):
            print(f"Error: Destination folder '{remote_folder}' not found.")
            return False

        remote_path = self.storage.normalize_path(os.path.join(dest_folder, local_path.name))
        file_size = local_path.stat().st_size

        # Hash computation
        print(f"Calculating hash for {local_path.name}...")
        sha256 = hashlib.sha256()
        with open(local_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        file_hash = sha256.hexdigest()

        # Deduplication
        existing_file = self.storage.find_file_by_hash(file_hash)
        if existing_file:
            print("[Deduplication] Content already exists on Telegram. Linking...")
            return self.storage.add_file(
                remote_path, None, 'me', file_size,
                encrypted=existing_file["encrypted"],
                session_id=existing_file["session_id"],
                file_hash=file_hash,
                encryption_key=existing_file["encryption_key"],
            )

        chunk_size = self._get_optimal_chunk_size(file_size)
        total_chunks = (file_size + chunk_size - 1) // chunk_size

        session = self.storage.get_active_session(remote_path)
        if session:
            print(f"Resuming existing session for {remote_path}...")
            session_id = session["id"]
            enc_key = session["encryption_key"]
            file_hash = session["file_hash"]
            chunk_size = session["chunk_size"]
        else:
            if self.storage.exists(remote_path):
                print(f"Error: '{remote_path}' already exists.")
                return False
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
            pending = sum(1 for c in chunks if c["status"] != "done")
            done_count = total_chunks - pending
            pbar = tqdm(total=total_chunks, initial=done_count, unit='chunk', desc=f"Uploading {local_path.name}")

        # FIX: retry failed chunks up to MAX_CHUNK_RETRIES times
        async def upload_worker(chunk_info):
            async with semaphore:
                idx = chunk_info["chunk_index"]
                if chunk_info["status"] == "done":
                    return True

                offset = idx * chunk_size
                size = min(chunk_size, file_size - offset)

                with open(local_path, "rb") as f:
                    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                        data = mm[offset:offset + size]

                nonce = os.urandom(12)
                ciphertext = aesgcm.encrypt(nonce, data, None)
                final_data = nonce + ciphertext

                for attempt in range(MAX_CHUNK_RETRIES):
                    try:
                        msg_id = await self.tg._upload_bytes(final_data, f"{local_path.name}.part{idx}")
                        self.storage.update_chunk(session_id, idx, msg_id, "done")
                        if pbar:
                            pbar.update(1)
                        return True
                    except Exception as e:
                        self.storage.increment_chunk_retry(session_id, idx)
                        if attempt < MAX_CHUNK_RETRIES - 1:
                            await asyncio.sleep(2 ** attempt)  # exponential back-off
                        else:
                            print(f"\nChunk {idx} failed after {MAX_CHUNK_RETRIES} attempts: {e}")
                            return False

        tasks = [upload_worker(c) for c in chunks]
        results = await asyncio.gather(*tasks)

        if pbar:
            pbar.close()

        if all(results):
            self.storage.finalize_session(session_id)
            self.storage.add_file(
                remote_path, None, 'me', file_size, True,
                session_id=session_id, file_hash=file_hash, encryption_key=enc_key,
            )
            print(f"Uploaded: {remote_path}")
            return True
        else:
            print("Upload incomplete. You can resume later.")
            return False

    def download(self, remote_path_str: str, local_dest: Optional[str] = None, recursive=False, progress=True) -> bool:
        if recursive:
            return self.download_directory(remote_path_str, local_dest, progress)
        return self.tg._run_async(self._download_chunked(remote_path_str, local_dest, progress))

    def download_directory(self, remote_path_str: str, local_dest: Optional[str] = None, progress=True) -> bool:
        full_remote = self._resolve_path(remote_path_str)
        item = self.storage.get_item(full_remote)
        if not item or item["type"] != "folder":
            print(f"Error: '{remote_path_str}' not found or is not a directory.")
            return False

        # If local_dest is not provided, use CWD
        local_base = Path(local_dest) if local_dest else Path.cwd()
        
        # Target folder name is the same as remote folder name
        target_local_root = local_base / item["name"]
        target_local_root.mkdir(parents=True, exist_ok=True)
        
        print(f"Downloading directory: {full_remote} -> {target_local_root}")
        
        # Get all descendants
        items = self.storage.get_tree(full_remote, include_hidden=True)
        
        success = True
        for d_item in items:
            if d_item["path"] == full_remote:
                continue # Skip the root itself
                
            # Compute local path
            rel_path = d_item["path"][len(full_remote):].lstrip("/")
            local_path = target_local_root / rel_path
            
            if d_item["type"] == "folder":
                local_path.mkdir(parents=True, exist_ok=True)
            else:
                # Ensure parent directory exists (though get_tree ORDER BY path ASC usually means it's already there)
                local_path.parent.mkdir(parents=True, exist_ok=True)
                if not self.tg._run_async(self._download_chunked(d_item["path"], str(local_path), progress)):
                    success = False
        
        if success:
            print(f"Recursive download of '{item['name']}' completed.")
        return success

    async def _download_chunked(self, remote_path_str: str, local_dest: Optional[str] = None, progress=True) -> bool:
        full_remote = self._resolve_path(remote_path_str)
        item = self.storage.get_item(full_remote)
        if not item or item["type"] != "file":
            print(f"Error: '{remote_path_str}' not found or is a directory.")
            return False

        session_id = item["session_id"]
        # FIX: if local_dest is a directory, place file inside it
        local_path: Path
        if local_dest:
            ld = Path(local_dest)
            if ld.is_dir():
                local_path = ld / item["name"]
            else:
                local_path = ld
        else:
            local_path = Path.cwd() / item["name"]

        if not session_id:
            msg_id = item["message_id"]
            if not msg_id:
                print(f"Error: '{remote_path_str}' has no storage information.")
                return False
            d_item = dict(item)
            print(f"Downloading legacy format file: {d_item['name']}...")
            try:
                if d_item["encrypted"]:
                    data = await self.tg._download_bytes(msg_id)
                    global_key = get_encryption_key()
                    if global_key:
                        f = Fernet(global_key)
                        data = f.decrypt(data)
                    else:
                        print("Error: Legacy encryption key missing.")
                        return False
                    with open(local_path, "wb") as f_out:
                        f_out.write(data)
                else:
                    await self.tg._download_file(msg_id, d_item.get("peer_id", "me"), local_path)
                print(f"Downloaded to: {local_path}")
                return True
            except Exception as e:
                if "not found" in str(e).lower() or "no media" in str(e).lower():
                    print(f"\n[bold red]Error:[/] Remote message {msg_id} was not found on Telegram for legacy file '{d_item['name']}'.")
                    print("[dim]The file has likely been deleted from your 'Saved Messages' chat and is no longer recoverable.[/]")
                else:
                    print(f"Error downloading legacy file: {e}")
                return False

        enc_key = item["encryption_key"]
        aesgcm = AESGCM(enc_key)
        chunks = self.storage.get_chunks(session_id)

        cur = self.storage.conn.execute(
            "SELECT chunk_size FROM upload_sessions WHERE id = ?", (session_id,)
        )
        row = cur.fetchone()
        chunk_size = row["chunk_size"]

        semaphore = asyncio.Semaphore(self.max_concurrent)
        pbar = None
        if progress:
            pbar = tqdm(total=len(chunks), unit='chunk', desc=f"Downloading {item['name']}")

        # Pre-allocate file
        with open(local_path, "wb") as f:
            f.truncate(item["size"])

        failed_chunks = []

        # FIX: retry download chunks like upload does
        async def download_worker(chunk_info):
            async with semaphore:
                idx = chunk_info["chunk_index"]
                msg_id = chunk_info["message_id"]
                for attempt in range(MAX_CHUNK_RETRIES):
                    try:
                        data = await self.tg._download_bytes(msg_id)
                        nonce = data[:12]
                        ciphertext = data[12:]
                        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
                        offset = idx * chunk_size
                        with open(local_path, "r+b") as f:
                            f.seek(offset)
                            f.write(plaintext)
                        if pbar:
                            pbar.update(1)
                        return True
                    except Exception as e:
                        if attempt < MAX_CHUNK_RETRIES - 1:
                            await asyncio.sleep(2 ** attempt)
                        else:
                            print(f"\nDownload chunk {idx} failed after {MAX_CHUNK_RETRIES} attempts: {e}")
                            failed_chunks.append(idx)
                            return False

        tasks = [download_worker(c) for c in chunks]
        try:
            results = await asyncio.gather(*tasks)
        except Exception as e:
            if "not found" in str(e).lower() or "no media" in str(e).lower():
                print(f"\n[bold red]Error:[/] One or more chunks for '{item['name']}' are missing from Telegram.")
                print("[dim]The remote data has likely been deleted and the file is corrupted.[/]")
            else:
                print(f"Download failed: {e}")
            return False

        if pbar:
            pbar.close()

        if all(results):
            print(f"Downloaded to: {local_path}")
            return True
        else:
            try:
                local_path.unlink()
            except OSError:
                pass
            print(f"Download failed (chunks {failed_chunks}). Partial file removed.")
            return False

    async def verify_item(self, remote_path_str: str) -> Dict[str, Any]:
        """
        Check if the remote data for an item (file or folder) is still accessible.
        Returns a status dict with 'healthy' (bool), 'reason' (str), and 'type' ('file'/'folder').
        """
        item = self.storage.get_item(remote_path_str)
        if not item:
            return {"healthy": False, "reason": "Not found in local database", "type": "unknown"}
        
        if item["type"] == "folder":
            return {"healthy": True, "reason": "Folder metadata exists", "type": "folder"}

        try:
            if not item.get("session_id"):
                msg_id = item["message_id"]
                if not msg_id:
                    return {"healthy": False, "reason": "No message ID stored", "type": "file"}
                
                msg = await self.tg.client.get_messages(item.get("peer_id", "me"), ids=msg_id)
                if not msg or not msg.media:
                    return {"healthy": False, "reason": "Remote message missing or has no media", "type": "file"}
            else:
                chunks = self.storage.get_chunks(item["session_id"])
                if not chunks:
                    return {"healthy": False, "reason": "No chunks found in database", "type": "file"}
                first_chunk = chunks[0]
                msg = await self.tg.client.get_messages(first_chunk.get("peer_id", "me"), ids=first_chunk["message_id"])
                if not msg or not msg.media:
                    return {"healthy": False, "reason": f"Chunk {first_chunk['chunk_index']} missing from Telegram", "type": "file"}
            
            return {"healthy": True, "reason": "Remote data accessible", "type": "file"}
        except Exception as e:
            return {"healthy": False, "reason": str(e), "type": "file"}

    def purge(self) -> bool:
        """Wipe ALL remote data and local metadata."""
        # 1. Collect all message IDs
        msg_ids = []
        
        # From chunks
        cur_chunks = self.storage.conn.execute("SELECT message_id FROM chunks WHERE message_id IS NOT NULL")
        msg_ids.extend([r[0] for r in cur_chunks.fetchall()])
        
        # From legacy items
        cur_items = self.storage.conn.execute("SELECT message_id FROM items WHERE message_id IS NOT NULL")
        msg_ids.extend([r[0] for r in cur_items.fetchall()])
        
        # 2. Delete from Telegram
        if msg_ids:
            # Remove duplicates
            msg_ids = list(set(msg_ids))
            print(f"Purging {len(msg_ids)} messages from Telegram...")
            self.tg._run_async(self._batch_delete_async(msg_ids))
            
        # 3. Wipe metadata
        self.storage.wipe_all_metadata()
        
        # 4. Reset CWD
        self.cwd = "/"
        
        return True

    def rm(self, path: str, recursive: bool = False, force: bool = False) -> bool:
        full = self._resolve_path(path)
        if not self.storage.exists(full):
            if not force:
                print(f"rm: cannot remove '{path}': No such file or directory")
            return force

        if self.storage.is_folder(full):
            if not recursive:
                print(f"rm: cannot remove '{path}': Is a directory")
                return False

            tree = self.storage.get_tree(full)
            msg_ids_to_delete = []
            for t_item in tree:
                actual_item = self.storage.get_item(t_item["path"])
                if not actual_item:
                    continue
                d_item = dict(actual_item)
                if d_item["type"] == "file":
                    if d_item.get("session_id") and self.storage.get_session_usage_count(d_item["session_id"]) <= 1:
                        chunks = self.storage.get_chunks(d_item["session_id"])
                        msg_ids_to_delete.extend([c["message_id"] for c in chunks if c["message_id"]])
                    elif d_item.get("message_id"):
                        msg_ids_to_delete.append(d_item["message_id"])

            self.storage.delete_recursive(full)
            if msg_ids_to_delete:
                self.tg._run_async(self._batch_delete_async(msg_ids_to_delete))
            if not force:
                print(f"Removed folder: {path}")

            if self.cwd == full or self.cwd.startswith(full + "/"):
                self.cwd = "/"
                save_cwd(self.cwd)
            return True
        else:
            actual_item = self.storage.get_item(full)
            if not actual_item:
                return False
            d_item = dict(actual_item)
            msg_ids_to_delete = []
            if d_item.get("session_id") and self.storage.get_session_usage_count(d_item["session_id"]) <= 1:
                chunks = self.storage.get_chunks(d_item["session_id"])
                msg_ids_to_delete = [c["message_id"] for c in chunks if c["message_id"]]
            elif d_item.get("message_id"):
                msg_ids_to_delete = [d_item["message_id"]]
            
            self.storage.delete_item(full)
            if msg_ids_to_delete:
                self.tg._run_async(self._batch_delete_async(msg_ids_to_delete))
            if not force:
                print(f"Removed file: {path}")
            return True

    async def _batch_delete_async(self, msg_ids: List[int]):
        try:
            for i in range(0, len(msg_ids), 100):
                await self.tg.delete_messages('me', msg_ids[i:i + 100])
        except Exception:
            pass

    def mv(self, old_path: str, new_path: str) -> bool:
        old_full = self._resolve_path(old_path)
        new_full = self._resolve_path(new_path)
        success = self.storage.rename_item(old_full, new_full)
        if success and (self.cwd == old_full or self.cwd.startswith(old_full + "/")):
            rel = self.cwd[len(old_full):].lstrip("/")
            self.cwd = self.storage.normalize_path(os.path.join(new_full, rel))
            save_cwd(self.cwd)
        return success

    def cp(self, old_path: str, new_path: str, recursive: bool = True) -> bool:
        old_full = self._resolve_path(old_path)
        new_full = self._resolve_path(new_path)
        return self.storage.copy_item(old_full, new_full, recursive)

    def get_completions(self, text: str) -> List[str]:
        if "/" in text:
            dir_part, prefix = text.rsplit("/", 1)
            dir_part = dir_part if dir_part else "/"
        else:
            dir_part, prefix = ".", text

        target = self._resolve_path(dir_part)
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
