"""A click-through scenery window registered to Atri's feet on the desktop."""

from dataclasses import dataclass,field
from datetime import datetime
from pathlib import Path
import ctypes
import json
import math
import os
import time

import numpy as np
from PIL import Image
from PySide6.QtCore import QEvent, QFileSystemWatcher, QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QActionGroup, QImage, QPainter
from PySide6.QtWidgets import QWidget


@dataclass
class Scene:
    image: QImage
    feet: tuple
    actor_height: float
    source: str
    label: str = ""
    variants: dict = field(default_factory=dict)

    def bounds(self, height):
        h = round(height / self.actor_height)
        w = round(h * self.image.width() / self.image.height())
        return QRect(-round(w * self.feet[0]), -round(h * self.feet[1]), w, h)


_DEFAULTS = {
    "classroom": ("assets/stage/classroom.webp", (180, 15, 1034, 719), (.59, .925), .45),
    "coast": ("assets/scenery/bg007.webp", (219, 79, 991, 717), (.38, .925), .44),
}
_COAST_ACTIVITIES = {"crab", "morning", "hum", "watch", "rest"}
_SCHOOL_ACTIVITIES = {"class", "science", "notes", "evening_log", "lamps",
                      "cook", "testing", "retry", "passed"}


def _qimage(pil):
    rgba = pil.convert("RGBA")
    raw = rgba.tobytes()
    return QImage(raw, rgba.width, rgba.height, rgba.width * 4,
                  QImage.Format.Format_RGBA8888).copy()


def _source_fallback(root, key):
    source, crop, feet, peak = _DEFAULTS[key]
    with Image.open(root / source) as raw:
        result = raw.crop(crop).convert("RGBA")
    # A soft rectangle keeps the floor under the feet, leaving transparent
    # corners. Shafts of window light and trees retain the original colors.
    y, x = np.mgrid[-1:1:complex(result.height), -1:1:complex(result.width)]
    radius = (np.abs(x) ** 4 + np.abs(y) ** 4) ** .25
    u = np.clip((1 - radius) / .29, 0, 1)
    alpha = peak * 255 * u * u * (3 - 2 * u)
    result.putalpha(Image.fromarray(np.rint(alpha).astype(np.uint8)))
    return Scene(_qimage(result), feet, .75, source)


def _scenes(root):
    directory = root / "assets" / "ambience"
    try:
        manifest=json.loads((directory / "manifest.json").read_text(encoding="utf-8-sig"))
        items=manifest["scenes"]
    except (OSError, ValueError, KeyError):
        items = {}
    result = {}
    if not isinstance(items,dict):
        items={}
    for name in dict.fromkeys([*items,*_DEFAULTS]):
        try:
            entry = items[name]
            path = directory / entry["file"]
            feet = tuple(float(v) for v in entry["footAnchor"])
            height = float(entry.get("actorHeight", .84))
            if len(feet) != 2 or not all(0 <= n <= 1 for n in feet) or not .3 <= height <= 1.4:
                raise ValueError("invalid registration")
            with Image.open(path) as image:
                result[name] = Scene(_qimage(image), feet, height, str(path.relative_to(root)),
                                     str(entry.get("label") or name))
            for period,record in entry.get("variants",{}).items():
                if isinstance(record,dict) and record.get("file"):
                    variant=QImage(str(directory/record["file"]))
                    if not variant.isNull() and variant.size()==result[name].image.size():
                        result[name].variants[period]=variant
        except (KeyError, TypeError, ValueError, OSError):
            if name in _DEFAULTS:
                result[name] = _source_fallback(root, name)
    return result


def _place_behind(scenery, actor):
    # An independent top-level window can sit immediately below the actor.
    # A native owned tool window would always stack above its owner on Win32.
    if os.name != "nt" or scenery.windowHandle().screen() is None:
        return
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.platformName() != "windows":
        return
    user = ctypes.windll.user32
    user.SetWindowPos(ctypes.c_void_p(int(scenery.winId())), ctypes.c_void_p(int(actor.winId())),
                      0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0200)


def suppress_native_border(widget):
    from PySide6.QtGui import QGuiApplication
    if os.name=="nt" and QGuiApplication.platformName()=="windows" and widget.windowHandle():
        hwnd=ctypes.c_void_p(int(widget.winId()))
        for attribute,value in ((2,1),(33,1),(34,0xFFFFFFFE)):
            # DWMNCRP_DISABLED, DWMWCP_DONOTROUND, DWMWA_COLOR_NONE.
            data=ctypes.c_uint32(value)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd,attribute,ctypes.byref(data),ctypes.sizeof(data))


