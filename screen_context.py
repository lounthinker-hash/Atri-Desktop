"""Observe the foreground window. OCR targets contain bounds in physical desktop pixels."""
import base64
from dataclasses import dataclass
from pathlib import Path
import os
import re
import secrets
import subprocess
import threading
import time

_WORKER = Path(__file__).resolve().parent / "desktop" / "observer" / "ScreenContextWorker.exe"
_KINDS = {"math", "study", "coding", "coding_error", "coding_success", "writing", "video", "music", "game", "system", "idle", "unknown"}
_REASONS = {"content_match", "title_match", "app_match", "inactive", "no_match", "build_diagnostic", "own_window",
            "privacy", "changed", "unavailable"}
_SOURCES = {"ocr", "title", "process", "system", "window", "none"}
_SAMPLE_KEY = base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def _empty(reason):
    return {"kind": "unknown", "confidence": 0.0, "reason": reason, "source": "none"}


def _target(rect_field, monitor_field, device, handle):
    rect = list(map(int, rect_field.split(",")))
    monitor = list(map(int, monitor_field.split(",")))
    x, y, w, h = rect
    mx, my, mw, mh = monitor
    if (handle > 0 and re.fullmatch(r"\\\\\.\\DISPLAY\d+", device)
            and 0 < w <= mw and 0 < h <= mh and mx <= x <= mx+mw-w and my <= y <= my+mh-h):
        return {"rect_px": rect, "monitor_px": monitor, "monitor": device, "window": handle}


@dataclass
class _Candidate:
    target: dict
    tier: int
    tag: str
    x: int
    y: int
    highlighted: bool


class _Selection:
    """Remember only the previous geometry and ephemeral line aliases for one window."""

    def __init__(self):
        self.guard = threading.Lock()
        self.context = None
        self.at = 0.0
        self.previous = []
        self.selected = None

    def clear(self):
        with self.guard:
            self.context, self.previous, self.selected = None, [], None

    def choose(self, context, options):
        with self.guard:
            now = time.monotonic()
            before = self.previous if self.context == context and now - self.at < 90 else []
            # Nearest one-to-one pairing also handles duplicate equations/diagnostics.
            pairs = sorted((max(abs(a.x-b.x), abs(a.y-b.y)), i, j)
                           for i, a in enumerate(options) for j, b in enumerate(before)
                           if a.tag == b.tag)
            matches, used = {}, set()
            for distance, i, j in pairs:
                if i not in matches and j not in used:
                    matches[i] = (distance, j)
                    used.add(j)

            highest = max(a.tier for a in options)
            eligible = [i for i, a in enumerate(options) if a.tier == highest]
            eligible = [i for i in eligible if options[i].highlighted] or eligible
            def priority(i):
                if i not in matches:
                    return 3
                distance, j = matches[i]
                height = options[i].target["rect_px"][3]
                # Ignore WinRT's one- or two-pixel bounding-box jitter.
                moved = distance >= max(14, round(.65*height))
                return 2 if moved else 1 if j == self.selected else 0
            # When every existing line moves together (e.g. scroll/scale), the
            # previously chosen one wins equal priority; unchanged pages stay put.
            best = max(eligible, key=lambda i: (priority(i),
                       i in matches and matches[i][1] == self.selected))
            self.context, self.at, self.previous, self.selected = context, now, options, best
            return options[best].target


_selection = _Selection()


def _decode(payload, selection):
    fields = payload.strip().split("|")
    kind, value, reason, source = fields[:4]
    confidence = float(value)
    if kind not in _KINDS or reason not in _REASONS or source not in _SOURCES or not 0 <= confidence <= 1:
        return _empty("unavailable")
    answer = {"kind": kind, "confidence": confidence, "reason": reason, "source": source}
    if reason == "privacy" or kind == "idle":
        selection.clear()
    if len(fields) < 8 or source != "ocr" or reason not in ("content_match", "build_diagnostic"):
        return answer
    handle = int(fields[7])
    target = _target(*fields[4:7], handle)
    if target is None:
        return answer
    if len(fields) == 10 and fields[8] == "v2":
        options = []
        for item in fields[9].split(";")[:8]:
            bounds, screen, device, tier, tag, point = item.split("/")
            entry = _target(bounds, screen, device, handle)
            x, y, *focus = map(int, point.split(","))
            if entry and tier in ("1", "2", "3", "4") and re.fullmatch(r"[0-9A-F]{16}", tag):
                options.append(_Candidate(entry, int(tier), tag, x, y, focus == [1]))
        if options:
            target = selection.choose((handle, kind), options)
    answer["target"] = target
    return answer


def _observe(expected_window=0):
    if os.name != "nt":
        return _empty("unavailable")
    try:
        result = subprocess.run(
            [str(_WORKER), str(os.getpid()), str(expected_window), "--targets-v2"],
            input=_SAMPLE_KEY+"\n", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="ascii", timeout=8, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return _decode(result.stdout, _selection)
    except subprocess.TimeoutExpired:
        return _empty("timeout")
    except (OSError, ValueError, subprocess.SubprocessError):
        return _empty("unavailable")


def observe_screen():
    """Classify current activity once. Call off the GUI thread. Maximum wait is 8 s."""
    return _observe()
