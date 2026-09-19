"""Launching / attaching to Fakturama and normalising its window."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import List, Optional

from ..config import SETTINGS
from ..errors import F2CError
from ..logging_setup import get
from .dpi import make_process_dpi_aware
from .uia import Element, desktop, uia
from .waits import wait_until

log = get("ui.app")

#: The main shell title always contains this.
APP_TITLE_HINT = "Fakturama"
PROCESS_HINTS = ("Fakturama.exe", "fakturama.exe", "Fakturamac.exe")

#: SWT shells are all `SWT_Window<n>`. Matching the class as well as the title
#: matters: a File Explorer window showing a folder called "fakturama-..." also
#: has "fakturama" in its title, and picking it instead produced a very
#: confusing failure before this check existed.
SHELL_CLASS_PREFIX = "SWT_Window"

#: UIA WindowVisualState values
WINDOW_NORMAL, WINDOW_MAXIMIZED, WINDOW_MINIMIZED = 0, 1, 2


def force_foreground(hwnd: int) -> bool:
    """Put `hwnd` on top and give it the input focus.

    Windows refuses `SetForegroundWindow` from a process that does not already
    own the foreground, so this does the AttachThreadInput dance and clears the
    foreground lock first. Shared, because anything that drives a window with
    physical input needs it - the automation before a click, and
    `scripts/capture_demo.py` before it presses a button in the front end.
    """
    try:
        import win32api
        import win32con
        import win32gui
        import win32process
    except Exception:  # pragma: no cover
        return False

    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        _release_foreground_lock()

        target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
        this_thread = win32api.GetCurrentThreadId()
        attached = False
        if target_thread != this_thread:
            attached = bool(win32process.AttachThreadInput(this_thread, target_thread, True))
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.SetActiveWindow(hwnd)
        finally:
            if attached:
                win32process.AttachThreadInput(this_thread, target_thread, False)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception as exc:
        log.debug("could not take the foreground for %s: %s", hwnd, exc)
        return False


class FakturamaApp:
    """Owns the process and hands out the main-window element."""

    def __init__(self, exe: Optional[Path] = None):
        self.exe = exe or SETTINGS.fakturama_exe
        self.process: Optional[subprocess.Popen] = None
        self._window: Optional[Element] = None
        self.dpi_mode = make_process_dpi_aware()
        log.debug("DPI awareness: %s", self.dpi_mode)

    # ------------------------------------------------------------------ #
    def attach(self, timeout: Optional[float] = None) -> Element:
        """Find an already-running instance."""
        timeout = timeout or 10.0
        win = wait_until(
            lambda: _find_main_window(),
            timeout=timeout,
            what="a running Fakturama main window",
        )
        self._window = win
        log.info("attached to %r", win.name)
        return win

    def launch(self, timeout: Optional[float] = None) -> Element:
        """Attach if running, otherwise start the executable and wait."""
        existing = _find_main_window()
        if existing is not None:
            self._window = existing
            log.info("Fakturama already running; attaching to %r", existing.name)
            return existing

        if not self.exe:
            raise F2CError(
                "F2C_FAKTURAMA_EXE is not set and no running instance was found "
                "(see .env.example)"
            )
        if not Path(self.exe).exists():
            raise F2CError("Fakturama executable not found: %s" % self.exe)

        log.info("launching %s", self.exe)
        self.process = subprocess.Popen([str(self.exe)], cwd=str(Path(self.exe).parent))
        win = wait_until(
            lambda: _find_main_window(),
            timeout=timeout or SETTINGS.app_start_timeout,
            interval=0.75,
            what="the Fakturama main window",
        )
        self._window = win
        log.info("launched; main window %r", win.name)
        return win

    # ------------------------------------------------------------------ #
    def window(self) -> Element:
        """Re-resolve the main window each call.

        Re-resolving matters: Eclipse recreates parts of its shell when editors
        open and close, and a cached Element can silently go stale.
        """
        win = _find_main_window()
        if win is None:
            if self._window is not None and self._window.exists():
                return self._window
            raise F2CError("the Fakturama main window has disappeared")
        self._window = win
        return win

    def normalise_window(self) -> None:
        """Restore, front and maximise.

        Not a fixed size - just a deterministic, on-screen state. A minimized
        Eclipse shell reports an empty bounding rectangle for every control it
        owns, so restoring first is a precondition for any grounding at all.
        """
        win = self.window()

        try:
            pattern = win.raw.GetWindowPattern()
            if pattern is not None and pattern.WindowVisualState == WINDOW_MINIMIZED:
                log.info("the Fakturama window was minimized; restoring it")
                win.raw.Restore()
        except Exception as exc:
            log.debug("could not restore: %s", exc)

        try:
            win.raw.SetActive()
        except Exception:
            pass

        try:
            pattern = win.raw.GetWindowPattern()
            if pattern is not None and pattern.WindowVisualState != WINDOW_MAXIMIZED:
                win.raw.Maximize()
        except Exception as exc:
            log.debug("could not maximise: %s", exc)

        # one settle after a window-state change; everything else is a polled
        # condition, but the shell relayout has no event worth waiting on
        time.sleep(0.4)

        wait_until(
            lambda: not self.window().rect.is_empty(),
            timeout=15.0,
            what="the Fakturama window to have a visible rectangle",
        )

    def ensure_foreground(self) -> bool:
        """Make sure a Fakturama window owns the foreground before a click.

        Physical clicks go to whatever window is on top at that screen point.
        If the automation is driven from a terminal that shares the screen, the
        click silently lands on the wrong application and the run fails much
        later with a confusing "control not found". This is cheap to check and
        is therefore checked before every click.

        Windows refuses `SetForegroundWindow` from a process that does not own
        the foreground, so the usual AttachThreadInput dance is used. When a
        modal child shell is open, activating the owner activates the modal
        child, which is what we want.
        """
        try:
            import win32api
            import win32con
            import win32gui
            import win32process
        except Exception:  # pragma: no cover
            return False

        pids = set(running_pids())
        if not pids:
            return False

        try:
            current = win32gui.GetForegroundWindow()
            if current:
                _, current_pid = win32process.GetWindowThreadProcessId(current)
                if current_pid in pids:
                    return True
        except Exception:
            pass

        try:
            hwnd = int(self.window().raw.NativeWindowHandle)
        except Exception:
            return False
        if not hwnd:
            return False

        force_foreground(hwnd)

        try:
            wait_until(
                lambda: win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())[1]
                in pids,
                timeout=8.0,
                interval=0.25,
                what="Fakturama to own the foreground",
            )
            return True
        except Exception:
            log.warning(
                "Fakturama did not take the foreground; physical clicks may land "
                "on another window"
            )
            return False

    def close(self, force: bool = False) -> None:
        if self.process is not None and self.process.poll() is None:
            if force:
                self.process.kill()
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=30)
            except Exception:
                self.process.kill()
            log.info("Fakturama process closed")


def _release_foreground_lock() -> None:
    """Persuade Windows to allow a foreground change.

    Windows refuses `SetForegroundWindow` from a process the user has not just
    interacted with. Without this the call silently does nothing, the intended
    window stays behind another one, and the automation's next physical click
    lands on whatever IS in front - which showed up as a dialog "failing to
    open" when in truth the click never reached the application.

    Two standard measures: zero the foreground lock timeout, and synthesise a
    tap of a harmless modifier key, which marks this process as having had
    recent input.
    """
    import ctypes

    SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
    SPIF_SENDCHANGE = 0x0002
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x0002

    try:
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(0), SPIF_SENDCHANGE
        )
    except Exception:
        pass
    try:
        ctypes.windll.user32.keybd_event(VK_MENU, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
def _find_main_window() -> Optional[Element]:
    """The Fakturama *main shell*: an SWT window belonging to a Fakturama process.

    Identified by process and shell class, then disambiguated by title.

    The title check is not decoration. Fakturama's modal dialogs ("Select a
    product", "Select the address") are SWT shells of the same process and the
    same window class, so "first SWT window of the Fakturama process" returns
    whichever one the desktop happens to list first - which changes with z-order
    as dialogs open and close. Picking a dialog as the application window
    silently breaks every selector scoped to `app`, including the one that looks
    for the dialog itself.

    Only the main shell carries "Fakturama" in its title, so that is what
    identifies it; a process-owned shell is kept as a fallback for the case
    where the title has not been set yet during start-up.
    """
    pids = set(running_pids())
    titled: Optional[Element] = None
    untitled: Optional[Element] = None

    for w in desktop().children():
        try:
            if w.control_type != "Window":
                continue
            # Note: no rectangle check here. A minimized window reports
            # (0,0,0,0), and we still want to find it so normalise_window() can
            # restore it rather than the run failing with "not running".
            if not (w.class_name or "").startswith(SHELL_CLASS_PREFIX):
                continue

            is_ours = False
            try:
                is_ours = bool(pids) and w.raw.ProcessId in pids
            except Exception:
                pass

            if APP_TITLE_HINT.casefold() in (w.name or "").casefold():
                if is_ours:
                    return w
                titled = titled or w
            elif is_ours:
                untitled = untitled or w
        except Exception:
            continue

    return titled or untitled


def running_pids() -> List[int]:
    """PIDs of Fakturama processes, used by the workspace reset script."""
    pids: List[int] = []
    try:
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"], text=True, stderr=subprocess.DEVNULL
        )
    except Exception:
        return pids
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if not parts:
            continue
        name = parts[0].strip('"')
        if any(name.casefold() == h.casefold() for h in PROCESS_HINTS):
            try:
                pids.append(int(parts[1]))
            except (IndexError, ValueError):
                pass
    return pids


def kill_all() -> int:
    killed = 0
    for pid in running_pids():
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            killed += 1
        except Exception:
            pass
    return killed
