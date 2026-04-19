import argparse
import cmd
import shlex
import sys
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.table import Table
from rich.tree import Tree as RichTree

from .fs_manager import FSManager
from .config import is_configured, load_config, save_config
import os


# ---------------------------------------------------------------------------
# Argument parsing helpers
# ---------------------------------------------------------------------------

def parse_linux_args(args_tokens: List[str]):
    """
    Parse a list of tokens into (flags_set, positional_list).
    Supports combined short flags like -rf.
    """
    flags = set()
    positional = []

    for token in args_tokens:
        if token.startswith("-") and len(token) > 1:
            if token.startswith("--"):
                flags.add(token[2:])
            else:
                for char in token[1:]:
                    flags.add(char)
        else:
            positional.append(token)

    return flags, positional


# ---------------------------------------------------------------------------
# Rich display helpers  (module-level so run_one_shot can use them directly)
# ---------------------------------------------------------------------------

def print_ls_table(console: Console, items: List, fs: FSManager, long: bool = False):
    """Render a rich table of files and folders."""
    if not items:
        console.print("[italic dim](empty)[/]")
        return

    table = Table(show_header=True, header_style="bold blue", box=None)
    table.add_column("Type", width=6)
    table.add_column("Name", min_width=20)
    table.add_column("Size", justify="right")
    if long:
        table.add_column("Uploaded")
        table.add_column("Peer ID")
    table.add_column("Status")

    for item in items:
        prefix = "[dim cyan]DIR[/]" if item["type"] == "folder" else "[green]FILE[/]"
        size = fs._format_size(item["size"]) if item["type"] == "file" else "-"
        status = "[yellow]Encrypted[/]" if item["encrypted"] else ""

        row = [prefix, item["name"], size]
        if long:
            d_item = dict(item)
            row.append(d_item.get("upload_date", "-"))
            row.append(str(d_item.get("peer_id", "-")))
        row.append(status)
        table.add_row(*row)

    console.print(table)


def print_info_table(console: Console, info: dict):
    """Render a detailed metadata table for one item."""
    table = Table(show_header=False, box=None)
    table.add_column("Key", style="bold cyan")
    table.add_column("Value")

    table.add_row("Name:", info["name"])
    table.add_row("Path:", info["path"])
    table.add_row("Type:", info["type"].upper())
    table.add_row("Size:", f"{info['size_h']} ({info['size']} bytes)")
    table.add_row("Uploaded:", info.get("upload_date", "-"))
    table.add_row("Encrypted:", "Yes" if info["encrypted"] else "No")

    if info["type"] == "file":
        table.add_row("Msg ID:", str(info.get("message_id", "-")))
        table.add_row("Hash:", info.get("file_hash") or "N/A")
        if info.get("session_id"):
            table.add_row("Session:", info["session_id"])

    console.print(table)


def print_tree_view(console: Console, items: List, show_size=False, human_readable=False, fs=None):
    """Render a rich tree of the filesystem."""
    if not items:
        console.print("[bold blue]📁 /")
        return

    # The root is the first item because get_tree returns ordered by path
    root_item = items[0]
    root_path = root_item["path"]
    
    def get_display_name(item):
        name = item.get("name") or Path(item["path"]).name
        icon = "📁" if item["type"] == "folder" else "📄"
        style = "bold blue" if item["type"] == "folder" else "green"
        
        display = f"{icon} [{style}]{name}[/]"
        if show_size and item["type"] == "file":
            size_val = item.get("size", 0)
            size_str = fs._format_size(size_val) if (human_readable and fs) else str(size_val)
            display += f" [dim]({size_str})[/]"
        return display

    tree_map = {root_path: RichTree(get_display_name(root_item))}
    
    d_count = 0
    f_count = 0
    
    for item in items:
        # Tally counts
        if item["type"] == "folder":
            d_count += 1
        else:
            f_count += 1
            
        if item["path"] == root_path:
            continue
            
        # Standardize path separators for parent lookup
        p_obj = Path(item["path"])
        parent_path = str(p_obj.parent).replace("\\", "/")
        if parent_path == ".": parent_path = "/"
        
        parent_node = tree_map.get(parent_path)
        if not parent_node:
            # Fallback: search for top-most available ancestor in the tree
            found = False
            for p in p_obj.parents:
                ps = str(p).replace("\\", "/")
                if ps in tree_map:
                    parent_node = tree_map[ps]
                    found = True
                    break
            if not found:
                parent_node = tree_map[root_path]
                
        tree_map[item["path"]] = parent_node.add(get_display_name(item))

    console.print(tree_map[root_path])
    
    # Standard tree summary: don't count the root if it's a directory
    dir_summary = d_count - 1 if root_item["type"] == "folder" else d_count
    console.print(f"\n[bold]{max(0, dir_summary)} directories, {f_count} files[/]")


