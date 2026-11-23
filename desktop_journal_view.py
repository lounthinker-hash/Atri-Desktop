"""Date navigation around the novel message window used by scene b402."""
import json
import os
from datetime import date
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageFilter
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont,QFontDatabase,QImage,QPainter,QTextBlockFormat,QTextCharFormat,QTextCursor,QTextOption
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)
from desktop_journal_stickers import paint_stickers


ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def _handwriting_families():
    directory=ROOT/"assets"/"props"/"diary-reading"/"handwriting"
    manifest=json.loads((directory/"manifest.json").read_text(encoding="utf-8-sig"))
    identifier=QFontDatabase.addApplicationFont(str(directory/manifest["file"]))
    families=list(QFontDatabase.applicationFontFamilies(identifier)) if identifier>=0 else []
    # Keep rare ideographs and emoji readable using installed system fallbacks.
    windows=Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts"
    for filename,family in (("msyh.ttc","Microsoft YaHei"),("simsunb.ttf","SimSun-ExtB"),
                            ("seguiemj.ttf","Segoe UI Emoji")):
        if family not in QFontDatabase.families() and (windows/filename).is_file():
            idx=QFontDatabase.addApplicationFont(str(windows/filename))
            if idx>=0:
                families.extend(QFontDatabase.applicationFontFamilies(idx))
        else:
            families.append(family)
    return tuple(dict.fromkeys(families))


class NovelPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        directory = ROOT / "assets/props/diary-reading"
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8-sig"))
        novel = manifest["originalNovel"]
        self.source = directory / novel["file"]
        self.source_entry = manifest.get("assets", {}).get("novelMessageBackground", {}).get("entry", novel["file"])
        self.image = QImage(str(self.source))
        self.message_rect = QRectF(*novel["messageRectXYWH"])
        w, h = novel["canvas"]
        if self.image.isNull() or (self.image.width(), self.image.height()) != (w, h):
            raise ValueError("原作日志阅读底板与清单尺寸不符。")
        # b402 *start, row 294. Scene snapshot 447 retains bg002 (two 10 px
        # box blurs) and item039b (200%, x+320, y-160) behind the novel layer.
        with Image.open(ROOT / "assets/scenery/bg002.webp") as stage:
            bg = stage.convert("RGB").resize((w, h), Image.Resampling.LANCZOS)
        bg = bg.filter(ImageFilter.BoxBlur(10)).filter(ImageFilter.BoxBlur(10))
        rgba = bg.convert("RGBA").tobytes()
        self.scene = QImage(rgba, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
        book = QImage(str(directory / manifest["paper"]["file"]))
        painter = QPainter(self.scene)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bw, bh = 2 * book.width(), 2 * book.height()
        painter.drawImage(QRectF((w - bw) / 2 + 320, (h - bh) / 2 - 160, bw, bh), book)
        painter.drawImage(0, 0, self.image)
        painter.end()
        self.entries = QTextBrowser(self)
        self.entries.setAccessibleName("当日日记正文")
        self.entries.setFrameShape(QFrame.Shape.NoFrame)
        self.entries.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.entries.document().setDocumentMargin(0)
        self.entries.setStyleSheet("QTextBrowser {background:transparent;color:#fcfcfd;border:0}"
            "QScrollBar:vertical {background:transparent;width:7px}"
            "QScrollBar::handle:vertical {background:#97a4b8;border-radius:3px;min-height:20px}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0}")
        self.entries.viewport().setAutoFillBackground(False)
        wrap=self.entries.document().defaultTextOption()
        wrap.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.entries.document().setDefaultTextOption(wrap)
        self.families=_handwriting_families()
        self.paragraphs=[]
        self.font_px=18
        self.rows = []
        self.day = ""
        self.stage_rect = QRectF()
        self.stickers=[]
        self.sticker_rects=[]
        self.visible_sticker_ids=[]
        self.entry_positions=[]
        self.entries.verticalScrollBar().valueChanged.connect(lambda _value:self.update())

    def write(self, day, rows, error, pending=False):
        self.day, self.rows = day, rows
        if error:
            self.paragraphs=["暂时无法读取日记。请稍后刷新。"]
        elif not rows:
            self.paragraphs=[] if pending else ["这一天还没有记录。" if day else "日记里还没有记录。"]
        else:
            heading = date.fromisoformat(day).strftime("%m/%d")
            self.paragraphs=[heading]+[row["text"] for row in rows]
        self._typeset()
        self.entries.verticalScrollBar().setValue(0)

    def _typeset(self):
        font=QFont()
        font.setFamilies(list(self.families))
        font.setPixelSize(self.font_px)
        document=self.entries.document()
        document.clear()
        document.setDefaultFont(font)
        cursor=QTextCursor(document)
        char=QTextCharFormat()
        char.setFont(font)
        self.entry_positions=[]
        for i,paragraph in enumerate(self.paragraphs):
            block=QTextBlockFormat()
            block.setLineHeight(128,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            block.setBottomMargin(self.font_px*(.64 if i==0 else .48))
            if i:
                cursor.insertBlock(block,char)
            else:
                cursor.setBlockFormat(block)
            if i:
                self.entry_positions.append(cursor.position())
            cursor.insertText(str(paragraph).replace("\r\n","\n").replace("\r","\n"),char)

    def resizeEvent(self, event):
        w, h = self.scene.width(), self.scene.height()
        scale = min(self.width() / w, self.height() / h)
        left, top = (self.width()-w*scale)/2, (self.height()-h*scale)/2
        self.stage_rect = QRectF(left, top, w*scale, h*scale)
        msg = self.message_rect
        self.entries.setGeometry(QRectF(left+msg.x()*scale, top+msg.y()*scale,
            msg.width()*scale, msg.height()*scale).toAlignedRect())
        px=max(15,round(31*scale))
        if px!=self.font_px:
            bar=self.entries.verticalScrollBar()
            fraction=bar.value()/bar.maximum() if bar.maximum() else 0
            self.font_px=px
            self._typeset()
            bar.setValue(round(bar.maximum()*fraction))
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(self.stage_rect, self.scene)
        stage=self.stage_rect
        scale=stage.width()/self.scene.width()
        margin=QRectF(stage.x()+52*scale,stage.y()+110*scale,164*scale,490*scale)
        visible_height=self.entries.viewport().height()
        anchors=[]
        for row in self.stickers:
            index=row.get("anchorEntryIndex",row.get("anchorParagraph",0))
            y=None
            if 0<=index<len(self.entry_positions) and index<len(self.rows) and visible_height:
                text=self.rows[index]["text"]
                offset=min(len(text),max(0,row.get("anchorOffset",0)))
                # QTextCursor uses UTF-16 units; diary evidence uses Python characters.
                before=text[:offset].replace("\r\n","\n").replace("\r","\n")
                pos=self.entry_positions[index]+len(before.encode("utf-16-le"))//2
                cursor=QTextCursor(self.entries.document())
                cursor.setPosition(min(pos,self.entries.document().characterCount()-1))
                line=self.entries.cursorRect(cursor)
                if line.bottom()>=0 and line.top()<visible_height:
                    y=margin.top()+margin.height()*line.center().y()/visible_height
            anchors.append(y)
        self.visible_sticker_ids=[]
        self.sticker_rects=paint_stickers(painter,self.stickers,margin,anchors=anchors,
                                          drawn_ids=self.visible_sticker_ids)
        painter.end()


class JournalView(QDialog):
    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Window)
        self.journal = owner.journal
        self.owner = owner
        self.pending = {}
        self.reading_state="idle"
        self.reading_notice=""
        self.job_notice=""
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("亚托莉的日记")
        self.setMinimumSize(555, 365)
        self.resize(835, 520)
        self.setStyleSheet("QDialog {background:#182741}"
            "QComboBox,QPushButton {background:#2d3e58;color:#f3f4f7;border:1px solid #56657a;"
            "border-radius:3px;padding:4px 9px} QPushButton:disabled {color:#9aa3b1}"
            "QComboBox QAbstractItemView {background:#34445c;color:#f3f4f7}")
        self.older = QPushButton("上一天")
        self.newer = QPushButton("下一天")
        self.days = QComboBox()
        self.days.setAccessibleName("选择日记日期")
        self.days.setMinimumContentsLength(11)
        self.refresh_button = QPushButton("刷新")
        self.read_button = QPushButton("朗读这篇")
        self.older.clicked.connect(lambda: self.days.setCurrentIndex(self.days.currentIndex()+1))
        self.newer.clicked.connect(lambda: self.days.setCurrentIndex(self.days.currentIndex()-1))
        self.refresh_button.clicked.connect(self.refresh)
        self.read_button.clicked.connect(self.toggle_reading)
        if hasattr(owner,"reading_changed"):
            owner.reading_changed.connect(self.set_reading_state)
        row = QHBoxLayout()
        row.setContentsMargins(10, 5, 10, 5)
        row.addWidget(self.older)
        row.addWidget(self.days)
        row.addWidget(self.newer)
        row.addStretch()
        row.addWidget(self.read_button)
        row.addWidget(self.refresh_button)
        self.paper = NovelPage(self)
        self.status_row=QWidget(self)
        status_layout=QHBoxLayout(self.status_row)
        status_layout.setContentsMargins(12,2,10,3)
        self.status_label=QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setStyleSheet('color:#f2e6c9')
        self.status_label.setWordWrap(True)
        self.retry_button=QPushButton("重试撰写")
        self.retry_button.clicked.connect(self.retry)
        status_layout.addWidget(self.status_label,1)
        status_layout.addWidget(self.retry_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(row)
        layout.addWidget(self.status_row)
        layout.addWidget(self.paper, 1)
        self.days.currentIndexChanged.connect(self.read_selected)
        self.finished.connect(self.stop_on_close)
        if hasattr(owner,"conversation_changed"):
            owner.conversation_changed.connect(self.refresh)
        self.refresh()

    def retry(self):
        self.owner.retry_conversation()
        self.refresh()

    def toggle_reading(self):
        if self.reading_state in ("preparing","playing"):
            self.owner.stop_diary_reading()
        else:
            self.owner.read_diary_aloud("\n".join(row["text"] for row in self.paper.rows))

    def reading_point(self,offset):
        viewport=self.paper.entries.viewport()
        document=self.paper.entries.document()
        mark=(document.revision(),offset)
        if getattr(self,"_reading_mark",None)!=mark:
            text=document.toPlainText()
            body_start=text.find("\n")+1
            cursor=QTextCursor(document)
            position=len(text[:body_start+offset].encode("utf-16-le"))//2
            cursor.setPosition(min(position,document.characterCount()-1))
            self._reading_mark,self._reading_cursor=mark,cursor
        point=self.paper.entries.cursorRect(self._reading_cursor).center()
        point.setY(min(max(0,point.y()),max(0,viewport.height()-1)))
        return viewport.mapToGlobal(point)

    def set_reading_state(self,state,notice=""):
        self.reading_state,self.reading_notice=state,notice
        self.update_reading_controls()

    def update_reading_controls(self):
        muted=getattr(self.owner,"muted",False)
        configured=bool(getattr(self.owner,"voice_lookup",True))
        labels={"idle":"朗读这篇","preparing":"准备中 · 停止","playing":"停止朗读","failed":"重试朗读"}
        self.read_button.setText("未配置日语语音" if not configured else "已静音" if muted else labels[self.reading_state])
        self.read_button.setToolTip("" if configured else "见 docs/local-setup.md 的本地日语语音配置")
        self.read_button.setEnabled(bool(self.paper.rows) and not muted and configured)
        notice="角色已静音，取消静音后即可朗读。" if muted and self.paper.rows else self.reading_notice
        self.status_label.setText("\n".join(filter(None,(self.job_notice,notice))))
        self.status_row.setVisible(bool(self.job_notice or notice))

    def stop_on_close(self):
        if self.reading_state in ("preparing","playing"):
            self.owner.stop_diary_reading()

    def refresh(self):
        selected = self.days.currentText()
        jobs=self.journal.pending_jobs("diary") if hasattr(self.journal,"pending_jobs") else []
        priority={"queued":0,"running":1,"failed":2,"uncertain":3}
        self.pending={}
        for job in jobs:
            day=job["day"]
            if day not in self.pending or priority[job["state"]]>priority[self.pending[day]["state"]]:
                self.pending[day]=job
        dates = sorted(set(self.journal.days()) | set(self.pending),reverse=True)
        self.days.blockSignals(True)
        self.days.clear()
        self.days.addItems(dates)
        if selected in dates:
            self.days.setCurrentText(selected)
        self.days.blockSignals(False)
        self.read_selected()

    def read_selected(self):
        i = self.days.currentIndex()
        self.older.setEnabled(0 <= i < self.days.count()-1)
        self.newer.setEnabled(i > 0)
        self.days.setEnabled(self.days.count() > 0)
        day = self.days.currentText()
        rows = self.journal.entries(day) if day else []
        self.read_button.setEnabled(bool(rows))
        job=self.pending.get(day)
        state=job["state"] if job else None
        labels={"queued":"这一天的日记已排队。","running":"亚托莉正在写这一天的日记。",
                "failed":"这一天的日记未能写完，可以重试。",
                "uncertain":"上次发送的回执未确认，重试可能在角色会话里重复出现。"}
        message=labels.get(state,"")
        if state=="failed" and job.get("error"):
            message+=" "+str(job["error"])
        self.job_notice=message
        self.retry_button.setVisible(state in ("failed","uncertain"))
        if hasattr(self.journal,"stickers"):
            self.paper.stickers=self.journal.stickers(day) if day else []
        error=self.journal.error
        contents=(day,tuple(row["text"] for row in rows),bool(error),bool(job and not rows))
        if getattr(self,"_contents",None)!=contents:
            previous=getattr(self,"_contents",None)
            if previous is not None and previous[:2]!=contents[:2]:
                if self.reading_state in ("preparing","playing"):
                    self.owner.stop_diary_reading()
                self.reading_state,self.reading_notice="idle",""
            self.paper.write(day,rows,error,pending=bool(job))
            self._contents=contents
        self.update_reading_controls()
        self.paper.update()


def show_journal(owner):
    window = getattr(owner, "_journal_view", None)
    if window is None:
        window = JournalView(owner)
        owner._journal_view = window
        window.destroyed.connect(lambda *_: setattr(owner, "_journal_view", None))
    else:
        window.refresh()
    window.showNormal()
    window.raise_()
    window.activateWindow()
    return window
