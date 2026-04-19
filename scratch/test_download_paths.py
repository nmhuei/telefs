
import os
from pathlib import Path

def mock_download_directory(remote_path_str, local_dest=None):
    # Simplified version of the actual logic to test path translation
    full_remote = remote_path_str
    item_name = Path(full_remote).name
    
    local_base = Path(local_dest) if local_dest else Path.cwd()
    target_local_root = local_base / item_name
    
    print(f"DEBUG: target_local_root = {target_local_root}")
    
    # Mock some tree items
    items = [
        {"path": "/Photos/Vacation", "type": "folder"},
        {"path": "/Photos/Vacation/beach.jpg", "type": "file"},
        {"path": "/Photos/Vacation/sub/forest.png", "type": "file"},
    ]
    
    for d_item in items:
        if d_item["path"] == full_remote:
            continue
            
        rel_path = d_item["path"][len(full_remote):].lstrip("/")
        local_path = target_local_root / rel_path
        print(f"DEBUG: {d_item['path']} -> {local_path}")

print("--- Test Case 1: Download to CWD ---")
mock_download_directory("/Photos/Vacation", None)

print("\n--- Test Case 2: Download to specific folder ---")
mock_download_directory("/Photos/Vacation", "/home/light/Downloads/test_dest")
