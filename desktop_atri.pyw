# Copyright (c) 2026 lounthinker
# SPDX-License-Identifier: MIT
"""Windows desktop companion with a locally supplied ATRI-compatible resource pack."""
import ctypes
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sqlite3
import sys
import threading
import time
import winsound
from desktop_delivery import delivery, segment_delivery
from desktop_japanese import DiaryReader, aligned_segment, exact_original, validated_reading
from desktop_speech import Speech
from desktop_routine import ROUTINES, motions_for, quote_key, short_hop

from PySide6.QtCore import QPoint, QProcess, QRectF, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QActionGroup, QBitmap, QCursor, QDesktopServices, QFont, QFontDatabase, QGuiApplication, QIcon, QImage, QPainter, QPixmap, QRegion
from desktop_expression import ExpressionRenderer
from desktop_journal import Journal
from desktop_journal_view import show_journal
from desktop_chat_view import show_chat
from desktop_conversation import RoleConversation
from desktop_fabric import FabricMotion
from desktop_motion import BodyMotion, GESTURES, smooth, warp
from desktop_pose_bridge import bridge_pose
from desktop_reach import reach_pose
from desktop_turn import turn_pose
from desktop_props import PropRenderer
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QMessageBox, QSystemTrayIcon, QWidget


ROOT = Path(__file__).resolve().parent
PORTRAIT = ROOT / "assets/stage/official-atri.png"
SIZES = {"小": 280, "中": 390, "大": 520}
CONTEXT_LABELS = {"math": "数学", "study": "学习", "coding": "编程", "coding_error": "调试遇错",
                  "coding_success": "验证成功", "writing": "写作", "video": "视频",
                  "music": "音乐", "game": "游戏", "system": "系统操作", "idle": "空闲", "unknown": "未识别"}


