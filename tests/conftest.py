import pytest
import sqlite3
import tempfile
import os
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path
from telefs.fs_manager import FSManager
from telefs.storage import Storage

@pytest.fixture
def mock_tg():
    client = MagicMock()
    client.connect.return_value = True
    client.is_connected.return_value = True
    
    # Public methods
    client.upload_file = AsyncMock(return_value=(123456, "me", "doc_id_test"))
    client.delete_messages = AsyncMock(return_value=True)
    
    # Internal methods used by FSManager
    client._upload_bytes = AsyncMock(return_value=777) # returns a message_id
    client._download_bytes = AsyncMock(return_value=b"\x00"*24) # dummy encrypted data
    
    # Bridge mock
    client._run_async = lambda coro: coro # Simplification: just return the coro so caller can await it
    return client

@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    yield db_path
    if os.path.exists(db_path):
        os.unlink(db_path)

@pytest.fixture
def fs_manager(temp_db, mock_tg):
    with patch("telefs.fs_manager.TelegramFSClient", return_value=mock_tg):
        # We need to make sure FSManager uses the mock client we provided
        manager = FSManager(db_path=temp_db)
        manager.tg = mock_tg
        return manager
