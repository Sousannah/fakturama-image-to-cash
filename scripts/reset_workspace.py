"""Restore the pristine workspace so a run starts from a known state.

    python scripts/reset_workspace.py            # restore
    python scripts/reset_workspace.py --launch   # restore and start Fakturama

Refuses to delete anything unless a snapshot exists, and always moves the
current workspace aside rather than deleting it outright.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from f2c.config import REPO_ROOT, SETTINGS  # noqa: E402
from f2c.ui.app import FakturamaApp, kill_all, running_pids  # noqa: E402

SNAPSHOT = REPO_ROOT / "fixtures" / "workspace_pristine.zip"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launch", action="store_true", help="start Fakturama afterwards")
    parser.add_argument("--keep", type=int, default=3, help="how many old workspaces to keep")
    args = parser.parse_args()

    workspace = Path(SETTINGS.workspace)

    if not SNAPSHOT.exists():
        print("no snapshot at %s - run scripts/snapshot_workspace.py first" % SNAPSHOT)
        return 2

    if running_pids():
        print("closing Fakturama ...")
        kill_all()
        for _ in range(40):
            if not running_pids():
                break
            time.sleep(0.5)
        if running_pids():
            print("could not close Fakturama; aborting so the database is not corrupted")
            return 2

    # Only the folders the snapshot actually contains are touched, and each one
    # is moved aside rather than deleted.
    #
    # Per folder, not "the workspace": Fakturama's workspace is whatever folder
    # holds Database/ and Templates/, and here that is a drive root. Renaming
    # THAT is at best impossible and at worst catastrophic - it would carry off
    # everything else on the drive with it.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    with zipfile.ZipFile(SNAPSHOT) as zf:
        restored = sorted({name.split("/")[0] for name in zf.namelist() if "/" in name})
        for folder in restored:
            current = workspace / folder
            if current.exists():
                archived = current.with_name("%s.old-%s" % (folder, stamp))
                current.rename(archived)
                print("moved %s aside -> %s" % (current, archived))
                _prune(current, args.keep)
        workspace.mkdir(parents=True, exist_ok=True)
        zf.extractall(workspace)
    print("restored %s in %s" % (", ".join(restored), workspace))

    if args.launch:
        FakturamaApp().launch()
        print("Fakturama launched")
    return 0


def _prune(folder: Path, keep: int) -> None:
    """Keep only the N most recent archived copies of one restored folder."""
    olds = sorted(
        folder.parent.glob(folder.name + ".old-*"),
        key=lambda p: p.name,
        reverse=True,
    )
    for path in olds[keep:]:
        shutil.rmtree(path, ignore_errors=True)
        print("pruned %s" % path)



if __name__ == "__main__":
    raise SystemExit(main())
