"""Serve the local speech synthesis page and its WAV endpoint."""

import argparse
import base64
import hashlib
import io
import json
import re
import shutil
import socket
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from collections import OrderedDict
from concurrent.futures import CancelledError
from contextlib import closing
from http import HTTPStatus
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

if __package__:
    from . import alignment, scene_read
    from .japanese_output import is_japanese_reading
else:
    import alignment
    import scene_read
    from japanese_output import is_japanese_reading


PAGE = Path(__file__).with_name("ui.html")
EMOTIONS_BY_LANGUAGE = {
    "auto": ("auto", "natural", "cheerful", "gentle", "proud"),
    "zh": ("natural",),
    "ja": ("auto", "natural", "cheerful", "gentle", "proud"),
    "en": ("natural",),
}
EMOTION_LABELS = {"auto": "按句意", "natural": "自然", "cheerful": "轻快", "gentle": "温柔", "proud": "得意"}
ENGINE_LOCK = threading.Lock()
REQUEST_LOCK = threading.Lock()
ACTIVE_REQUESTS = {}


def load_engine():
    if __package__:
        from . import engine
    else:
        import engine
    return engine


def scene_request(data):
    """Resolve a locked audition or new directed clauses before reserving the model."""
    if "take_id" in data:
        if set(data) - {"take_id", "request_id"}:
            raise ValueError("按 take_id 复现时只能另传 request_id。")
        take_id = data["take_id"]
        if not isinstance(take_id, str):
            raise ValueError("未知的语境试听编号。")
        language, parts, seed = scene_read.load_take(take_id)
    else:
        if set(data) - {"language", "parts", "seed", "request_id"}:
            raise ValueError("新语境朗读接受 language、parts、seed 和 request_id。")
        language, clauses = data.get("language"), data.get("parts")
        if not isinstance(language, str) or language not in scene_read.REFERENCES:
            raise ValueError("语境朗读请选择 ja 或 zh。")
        if not isinstance(clauses, list) or not clauses:
            raise ValueError("parts 需要至少一个带有 text 和 style 的段落。")
        parts = []
        for clause in clauses:
            if not isinstance(clause, dict) or set(clause) != {"text", "style"}:
                raise ValueError("每段需要 text 和 style。")
            text, style = clause["text"], clause["style"]
            if not isinstance(text, str) or not any(c.isalnum() for c in text):
                raise ValueError("每段台词需包含文字或数字。")
            if not isinstance(style, str) or style not in scene_read.REFERENCES[language]:
                raise ValueError("该语种的 style 可选 " + "、".join(scene_read.REFERENCES[language]) + "。")
            parts.append(scene_read.direct(text, language, style))
        seed = data.get("seed", 213)
        if type(seed) is not int or not -(2**63) <= seed < 2**64:
            raise ValueError("seed 超出 PyTorch 随机种子的整数范围。")
        take_id = None
    return language, parts, seed, take_id


def reading_key(text, emotion, scene):
    """Identify one resolved Japanese rendition, independently of its playback request ID."""
    if scene is not None:
        parts, seed, _ = scene
        plan = ("scene", "ja", seed, [(p.text, p.style, p.reference, p.speaker_only, p.model_text)
                                      for p in parts])
    else:
        engine = load_engine()
        parts = []
        for part, _ in engine.utterances(text.strip(), "ja"):
            style = engine.resolve_emotion(part, "ja", emotion)
            parts.append((part, style, engine.JAPANESE_REFERENCES[style]))
        plan = ("synthesize", "ja", 213, text, emotion, parts)
    return hashlib.sha256(json.dumps(plan, ensure_ascii=False).encode("utf-8")).digest()


