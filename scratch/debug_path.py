import os
import sys
from pathlib import Path

print(f"DEBUG: sys.argv = {sys.argv}")
print(f"DEBUG: os.getcwd() = {os.getcwd()}")

local_path_str = "vt2.png"
local_path = Path(local_path_str).expanduser().resolve()
print(f"DEBUG: Resolving '{local_path_str}' -> '{local_path}'")
print(f"DEBUG: is_file() = {local_path.is_file()}")
print(f"DEBUG: is_dir() = {local_path.is_dir()}")
print(f"DEBUG: exists() = {local_path.exists()}")
