"""
TeleFS interactive shell and one-shot CLI.
"""
import argparse
import cmd
import functools
import glob
import os
import shlex
import sys
import tempfile
import subprocess
import sqlite3
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.table import Table
from rich.tree import Tree as RichTree
from rich.panel import Panel
from rich.columns import Columns
from rich import box as rich_box

from .fs_manager import FSManager
from .config import is_configured, load_config, save_config

# ---------------------------------------------------------------------------
# readline / history (optional — gracefully absent on some platforms)
# ---------------------------------------------------------------------------

try:
    import readline
    _READLINE = True
except ImportError:
    _READLINE = False

_HISTORY_FILE = Path.home() / ".config" / "telefs" / "shell_history"
_HISTORY_MAX  = 500


def _load_history():
    if _READLINE and _HISTORY_FILE.exists():
        try:
            readline.read_history_file(str(_HISTORY_FILE))
            readline.set_history_length(_HISTORY_MAX)
        except OSError:
            pass


def _save_history():
    if _READLINE:
        try:
            _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            readline.write_history_file(str(_HISTORY_FILE))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Shell exception guard — prevent a single command crash from killing the REPL
# ---------------------------------------------------------------------------

def _guard(method):
    """Decorator: catch any unhandled exception inside a do_* method."""
    @functools.wraps(method)
    def wrapper(self, arg):
        # Reset failure state at start of each command
        if hasattr(self, "_last_failed"):
            self._last_failed = False
        try:
            return method(self, arg)
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Operation cancelled by user.[/]")
            if hasattr(self, "_last_failed"):
                self._last_failed = True
        except ValueError as exc:
            if hasattr(self, "_last_failed"):
                self._last_failed = True
            self.console.print(f"[bold red]Syntax error:[/] {exc}")
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc).lower():
                self.console.print("[bold red]Error:[/] Database is locked by another process. Please wait or check if another telefs command is running.")
            else:
                self.console.print(f"[bold red]Database error:[/] {exc}")
        except Exception as exc:
            if hasattr(self, "_last_failed"):
                self._last_failed = True
            self.console.print(f"[bold red]Unexpected error:[/] {exc}")
    return wrapper


# ---------------------------------------------------------------------------
# Argument parsing helpers
# ---------------------------------------------------------------------------

def parse_linux_args(args_tokens: List[str]):
    """
    Parse tokens into (flags_dict, positional_list).
    flags_dict: char/word → value (True for bare flags, str for --key=val).
    Supports combined short flags like -rf and value flags like -l 3.
    """
    flags: dict = {}
    positional: list = []
    i = 0
    while i < len(args_tokens):
        token = args_tokens[i]
        if token == "--":
            positional.extend(args_tokens[i + 1:])
            break
        elif token.startswith("--"):
            key = token[2:]
            if "=" in key:
                k, v = key.split("=", 1)
                flags[k] = v
            else:
                flags[key] = True
        elif token.startswith("-") and len(token) > 1:
            for j, char in enumerate(token[1:]):
                # Last char may consume the next token as its value
                if j == len(token) - 2 and i + 1 < len(args_tokens) and not args_tokens[i + 1].startswith("-"):
                    # peek: only consume if this flag is known to take a value (l, n)
                    if char in ("l", "n"):
                        i += 1
                        flags[char] = args_tokens[i]
                        break
                flags[char] = True
        else:
            positional.append(token)
        i += 1
    return flags, positional


# ---------------------------------------------------------------------------
# Rich display helpers
# ---------------------------------------------------------------------------

_TYPE_ICON = {"folder": "📁", "file": "📄"}
_FILE_EXT_ICONS = {
    ".py": "🐍", ".js": "📜", ".ts": "📜", ".json": "🗂",
    ".zip": "🗜", ".tar": "🗜", ".gz": "🗜", ".7z": "🗜",
    ".mp4": "🎬", ".mkv": "🎬", ".avi": "🎬", ".mov": "🎬",
    ".mp3": "🎵", ".wav": "🎵", ".flac": "🎵",
    ".jpg": "🖼", ".jpeg": "🖼", ".png": "🖼", ".gif": "🖼", ".webp": "🖼",
    ".pdf": "📕", ".docx": "📝", ".xlsx": "📊", ".pptx": "📊",
    ".md": "📖", ".txt": "📄", ".log": "📋",
}


def _file_icon(name: str, item_type: str) -> str:
    if item_type == "folder":
        return "📁"
    ext = Path(name).suffix.lower()
    return _FILE_EXT_ICONS.get(ext, "📄")


def print_ls_table(console: Console, items: List, fs: FSManager,
                   long: bool = False, human: bool = True):
    """Render a rich table of files and folders."""
    if not items:
        console.print("[italic dim](empty)[/]")
        return

    table = Table(show_header=True, header_style="bold dim", box=rich_box.SIMPLE, padding=(0, 1))
    table.add_column("", width=3)           # icon
    table.add_column("Name", min_width=24, no_wrap=False)
    table.add_column("Size", justify="right", width=10)
    if long:
        table.add_column("Uploaded", width=20)
        table.add_column("Hash", width=10)
    table.add_column("", width=3)           # lock icon

    for item in items:
        is_dir = item["type"] == "folder"
        icon   = _file_icon(item["name"], item["type"])
        name   = f"[bold blue]{item['name']}[/]" if is_dir else f"[green]{item['name']}[/]"
        size   = "-" if is_dir else (fs._format_size(item["size"]) if human else str(item["size"]))
        lock   = "🔒" if item.get("encrypted") else ""
        row    = [icon, name, size]
        if long:
            d = dict(item)
            ts   = str(d.get("upload_date", "-") or "-")[:16]
            h    = (d.get("file_hash") or "")[:8]
            hash_str = f"[dim]{h}…[/]" if h else "-"
            row += [ts, hash_str]
        row.append(lock)
        table.add_row(*row)

    console.print(table)
    # Summary line
    dirs  = sum(1 for i in items if i["type"] == "folder")
    files = sum(1 for i in items if i["type"] == "file")
    parts = []
    if dirs:  parts.append(f"[bold blue]{dirs}[/] director{'y' if dirs == 1 else 'ies'}")
    if files: parts.append(f"[green]{files}[/] file{'s' if files != 1 else ''}")
    if parts:
        console.print("[dim]" + ",  ".join(parts) + "[/]")