def print_status(console: Console, fs: FSManager):
    """Render the status / quota panel."""
    status = fs.get_status()

    console.print("[bold blue]== TeleFS Status ==[/]")

    config_status = (
        "[green]OK[/]" if status["api_configured"]
        else "[red]Missing (run 'telefs login')[/]"
    )
    console.print(f"API Configuration: {config_status}")

    phone = status["phone"] or "[yellow]Not set[/]"
    console.print(f"Phone Number: {phone}")

    session_status = (
        "[green]Active (Logged in)[/]" if status["session_exists"]
        else "[yellow]No session found (run 'telefs login')[/]"
    )
    console.print(f"Telegram Session: {session_status}")

    stats = status["db_stats"]
    console.print("\n[bold]Local Metadata Statistics:[/]")
    console.print(f" - Folders:    {stats['folders']}")
    console.print(f" - Files:      {stats['files']}")
    console.print(f" - Total Size: {fs._format_size(stats['total_size'])}")
    console.print("")


# ---------------------------------------------------------------------------
# Interactive shell
# ---------------------------------------------------------------------------

class TeleFSShell(cmd.Cmd):
    intro = "Welcome to TeleFS. Type help or ? to list commands.\n"

    @property
    def prompt(self):
        return f"telefs:{self.fs.cwd}> "

    def __init__(self):
        super().__init__()
        self.fs = FSManager()
        self.console = Console()
        try:
            self.fs.connect()
        except Exception as e:
            if not is_configured():
                self.console.print("\n[bold red]TeleFS is not configured.[/]")
                self.console.print(
                    "Please run [bold cyan]telefs login[/] to set up your Telegram API credentials.\n"
                )
            else:
                self.console.print(f"[bold red]Failed to connect to Telegram:[/] {e}")
            sys.exit(1)

    def _update_prompt(self):
        self.prompt = f"telefs:{self.fs.pwd()}> "

    # ------------------------------------------------------------------ ls --

    def do_ls(self, arg):
        """List directory contents.\nUsage: ls [-l] [-a] [-R] [path...]"""
        args = shlex.split(arg)
        flags, paths = parse_linux_args(args)

        long      = "l" in flags
        recursive = "R" in flags
        all_files = "a" in flags

        if not paths:
            paths = ["."]

        for path in paths:
            if len(paths) > 1:
                self.console.print(f"\n{path}:")
            items = self.fs.ls(path, recursive=recursive, all=all_files)

            buffer = []
            for item in items:
                if isinstance(item, str) and item.startswith("ls:"):
                    print(item)
                    continue
                if isinstance(item, dict) and item.get("type") == "header":
                    if buffer:
                        print_ls_table(self.console, buffer, self.fs, long)
                        buffer = []
                    if recursive:
                        self.console.print(f"\n{item['path']}:")
                else:
                    buffer.append(item)

            if buffer:
                print_ls_table(self.console, buffer, self.fs, long)

    def do_ll(self, arg):
        """Alias: ls -l"""
        self.do_ls(f"-l {arg}")

    def do_la(self, arg):
        """Alias: ls -a"""
        self.do_ls(f"-a {arg}")

    # ----------------------------------------------------------------- tree --

    def do_tree(self, arg):
        """Show directory tree.\nUsage: tree [path] [-a] [-d] [-s] [-h] [-l level]"""
        args = shlex.split(arg)
        flags, paths = parse_linux_args(args)
        
        path = paths[0] if paths else self.fs.cwd
        
        level = flags.get('l') or flags.get('level')
        all_files = 'a' in flags or 'all' in flags
        dirs_only = 'd' in flags or 'dirs' in flags
        show_size = 's' in flags or 'size' in flags
        human = 'h' in flags or 'human' in flags
        
        if level:
            try:
                level = int(level)
            except ValueError:
                print(f"tree: invalid level '{level}'")
                return

        items = self.fs.storage.get_tree(path, max_level=level, include_hidden=all_files, dirs_only=dirs_only)
        if not items:
            # Check if directory exists at all
            if not self.fs.storage.get_item(path):
                print(f"tree: '{path}': No such file or directory")
            else:
                print(f"{path} [empty]")
            return
            
        print_tree_view(self.console, items, show_size=show_size, human_readable=human, fs=self.fs)

    # ------------------------------------------------------------------ pwd --

    def do_pwd(self, arg):
        """Print current working directory."""
        print(self.fs.pwd())

    # ------------------------------------------------------------------- cd --

    def do_cd(self, arg):
        """Change directory.\nExample: cd /Documents"""
        args = shlex.split(arg)
        if not args:
            self.console.print("[bold yellow]Tip:[/] Usage: cd <folder>")
            return
        if not self.fs.cd(args[0]):
            self.console.print(
                f"[bold red]Error:[/] Directory '{args[0]}' not found."
            )
        self._update_prompt()

    # ----------------------------------------------------------------- mkdir --

    def do_mkdir(self, arg):
        """Create directories.\nUsage: mkdir [-p] <path...>"""
        args = shlex.split(arg)
        flags, paths = parse_linux_args(args)
        parents = "p" in flags

        if not paths:
            self.console.print("[bold yellow]Tip:[/] Usage: mkdir [-p] <path...>")
            return

        for path in paths:
            if not self.fs.mkdir(path, parents=parents):
                self.console.print(
                    f"[bold red]Error:[/] Cannot create directory '{path}'."
                )

    def do_md(self, arg):
        """Alias: mkdir"""
        self.do_mkdir(arg)

    # ---------------------------------------------------------------- upload --

    def do_upload(self, arg):
        """Upload a file or directory.\nUsage: upload [-r] <local_path> [remote_folder]"""
        args = shlex.split(arg)
        flags, positional = parse_linux_args(args)
        recursive = "r" in flags or "R" in flags

        if not positional:
            self.console.print(
                "[bold yellow]Tip:[/] Usage: upload [-r] <local_path> [remote_folder]"
            )
            return

        local  = positional[0]
        remote = positional[1] if len(positional) > 1 else "."
        self.fs.upload(local, remote, recursive=recursive)

    def do_ul(self, arg):
        """Alias: upload"""
        self.do_upload(arg)

    # -------------------------------------------------------------- download --

    def do_download(self, arg):
        """Download a file.\nUsage: download <remote_path> [local_dest]"""
        args = shlex.split(arg)
        if not args:
            self.console.print(
                "[bold yellow]Tip:[/] Usage: download <remote_path> [local_dest]"
            )
            return
        remote = args[0]
        local  = args[1] if len(args) > 1 else None
        self.fs.download(remote, local)

    def do_dl(self, arg):
        """Alias: download"""
        self.do_download(arg)

    # -------------------------------------------------------------------- rm --

    def do_rm(self, arg):
        """Remove files or directories.\nUsage: rm [-r] [-f] <path...>"""
        args = shlex.split(arg)
        flags, paths = parse_linux_args(args)

        recursive = "r" in flags or "R" in flags
        force     = "f" in flags

        if not paths:
            self.console.print("[bold yellow]Tip:[/] Usage: rm [-r] [-f] <path...>")
            return

        for path in paths:
            self.fs.rm(path, recursive=recursive, force=force)

    def do_rd(self, arg):
        """Alias: rm -r"""
        self.do_rm(f"-r {arg}")

    # -------------------------------------------------------------------- cp --

    def do_cp(self, arg):
        """Copy files or directories.\nUsage: cp [-r] <src...> <dest>"""
        args = shlex.split(arg)
        flags, paths = parse_linux_args(args)
        recursive = "r" in flags or "R" in flags

        if len(paths) < 2:
            self.console.print("[bold yellow]Tip:[/] Usage: cp [-r] <src...> <dest>")
            return

        dest        = paths[-1]
        srcs        = paths[:-1]
        norm_dest   = self.fs.storage.normalize_path(os.path.join(self.fs.cwd, dest))
        is_dest_dir = self.fs.storage.is_folder(norm_dest)

        if len(srcs) > 1 and not is_dest_dir:
            self.console.print(
                f"[bold red]Error:[/] target '{dest}' is not a directory"
            )
            return

        for src in srcs:
            final_dest = os.path.join(dest, os.path.basename(src)) if is_dest_dir else dest
            self.fs.cp(src, final_dest, recursive=recursive)

    # -------------------------------------------------------------------- mv --
    # NOTE: Only ONE definition of do_mv. The duplicate that was here before
    # silently overwrote this version, breaking multi-source support.

    def do_mv(self, arg):
        """Move or rename files/directories.\nUsage: mv <src...> <dest>"""
        args = shlex.split(arg)
        _, paths = parse_linux_args(args)

        if len(paths) < 2:
            self.console.print("[bold yellow]Tip:[/] Usage: mv <src...> <dest>")
            return

        dest        = paths[-1]
        srcs        = paths[:-1]
        norm_dest   = self.fs.storage.normalize_path(os.path.join(self.fs.cwd, dest))
        is_dest_dir = self.fs.storage.is_folder(norm_dest)

        if len(srcs) > 1 and not is_dest_dir:
            self.console.print(
                f"[bold red]Error:[/] target '{dest}' is not a directory"
            )
            return

        for src in srcs:
            final_dest = os.path.join(dest, os.path.basename(src)) if is_dest_dir else dest
            if not self.fs.mv(src, final_dest):
                self.console.print(
                    f"[bold red]Error:[/] Failed to move '{src}'. "
                    "Make sure the destination does not already exist."
                )

    def do_rename(self, arg):
        """Alias: mv"""
        self.do_mv(arg)

    # ------------------------------------------------------------------- cat --

    def do_cat(self, arg):
        """Display file content.\nUsage: cat <path...>"""
        args = shlex.split(arg)
        if not args:
            self.console.print("[bold yellow]Tip:[/] Usage: cat <path...>")
            return
        for path in args:
            self.fs.cat(path)

    # ------------------------------------------------------------------ info --

    def do_info(self, arg):
        """Show technical info about an item.\nUsage: info <path...>"""
        args = shlex.split(arg)
        if not args:
            self.console.print("[bold yellow]Tip:[/] Usage: info <path...>")
            return
        for path in args:
            info = self.fs.stat(path)
            if info:
                print_info_table(self.console, info)
            else:
                self.console.print(f"[bold red]Error:[/] '{path}' not found.")

    def do_stat(self, arg):
        """Alias: info"""
        self.do_info(arg)

    # -------------------------------------------------------------------- du --

    def do_du(self, arg):
        """Show directory disk usage.\nUsage: du [path]"""
        path = arg.strip() if arg.strip() else "."
        size = self.fs.du(path)
        self.console.print(f"{self.fs._format_size(size)}\t{path}")

    # ------------------------------------------------------------------ find --

    def do_find(self, arg):
        """Find items by name pattern.\nUsage: find [path] -name <pattern>\nExample: find / -name '*.jpg'"""
        args    = shlex.split(arg)
        path    = "."
        pattern = "*"

        if len(args) >= 2 and args[0] != "-name":
            path = args[0]
            args = args[1:]

        if "-name" in args:
            idx = args.index("-name")
            if idx + 1 < len(args):
                pattern = args[idx + 1]
        elif args:
            pattern = args[0]

        items = self.fs.find(pattern, path)
        if not items:
            self.console.print(f"No results found for '{pattern}' in '{path}'")
            return

        for item in items:
            self.console.print(item["path"])

    # -------------------------------------------------------------- checksum --

    def do_checksum(self, arg):
        """Show file hash (SHA-256).\nUsage: checksum <path>"""
        path = arg.strip()
        if not path:
            self.console.print("[bold yellow]Tip:[/] Usage: checksum <path>")
            return
        h = self.fs.get_checksum(path)
        if h:
            print(f"{h}  {path}")
        else:
            self.console.print(
                f"[bold red]Error:[/] '{path}' not found or is a directory."
            )

    # ----------------------------------------------------------------- status --

    def do_status(self, arg):
        """Show TeleFS configuration and storage status."""
        print_status(self.console, self.fs)

    def do_quota(self, arg):
        """Alias: status"""
        self.do_status(arg)

    # ----------------------------------------------------------------- config --

    def do_config(self, arg):
        """Get or set configuration.\nUsage: config [list|get <key>|set <key> <value>]"""
        args    = shlex.split(arg)
        config  = load_config()
        # 'op' is used instead of 'cmd' to avoid shadowing the cmd module.
        op      = args[0] if args else "list"

        if op == "list" or not args:
            table = Table(title="TeleFS Configuration", box=None)
            table.add_column("Key", style="bold cyan")
            table.add_column("Value")
            for k, v in config.items():
                if k == "encryption":
                    continue
                table.add_row(k, str(v))
            self.console.print(table)
            return

        if op == "get":
            if len(args) < 2:
                self.console.print("[bold yellow]Usage:[/] config get <key>")
                return
            key = args[1]
            val = config.get(key)
            if val is None:
                self.console.print(f"[bold red]Error:[/] key '{key}' not found.")
            else:
                print(f"{key} = {val}")

        elif op == "set":
            if len(args) < 3:
                self.console.print("[bold yellow]Usage:[/] config set <key> <value>")
                return
            key, val = args[1], args[2]
            if key == "api_id":
                try:
                    val = int(val)
                except ValueError:
                    self.console.print("[bold red]Error:[/] api_id must be an integer.")
                    return
            config[key] = val
            save_config(config)
            self.console.print(f"[green]Set {key} = {val}[/]")

        else:
            self.console.print("[bold yellow]Usage:[/] config [list|get <key>|set <key> <value>]")

    # ------------------------------------------------------------------ login --

    def do_login(self, arg):
        """Configure Telegram API credentials and log in."""
        self.console.print("[bold blue]== TeleFS Setup ==[/]")
        self.console.print(
            "Get your API ID and Hash from [underline]https://my.telegram.org[/]\n"
        )

        try:
            config = load_config()

            api_id_str = input(f"API ID [{config.get('api_id') or ''}]: ").strip()
            if api_id_str:
                config["api_id"] = int(api_id_str)

            api_hash_str = input(f"API Hash [{config.get('api_hash') or ''}]: ").strip()
            if api_hash_str:
                config["api_hash"] = api_hash_str

            phone_str = input(
                f"Phone Number (with +country code) [{config.get('phone_number') or ''}]: "
            ).strip()
            if phone_str:
                config["phone_number"] = phone_str

            save_config(config)

            self.console.print("\n[yellow]Connecting to Telegram to verify…[/]")
            self.fs.connect()
            self.console.print("[bold green]Success![/] You are now logged in.")
            self._update_prompt()

        except ValueError as exc:
            self.console.print(f"[bold red]Invalid input:[/] {exc}")
        except Exception as exc:
            self.console.print(f"[bold red]Login failed:[/] {exc}")

    # ------------------------------------------------------------------ misc --

    def do_help(self, arg):
        """Show help for commands."""
        super().do_help(arg)

    def do_exit(self, arg):
        """Exit the shell."""
        print("Goodbye!")
        self.fs.disconnect()
        return True

    def do_quit(self, arg):
        """Alias: exit"""
        return self.do_exit(arg)

    def do_EOF(self, arg):
        print()
        return self.do_exit(arg)

    def emptyline(self):
        pass

    # --------------------------------------------------------- tab completion --

    def _complete_remote(self, text):
        return self.fs.get_completions(text)

    # Commands that take remote paths as positional arguments.
    complete_ls       = lambda self, t, l, b, e: self._complete_remote(t)
    complete_cd       = lambda self, t, l, b, e: self._complete_remote(t)
    complete_rm       = lambda self, t, l, b, e: self._complete_remote(t)
    complete_rd       = lambda self, t, l, b, e: self._complete_remote(t)
    complete_mv       = lambda self, t, l, b, e: self._complete_remote(t)
    complete_cp       = lambda self, t, l, b, e: self._complete_remote(t)
    complete_cat      = lambda self, t, l, b, e: self._complete_remote(t)
    complete_info     = lambda self, t, l, b, e: self._complete_remote(t)
    complete_stat     = lambda self, t, l, b, e: self._complete_remote(t)
    complete_du       = lambda self, t, l, b, e: self._complete_remote(t)
    complete_find     = lambda self, t, l, b, e: self._complete_remote(t)
    complete_tree     = lambda self, t, l, b, e: self._complete_remote(t)
    complete_checksum = lambda self, t, l, b, e: self._complete_remote(t)
    complete_download = lambda self, t, l, b, e: self._complete_remote(t)
    complete_dl       = lambda self, t, l, b, e: self._complete_remote(t)
    # upload/ul first arg is a local path — no remote completion needed there,
    # but the second arg (remote folder) can still be completed.
    complete_upload   = lambda self, t, l, b, e: self._complete_remote(t)
    complete_ul       = lambda self, t, l, b, e: self._complete_remote(t)


