#!/usr/bin/env python3
"""pyprune - find (and optionally remove) unused imports in Python projects.

One file, zero dependencies. Needs Python 3.9+. Dry-run by default.

  python pyprune.py                # report unused imports under the current folder
  python pyprune.py src/ --fix     # remove them (safe, single-line imports only)
  python pyprune.py --defs         # also list top-level functions/classes nobody uses
"""
import argparse
import ast
import re
import sys
from pathlib import Path

__version__ = "1.0.0"

SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", "build", "dist", ".tox"}
WORD = re.compile(r"[A-Za-z_]\w*")


def py_files(root):
    if root.is_file():
        yield root
        return
    for p in sorted(root.rglob("*.py")):
        if not SKIP_DIRS & set(p.parts):
            yield p


def analyze(path):
    """Parse one file. Returns None if it can't be read or parsed."""
    try:
        src = path.read_bytes().decode("utf-8")
        tree = ast.parse(src)
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return None
    names, attrs, imports, guarded = set(), set(), [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            attrs.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 100:
            names.update(WORD.findall(node.value))  # __all__, string annotations
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
        elif isinstance(node, ast.Try):  # `try: import x / except ImportError` guards
            for stmt in node.body:
                guarded.update(id(n) for n in ast.walk(stmt))
    defs = [
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not n.decorator_list
    ]
    return src, names, attrs, imports, guarded, defs


def unused_imports(info):
    src, names, _, imports, guarded, _ = info
    lines = src.splitlines()
    found = []
    for node in imports:
        if id(node) in guarded or "noqa" in lines[node.lineno - 1]:
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        bad = []
        for al in node.names:
            if al.name == "*" or al.asname == al.name:  # `import x as x` marks a re-export
                continue
            bound = al.asname or (al.name if isinstance(node, ast.ImportFrom) else al.name.split(".")[0])
            if bound not in names:
                bad.append(al)
        if bad:
            found.append((node, bad))
    return found


def apply_fix(path, src, items):
    lines = src.splitlines(keepends=True)
    for node, bad in sorted(items, key=lambda it: -it[0].lineno):
        i = node.lineno - 1
        if node.end_lineno != node.lineno or ";" in lines[i]:
            continue  # multi-line or compound statements: leave for a human
        keep = [a for a in node.names if a not in bad]
        indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
        eol = "\r\n" if lines[i].endswith("\r\n") else "\n"
        if keep:
            node.names = keep
            lines[i] = indent + ast.unparse(node) + eol
        elif indent:
            lines[i] = indent + "pass" + eol  # keeps indented blocks valid
        else:
            del lines[i]
    new = "".join(lines)
    try:
        ast.parse(new)
    except SyntaxError:
        return False
    if new == src:
        return False
    path.write_bytes(new.encode("utf-8"))
    return True


def label(al, node):
    if isinstance(node, ast.ImportFrom):
        return f"{node.module or '.'}.{al.name}" + (f" as {al.asname}" if al.asname else "")
    return al.name + (f" as {al.asname}" if al.asname else "")


def main():
    p = argparse.ArgumentParser(prog="pyprune", description="Find and remove unused Python imports.")
    p.add_argument("paths", nargs="*", default=["."], help="files or folders (default: .)")
    p.add_argument("--fix", action="store_true", help="remove unused imports in place")
    p.add_argument("--defs", action="store_true", help="also list top-level functions/classes never referenced")
    p.add_argument("--init", action="store_true", help="include __init__.py files (re-exports are skipped by default)")
    p.add_argument("--version", action="version", version=__version__)
    a = p.parse_args()

    files = []
    for raw in a.paths:
        root = Path(raw)
        if not root.exists():
            sys.exit(f"error: no such path: {raw}")
        files.extend(py_files(root))

    parsed = {f: analyze(f) for f in files}
    total, touched = 0, 0
    for f, info in parsed.items():
        if info is None or (f.name == "__init__.py" and not a.init):
            continue
        items = unused_imports(info)
        for node, bad in items:
            for al in bad:
                total += 1
                print(f"{f}:{node.lineno}  unused import '{label(al, node)}'")
        if a.fix and items and apply_fix(f, info[0], items):
            touched += 1

    if a.defs:
        used = set().union(*[i[1] | i[2] for i in parsed.values() if i]) if any(parsed.values()) else set()
        print("\nPossibly unused top-level definitions (verify before deleting):")
        for f, info in parsed.items():
            if info is None or f.name == "__init__.py" or f.name.startswith("test"):
                continue
            for d in info[5]:
                if d.name not in used and not d.name.startswith("__") and d.name != "main" and not d.name.startswith(("test", "Test")):
                    print(f"{f}:{d.lineno}  {d.name}")

    scanned = sum(1 for i in parsed.values() if i)
    if a.fix:
        print(f"\nRemoved {total} unused import(s) in {touched} file(s) ({scanned} scanned).")
        return 0
    print(f"\n{total} unused import(s) in {scanned} file(s). Run with --fix to remove them.")
    return 1 if total else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
