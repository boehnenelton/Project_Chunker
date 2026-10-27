"""
File Name:       lib_bejson_Projects_browser.py
Relative Path:   lib/lib_bejson_Projects_browser.py
Family:          Projects
Description:     Numbered, keyboard-driven directory picker so no tool ever asks the user to type a path.
                 I/O is injected (read_choice / write) which keeps it testable and front-end agnostic.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   cbd90153-123a-4f0a-995f-3e30d54b8007
"""

from pathlib import Path
from typing import Callable, List, Optional

VERSION = "1.8.0"
PAGE_SIZE = 15


# Immediate sub-directories of `directory`, sorted, hidden ones only when show_hidden. Unreadable -> [].
def projects_browser_list_subdirs(directory: Path, show_hidden: bool = False) -> List[Path]:
    try:
        entries = [p for p in Path(directory).iterdir() if p.is_dir() and not p.is_symlink()]
    except OSError:
        return []
    return sorted((p for p in entries if show_hidden or not p.name.startswith(".")), key=lambda p: p.name.lower())


# Interactive picker. Commands: <number> enter that folder, s select the current folder, u up, n/p page,
# h toggle hidden folders, q cancel. Returns the chosen directory or None (cancel or end of input).
def projects_browser_pick_directory(start: Path,
                                    read_choice: Callable[[str], str],
                                    write: Callable[[str], None]) -> Optional[Path]:
    current = Path(start).resolve()
    if not current.is_dir():
        current = Path.home()
    show_hidden = False
    page = 0
    while True:
        subdirs = projects_browser_list_subdirs(current, show_hidden)
        pages = max(1, -(-len(subdirs) // PAGE_SIZE))
        page = min(page, pages - 1)
        write(f"\n  {current}\n")
        for offset, folder in enumerate(subdirs[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], start=1):
            write(f"   {offset:>2}) {folder.name}/\n")
        if not subdirs:
            write("   (no sub-folders)\n")
        write(f"  [s] select this folder  [u] up  [n]/[p] page {page + 1}/{pages}  "
              f"[h] hidden {'on' if show_hidden else 'off'}  [q] cancel\n")
        try:
            choice = read_choice("  > ").strip().lower()
        except EOFError:
            return None
        if choice == "q":
            return None
        if choice == "s":
            return current
        if choice == "u":
            current, page = current.parent, 0
        elif choice == "n":
            page = min(page + 1, pages - 1)
        elif choice == "p":
            page = max(page - 1, 0)
        elif choice == "h":
            show_hidden, page = not show_hidden, 0
        elif choice.isdigit() and 1 <= int(choice) <= len(subdirs[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]):
            current, page = subdirs[page * PAGE_SIZE + int(choice) - 1], 0
        else:
            write("  ?\n")
