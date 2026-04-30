"""Apply all 23 patches in time order, stopping on first failure."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from apply_patch import parse_patch, apply_op

ROOT = Path(__file__).parent.parent  # Symbiont/
PATCHES_DIR = Path(__file__).parent / "patches"


def main():
    patches = sorted(PATCHES_DIR.glob("*.patch"))
    print(f"Applying {len(patches)} patches to {ROOT}\n")

    total_ok = 0
    total_fail = 0
    failed_patches: list[str] = []
    for patch_file in patches:
        text = patch_file.read_text(encoding="utf-8")
        ops = parse_patch(text)
        print(f"[{patch_file.name}] {len(ops)} operation(s)")
        for op in ops:
            ok, msg = apply_op(op, ROOT, dry_run=False)
            prefix = "  ok  " if ok else "  FAIL"
            print(f"{prefix} {msg}")
            if ok:
                total_ok += 1
            else:
                total_fail += 1
                failed_patches.append(patch_file.name)
        print()

    print(f"\nDone. ok={total_ok}, fail={total_fail}")
    if failed_patches:
        print("Failed patches (skipped, may need manual review):")
        for f in failed_patches:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
