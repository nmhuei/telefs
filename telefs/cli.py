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


def print_ls_table(console: Console, items: List, fs: FSManager):
    """Print a rich table of files and folders."""
    if not items:
        console.print("[italic dim](empty)[/]")
        return

    table = Table(show_header=True, header_style="bold blue", box=None)
    table.add_column("Type", width=6)
    table.add_column("Name", min_width=20)
    table.add_column("Size", justify="right")
    table.add_column("Status")

    for item in items:
        prefix = "[dim cyan]DIR[/]" if item["type"] == "folder" else "[green]FILE[/]"
        size = fs._format_size(item["size"]) if item["type"] == "file" else "-"
        status = "[yellow]Encrypted[/]" if item["encrypted"] else ""
        table.add_row(prefix, item["name"], size, status)
    
    console.print(table)


def print_tree_view(console: Console, items: List):
    """Print a rich tree of files and folders."""
    if not items:
        console.print("[bold blue]/")
        return
    
    tree_map = {"/": RichTree("[bold blue]/")}
    for item in items:
        if item["path"] == "/": continue
        parent_path = str(Path(item["path"]).parent)
        name = Path(item["path"]).name
        style = "bold blue" if item["type"] == "folder" else "green"
        
        # Find the parent in our tree_map
        if parent_path in tree_map:
            tree_map[item["path"]] = tree_map[parent_path].add(f"[{style}]{name}")
        else:
            # Handle cases where parent might not be in BFS order (unlikely with get_tree)
            tree_map[item["path"]] = tree_map["/"].add(f"[{style}]{name}")
    
    console.print(tree_map["/"])


class TeleFSShell(cmd.Cmd):
    intro = "Welcome to TeleFS. Type help or ? to list commands.\n"
    
    def __init__(self):
        super().__init__()
        self.fs = FSManager()
        self.console = Console()
        try:
            self.fs.connect()
            self._update_prompt()
        except Exception as e:
            if not is_configured():
                self.console.print("\n[bold red]TeleFS is not configured.[/]")
                self.console.print("Please run [bold cyan]telefs login[/] to set up your Telegram API credentials.\n")
            else:
                self.console.print(f"[bold red]Failed to connect to Telegram:[/] {e}")
            sys.exit(1)

    def _update_prompt(self):
        self.prompt = f"telefs:{self.fs.pwd()}> "

    def do_ls(self, arg):
        """
        List directory contents.
        Example: ls /Photos
        """
        try:
            path = arg if arg else self.fs.cwd
            items = self.fs.storage.list_folder(self.fs.storage.normalize_path(path))
            print_ls_table(self.console, items, self.fs)
        except Exception as e:
            self.console.print(f"[bold red]Error:[/] {e}")

    def do_tree(self, arg):
        """
        Show directory tree.
        Example: tree /
        """
        items = self.fs.storage.get_tree("/")
        print_tree_view(self.console, items)

    def do_pwd(self, arg):
        """Print current working directory."""
        print(self.fs.pwd())

    def do_cd(self, arg):
        """
        Change directory.
        Example: cd /Documents
        """
        args = shlex.split(arg)
        if not args:
            self.console.print("[bold yellow]Tip:[/] Usage: cd <folder>")
            return
        if not self.fs.cd(args[0]):
            self.console.print(f"[bold red]Error:[/] Directory '{args[0]}' not found. Use 'ls' to see available folders.")
        self._update_prompt()

    def do_mkdir(self, arg):
        """
        Create directory.
        Example: mkdir /NewFolder
        """
        args = shlex.split(arg)
        if not args:
            self.console.print("[bold yellow]Tip:[/] Usage: mkdir <folder>")
            return
        if not self.fs.mkdir(args[0]):
            self.console.print(f"[bold red]Error:[/] Cannot create directory '{args[0]}'. It might already exist.")

    def do_upload(self, arg):
        """
        Upload a file.
        Example: upload ~/Pictures/cat.jpg /Photos
        """
        args = shlex.split(arg)
        if not args:
            self.console.print("[bold yellow]Tip:[/] Usage: upload <local_path> [remote_folder]")
            return
        local = args[0]
        remote = args[1] if len(args) > 1 else "."
        self.fs.upload(local, remote)

    def do_download(self, arg):
        """
        Download a file.
        Example: download /Photos/cat.jpg ./downloads/
        """
        args = shlex.split(arg)
        if not args:
            self.console.print("[bold yellow]Tip:[/] Usage: download <remote_path> [local_dest]")
            return
        remote = args[0]
        local = args[1] if len(args) > 1 else None
        self.fs.download(remote, local)

    def do_rm(self, arg):
        """
        Remove file or folder.
        Example: rm -r /OldFolder
        """
        args = shlex.split(arg)
        recursive = False
        if "-r" in args:
            recursive = True
            args.remove("-r")
        if not args:
            self.console.print("[bold yellow]Tip:[/] Usage: rm [-r] <path>")
            return
        
        path = args[0]
        if recursive:
            confirm = input(f"CAUTION: Remove folder '{path}' and ALL contents? [y/N] ").strip().lower()
            if confirm != 'y':
                print("Operation aborted.")
                return
                
        self.fs.rm(path, recursive)

    def do_login(self, arg):
        """Configure Telegram API credentials and log in.
        
        This will guide you through entering your API ID and Hash from my.telegram.org
        and performing the first-time SMS authentication.
        """
        self.console.print("[bold blue]== TeleFS Setup ==[/]")
        self.console.print("Get your API ID and Hash from [underline]https://my.telegram.org[/]\n")
        
        try:
            config = load_config()
            
            api_id_input = input(f"API ID [{config.get('api_id') or ''}]: ").strip()
            if api_id_input:
                config['api_id'] = int(api_id_input)
                
            api_hash_input = input(f"API Hash [{config.get('api_hash') or ''}]: ").strip()
            if api_hash_input:
                config['api_hash'] = api_hash_input
                
            phone_input = input(f"Phone Number (with +country code) [{config.get('phone_number') or ''}]: ").strip()
            if phone_input:
                config['phone_number'] = phone_input

            save_config(config)
            
            self.console.print("\n[yellow]Connecting to Telegram to verify...[/]")
            self.fs.connect()
            self.console.print("[bold green]Success![/] You are now logged in.")
            self._update_prompt()
            
        except ValueError as ve:
            self.console.print(f"[bold red]Invalid input:[/] {ve}")
        except Exception as e:
            self.console.print(f"[bold red]Login failed:[/] {e}")

    def do_help(self, arg):
        """Show help for commands."""
        super().do_help(arg)

    def do_exit(self, arg):
        """Exit the shell."""
        print("Goodbye!")
        self.fs.disconnect()
        return True

    def do_quit(self, arg):
        return self.do_exit(arg)

    def do_EOF(self, arg):
        print()
        return self.do_exit(arg)

    # Shortcuts
    def do_ul(self, arg):
        """Shortcut for upload."""
        return self.do_upload(arg)

    def do_dl(self, arg):
        """Shortcut for download."""
        return self.do_download(arg)

    def emptyline(self):
        pass

    def complete_ls(self, text, line, begidx, endidx):
        return self.fs.get_completions(text)

    def complete_cd(self, text, line, begidx, endidx):
        return self.fs.get_completions(text)

    def complete_rm(self, text, line, begidx, endidx):
        return self.fs.get_completions(text)

    def complete_download(self, text, line, begidx, endidx):
        return self.fs.get_completions(text)

    def complete_dl(self, text, line, begidx, endidx):
        return self.fs.get_completions(text)


