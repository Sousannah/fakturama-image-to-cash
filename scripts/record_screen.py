"""Record the primary monitor to an mp4 - used to produce the demo recording.

    python scripts/record_screen.py --out docs/recording/demo.mp4 --fps 6
    # ... stops on Ctrl-C, when --seconds elapses, or when the stop file appears
    python scripts/record_screen.py --stop            # signal a running recorder

Deliberately dependency-light: `mss` grabs the frames and OpenCV writes them,
both of which the automation already needs. No ffmpeg, no capture driver.

The writer runs on its own thread with a bounded queue, so a slow encode drops
frames rather than stretching the wall-clock time of what is being recorded -
a recording that runs slower than reality would misrepresent the run.
"""
from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STOP_FILE = REPO_ROOT / "out" / ".recording-stop"


def record(out: Path, fps: int, seconds: float, width: int, monitor: int,
           playback_fps: int = 0) -> int:
    import cv2
    import mss
    import numpy as np

    out.parent.mkdir(parents=True, exist_ok=True)
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    if STOP_FILE.exists():
        STOP_FILE.unlink()

    with mss.mss() as sct:
        box = sct.monitors[monitor]
        scale = min(1.0, width / box["width"]) if width else 1.0
        size = (int(box["width"] * scale) // 2 * 2, int(box["height"] * scale) // 2 * 2)

        # Capturing at `fps` and writing the file at `playback_fps` produces a
        # time-lapse: a 25-minute UI run becomes a few minutes of video without
        # dropping anything that happened. playback_fps == fps is real time.
        playback = playback_fps or fps
        writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), playback, size)
        if not writer.isOpened():
            print("could not open the video writer for %s" % out)
            return 2

        frames: "queue.Queue" = queue.Queue(maxsize=8)
        stop = threading.Event()

        def encode() -> None:
            while not (stop.is_set() and frames.empty()):
                try:
                    writer.write(frames.get(timeout=0.4))
                except queue.Empty:
                    continue
            writer.release()

        thread = threading.Thread(target=encode, daemon=True)
        thread.start()

        print("recording monitor %d at %dx%d, %d fps captured / %d fps written (%.1fx) -> %s"
              % (monitor, size[0], size[1], fps, playback, playback / float(fps), out))
        print("stop with Ctrl-C, or: python scripts/record_screen.py --stop")

        started = time.monotonic()
        period = 1.0 / fps
        count = dropped = 0
        try:
            while True:
                tick = time.monotonic()
                if seconds and tick - started >= seconds:
                    break
                if STOP_FILE.exists():
                    break
                raw = np.array(sct.grab(box))[:, :, :3]      # BGRA -> BGR
                if scale != 1.0:
                    raw = cv2.resize(raw, size, interpolation=cv2.INTER_AREA)
                try:
                    frames.put_nowait(raw)
                    count += 1
                except queue.Full:
                    dropped += 1
                time.sleep(max(0.0, period - (time.monotonic() - tick)))
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            thread.join(timeout=30)
            if STOP_FILE.exists():
                STOP_FILE.unlink()

    length = count / float(playback_fps or fps)
    print("wrote %d frames (%.0fs of video, %d dropped) -> %s  [%.1f MB]"
          % (count, length, dropped, out, out.stat().st_size / 1e6))
    return 0



def shorten(src: Path, dst: Path, every: int, width: int, fps: int) -> int:
    """Make a smaller, shorter copy of a recording.

    A full run is twenty-odd minutes; even time-lapsed it is a large file. This
    keeps every `every`-th frame at a reduced width, which is what makes a
    "short recording" short without cutting anything out of the middle.
    """
    import cv2

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        print("could not open %s" % src)
        return 2
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    scale = min(1.0, width / float(src_w)) if width else 1.0
    size = (int(src_w * scale) // 2 * 2, int(src_h * scale) // 2 * 2)

    dst.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(dst), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    kept = index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if index % every == 0:
            if scale != 1.0:
                frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
            writer.write(frame)
            kept += 1
        index += 1
    cap.release()
    writer.release()
    print("kept %d of %d frames -> %s  [%.1f MB, %.0fs at %d fps]"
          % (kept, index, dst, dst.stat().st_size / 1e6, kept / float(fps), fps))
    return 0



def to_gif(src: Path, dst: Path, frames: int, width: int, ms: int) -> int:
    """Sample `frames` evenly from a recording into an animated GIF.

    A README renders a GIF inline; an mp4 is only a link. So the repository
    carries both, and this makes the GIF from the recording rather than from a
    second run - the two cannot disagree.
    """
    import cv2
    from PIL import Image

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        print("could not open %s" % src)
        return 2
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        print("%s reports no frames" % src)
        return 2

    wanted = [int(round(i * (total - 1) / float(max(1, frames - 1)))) for i in range(frames)]
    picked, index, want = [], 0, set(wanted)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if index in want:
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if width and image.width > width:
                image = image.resize(
                    (width, int(image.height * width / image.width)), Image.LANCZOS)
            # a 256-colour adaptive palette keeps UI text legible at this size
            picked.append(image.convert("P", palette=Image.ADAPTIVE, colors=256))
        index += 1
    cap.release()

    if not picked:
        print("no frames sampled")
        return 2
    dst.parent.mkdir(parents=True, exist_ok=True)
    picked[0].save(dst, save_all=True, append_images=picked[1:],
                   duration=ms, loop=0, optimize=True)
    print("wrote %s  (%d frames, %.1f MB, %.0fs loop)"
          % (dst, len(picked), dst.stat().st_size / 1e6, len(picked) * ms / 1000.0))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "recording" / "demo.mp4")
    ap.add_argument("--fps", type=int, default=6)
    ap.add_argument("--seconds", type=float, default=0, help="0 = until stopped")
    ap.add_argument("--width", type=int, default=1280, help="0 = native")
    ap.add_argument("--monitor", type=int, default=1)
    ap.add_argument("--playback-fps", type=int, default=0,
                    help="frames per second in the FILE; higher than --fps gives a time-lapse")
    ap.add_argument("--stop", action="store_true", help="stop a running recorder")
    ap.add_argument("--shorten", type=Path, default=None,
                    help="post-process this recording into --out instead of recording")
    ap.add_argument("--every", type=int, default=2, help="with --shorten: keep every Nth frame")
    ap.add_argument("--gif", type=Path, default=None,
                    help="post-process this recording into an animated GIF at --out")
    ap.add_argument("--gif-frames", type=int, default=48)
    ap.add_argument("--gif-ms", type=int, default=220, help="milliseconds per GIF frame")
    args = ap.parse_args()

    if args.stop:
        STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
        STOP_FILE.write_text("stop", encoding="utf-8")
        print("stop requested")
        return 0

    if args.gif is not None:
        return to_gif(args.gif, args.out, args.gif_frames, args.width, args.gif_ms)

    if args.shorten is not None:
        return shorten(args.shorten, args.out, args.every, args.width,
                       args.playback_fps or args.fps)

    return record(args.out, args.fps, args.seconds, args.width, args.monitor, args.playback_fps)


if __name__ == "__main__":
    sys.exit(main())
