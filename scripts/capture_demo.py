"""Drive the desktop front end end-to-end and capture the deliverables.

    python scripts/capture_demo.py [--no-record] [--no-reset]

Opens the window, clicks through the three stages, and saves
`docs/screenshots/gui-0*.png` at each one. With `--record` (the default) a
screen recording runs alongside and lands in `docs/recording/`.

The window is captured with `PrintWindow` rather than by grabbing the screen,
because stage 3 hands the foreground to Fakturama - and taking it back to
photograph the front end would break the run it is photographing. The one
figure that has to show the run in progress is cut out of the recording
afterwards for the same reason.
"""
from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

SHOTS = REPO_ROOT / "docs" / "screenshots"
RECORDING = REPO_ROOT / "docs" / "recording"
GUI_TITLE = "Fakturama Image-to-Cash"


# --------------------------------------------------------------------------- #
# window capture
# --------------------------------------------------------------------------- #
def find_window(title_substring: str) -> Optional[int]:
    import win32gui

    found = []

    def cb(handle, _):
        if win32gui.IsWindowVisible(handle):
            text = win32gui.GetWindowText(handle)
            if title_substring.casefold() in text.casefold():
                found.append(handle)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def capture_window(handle: int, dst: Path) -> Optional[Path]:
    """PrintWindow with PW_RENDERFULLCONTENT, so a background window still works."""
    import win32con
    import win32gui
    import win32ui
    from PIL import Image

    left, top, right, bottom = win32gui.GetClientRect(handle)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None

    window_dc = win32gui.GetWindowDC(handle)
    src = win32ui.CreateDCFromHandle(window_dc)
    mem = src.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(src, width, height)
    mem.SelectObject(bitmap)

    ok = ctypes.windll.user32.PrintWindow(wintypes.HWND(handle), mem.GetSafeHdc(), 3)

    info = bitmap.GetInfo()
    bits = bitmap.GetBitmapBits(True)
    image = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1)

    win32gui.DeleteObject(bitmap.GetHandle())
    mem.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(handle, window_dc)

    if not ok:
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    image.save(dst)
    return dst


def click_at(rect, handle: Optional[int] = None) -> None:
    """A real click at the centre of a published widget rectangle.

    Tk exposes no child controls to UI Automation, so the window publishes its
    button rectangles (`run_gui.py --geometry-out`) and this presses them the
    way a person would. The cursor movement is also what makes the recording
    show the buttons being used.
    """
    import win32api
    import win32con
    import win32gui

    if handle is not None and win32gui.GetForegroundWindow() != handle:
        foreground(handle)

    x, y, w, h = rect
    win32api.SetCursorPos((int(x + w / 2), int(y + h / 2)))
    time.sleep(0.35)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.06)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def foreground(handle: int, attempts: int = 6) -> bool:
    """Bring the front end forward, properly.

    `SetForegroundWindow` on its own is refused when the calling process does
    not already own the foreground - which is exactly the situation here - so
    this reuses the automation's own AttachThreadInput helper and then checks
    that it worked, because a click into a window that is not on top lands in
    whatever IS on top.
    """
    from f2c.ui.app import force_foreground

    for _ in range(attempts):
        if force_foreground(handle):
            time.sleep(0.4)
            return True
        time.sleep(0.5)
    print("   WARNING: the front end would not come to the front")
    return False


def frame_from(video: Path, dst: Path, fraction: float) -> Optional[Path]:
    """Save a single frame from a recording, `fraction` of the way through."""
    try:
        import cv2
    except Exception as exc:
        print("   could not extract a frame: %s" % exc)
        return None

    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        print("   %s has no frames" % video)
        return None
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * fraction))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        print("   could not read a frame from %s" % video)
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dst), frame)
    print("   %s (from the recording, %d%% in)" % (dst.name, fraction * 100))
    return dst