class Ambience(QWidget):
    """A scenery companion with no input surface, keyboard focus or taskbar icon."""

    def __init__(self, owner, root, clock=None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
        self.setWindowTitle("桌面亚托莉 · 景物")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.owner = owner
        self.walk_offset=QPointF()
        self.root=Path(root)
        self.clock=clock or (lambda:datetime.now().astimezone())
        self.scenes = _scenes(self.root)
        self.catalog=self.root/"assets/ambience/manifest.json"
        self.watcher=QFileSystemWatcher([str(self.catalog.parent)],self)
        self.reload_timer=QTimer(self,singleShot=True,interval=400)
        self.watcher.directoryChanged.connect(lambda *_:self.reload_timer.start())
        self.watcher.fileChanged.connect(lambda *_:self.reload_timer.start())
        self.reload_timer.timeout.connect(self.reload_scenes)
        if self.catalog.is_file():
            self.watcher.addPath(str(self.catalog))
        self.enabled = owner.prefs.value("ambience/enabled", True, type=bool)
        self.mode = owner.prefs.value("ambience/scene", "auto")
        if self.mode not in {"auto", *self.scenes}:
            self.mode = "auto"
        self.current = self.mode if self.mode in self.scenes else "coast"
        self.last_routine = None
        self.left_routine_at = None
        self.periods=self.read_periods()
        self.period=self.current_period()
        self.start_mix = {(self.current,self.period):1.}
        self.change_time = 0.
        self.timer = QTimer(self, interval=32)
        self.timer.timeout.connect(self._tick)
        self.calendar_timer=QTimer(self,interval=1000)
        self.calendar_timer.timeout.connect(self.check_clock)
        self.calendar_timer.start()
        self.return_to_shore = QTimer(self, singleShot=True, interval=3050)
        self.return_to_shore.timeout.connect(lambda: self.follow_activity(self.owner.active_behavior))
        self.rectangles = {}
        self.actor_height = 0
        self.rendered_layers={}
        self.pending = False
        owner.installEventFilter(self)
        self.installEventFilter(self)
        owner.destroyed.connect(self.close)

    def add_menu(self, menu):
        enabled = menu.addAction("显示半透明原作背景")
        enabled.setCheckable(True)
        enabled.setChecked(self.enabled)
        enabled.toggled.connect(self.set_enabled)
        choices = self.scene_menu = menu.addMenu("背景场景")
        self.scene_group = QActionGroup(choices)
        self.populate_menu()

    def populate_menu(self):
        choices=self.scene_menu
        choices.clear()
        self.scene_actions = {}
        fallback={"classroom":"窗边教室","coast":"海边草坡"}
        for key,label in [("auto","随活动变化")]+[(k,s.label or fallback.get(k,k)) for k,s in self.scenes.items()]:
            action = choices.addAction(label)
            self.scene_actions[key] = action
            action.setCheckable(True)
            action.setChecked(key == self.mode)
            self.scene_group.addAction(action)
            action.triggered.connect(lambda checked=False, key=key: self.set_mode(key))

    def reload_scenes(self):
        try:
            if not json.loads(self.catalog.read_text(encoding="utf-8-sig")).get("scenes"):
                return
        except (OSError,ValueError,AttributeError):
            return
        if str(self.catalog) not in self.watcher.files():
            self.watcher.addPath(str(self.catalog))
        self.scenes=_scenes(self.root)
        if self.mode not in self.scenes:
            self.mode="auto"
        if self.current not in self.scenes:
            self.current="coast"
        self.periods=self.read_periods()
        self.period=self.current_period()
        self.start_mix={(self.current,self.period):1.}
        self.actor_height=0
        self.populate_menu()
        self.follow_activity(self.owner.active_behavior)
        self.sync_geometry()
        self.update()

    def read_periods(self):
        try:
            spec=json.loads(self.catalog.read_text(encoding="utf-8-sig"))["time_of_day"]
            return sorted((datetime.strptime(spec["periods"][key]["start_local"],"%H:%M").time(),key)
                          for key in spec["order"])
        except (ValueError,OSError,TypeError,KeyError):
            return []

    def current_period(self):
        if not self.periods:return None
        now=self.clock().time().replace(tzinfo=None)
        return next((key for start,key in reversed(self.periods) if start<=now),self.periods[-1][1])

    def check_clock(self):
        period=self.current_period()
        if period!=self.period:
            self.start_mix=self.mix()
            self.period=period
            self.change_time=time.monotonic()
            if self.isVisible():self.timer.start()
            self.update()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if not self.enabled:self.walk_offset=QPointF()
        self.owner.prefs.setValue("ambience/enabled", self.enabled)
        self.sync_geometry()

    def set_mode(self, mode):
        self.mode = mode
        self.owner.prefs.setValue("ambience/scene", mode)
        self.follow_activity(self.owner.active_behavior)

    def follow_activity(self, routine):
        if self.mode in self.scenes:
            key = self.mode
        elif routine=="lamps" and "dusk" in self.scenes:
            key="dusk"
        elif routine=="hum" and any("花海" in scene.label for scene in self.scenes.values()):
            key=next(k for k,s in self.scenes.items() if "花海" in s.label)
        elif routine in _COAST_ACTIVITIES:
            key = "coast"
        elif routine in _SCHOOL_ACTIVITIES:
            key = "classroom"
        elif routine is None:
            # Leave the book's setting in view while Atri puts it away.
            # The following stretch of outdoor idle returns to the grassy shore.
            if self.last_routine is not None:
                self.left_routine_at = time.monotonic()
                self.return_to_shore.start()
            self.last_routine = None
            if self.left_routine_at is not None and time.monotonic() - self.left_routine_at < 3:
                return
            key = "coast"
        else:
            return
        if routine is not None or self.mode != "auto":
            self.return_to_shore.stop()
        self.last_routine = routine
        if key != self.current:
            self.start_mix = self.mix()
            self.current = key
            self.change_time = time.monotonic()
            if self.isVisible():
                self.timer.start()
            self.update()

    def mix(self):
        amount = min(1., max(0., (time.monotonic() - self.change_time) / .55))
        eased = amount * amount * (3 - 2 * amount)
        target=(self.current,self.period)
        return {k:v for k in self.start_mix.keys()|{target}
                if (v:=self.start_mix.get(k,0.)*(1-eased)+(k==target)*eased)>.00001}

    def _tick(self):
        self.update()
        if time.monotonic() - self.change_time >= .55:
            self.timer.stop()

    def walk_to(self,position):
        """Leave the scenery planted for a short step, pan only on a longer walk."""
        delta=position-self.owner.pos()
        if delta.isNull():return
        if self.enabled:
            height=self.owner.height()-80
            def follow(offset,travel,limit):
                if not travel:return offset
                sign=1 if travel>0 else -1
                current=offset*sign
                # If she walks back into view, the landscape stays where it
                # was left. Beyond the central area it starts tracking smoothly.
                free=height*.21
                end=current+abs(travel)
                if end<=free:return end*sign
                old=max(free,current)
                width=limit-free
                return (limit-(limit-old)*math.exp(-(end-old)/width))*sign
            self.walk_offset=QPointF(follow(self.walk_offset.x(),delta.x(),height*.47),
                                     follow(self.walk_offset.y(),delta.y(),height*.57))
        self.owner.move(position)

    def sync_geometry(self):
        self.pending = False
        self.check_clock()
        if not self.enabled or not self.owner.isVisible() or self.owner.closed:
            self.timer.stop()
            self.hide()
            return
        height = self.owner.height() - 80
        if height <= 0:
            return
        if self.actor_height != height:
            if self.actor_height:
                self.walk_offset*=height/self.actor_height
            self.rendered_layers.clear()
            rects = {k: scene.bounds(height) for k, scene in self.scenes.items()}
            self.area=QRect()
            for rect in rects.values():
                self.area=self.area.united(rect)
            self.rectangles = {k: rect.translated(-self.area.topLeft()) for k, rect in rects.items()}
            self.actor_height = height
        foot = self.owner.mapToGlobal(QPoint(self.owner.width()//2,self.owner.height()-getattr(self.owner,"foot_margin",5)))
        foot-=self.walk_offset.toPoint()
        self.setGeometry(self.area.translated(foot))
        if not self.isVisible():
            self.show()
        _place_behind(self, self.owner)

    def eventFilter(self, watched, event):
        if event.type()==QEvent.Type.Show or (event.type()==QEvent.Type.WinIdChange and watched.isVisible()):
            suppress_native_border(watched)
        if watched is self.owner:
            kind = event.type()
            if kind in (QEvent.Type.Hide, QEvent.Type.Close):
                self.timer.stop()
                self.hide()
            elif kind in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.ZOrderChange):
                if not self.pending:
                    self.pending = True
                    QTimer.singleShot(0, self.sync_geometry)
        return False

    def paintEvent(self, event):
        mix = self.mix()
        ratio=self.devicePixelRatioF()
        w,h=round(self.width()*ratio),round(self.height()*ratio)
        layers=[]
        for (key,period),weight in mix.items():
            if key not in self.rectangles:continue
            index=(key,period,w,h,ratio)
            layer=self.rendered_layers.get(index)
            if layer is None:
                layer=QImage(w,h,QImage.Format.Format_ARGB32_Premultiplied)
                layer.setDevicePixelRatio(ratio)
                layer.fill(Qt.GlobalColor.transparent)
                scene=self.scenes[key]
                with QPainter(layer) as draw:
                    draw.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    draw.drawImage(self.rectangles[key],scene.variants.get(period,scene.image))
                self.rendered_layers[index]=layer
            layers.append((layer,weight))
        if not layers:return
        frame=layers[0][0]
        if len(layers)>1:
            # Weight the already premultiplied color and alpha channels together.
            # The fixed-point weights telescope to 256, even after a retarget.
            total=sum(weight for _,weight in layers)
            pixels=np.full((h,w,4),128,dtype=np.uint16)
            cumulative=0.
            previous=0
            for layer,weight in layers:
                cumulative+=weight
                end=round(256*cumulative/total)
                raw=np.frombuffer(layer.constBits(),np.uint8).reshape(h,w,4)
                pixels+=np.multiply(raw,end-previous,dtype=np.uint16)
                previous=end
            rgba=(pixels>>8).astype(np.uint8)
            frame=QImage(rgba.data,w,h,w*4,QImage.Format.Format_ARGB32_Premultiplied)
            frame.setDevicePixelRatio(ratio)
        with QPainter(self) as painter:
            painter.drawImage(QPoint(0,0),frame)
