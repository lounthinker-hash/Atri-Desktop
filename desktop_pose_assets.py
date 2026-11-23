"""Lossless, pre-sized originals used by the public desktop-size/DPI combinations."""
from functools import lru_cache
import struct
from zipfile import BadZipFile, ZipFile

from PIL import Image


@lru_cache(maxsize=4)
def _archive(path):
    try:
        return ZipFile(path)
    except (FileNotFoundError, BadZipFile):
        return None


def read_plate(directory, key, mode="RGBA", archive_name="pose-plates.zip"):
    pack = _archive(directory / archive_name)
    if pack is not None:
        try:
            data = pack.read(key)
        except (KeyError, BadZipFile):
            return None
        width, height = struct.unpack_from("<II", data)
        return Image.frombytes(mode, (width, height), data[8:])
    return None


@lru_cache(maxsize=8)
def prepare_small_walk(width,height):
    """Decode all three original sheets before the small actor takes a step."""
    import threading
    def prepare():
        from desktop_stride import warm_step_art, _front_upper
        try:
            warm_step_art()
            _front_upper(width,height)
        except (OSError,ValueError,KeyError):
            return
    threading.Thread(target=prepare,name="Atri walking artwork",daemon=True).start()


class PoseWarmup:
    """Fill the pure Pillow/NumPy caches after the first on-screen render."""
    def __init__(self, renderer, sprites):
        from threading import Lock
        self.renderer = renderer
        self.poses = {}
        for sprite in sprites:
            if sprite.get("dress") == "d1" and sprite.get("origin") == "scene":
                self.poses.setdefault(sprite["pose"], sprite)
        self.height = None
        self.worker = None
        self.lock = Lock()

    def request(self, height):
        from threading import Thread, current_thread
        if self.height == height or current_thread() is self.worker:
            return
        with self.lock:
            self.height = height
            if self.worker is None:
                self.worker = Thread(target=self._run, name="Atri pose preparation", daemon=True)
                self.worker.start()

    def _run(self):
        import time
        try:
            import ctypes
            system = ctypes.windll.kernel32
            system.GetCurrentThread.restype = ctypes.c_void_p
            system.SetThreadPriority.argtypes = (ctypes.c_void_p, ctypes.c_int)
            system.SetThreadPriority(system.GetCurrentThread(), -1)
            while True:
                height = self.height
                time.sleep(.10)
                if height == self.height:
                    self._prepare(height)
                with self.lock:
                    if self.height == height:
                        self.worker = None
                        return
        except (OSError, ValueError, KeyError):
            # A partial or older artwork installation still uses live rendering.
            with self.lock:
                self.worker = None

    def _prepare(self, height):
        import time
        from desktop_kick_surface import _geometry, _paint_channels
        from desktop_motion import _registered_fields
        from desktop_turn import _foot_profiles

        images = {}
        for pose, mood in (("p1","neutral"), ("p3","happy"), ("p4","neutral"), ("p2","relaxed")):
            if self.height != height:
                return
            h = round(.922*height) if pose=="p3" else height
            image = self.renderer.render(self.poses[pose], mood, height=h)
            images[pose] = image
            if pose in ("p1","p4"):
                _foot_profiles(image)
            if pose != "p4":
                _paint_channels(pose, image.size, image.tobytes())
            time.sleep(.008)
        for pose in ("p1","p2"):
            if self.height != height:
                return
            front, side = images[pose], images["p3"]
            _, _, origin, size, *_ = _geometry(pose, front.size, side.size)
            for name, frame, dx, dy in ((pose,front,6 if pose=="p2" else 0,0), ("p3",side,11,3)):
                left = -frame.width/2 + dx*height/390
                top = -frame.height + dy*height/390
                _registered_fields(*size, (name,*frame.size, round(left-origin[0]), round(top-origin[1])))
            time.sleep(.008)
