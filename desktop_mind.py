"""A private workspace and explicitly bound Codex CLI thread for one desktop character."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import tomllib
import uuid


class MindError(RuntimeError):
    """The character turn did not complete with a reply."""


class MindBusy(MindError):
    """Another process already owns this character's active turn."""


_DEFAULT_INSTRUCTIONS = (
    "你是桌面伙伴亚托莉。只依据这次角色对话中明确给出的内容和此前的对话，以自然、简洁的中文回应。"
    "只输出要对用户说的话。不要调用工具、读取文件、读取其他聊天或编造记忆。"
)


@contextmanager
def _turn_lock(path):
    # OS locks release when the process exits, including after a crash.
    with path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"1")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as err:
                raise MindBusy("这段角色对话正在生成回复。") from err
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as err:
                raise MindBusy("这段角色对话正在生成回复。") from err
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as err:
        raise MindError("角色会话编号无效。") from err


def _stop_child(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # Only the CLI process launched by this turn and its children.
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
                           check=False, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)


_PACKAGE_FAMILY="OpenAI.Codex_2p2nqsd0c76g0"


def _codex_cli():
    cli = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if cli:
        return cli
    if os.name == "nt":
        # Explorer launches pythonw with the user's ordinary PATH. The app's
        # bundled CLI lives in a versioned directory recorded by Windows.
        windows = Path(os.environ.get("SystemRoot") or r"C:\Windows")
        powershell = windows / "System32/WindowsPowerShell/v1.0/powershell.exe"
        query = (
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
            "Get-AppxPackage -Name OpenAI.Codex -PackageTypeFilter Main -ErrorAction Stop | "
            f"Where-Object PackageFamilyName -eq '{_PACKAGE_FAMILY}' | "
            "Sort-Object Version -Descending | Select-Object -First 1 -ExpandProperty InstallLocation"
        )
        try:
            found = subprocess.run([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", query],
                                   stdin=subprocess.DEVNULL, capture_output=True, timeout=12, check=False,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
            install = Path(found.stdout.decode("utf-8-sig").strip())
            candidate = install / "app/resources/codex.exe"
            if not found.returncode and install.is_absolute() and candidate.is_file():
                return str(candidate)
        except (OSError, UnicodeError, subprocess.TimeoutExpired):
            pass
    raise MindError("未找到已安装的 Codex CLI。")


def _cli_environment():
    env=os.environ.copy()
    platform_keys={"CODEX_HOME","CODEX_WINDOWS_REGISTERED_CORE","CODEX_WINDOWS_SANDBOX_PACKAGE_FAMILY"}
    for key in tuple(env):
        if (key.startswith("CODEX_") and key not in platform_keys) or key=="OPENAI_API_KEY":
            env.pop(key)
    return env


def _command(workspace, thread_id, instructions):
    cli = _codex_cli()
    cmd = [cli, "--no-daemon", "-a", "never", "-s", "read-only", "-C", str(workspace),
           "exec", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
           "--json", "-c", 'web_search="disabled"', "-c", "project_doc_max_bytes=0",
           "-c", 'project_root_markers=[".atri-role-root"]',
           "-c",
           "developer_instructions=" + json.dumps(instructions, ensure_ascii=False)]
    # Preserve the account's explicit model choice inside this isolated role.
    # Account tools, project instructions and engineering memory stay separate.
    config_dir=Path(os.environ.get("CODEX_HOME") or Path.home()/".codex")
    try:
        with (config_dir/"config.toml").open("rb") as src:
            settings=tomllib.load(src)
    except (OSError,ValueError):
        settings={}
    for key in ("model","model_reasoning_effort"):
        value=settings.get(key)
        if isinstance(value,str) and value:
            cmd.extend(("-c",key+"="+json.dumps(value)))
    for feature in ("memories", "apps", "plugins", "hooks", "computer_use", "shell_tool",
                    "unified_exec", "shell_snapshot", "multi_agent", "multi_agent_v2",
                    "skill_search", "goals"):
        cmd.extend(("--disable", feature))
    cmd.extend(("--enable", "skip_host_skill_discovery"))
    if thread_id:
        cmd.extend(("resume", thread_id))
    return [*cmd, "-"]


def _receipt_prompt(value, job_id):
    # The app's own prompt has one instruction line and a complete final JSON envelope.
    # Its message text, including newlines and pasted JSON, is nested inside that envelope.
    if not isinstance(value,str):
        return False
    intro, newline, envelope=value.rpartition("\n")
    if not newline or not intro.startswith(("现在是交谈。","现在给指定日期续写自己的日记。")):
        return False
    try:
        data=json.loads(envelope)
    except ValueError:
        return False
    return (isinstance(data,dict) and data.get("本次请求编号")==job_id
            and "所属本地日期" in data and ("当前用户消息" in data or "本批当天观察" in data))


def _receipt_from_log(path, workspace, tid, job_id):
    matching=[]
    receipts=[]
    active=None
    with path.open(encoding="utf8") as stream:
        header=json.loads(next(stream))
        meta=header["payload"]
        if (header["type"]!="session_meta" or meta.get("id")!=tid or meta.get("source")!="exec"
                or Path(meta["cwd"]).resolve()!=workspace):
            return None
        for line in stream:
            event=json.loads(line)
            if event.get("type")!="event_msg":
                continue
            payload=event["payload"]
            kind=payload.get("type")
            turn_id=payload.get("turn_id")
            if kind=="task_started":
                active={"turn_id":turn_id,"user_count":0,"finals":[],"matches":False}
                continue
            if not active or not turn_id or active["turn_id"]!=turn_id:
                continue
            if kind in ("turn_aborted","task_failed","error"):
                active=None
            elif kind=="item_completed" and payload.get("thread_id")==tid:
                item=payload["item"]
                parts=item.get("content",[])
                if item.get("type")=="UserMessage":
                    active["user_count"]+=1
                    if (len(parts)==1 and parts[0].get("type")=="text"
                            and _receipt_prompt(parts[0].get("text"),job_id)):
                        matching.append(turn_id)
                        active["matches"]=True
                elif (active["matches"] and item.get("type")=="AgentMessage"
                        and item.get("phase")=="final_answer"
                        and len(parts)==1 and parts[0].get("type")=="Text"):
                    active["finals"].append(parts[0].get("text"))
            elif kind=="task_complete":
                final=payload.get("last_agent_message")
                if (active["matches"] and active["user_count"]==1 and isinstance(final,str)
                        and final.strip() and active["finals"]==[final] and payload.get("completed_at")):
                    receipts.append({"thread_id":tid,"turn_id":turn_id,"text":final.strip(),"recovered":True})
                active=None
    return receipts[0] if len(matching)==len(receipts)==1 else None


def recover_reply(directory, thread_id, job_id):
    """Read a single proven local receipt. Missing, ambiguous or incomplete evidence returns None."""
    if not thread_id or not isinstance(job_id,str) or not job_id:
        return None
    try:
        root=Path(directory).expanduser().resolve()
        workspace=root/"role-workspace"
        binding=root/"role-thread.json"
        tid=_uuid(thread_id)
        if (workspace.resolve()!=workspace or binding.resolve()!=binding or not workspace.is_dir()
                or not (workspace/".atri-role-root").is_file()
                or json.loads(binding.read_text(encoding="utf8"))["thread_id"]!=tid):
            return None
        home=Path(os.environ.get("CODEX_HOME") or (Path.home()/".codex")).expanduser().resolve()
        paths=[]
        for base,pattern in ((home/"sessions","????/??/??/rollout-*-"+tid+".jsonl"),
                             (home/"archived_sessions","rollout-*-"+tid+".jsonl")):
            for path in base.glob(pattern):
                if path.resolve()!=path or path.is_symlink():
                    return None
                paths.append(path)
        if len(paths)==1:
            return _receipt_from_log(paths[0],workspace,tid,job_id)
    except (OSError,ValueError,KeyError,TypeError,StopIteration,AttributeError,MindError,RuntimeError):
        pass
    return None


def invoke(text, directory, thread_id=None, *, cancel_event=None, on_thread=None,
           timeout=360, instructions=None):
    """Run one real turn. Return thread_id, text and resumed; raise on failure or cancellation.

    The caller provides a dedicated character-data directory. The CLI starts in its
    separate role-workspace subdirectory. On a fresh directory a thread is created; subsequent
    calls, including after a process restart, resume the thread bound to that directory.
    on_thread(id) runs as soon as the CLI announces the ID, before waiting for its answer.
    """
    handed_to_cli=threading.Event()
    try:
        return _run_turn(text,directory,thread_id,cancel_event,on_thread,timeout,
                         instructions,handed_to_cli)
    except Exception as err:
        if not handed_to_cli.is_set():
            err.not_sent=True
        raise


def _run_turn(text,directory,thread_id,cancel_event,on_thread,timeout,instructions,handed_to_cli):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("请输入要发送给亚托莉的文字。")
    if timeout <= 0:
        raise ValueError("回复超时必须为正数。")
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("角色回复已取消。")
    root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace = root/"role-workspace"
    binding = root/"role-thread.json"
    workspace.mkdir(exist_ok=True)
    if workspace.resolve() != workspace or binding.is_symlink():
        raise MindError("角色工作目录或会话绑定指向了其他位置。")
    with _turn_lock(root/"role-turn.lock"):
        (workspace/".atri-role-root").touch(exist_ok=True)
        bound = None
        if binding.exists():
            try:
                state = json.loads(binding.read_text(encoding="utf-8"))
                bound = _uuid(state["thread_id"])
            except (OSError, KeyError, TypeError, ValueError, MindError) as err:
                raise MindError("无法读取此角色的会话绑定。") from err
        if thread_id is not None and _uuid(thread_id) != bound:
            raise MindError("该会话编号未绑定到此角色目录。")
        cmd = _command(workspace, bound, instructions or _DEFAULT_INSTRUCTIONS)
        env = _cli_environment()
        opts = ({"creationflags":subprocess.CREATE_NO_WINDOW} if os.name == "nt"
                else {"start_new_session":True})
        proc = subprocess.Popen(cmd, cwd=workspace, env=env, shell=False,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, **opts)
        messages = queue.Queue()

        def write_input():
            try:
                with proc.stdin:
                    proc.stdin.write(text.encode("utf-8"))
            except (OSError, ValueError):
                pass

        def read_events():
            try:
                with proc.stdout:
                    for line in proc.stdout:
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if isinstance(event, dict):
                            messages.put(event)
            finally:
                messages.put(None)

        threading.Thread(target=read_events, daemon=True).start()
        handed_to_cli.set()
        threading.Thread(target=write_input, daemon=True).start()
        deadline = time.monotonic()+timeout
        seen = reply = error = None
        completed = failed = item_seen = False
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("角色回复已取消。")
                if time.monotonic() >= deadline:
                    raise TimeoutError("等待 Codex 角色回复超时。")
                try:
                    event = messages.get(timeout=.1)
                except queue.Empty:
                    continue
                if event is None:
                    break
                kind = event.get("type")
                if kind == "thread.started":
                    announced = _uuid(event.get("thread_id"))
                    if (bound and announced != bound) or (seen and seen != announced):
                        raise MindError("Codex 返回了不属于本角色的会话编号。")
                    if seen == announced:
                        continue
                    seen = announced
                    if not bound:
                        temp = binding.with_suffix(".new")
                        temp.write_text(json.dumps({"thread_id":seen}), encoding="utf-8")
                        temp.replace(binding)
                    if on_thread:
                        on_thread(seen)
                elif kind in ("item.started","item.completed"):
                    item_seen=True
                    item = event.get("item") or {}
                    action = item.get("type")
                    if action in ("command_execution","mcp_tool_call","file_change","web_search",
                                  "collab_tool_call"):
                        raise MindError("角色会话请求了对话以外的工具。")
                    if kind == "item.completed" and action == "agent_message":
                        reply = str(item.get("text") or "").strip()
                elif kind == "turn.completed":
                    completed = True
                elif kind == "turn.failed":
                    failed = True
                    detail = event.get("error") or {}
                    error = detail.get("message") if isinstance(detail,dict) else str(detail)
                elif kind == "error":
                    error = str(event.get("message") or "Codex 返回了错误。")
            status = proc.wait(timeout=max(.1, deadline-time.monotonic()))
            if status or failed or not completed or not seen or not reply:
                detail = f" {error[:360]}" if error else ""
                failure=MindError(f"Codex 未完成角色回复（退出码 {status}）。{detail}")
                if failed and not item_seen and error and any(code in error.lower() for code in (
                        "invalid_api_key","invalid api key","authentication token is expired",
                        "missing bearer authentication","insufficient_quota")):
                    failure.not_sent=True
                raise failure
            return {"thread_id":seen, "text":reply, "resumed":bool(bound)}
        finally:
            _stop_child(proc)
