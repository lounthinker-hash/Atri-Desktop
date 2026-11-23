"""Qt chat window for the companion's local conversation history."""
from collections import Counter
from html import escape
import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor,QFont,QFontDatabase,QPalette
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout


class Composer(QPlainTextEdit):
    submit=Signal()

    def keyPressEvent(self,event):
        if (event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter)
                and not event.modifiers() & (Qt.KeyboardModifier.ShiftModifier|Qt.KeyboardModifier.ControlModifier)
                and not getattr(self,"composing",False)):
            event.accept()
            self.submit.emit()
            return
        super().keyPressEvent(event)

    def inputMethodEvent(self,event):
        self.composing=bool(event.preeditString())
        super().inputMethodEvent(event)


class ChatView(QDialog):
    def __init__(self,owner):
        super().__init__(owner,Qt.WindowType.Window)
        self.owner=owner
        self.unconfirmed=[]
        if "Microsoft YaHei" not in QFontDatabase.families():
            font_file=Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts/msyh.ttc"
            if font_file.is_file():QFontDatabase.addApplicationFont(str(font_file))
        self.setFont(QFont("Microsoft YaHei",10))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("和亚托莉聊天")
        self.setMinimumSize(350,340)
        self.resize(505,570)
        self.setStyleSheet("QDialog {background:#f6f3ee} "
                "QPlainTextEdit {background:#fffdfa;color:#242c2b;border:1px solid #9dada8;"
                "border-radius:5px;padding:7px;font-size:15px} "
                "QPushButton {background:#335e59;color:white;border:0;border-radius:4px;padding:7px 15px}"
                "QPushButton:disabled {background:#d5dedb;color:#63716d}"
                "QLabel {color:#4b5c56} QTextBrowser {background:transparent;color:#242c2b;border:0;font-size:15px}")
        self.history=QTextBrowser()
        self.history.document().setDefaultFont(self.font())
        self.history.setAccessibleName("聊天记录")
        self.history.setOpenExternalLinks(False)
        self.history.setFrameShape(QFrame.Shape.NoFrame)
        self.entry=Composer()
        self.entry.setAccessibleName("消息正文")
        self.entry.setPlaceholderText("给亚托莉写消息…")
        pal=self.entry.palette();pal.setColor(QPalette.ColorRole.PlaceholderText,QColor("#65766f"));self.entry.setPalette(pal)
        self.entry.setMaximumHeight(88)
        self.entry.setTabChangesFocus(True)
        self.send_button=QPushButton("发送")
        self.retry_button=QPushButton("重试")
        self.status=QLabel()
        self.status.setAccessibleName("回复状态")
        self.retry_button.clicked.connect(self.retry)
        self.send_button.clicked.connect(self.submit)
        self.entry.submit.connect(self.submit)
        self.entry.textChanged.connect(self.toggle_send)
        actions=QHBoxLayout()
        actions.addWidget(self.status,1)
        actions.addWidget(self.retry_button);actions.addWidget(self.send_button)
        layout=QVBoxLayout(self)
        layout.setContentsMargins(15,12,15,13);layout.setSpacing(8)
        layout.addWidget(self.history,1);layout.addWidget(self.entry);layout.addLayout(actions)
        owner.conversation_changed.connect(self.refresh)
        self.toggle_send();self.refresh(scroll=True)

    def toggle_send(self):
        self.send_button.setEnabled(bool(self.entry.toPlainText().strip()))

    def refresh(self,*_,scroll=False):
        bar=self.history.verticalScrollBar()
        pos=bar.value()
        at_end=scroll or pos>=bar.maximum()-8
        rows=[dict(row) for row in self.owner.journal.dialogue()]
        counts=Counter(str(row["text"]) for row in rows if row.get("role")=="user")
        self.unconfirmed=[(text,sequence) for text,sequence in self.unconfirmed if counts[text]<sequence]
        rows.extend({"role":"user","text":text,"state":"pending"} for text,_ in self.unconfirmed)
        parts=[]
        failed=False
        for i,row in enumerate(rows):
            user=row.get("role")=="user"
            state=row.get("state","")
            body=str(row.get("text") or "")
            if not user and not body.strip():
                if state=="uncertain":
                    failed=True
                    parts.append('<p style="color:#864625;margin:4px 0">上次发送的回执未确认，重试可能在角色会话里重复出现。</p>')
                elif state=="failed":
                    failed=True
                    detail=escape(str(row.get("error") or "这条消息尚未收到回复。"))
                    parts.append(f'<p style="color:#864625;margin:4px 0">回复失败 · {detail}</p>')
                continue
            title="你" if user else "亚托莉"
            paired=rows[i+1] if user and i+1<len(rows) and rows[i+1].get("role")=="assistant" else {}
            if user and paired.get("state")=="queued":title+=" · 排队中"
            elif user and paired.get("state")=="running":title+=" · 正在回复"
            elif state=="pending":title+=" · "+("排队中" if user else "正在回复")
            elif user and i==len(rows)-1:title+=" · 已提交"
            text=escape(body)
            align="right" if user else "left"
            palette="#deebe6" if user else "#ede7dc"
            parts.append(f'<p align="{align}" style="margin:12px 0 3px;color:#536962;font-size:12px">{title}</p>'
                         f'<table align="{align}" width="84%" cellspacing="0" cellpadding="9"><tr>'
                         f'<td bgcolor="{palette}"><span style="white-space:pre-wrap">{text}</span>'
                         '</td></tr></table><p style="margin:0;font-size:2px">&nbsp;</p>')
        self.history.setHtml("".join(parts))
        bar.setValue(bar.maximum() if at_end else min(pos,bar.maximum()))
        state=str(self.owner.dialogue_status or "就绪")
        self.status.setText(state)
        self.retry_button.setVisible(failed or "失败" in state or "重试" in state)

    def submit(self):
        text=self.entry.toPlainText().strip()
        if not text:return
        existing=sum(1 for r in self.owner.journal.dialogue() if r["role"]=="user" and r["text"]==text)
        sequence=max([existing]+[n for t,n in self.unconfirmed if t==text])+1
        self.unconfirmed.append((text,sequence))
        try:
            accepted=self.owner.send_chat(text)
        except (ValueError,OSError) as err:
            self.unconfirmed=[v for v in self.unconfirmed if v!=(text,sequence)]
            self.status.setText(str(err));return
        if accepted is False:
            self.unconfirmed=[v for v in self.unconfirmed if v!=(text,sequence)]
            self.refresh();return
        self.entry.clear();self.refresh(scroll=True);self.entry.setFocus()

    def retry(self):
        self.owner.retry_conversation()
        self.refresh(scroll=True)


def show_chat(owner):
    window=getattr(owner,"_chat_view",None)
    if window is None:
        window=ChatView(owner)
        owner._chat_view=window
        window.destroyed.connect(lambda *_:setattr(owner,"_chat_view",None))
    else:
        window.refresh()
    window.showNormal()
    window.raise_()
    window.activateWindow()
    window.entry.setFocus()
    return window