class ReadingCache:
    """Completed diary renditions retained only for the lifetime of this HTTP server."""
    def __init__(self, max_bytes=16 * 1024 * 1024, max_entries=16):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.entries = OrderedDict()
        self.size = 0
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            item = self.entries.get(key)
            if item is not None:
                self.entries.move_to_end(key)
            return item

    def clear(self):
        with self.lock:
            self.entries.clear()
            self.size = 0

    def put(self, key, item):
        size = sum(map(len, item))
        if size > self.max_bytes or not self.max_entries:
            return
        with self.lock:
            old = self.entries.pop(key, None)
            if old is not None:
                self.size -= sum(map(len, old))
            while self.entries and (len(self.entries) >= self.max_entries or self.size + size > self.max_bytes):
                _, evicted = self.entries.popitem(last=False)
                self.size -= sum(map(len, evicted))
            self.entries[key] = item
            self.size += size


class LocalSpeechServer(ThreadingHTTPServer):
    allow_reuse_address = False
    stopping = False
    japanese_only = False

    def server_bind(self):
        self.readings = ReadingCache()
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def server_close(self):
        super().server_close()
        if hasattr(self, "readings"):
            self.readings.clear()


class SpeechHandler(BaseHTTPRequestHandler):
    def stream_event(self, event_type, request_id, **fields):
        message = {"type": event_type, "request_id": request_id, **fields}
        self.wfile.write((json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        self.wfile.flush()

    def headers_for(self, status, mime, size, reference_id=None, emotion=None, audio=None, request_id=None,
                    audio_cache=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        if reference_id is not None:
            self.send_header("X-Reference-Id", reference_id)
        if emotion is not None:
            self.send_header("X-Emotion", emotion)
        if audio is not None:
            self.send_header("X-Audio-Frames", str(audio["frame_count"]))
            self.send_header("X-Audio-Sample-Rate", str(audio["sample_rate_hz"]))
        if request_id is not None:
            self.send_header("X-Request-Id", request_id)
        if audio_cache is not None:
            self.send_header("X-Atri-Audio-Cache", audio_cache)
        self.end_headers()

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.headers_for(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def valid_selection(self, language, emotion):
        if not isinstance(language, str) or language not in EMOTIONS_BY_LANGUAGE:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "语言可选 auto、zh、ja 或 en。"})
            return False
        if not isinstance(emotion, str) or emotion not in EMOTION_LABELS:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "语气可选 auto、natural、cheerful、gentle 或 proud。"})
            return False
        return True

    def do_GET(self):
        route = urlsplit(self.path)
        if route.path == "/health":
            self.send_json(HTTPStatus.OK, {"status": "ok", "service": "atri-tts",
                                          "output_language": "ja" if self.server.japanese_only else "multilingual"})
        elif route.path == "/api/options":
            options = {"ja": EMOTIONS_BY_LANGUAGE["ja"]} if self.server.japanese_only else EMOTIONS_BY_LANGUAGE
            self.send_json(HTTPStatus.OK, {"emotions": options, "labels": EMOTION_LABELS})
        elif route.path == "/api/examples":
            examples = {"examples": []}
            if alignment.EXAMPLES.is_file():
                examples = json.loads(alignment.EXAMPLES.read_text(encoding="utf-8"))
            self.send_json(HTTPStatus.OK, examples)
        elif route.path.startswith("/api/clips/"):
            fields = route.path.split("/")
            clip_id = fields[3]
            record = alignment.original_index()[0].get(clip_id)
            if record is None or fields[4:] not in ([], ["audio"]):
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "原声索引中没有这段录音。"})
                return
            if fields[4:]:
                body = (alignment.PROJECT / record["file"]).read_bytes()
                self.headers_for(HTTPStatus.OK, "audio/ogg", len(body), reference_id=clip_id)
                self.wfile.write(body)
            else:
                mouth = parse_qs(route.query).get("mouth") == ["1"]
                self.send_json(HTTPStatus.OK, alignment.original_metadata(clip_id, mouth=mouth))
        elif route.path == "/api/reference":
            query = parse_qs(route.query, keep_blank_values=True)
            language = query.get("language", ["auto"])[0]
            emotion = query.get("emotion", ["natural"])[0]
            if not self.valid_selection(language, emotion):
                return
            if emotion == "auto" or emotion not in EMOTIONS_BY_LANGUAGE[language]:
                emotion = "natural"
            try:
                reference_id, path = load_engine().reference_for(language, emotion)
                sound = path.read_bytes()
            except Exception as exc:
                self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"原作参考加载失败。{exc}"})
                return
            self.headers_for(HTTPStatus.OK, "audio/ogg", len(sound), reference_id, emotion)
            self.wfile.write(sound)
        elif route.path == "/":
            page = PAGE.read_bytes()
            self.headers_for(HTTPStatus.OK, "text/html; charset=utf-8", len(page))
            self.wfile.write(page)
        else:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "没有这个接口。"})

    def do_POST(self):
        received_at = time.perf_counter()
        route = urlsplit(self.path).path
        if route not in ("/api/synthesize", "/api/scene-synthesize", "/api/cancel", "/api/stop"):
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "没有这个接口。"})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "请发送 application/json。"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
            if length < 0:
                raise ValueError()
        except ValueError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "请求缺少有效的 Content-Length。"})
            return
        if length > 1024 * 1024:
            self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "单次请求不能超过 1 MiB。"})
            return
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, RecursionError):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "请使用 UTF-8 编码的有效 JSON。"})
            return
        if route == "/api/stop":
            with REQUEST_LOCK:
                busy = bool(ACTIVE_REQUESTS)
                if not busy:
                    self.server.stopping = True
            if busy:
                self.send_json(HTTPStatus.CONFLICT, {"error": "语音正在生成，请稍后退出。"})
            else:
                try:
                    self.send_json(HTTPStatus.OK, {"status": "stopped"})
                finally:
                    self.server.shutdown()
            return
        if not isinstance(data, dict):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "请求体须为 JSON 对象。"})
            return
        request_id = data.get("request_id", "" if route == "/api/cancel" else uuid.uuid4().hex)
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", request_id):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "request_id 需为 1 至 80 位 ASCII 字母、数字、点、下划线或短横线。"})
            return
        if route == "/api/cancel":
            with REQUEST_LOCK:
                event = ACTIVE_REQUESTS.get(request_id)
                if event is not None:
                    event.set()
            if event is None:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "当前没有这个生成请求。"})
            else:
                self.send_json(HTTPStatus.ACCEPTED, {"request_id": request_id, "status": "cancelling"})
            return
        scene = None
        if route == "/api/scene-synthesize":
            try:
                language, parts, seed, take_id = scene_request(data)
            except ValueError as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            scene = (parts, seed, take_id)
            text, emotion = "".join(p.text for p in parts), None
        else:
            text = data.get("text")
            if not isinstance(text, str) or not text.strip():
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "请输入要朗读的文字。"})
                return
            language = data.get("language", "auto")
            emotion = data.get("emotion", "natural")
            if not self.valid_selection(language, emotion):
                return
        if self.server.japanese_only:
            readings = [part.text for part in parts] if scene is not None else [text]
            if language != "ja" or not all(is_japanese_reading(reading) for reading in readings):
                self.send_json(HTTPStatus.BAD_REQUEST, {"code": "japanese_reading_required",
                               "error": "桌面目前只播放日语，请先提供日语读稿。"})
                return
        event = threading.Event()
        with REQUEST_LOCK:
            stopping = self.server.stopping
            duplicate = request_id in ACTIVE_REQUESTS
            if not duplicate and not stopping:
                ACTIVE_REQUESTS[request_id] = event
        if stopping:
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "服务正在退出。"})
            return
        if duplicate:
            self.send_json(HTTPStatus.CONFLICT, {"error": "request_id 正在使用。"})
            return
        stream_started = False
        try:
            if "application/x-ndjson" in self.headers.get("Accept", "").lower():
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("X-Request-Id", request_id)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.close_connection = True
                self.end_headers()
                stream_started = True
            self.render(text, language, emotion, request_id, event, scene, stream_started, received_at)
        except ConnectionError:
            pass
        except Exception as exc:
            cancelled = isinstance(exc, CancelledError)
            code = "cancelled" if cancelled else "synthesis_failed"
            message = "语音请求已取消。" if cancelled else f"合成失败。{exc}"
            if stream_started:
                try:
                    self.stream_event("error", request_id, code=code, error=message)
                except ConnectionError:
                    pass
            else:
                self.send_json(HTTPStatus.CONFLICT if cancelled else HTTPStatus.INTERNAL_SERVER_ERROR,
                               {"code": code, "error": message, "request_id": request_id})
        finally:
            with REQUEST_LOCK:
                ACTIVE_REQUESTS.pop(request_id, None)

    def send_reading(self, item, request_id, event, status):
        wav, saved = item
        metadata = json.loads(saved)
        metadata["request_id"] = request_id
        with io.BytesIO() as body:
            with zipfile.ZipFile(body, "w") as bundle:
                bundle.writestr("audio.wav", wav)
                bundle.writestr("alignment.json", json.dumps(metadata, ensure_ascii=False),
                                compress_type=zipfile.ZIP_DEFLATED)
            if event.is_set():
                raise CancelledError()
            self.headers_for(HTTPStatus.OK, "application/zip", body.tell(), emotion=metadata["emotion"],
                             audio=metadata["audio"], request_id=request_id, audio_cache=status)
            self.wfile.write(body.getbuffer())

    def render(self, text, language, emotion, request_id, event, scene=None, streaming=False, received_at=None):
        zip_requested = "application/zip" in self.headers.get("Accept", "").lower()
        bundle_requested = scene is not None or zip_requested
        segments = []
        provenance = None
        if scene is not None:
            parts, seed, take_id = scene
            provenance = {"mode": "scene_take" if take_id else "scene_defaults", "seed": seed}
            if take_id:
                provenance["take_id"] = take_id
        key = None
        if (not streaming and zip_requested and self.headers.get("X-Atri-Reuse-Audio") == "1"
                and language == "ja" and (scene is None or take_id is None)):
            readings = [part.text for part in parts] if scene is not None else [text]
            if self.server.japanese_only or all(is_japanese_reading(s) for s in readings):
                key = reading_key(text, emotion, scene)
                saved = self.server.readings.get(key)
                if saved is not None:
                    self.send_reading(saved, request_id, event, "hit")
                    return
        sent_frames = 0
        sent_segments = 0
        engine_times = {}

        def deliver(row, pcm, rate):
            nonlocal sent_frames, sent_segments
            if event.is_set():
                raise CancelledError()
            if sent_frames + len(pcm) // 2 != row["end_frame"] or len(pcm) % 2:
                raise ValueError("语音段的帧数与 PCM 不一致。")
            if scene is not None:
                row = dict(row, language=load_engine().LANGUAGES[language])
            self.stream_event("segment", request_id, index=sent_segments, segment=row,
                              start_frame=sent_frames, sample_rate_hz=rate, channels=1,
                              pcm_s16le=base64.b64encode(pcm).decode("ascii"),
                              requested_language=language, provenance=provenance)
            sent_frames = row["end_frame"]
            sent_segments += 1

        stream_options = {"on_segment": deliver, "timings": engine_times} if streaming else {}
        with tempfile.TemporaryDirectory(prefix="atri-speech-") as folder:
            wav = Path(folder) / "speech.wav"
            while not ENGINE_LOCK.acquire(timeout=0.1):
                if event.is_set():
                    raise CancelledError()
                if key is not None:
                    saved = self.server.readings.get(key)
                    if saved is not None:
                        self.send_reading(saved, request_id, event, "hit")
                        return
            try:
                if event.is_set():
                    raise CancelledError()
                acquired_at = time.perf_counter()
                # A simultaneous request for the same page may have filled the cache while we waited.
                saved = self.server.readings.get(key) if key is not None else None
                cache_status = "hit" if saved is not None else "miss"
                if saved is None:
                    if scene is None:
                        load_engine().synthesize(text.strip(), language, wav, emotion=emotion,
                                                 segments=segments, cancel_event=event, **stream_options)
                        styles = {part["emotion"] for part in segments}
                    else:
                        result = scene_read.render(parts, language, wav, seed=seed, cancel_event=event, **stream_options)
                        segments = [dict(part, language=load_engine().LANGUAGES[language]) for part in result["segments"]]
                        styles = {part["style"] for part in segments}
                    actual_emotion = styles.pop() if len(styles) == 1 else "mixed"
                    if key is not None and wav.stat().st_size <= self.server.readings.max_bytes:
                        meta = alignment.render_metadata(wav, text, language, actual_emotion, segments, None,
                                                         provenance=provenance)
                        if meta["audio"]["frame_count"] <= 0:
                            raise ValueError("引擎生成了空音频。")
                        saved = (wav.read_bytes(), json.dumps(meta, ensure_ascii=False).encode("utf-8"))
                        with REQUEST_LOCK:
                            if event.is_set():
                                raise CancelledError()
                            self.server.readings.put(key, saved)
            finally:
                ENGINE_LOCK.release()
            if saved is not None:
                self.send_reading(saved, request_id, event, cache_status)
                return
            if streaming:
                meta = alignment.render_metadata(wav, text, language, actual_emotion, segments, request_id,
                                                 provenance=provenance)
                if (not sent_segments or len(segments) != sent_segments
                        or meta["audio"]["frame_count"] != sent_frames):
                    raise ValueError("流式交付与最终音频帧数或段数不一致。")
                if event.is_set():
                    raise CancelledError()
                self.stream_event("complete", request_id, alignment=meta,
                                  timings={"engine": engine_times, "before_engine_s": acquired_at - received_at,
                                           "request_s": time.perf_counter() - received_at})
                return
            audio = alignment.audio_info(wav)
            if audio["frame_count"] <= 0:
                raise ValueError("引擎生成了空音频。")
            result, mime = wav, "audio/wav"
            if bundle_requested:
                result, mime = Path(folder) / "speech.zip", "application/zip"
                alignment.make_render_bundle(result, wav, text, language, actual_emotion, segments, request_id,
                                             provenance=provenance)
            if event.is_set():
                raise CancelledError()
            with result.open("rb") as stream:
                self.headers_for(HTTPStatus.OK, mime, result.stat().st_size, emotion=actual_emotion,
                                 audio=audio, request_id=request_id, audio_cache="miss" if key is not None else None)
                shutil.copyfileobj(stream, self.wfile)


