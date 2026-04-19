import pytest
import asyncio
from unittest.mock import MagicMock

def test_graceful_disconnect(fs_manager):
    # Setup some dummy background tasks to simulate active connection
    loop = asyncio.get_event_loop()
    
    # Mock the telegram client disconnect to ensure it doesn't crash
    fs_manager.tg.disconnect = MagicMock()
    
    # Run disconnect
    # In one_shot mode, disconnect is called at the end
    try:
        fs_manager.disconnect()
        # If it reaches here without raising CancelledError, it's a win
        assert True
    except Exception as e:
        pytest.fail(f"Disconnect raised an unexpected exception: {e}")

def test_storage_wal_mode(fs_manager):
    # Verify WAL mode and timeout are set correctly
    conn = fs_manager.storage.conn
    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    
    assert timeout == 30000 # 30s in ms
    assert journal_mode.lower() == "wal"