def read_geometry(path: Path, timeout: float = 30) -> dict:
    import json

    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        time.sleep(0.5)
    raise SystemExit("the front end never published its geometry to %s" % path)


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--no-reset", action="store_true")
    ap.add_argument("--image", default=str(REPO_ROOT / "fixtures" / "sample_order.png"))
    ap.add_argument("--run-timeout", type=float, default=2700)
    args = ap.parse_args()

    from f2c.ui.app import kill_all

    SHOTS.mkdir(parents=True, exist_ok=True)
    geometry_file = REPO_ROOT / "out" / "gui-geometry.json"
    if geometry_file.exists():
        geometry_file.unlink()

    if not args.no_reset:
        print("== resetting the workspace")
        kill_all()
        subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "reset_workspace.py")], check=True)

    recorder = None
    if not args.no_record:
        RECORDING.mkdir(parents=True, exist_ok=True)
        recorder = subprocess.Popen(
            [sys.executable, str(REPO_ROOT / "scripts" / "record_screen.py"),
             "--out", str(RECORDING / "fakturama-image-to-cash.mp4"),
             "--fps", "5", "--playback-fps", "25"],
            cwd=str(REPO_ROOT),
        )
        time.sleep(4)
        print("== recording started")

    print("== opening the front end")
    subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "run_gui.py"), args.image,
         "--geometry-out", str(geometry_file)],
        cwd=str(REPO_ROOT),
    )

    handle = None
    for _ in range(80):
        handle = find_window(GUI_TITLE)
        if handle:
            break
        time.sleep(0.5)
    if not handle:
        print("the front end never appeared")
        return 2

    geometry = read_geometry(geometry_file)
    foreground(handle)
    time.sleep(3.5)
    capture_window(handle, SHOTS / "gui-01-start.png")
    print("   gui-01-start.png")

    for key, done_key, shot, budget in (
        ("ocr", "ocr_done", "gui-02-ocr.png", 300),
        ("llm", "llm_done", "gui-03-groq.png", 300),
    ):
        print("== clicking the %s button" % key)
        foreground(handle)
        time.sleep(0.6)
        click_at(geometry[key], handle)
        time.sleep(3)
        if not wait_for_stage(done_key, budget):
            print("   %s did not finish within %.0fs" % (key, budget))
        time.sleep(2.5)
        capture_window(handle, SHOTS / shot)
        print("   %s" % shot)

    print("== clicking the Fakturama button - this drives the live application")
    foreground(handle)
    time.sleep(0.6)
    click_at(geometry["ui"], handle)

    print("== waiting for the run to finish (up to %.0f minutes)" % (args.run_timeout / 60))
    deadline = time.time() + args.run_timeout
    while time.time() < deadline:
        state = read_state()
        if state.get("run_dir") and not state.get("busy", True):
            break
        time.sleep(10)
    time.sleep(6)
    capture_window(handle, SHOTS / "gui-05-done.png")
    print("   gui-05-done.png")

    if recorder is not None:
        subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "record_screen.py"), "--stop"],
                       cwd=str(REPO_ROOT))
        recorder.wait(timeout=180)
        print("== recording stopped")

        # The mid-run shot of the front end cannot be taken while the run is
        # going: Fakturama owns the foreground, and PrintWindow on an obscured
        # Tk window returns a clipped image. Taking the foreground back would
        # break the run being photographed. So the "stage 3 in progress" figure
        # comes out of the recording, which was watching the whole time.
        frame_from(RECORDING / "fakturama-image-to-cash.mp4",
                   SHOTS / "gui-04-running.png", 0.55)

    print("== done; the front end is left open")
    return 0


def read_state() -> dict:
    """What the front end published about itself, if anything yet."""
    import json

    path = REPO_ROOT / "out" / "gui-geometry-state.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def wait_for_stage(key: str, budget: float) -> bool:
    """Wait until the front end says the named stage finished and it is idle."""
    deadline = time.time() + budget
    while time.time() < deadline:
        state = read_state()
        if state.get(key) and not state.get("busy", True):
            return True
        time.sleep(1.5)
    return False


if __name__ == "__main__":
    sys.exit(main())
