"""Minimal OpenAI Apply Patch implementation for recovery.

Format reference:
  *** Begin Patch
  *** [Add|Update|Delete] File: <path>
  [optional @@ anchors and ' '/'-'/'+ ' lines]
  *** End Patch

For Update: hunks separated by @@ anchor lines. Lines starting with ' ' are
context, '-' removed, '+' added. Anchors narrow the search range; if no anchor
is given we search the whole file for the first matching context+remove run.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Hunk:
    anchors: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


@dataclass
class FileOp:
    kind: str            # 'add' | 'update' | 'delete'
    path: str
    add_lines: list[str] = field(default_factory=list)   # for add
    hunks: list[Hunk] = field(default_factory=list)      # for update


def parse_patch(text: str) -> list[FileOp]:
    """Parse a patch text containing one or more *** Begin Patch ... *** End Patch."""
    ops: list[FileOp] = []
    blocks = re.findall(r"\*\*\* Begin Patch\n(.*?)\n\*\*\* End Patch",
                        text, re.DOTALL)
    for block in blocks:
        ops.extend(_parse_block(block))
    return ops


def _parse_block(block: str) -> list[FileOp]:
    ops: list[FileOp] = []
    cur: FileOp | None = None
    in_hunk = False

    for line in block.split("\n"):
        if line.startswith("*** Add File: "):
            if cur:
                ops.append(cur)
            cur = FileOp(kind="add", path=line[len("*** Add File: "):].strip())
            in_hunk = False
        elif line.startswith("*** Delete File: "):
            if cur:
                ops.append(cur)
            cur = FileOp(kind="delete", path=line[len("*** Delete File: "):].strip())
            in_hunk = False
        elif line.startswith("*** Update File: "):
            if cur:
                ops.append(cur)
            cur = FileOp(kind="update", path=line[len("*** Update File: "):].strip())
            in_hunk = False
        elif line.startswith("*** End of File"):
            continue
        elif line.startswith("@@"):
            if cur and cur.kind == "update":
                anchor = line[2:].strip()
                if not in_hunk or cur.hunks[-1].lines:
                    cur.hunks.append(Hunk(anchors=[anchor] if anchor else []))
                    in_hunk = True
                else:
                    cur.hunks[-1].anchors.append(anchor)
        elif cur:
            if cur.kind == "update":
                if not in_hunk:
                    cur.hunks.append(Hunk())
                    in_hunk = True
                cur.hunks[-1].lines.append(line)
            elif cur.kind == "add":
                cur.add_lines.append(line)

    if cur:
        ops.append(cur)
    return ops


def normalize_path(p: str) -> str:
    """Strip leading 'projects/Symbiont/' if present."""
    prefix = "projects/Symbiont/"
    if p.startswith(prefix):
        return p[len(prefix):]
    return p


def apply_op(op: FileOp, root: Path, dry_run: bool = False) -> tuple[bool, str]:
    rel_path = normalize_path(op.path)
    target = root / rel_path

    if op.kind == "add":
        if target.exists():
            return True, f"SKIP add (exists): {rel_path}"
        # add_lines: each prefixed with '+'; preserve trailing newline behavior
        content_lines = []
        for line in op.add_lines:
            if line.startswith("+"):
                content_lines.append(line[1:])
            elif line == "":
                content_lines.append("")
            else:
                content_lines.append(line)
        content = "\n".join(content_lines)
        if not content.endswith("\n"):
            content += "\n"
        if dry_run:
            return True, f"WOULD ADD: {rel_path} ({len(content_lines)} lines)"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        return True, f"ADDED: {rel_path}"

    if op.kind == "delete":
        if dry_run:
            return True, f"WOULD DELETE: {rel_path}"
        target.unlink(missing_ok=True)
        return True, f"DELETED: {rel_path}"

    if op.kind == "update":
        if not target.exists():
            return False, f"ERROR (not found): {rel_path}"
        original = target.read_text(encoding="utf-8")
        # Detect line ending
        eol = "\r\n" if "\r\n" in original else "\n"
        content = original.replace("\r\n", "\n")
        for i, hunk in enumerate(op.hunks):
            new_content, ok = _apply_hunk(content, hunk)
            if not ok:
                return False, f"ERROR (hunk {i + 1}/{len(op.hunks)} failed): {rel_path}"
            content = new_content
        # Restore line endings
        if eol == "\r\n":
            content = content.replace("\n", "\r\n")
        if dry_run:
            return True, f"WOULD UPDATE: {rel_path} ({len(op.hunks)} hunks ok)"
        target.write_text(content, encoding="utf-8", newline="")
        return True, f"UPDATED: {rel_path} ({len(op.hunks)} hunks)"

    return False, f"UNKNOWN op kind: {op.kind}"


def _apply_hunk(content: str, hunk: Hunk) -> tuple[str, bool]:
    lines = content.split("\n")

    # Build search lines (context + remove) and replace lines (context + add)
    search: list[str] = []
    replace: list[str] = []
    for l in hunk.lines:
        if l.startswith(" "):
            search.append(l[1:])
            replace.append(l[1:])
        elif l.startswith("-"):
            search.append(l[1:])
        elif l.startswith("+"):
            replace.append(l[1:])
        elif l == "":
            # blank context line
            search.append("")
            replace.append("")

    if not search:
        return content, False

    # Narrow search by anchors
    start = 0
    for anchor in hunk.anchors:
        anchor = anchor.rstrip()
        if not anchor:
            continue
        for j in range(start, len(lines)):
            if lines[j].rstrip() == anchor:
                start = j + 1
                break

    # Find search block
    n = len(search)
    for i in range(start, len(lines) - n + 1):
        if all(lines[i + k].rstrip() == search[k].rstrip() for k in range(n)):
            new_lines = lines[:i] + replace + lines[i + n:]
            return "\n".join(new_lines), True

    return content, False


# ── CLI ───────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print("Usage: apply_patch.py <root_dir> <patch_file> [--dry-run]")
        sys.exit(1)
    root = Path(sys.argv[1]).resolve()
    patch_file = Path(sys.argv[2])
    dry_run = "--dry-run" in sys.argv[3:]

    text = patch_file.read_text(encoding="utf-8")
    ops = parse_patch(text)
    if not ops:
        print(f"WARNING: no operations parsed from {patch_file}")
        sys.exit(2)

    fail = 0
    for op in ops:
        ok, msg = apply_op(op, root, dry_run=dry_run)
        prefix = "  " if ok else "X "
        print(f"{prefix}{msg}")
        if not ok:
            fail += 1

    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
