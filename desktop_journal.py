"""Immutable observations, previous diary editions and the local calendar."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3

from desktop_memory_store import MemoryStore, SCHEMA


class Journal(MemoryStore):
    def __init__(self, path, clock=None):
        self.path = Path(path)
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.error = None
        self.ready = False

    def _db(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db = sqlite3.connect(self.path,timeout=2)
        db.row_factory = sqlite3.Row
        if not self.ready:
            try:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS evidence (
                        day TEXT, fact TEXT, source TEXT NOT NULL, wording TEXT NOT NULL,
                        observed TEXT NOT NULL, PRIMARY KEY(day, fact)
                    );
                    CREATE TABLE IF NOT EXISTS entries (
                        id INTEGER PRIMARY KEY, day TEXT NOT NULL, fact TEXT NOT NULL,
                        text TEXT NOT NULL, written TEXT NOT NULL, UNIQUE(day, fact)
                    );
                """+SCHEMA)
            except sqlite3.Error:
                db.close()
                raise
            self.ready = True
        return db

    def _observe(self, fact, source, observation):
        now = self.clock()
        try:
            with closing(self._db()) as db, db:
                db.execute("INSERT OR IGNORE INTO evidence VALUES (?, ?, ?, ?, ?)",
                           (now.date().isoformat(),fact,source,observation,now.isoformat()))
            self.error = None
        except (OSError,sqlite3.Error) as exc:
            self.error = str(exc)

    def presence(self, visible=True):
        self._observe("presence", "desktop-visible" if visible else "app-running",
                      "当天角色桌面窗口曾经可见。" if visible else "当天程序确实运行过，窗口当时隐藏。")

    def activity(self, routine, gesture, routine_name=None, gesture_name=None):
        if routine in ("notes","evening_log") or gesture=="settle":
            return  # Writing or watching for new facts must not generate its own next job.
        source = f"activity/{routine}/{gesture}"
        self._observe(source,source,f"在「{routine_name or routine}」活动中，亚托莉实际做了「{gesture_name or gesture}」。")

    def spoken(self, clip, audible, manual=True):
        text = (clip.get("zh") or "").strip()
        if text:
            source = ("manual-line/" if manual else "activity-line/")+clip["id"]
            context = "用户点播了原作台词。" if manual else "自主活动播放了原作台词。"
            medium = "亚托莉播放录音，字幕为" if audible else "亚托莉显示字幕，内容为"
            self._observe(source,source,context+medium+"「"+text+"」。")

    def _rows(self, sql, params=()):
        try:
            with closing(self._db()) as db:
                rows = [dict(row) for row in db.execute(sql,params)]
            self.error = None
            return rows
        except (OSError,sqlite3.Error) as exc:
            self.error = str(exc)
            return []

    def days(self):
        return [r["day"] for r in self._rows("SELECT DISTINCT day FROM entries ORDER BY day DESC")]

    def entries(self, day):
        return self._rows("""SELECT n.text, n.written, COALESCE(e.source,n.fact) AS source,
                        COALESCE(e.observed,n.written) AS observed FROM entries n
                        LEFT JOIN evidence e USING(day,fact) WHERE n.day=? ORDER BY n.id""",(day,))

    def save(self, day=None):
        try:
            result=self.queue_diary(day or self.clock().date().isoformat())
            self.error=None
            return result
        except (OSError,sqlite3.Error) as err:
            self.error=str(err)
            return None

    def finish_previous_days(self):
        try:
            for day in self.unfinished_days(self.clock().date().isoformat()):
                self.queue_diary(day)
            self.error=None
        except (OSError,sqlite3.Error) as err:
            self.error=str(err)
