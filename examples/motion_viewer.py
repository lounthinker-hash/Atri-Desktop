"""Interactive preview of Atri's motion controller on a simple joint diagram."""
import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import desktop_stride
from desktop_motion import BodyMotion, GESTURES, attached_point


CUES = ("settle", "greet", "listen", "write", "step", "hop")
CHANNELS = ("重心", "头倾", "低头", "展臂", "抬手", "步相", "起跳", "手腕", "跟随")
DURATION = 3.2
RIG_WIDTH, RIG_HEIGHT = 208, 296
# A neutral diagram in normalized coordinates, independent of the character artwork.
JOINTS = {
    "head": (.50, .17), "neck": (.50, .30), "hip": (.50, .62),
    "ls": (.32, .33), "le": (.22, .46), "lw": (.18, .57),
    "rs": (.68, .33), "re": (.78, .46), "rw": (.82, .57),
    "lh": (.39, .63), "lk": (.33, .79), "lf": (.32, .97),
    "rh": (.61, .63), "rk": (.66, .79), "rf": (.68, .97),
}
UV = np.array(list(JOINTS.values())).T


class Stage(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 320)
        self.now = 0.
        self.motion = BodyMotion(self.now)
        self.body, self.breath = self.motion.sample(self.now)
        self.origin_x = 0.
        self.walk_from = 0.
        self.distance = 0.

    def start(self, name):
        self.walk_from = self.origin_x
        self.motion.start_gesture(name, DURATION, self.now)
        self.distance = 0.
        if name == "step":
            self.distance = 82. if self.origin_x <= 0 else -82.
            self.motion.plan_walk((self.distance, 0.), RIG_HEIGHT)

    def advance(self, dt):
        self.now += dt
        self.body, self.breath = self.motion.sample(self.now)
        phase = min(1., (self.now - self.motion.start) / DURATION)
        self.origin_x = self.walk_from + self.distance * self.motion.travel_fraction(phase)
        self.update()

    def joints(self):
        x, y = attached_point(UV, RIG_WIDTH, RIG_HEIGHT, "p1", self.body, self.breath)
        points = {name: QPointF(float(a), float(b)) for name, a, b in zip(JOINTS, x, y)}
        if self.distance:
            phase = min(1., (self.now - self.motion.start) / DURATION)
            travel = self.motion.walk_distance
            world = travel * self.motion.travel_fraction(phase)
            legs, _ = desktop_stride.stance("step", phase, travel, world, DURATION)
            for side, (dx, dy, *_rest) in zip(("l", "r"), legs):
                delta = QPointF(dx * RIG_HEIGHT, dy * RIG_HEIGHT)
                points[side + "f"] += delta
                points[side + "k"] += delta * .55
        return points

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#192b35"))
        # Scale uniformly; leave room for a complete step in either direction.
        scale = min(self.width() / 455, self.height() / 360)
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(scale, scale)
        floor_y = RIG_HEIGHT * .47
        p.setPen(QPen(QColor("#435963"), 1))
        p.drawLine(QPointF(-300, floor_y), QPointF(300, floor_y))
        for tick in range(-280, 281, 20):
            p.drawLine(QPointF(tick, floor_y + 1), QPointF(tick, floor_y + 6))
        p.translate(-RIG_WIDTH / 2 + self.origin_x, -RIG_HEIGHT / 2)
        joints = self.joints()

        def chain(names, color, width):
            p.setPen(QPen(QColor("#60717a"), 1.5, Qt.PenStyle.DashLine))
            neutral = [QPointF(JOINTS[key][0] * RIG_WIDTH, JOINTS[key][1] * RIG_HEIGHT)
                       for key in names]
            for a, b in zip(neutral, neutral[1:]):
                p.drawLine(a, b)
            pen = QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for a, b in zip(names, names[1:]):
                p.drawLine(joints[a], joints[b])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#192b35"))
            for key in names[1:-1]:
                p.drawEllipse(joints[key], width * .24, width * .24)

        chain(("lf", "lk", "lh", "hip", "rh", "rk", "rf"), "#a9c5cc", 9)
        chain(("lw", "le", "ls", "neck", "hip"), "#7cc6c0", 9)
        chain(("neck", "rs", "re", "rw"), "#ffbb8e", 9)
        chain(("neck", "head"), "#dfedea", 7)
        p.translate(joints["head"])
        p.rotate(float(self.body[1]) * 11)
        p.setPen(QPen(QColor("#dfedea"), 3))
        p.setBrush(QColor("#263f49"))
        p.drawRoundedRect(QRectF(-22, -25, 44, 48), 15, 15)
        p.setPen(QPen(QColor("#9fe2db"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(-10, -1), QPointF(10, -1))
        p.end()


class Viewer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Atri · 动作预览")
        self.resize(744, 492)
        self.setStyleSheet("""
            QWidget {background:#f4f7f5; color:#203239; font-size:14px;}
            QLabel#heading {font-size:24px; font-weight:600;}
            QLabel#caption {color:#45615e;}
            QPushButton {padding:7px 11px; border:1px solid #cedbd7;
                         border-radius:5px; background:#ffffff;}
            QPushButton:checked {background:#285c5a; color:white; border-color:#285c5a;}
            QPushButton:focus {border-color:#29887e;}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        heading = QLabel("Atri · 动作预览")
        heading.setObjectName("heading")
        root.addWidget(heading)
        main = QHBoxLayout()
        root.addLayout(main, 1)
        self.stage = Stage()
        main.addWidget(self.stage, 1)
        panel = QVBoxLayout()
        panel.setContentsMargins(13, 1, 0, 0)
        main.addLayout(panel)
        self.cue_name = QLabel()
        self.cue_name.setObjectName("caption")
        panel.addWidget(self.cue_name)
        values = QGridLayout()
        panel.addLayout(values)
        self.readouts = []
        for row, label in enumerate((*CHANNELS, "呼吸")):
            values.addWidget(QLabel(label), row, 0)
            reading = QLabel("0.00")
            reading.setMinimumWidth(63)
            reading.setAlignment(Qt.AlignmentFlag.AlignRight)
            values.addWidget(reading, row, 1)
            self.readouts.append(reading)
        panel.addStretch()
        note = QLabel("虚线为基准站姿")
        note.setObjectName("caption")
        panel.addWidget(note)
        self.pause = QPushButton("暂停")
        self.pause.setCheckable(True)
        self.pause.toggled.connect(lambda paused: self.pause.setText("继续" if paused else "暂停"))
        panel.addWidget(self.pause)
        controls = QHBoxLayout()
        root.addLayout(controls)
        self.buttons = {}
        self.group = QButtonGroup(self)
        for name in CUES:
            button = QPushButton(GESTURES[name].label)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, cue=name: self.select(cue))
            self.group.addButton(button)
            controls.addWidget(button)
            self.buttons[name] = button
        self.select("settle")
        self.previous_tick = time.monotonic()
        self.timer = QTimer(self, interval=16)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def select(self, name):
        self.buttons[name].setChecked(True)
        self.stage.start(name)
        self.pause.setChecked(False)
        self.refresh_readouts()

    def refresh_readouts(self):
        stage = self.stage
        settled = stage.now - stage.motion.start >= DURATION
        self.cue_name.setText("settle" if settled else stage.motion.name)
        for label, value in zip(self.readouts, (*stage.body, stage.breath)):
            label.setText(f"{value:+.2f}")

    def tick(self):
        now = time.monotonic()
        if not self.pause.isChecked():
            self.stage.advance(now - self.previous_tick)
            self.refresh_readouts()
        self.previous_tick = now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-test", action="store_true", help="Check gestures offscreen and exit")
    parser.add_argument("--screenshot", type=Path, help="Save a fixed writing pose and exit")
    args = parser.parse_args()
    if args.smoke_test or args.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication([sys.argv[0]])
    if app.platformName() == "offscreen" and os.name == "nt":
        # Qt's offscreen platform can start with an empty Windows font database.
        fonts = Path(os.environ["WINDIR"]) / "Fonts"
        for filename in ("msyh.ttc", "msyhbd.ttc"):
            QFontDatabase.addApplicationFont(str(fonts / filename))
    if "Microsoft YaHei UI" in QFontDatabase.families():
        app.setFont(QFont("Microsoft YaHei UI", 10))
    view = Viewer()
    view.show()
    if not args.smoke_test and not args.screenshot:
        return app.exec()
    view.timer.stop()
    app.processEvents()
    view.stage.motion.seed = 0.
    frames = set()
    wrist_height = {}
    for cue in ("settle", "step", "listen", "hop", "greet", "write"):
        view.buttons[cue].click()
        for _ in range(45):
            view.stage.advance(1 / 60)
        # Compare the canvas alone; labels and button state cannot change this result.
        image = view.stage.grab().toImage()
        if image.isNull() or view.stage.motion.name != cue:
            raise RuntimeError(f"Could not render {cue}")
        frames.add(bytes(image.constBits()))
        wrist_height[cue] = view.stage.joints()["rw"].y()
    if len(frames) != len(CUES) or wrist_height["settle"] - wrist_height["write"] < 10:
        raise RuntimeError("The rendered gestures did not follow the motion controller")
    view.refresh_readouts()
    if args.screenshot and not view.grab().save(str(args.screenshot)):
        raise OSError(f"Could not save {args.screenshot}")
    print(f"OK: {len(frames)} gestures rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