# ---------------------------------------------------------------------------
# One-shot (non-interactive) command runner
# ---------------------------------------------------------------------------

def _connect(console: Console) -> Optional[FSManager]:
    """Connect and return an FSManager, or print an error and return None."""
    fs = FSManager()
    try:
        fs.connect()
        return fs
    except Exception as exc:
        if not is_configured():
            console.print("\n[bold red]TeleFS is not configured.[/]")
            console.print(
                "Please run [bold cyan]telefs login[/] to set up your Telegram API credentials.\n"
            )
        else:
            console.print(f"[bold red]Connection error:[/] {exc}")
        return None


def _resolve_multi_dest(fs: FSManager, srcs: List[str], dest: str, console: Console):
    """
    Returns the normalised destination path and whether it is an existing dir.
    Prints an error and returns (None, None) if multiple sources point to a
    non-directory destination.
    """
    norm_dest   = fs.storage.normalize_path(os.path.join(fs.cwd, dest))
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

        # ---- status / quota ----
        if cmd_name in ("status", "quota"):
            print_status(console, fs)

        # ---- pwd ----
        elif cmd_name == "pwd":
            print(fs.pwd())

        # ---- cd ----
        elif cmd_name == "cd":
            if not fs.cd(args.path):
                print(f"cd: {args.path}: No such directory")
            else:
                print(f"Set working directory to: {fs.cwd}")

        # ---- ls ----
        elif cmd_name == "ls":
            paths = args.paths if args.paths else ["."]
            for path in paths:
                if len(paths) > 1:
                    console.print(f"\n{path}:")
                items  = fs.ls(path, recursive=args.recursive, all=args.all)
                buffer = []
                for item in items:
                    if isinstance(item, str) and item.startswith("ls:"):
                        print(item)
                    elif isinstance(item, dict) and item.get("type") == "header":
                        if buffer:
                            print_ls_table(console, buffer, fs, long=args.long)
                            buffer = []
                        if args.recursive:
                            console.print(f"\n{item['path']}:")
                    else:
                        buffer.append(item)
                if buffer:
                    print_ls_table(console, buffer, fs, long=args.long)

        # ---- tree ----
        elif cmd_name == "tree":
            items = fs.storage.get_tree(args.path, max_level=args.level)
            print_tree_view(console, items)

        # ---- mkdir ----
        elif cmd_name == "mkdir":
            for path in args.paths:
                fs.mkdir(path, parents=args.parents)

        # ---- rm ----
        elif cmd_name == "rm":
            for path in args.paths:
                fs.rm(path, recursive=args.recursive, force=args.force)

        # ---- cp ----
        elif cmd_name == "cp":
            dest, is_dest_dir = _resolve_multi_dest(fs, args.paths[:-1], args.paths[-1], console)
            if dest is None:
                return
            srcs = args.paths[:-1]
            raw_dest = args.paths[-1]
            for src in srcs:
                final_dest = (
                    os.path.join(raw_dest, os.path.basename(src)) if is_dest_dir else raw_dest
                )
                fs.cp(src, final_dest, recursive=args.recursive)

        # ---- mv / rename ----
        # NOTE: mv subparser intentionally has NO -r flag (mv is always recursive
        # for directories in POSIX). Accessing args.recursive would raise AttributeError.
        elif cmd_name in ("mv", "rename"):
            dest, is_dest_dir = _resolve_multi_dest(fs, args.paths[:-1], args.paths[-1], console)
            if dest is None:
                return
            srcs     = args.paths[:-1]
            raw_dest = args.paths[-1]
            for src in srcs:
                final_dest = (
                    os.path.join(raw_dest, os.path.basename(src)) if is_dest_dir else raw_dest
                )
                if not fs.mv(src, final_dest):
                    console.print(
                        f"[bold red]Error:[/] Failed to move '{src}'."
                    )

        # ---- cat ----
        elif cmd_name == "cat":
            for path in args.paths:
                fs.cat(path)

        # ---- info / stat ----
        elif cmd_name in ("info", "stat"):
            for path in args.paths:
                info = fs.stat(path)
                if info:
                    print_info_table(console, info)
                else:
                    print(f"item not found: {path}")

        # ---- du ----
        elif cmd_name == "du":
            size = fs.du(args.path)
            print(f"{fs._format_size(size)}\t{args.path}")

        # ---- find ----
        elif cmd_name == "find":
            items = fs.find(args.name, args.path)
            for item in items:
                print(item["path"])

        # ---- checksum ----
        elif cmd_name == "checksum":
            h = fs.get_checksum(args.path)
            if h:
                print(f"{h}  {args.path}")
            else:
                console.print(
                    f"[bold red]Error:[/] '{args.path}' not found or is a directory."
                )

        # ---- upload / ul ----
        elif cmd_name in ("upload", "ul"):
            fs.upload(args.local, args.remote, recursive=args.recursive)

        # ---- download / dl ----
        elif cmd_name in ("download", "dl"):
            fs.download(args.remote, args.local)

        # ---- config ----
        elif cmd_name == "config":
            config = load_config()
            op     = getattr(args, "op", None)

            if op == "get":
                val = config.get(args.key)
                if val is None:
                    console.print(f"[bold red]Error:[/] key '{args.key}' not found.")
                else:
                    print(f"{args.key} = {val}")

            elif op == "set":
                key, val = args.key, args.val
                if key == "api_id":
                    try:
                        val = int(val)
                    except ValueError:
                        console.print("[bold red]Error:[/] api_id must be an integer.")
                        return
                config[key] = val
                save_config(config)
                console.print(f"[green]Set {key} = {val}[/]")

            else:  # list / default
                table = Table(title="TeleFS Configuration", box=None)
                table.add_column("Key", style="bold cyan")
                table.add_column("Value")
                for k, v in config.items():
                    if k == "encryption":
                        continue
                    table.add_row(k, str(v))
                console.print(table)

    except KeyboardInterrupt:
        print("\nAborted.")
    finally:
        fs.disconnect()


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telefs",
        description="TeleFS - Telegram as a remote filesystem",
    )
    parser.add_argument("--version", action="version", version="TeleFS 0.2.2")
    sub = parser.add_subparsers(dest="command", help="Command to execute")

    # ---- status ----
    sub.add_parser("status", help="Show connection and storage status")

    # ---- quota ----
    sub.add_parser("quota", help="Alias for status")

    # ---- cd ----
    p = sub.add_parser("cd", help="Change working directory (persistent)")
    p.add_argument("path", help="Path to switch to")

    # ---- pwd ----
    sub.add_parser("pwd", help="Print working directory")

    # ---- ls ----
    p = sub.add_parser("ls", help="List directory")
    p.add_argument("paths", nargs="*", default=[], help="Paths to list")
    p.add_argument("-l", action="store_true", dest="long", help="Long listing format")
    p.add_argument("-a", "--all", action="store_true", help="Show hidden files")
    p.add_argument("-R", "--recursive", action="store_true", help="Recursive listing")

    # ---- tree ----
    p = sub.add_parser("tree", help="Print directory tree", add_help=False)
    p.add_argument("path", nargs="?", default="/", help="Root path")
    p.add_argument("-l", "--level", type=int, default=None, help="Maximum depth")
    p.add_argument("-a", "--all", action="store_true", help="Include hidden files")
    p.add_argument("-d", "--dirs", action="store_true", help="List directories only")
    p.add_argument("-s", "--size", action="store_true", help="Show byte size")
    p.add_argument("-h", "--human", action="store_true", help="Human readable sizes")

    # ---- mkdir ----
    p = sub.add_parser("mkdir", help="Create directory")
    p.add_argument("paths", nargs="+", help="Directory paths")
    p.add_argument("-p", "--parents", action="store_true", help="Create parent dirs as needed")

    # ---- rm ----
    p = sub.add_parser("rm", help="Remove file or folder")
    p.add_argument("paths", nargs="+", help="Paths to remove")
    p.add_argument("-r", "-R", "--recursive", action="store_true")
    p.add_argument("-f", "--force", action="store_true")

    # ---- cp ----
    p = sub.add_parser("cp", help="Copy files or folders")
    p.add_argument("paths", nargs="+", help="<src...> <dest>")
    p.add_argument("-r", "-R", "--recursive", action="store_true", help="Copy recursively")

    # ---- mv / rename ----
    for name in ("mv", "rename"):
        p = sub.add_parser(name, help="Move or rename file/folder" if name == "mv" else "Alias for mv")
        p.add_argument("paths", nargs="+", help="<src...> <dest>")
        # NOTE: no --recursive flag — mv handles dirs implicitly (POSIX behaviour)

    # ---- cat ----
    p = sub.add_parser("cat", help="Display file content")
    p.add_argument("paths", nargs="+", help="Remote file paths")

    # ---- info / stat ----
    for name in ("info", "stat"):
        p = sub.add_parser(name, help="Show detailed item information" if name == "info" else "Alias for info")
        p.add_argument("paths", nargs="+", help="Paths to items")

    # ---- du ----
    p = sub.add_parser("du", help="Show directory disk usage")
    p.add_argument("path", nargs="?", default=".", help="Path to calculate usage for")

    # ---- find ----
    p = sub.add_parser("find", help="Find items by name pattern")
    p.add_argument("path", nargs="?", default=".", help="Root path for search")
    p.add_argument("-name", required=True, help="Pattern to match (e.g. '*.jpg')")

    # ---- checksum ----
    p = sub.add_parser("checksum", help="Show file hash (SHA-256)")
    p.add_argument("path", help="Remote file path")

    # ---- upload / ul ----
    for name in ("upload", "ul"):
        p = sub.add_parser(name, help="Upload a file or directory")
        p.add_argument("local", help="Local file or directory path")
        p.add_argument("remote", nargs="?", default=".", help="Remote folder (default: .)")
        p.add_argument("-r", "-R", "--recursive", action="store_true", help="Upload recursively")

    # ---- download / dl ----
    for name in ("download", "dl"):
        p = sub.add_parser(name, help="Download a file")
        p.add_argument("remote", help="Remote file path")
        p.add_argument("local", nargs="?", default=None, help="Local destination path")

    # ---- config ----
    p      = sub.add_parser("config", help="Manage configuration")
    conf   = p.add_subparsers(dest="op")
    pg     = conf.add_parser("get", help="Get a config value")
    pg.add_argument("key")
    ps     = conf.add_parser("set", help="Set a config value")
    ps.add_argument("key")
    ps.add_argument("val")
    conf.add_parser("list", help="List all config values")

    # ---- login ----
    sub.add_parser("login", help="Configure and log in to Telegram")

    # ---- help ----
    sub.add_parser("help", help="Show this help message")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = build_parser()
    args   = parser.parse_args()

    if args.command == "help" or args.command is None:
        # No sub-command: drop into interactive shell.
        if args.command is None:
            try:
                TeleFSShell().cmdloop()
            except KeyboardInterrupt:
                print("\nExiting…")
        else:
            parser.print_help()
        return

    if args.command == "login":
        # Login is interactive — always run in shell mode.
        console = Console()
        fs      = FSManager()
        shell   = TeleFSShell.__new__(TeleFSShell)
        shell.fs      = fs
        shell.console = console
        shell.do_login("")
        return

    run_one_shot(args)


if __name__ == "__main__":
    main()