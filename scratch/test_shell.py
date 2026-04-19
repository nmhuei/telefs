import os
import sys
import shutil
from pathlib import Path
from unittest.mock import MagicMock

# Add project root to sys.path
sys.path.append(os.getcwd())

# Mock Telegram BEFORE importing anything that might use it
from telefs import telegram_client
telegram_client.TelegramFSClient = MagicMock()

from telefs.cli import TeleFSShell
from rich.console import Console

def run_simulation():
    console = Console()
    console.print("\n[bold cyan]=== TeleFS Command Suite Simulation ===[/]\n")
    
    # Initialize shell with a fresh test database
    db_path = "sim_test.db"
    if os.path.exists(db_path): os.remove(db_path)
    
    # We need to mock the connection part so it doesn't fail
    shell = TeleFSShell()
    shell.fs.storage.db_path = db_path
    
    # Helper to run a command as if typed by user
    def cmd(line):
        console.print(f"\n[bold yellow]telefs {shell.fs.cwd} ❯[/] {line}")
        shell.onecmd(line)

    # 1. Setup structure
    console.print("[bold blue]Step 1: Creating directory structure[/]")
    cmd("mkdir -p /Documents/Work")
    cmd("mkdir -p /Photos/Vacation")
    
    # 2. Add some dummy entries
    # Manually adding files since upload requires local files
    shell.fs.storage.add_file("/Documents/todo.txt", 1, "me", 1024)
    shell.fs.storage.add_file("/Documents/Work/notes.pdf", 2, "me", 5*1024*1024)
    shell.fs.storage.add_file("/Photos/Vacation/beach.jpg", 3, "me", 2*1024*1024)
    
    # 3. Test LS and TREE (The primary bug fix)
    console.print("\n[bold blue]Step 2: Verifying LS and TREE consistency[/]")
    cmd("ls /")
    cmd("tree /")
    
    # 4. Test Recursive Move
    console.print("\n[bold blue]Step 3: Recursive Move[/]")
    cmd("mkdir /Archive")
    cmd("mv /Documents /Archive/Docs")
    cmd("ls /Archive/Docs")
    cmd("tree /Archive")
    
    # 5. Test Recursive Copy
    console.print("\n[bold blue]Step 4: Recursive Copy[/]")
    cmd("cp -r /Archive/Docs /Archive/Docs_Backup")
    cmd("tree /Archive")
    
    # 6. Test Removal
    console.print("\n[bold blue]Step 5: Recursive Removal[/]")
    cmd("rm -rf /Archive/Docs")
    cmd("tree /Archive")
    
    console.print("\n[bold green]Simulation Complete![/]")
    
    # Cleanup
    if os.path.exists(db_path): os.remove(db_path)

if __name__ == "__main__":
    try:
        run_simulation()
    except Exception as e:
        print(f"Simulation failed: {e}")
        import traceback
        traceback.print_exc()
