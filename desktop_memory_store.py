"""Durable, ordered Codex dialogue and append-only diary requests."""

from contextlib import closing, contextmanager
from datetime import date, datetime
import json
import os
from pathlib import Path
from uuid import uuid4


SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_jobs (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('chat','diary')),
    day TEXT NOT NULL,
    text TEXT NOT NULL,
    facts TEXT NOT NULL DEFAULT '[]',
    used_facts TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued','running','failed','complete')),
    created TEXT NOT NULL,
    completed TEXT,
    answer TEXT,
    thread_id TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    retry_at REAL NOT NULL DEFAULT 0,
    error TEXT,
    worker TEXT,
    owner_pid INTEGER,
    owner_start TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS memory_one_worker
    ON memory_jobs(state) WHERE state='running';
CREATE TABLE IF NOT EXISTS memory_review (
    day TEXT NOT NULL,
    fact TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES memory_jobs(id),
    PRIMARY KEY(day,fact)
);
CREATE TABLE IF NOT EXISTS memory_session (
    id INTEGER PRIMARY KEY CHECK(id=1),
    thread_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS journal_stickers (
    day TEXT PRIMARY KEY,
    last_entry INTEGER NOT NULL,
    selection TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS journal_sticker_editions (
    day TEXT PRIMARY KEY,
    catalog TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_review_receipts (
    job_id TEXT PRIMARY KEY REFERENCES memory_jobs(id),
    recorded TEXT NOT NULL,
    receipt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_delivery (
    job_id TEXT PRIMARY KEY REFERENCES memory_jobs(id),
    phase TEXT NOT NULL CHECK(phase IN ('awaiting','reply','uncertain','failed')),
    worker TEXT,
    attempt INTEGER,
    thread_id TEXT,
    dispatched TEXT,
    received TEXT,
    draft TEXT
);
"""

_JOBS = """SELECT j.*,d.phase AS delivery_phase,d.thread_id AS dispatch_thread,
           d.draft AS received_text,d.received AS received_at
           FROM memory_jobs j LEFT JOIN memory_delivery d ON d.job_id=j.id """
_UNCERTAIN = "上次请求已经发出，尚未确认服务是否回复。"

_FRESH = """
    FROM evidence AS e
    WHERE NOT EXISTS (
        SELECT 1 FROM entries AS n WHERE n.day=e.day AND n.fact=e.fact
    ) AND NOT EXISTS (
        SELECT 1 FROM memory_review AS r WHERE r.day=e.day AND r.fact=e.fact
    )
"""


def _local_day(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(value).isoformat()


def _process_start(pid):
    """Return the process creation token, '' for dead, None for inaccessible."""
    if not pid:
        return ""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes as wt

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        kernel.OpenProcess.restype = wt.HANDLE
        kernel.GetProcessTimes.argtypes = [wt.HANDLE]+[ctypes.POINTER(wt.FILETIME)]*4
        kernel.WaitForSingleObject.argtypes = [wt.HANDLE,wt.DWORD]
        kernel.CloseHandle.argtypes = [wt.HANDLE]
        handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not handle:
            return "" if ctypes.get_last_error() in (87,1168) else None
        try:
            if kernel.WaitForSingleObject(handle,0)==0:
                return ""
            stamps = [wt.FILETIME() for _ in range(4)]
            if kernel.GetProcessTimes(handle,*(ctypes.byref(s) for s in stamps)):
                return str(stamps[0].dwHighDateTime<<32 | stamps[0].dwLowDateTime)
            return None
        finally:
            kernel.CloseHandle(handle)
    # Linux exposes creation time independent of PID reuse.
    try:
        proc = (Path("/proc") / str(pid) / "stat").read_text()
        fields = proc[proc.rindex(")")+2:].split()
        return "" if fields[0] in ("Z","X") else fields[19]
    except (OSError, ValueError, IndexError):
        try:
            os.kill(pid,0)
            return None
        except ProcessLookupError:
            return ""
        except PermissionError:
            return None


class MemoryStore:
    """Mixin for a journal with _db(), path and a local datetime clock()."""

    @contextmanager
    def _memory_transaction(self):
        with closing(self._db()) as db, db:
            db.execute("PRAGMA busy_timeout=3000")
            db.execute("BEGIN IMMEDIATE")
            yield db

    @staticmethod
    def _chat_fact(db, job_id, role, text, timestamp):
        label = "用户说" if role=="user" else "我答复"
        db.execute("""INSERT INTO evidence(day,fact,source,wording,observed)
                      VALUES(?,?,?,?,?)""",
                   (timestamp.date().isoformat(),f"chat/{job_id}/{role}",f"chat/{role}",
                    f"{label}：「{text}」",timestamp.isoformat()))

    def queue_chat(self, text):
        if not isinstance(text,str) or not text.strip():
            raise ValueError("消息内容不能为空")
        job_id = uuid4().hex
        with self._memory_transaction() as db:
            now = self.clock()
            db.execute("""INSERT INTO memory_jobs(id,kind,day,text,created)
                          VALUES(?,'chat',?,?,?)""",
                       (job_id,now.date().isoformat(),text,now.isoformat()))
            self._chat_fact(db,job_id,"user",text,now)
        return job_id

    def due(self, day):
        with closing(self._db()) as db:
            return db.execute("SELECT 1 "+_FRESH+" AND e.day=? LIMIT 1",
                              (_local_day(day),)).fetchone() is not None

    def unfinished_days(self, before=None):
        clause = "" if before is None else " AND e.day<?"
        args = () if before is None else (_local_day(before),)
        with closing(self._db()) as db:
            return [r[0] for r in db.execute("SELECT DISTINCT e.day "+_FRESH+clause+
                                             " ORDER BY e.day",args)]

    def queue_diary(self, day):
        day = _local_day(day)
        with self._memory_transaction() as db:
            facts = [dict(r) for r in db.execute(
                "SELECT e.day,e.fact,e.source,e.wording,e.observed "+_FRESH+
                " AND e.day=? ORDER BY e.observed,e.rowid",(day,))]
            if not facts:
                return None
            job_id = uuid4().hex
            db.execute("""INSERT INTO memory_jobs(id,kind,day,text,created,facts)
                          VALUES(?,'diary',?,'',?,?)""",
                       (job_id,day,self.clock().isoformat(),json.dumps(facts,ensure_ascii=False)))
            db.executemany("INSERT INTO memory_review(day,fact,job_id) VALUES(?,?,?)",
                           [(day,item["fact"],job_id) for item in facts])
        return job_id

    @staticmethod
    def _session(db):
        row = db.execute("SELECT thread_id FROM memory_session WHERE id=1").fetchone()
        return row[0] if row else None

    def session_id(self):
        with closing(self._db()) as db:
            return self._session(db)

    @staticmethod
    def _save_session(db, thread_id):
        if thread_id:
            db.execute("""INSERT INTO memory_session(id,thread_id) VALUES(1,?)
                          ON CONFLICT(id) DO UPDATE SET thread_id=excluded.thread_id""",(thread_id,))

    def remember_thread(self, job_id, worker, thread_id):
        if not thread_id:
            return False
        with self._memory_transaction() as db:
            row = db.execute(_JOBS+"WHERE j.id=?",(job_id,)).fetchone()
            if not row or any(value and value!=thread_id for value in
                              (row["thread_id"],row["dispatch_thread"],self._session(db))):
                return False
            count = db.execute("""UPDATE memory_jobs SET thread_id=?
                                  WHERE id=? AND state='running' AND worker=?""",
                               (thread_id,job_id,worker)).rowcount
            if count:
                self._save_session(db,thread_id)
                db.execute("""UPDATE memory_delivery SET thread_id=?
                              WHERE job_id=? AND phase='awaiting' AND worker=?""",
                           (thread_id,job_id,worker))
            return bool(count)

    @staticmethod
    def _earliest(db):
        return db.execute(_JOBS+"WHERE j.state!='complete' ORDER BY j.seq LIMIT 1").fetchone()

    @staticmethod
    def _abandoned(row):
        actual = _process_start(row["owner_pid"])
        return actual=="" or bool(actual and row["owner_start"] and actual!=row["owner_start"])

    @classmethod
    def _state(cls,row):
        if row["state"]=="complete":
            return "complete"
        if row["delivery_phase"]=="uncertain":
            return "uncertain"
        if row["delivery_phase"]=="awaiting" and row["state"]!="running":
            return "uncertain"
        if row["state"]=="running" and cls._abandoned(row):
            return "uncertain" if row["delivery_phase"]=="awaiting" else "queued"
        return row["state"]

    @classmethod
    def _as_job(cls,row):
        job = dict(row)
        for name in ("facts","used_facts"):
            job[name] = json.loads(job[name])
        job["state"] = cls._state(row)
        job["receipt"] = (dict(text=row["received_text"],draft=row["received_text"],
                               thread_id=row["dispatch_thread"],received=row["received_at"])
                          if row["delivery_phase"]=="reply" else None)
        if job["state"]=="uncertain":
            job["error"] = job["error"] or _UNCERTAIN
        job["thread_id"] = job["thread_id"] or job["dispatch_thread"]
        return job

    def claim(self, worker):
        if not isinstance(worker,str) or not worker:
            raise ValueError("worker 不能为空")
        with self._memory_transaction() as db:
            row = self._earliest(db)
            if row is None:
                return None
            state = self._state(row)
            if state in ("uncertain","running"):
                return None
            if state=="failed" and self.clock().timestamp()<row["retry_at"]:
                return None
            pid = os.getpid()
            db.execute("""UPDATE memory_jobs SET state='running',worker=?,owner_pid=?,owner_start=?,
                          attempt=attempt+1,retry_at=0 WHERE id=?""",
                       (worker,pid,_process_start(pid),row["id"]))
            job = self._as_job(db.execute(_JOBS+"WHERE j.id=?",(row["id"],)).fetchone())
            job["thread_id"] = job["thread_id"] or self._session(db)
            return job

    def mark_dispatched(self,job_id,worker):
        """Commit immediately before sending. False means this caller must not send."""
        with self._memory_transaction() as db:
            row = db.execute(_JOBS+"WHERE j.id=?",(job_id,)).fetchone()
            if (not row or row["state"]!="running" or row["worker"]!=worker or
                    row["delivery_phase"] in ("awaiting","reply","uncertain")):
                return False
            db.execute("""INSERT INTO memory_delivery(job_id,phase,worker,attempt,thread_id,dispatched)
                          VALUES(?,'awaiting',?,?,?,?)
                          ON CONFLICT(job_id) DO UPDATE SET phase='awaiting',worker=excluded.worker,
                          attempt=excluded.attempt,thread_id=excluded.thread_id,
                          dispatched=excluded.dispatched,received=NULL,draft=NULL""",
                       (job_id,worker,row["attempt"],row["thread_id"] or self._session(db),
                        self.clock().isoformat()))
            return True

    def _store_reply(self,db,job_id,text,thread_id):
        db.execute("""UPDATE memory_delivery SET phase='reply',draft=?,received=?,thread_id=?
                      WHERE job_id=?""",(text,self.clock().isoformat(),thread_id,job_id))
        if thread_id:
            db.execute("UPDATE memory_jobs SET thread_id=? WHERE id=?",(thread_id,job_id))
            self._save_session(db,thread_id)

    def record_reply(self,job_id,worker,text,thread_id):
        if not isinstance(text,str) or not text.strip():
            return False
        with self._memory_transaction() as db:
            row = db.execute(_JOBS+"WHERE j.id=?",(job_id,)).fetchone()
            if not row or row["state"]!="running" or row["worker"]!=worker:
                return False
            bound = row["dispatch_thread"] or row["thread_id"]
            if thread_id and bound and thread_id!=bound:
                return False
            if row["delivery_phase"]=="reply":
                return text==row["received_text"]
            if row["delivery_phase"]!="awaiting":
                return False
            self._store_reply(db,job_id,text,thread_id or bound)
            return True

    def adopt_recovered(self,job_id,text,thread_id):
        """Accept a verified old turn from its bound session without resending."""
        if not thread_id or not isinstance(text,str) or not text.strip():
            return False
        with self._memory_transaction() as db:
            row = db.execute(_JOBS+"WHERE j.id=?",(job_id,)).fetchone()
            if (not row or self._state(row)!="uncertain" or
                    thread_id!=(row["dispatch_thread"] or row["thread_id"])):
                return False
            self._store_reply(db,job_id,text,thread_id)
            db.execute("""UPDATE memory_jobs SET state='queued',error=NULL,retry_at=0,
                          worker=NULL,owner_pid=NULL,owner_start=NULL WHERE id=?""",(job_id,))
            return True

    def fail(self, job_id, worker, error, *, definitive=False, uncertain=False, discard_reply=False):
        with self._memory_transaction() as db:
            row = db.execute(_JOBS+"WHERE j.id=? AND j.state='running' AND j.worker=?",
                             (job_id,worker)).fetchone()
            if not row:
                return False
            phase = row["delivery_phase"]
            ambiguous = (phase!="reply" and (uncertain or (phase=="awaiting" and not definitive)))
            if ambiguous:
                db.execute("""INSERT INTO memory_delivery(job_id,phase,worker,attempt,thread_id)
                              VALUES(?,'uncertain',?,?,?) ON CONFLICT(job_id) DO UPDATE SET phase='uncertain'""",
                           (job_id,worker,row["attempt"],row["thread_id"] or self._session(db)))
            elif discard_reply or phase!="reply":
                db.execute("""INSERT INTO memory_delivery(job_id,phase,worker,attempt,thread_id)
                              VALUES(?,'failed',?,?,?) ON CONFLICT(job_id) DO UPDATE SET phase='failed',
                              received=NULL,draft=NULL""",
                           (job_id,worker,row["attempt"],row["thread_id"] or self._session(db)))
            delay = min(300,4 * 2**min(7,max(0,row["attempt"]-1)))
            db.execute("""UPDATE memory_jobs SET state='failed',error=?,retry_at=?,
                          worker=NULL,owner_pid=NULL,owner_start=NULL WHERE id=?""",
                       (str(error),0 if ambiguous else self.clock().timestamp()+delay,job_id))
            return True

    def retry(self,confirm_uncertain=False,only_job_id=None):
        with self._memory_transaction() as db:
            count = db.execute("""UPDATE memory_jobs SET state='queued',retry_at=0 WHERE state='failed'
                                  AND (? IS NULL OR id=?)
                                  AND NOT EXISTS (SELECT 1 FROM memory_delivery d WHERE d.job_id=memory_jobs.id
                                  AND d.phase='uncertain')""",(only_job_id,only_job_id)).rowcount
            if confirm_uncertain:
                row = self._earliest(db)
                if (row and self._state(row)=="uncertain" and
                        (only_job_id is None or only_job_id==row["id"])):
                    db.execute("UPDATE memory_delivery SET phase='failed',draft=NULL,received=NULL WHERE job_id=?",
                               (row["id"],))
                    db.execute("""UPDATE memory_jobs SET state='queued',retry_at=0,worker=NULL,
                                  owner_pid=NULL,owner_start=NULL WHERE id=?""",(row["id"],))
                    count += 1
            return count

    def release(self, worker):
        with self._memory_transaction() as db:
            db.execute("""UPDATE memory_delivery SET phase='uncertain' WHERE phase='awaiting' AND job_id IN
                          (SELECT id FROM memory_jobs WHERE state='running' AND worker=?)""",(worker,))
            return db.execute("""UPDATE memory_jobs SET state='queued',worker=NULL,owner_pid=NULL,
                                 owner_start=NULL,retry_at=0 WHERE state='running' AND worker=?""",
                              (worker,)).rowcount

    def next_delay(self):
        with closing(self._db()) as db:
            row = self._earliest(db)
            if row is None:
                return None
            if self._state(row) in ("running","uncertain"):
                return None
            return max(0.,row["retry_at"]-self.clock().timestamp())

    def complete(self, job, worker, answer, thread_id, used_facts=(), *, review_receipt=None):
        if not isinstance(answer,str) or not answer.strip():
            raise ValueError("答复内容不能为空")
        job_id = job["id"] if isinstance(job,dict) else job
        with self._memory_transaction() as db:
            row = db.execute("SELECT * FROM memory_jobs WHERE id=?",(job_id,)).fetchone()
            if not row or row["state"]!="running" or row["worker"]!=worker:
                return False
            if isinstance(job,dict) and job.get("attempt",row["attempt"])!=row["attempt"]:
                return False
            facts = json.loads(row["facts"])
            selected = {f["fact"] if isinstance(f,dict) else f for f in (used_facts or ())}
            used = [f["fact"] for f in facts if f["fact"] in selected]
            now = self.clock()
            if row["kind"]=="chat":
                self._chat_fact(db,job_id,"assistant",answer,now)
            else:
                db.execute("""INSERT INTO entries(day,fact,text,written) VALUES(?,?,?,?)""",
                           (row["day"],"codex/"+job_id,answer,now.isoformat()))
            if review_receipt is not None:
                db.execute("INSERT INTO memory_review_receipts(job_id,recorded,receipt) VALUES(?,?,?)",
                           (job_id,now.isoformat(),json.dumps(review_receipt,ensure_ascii=False,allow_nan=False)))
            self._save_session(db,thread_id)
            db.execute("""UPDATE memory_jobs SET state='complete',answer=?,completed=?,
                          used_facts=?,thread_id=COALESCE(?,thread_id),error=NULL,retry_at=0,
                          worker=NULL,owner_pid=NULL,owner_start=NULL WHERE id=?""",
                       (answer,now.isoformat(),json.dumps(used,ensure_ascii=False),thread_id or None,job_id))
            return True

    def review_receipt(self, job_id):
        with closing(self._db()) as db:
            row = db.execute("SELECT receipt FROM memory_review_receipts WHERE job_id=?",(job_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def job_status(self, job_id):
        with closing(self._db()) as db:
            row = db.execute(_JOBS+"WHERE j.id=?",(job_id,)).fetchone()
            return self._as_job(row) if row else None

    def pending_jobs(self, kind=None):
        if kind not in (None, "chat", "diary"):
            raise ValueError("未知请求类型")
        clause = "" if kind is None else " AND j.kind=?"
        params = () if kind is None else (kind,)
        with closing(self._db()) as db:
            rows = db.execute(_JOBS+"WHERE j.state!='complete'"+clause+" ORDER BY j.seq",params)
            result = []
            for row in rows:
                item = {k:row[k] for k in ("seq","id","kind","day","error","retry_at",
                                           "attempt","created","delivery_phase")}
                item["state"] = self._state(row)
                item["thread_id"] = row["thread_id"] or row["dispatch_thread"]
                if item["state"]=="uncertain":
                    item["error"] = item["error"] or _UNCERTAIN
                result.append(item)
            return result

    def stickers(self, day):
        from desktop_journal_stickers import catalog_signature,choose,for_display
        if not day:
            return []
        day = _local_day(day)
        with self._memory_transaction() as db:
            pages = db.execute("SELECT id,day,fact,text FROM entries WHERE day=? ORDER BY id",(day,)).fetchall()
            if not pages:
                return []
            last_entry = pages[-1]["id"]
            previous = db.execute("SELECT * FROM journal_stickers WHERE day=?",(day,)).fetchone()
            saved = json.loads(previous["selection"]) if previous else []
            edition = db.execute("SELECT catalog FROM journal_sticker_editions WHERE day=?",(day,)).fetchone()
            catalog = catalog_signature()
            if not previous or previous["last_entry"]!=last_entry or not edition or edition[0]!=catalog:
                facts = db.execute("SELECT fact,source,wording FROM evidence WHERE day=? ORDER BY observed,fact",
                                   (day,)).fetchall()
                source_rows = db.execute("""SELECT n.id,j.used_facts FROM entries n JOIN memory_jobs j
                                            ON n.fact='codex/'||j.id WHERE n.day=?""",(day,))
                citations = {r[0]:json.loads(r[1]) for r in source_rows}
                saved = choose(pages,facts,saved,citations)
                db.execute("""INSERT INTO journal_stickers(day,last_entry,selection) VALUES(?,?,?)
                              ON CONFLICT(day) DO UPDATE SET last_entry=excluded.last_entry,
                              selection=excluded.selection""",
                           (day,last_entry,json.dumps(saved,ensure_ascii=False)))
                db.execute("""INSERT INTO journal_sticker_editions(day,catalog) VALUES(?,?)
                              ON CONFLICT(day) DO UPDATE SET catalog=excluded.catalog""",(day,catalog))
        return for_display(saved)

    def dialogue(self):
        with closing(self._db()) as db:
            rows = db.execute(_JOBS+"WHERE j.kind='chat' ORDER BY j.seq")
            result = []
            for row in rows:
                result.append(dict(id=row["id"],role="user",text=row["text"],state="complete",
                                   time=row["created"],day=row["day"]))
                completed = row["completed"]
                state = self._state(row)
                error = row["error"] or (_UNCERTAIN if state=="uncertain" else None)
                result.append(dict(id=row["id"],role="assistant",text=row["answer"] or "",
                                   state=state,time=completed,
                                   day=completed[:10] if completed else None,error=error))
            return result
