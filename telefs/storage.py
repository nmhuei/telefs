"""SQLite metadata storage for the virtual filesystem."""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Dict

from .config import get_config_dir

DB_PATH = get_config_dir() / "metadata.db"


class Storage:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(DB_PATH)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
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
        
        # Check current version
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
                    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_path ON items(path)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_parent ON items(parent_path)")
            
            # Ensure root folder exists
            self.conn.execute("""
                INSERT OR IGNORE INTO items (name, path, parent_path, type)
                VALUES ('', '/', NULL, 'folder')
            """)
            
            self.conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (1)")
            self.conn.commit()
            version = 1

        if version == 1:
            # Migration to version 2
            try:
                self.conn.execute("ALTER TABLE items ADD COLUMN session_id INTEGER")
                self.conn.execute("ALTER TABLE items ADD COLUMN file_hash TEXT")
                self.conn.execute("ALTER TABLE items ADD COLUMN encryption_key BLOB")
            except sqlite3.OperationalError:
                # Columns might already exist if migration partially failed
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
        """Check if an item exists at the given path."""
        path = self.normalize_path(path)
        cur = self.conn.execute("SELECT 1 FROM items WHERE path = ?", (path,))
        return cur.fetchone() is not None

    def is_folder(self, path: str) -> bool:
        """Check if path exists and is a folder."""
        path = self.normalize_path(path)
        cur = self.conn.execute("SELECT type FROM items WHERE path = ?", (path,))
        row = cur.fetchone()
        return row and row["type"] == "folder"

    def get_item(self, path: str) -> Optional[sqlite3.Row]:
        """Return item at path or None."""
        path = self.normalize_path(path)
        cur = self.conn.execute("SELECT * FROM items WHERE path = ?", (path,))
        return cur.fetchone()

    def find_file_by_hash(self, file_hash: str) -> Optional[sqlite3.Row]:
        """Find an existing file entry with the same content hash."""
        cur = self.conn.execute("""
            SELECT * FROM items 
            WHERE file_hash = ? AND session_id IS NOT NULL 
            LIMIT 1
        """, (file_hash,))
        return cur.fetchone()

    def get_session_usage_count(self, session_id: int) -> int:
        """Count how many items are referring to this session_id."""
        cur = self.conn.execute("SELECT COUNT(*) as count FROM items WHERE session_id = ?", (session_id,))
        row = cur.fetchone()
        return row["count"] if row else 0

    def list_folder(self, path: str) -> List[sqlite3.Row]:
        """List all items in a folder."""
        path = self.normalize_path(path)
        cur = self.conn.execute("""
            SELECT * FROM items
            WHERE parent_path = ?
            ORDER BY type DESC, name ASC
        """, (path,))
        return cur.fetchall()

    def create_folder(self, path: str) -> bool:
        """Create a new folder. Return True if created, False if exists or error."""
        path = self.normalize_path(path)
        if self.exists(path):
            return False
        if path == "/":
            return False
            
        p_obj = Path(path)
        parent = str(p_obj.parent)
        name = p_obj.name
        
        parent_path = self.normalize_path(parent)
        
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
        """Create a new upload session."""
        cur = self.conn.execute("""
            INSERT INTO upload_sessions (file_path, total_chunks, chunk_size, encryption_key, file_hash)
            VALUES (?, ?, ?, ?, ?)
        """, (path, total_chunks, chunk_size, encryption_key, file_hash))
        
        session_id = cur.lastrowid
        
        # Initialize chunks
        for i in range(total_chunks):
            self.conn.execute("""
                INSERT INTO chunks (session_id, chunk_index, status)
                VALUES (?, ?, 'pending')
            """, (session_id, i))
            
        self.conn.commit()
        return session_id

    def get_active_session(self, path: str) -> Optional[sqlite3.Row]:
        """Get an active session for a path if it exists."""
        cur = self.conn.execute("""
            SELECT * FROM upload_sessions 
            WHERE file_path = ? AND status = 'active'
            ORDER BY created_at DESC LIMIT 1
        """, (path,))
        return cur.fetchone()

    def update_chunk(self, session_id: int, chunk_index: int, message_id: int, status: str = 'done'):
        """Update chunk status."""
        self.conn.execute("""
            UPDATE chunks SET message_id = ?, status = ?
            WHERE session_id = ? AND chunk_index = ?
        """, (message_id, status, session_id, chunk_index))
        self.conn.commit()

    def get_chunks(self, session_id: int) -> List[sqlite3.Row]:
        """Get all chunks for a session."""
        cur = self.conn.execute("""
            SELECT * FROM chunks WHERE session_id = ? ORDER BY chunk_index ASC
        """, (session_id,))
        return cur.fetchall()

    def finalize_session(self, session_id: int):
        """Mark session as completed."""
        self.conn.execute("""
            UPDATE upload_sessions SET status = 'completed' WHERE id = ?
        """, (session_id,))
        self.conn.commit()

    def delete_item(self, path: str) -> bool:
        """Delete a file or an empty folder. Return True if deleted."""
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
        """Delete a folder and all its contents. Return number of items deleted."""
        path = self.normalize_path(path)
        if path == "/":
            return 0
            
        # Get all descendants - escape special LIKE chars
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

    def get_tree(self, root_path: str = "/") -> List[Dict]:
        """Return a list of items for tree display."""
        root_path = self.normalize_path(root_path)
        
        if root_path == "/":
            query = "SELECT path, type FROM items ORDER BY path ASC"
            params = ()
        else:
            escaped_root = root_path.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            query = "SELECT path, type FROM items WHERE path = ? OR path LIKE ? || '/%' ESCAPE '\\' ORDER BY path ASC"
            params = (root_path, escaped_root)
            
        cur = self.conn.execute(query, params)
        
        items = []
        for row in cur.fetchall():
            path = row["path"]
            if path == root_path:
                level = 0
            else:
                rel = path[len(root_path):].lstrip("/")
                level = rel.count("/") + (1 if rel else 0)
            items.append({"path": path, "type": row["type"], "level": level})
        return items

    def close(self):
        if hasattr(self, 'conn') and self.conn:
            self.conn.close()
            self.conn = None