def print_info_table(console: Console, info: dict):
    """Render a detailed metadata table for one item."""
    table = Table(show_header=False, box=rich_box.SIMPLE, padding=(0, 1))
    table.add_column("Key", style="bold cyan", width=14)
    table.add_column("Value")

    table.add_row("Name",      info["name"])
    table.add_row("Path",      info["path"])
    table.add_row("Type",      info["type"].upper())
    table.add_row("Size",      f"{info['size_h']}  [dim]({info['size']} bytes)[/]")
    table.add_row("Uploaded",  str(info.get("upload_date") or "-"))
    table.add_row("Modified",  str(info.get("updated_at") or "-"))
    table.add_row("Encrypted", "🔒 Yes" if info.get("encrypted") else "No")

    if info["type"] == "file":
        if info.get("file_hash"):
            table.add_row("SHA-256", info["file_hash"])
        if info.get("session_id"):
            table.add_row("Session", str(info["session_id"]))
        if info.get("message_id"):
            table.add_row("Msg ID", str(info["message_id"]))

    console.print(Panel(table, title=f"[bold]{info['name']}[/]", border_style="blue"))


def print_tree_view(console: Console, items: List, show_size=False,
                    human_readable=True, fs=None):
    """Render a rich tree of the filesystem."""
    if not items:
        console.print("[bold blue]📁 /")
        return

    root_item = items[0]
    root_path = root_item["path"]

    def get_display_name(item):
        name = item.get("name") or Path(item["path"]).name or "/"
        icon  = _file_icon(name, item["type"])
        style = "bold blue" if item["type"] == "folder" else "green"
        display = f"{icon} [{style}]{name}[/]"
        if show_size and item["type"] == "file":
            sz = item.get("size", 0)
            sz_str = fs._format_size(sz) if (human_readable and fs) else str(sz)
            display += f" [dim]({sz_str})[/]"
        return display

    tree_map = {root_path: RichTree(get_display_name(root_item))}
    d_count = f_count = 0

    for item in items:
        if item["type"] == "folder":
            d_count += 1
        else:
            f_count += 1
            
        if item["path"] == root_path:
            continue
            
        # Use parent_path from database if available, else derive from string
        parent_path = item.get("parent_path")
        if not parent_path:
            p_obj = Path(item["path"])
            parent_path = str(p_obj.parent).replace("\\", "/")
            if parent_path == ".":
                parent_path = "/"
        
        parent_node = tree_map.get(parent_path)
        if not parent_node:
            # Fallback for deep structures where parent might be missing in result set
            p_obj = Path(item["path"])
            for p in p_obj.parents:
                ps = str(p).replace("\\", "/")
                if ps in tree_map:
                    parent_node = tree_map[ps]
                    break
            if not parent_node:
                parent_node = tree_map[root_path]
        
        tree_map[item["path"]] = parent_node.add(get_display_name(item))

    console.print(tree_map[root_path])
    dir_summary = d_count - 1 if root_item["type"] == "folder" else d_count
    console.print(
        f"\n[dim]{max(0, dir_summary)} directories, {f_count} files[/]"
    )


def print_status(console: Console, fs: FSManager):
    """Render the status panel."""
    status = fs.get_status()
    stats  = status["db_stats"]

    cfg_ok  = status["api_configured"]
    sess_ok = status["session_exists"]

    rows = [
        ("API Config",       "[green]OK[/]"          if cfg_ok  else "[red]Missing — run 'login'[/]"),
        ("Phone",            status["phone"] or "[yellow]Not set[/]"),
        ("Session",          "[green]Logged in[/]"   if sess_ok else "[yellow]No session — run 'login'[/]"),
        ("Folders",          str(stats["folders"])),
        ("Files",            str(stats["files"])),
        ("Total size",       fs._format_size(stats["total_size"])),
    ]

    table = Table(show_header=False, box=rich_box.SIMPLE, padding=(0, 1))
    table.add_column("Key", style="bold cyan", width=14)
    table.add_column("Value")
    for k, v in rows:
        table.add_row(k, v)

    console.print(Panel(table, title="[bold blue]TeleFS Status[/]", border_style="blue"))


# ---------------------------------------------------------------------------
# Interactive shell
# ---------------------------------------------------------------------------

_INTRO_BANNER = """
[bold blue]╔══════════════════════════════════╗[/]
[bold blue]║[/]  [bold white]TeleFS[/] [dim]— Telegram Filesystem[/]   [bold blue]║[/]
[bold blue]╚══════════════════════════════════╝[/]
Type [bold cyan]help[/] for commands or [bold cyan]?cmd[/] for per-command help.
"""


