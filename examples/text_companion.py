"""Talk to Atri's persistent desktop conversation and read today's diary."""
import argparse
import os
from pathlib import Path
import sqlite3
import sys
import time

from PySide6.QtCore import QCoreApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_conversation import RoleConversation
from desktop_journal import Journal


def data_directory():
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "Atri-Desktop" / "text-companion"


def wait_for(app, worker, job_id):
    journal = worker.journal
    worker.pump()
    while True:
        target = journal.job_status(job_id)
        if target["state"] == "complete":
            return target["answer"]
        pending = journal.pending_jobs()
        if pending:
            first = pending[0]
            if first["state"] == "failed":
                raise RuntimeError(f'{first["error"]} 记录已保留；修复后重新运行同一命令。')
            if first["state"] == "uncertain" and not worker.busy:
                raise RuntimeError("上一次发送的回执尚未确认。若接受重复发送的可能，可加 --retry-uncertain。")
        app.processEvents()
        time.sleep(0.10)


def run(args):
    app = QCoreApplication.instance() or QCoreApplication([])
    journal = Journal(args.data_dir.expanduser() / "diary.sqlite3")
    worker = RoleConversation(app, journal)
    try:
        # Finish interrupted turns before taking a snapshot of today's evidence.
        pending = journal.pending_jobs()
        repeat = None
        for job in pending:
            if job["state"] == "failed" or (job["state"] == "uncertain" and args.retry_uncertain):
                journal.retry(confirm_uncertain=args.retry_uncertain, only_job_id=job["id"])
            if args.command == "chat" and job["kind"] == "chat":
                if journal.job_status(job["id"])["text"] == args.message:
                    repeat = job["id"]
            wait_for(app, worker, job["id"])

        if args.command == "chat":
            job_id = repeat or journal.queue_chat(args.message)
            print(wait_for(app, worker, job_id))
            return

        day = journal.clock().date().isoformat()
        new_page = journal.save(day)
        if journal.error:
            raise RuntimeError(journal.error)
        if new_page:
            wait_for(app, worker, new_page)
        pages = journal.entries(day)
        if journal.error:
            raise RuntimeError(journal.error)
        print(day)
        print("\n\n".join(page["text"] for page in pages) or "今天还没有可写进日记的记录。")
    finally:
        worker.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="与亚托莉交谈，或查看她依据当天记录写下的日记。")
    parser.add_argument("command", choices=("chat", "diary"))
    parser.add_argument("message", nargs="?")
    parser.add_argument("--data-dir", type=Path, default=data_directory(), help="此文字示例的存档目录")
    parser.add_argument("--retry-uncertain", action="store_true", help="允许重新发送未收到回执的请求")
    args = parser.parse_intermixed_args(argv)
    if args.command == "chat" and not (args.message and args.message.strip()):
        parser.error('chat 需要一条消息，例如 chat "你好，亚托莉。"')
    if args.command == "diary" and args.message is not None:
        parser.error("diary 读取当天日记，无需输入消息。")
    try:
        run(args)
        return 0
    except (OSError, sqlite3.Error, RuntimeError) as err:
        print(f"未能完成：{err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中断。下次运行时会检查已保存的进度。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