def is_running(port):
    try:
        with closing(HTTPConnection("127.0.0.1", port, timeout=2)) as client:
            client.request("GET", "/health")
            response = client.getresponse()
            if response.status == HTTPStatus.OK:
                body = json.loads(response.read(2048))
                return isinstance(body, dict) and body.get("service") == "atri-tts" and body.get("status") == "ok"
    except (OSError, HTTPException, ValueError):
        pass
    return False


def open_page(url):
    if not webbrowser.open(url):
        print(f"请在浏览器中打开 {url}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="亚托莉本地语音合成与试听")
    parser.add_argument("--port", type=int, default=8875, help="本地 HTTP 端口，默认 8875")
    parser.add_argument("--open", action="store_true", help="打开浏览器，已启动时使用现有服务")
    parser.add_argument("--japanese-only", action="store_true", help="在独立测试端口也启用桌面的日语音轨规则")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("端口须在 0 到 65535 之间。")
    try:
        server = LocalSpeechServer(("127.0.0.1", args.port), SpeechHandler)
    except OSError as exc:
        url = f"http://127.0.0.1:{args.port}/"
        if is_running(args.port):
            print(f"亚托莉语音合成服务已在运行 {url}", flush=True)
            if args.open:
                open_page(url)
            return
        parser.exit(1, f"无法使用本机端口 {args.port}，且未检测到亚托莉语音服务。请检查端口占用或用 --port 另选端口。\n{exc}\n")
    with server:
        server.japanese_only = args.port == 8875 or args.japanese_only
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"亚托莉语音合成 {url}", flush=True)
        if args.open:
            threading.Thread(target=open_page, args=(url,), daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