def instance_lock():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    name = hashlib.sha256(str(ROOT).casefold().encode()).hexdigest()[:16]
    handle = kernel.CreateMutexW(None, False, "Local\\AtriDesktop-" + name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(handle)
        return None
    return handle


def read_data(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8-sig"))


@lru_cache(maxsize=24)
def artwork(filename, height, pixel_ratio):
    image = QImage(str(filename))
    if image.isNull() and filename.suffix.lower() == ".webp":
        try:
            from PIL import Image
            with Image.open(filename) as original:
                rgba = original.convert("RGBA")
                pixels = rgba.tobytes()
                image = QImage(pixels, rgba.width, rgba.height, rgba.width * 4,
                               QImage.Format.Format_RGBA8888).copy()
        except (ImportError, OSError):
            pass
    if image.isNull():
        return QPixmap()
    if image.hasAlphaChannel() and filename == PORTRAIT:
        mask = QBitmap.fromImage(image.createAlphaMask())
        bounds = QRegion(mask).boundingRect()
        if not bounds.isEmpty():
            image = image.copy(bounds)
    art = QPixmap.fromImage(image).scaledToHeight(
        round(height * pixel_ratio), Qt.TransformationMode.SmoothTransformation)
    art.setDevicePixelRatio(pixel_ratio)
    return art


def face_key(face):
    return tuple(sorted(str(face or "").split(":")))


class Companion(QWidget):
    screen_observed = Signal(int, str, float, object)
    media_observed = Signal(int, object)
    conversation_changed = Signal()
    reading_changed = Signal(str,str)

    def __init__(self, *, journal=None, role_invoke=None, diary_review=None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("桌面亚托莉")
        self.setAccessibleName("桌面亚托莉")
        self.setToolTip("单击和亚托莉打招呼，拖动可移动，右键打开菜单")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prefs = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                               "AtriLocal", "DesktopCompanion")
        self.journal = journal if journal is not None else Journal(Path(self.prefs.fileName()).with_name("diary.sqlite3"))
        self.conversation = RoleConversation(self,self.journal,role_invoke,diary_review)
        self.conversation.changed.connect(self.conversation_changed)
        self.conversation.delivered.connect(self.role_delivered)
        self.journal_day = None
        self.next_notebook = self.journal.clock().timestamp()+random.uniform(8,16)*60
        self.size_name = self.prefs.value("size", "中")
        if self.size_name not in SIZES:
            self.size_name = "中"
        self.muted = self.prefs.value("muted", False, type=bool)
        self.voices = read_data("assets/voice/manifest.json")
        self.voice_lookup = {v["id"]: v for v in read_data("assets/voice/all.json")}
        examples_file = ROOT / "tts/original_examples.json"
        self.voice_examples = []
        if examples_file.is_file():
            self.voice_examples = [row for row in read_data("tts/original_examples.json")["examples"]
                                   if row["id"] in self.voice_lookup]
        self.example_by_id = {row["id"]: row for row in self.voice_examples}
        self.behaviors = read_data("data/behaviors.json") if (ROOT / "data/behaviors.json").exists() else []
        self.behavior_names = {item["id"]:item["name"] for item in self.behaviors}
        self.automatic = self.prefs.value("automatic", True, type=bool)
        self.chatter = self.prefs.value("chatter", False, type=bool)
        self.sensing = self.prefs.value("sensing", True, type=bool)
        self.context_generation = 0
        from desktop_media import MediaObserver
        self.media_reader=MediaObserver()
        self.media_state=("none","unknown")
        self.media_busy=False
        self.media_reacted=False
        self.target_visit=None
        self.seen_targets=[]
        self.last_target_rect=None
        self.eye_x=self.eye_y=self.head_look=0.
        self.paper_focus=0.
        self.look_sample_at=time.monotonic()
        self.preview_look=0.
        self.target_elapsed=QTimer(self,singleShot=True)
        self.target_elapsed.timeout.connect(self.advance_target)
        self.journal.desktop_state={}
        self.media_timer=QTimer(self,interval=4000)
        self.media_timer.timeout.connect(self.sample_media)
        self.media_observed.connect(self.receive_media)
        self.sampling = False
        self.closed = False
        self.last_kind = self.confirmed_kind = "unknown"
        self.pending_observation=True
        self.run_length = 0
        self.reacted_kind = None
        self.reaction_times = {}
        self.last_reaction = self.last_auto_audio = -1000.0
        self.last_touch = time.monotonic()
        self.last_tap_voice = -math.inf
        self.last_behavior_ids = []
        self.last_routine_quote = -1000.0
        self.quote_times = {}
        self.behavior_context = None
        self.steps = []
        self.active_behavior = None
        self.moods = list(dict.fromkeys(clip["mood"] for clip in self.voices))
        self.mood = self.prefs.value("mood", "全部")
        if self.mood not in self.moods:
            self.mood = "全部"
        self.bag = []
        self.previous_id = None
        self.stage = read_data("data/staging.json")
        sprite_dir = ROOT / "assets/sprites"
        self.sprites = {}
        self.looks = {}
        if (sprite_dir / "manifest.json").is_file():
            for item in read_data("assets/sprites/manifest.json"):
                path = sprite_dir / item["file"]
                if path.is_file() and item.get("dress") == "d1":
                    sprite = dict(item, path=path)
                    self.sprites[item["id"]] = sprite
                    self.looks[(item.get("dress"), item.get("pose"), face_key(item.get("face")))] = sprite
        self.fallback = {"id":"d1-p1-f18e483b6", "path":sprite_dir/"d1-p1-f18e483b6.webp",
                         "pose":"p1", "dress":"d1", "face":"眉1:目1:口1:足1:頬0",
                         "layerIds":[694,715,695,804,827,850]}
        self.base = self.looks.get(("d1", "p1", face_key("眉1:目1:口1:足1:頬0")), self.fallback)
        self.shown = self.base
        self.white_poses = {}
        for item in self.sprites.values():
            if item.get("origin") == "scene":
                self.white_poses.setdefault(item["pose"], item)
        self.white_poses.setdefault("p1", self.base)
        self.expressions = ExpressionRenderer(ROOT,standing_shoes=True)
        self.props = PropRenderer(ROOT)
        self.body = BodyMotion(time.monotonic())
        self.fabric = FabricMotion(self.body.start,self.body.seed)
        self.face_mood = "source"
        self.last_art = self.old_art = None
        self.last_original = self.pose_source = self.next_look = self.face_swap = None
        self.pose_frame = self.source_rig = None
        self.pose_curve = self.pose_brake = None
        self.paint_height = None
        self.pose_seconds = .44
        self.transition_at = 0.
        self.walk_start = self.walk_goal = None
        self.walk_origin = self.walk_destination = None
        self.walk_history = []
        self.walk_headings = []
        self.standing_since=time.monotonic()
        self.walk_brake = None
        self.home = None
        self.blink = self.mouth = 0.
        self.mouth_active = False
        self.lip_opening=0.
        self.voice_released_at=-math.inf
        self.blink_length = .20
        self.setMouseTracking(True)
        self.drag_start = None
        self.dragged = False
        self.speech = Speech(self, ROOT)
        self.diary_reader = DiaryReader(self)
        self.diary_reader.prepared.connect(self.diary_reading_prepared)
        self.speech.set_muted(self.muted)
        self.speech.ready.connect(self.speech_ready)
        self.speech.ended.connect(self.speech_ended)
        self.speech.failed.connect(self.speech_failed)
        self.speech.approaching_segment.connect(self.prepare_speech_face)
        self.speech.segment.connect(self.speech_segment)
        self.utterance = None
        self.speech_emphasis = 0.
        self.face_sample_at = time.monotonic()
        self.blink_at = time.monotonic() + random.uniform(3, 6)
        self.caption = QLabel(self)
        if not QFontDatabase.families(QFontDatabase.WritingSystem.SimplifiedChinese):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"))
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setWordWrap(True)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.caption.setFont(QFont("Microsoft YaHei UI", 10))
        self.caption.setStyleSheet("QLabel {color:#263746; background:rgba(251,252,253,242);"
                                   "border:1px solid #9ab7be; border-radius:11px; padding:6px 9px;}")
        self.caption.hide()
        self.caption_timer = QTimer(self, singleShot=True)
        self.caption_timer.timeout.connect(self.caption.hide)
        self.idle = QTimer(self, singleShot=True)
        self.idle.timeout.connect(self.begin_behavior)
        self.next_step = QTimer(self, singleShot=True)
        self.next_step.timeout.connect(self.behavior_step)
        self.sense_timer = QTimer(self, singleShot=True)
        self.sense_timer.timeout.connect(self.sample_screen)
        self.screen_observed.connect(self.receive_screen)
        self.startup_process = QProcess(self)
        self.startup_process.finished.connect(self.startup_finished)
        self.startup_process.errorOccurred.connect(self.startup_error)
        self.motion = QTimer(self, interval=33)
        self.motion.setTimerType(Qt.TimerType.PreciseTimer)
        self.motion.timeout.connect(self.advance_face)
        self.diary_clock = QTimer(self, interval=15*60*1000)
        self.diary_clock.timeout.connect(self.diary_tick)
        self.diary_action = QTimer(self,singleShot=True,interval=1000)
        self.diary_action.timeout.connect(self.remember_gesture)
        from desktop_ambience import Ambience
        self.ambience = Ambience(self, ROOT)
        self.build_menu()
        self.apply_size(self.size_name, initial=True)
        self.place(self.prefs.value("position", None))
        self.home = QPoint(self.pos())
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QIcon(artwork(PORTRAIT, 64, 1.0)))
        self.tray.setToolTip("Atri Desktop · lounthinker\n游戏图像 © Aniplex Inc. All rights reserved.")
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.refresh_motion()
        self.schedule_idle(initial=True)
        self.diary_clock.start()
        if self.automatic and self.sensing:
            self.sense_timer.start(1900)
            self.media_timer.start()

    def build_menu(self):
        self.menu = QMenu("桌面亚托莉")
        self.menu.addAction("和亚托莉聊天", lambda: show_chat(self))
        original = self.menu.addAction("播放一句原声" if self.voices else "尚未配置原声",
                                       lambda:self.speak(self.pick_voice()))
        original.setEnabled(bool(self.voices))
        examples = self.menu.addMenu("按语境听原声")
        examples.setEnabled(bool(self.voice_examples))
        for row in self.voice_examples:
            clip = self.voice_lookup[row["id"]]
            action = examples.addAction(f"{row['categoryZh']} · {clip['zh']}")
            action.setToolTip(row["contextZh"])
            action.triggered.connect(lambda checked=False, c=clip: self.speak(c))
        self.line_pair = ROOT / "tts/试听/安慰与得意_连读对齐.zip"
        if self.line_pair.is_file():
            self.menu.addAction("试听安慰与得意连读", self.play_line_pair)
        self.menu.addAction("翻阅亚托莉的日记", lambda:show_journal(self))
        moods = self.menu.addMenu("选择心情")
        moods.setEnabled(bool(self.moods))
        mood_group = QActionGroup(moods)
        for mood in ["全部", *self.moods]:
            action = moods.addAction(mood)
            action.setCheckable(True)
            mood_group.addAction(action)
            action.setChecked(mood == self.mood)
            action.triggered.connect(lambda checked=False, name=mood: self.choose_mood(name))
        sizes = self.menu.addMenu("角色大小")
        size_group = QActionGroup(sizes)
        for size in SIZES:
            action = sizes.addAction(size)
            action.setCheckable(True)
            size_group.addAction(action)
            action.setChecked(size == self.size_name)
            action.triggered.connect(lambda checked=False, name=size: self.apply_size(name))
        mute = self.menu.addAction("静音")
        mute.setCheckable(True)
        mute.setChecked(self.muted)
        mute.toggled.connect(self.set_muted)
        self.ambience.add_menu(self.menu)
        self.menu.addSeparator()
        auto = self.menu.addAction("自动进行日常活动")
        auto.setCheckable(True)
        auto.setChecked(self.automatic)
        auto.toggled.connect(self.set_automatic)
        self.menu.addAction("立即演示一次日常活动", lambda: self.begin_behavior(demo=True))
        preview = self.menu.addMenu("试演身体动作")
        for heading, keys in [
            ("走动与探看","glance greet step walk_back tiptoe peek turn turn_away lean inspect look_around"),
            ("学习与忙碌","look_down read turn_page write think count knead stir taste point"),
            ("交流与情绪","listen watch wait nod bow beckon proud cheer hop refuse surprise shy"),
            ("音乐与休息","hum sway stretch yawn rest doze wake settle drag land"),
        ]:
            group=preview.addMenu(heading)
            for key in keys.split():
                group.addAction(GESTURES[key].label, lambda checked=False,k=key: self.preview_gesture(k))
        chatter = self.menu.addAction("自主活动时偶尔开口")
        chatter.setCheckable(True)
        chatter.setEnabled(bool(self.voices))
        chatter.setChecked(self.chatter)
        chatter.toggled.connect(self.set_chatter)
        sense = self.menu.addAction("感知当前屏幕并回应")
        sense.setCheckable(True)
        sense.setChecked(self.sensing)
        sense.toggled.connect(self.set_sensing)
        self.status_action = self.menu.addAction("")
        self.status_action.setEnabled(False)
        self.refresh_status()
        self.start_action = self.menu.addAction("随 Windows 登录启动")
        self.start_action.setCheckable(True)
        self.start_action.triggered.connect(lambda checked: self.startup_request("Enable" if checked else "Disable"))
        self.menu.aboutToShow.connect(self.menu_opened)
        self.menu.aboutToHide.connect(self.menu_closed)
        self.menu.addSeparator()
        self.menu.addAction("打开角色资料", self.open_archive)
        self.menu.addAction("退出桌面亚托莉", self.close)

    def choose_mood(self, name):
        self.mood = name
        self.prefs.setValue("mood", name)
        self.bag.clear()
        self.speak(self.pick_voice())

    def set_muted(self, muted):
        self.muted = muted
        self.prefs.setValue("muted", muted)
        self.speech.set_muted(muted)
        if muted and self.utterance:
            if self.utterance.get("diary"):
                self.stop_diary_reading()
            else:
                self.act("settle",0,appearance=False)
        view=getattr(self,"_journal_view",None)
        if view is not None:
            view.update_reading_controls()

    def interrupt(self, *, settle=True):
        self.finish_walk()
        self.target_elapsed.stop()
        if self.target_visit and "target" in self.journal.desktop_state:
            self.journal.desktop_state={k:v for k,v in self.journal.desktop_state.items() if k!="target"}
            self.record_context("recent_target","角色动作","我结束了对屏幕上那一处的查看。")
        self.target_visit=None
        self.last_touch = time.monotonic()
        self.props.interrupt(self.last_touch)
        self.walk_brake = None
        self.idle.stop()
        self.next_step.stop()
        self.stop_speech()
        self.active_behavior = None
        self.behavior_context = None
        self.steps.clear()
        self.walk_start = self.walk_goal = None
        if settle:
            self.act("settle", 0.0)

    def schedule_idle(self, initial=False):
        if self.automatic and not self.closed and not self.active_behavior:
            low, high = (5, 11) if initial else (10, 26)
            self.idle.start(random.randint(low, high) * 1000)

    def set_automatic(self, checked):
        self.interrupt()
        self.automatic = checked
        self.prefs.setValue("automatic", checked)
        self.schedule_idle(initial=True)
        self.restart_sensing()
        self.refresh_motion()

    def set_chatter(self, checked):
        self.chatter = checked
        self.prefs.setValue("chatter", checked)

    def set_sensing(self, checked):
        self.sensing = checked
        self.prefs.setValue("sensing", checked)
        self.restart_sensing()

    def restart_sensing(self):
        self.context_generation += 1
        self.sense_timer.stop()
        self.media_timer.stop()
        self.media_reader.close()
        self.media_state=("none","unknown")
        self.media_reacted=False
        self.journal.desktop_state={}
        self.seen_targets.clear()
        self.last_target_rect=None
        if self.target_visit or self.behavior_context=="system_audio":
            self.interrupt()
            self.schedule_idle()
        self.last_kind = self.confirmed_kind = "unknown"
        self.pending_observation=True
        self.reacted_kind = None
        self.run_length = 0
        self.refresh_status()
        if self.sensing and self.automatic:
            self.sense_timer.start(1500)
            self.media_timer.start()

    def record_context(self,key,source,description):
        data=dict(self.journal.desktop_state)
        data[key]={"source":source,"description":description,"time":self.journal.clock().isoformat()}
        self.journal.desktop_state=data

    def sample_media(self):
        if self.media_busy or self.closed or not self.sensing or not self.automatic:return
        generation=self.context_generation
        self.media_busy=True
        def observe():
            reading=self.media_reader.sample()
            if not self.closed:self.media_observed.emit(generation,reading)
        threading.Thread(target=observe,daemon=True,name="atri-system-media").start()

    def receive_media(self,generation,reading):
        self.media_busy=False
        if generation!=self.context_generation or self.closed or not self.sensing or not self.automatic:return
        previous=self.media_state
        state=self.media_state=(reading["state"],reading["kind"])
        if state!=previous:self.media_reacted=False
        if state!=("playing","audio"):
            if previous==("playing","audio"):
                self.journal.desktop_state={k:v for k,v in self.journal.desktop_state.items() if k!="media"}
                cause={"paused":"系统报告音频暂停。","stopped":"系统报告音频停止。"}.get(state[0],"先前确认的音频播放状态已失去。")
                if self.behavior_context=="system_audio":
                    cause+="我因此结束侧耳和轻晃，重新站稳。"
                self.record_context("recent_media","Windows 系统媒体",cause)
                if self.behavior_context=="system_audio":
                    self.interrupt()
                    self.schedule_idle()
            return
        self.record_context("media","Windows 系统媒体","播放器报告音频正在播放。")
        if (self.media_reacted or self.active_behavior or self.utterance or self.drag_start is not None
                or self.menu.isVisible() or not self.isVisible()):return
        self.media_reacted=True
        self.idle.stop()
        self.active_behavior="hum"
        self.behavior_context="system_audio"
        self.steps=[("listen",3.4),("sway",5.),("settle",2.)]
        self.behavior_step()
        self.journal._observe("system-audio/"+self.journal.clock().isoformat(),"system-media/audio",
                              "Windows 报告外部音频播放，我侧耳听了一会儿。")

    def refresh_status(self):
        if not self.sensing or not self.automatic:
            state = "已暂停"
        elif self.pending_observation:
            state = "待确认"
        else:
            state = CONTEXT_LABELS[self.confirmed_kind]
        self.status_action.setText("屏幕感知 · " + state)

    def menu_opened(self):
        self.interrupt()
        self.startup_request("Status")

    def menu_closed(self):
        self.last_touch = time.monotonic()
        self.schedule_idle()

    def startup_request(self, mode):
        if self.startup_process.state() != QProcess.ProcessState.NotRunning:
            return
        self.start_action.setEnabled(False)
        shell = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        self.startup_process.start(str(shell), ["-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                                   "-ExecutionPolicy", "RemoteSigned", "-File", str(ROOT / "startup.ps1"), "-Mode", mode])

    def startup_finished(self, code, _status):
        result = bytes(self.startup_process.readAllStandardOutput()).decode(errors="replace").strip()
        if code == 0 and result in {"Enabled", "Disabled"}:
            self.start_action.setChecked(result == "Enabled")
        else:
            self.say_caption("未能读取或更改登录启动设置", 3)
        self.start_action.setEnabled(True)

    def startup_error(self, _error):
        self.start_action.setEnabled(True)
        self.say_caption("未能运行登录启动设置", 3)

    def sample_screen(self):
        if not self.sensing or not self.automatic or self.closed:
            return
        self.sense_timer.start(1400 if self.target_visit else random.randint(15,25)*1000)
        if self.sampling:
            return
        self.sampling = True
        generation = self.context_generation

        def observe():
            try:
                from screen_context import observe_screen
                observation = observe_screen()
                kind = str(observation.get("kind", "unknown"))
                confidence = float(observation.get("confidence", 0))
            except Exception:
                kind, confidence,observation = "unknown", 0,{}
            if not self.closed:
                self.screen_observed.emit(generation, kind, confidence,observation.get("target"))

        threading.Thread(target=observe, name="Atri screen context", daemon=True).start()

    def observe_target(self,kind,target):
        from desktop_attention import beside
        location=beside(self,target) if kind in ("math","study","coding","coding_error","coding_success","writing") else None
        visit=self.target_visit
        now=time.monotonic()
        if not location:
            self.last_target_rect=None
            if visit:
                # Brake on missing evidence. Keep an unfinished approach for
                # one quick resample, separately from places actually reached.
                if kind=="unknown" and visit["phase"] in ("look","turn","walk"):
                    self.finish_target()
                    visit["phase"]="recheck"
                    self.target_visit=visit
                    self.active_behavior="view_target"
                    self.behavior_context="ocr_target"
                    self.target_elapsed.start(4500)
                    self.sense_timer.start(350)
                    return True
                self.finish_target()
                self.schedule_idle()
                return True
            return False
        rect,goal=location
        self.last_target_rect=rect
        self.last_target_time=now
        hwnd=target.get("window")
        if visit and hwnd==visit["window"] and kind==visit["kind"] and (visit["rect"].center()-rect.center()).manhattanLength()<25:
            visit["rect"]=rect
            if visit["phase"]=="recheck":
                visit["phase"]="look"
                visit["goal"]=goal
                route=goal-self.pos()
                visit["back"]=route.y() < -max(32.,1.35*abs(route.x()))
                pause=max(.35,self.props.release_until-now)
                if self.walk_brake:
                    pause=max(pause,self.walk_brake[0]+.49-now)
                else:
                    self.act("glance",pause)
                self.record_context("target","前台窗口 OCR 匹配行","我重新看到了刚才留意的那处"+CONTEXT_LABELS[kind]+"内容。")
                self.target_elapsed.start(math.ceil(pause*1000))
            return True
        # Refresh a completed visit for as long as its line remains visible.
        self.seen_targets=[v for v in self.seen_targets if now-v[3]<120.]
        for i,(window,category,center,_) in enumerate(self.seen_targets):
            if window==hwnd and category==kind and (rect.center()-center).manhattanLength()<25:
                self.seen_targets[i]=(window,category,center,now)
                if visit:
                    self.finish_target()
                    self.schedule_idle()
                return True
        if self.drag_start is not None or self.utterance or self.menu.isVisible() or not self.isVisible():return True
        if not visit and self.walk_brake is None and now-self.last_touch<1:return True
        if visit and visit["phase"] not in ("recheck","watch") and hwnd==visit["window"] and kind==visit["kind"] and (visit["goal"]-goal).manhattanLength()<10:
            visit["rect"]=rect
            return True
        if visit and visit["phase"]=="walk":
            self.finish_target()
        elif not visit and self.walk_brake is None:
            self.interrupt(settle=False)
        route=goal-self.pos()
        self.target_visit={"rect":rect,"goal":goal,"window":target.get("window"),"phase":"look","kind":kind,
                           "back":route.y() < -max(32.,1.35*abs(route.x()))}
        self.active_behavior="view_target"
        self.behavior_context="ocr_target"
        self.body.direction=1 if rect.center().x()>self.geometry().center().x() else -1
        now=time.monotonic()
        look_seconds=max(.72,self.props.release_until-now)
        if self.walk_brake is None:
            self.act("glance",look_seconds)
        self.record_context("target","前台窗口 OCR 匹配行","我正在看屏幕上一处"+CONTEXT_LABELS[kind]+"内容。")
        self.target_elapsed.start(math.ceil(look_seconds*1000))
        self.sense_timer.start(1400)
        return True

    def finish_target(self):
        visit=self.target_visit
        ongoing=self.walk_brake
        braking=None
        now=time.monotonic()
        if visit and visit["phase"]=="walk" and self.walk_goal is not None:
            duration=self.body.duration
            u=max(0.,min(1.,(now-self.body.start)/duration))
            earlier=max(0.,u-.025/duration)
            if u>earlier:
                fraction=self.body.travel_fraction(u)
                speed=(fraction-self.body.travel_fraction(earlier))/((u-earlier)*duration)
                # Ease the window to rest along its existing segment, inside the
                # available desktop. The leg solver uses the rounded displacement.
                coast=.32
                distance=min(1.-fraction,speed*coast/2)
                braking=now,self.pos(),(visit["goal"]-visit["start"])*distance,coast
        hold_back=bool((braking or ongoing) and self.base.get("pose")=="p4")
        self.interrupt(settle=not hold_back)
        if hold_back:
            self.act("settle",0.,appearance=False)
        if braking:
            self.walk_brake=braking
            self.body.brake_walk(now,SIZES[self.size_name])
        elif ongoing:
            self.walk_brake=ongoing

    def advance_target(self):
        visit=self.target_visit
        if not visit:return
        if visit["phase"]=="recheck":
            self.finish_target()
            self.schedule_idle()
            return
        if visit["phase"]=="look" and self.props.release_until>time.monotonic():
            self.target_elapsed.start(max(1,math.ceil((self.props.release_until-time.monotonic())*1000)))
            return
        goal=visit["goal"]
        far=math.hypot(goal.x()-self.x(),goal.y()-self.y())>32
        if visit["phase"]=="look" and far and visit.get("back") and self.base.get("pose")!="p4":
            visit["phase"]="turn"
            self.act("turn_away",.72)
            self.target_elapsed.start(730)
            return
        if visit["phase"] in ("look","turn") and far:
            if visit["phase"]=="turn":
                self.advance_pose(time.monotonic())
            visit["phase"]="walk"
            seconds=max(1.3,math.hypot(goal.x()-self.x(),goal.y()-self.y())/(SIZES[self.size_name]*.15))
            visit["start"]=self.pos()
            self.act("step",seconds)
            self.target_elapsed.start(round(seconds*1000))
        elif visit["phase"]!="watch":
            if visit["phase"]=="walk":self.ambience.walk_to(goal)
            visit["phase"]="watch"
            self.seen_targets=(self.seen_targets+[(visit["window"],visit["kind"],visit["rect"].center(),time.monotonic())])[-24:]
            self.act("inspect",3.)
            self.record_context("target","OCR 匹配行及角色动作","我在屏幕上那处"+CONTEXT_LABELS[visit["kind"]]+"旁站稳，停下来看看。")
            self.target_elapsed.start(3000)
        else:
            self.interrupt()
            self.schedule_idle()

    def receive_screen(self, generation, kind, confidence,target=None):
        self.sampling = False
        if generation != self.context_generation or not self.sensing or not self.automatic or self.closed:
            return
        if kind not in CONTEXT_LABELS or confidence < 0.6:
            kind = "unknown"
        # A player window or its title alone does not establish playback.
        if kind=="music":kind="unknown"
        handled=self.observe_target(kind,target)
        self.run_length = self.run_length + 1 if kind == self.last_kind else 1
        self.last_kind = kind
        self.pending_observation=self.last_target_rect is None and self.run_length<2 and confidence<.92
        if not self.pending_observation:
            self.confirmed_kind = kind
        self.refresh_status()
        if handled or self.pending_observation:return
        if kind == "unknown":
            self.reacted_kind = None
            return
        now = time.monotonic()
        # A successful rerun can immediately follow the error she just noticed.
        cooldown = 14 if (self.reacted_kind, kind) == ("coding_error", "coding_success") else 75
        if (kind == self.reacted_kind or self.drag_start is not None
                or self.menu.isVisible() or now - self.last_touch < 8 or now - self.last_reaction < cooldown
                or now - self.reaction_times.get(kind, -1000) < 300):
            return
        if self.begin_behavior(context=kind):
            self.reacted_kind = kind
            self.reaction_times[kind] = self.last_reaction = now

    def begin_behavior(self, context=None, demo=False):
        if self.closed or (not demo and not self.automatic):
            return False
        if not demo and (self.drag_start is not None or self.menu.isVisible() or self.utterance):
            self.schedule_idle()
            return False
        options = [b for b in self.behaviors if b["id"] in ROUTINES]
        if context:
            matched = [b for b in options if context in b.get("contexts", [])]
            if not matched and context == "math":
                matched = [b for b in options if "study" in b.get("contexts", [])]
            if not matched and context in {"coding_error", "coding_success"}:
                matched = [b for b in options if "coding" in b.get("contexts", [])]
            options = matched
        else:
            # These two behaviors explicitly refer to the outcome of a build.
            options = [b for b in options if b["id"] not in {"retry", "passed"}]
        notebook={"notes","evening_log"}
        calendar_now=self.journal.clock()
        if not demo and calendar_now.timestamp()<self.next_notebook:
            options=[b for b in options if b["id"] not in notebook]
        if not options:
            if not context:
                self.schedule_idle()
            return False
        hour = calendar_now.hour
        in_time = []
        for item in options:
            start, end = item.get("hours", [0, 24])
            if start == end or (start <= hour < end if start < end else hour >= start or hour < end):
                in_time.append(item)
        options = in_time or options
        families={"notes":"notebook","evening_log":"notebook","class":"study","science":"study"}
        if not demo and self.last_behavior_ids:
            last=self.last_behavior_ids[-1]
            different=[b for b in options if families.get(b["id"],b["id"])!=families.get(last,last)]
            if not different:
                if not context:
                    self.schedule_idle()
                return False
            options=different
        fresh=[b for b in options if b["id"] not in self.last_behavior_ids[-2:]]
        options=fresh or options
        selected = random.choices(options, weights=[item.get("weight", 1) for item in options])[0]
        if selected["id"] in notebook:
            self.next_notebook=calendar_now.timestamp()+random.uniform(30,50)*60
        self.next_step.stop()
        self.idle.stop()
        self.stop_speech()
        self.active_behavior = selected["id"]
        self.behavior_context = context
        self.last_behavior_ids = (self.last_behavior_ids + [selected["id"]])[-2:]
        # A morning greeting is grounded in the local clock. Scene dialogue
        # from the original game remains available in the voice menu.
        greeting="ATR_b124_010"
        clips=([self.voice_lookup[greeting]] if selected["id"]=="morning" and 6<=hour<10
               and self.chatter and not self.muted and greeting in self.voice_lookup else [])
        self.steps = motions_for(selected["id"], reacting=bool(context), demo=demo)
        now = time.monotonic()
        unused = [candidate for candidate in clips if quote_key(candidate)
                  and now - self.quote_times.get(quote_key(candidate), -1000) > 600]
        speaking = now-self.last_routine_quote>1800 and random.random()<.32
        if speaking and unused:
            # Respond early to a change on screen. During independent activities
            # a line, if any, follows the main movement rather than announcing it.
            index = 1 if context or demo else len(self.steps) - 1
            self.steps.insert(index, ("quote", random.choice(unused)))
        self.behavior_step()
        return True

    def behavior_step(self):
        if not self.steps:
            self.active_behavior = None
            self.behavior_context = None
            self.schedule_idle()
            return
        gesture, value = self.steps.pop(0)
        if gesture == "quote":
            clip = value
            now = time.monotonic()
            self.quote_times[quote_key(clip)] = self.last_routine_quote = now
            audible = self.chatter and now - self.last_auto_audio >= 120
            if audible and not self.muted:
                self.last_auto_audio = now
            self.play_clip(clip, audible)
            return  # The player's end signal advances the routine after this line.
        else:
            seconds = value
            self.act(gesture, seconds)
            if self.walk_destination is not None:
                seconds=max(seconds,self.body.start+self.body.duration-time.monotonic())
        self.next_step.start(round(seconds * 1000))

    def apply_size(self, name, initial=False):
        old_floor=self.geometry().bottom()+1-self.foot_margin
        self.size_name = name
        self.prefs.setValue("size", name)
        foot_x=self.geometry().center().x()
        height = SIZES[name]
        self.setFixedSize(max(240, round(height * 1.08)), height + 80)
        if not initial:
            self.place(QPoint(foot_x-self.width()//2,old_floor+self.foot_margin-self.height()))
            self.refresh_caption()
        self.update()

    @property
    def foot_margin(self):
        # The lowest rear-view shoe can travel below the standing registration.
        return 22 if self.size_name=="小" else 5

    def place(self, position):
        screen = QGuiApplication.primaryScreen()
        if isinstance(position, QPoint):
            center = position + QPoint(self.width() // 2, self.height() // 2)
            screen = QGuiApplication.screenAt(center) or screen
        area = screen.availableGeometry()
        if not isinstance(position, QPoint):
            position = QPoint(area.right() - self.width() - 18, area.bottom() - self.height() - 5)
        self.move(max(area.left(), min(position.x(), area.right() + 1 - self.width())),
                  max(area.top(), min(position.y(), area.bottom() + 1 - self.height())))

    def refresh_caption(self):
        metrics = self.caption.fontMetrics()
        text_width = max((metrics.horizontalAdvance(line) for line in self.caption.text().splitlines()), default=0)
        width = min(max(72, text_width + 24), 340, self.width() - 16)
        self.caption.setFixedWidth(width)
        height = min(100, max(34, self.caption.heightForWidth(width)))
        self.caption.setFixedHeight(height)
        self.caption.move((self.width() - width) // 2, max(1, 77 - height))

    def say_caption(self, text, seconds):
        self.caption.setText(text)
        self.refresh_caption()
        self.caption.show()
        self.caption_timer.start(round(max(2.8, seconds + 0.6) * 1000))

    def pick_voice(self):
        if not self.bag:
            self.bag = [v for v in self.voices if self.mood == "全部" or v["mood"] == self.mood]
            random.shuffle(self.bag)
            if len(self.bag) > 1 and self.bag[-1]["id"] == self.previous_id:
                self.bag[0], self.bag[-1] = self.bag[-1], self.bag[0]
        return self.bag.pop() if self.bag else None

    def speak(self, clip=None):
        self.interrupt(settle=False)
        if not isinstance(clip, dict):
            now=time.monotonic()
            clip=self.voice_lookup.get("ATR_b122_004")
            if self.muted or now-self.last_tap_voice<75 or clip is None:
                self.act("nod",1.5)
                self.schedule_idle()
                return
            self.last_tap_voice=now
        self.previous_id = clip["id"]
        self.play_clip(clip, audible=True, manual=True)

    def preview_gesture(self, key):
        self.interrupt(settle=False)
        duration={"settle":2.5,"write":7.9,"step":2.1,"tiptoe":2.5}.get(key,4.8)
        if key=="glance":self.preview_look=-1. if self.preview_look>=0 else 1.
        # Crossing to a different full-body plate needs the same real wrist
        # and book release as an observation. Drag still takes effect on press.
        remaining=self.props.release_until-time.monotonic()
        if remaining>0. and (GESTURES[key].pose!="p1" or key in ("step","tiptoe","walk_back")):
            self.steps=short_hop() if key=="hop" else [(key,duration)]
            self.act("settle",0.,appearance=False)
            self.next_step.start(math.ceil(remaining*1000))
            return
        if key=="hop":
            self.steps=short_hop()
            self.behavior_step()
            return
        self.act(key,duration)
        self.schedule_idle()
        if self.walk_destination is not None and self.idle.isActive():
            self.idle.start(self.idle.interval()+math.ceil(self.body.duration*1000))

    def pose_timing(self, now):
        """Phase and phase velocity, including the short braking arc on interruption."""
        if self.pose_brake:
            start,phase,speed = self.pose_brake
            dt = min(.08,max(0.,now-start))
            return min(1.,max(0.,phase+speed*dt*(1-dt/.16))), speed*(1-dt/.08)
        u = min(1.,max(0.,(now-self.transition_at)/self.pose_seconds))
        if self.pose_curve is None:
            return u,1/self.pose_seconds
        phase,speed = self.pose_curve
        value = phase+(1-phase)*smooth(u)+speed*self.pose_seconds*u*(1-u)**2
        velocity = (1-phase)*6*u*(1-u)/self.pose_seconds+speed*(1-u)*(1-3*u)
        return min(1.,max(0.,value)),velocity

    def set_appearance(self, sprite, mood):
        if sprite.get("dress") != "d1" or sprite.get("pose") not in self.white_poses:
            base = self.white_poses.get(sprite.get("pose"), self.white_poses["p1"])
            sprite = self.expressions.with_scene_face(base,sprite.get("face")) or base
        now = time.monotonic()
        target_pose = sprite.get("pose","p1")
        previous_pose = self.base.get("pose","p1")
        if self.pose_source is not None and (self.pose_brake or now-self.transition_at<self.pose_seconds):
            source, source_pose = self.pose_source
            if target_pose == previous_pose:
                if self.pose_brake:
                    phase,speed = self.pose_timing(now)
                    self.pose_curve = phase,speed
                    self.pose_brake = None
                    self.transition_at = now
                    self.pose_seconds = max(.14,(1-phase)*self.pose_seconds)
                self.next_look = None
                if self.base is not sprite or mood != self.face_mood:
                    self.face_swap = now+.077,sprite,mood
                    self.blink_at,self.blink_length = now,.22
            elif (self.pose_frame and "atri_pose_registration" in self.pose_frame[0].info
                    and target_pose in ("p1","p2","p3")):
                if not self.pose_brake:
                    self.pose_brake = (now,*self.pose_timing(now))
                self.next_look = sprite,mood
            elif target_pose == source_pose and self.last_original and "p3" not in (source_pose,previous_pose):
                phase,speed = self.pose_timing(now)
                if self.last_original[1] == previous_pose:
                    self.pose_source = self.last_original
                    self.pose_curve = 1-phase,-speed
                    self.transition_at = now
                    self.pose_seconds = max(.20,.12+self.pose_seconds*phase)
                else:
                    # Several mouse/menu cues can arrive before the first
                    # paint. The screen is still at the requested endpoint.
                    self.pose_source = None
                    self.pose_curve = None
                self.face_swap = self.pose_brake = None
                self.source_rig = None
                self.base = self.shown = sprite
                self.face_mood = mood
                self.next_look = None
            else:
                self.next_look = sprite,mood
            return
        self.face_swap = None
        if sprite is self.base and mood == self.face_mood:
            return
        if previous_pose==target_pose=="p4":
            self.base=self.shown=sprite
            self.face_mood=mood
            return
        if self.last_original and {previous_pose,target_pose} in ({"p2","p4"},{"p3","p4"}):
            self.set_appearance(self.white_poses["p1"],"neutral")
            self.next_look = sprite,mood
            return
        if previous_pose == target_pose and self.last_original is not None and previous_pose != "p4":
            # Change a strong expression at eyelid closure; no doubled irises.
            self.face_swap = now+.077,sprite,mood
            self.blink_at,self.blink_length = now,.22
            self.old_art = None
            return
        if (previous_pose != target_pose and self.last_original is not None
                and self.last_original[1] == previous_pose):
            self.pose_source = self.last_original
            self.source_rig = None
            self.pose_curve = self.pose_brake = None
            self.old_art = self.next_look = None
            self.transition_at=now
            self.pose_seconds = .44 if "p3" in (previous_pose,target_pose) else .72
            self.base=self.shown=sprite
            self.face_mood=mood
            return
        mix = smooth((now-self.transition_at)/.24)
        if self.old_art and self.last_art and mix < 1:
            # Restart from the image actually on screen if a click interrupts a blend.
            bounds = self.old_art[1].united(self.last_art[1])
            ratio = self.devicePixelRatioF()
            snapshot = QImage(math.ceil(bounds.width()*ratio), math.ceil(bounds.height()*ratio),
                              QImage.Format.Format_ARGB32_Premultiplied)
            snapshot.fill(Qt.GlobalColor.transparent)
            painter = QPainter(snapshot)
            painter.scale(ratio,ratio)
            painter.translate(-bounds.left(),-bounds.top())
            for (image,rect),opacity in ((self.old_art,1-mix),(self.last_art,mix)):
                painter.setOpacity(opacity)
                painter.drawImage(rect,image)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            painter.end()
            self.old_art = snapshot,bounds
        else:
            self.old_art = self.last_art
        self.transition_at = now
        self.base = self.shown = sprite
        self.face_mood = mood

    def finish_walk(self):
        origin=self.walk_origin
        if origin is not None and (self.pos()-origin).manhattanLength()>8:
            route=self.pos()-origin
            distance=math.hypot(route.x(),route.y())
            stride=SIZES[self.size_name]*.14
            marks=[origin]
            if distance>2*stride:
                marks.append(self.pos()-route*(stride/distance))
            self.walk_history=(self.walk_history+marks)[-10:]
            self.walk_headings=(self.walk_headings+[(route.x()/distance,route.y()/distance)])[-3:]
            self.standing_since=time.monotonic()
        self.walk_origin=self.walk_destination=None
        self.walk_start=self.walk_goal=None

    def standing_seconds(self):
        return time.monotonic()-self.standing_since

    def act(self, gesture, seconds, *, appearance=True):
        now = time.monotonic()
        self.finish_walk()
        goal=None
        if gesture in ("step","tiptoe","walk_back") and self.home is not None and seconds>0:
            from desktop_attention import walk_destination
            height=SIZES[self.size_name]
            if self.target_visit and self.target_visit["phase"]=="walk":
                goal=self.target_visit["goal"]
            else:
                screen=QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
                if self.last_target_rect is not None and now-self.last_target_time>30.:
                    self.last_target_rect=None
                goal=walk_destination(self,gesture,height,screen.availableGeometry())
                distance=math.hypot(goal.x()-self.x(),goal.y()-self.y())
                if distance<height*.045:
                    gesture="look_down" if self.active_behavior=="crab" else "watch"
                    goal=None
                elif gesture=="walk_back":
                    seconds=max(seconds,distance/(height*.15))
        definition = GESTURES[gesture]
        walk_pose=None
        if goal is not None:
            dx,dy=goal.x()-self.x(),goal.y()-self.y()
            walk_pose="p4" if dy < -max(.1*height,1.35*abs(dx)) else "p1"
            if self.target_visit and self.target_visit["phase"]=="walk":
                walk_pose="p4" if self.target_visit["back"] else "p1"
        prior_pose=self.base.get("pose")
        self.body.start_gesture(gesture, seconds, now)
        self.diary_action.stop()
        if self.active_behavior and seconds >= 1.:
            self.diary_action.start()
        if gesture == "settle" and self.utterance:
            self.refresh_motion()
            return
        if appearance:
            paper=self.looking_at_book(gesture)
            mood="neutral" if paper or (gesture=="glance" and self.paper_focus>.04) else definition.mood
            sprite=self.white_poses.get(walk_pose or definition.pose,self.base)
            if paper and self.base.get("pose")=="p1" and self.pose_source is None:
                # Opening the book, following a leaf and placing the nib share
                # the same open eyes. No expression-change blink between them.
                self.base=self.shown=sprite
                self.face_mood=mood
                self.face_swap=None
            else:
                self.set_appearance(sprite,mood)
        if goal is not None:
            self.walk_origin,self.walk_destination=self.pos(),goal
            self.walk_start,self.walk_goal=self.x(),goal.x()
            if prior_pose!=walk_pose and "p4" in (prior_pose,walk_pose) and self.pose_source:
                # Let the planted pivot finish before translating an ordinary
                # step. Observed targets already turn during their looking beat.
                self.body.start=max(now,self.transition_at+self.pose_seconds)
            if dx:self.body.direction=1 if dx>0 else -1
            self.body.plan_walk((dx,dy),height)
        self.refresh_motion()
        self.update()

    def book_routine(self):
        if self.active_behavior=="class" and self.behavior_context:
            return None
        return self.active_behavior

    def looking_at_book(self, gesture):
        routine=self.book_routine()
        if routine in ("notes","evening_log","class"):
            return gesture in ("look_down","turn_page","write","read")
        return not self.active_behavior and gesture in ("turn_page","write") and self.drag_start is None

    def show_look(self, clip):
        frame_id = self.stage["byVoice"].get(clip["id"])
        actor = self.stage["frames"][frame_id]["atri"] if frame_id is not None else None
        if not actor or not actor.get("face") or actor.get("pose") not in ("p1","p2","p3"):
            return False
        pose = actor.get("pose")
        white = self.looks.get(("d1",pose,face_key(actor.get("face"))))
        if not white and pose in self.white_poses:
            white = self.expressions.with_scene_face(self.white_poses[pose],actor["face"])
        if not white:
            return False
        self.set_appearance(white, "source")
        return True

    def deliver(self, clip, seconds, original=True):
        gesture,mood = delivery(clip,self.active_behavior,self.example_by_id.get(clip["id"]))
        self.act(gesture,seconds,appearance=False)
        if not original or not self.show_look(clip):
            pose = GESTURES[gesture].pose
            self.set_appearance(self.white_poses.get(pose,self.white_poses["p1"]),mood)

    def play_clip(self, clip, audible, manual=False):
        self.stop_speech()
        text=clip["zh"].replace("\\n","\n")
        self.utterance = {"clip":clip,"text":text,"manual":manual,"audible":audible}
        self.act("listen",.7,appearance=False)
        self.say_caption(text,clip["duration"])
        self.caption_timer.stop()
        self.speech.original(clip,audible)

    def read_diary_aloud(self, text):
        if not text.strip():
            return
        if not self.voice_lookup:
            self.reading_changed.emit("failed","请先按 docs/local-setup.md 配置日语语音。")
            return
        self.interrupt(settle=False)
        self.say_caption(text,min(15,max(4,len(text)/5)))
        if not self.muted:
            self.utterance = {"text":text,"diary":True}
            self.act("read",1.5,appearance=False)
            self.reading_changed.emit("preparing","正在准备这页的日语读稿。")
            self.diary_reader.request(self.utterance)
        else:
            self.schedule_idle()

    def stop_diary_reading(self):
        if self.utterance and self.utterance.get("diary"):
            self.interrupt()
            self.schedule_idle()

    def diary_reading_prepared(self, line, plan):
        if self.utterance is not line:
            return
        if not self.muted and plan:
            self.reading_changed.emit("preparing","正在准备这页的完整日语音频。")
            self.play_japanese_reading(line,plan)
        else:
            self.utterance = None
            self.reading_changed.emit("failed","这次未能取得完整日语读稿，可以重试。")
            self.say_caption(line["text"],min(15,max(4,len(line["text"])/5)))
            self.schedule_idle()

    def play_japanese_reading(self, line, plan):
        reading = validated_reading(line["text"],plan)
        if not reading:
            self.utterance = None
            if line.get("diary"):
                self.reading_changed.emit("failed","日语分段未能与这页正文对应，可以重试。")
            return
        line["reading"] = reading
        japanese, parts = reading["spoken_ja"], reading["parts"]
        clip = exact_original(self.voice_lookup.values(),japanese,line["text"])
        if clip:
            line["original_reading"] = clip["id"]
            self.speech.original(clip,True)
        elif len(parts)==1 and parts[0]["style"]=="natural":
            self.speech.synthesize(japanese,language="ja",emotion="natural",audible=True,
                                   continuous=bool(line.get("diary")))
        else:
            directions = [{"text":p["text"],"style":p["style"]} for p in parts]
            self.speech.synthesize_scene(japanese,language="ja",parts=directions,audible=True,
                                         continuous=bool(line.get("diary")))

    @property
    def dialogue_status(self):
        return self.conversation.status

    def send_chat(self, text):
        if not text.strip():
            return False
        self.observe_journal_day()
        if any(phrase in text for phrase in ("没在听歌","没有在听歌","没听歌","没在听音乐","只想歇会","只想休息")):
            self.media_reacted=True
            self.record_context("correction","用户亲口说明",text.strip()[:160])
            if self.behavior_context=="system_audio":
                self.interrupt()
                self.schedule_idle()
        if self.utterance:
            self.interrupt()
        try:
            self.journal.queue_chat(text.strip())
        except (OSError,sqlite3.Error) as err:
            raise OSError(f"暂时无法保存这条消息。{err}") from err
        self.conversation_changed.emit()
        self.conversation.pump()
        return True

    def retry_conversation(self):
        pending=self.journal.pending_jobs()
        uncertain=bool(pending and pending[0]["state"]=="uncertain")
        if uncertain:
            from desktop_mind import recover_reply
            job_id=pending[0]["id"]
            found=recover_reply(self.conversation.directory,self.journal.session_id(),job_id)
            if found and self.journal.adopt_recovered(job_id,found["text"],found["thread_id"]):
                self.conversation.pump()
                self.conversation_changed.emit()
                return
            choice=QMessageBox.question(self,"重新发送给亚托莉",
                "先前的请求可能已经送达，暂时没有找到完整回执。再发一次，可能在同一角色会话中出现两次。要继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
            if choice!=QMessageBox.StandardButton.Yes:
                return
        self.conversation.retry(uncertain,pending[0]["id"] if uncertain else None)

    def role_delivered(self, kind, text, plan=None):
        if kind=="diary":
            view=getattr(self,"_journal_view",None)
            if view is not None:
                view.refresh()
        elif kind=="chat" and self.isVisible() and self.drag_start is None:
            self.interrupt(settle=False)
            self.say_caption(text, min(15,max(4,len(text)/5)))
            spoken=bool(not self.muted and self.voice_lookup and plan)
            self.act("listen" if spoken else "nod",1.1)
            if spoken:
                self.utterance={"text":text,"reply":True}
                self.play_japanese_reading(self.utterance,plan)
            self.schedule_idle()

    def play_line_pair(self):
        self.interrupt(settle=False)
        self.utterance = {"text":""}
        self.speech.replay_bundle(self.line_pair,audible=True)

    def stop_speech(self):
        was_diary=bool(self.utterance and self.utterance.get("diary"))
        if self.utterance:
            self.voice_released_at=time.monotonic()
        self.utterance = None
        self.diary_reader.stop()
        self.speech.stop()
        self.caption_timer.stop()
        self.caption.hide()
        self.mouth_active = False
        self.mouth = 0.
        if was_diary:
            self.reading_changed.emit("idle","")

    def speech_ready(self, metadata):
        line = self.utterance
        if not line:
            return
        seconds = metadata["duration_seconds"]
        if line.get("diary"):
            self.reading_changed.emit("playing","")
        line["sample_rate_hz"] = metadata["sample_rate_hz"]
        if "clip" not in line and "reading" not in line:
            line["text"] = metadata.get("text") or line["text"]
        self.say_caption(line["text"],seconds)
        self.caption_timer.stop()
        if "clip" in line:
            self.deliver(line["clip"],seconds)
            if self.isVisible():
                self.observe_journal_day()
                self.journal.spoken(line["clip"], line["audible"] and not self.muted
                                    and metadata.get("audible",True),manual=line.get("manual",False))
        elif line.get("original_reading") and line.get("reply"):
            self.deliver(self.voice_lookup[line["original_reading"]],seconds)
        elif not line.get("reply"):
            if line.get("diary") and self.body.name=="read":
                self.body.duration=max(0.,time.monotonic()-self.body.start)+seconds
            else:
                self.act("read",seconds,appearance=False)
        self.refresh_motion()

    def prepare_speech_face(self, segment, seconds_remaining):
        line=self.utterance
        if (not line or not line.get("reply") or self.base is not self.white_poses["p1"]
                or self.pose_source or self.face_swap):
            return
        performance,_,_=aligned_segment(line,segment)
        _,mood=segment_delivery(performance,self.example_by_id)
        if mood!=self.face_mood:
            # Close the eyelids during the measured pause. The next segment
            # still owns the actual change of brows, caption and gesture.
            self.blink_length=.22
            self.blink_at=time.monotonic()+seconds_remaining-.33*self.blink_length
            line["prepared_face"]=(segment["start_frame"],mood)

    def speech_segment(self, segment):
        if self.utterance and "clip" not in self.utterance:
            continuing=self.utterance.get("diary") or "segment" in self.utterance
            previous_index=self.utterance.get("part_index",0)
            performance,text,next_index = aligned_segment(self.utterance,segment)
            self.utterance["part_index"] = next_index
            self.utterance["segment"] = segment
            gesture,mood = segment_delivery(performance,self.example_by_id)
            duration = (segment["end_frame"]-segment["start_frame"])/self.utterance["sample_rate_hz"]
            if self.utterance.get("diary"):
                parts=self.utterance.get("reading",{}).get("parts",[])
                self.utterance["read_offset"]=sum(len(part["zh"]) for part in parts[:previous_index])
                gesture="read"
                if performance.get("style","natural")=="natural":
                    mood="neutral"
            if not self.muted and continuing and gesture==self.body.name:
                self.body.duration=max(0.,time.monotonic()-self.body.start)+duration
            elif not self.muted:
                self.act(gesture,max(.1,duration),appearance=False)
            prepared=self.utterance.pop("prepared_face",None)
            if not self.muted:
                if (prepared==(segment["start_frame"],mood) and not self.pose_source
                        and self.base is self.white_poses["p1"] and not self.face_swap):
                    self.face_mood=mood
                    self.blink=1.
                    self.update()
                else:
                    self.set_appearance(self.white_poses["p1"],mood)
            self.say_caption(text,3.)
            self.caption_timer.stop()

    def speech_ended(self, *, completed=True):
        if not self.utterance:
            return
        if self.utterance.get("diary"):
            if completed and "reading" in self.utterance:
                self.diary_reader.remember(self.utterance)
            self.reading_changed.emit("idle","")
        self.voice_released_at=time.monotonic()
        self.utterance = None
        self.mouth_active = False
        self.mouth = 0.
        self.caption.hide()
        self.caption_timer.stop()
        if self.active_behavior:
            self.next_step.start(450)
        else:
            self.act("settle",0)
            self.schedule_idle()
        self.refresh_motion()

    def speech_failed(self, message):
        if self.utterance:
            text=self.utterance.get("text","")
            reading=self.utterance.get("reading")
            diary=self.utterance.get("diary")
            self.speech_ended(completed=False)
            if diary:
                self.reading_changed.emit("failed","日语朗读失败，可以重试。"+str(message).strip())
            if reading:
                self.say_caption(text,min(15,max(4,len(text)/5)))
            else:
                self.say_caption((text+"\n" if text else "")+message,3.5)

    def refresh_motion(self):
        if self.closed or not self.isVisible() or self.isMinimized():
            self.motion.stop()
        elif not self.motion.isActive():
            self.motion.start()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_motion()
        self.observe_journal_day()

    def observe_journal_day(self):
        day = self.journal.clock().date().isoformat()
        if day != self.journal_day:
            self.journal.finish_previous_days()
            self.journal.presence(self.isVisible())
            self.journal_day = day if self.journal.error is None else None
        return day

    def diary_tick(self):
        if self.closed:
            return
        day = self.observe_journal_day()
        self.journal.finish_previous_days()
        # Committing observations never takes control of the on-screen body.
        self.journal.save(day)
        self.conversation.pump()

    def remember_gesture(self):
        if not self.isVisible() or self.drag_start is not None or not self.active_behavior:
            return
        self.observe_journal_day()
        gesture = self.body.name
        self.journal.activity(self.active_behavior,gesture,self.behavior_names.get(self.active_behavior),
                              GESTURES[gesture].label)

    def hideEvent(self, event):
        self.motion.stop()
        super().hideEvent(event)

    def advance_pose(self,now):
        if self.face_swap and now >= self.face_swap[0]:
            _,self.base,self.face_mood = self.face_swap
            self.shown=self.base
            self.face_swap=None
        if self.pose_brake and now-self.pose_brake[0]>=.08 and self.pose_frame and self.next_look:
            # The mouse/window already followed the input. Re-register the
            # decelerated body from the last frame actually drawn on screen.
            canvas,self.source_rig = self.pose_frame
            sprite,mood = self.next_look
            deferred = None
            if sprite.get("pose") == "p4":
                deferred = sprite,mood
                sprite,mood = self.white_poses["p1"],"neutral"
            self.pose_source = (canvas,self.base.get("pose","p1"))
            self.base = self.shown = sprite
            self.face_mood = mood
            self.transition_at,self.pose_seconds = now,.44
            self.face_swap = self.pose_brake = self.pose_curve = None
            self.next_look = deferred
        elif self.pose_source and not self.pose_brake and now-self.transition_at >= self.pose_seconds:
            self.pose_source = None
            self.source_rig = None
            self.pose_curve = None
            if self.next_look:
                sprite,mood=self.next_look
                self.next_look=None
                self.set_appearance(sprite,mood)

    def advance_face(self):
        now=time.monotonic()
        self.advance_pose(now)
        self.mouth_active,self.mouth = self.speech.level()
        dt = max(0.,min(.25,now-self.face_sample_at))
        self.face_sample_at = now
        target = self.mouth if self.mouth_active else 0.
        rate=45. if target>self.lip_opening else 22.
        self.lip_opening+=(target-self.lip_opening)*(-math.expm1(-dt*rate))
        if self.lip_opening<.006 and target==0.:
            self.lip_opening=0.
        self.speech_emphasis += (target-self.speech_emphasis)*(1-math.exp(-dt*5.))
        self.blink = 0.
        if now >= self.blink_at:
            v = (now-self.blink_at)/self.blink_length
            if v < 1:
                self.blink = smooth(v/.33) if v < .33 else 1-smooth((v-.33)/.67)
            else:
                self.blink_at = now+random.uniform(2.7,6.2)
                self.blink_length = random.uniform(.17,.25)
        if self.walk_brake is not None and self.drag_start is None:
            start,origin,displacement,seconds=self.walk_brake
            u=max(0.,min(1.,(now-start)/seconds))
            self.ambience.walk_to(origin+displacement*(u*(2.-u)))
            if u>=1 and not self.body.braking:
                self.walk_brake=None
                if self.base.get("pose")=="p4" and not (self.target_visit and self.target_visit.get("back")):
                    self.set_appearance(self.white_poses["p1"],"neutral")
        elif self.walk_goal is not None and self.drag_start is None:
            u = min(1.,(now-self.body.start)/max(.1,self.body.duration))
            fraction=self.body.travel_fraction(u)
            self.ambience.walk_to(self.walk_origin+(self.walk_destination-self.walk_origin)*fraction)
            if u >= 1:
                self.finish_walk()
        if not self.utterance and self.body.name != "settle" and now-self.body.start > self.body.duration+.12:
            self.act("settle",0)
        self.refresh_motion()

    def paintEvent(self, event):
        self.ambience.follow_activity(self.active_behavior)
        now = time.monotonic()
        ratio = self.devicePixelRatioF()
        logical_height = SIZES[self.size_name]
        display_height = round(logical_height*ratio)
        if self.paint_height is not None and self.paint_height != display_height:
            # The old mesh is registered to the former physical pixel size.
            if self.next_look:
                self.base,self.face_mood = self.next_look
                self.shown = self.base
            self.pose_source=self.source_rig=self.pose_frame=self.next_look=None
            self.pose_curve=self.pose_brake=None
            self.last_art=self.old_art=self.last_original=None
        self.paint_height = display_height
        pose = self.base.get("pose","p1")
        scale = .922 if pose == "p3" else 1.
        height = max(1,round(logical_height*ratio*scale))
        elapsed=max(0.,min(.1,now-self.look_sample_at))
        self.look_sample_at=now
        on_page=self.looking_at_book(self.body.name) and not self.utterance
        page_aim=(.58+.42*self.props.opening) if on_page else 0.
        # Keep the gaze through the first lift of the nib. The eyes leave
        # the paper while the forearm is still returning and before either foot moves.
        if self.props.releasing and now-self.last_touch<.1:
            page_aim=self.paper_focus
        self.paper_focus+=(page_aim-self.paper_focus)*(-math.expm1(-elapsed*5.8))
        aim_x=self.preview_look if self.body.name=="glance" else 0.
        aim_y=0.
        if self.target_visit and self.target_visit["phase"]!="recheck":
            target=self.target_visit["rect"].center()
            aim_x=max(-1.,min(1.,(target.x()-self.geometry().center().x())/(logical_height*.25)))
            aim_y=max(-1.,min(1.,(target.y()-self.y()-logical_height*.4)/(logical_height*.5)))
            if self.target_visit["phase"]=="walk":
                route=self.target_visit["goal"]-self.target_visit["start"]
                length=max(1.,math.hypot(route.x(),route.y()))
                aim_x=.65*route.x()/length+.25*aim_x
                aim_y=.40*route.y()/length+.30*aim_y
        elif self.walk_goal is not None:
            route=self.walk_destination-self.walk_origin
            length=max(1.,math.hypot(route.x(),route.y()))
            aim_x,aim_y=.58*route.x()/length,.42*route.y()/length
        elif self.active_behavior=="crab" and self.props.ground is not None:
            aim_x=max(-1.,min(1.,(self.props.ground.x()-self.geometry().center().x())/(logical_height*.25)))
            aim_y=.72
        elif self.utterance and self.utterance.get("diary"):
            view=getattr(self,"_journal_view",None)
            if view is not None and view.isVisible():
                target=view.reading_point(self.utterance.get("read_offset",0))
                aim_x=.74*max(-1.,min(1.,(target.x()-self.geometry().center().x())/(logical_height*.55)))
                aim_y=.70*max(-1.,min(1.,(target.y()-self.y()-logical_height*.4)/(logical_height*.55)))
            else:
                aim_x,aim_y=-.50,.65
        # The eyes acquire the object before the heavier head follows.
        aim_x=aim_x*(1-self.paper_focus)-self.paper_focus
        aim_y=aim_y*(1-self.paper_focus)+self.paper_focus
        self.head_look+=(self.eye_x*(1-.8*self.paper_focus)-self.head_look)*(-math.expm1(-elapsed*4.7))
        self.eye_x+=(aim_x-self.eye_x)*(-math.expm1(-elapsed*20.))
        self.eye_y+=(aim_y-self.eye_y)*(-math.expm1(-elapsed*20.))
        gaze=(round(self.eye_x*8),round(self.eye_y*8))
        on_paper_face=(on_page or self.paper_focus>.06) and not self.utterance
        original = self.expressions.render(self.base, "writing" if on_paper_face else self.face_mood, self.blink,
                                           self.lip_opening if (self.utterance or self.mouth_active or
                                               now-self.voice_released_at<.32) else None, height,self.speech_emphasis,
                                           paper_focus=self.paper_focus,gaze=gaze)
        if original is None:
            return
        if pose=="p1":
            self.props.warm_hand(original.size)
            if logical_height==280:
                from desktop_pose_assets import prepare_small_walk
                prepare_small_walk(*original.size)
        self.last_original=original,pose
        anchor_x = {"p2":6,"p3":11}.get(pose,0)*logical_height/390
        anchor_y = (3 if pose == "p3" else 0)*logical_height/390
        offset_x,offset_y = -original.width/2+anchor_x*ratio,-original.height+anchor_y*ratio
        target_rig = (pose,original.width,original.height,offset_x,offset_y)
        world_rig = ((target_rig,1.),)
        canvas = original
        if self.pose_source:
            start,previous_pose=self.pose_source
            progress,_=self.pose_timing(now)
            if "p4" in (previous_pose,pose):
                canvas,(offset_x,offset_y)=turn_pose(start,previous_pose,original,pose,progress)
            elif "p3" in (previous_pose,pose) or "atri_pose_registration" in start.info:
                canvas,(offset_x,offset_y)=bridge_pose(start,previous_pose,original,pose,progress,
                                                       pixel_center=self.width()*ratio/2)
            else:
                canvas,(offset_x,offset_y)=reach_pose(start,previous_pose,original,pose,progress)
            unit = logical_height*ratio/390
            prev_left = -start.width/2+{"p2":6,"p3":11}.get(previous_pose,0)*unit
            prev_top = -start.height+(3*unit if previous_pose=="p3" else 0)
            source_rig = self.source_rig or (((previous_pose,start.width,start.height,prev_left,prev_top),1.),)
            mix = smooth(progress)
            weights = {}
            for reg, value in source_rig:
                weights[reg] = weights.get(reg,0.) + value*(1.-mix)
            weights[target_rig] = weights.get(target_rig,0.) + mix
            world_rig = tuple((reg,value) for reg,value in weights.items() if value>1e-6)
        self.pose_frame = canvas,world_rig
        offset = self.mapFromGlobal(QCursor.pos()).x()-self.width()/2
        near = self.underMouse() and self.drag_start is None and self.automatic
        pointer = min(1,max(-1, offset/(self.width()*.45))) if near else 0.
        if self.target_visit:
            pointer=max(-1.,min(1.,(self.target_visit["rect"].center().x()-self.geometry().center().x())/(self.width()*.35)))
        self.body.look_direction=self.head_look
        channels,breath = self.body.sample(now,pointer)
        if self.speech_emphasis>.005:
            channels=channels.copy()
            channels[2]+=.18*self.speech_emphasis
            breath+=.10*self.speech_emphasis
        channels=self.props.support_body(channels,now)
        moving_material = self.fabric.sample(now,channels)
        local_rig = tuple(((p,w,h,round(x-offset_x),round(y-offset_y)),weight)
                          for (p,w,h,x,y),weight in world_rig)
        gesture_age = max(0.,now-self.body.start)
        prop_routine=self.book_routine()
        held = self.props.held_frame(routine=prop_routine,gesture=self.body.name,
                    pose=pose,dress=self.base.get("dress"),transitioning=self.pose_source is not None,
                    now=now,age=gesture_age,remaining=self.body.duration-gesture_age,
                    final=not self.steps,pointer_down=self.drag_start is not None,allow_preview=not self.active_behavior)
        if held:
            canvas,grip_size,grip_origin = self.props.prepare_hand(
                canvas,(offset_x,offset_y),pose,self.pose_source is not None)
        if pose in ("p1","p4") and not self.pose_source:
            travel=None
            if self.walk_brake is not None:
                travel=self.pos()-self.walk_brake[1]
            elif self.walk_start is not None:
                travel=self.pos()-self.walk_origin
            canvas=self.body.walk_art(canvas,now,travel,pose=pose)
        frame,pad = warp(canvas,pose,channels,breath,moving_material,local_rig)
        raw = frame.tobytes()
        image = QImage(raw,frame.width,frame.height,frame.width*4,QImage.Format.Format_RGBA8888)
        w,h=frame.width/ratio,frame.height/ratio
        floor=self.height()-self.foot_margin
        rect = QRectF(self.width()/2+(offset_x-pad)/ratio,floor+(offset_y-pad)/ratio,w,h)
        painter=QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        actor_rect=QRectF(self.width()/2-original.width/(2*ratio)+anchor_x,
                          floor-original.height/ratio+anchor_y,
                          original.width/ratio,original.height/ratio)
        standing_width = logical_height * self.props.front_aspect
        ground_rect = QRectF((self.width()-standing_width)/2, floor-logical_height,
                             standing_width, logical_height)
        self.props.paint(painter,routine=prop_routine,gesture=self.body.name,
                         actor_rect=actor_rect,pose=pose,body=channels,breath=breath,
                         now=now,window_pos=self.pos(),ground_rect=ground_rect,foreground_diary=True,
                         allow_preview=not self.active_behavior)
        progress=smooth((now-self.transition_at)/.24)
        if self.old_art and progress<1 and not held:
            old, old_rect=self.old_art
            painter.setOpacity(1-progress)
            painter.drawImage(old_rect,old)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            painter.setOpacity(progress)
        else:
            self.old_art=None
        painter.drawImage(rect,image)
        if held:
            grip_rect=QRectF(self.width()/2+grip_origin[0]/ratio,floor+grip_origin[1]/ratio,
                             grip_size[0]/ratio,grip_size[1]/ratio)
            self.props.paint_grip(painter,held,grip_size,grip_rect,channels,breath)
        painter.end()
        self.last_art = (image.copy(),rect)


    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.interrupt(settle=False)
            self.drag_start = event.globalPosition().toPoint()
            self.origin = self.pos()
            self.last_drag_at = time.monotonic()
            self.dragged = False
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_start is None:
            return
        delta = event.globalPosition().toPoint() - self.drag_start
        if self.dragged or delta.manhattanLength() >= QApplication.startDragDistance():
            if not self.dragged:
                self.act("drag", math.inf)
            self.dragged = True
            target = self.origin + delta
            travel = target-self.pos()
            now = time.monotonic()
            self.body.follow_drag(travel.x(),travel.y(),SIZES[self.size_name],now,self.last_drag_at)
            self.last_drag_at = now
            self.move(target)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.drag_start is not None:
            if self.dragged:
                self.place(self.pos())
                self.home = QPoint(self.pos())
                self.standing_since=time.monotonic()
                self.walk_history.clear()
                self.walk_headings.clear()
                self.prefs.setValue("position", self.pos())
                self.act("land", .9)
                self.schedule_idle()
            else:
                self.speak()
            self.drag_start = None
            event.accept()

    def enterEvent(self, event):
        # Entering transparent space above a subtitle should not start a response.
        if (self.automatic and self.drag_start is None and not self.active_behavior
                and not self.utterance and self.rect().contains(self.mapFromGlobal(QCursor.pos()))
                and time.monotonic()-self.last_touch > 3 and self.body.name == "settle"):
            self.act("glance", 1.8)
        super().enterEvent(event)

    def contextMenuEvent(self, event):
        self.menu.popup(event.globalPos())

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show()
            self.raise_()
            self.speak()

    def open_archive(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT / "index.html")))

    def closeEvent(self, event):
        self.closed = True
        self.target_elapsed.stop()
        self.media_timer.stop()
        self.media_reader.close()
        self.diary_clock.stop()
        self.diary_action.stop()
        if self.journal_day:
            self.journal.save(self.observe_journal_day())
        self.conversation.close()
        self.context_generation += 1
        self.sense_timer.stop()
        self.idle.stop()
        self.next_step.stop()
        self.stop_speech()
        self.motion.stop()
        self.tray.hide()
        self.prefs.setValue("position", self.home if self.home is not None else self.pos())
        self.prefs.sync()
        event.accept()
        QApplication.quit()


if __name__ == "__main__":
    lock = instance_lock()
    if lock is None:
        sys.exit(0)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    try:
        pet = Companion()
    except (OSError, ValueError, KeyError) as error:
        QMessageBox.critical(None, "桌面亚托莉", f"无法读取项目素材。\n{error}")
        sys.exit(1)
    pet.show()
    sys.exit(app.exec())
