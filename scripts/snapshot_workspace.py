"""Snapshot a pristine Fakturama workspace so runs can be repeated.

The flow creates master data (Debtor, VAT, Payment Method, Products). Run it
twice against the same workspace and the second run takes the "already exists"
branch, which means the creation branches stop being exercised. Snapshot once,
then `reset_workspace.py` before every run.

    python scripts/snapshot_workspace.py
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from f2c.config import REPO_ROOT, SETTINGS  # noqa: E402
from f2c.ui.app import running_pids  # noqa: E402

TARGET = REPO_ROOT / "fixtures" / "workspace_pristine.zip"

#: Only these subfolders are Fakturama's own data. A Fakturama workspace can be
#: a drive root (this installation chose `D:\`), so never walk the whole tree.
OWNED = ("Database", "Templates")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-running", action="store_true",
                        help="snapshot even while Fakturama is open (see note below)")
    args = parser.parse_args()

    workspace = Path(SETTINGS.workspace)
    if not workspace.exists():
        print("workspace not found: %s" % workspace)
        print("set F2C_WORKSPACE, or start Fakturama once so it creates one")
        return 2

    folders = [workspace / name for name in OWNED if (workspace / name).is_dir()]
    if not folders:
        print("no Fakturama data folders (%s) under %s" % (", ".join(OWNED), workspace))
        return 2

    pids = running_pids()
    if pids and not args.allow_running:
        print("Fakturama is running (pid %s)." % pids)
        print("Close it for a fully consistent snapshot, or pass --allow-running:")
        print("  HSQLDB keeps committed-but-uncheckpointed rows in Database.log,")
        print("  which is copied too, so a hot snapshot is still restorable.")
        return 2

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(TARGET, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder in folders:
            for path in folder.rglob("*"):
                if path.is_file():
                    try:
                        zf.write(path, path.relative_to(workspace))
                        count += 1
                    except (PermissionError, OSError) as exc:
                        print("  skipped %s (%s)" % (path.name, exc))
    print("snapshotted %d files from %s -> %s (%.1f MB)"
          % (count, ", ".join(str(f) for f in folders), TARGET, TARGET.stat().st_size / 1e6))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