class TeleFSShell(cmd.Cmd):
    """Interactive TeleFS REPL."""

    # prompt is a property so it updates after every cd
    @property
    def prompt(self):
        cwd   = self.fs.cwd
        mark  = "[✗]" if self._last_failed else "❯"
        # Use plain string for readline compatibility
        return f"telefs {cwd} {mark} "

    # ---------------------------------------------------------------- init --

    def __init__(self):
        super().__init__()
        self.fs          = FSManager()
        self.console     = Console()
        self._last_failed = False   # track last command result for prompt
        self._prev_cwd   = "/"     # for `cd -`
        self._aliases: dict = {}    # user-defined aliases

        _load_history()

        try:
            self.fs.connect()
        except Exception as exc:
            if not is_configured():
                self.console.print("\n[bold red]TeleFS is not configured.[/]")
                self.console.print(
                    "Run [bold cyan]login[/] inside the shell to set up credentials.\n"
                )
            else:
                self.console.print(f"[bold red]Connection failed:[/] {exc}")
            sys.exit(1)

        self.console.print(_INTRO_BANNER)

    # ---------------------------------------------------------------- hook --

    def precmd(self, line: str) -> str:
        """Expand aliases before dispatching."""
        if not line.strip():
            return line
        parts = line.split(None, 1)
        cmd_word = parts[0]
        if cmd_word in self._aliases:
            rest = parts[1] if len(parts) > 1 else ""
            line = self._aliases[cmd_word] + (" " + rest if rest else "")
        return line

    def postcmd(self, stop, line: str):
        """After each command update prompt colour via _last_failed."""
        return stop

    def default(self, line: str):
        """Handle `!cmd` (local shell passthrough) and unknown commands."""
        if line.startswith("!"):
            local_cmd = line[1:].strip()
            if local_cmd:
                os.system(local_cmd)
            return
        # Unknown command
        cmd_word = line.split()[0] if line.split() else line
        self.console.print(f"[red]Unknown command:[/] [bold]{cmd_word}[/]  (type [cyan]help[/])")

    def emptyline(self):
        self._last_failed = False  # Pressing Enter clears the error mark
        pass  # do not repeat last command

    # ---------------------------------------------------- exit / lifecycle --

    def do_exit(self, arg):
        """Exit the shell."""
        _save_history()
        self.console.print("[dim]Goodbye![/]")
        self.fs.disconnect()
        return True

    def do_quit(self, arg):
        """Alias: exit"""
        return self.do_exit(arg)

    def do_EOF(self, arg):
        self.console.print()
        return self.do_exit(arg)

    # ------------------------------------------------------------------- cd --

    @_guard
    def do_cd(self, arg):
        """Change directory.
Usage: cd [path]   (no arg → /, '-' → previous dir)"""
        args = shlex.split(arg) if arg.strip() else []
        if not args:
            # cd with no arg → go to root (analogous to $HOME)
            target = "/"
        elif args[0] == "-":
            target = self._prev_cwd
        else:
            target = args[0]

        old = self.fs.cwd
        if self.fs.cd(target):
            self._prev_cwd = old
            self._last_failed = False
        else:
            self.console.print(f"[red]cd:[/] '{target}': No such directory")
            self._last_failed = True

    def complete_cd(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    # ------------------------------------------------------------------ pwd --

    @_guard
    def do_bones(self, arg):
        """
        Initialize standard Linux directory hierarchy (/bin, /etc, /home, etc.)
        Usage: bones
        """
        created, total = self.fs.init_linux_layout()
        self.console.print(f"[bold green]Skeleton project initialized![/] Created {created}/{total} directories.")

    @_guard
    def do_pwd(self, arg):
        """Print current working directory."""
        self.console.print(self.fs.pwd())

    # ------------------------------------------------------------------- ls --

    @_guard
    def do_ls(self, arg):
        """List directory contents.
Usage: ls [-l] [-a] [-R] [-h] [path...]"""
        args  = shlex.split(arg) if arg.strip() else []
        flags, paths = parse_linux_args(args)

        long      = "l" in flags
        recursive = "R" in flags or "recursive" in flags
        all_files = "a" in flags or "all" in flags
        human     = "h" not in flags  # human-readable by default; -h disables

        if not paths:
            paths = ["."]

        for path in paths:
            if len(paths) > 1:
                self.console.print(f"\n[bold]{path}:[/]")
            items = self.fs.ls(path, recursive=recursive, all=all_files)

            buffer = []
            for item in items:
                if isinstance(item, str) and item.startswith("ls:"):
                    self.console.print(f"[red]{item}[/]")
                    self._last_failed = True
                    continue
                if isinstance(item, dict) and item.get("type") == "header":
                    if buffer:
                        print_ls_table(self.console, buffer, self.fs, long, human)
                        buffer = []
                    if recursive:
                        self.console.print(f"\n[bold blue]{item['path']}:[/]")
                else:
                    buffer.append(item)
            if buffer:
                print_ls_table(self.console, buffer, self.fs, long, human)

    def do_ll(self, arg):
        """Alias: ls -l"""
        self.do_ls(f"-l {arg}")

    def do_la(self, arg):
        """Alias: ls -la"""
        self.do_ls(f"-la {arg}")

    def complete_ls(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    complete_ll = complete_ls
    complete_la = complete_ls

    # ----------------------------------------------------------------- tree --

    @_guard
    def do_tree(self, arg):
        """Show directory tree.
Usage: tree [path] [-a] [-d] [-s] [-h] [-l <level>]"""
        args  = shlex.split(arg) if arg.strip() else []
        flags, paths = parse_linux_args(args)

        path      = paths[0] if paths else self.fs.cwd
        all_files = "a" in flags
        dirs_only = "d" in flags
        show_size = "s" in flags
        human     = "h" in flags

        # -l <level> support (parse_linux_args stores value in flags["l"])
        level_raw = flags.get("l") or flags.get("level")
        level = None
        if level_raw and level_raw is not True:
            try:
                level = int(level_raw)
            except ValueError:
                self.console.print(f"[red]tree:[/] invalid level '{level_raw}'")
                return

        items = self.fs.storage.get_tree(
            path, max_level=level, include_hidden=all_files, dirs_only=dirs_only
        )
        if not items:
            if not self.fs.storage.get_item(path):
                self.console.print(f"[red]tree:[/] '{path}': No such file or directory")
                self._last_failed = True
            else:
                self.console.print(f"{path} [dim](empty)[/]")
            return

        print_tree_view(self.console, items, show_size=show_size,
                        human_readable=human, fs=self.fs)

    def complete_tree(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    # ---------------------------------------------------------------- mkdir --

    @_guard
    def do_mkdir(self, arg):
        """Create directories.
Usage: mkdir [-p] <path...>"""
        args  = shlex.split(arg) if arg.strip() else []
        flags, paths = parse_linux_args(args)
        parents = "p" in flags

        if not paths:
            self.console.print("[yellow]Usage:[/] mkdir [-p] <path...>")
            return

        for path in paths:
            if self.fs.mkdir(path, parents=parents):
                self.console.print(f"[dim]Created:[/] {path}")
            else:
                self.console.print(f"[red]mkdir:[/] Cannot create '{path}'")
                self._last_failed = True

    do_md = do_mkdir

    def complete_mkdir(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    complete_md = complete_mkdir

    # --------------------------------------------------------------- upload --

    @_guard
    def do_upload(self, arg):
        """Upload a local file or directory.
Usage: upload [-r] <local_path> [remote_folder]"""
        args  = shlex.split(arg) if arg.strip() else []
        flags, positional = parse_linux_args(args)
        recursive = "r" in flags or "R" in flags

        if not positional:
            self.console.print("[yellow]Usage:[/] upload [-r] <local_path> [remote_folder]")
            return

        local  = positional[0]
        remote = positional[1] if len(positional) > 1 else "."
        ok = self.fs.upload(local, remote, recursive=recursive)
        self._last_failed = not ok

    def do_ul(self, arg):
        """Alias: upload"""
        self.do_upload(arg)

    def complete_upload(self, text, line, begidx, endidx):
        """Complete local paths for first arg, remote for second."""
        parts = shlex.split(line[:begidx]) if line[:begidx].strip() else []
        # Count non-flag positionals already typed
        positionals = [p for p in parts[1:] if not p.startswith("-")]
        if len(positionals) < 1:
            return self._complete_local(text)
        return self._complete_remote(text)

    complete_ul = complete_upload

    # ------------------------------------------------------------- download --

    @_guard
    def do_download(self, arg):
        """Download remote files or directories.
Usage: download [-r] <remote_path> [local_dest]"""
        args  = shlex.split(arg) if arg.strip() else []
        flags, positional = parse_linux_args(args)
        recursive = "r" in flags or "R" in flags

        if not positional:
            self.console.print("[yellow]Usage:[/] download [-r] <remote_path> [local_dest]")
            return
        
        remote = positional[0]
        local  = positional[1] if len(positional) > 1 else None
        ok = self.fs.download(remote, local, recursive=recursive)
        self._last_failed = not ok

    def do_dl(self, arg):
        """Alias: download"""
        self.do_download(arg)

    def complete_download(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    complete_dl = complete_download

    # ---------------------------------------------------------------- check --

    @_guard
    def do_check(self, arg):
        """Check the health of remote data (verify messages exist).
Usage: check <path>"""
        if not arg.strip():
            path = self.fs.cwd
        else:
            path = arg.strip()

        full_path = self.fs._resolve_path(path)
        if not self.fs.storage.exists(full_path):
            self.console.print(f"[bold red]Error:[/] '{path}' not found.")
            self._last_failed = True
            return

        items_to_check = []
        if self.fs.storage.is_folder(full_path):
            tree = self.fs.storage.get_tree(full_path)
            items_to_check = [i for i in tree if i["type"] == "file"]
        else:
            item = self.fs.storage.get_item(full_path)
            items_to_check = [dict(item)]

        if not items_to_check:
            self.console.print(f"[green]✓ {path} is a directory and looks healthy (metadata only).[/]")
            return

        self.console.print(f"🔍 Checking [bold]{len(items_to_check)}[/] files in {path}...")
        
        from rich.table import Table
        table = Table(box=None, padding=(0, 2))
        table.add_column("Status", width=10)
        table.add_column("Name")
        table.add_column("Details")

        broken_count = 0
        import asyncio
        
        async def check_all():
            nonlocal broken_count
            for i in items_to_check:
                res = await self.fs.verify_item(i["path"])
                if res["healthy"]:
                    table.add_row("[green]Healthy[/]", i["name"], "[dim]Accessible[/]")
                else:
                    table.add_row("[bold red]Broken[/]", i["name"], f"[red]{res['reason']}[/]")
                    broken_count += 1
        
        self.fs.tg._run_async(check_all())
        self.console.print(table)

        if broken_count > 0:
            self.console.print(f"\n[bold red]Found {broken_count} broken files.[/]")
            self.console.print("[dim]Use 'rm' to remove broken metadata or check your Telegram account.[/]")
            self._last_failed = True
        else:
            self.console.print("\n[bold green]All files are healthy![/]")

    def complete_check(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    # --------------------------------------------------------------- purge --

    @_guard
    def do_purge(self, arg):
        """Wipe ALL data from Telegram and local metadata.
Usage: purge"""
        self.console.print("[bold red]⚠️ CAUTION: THIS WILL PERMANENTLY DELETE ALL DATA ON TELEGRAM AND LOCALLY. ⚠️[/]")
        self.console.print("This action is irreversible.")
        
        confirm1 = input("Are you sure you want to proceed? [y/N]: ").strip().lower()
        if confirm1 != 'y':
            self.console.print("[yellow]Purge cancelled.[/]")
            return
            
        self.console.print("\nTo confirm, please type [bold red]PURGE[/] (case-sensitive):")
        confirm2 = input("> ").strip()
        
        if confirm2 == "PURGE":
            self.console.print("Processing total purge...")
            ok = self.fs.purge()
            if ok:
                self.console.print("[bold green]Total purge complete. Your TeleFS is now clean.[/]")
            else:
                self.console.print("[bold red]Purge failed during execution.[/]")
                self._last_failed = True
        else:
            self.console.print("Verification failed. Purge cancelled.")

    # ------------------------------------------------------------------- rm --

    @_guard
    def do_rm(self, arg):
        """Remove files or directories.
Usage: rm [-r] [-f] [-i] <path...>"""
        args  = shlex.split(arg) if arg.strip() else []
        flags, paths = parse_linux_args(args)

        recursive   = "r" in flags or "R" in flags
        force       = "f" in flags
        interactive = "i" in flags

        if not paths:
            self.console.print("[yellow]Usage:[/] rm [-r] [-f] [-i] <path...>")
            return

        for path in paths:
            if interactive:
                answer = input(f"rm: remove '{path}'? [y/N] ").strip().lower()
                if answer != "y":
                    continue
            ok = self.fs.rm(path, recursive=recursive, force=force)
            if not ok:
                self._last_failed = True

    def do_rd(self, arg):
        """Alias: rm -r"""
        self.do_rm(f"-r {arg}")

    def complete_rm(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    complete_rd = complete_rm

    # ------------------------------------------------------------------- cp --

    @_guard
    def do_cp(self, arg):
        """Copy files or directories.
Usage: cp [-r] [-i] <src...> <dest>"""
        args  = shlex.split(arg) if arg.strip() else []
        flags, paths = parse_linux_args(args)
        recursive   = "r" in flags or "R" in flags
        interactive = "i" in flags

        if len(paths) < 2:
            self.console.print("[yellow]Usage:[/] cp [-r] <src...> <dest>")
            return

        dest        = paths[-1]
        srcs        = paths[:-1]
        norm_dest   = self.fs._resolve_path(dest)
        is_dest_dir = self.fs.storage.is_folder(norm_dest)

        if len(srcs) > 1 and not is_dest_dir:
            self.console.print(f"[red]cp:[/] target '{dest}' is not a directory")
            return

        for src in srcs:
            final_dest = os.path.join(dest, Path(src).name) if is_dest_dir else dest
            if interactive and self.fs.storage.exists(self.fs._resolve_path(final_dest)):
                answer = input(f"cp: overwrite '{final_dest}'? [y/N] ").strip().lower()
                if answer != "y":
                    continue
            if not self.fs.cp(src, final_dest, recursive=recursive):
                self.console.print(f"[red]cp:[/] Failed to copy '{src}'")
                self._last_failed = True

    def complete_cp(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    # ------------------------------------------------------------------- mv --

    @_guard
    def do_mv(self, arg):
        """Move or rename files/directories.
Usage: mv [-i] <src...> <dest>"""
        args  = shlex.split(arg) if arg.strip() else []
        flags, paths = parse_linux_args(args)
        interactive = "i" in flags

        if len(paths) < 2:
            self.console.print("[yellow]Usage:[/] mv <src...> <dest>")
            return

        dest        = paths[-1]
        srcs        = paths[:-1]
        norm_dest   = self.fs._resolve_path(dest)
        is_dest_dir = self.fs.storage.is_folder(norm_dest)

        if len(srcs) > 1 and not is_dest_dir:
            self.console.print(f"[red]mv:[/] target '{dest}' is not a directory")
            return

        for src in srcs:
            final_dest = os.path.join(dest, Path(src).name) if is_dest_dir else dest
            if interactive and self.fs.storage.exists(self.fs._resolve_path(final_dest)):
                answer = input(f"mv: overwrite '{final_dest}'? [y/N] ").strip().lower()
                if answer != "y":
                    continue
            if not self.fs.mv(src, final_dest):
                self.console.print(
                    f"[red]mv:[/] Failed to move '{src}'. Destination may already exist."
                )
                self._last_failed = True

    def do_rename(self, arg):
        """Alias: mv"""
        self.do_mv(arg)

    def complete_mv(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    complete_rename = complete_mv

    # ------------------------------------------------------------------ cat --

    @_guard
    def do_cat(self, arg):
        """Display remote file content.
Usage: cat <path...>"""
        args = shlex.split(arg) if arg.strip() else []
        if not args:
            self.console.print("[yellow]Usage:[/] cat <path...>")
            return
        for path in args:
            ok = self.fs.cat(path)
            if not ok:
                self._last_failed = True

    def complete_cat(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    # ----------------------------------------------------------------- open --

    @_guard
    def do_open(self, arg):
        """Download a file to a temp location and open it with the system viewer.
Usage: open <remote_path>"""
        args = shlex.split(arg) if arg.strip() else []
        if not args:
            self.console.print("[yellow]Usage:[/] open <remote_path>")
            return

        remote = args[0]
        full   = self.fs._resolve_path(remote)
        item   = self.fs.storage.get_item(full)
        if not item or item["type"] != "file":
            self.console.print(f"[red]open:[/] '{remote}': No such file")
            self._last_failed = True
            return

        with tempfile.TemporaryDirectory(prefix="telefs_") as tmpdir:
            dest = Path(tmpdir) / item["name"]
            self.console.print(f"[dim]Downloading to temp…[/]")
            ok = self.fs.download(remote, str(dest))
            if not ok:
                self._last_failed = True
                return
            # Cross-platform open
            opener = "xdg-open" if sys.platform.startswith("linux") else (
                "open" if sys.platform == "darwin" else "start"
            )
            self.console.print(f"[dim]Opening with {opener}…[/]")
            subprocess.run([opener, str(dest)], check=False)
            input("[dim]Press Enter when done…[/] ")

    def do_view(self, arg):
        """Alias: open"""
        self.do_open(arg)

    def complete_open(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    complete_view = complete_open

    # ----------------------------------------------------------------- info --

    @_guard
    def do_info(self, arg):
        """Show detailed metadata for an item.
Usage: info <path...>"""
        args = shlex.split(arg) if arg.strip() else []
        if not args:
            self.console.print("[yellow]Usage:[/] info <path...>")
            return
        for path in args:
            info = self.fs.stat(path)
            if info:
                print_info_table(self.console, info)
            else:
                self.console.print(f"[red]info:[/] '{path}': Not found")
                self._last_failed = True

    def do_stat(self, arg):
        """Alias: info"""
        self.do_info(arg)

    def complete_info(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    complete_stat = complete_info

    # ------------------------------------------------------------------- du --

    @_guard
    def do_du(self, arg):
        """Show disk usage for a path.
Usage: du [path]"""
        path = arg.strip() or "."
        size = self.fs.du(path)
        self.console.print(f"[bold]{self.fs._format_size(size)}[/]\t{path}")

    def complete_du(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    # ----------------------------------------------------------------- find --

    @_guard
    def do_find(self, arg):
        """Find items by name pattern.
Usage: find [path] -name <pattern>
  -type f|d   restrict to files (f) or directories (d)
Example: find / -name '*.mp4' -type f"""
        args  = shlex.split(arg) if arg.strip() else []
        path    = "."
        pattern = "*"
        item_type = None

        i = 0
        while i < len(args):
            if args[i] == "-name" and i + 1 < len(args):
                pattern = args[i + 1]; i += 2
            elif args[i] == "-type" and i + 1 < len(args):
                t = args[i + 1].lower()
                item_type = "file" if t == "f" else ("folder" if t == "d" else None)
                i += 2
            elif not args[i].startswith("-"):
                path = args[i]; i += 1
            else:
                i += 1

        items = self.fs.storage.find_items(
            pattern,
            self.fs._resolve_path(path),
            item_type=item_type,
        )
        if not items:
            self.console.print(f"[dim]No results for '{pattern}' in '{path}'[/]")
            return

        for item in items:
            icon = _file_icon(item["name"], item["type"])
            self.console.print(f"{icon} {item['path']}")

    def complete_find(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    # ------------------------------------------------------------ checksum --

    @_guard
    def do_checksum(self, arg):
        """Show SHA-256 hash of a remote file.
Usage: checksum <path>"""
        path = arg.strip()
        if not path:
            self.console.print("[yellow]Usage:[/] checksum <path>")
            return
        h = self.fs.get_checksum(path)
        if h:
            self.console.print(f"[bold]{h}[/]  [dim]{path}[/]")
        else:
            self.console.print(f"[red]checksum:[/] '{path}': Not found or is a directory")
            self._last_failed = True

    def complete_checksum(self, text, line, begidx, endidx):
        return self._complete_remote(text)

    # ---------------------------------------------------------------- status --

    @_guard
    def do_status(self, arg):
        """Show TeleFS connection and storage status."""
        print_status(self.console, self.fs)

    def do_quota(self, arg):
        """Alias: status"""
        self.do_status(arg)

    # --------------------------------------------------------------- config --

    @_guard
    def do_config(self, arg):
        """Manage configuration.
Usage: config [list|get <key>|set <key> <value>]"""
        args   = shlex.split(arg) if arg.strip() else []
        config = load_config()
        op     = args[0] if args else "list"

        if op in ("list", "ls") or not args:
            table = Table(title="TeleFS Configuration", box=rich_box.SIMPLE)
            table.add_column("Key", style="bold cyan")
            table.add_column("Value")
            for k, v in config.items():
                if k == "encryption":
                    continue
                table.add_row(k, str(v))
            self.console.print(table)

        elif op == "get":
            if len(args) < 2:
                self.console.print("[yellow]Usage:[/] config get <key>")
                return
            val = config.get(args[1])
            if val is None:
                self.console.print(f"[red]config:[/] key '{args[1]}' not found")
            else:
                self.console.print(f"{args[1]} = [bold]{val}[/]")

        elif op == "set":
            if len(args) < 3:
                self.console.print("[yellow]Usage:[/] config set <key> <value>")
                return
            key, val = args[1], args[2]
            if key == "api_id":
                try:
                    val = int(val)
                except ValueError:
                    self.console.print("[red]config:[/] api_id must be an integer")
                    return
            config[key] = val
            save_config(config)
            self.console.print(f"[green]✓[/] {key} = {val}")

        else:
            self.console.print("[yellow]Usage:[/] config [list|get <key>|set <key> <value>]")

    # ----------------------------------------------------------------- login --

    @_guard
    def do_login(self, arg):
        """Configure Telegram API credentials and log in."""
        self.console.print(Panel(
            "Get your credentials at [underline]https://my.telegram.org[/]",
            title="[bold blue]TeleFS Setup[/]",
            border_style="blue",
        ))
        try:
            config = load_config()
            api_id_str = input(f"API ID [{config.get('api_id') or ''}]: ").strip()
            if api_id_str:
                config["api_id"] = int(api_id_str)
            api_hash_str = input(f"API Hash [{config.get('api_hash') or ''}]: ").strip()
            if api_hash_str:
                config["api_hash"] = api_hash_str
            phone_str = input(
                f"Phone (with country code) [{config.get('phone_number') or ''}]: "
            ).strip()
            if phone_str:
                config["phone_number"] = phone_str
            save_config(config)
            self.console.print("\n[yellow]Connecting to Telegram…[/]")
            self.fs.connect()
            self.console.print("[bold green]✓ Logged in successfully![/]")
        except ValueError as exc:
            self.console.print(f"[red]Invalid input:[/] {exc}")
        except Exception as exc:
            self.console.print(f"[red]Login failed:[/] {exc}")

    # --------------------------------------------------------------- clear --

    @_guard
    def do_clear(self, arg):
        """Clear the terminal screen."""
        os.system("clear" if os.name != "nt" else "cls")

    do_cls = do_clear

    # ------------------------------------------------------------- history --

    @_guard
    def do_history(self, arg):
        """Show command history.
Usage: history [n]   (show last n entries; default 20)"""
        if not _READLINE:
            self.console.print("[dim]readline not available — no history.[/]")
            return
        args = shlex.split(arg) if arg.strip() else []
        n = 20
        if args:
            try:
                n = int(args[0])
            except ValueError:
                pass
        total = readline.get_current_history_length()
        start = max(1, total - n + 1)
        for i in range(start, total + 1):
            self.console.print(f"[dim]{i:4}[/]  {readline.get_history_item(i)}")

    # --------------------------------------------------------------- alias --

    @_guard
    def do_alias(self, arg):
        """Manage shell aliases.
Usage:
  alias               — list all aliases
  alias name=cmd      — define alias
  unalias name        — remove alias"""
        arg = arg.strip()
        if not arg:
            if not self._aliases:
                self.console.print("[dim](no aliases defined)[/]")
                return
            for name, val in sorted(self._aliases.items()):
                self.console.print(f"[cyan]{name}[/] = [green]{val}[/]")
            return
        if "=" in arg:
            name, val = arg.split("=", 1)
            name = name.strip()
            val  = val.strip().strip("'\"")
            self._aliases[name] = val
            self.console.print(f"[green]✓[/] alias [cyan]{name}[/] → {val}")
        else:
            self.console.print("[yellow]Usage:[/] alias name=command")

    @_guard
    def do_unalias(self, arg):
        """Remove a shell alias.
Usage: unalias <name>"""
        name = arg.strip()
        if name in self._aliases:
            del self._aliases[name]
            self.console.print(f"[dim]Removed alias:[/] {name}")
        else:
            self.console.print(f"[red]unalias:[/] '{name}': Not found")

    # ----------------------------------------------------------------- help --

    def do_help(self, arg):
        """Show help. Use 'help <command>' for details."""
        if arg:
            super().do_help(arg)
            return

        groups = {
            "Navigation": ["pwd", "cd", "ls", "ll", "la", "tree"],
            "File ops":   ["cat", "open", "view", "info", "stat", "du", "checksum", "find"],
            "Mutate":     ["mkdir", "md", "rm", "rd", "cp", "mv", "rename"],
            "Transfer":   ["upload", "ul", "download", "dl"],
            "Shell":      ["clear", "cls", "history", "alias", "unalias"],
            "System":     ["status", "quota", "config", "login", "exit", "quit"],
        }

        self.console.print("\n[bold blue]TeleFS Commands[/]\n")
        for group, cmds in groups.items():
            table = Table(box=None, show_header=False, padding=(0, 2))
            table.add_column("cmd",  style="bold cyan",  width=14)
            table.add_column("desc", style="dim")
            for c in cmds:
                doc = getattr(self, f"do_{c}", None)
                if doc and doc.__doc__:
                    first_line = doc.__doc__.strip().splitlines()[0]
                else:
                    first_line = ""
                table.add_row(c, first_line)
            self.console.print(Panel(table, title=f"[bold]{group}[/]", border_style="dim"))

        self.console.print(
            "[dim]Tip: prefix any line with [bold]![/] to run a local shell command. "
            "Type [bold]?cmd[/] for per-command help.[/]\n"
        )

    # --------------------------------------------------- tab completion --

    def _complete_remote(self, text: str) -> List[str]:
        return self.fs.get_completions(text)

    def _complete_local(self, text: str) -> List[str]:
        """Complete local filesystem paths."""
        matches = glob.glob(os.path.expanduser(text) + "*")
        results = []
        for m in matches:
            if os.path.isdir(m):
                results.append(m.rstrip("/") + "/")
            else:
                results.append(m)
        return results


# ---------------------------------------------------------------------------
# One-shot (non-interactive) command runner
# ---------------------------------------------------------------------------

def _connect(console: Console) -> Optional[FSManager]:
    fs = FSManager()
    try:
        fs.connect()
        return fs
    except Exception as exc:
        if not is_configured():
            console.print("\n[bold red]TeleFS is not configured.[/]")
            console.print("Run [bold cyan]telefs login[/] to set up your credentials.\n")
        else:
            console.print(f"[bold red]Connection error:[/] {exc}")
        return None


def _resolve_multi_dest(fs: FSManager, srcs: List[str], dest: str, console: Console):
    norm_dest   = fs._resolve_path(dest)
    is_dest_dir = fs.storage.is_folder(norm_dest)
    if len(srcs) > 1 and not is_dest_dir:
        console.print(f"[bold red]Error:[/] target '{dest}' is not a directory")
        return None, None
    return norm_dest, is_dest_dir


def run_one_shot(args):
    """Execute a single CLI sub-command and exit."""
    console = Console()
    fs      = _connect(console)
    if fs is None:
        sys.exit(1)

    try:
        cmd_name = args.command

        if cmd_name in ("status", "quota"):
            print_status(console, fs)

        elif cmd_name == "pwd":
            console.print(fs.pwd())

        elif cmd_name == "cd":
            if not fs.cd(args.path):
                console.print(f"[red]cd:[/] {args.path}: No such directory")
                sys.exit(1)
            else:
                console.print(f"Working directory set to: [bold cyan]{fs.cwd}[/]")

        elif cmd_name == "ls":
            paths = args.paths if args.paths else ["."]
            for path in paths:
                if len(paths) > 1:
                    console.print(f"\n[bold]{path}:[/]")
                items  = fs.ls(path, recursive=args.recursive, all=args.all)
                buffer = []
                for item in items:
                    if isinstance(item, str) and item.startswith("ls:"):
                        console.print(f"[red]{item}[/]")
                    elif isinstance(item, dict) and item.get("type") == "header":
                        if buffer:
                            print_ls_table(console, buffer, fs, long=args.long)
                            buffer = []
                        if args.recursive:
                            console.print(f"\n[bold blue]{item['path']}:[/]")
                    else:
                        buffer.append(item)
                if buffer:
                    print_ls_table(console, buffer, fs, long=args.long)

        elif cmd_name == "tree":
            items = fs.storage.get_tree(args.path, max_level=args.level)
            print_tree_view(console, items)

        elif cmd_name == "mkdir":
            for path in args.paths:
                fs.mkdir(path, parents=args.parents)

        elif cmd_name == "rm":
            for path in args.paths:
                fs.rm(path, recursive=args.recursive, force=args.force)

        elif cmd_name == "cp":
            dest, is_dest_dir = _resolve_multi_dest(fs, args.paths[:-1], args.paths[-1], console)
            if dest is None:
                return
            raw_dest = args.paths[-1]
            for src in args.paths[:-1]:
                final_dest = os.path.join(raw_dest, Path(src).name) if is_dest_dir else raw_dest
                if not fs.cp(src, final_dest, recursive=args.recursive):
                    console.print(f"[red]Error:[/] Failed to copy '{src}'")

        elif cmd_name in ("mv", "rename"):
            dest, is_dest_dir = _resolve_multi_dest(fs, args.paths[:-1], args.paths[-1], console)
            if dest is None:
                return
            raw_dest = args.paths[-1]
            for src in args.paths[:-1]:
                final_dest = os.path.join(raw_dest, Path(src).name) if is_dest_dir else raw_dest
                if not fs.mv(src, final_dest):
                    console.print(f"[red]Error:[/] Failed to move '{src}'")

        elif cmd_name == "cat":
            for path in args.paths:
                fs.cat(path)

        elif cmd_name in ("info", "stat"):
            for path in args.paths:
                info = fs.stat(path)
                if info:
                    print_info_table(console, info)
                else:
                    console.print(f"[red]Error:[/] '{path}': Not found")

        elif cmd_name == "du":
            size = fs.du(args.path)
            console.print(f"[bold]{fs._format_size(size)}[/]\t{args.path}")

        elif cmd_name == "find":
            item_type = None
            if hasattr(args, "type") and args.type:
                item_type = "file" if args.type == "f" else "folder"
            items = fs.storage.find_items(args.name, fs._resolve_path(args.path), item_type=item_type)
            for item in items:
                console.print(item["path"])

        elif cmd_name == "checksum":
            h = fs.get_checksum(args.path)
            if h:
                console.print(f"[bold]{h}[/]  [dim]{args.path}[/]")
            else:
                console.print(f"[red]Error:[/] '{args.path}': Not found")
                sys.exit(1)

        elif cmd_name in ("upload", "ul"):
            fs.upload(args.local, args.remote, recursive=args.recursive)

        elif cmd_name in ("download", "dl"):
            fs.download(args.remote, args.local)

        elif cmd_name == "config":
            config = load_config()
            op     = getattr(args, "op", None)
            if op == "get":
                val = config.get(args.key)
                if val is None:
                    console.print(f"[red]Error:[/] key '{args.key}' not found")
                else:
                    console.print(f"{args.key} = [bold]{val}[/]")
            elif op == "set":
                key, val = args.key, args.val
                if key == "api_id":
                    try:
                        val = int(val)
                    except ValueError:
                        console.print("[red]Error:[/] api_id must be an integer")
                        return
                config[key] = val
                save_config(config)
                console.print(f"[green]✓[/] {key} = {val}")
            else:
                table = Table(title="TeleFS Configuration", box=rich_box.SIMPLE)
                table.add_column("Key", style="bold cyan")
                table.add_column("Value")
                for k, v in config.items():
                    if k == "encryption":
                        continue
                    table.add_row(k, str(v))
                console.print(table)

    except sqlite3.OperationalError as exc:
        if "database is locked" in str(exc).lower():
            console.print("[bold red]Error:[/] Database is locked. Another telefs command may be running.")
        else:
            console.print(f"[bold red]Database error:[/] {exc}")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[bold red]Unexpected error:[/] {exc}")
        sys.exit(1)
    finally:
        if fs:
            fs.disconnect()


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telefs",
        description="TeleFS — Telegram as a remote filesystem",
    )
    parser.add_argument("--version", action="version", version="TeleFS 0.2.16")
    sub = parser.add_subparsers(dest="command", help="Sub-command")

    sub.add_parser("status", help="Show connection and storage status")
    sub.add_parser("quota",  help="Alias for status")

    p = sub.add_parser("cd",  help="Change working directory (persistent)")
    p.add_argument("path")

    sub.add_parser("bones", help="Initialize standard Linux directory hierarchy (/bin, /etc, /home, etc.)")
    sub.add_parser("skeleton", help="Alias for bones")

    sub.add_parser("pwd", help="Print working directory")

    p = sub.add_parser("ls", help="List directory")
    p.add_argument("paths", nargs="*", default=[])
    p.add_argument("-l", action="store_true", dest="long")
    p.add_argument("-a", "--all", action="store_true")
    p.add_argument("-R", "--recursive", action="store_true")

    p = sub.add_parser("tree", help="Print directory tree", add_help=False)
    p.add_argument("path",  nargs="?", default="/")
    p.add_argument("-l", "--level", type=int, default=None)
    p.add_argument("-a", "--all",   action="store_true")
    p.add_argument("-d", "--dirs",  action="store_true")
    p.add_argument("-s", "--size",  action="store_true")
    p.add_argument("-H", "--human", action="store_true")

    p = sub.add_parser("mkdir", help="Create directory")
    p.add_argument("paths", nargs="+")
    p.add_argument("-p", "--parents", action="store_true")

    p = sub.add_parser("rm", help="Remove file or folder")
    p.add_argument("paths", nargs="+")
    p.add_argument("-r", "-R", "--recursive", action="store_true")
    p.add_argument("-f", "--force",     action="store_true")
    p.add_argument("-i", "--interactive", action="store_true")

    p = sub.add_parser("cp", help="Copy files or folders")
    p.add_argument("paths", nargs="+", help="<src...> <dest>")
    p.add_argument("-r", "-R", "--recursive", action="store_true")

    for name in ("mv", "rename"):
        p = sub.add_parser(name)
        p.add_argument("paths", nargs="+", help="<src...> <dest>")

    p = sub.add_parser("cat", help="Display file content")
    p.add_argument("paths", nargs="+")

    for name in ("info", "stat"):
        p = sub.add_parser(name, help="Show detailed item metadata")
        p.add_argument("paths", nargs="+")

    p = sub.add_parser("du", help="Show disk usage")
    p.add_argument("path", nargs="?", default=".")

    p = sub.add_parser("find", help="Find items by name pattern")
    p.add_argument("path",  nargs="?", default=".")
    p.add_argument("-name", required=True)
    p.add_argument("-type", choices=["f", "d"], default=None)

    p = sub.add_parser("checksum", help="Show SHA-256 of a file")
    p.add_argument("path")

    p = sub.add_parser("check", help="Verify message integrity")
    p.add_argument("path", nargs="?", default=".")

    p = sub.add_parser("purge", help="Wipe all remote and local data")

    for name in ("upload", "ul"):
        p = sub.add_parser(name, help="Upload file or directory")
        p.add_argument("local")
        p.add_argument("remote", nargs="?", default=".")
        p.add_argument("-r", "-R", "--recursive", action="store_true")

    for name in ("download", "dl"):
        p = sub.add_parser(name, help="Download a file")
        p.add_argument("remote")
        p.add_argument("local", nargs="?", default=None)

    p    = sub.add_parser("config", help="Manage configuration")
    conf = p.add_subparsers(dest="op")
    pg   = conf.add_parser("get");  pg.add_argument("key")
    ps   = conf.add_parser("set");  ps.add_argument("key"); ps.add_argument("val")
    conf.add_parser("list")

    sub.add_parser("login", help="Configure and log in to Telegram")
    sub.add_parser("help",  help="Show help")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = build_parser()
    args   = parser.parse_args()

    if args.command is None:
        try:
            TeleFSShell().cmdloop()
        except KeyboardInterrupt:
            self.console.print("\n[dim]Exiting…[/]")
        finally:
            _save_history()
        return

    if args.command == "help":
        parser.print_help()
        return

    if args.command == "login":
        console = Console()
        fs      = FSManager()
        shell   = TeleFSShell.__new__(TeleFSShell)
        shell.fs      = fs
        shell.console = console
        shell._aliases     = {}
        shell._last_failed = False
        shell._prev_cwd    = "/"
        shell.do_login("")
        return

    run_one_shot(args)


if __name__ == "__main__":
    main()
