"""SQLite metadata storage for the virtual filesystem."""
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Dict

from .config import get_config_dir

DB_PATH = get_config_dir() / "metadata.db"


class Storage:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(DB_PATH)
        self.conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,  # FIX: allow use across threads (async workers)
        )
        self.conn.row_factory = sqlite3.Row
        # FIX: enable WAL mode for better concurrency and crash safety
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_db()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _init_db(self):
        """Create tables if they don't exist and handle migrations."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            )
        """)
        
        cur = self.conn.execute("SELECT version FROM schema_version")
        row = cur.fetchone()
        version = row["version"] if row else 0

        if version == 0:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    parent_path TEXT,
                    type TEXT CHECK(type IN ('file','folder')) NOT NULL,
                    telegram_file_id TEXT,
                    message_id INTEGER,
                    peer_id TEXT,
                    size INTEGER DEFAULT 0,
                    encrypted BOOLEAN DEFAULT 0,
                    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_path ON items(path)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_parent ON items(parent_path)")
            self.conn.execute("""
                INSERT OR IGNORE INTO items (name, path, parent_path, type)
                VALUES ('', '/', NULL, 'folder')
            """)
            self.conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (1)")
            self.conn.commit()
            version = 1

        if version == 1:
            try:
                self.conn.execute("ALTER TABLE items ADD COLUMN session_id INTEGER")
                self.conn.execute("ALTER TABLE items ADD COLUMN file_hash TEXT")
                self.conn.execute("ALTER TABLE items ADD COLUMN encryption_key BLOB")
            except sqlite3.OperationalError:
                pass
            
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS upload_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    total_chunks INTEGER NOT NULL,
                    chunk_size INTEGER NOT NULL,
                    encryption_key BLOB,
                    file_hash TEXT,
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    session_id INTEGER REFERENCES upload_sessions(id),
                    chunk_index INTEGER NOT NULL,
                    message_id INTEGER,
                    peer_id TEXT DEFAULT 'me',
                    status TEXT DEFAULT 'pending',
                    PRIMARY KEY (session_id, chunk_index)
                )
            """)
            self.conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (2)")
            self.conn.commit()
            version = 2

        if version == 2:
            try:
                self.conn.execute("ALTER TABLE items ADD COLUMN updated_at TIMESTAMP")
            except sqlite3.OperationalError:
                pass
            self.conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (3)")
            self.conn.commit()
            version = 3

        if version == 3:
            # Migration to version 4: index on file_hash + retry_count on chunks
            try:
                self.conn.execute("CREATE INDEX IF NOT EXISTS idx_file_hash ON items(file_hash)")
                self.conn.execute("ALTER TABLE chunks ADD COLUMN retry_count INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            self.conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (4)")
            self.conn.commit()
            version = 4
            
        # Ensure updated_at column exists in case of previous migration skips
        try:
            self.conn.execute("ALTER TABLE items ADD COLUMN updated_at TIMESTAMP")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass

    def normalize_path(self, path: str) -> str:
        """Normalize a path to start with '/' and remove trailing slash (except root)."""
        if not path:
            return "/"
        p = Path("/", path)
        normalized = str(p.as_posix())
        if not normalized.startswith("/"):
            normalized = "/" + normalized
        return normalized

    def exists(self, path: str) -> bool:
        path = self.normalize_path(path)
        cur = self.conn.execute("SELECT 1 FROM items WHERE path = ?", (path,))
        return cur.fetchone() is not None

    def is_folder(self, path: str) -> bool:
        path = self.normalize_path(path)
        cur = self.conn.execute("SELECT type FROM items WHERE path = ?", (path,))
        row = cur.fetchone()
        return row and row["type"] == "folder"

    def get_item(self, path: str) -> Optional[sqlite3.Row]:
        path = self.normalize_path(path)
        cur = self.conn.execute("SELECT * FROM items WHERE path = ?", (path,))
        return cur.fetchone()

    def find_file_by_hash(self, file_hash: str) -> Optional[sqlite3.Row]:
        cur = self.conn.execute("""
            SELECT * FROM items 
            WHERE file_hash = ? AND session_id IS NOT NULL 
            LIMIT 1
        """, (file_hash,))
        return cur.fetchone()

    def get_session_usage_count(self, session_id: int) -> int:
        cur = self.conn.execute("SELECT COUNT(*) as count FROM items WHERE session_id = ?", (session_id,))
        row = cur.fetchone()
        return row["count"] if row else 0

    def list_folder(self, path: str, include_hidden: bool = False) -> List[sqlite3.Row]:
        path = self.normalize_path(path)
        query = "SELECT * FROM items WHERE parent_path = ?"
        params = [path]
        if not include_hidden:
            query += " AND name NOT LIKE '.%'"
        query += " ORDER BY type DESC, name ASC"
        cur = self.conn.execute(query, params)
        return cur.fetchall()

    def create_folder(self, path: str, parents: bool = False) -> bool:
        """Create a new folder. Return True if created, False if exists or error."""
        path = self.normalize_path(path)
        if self.exists(path):
            return parents
        if path == "/":
            return False
            
        if parents:
            parts = [p for p in path.split("/") if p]
            current = ""
            for p in parts:
                current += "/" + p
                if not self.exists(current):
                    self.create_folder(current, parents=False)
            return True

        p_obj = Path(path)
        parent = str(p_obj.parent)
        name = p_obj.name
        parent_path = self.normalize_path(parent)

        # FIX: prevent orphan folders when parent does not exist
        if not self.exists(parent_path):
            return False
        
        try:
            self.conn.execute("""
                INSERT INTO items (name, path, parent_path, type)
                VALUES (?, ?, ?, 'folder')
            """, (name, path, parent_path))
            self.conn.commit()
            return True
        except sqlite3.Error:
            return False

    def add_file(self, path: str, msg_id: Optional[int], peer_id: str, size: int = 0, 
                 encrypted: bool = False, file_id: str = None, session_id: int = None,
                 file_hash: str = None, encryption_key: bytes = None) -> bool:
        """Add a file entry. Returns True if added, False if exists."""
        path = self.normalize_path(path)
        if self.exists(path):
            return False
        p_obj = Path(path)
        parent = str(p_obj.parent)
        name = p_obj.name
        parent_path = self.normalize_path(parent)
        self.conn.execute("""
            INSERT INTO items (name, path, parent_path, type, message_id, peer_id, size, 
                               encrypted, telegram_file_id, session_id, file_hash, encryption_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, path, parent_path, 'file', msg_id, peer_id, size, 
              encrypted, file_id, session_id, file_hash, encryption_key))
        self.conn.commit()
        return True

    def create_upload_session(self, path: str, total_chunks: int, chunk_size: int, 
                               encryption_key: bytes, file_hash: str = None) -> int:
        cur = self.conn.execute("""
            INSERT INTO upload_sessions (file_path, total_chunks, chunk_size, encryption_key, file_hash)
            VALUES (?, ?, ?, ?, ?)
        """, (path, total_chunks, chunk_size, encryption_key, file_hash))
        session_id = cur.lastrowid
        for i in range(total_chunks):
            self.conn.execute("""
                INSERT INTO chunks (session_id, chunk_index, status)
                VALUES (?, ?, 'pending')
            """, (session_id, i))
        self.conn.commit()
        return session_id

    def get_active_session(self, path: str) -> Optional[sqlite3.Row]:
        cur = self.conn.execute("""
            SELECT * FROM upload_sessions 
            WHERE file_path = ? AND status = 'active'
            ORDER BY created_at DESC LIMIT 1
        """, (path,))
        return cur.fetchone()

    def update_chunk(self, session_id: int, chunk_index: int, message_id: int, status: str = 'done'):
        self.conn.execute("""
            UPDATE chunks SET message_id = ?, status = ?
            WHERE session_id = ? AND chunk_index = ?
        """, (message_id, status, session_id, chunk_index))
        self.conn.commit()

    def increment_chunk_retry(self, session_id: int, chunk_index: int):
        """Increment retry counter for a chunk."""
        self.conn.execute("""
            UPDATE chunks SET retry_count = COALESCE(retry_count, 0) + 1
            WHERE session_id = ? AND chunk_index = ?
        """, (session_id, chunk_index))
        self.conn.commit()

    def get_chunks(self, session_id: int) -> List[sqlite3.Row]:
        cur = self.conn.execute("""
            SELECT * FROM chunks WHERE session_id = ? ORDER BY chunk_index ASC
        """, (session_id,))
        return cur.fetchall()

    def finalize_session(self, session_id: int):
        self.conn.execute("""
            UPDATE upload_sessions SET status = 'completed' WHERE id = ?
        """, (session_id,))
        self.conn.commit()

    def abandon_session(self, session_id: int):
        """Mark session as failed so it won't be picked up as a resume candidate."""
        self.conn.execute("""
            UPDATE upload_sessions SET status = 'failed' WHERE id = ?
        """, (session_id,))
        self.conn.commit()

    def delete_item(self, path: str) -> bool:
        path = self.normalize_path(path)
        if path == "/":
            return False
        if self.is_folder(path):
            children = self.list_folder(path)
            if children:
                return False
        self.conn.execute("DELETE FROM items WHERE path = ?", (path,))
        self.conn.commit()
        return True

    def delete_recursive(self, path: str) -> int:
        path = self.normalize_path(path)
        if path == "/":
            return 0
        escaped_path = path.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        cur = self.conn.execute("""
            SELECT path FROM items
            WHERE path = ? OR path LIKE ? || '/%' ESCAPE '\\'
        """, (path, escaped_path))
        paths = [row["path"] for row in cur.fetchall()]
        for p in paths:
            self.conn.execute("DELETE FROM items WHERE path = ?", (p,))
        self.conn.commit()
        return len(paths)

    def rename_item(self, old_path: str, new_path: str) -> bool:
        """Rename a file or folder. Moves descendants if folder."""
        old_path = self.normalize_path(old_path)
        new_path = self.normalize_path(new_path)
        
        if old_path == "/" or new_path == "/":
            return False
        if new_path.startswith(old_path + "/") or new_path == old_path:
            return False
        if self.get_item(new_path):
            return False

        # FIX: verify the destination parent exists before moving
        new_parent = self.normalize_path(str(Path(new_path).parent))
        if not self.exists(new_parent):
            return False
            
        if self.is_folder(old_path):
            escaped_old = old_path.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            cur = self.conn.execute(
                "SELECT id, path FROM items WHERE path = ? OR path LIKE ? || '/%' ESCAPE '\\'",
                (old_path, escaped_old)
            )
            for row in cur.fetchall():
                pid = row["id"]
                p_old = row["path"]
                rel = p_old[len(old_path):].lstrip("/")
                p_new = self.normalize_path(os.path.join(new_path, rel)) if rel else new_path
                p_obj = Path(p_new)
                parent_desc = self.normalize_path(str(p_obj.parent))
                self.conn.execute(
                    "UPDATE items SET path = ?, parent_path = ?, name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (p_new, parent_desc, p_obj.name, pid)
                )
        else:
            p_obj = Path(new_path)
            parent_desc = self.normalize_path(str(p_obj.parent))
            self.conn.execute(
                "UPDATE items SET path = ?, parent_path = ?, name = ?, updated_at = CURRENT_TIMESTAMP WHERE path = ?",
                (new_path, parent_desc, p_obj.name, old_path)
            )
        self.conn.commit()
        return True

    def get_tree(self, root_path: str = "/", max_level: Optional[int] = None,
                 include_hidden: bool = False, dirs_only: bool = False) -> List[Dict]:
        root_path = self.normalize_path(root_path)
        where_clauses = []
        params = []
        if root_path != "/":
            escaped_root = root_path.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            where_clauses.append("(path = ? OR path LIKE ? || '/%' ESCAPE '\\')")
            params.extend([root_path, escaped_root])
        if not include_hidden:
            where_clauses.append("name NOT LIKE '.%'")
        if dirs_only:
            where_clauses.append("type = 'folder'")
        query = "SELECT path, type, name, size FROM items"
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += " ORDER BY path ASC"
        cur = self.conn.execute(query, params)
        items = []
        for row in cur.fetchall():
            path = row["path"]
            if path == root_path:
                level = 0
            else:
                rel = path[len(root_path):].lstrip("/")
                level = rel.count("/") + (1 if rel else 0)
            if max_level is not None and level > max_level:
                continue
            items.append({
                "path": path, "type": row["type"],
                "name": row["name"], "size": row["size"], "level": level
            })
        return items

    def copy_item(self, old_path: str, new_path: str, recursive: bool = True) -> bool:
        old_path = self.normalize_path(old_path)
        new_path = self.normalize_path(new_path)
        if old_path == "/" or new_path == "/":
            return False
        if new_path.startswith(old_path + "/") or new_path == old_path:
            return False
        item = self.get_item(old_path)
        if not item:
            return False
        if self.get_item(new_path):
            return False

        # FIX: verify destination parent exists
        new_parent = self.normalize_path(str(Path(new_path).parent))
        if not self.exists(new_parent):
            return False
            
        if item["type"] == "file":
            p_obj = Path(new_path)
            parent_path = self.normalize_path(str(p_obj.parent))
            self.conn.execute("""
                INSERT INTO items (name, path, parent_path, type, telegram_file_id, message_id, 
                                   peer_id, size, encrypted, session_id, file_hash, encryption_key)
                SELECT ?, ?, ?, type, telegram_file_id, message_id, 
                       peer_id, size, encrypted, session_id, file_hash, encryption_key
                FROM items WHERE path = ?
            """, (p_obj.name, new_path, parent_path, old_path))
            self.conn.commit()
            return True
        elif recursive:
            self.create_folder(new_path)
            escaped_old = old_path.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            cur = self.conn.execute("""
                SELECT * FROM items 
                WHERE path LIKE ? || '/%' ESCAPE '\\' 
                ORDER BY path ASC
            """, (escaped_old,))
            descendants = cur.fetchall()
            for desc in descendants:
                rel_path = desc["path"][len(old_path):].lstrip("/")
                desc_new_path = self.normalize_path(os.path.join(new_path, rel_path))
                p_desc = Path(desc_new_path)
                parent_desc = self.normalize_path(str(p_desc.parent))
                cols = [c for c in desc.keys() if c not in ('id', 'path', 'parent_path', 'name', 'upload_date', 'updated_at')]
                col_names = ", ".join(cols)
                placeholders = ", ".join(["?"] * len(cols))
                vals = [desc[c] for c in cols]
                self.conn.execute(f"""
                    INSERT INTO items (name, path, parent_path, {col_names})
                    VALUES (?, ?, ?, {placeholders})
                """, (p_desc.name, desc_new_path, parent_desc, *vals))
            self.conn.commit()
            return True
        return False

    def find_items(self, pattern: str, root_path: str = "/", item_type: Optional[str] = None) -> List[sqlite3.Row]:
        """Find items matching a glob pattern (supporting * and ?)."""
        # FIX: escape SQL wildcards in pattern before translating glob chars
        sql_pattern = pattern.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        sql_pattern = sql_pattern.replace('?', '_').replace('*', '%')
        query = "SELECT * FROM items WHERE name LIKE ? ESCAPE '\\'"
        params = [sql_pattern]
        if root_path != "/":
            escaped_root = root_path.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            query += " AND (path = ? OR path LIKE ? || '/%' ESCAPE '\\')"
            params.extend([root_path, escaped_root])
        if item_type:
            query += " AND type = ?"
            params.append(item_type)
        cur = self.conn.execute(query, params)
        return cur.fetchall()

    def close(self):
        if hasattr(self, 'conn') and self.conn:
            self.conn.close()
            self.conn = None