def run_one_shot(args):
    """Execute a single command and exit."""
    fs = FSManager()
    console = Console()
    try:
        fs.connect()
    except Exception as e:
        if not is_configured():
            console.print("\n[bold red]TeleFS is not configured.[/]")
            console.print("Please run [bold cyan]telefs login[/] to set up your Telegram API credentials.\n")
        else:
            console.print(f"[bold red]Connection error:[/] {e}")
        sys.exit(1)

    try:
        if args.command == "ls":
            path = args.path if args.path else fs.cwd
            items = fs.storage.list_folder(fs.storage.normalize_path(path))
            print_ls_table(console, items, fs)
        elif args.command == "tree":
            items = fs.storage.get_tree("/")
            print_tree_view(console, items)
        elif args.command == "pwd":
            print(fs.pwd())
        elif args.command == "mkdir":
            if not fs.mkdir(args.path):
                print(f"mkdir: failed to create '{args.path}'")
        elif args.command in ("upload", "ul"):
            fs.upload(args.local, args.remote)
        elif args.command in ("download", "dl"):
            fs.download(args.remote, args.local)
        elif args.command == "rm":
            fs.rm(args.path, recursive=args.recursive)
    except KeyboardInterrupt:
        print("\nAborted.")
    finally:
        fs.disconnect()


def main():
    parser = argparse.ArgumentParser(description="TeleFS - Telegram as a remote filesystem")
    parser.add_argument("--version", action="version", version="TeleFS 0.1.6")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # ls
    parser_ls = subparsers.add_parser("ls", help="List directory")
    parser_ls.add_argument("path", nargs="?", default=None)

    # tree
    subparsers.add_parser("tree", help="Print directory tree")

    # pwd
    subparsers.add_parser("pwd", help="Print working directory")

    # mkdir
    parser_mkdir = subparsers.add_parser("mkdir", help="Create directory")
    parser_mkdir.add_argument("path", help="Directory path")

    # upload
    for cmd_name in ["upload", "ul"]:
        p = subparsers.add_parser(cmd_name, help="Upload a file")
        p.add_argument("local", help="Local file path")
        p.add_argument("remote", nargs="?", default=".", help="Remote folder path")

    # download
    for cmd_name in ["download", "dl"]:
        p = subparsers.add_parser(cmd_name, help="Download a file")
        p.add_argument("remote", help="Remote file path")
        p.add_argument("local", nargs="?", default=None, help="Local destination path")

    # rm
    parser_rm = subparsers.add_parser("rm", help="Remove file or folder")
    parser_rm.add_argument("path", help="Path to remove")
    parser_rm.add_argument("-r", "--recursive", action="store_true")

    # login
    subparsers.add_parser("login", help="Configure and log in to Telegram")

    # help alias
    subparsers.add_parser("help", help="Show this help message")

    args = parser.parse_args()

    if args.command == "help":
        parser.print_help()
        return

    if args.command == "login":
        # Interactive login logic
        shell = TeleFSShell.__new__(TeleFSShell)
        shell.console = Console()
        shell.fs = FSManager()
        shell.do_login("")
        return

    if args.command:
        run_one_shot(args)
    else:
        try:
            TeleFSShell().cmdloop()
        except KeyboardInterrupt:
            print("\nExiting...")


if __name__ == "__main__":
    main()
