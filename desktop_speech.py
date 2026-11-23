"""Asynchronous desktop speech with a sample clock from the selected output device."""
import ctypes as ct
import base64
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass
import io
import json
import math
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid
import wave
import zipfile

import numpy as np
from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot

from tts.japanese_output import is_japanese_reading, original_catalog


_startup_lock=threading.Lock()
_launched={}


class _Format(ct.Structure):
    _pack_=1
    _fields_=[("tag",ct.c_uint16),("channels",ct.c_uint16),("rate",ct.c_uint32),
              ("bytes_per_second",ct.c_uint32),("align",ct.c_uint16),("bits",ct.c_uint16),("extra",ct.c_uint16)]


class _Header(ct.Structure):
    _fields_=[("data",ct.c_void_p),("length",ct.c_uint32),("recorded",ct.c_uint32),("user",ct.c_size_t),
              ("flags",ct.c_uint32),("loops",ct.c_uint32),("next",ct.c_void_p),("reserved",ct.c_size_t)]


class _Position(ct.Structure):
    _fields_=[("kind",ct.c_uint32),("value",ct.c_uint32),("extra",ct.c_uint32)]


class WaveOutput:
    def __init__(self,pcm,rate,channels,offset=0):
        self.api=ct.WinDLL("winmm")
        self.handle=ct.c_void_p()
        self.rate,self.channels,self.offset=rate,channels,offset
        sample=pcm[offset*channels*2:]
        fmt=_Format(1,channels,rate,rate*channels*2,channels*2,16,0)
        self.buffers=deque()
        self._pending=deque()
        self._condition=threading.Condition()
        self._chunk_bytes=max(1,round(rate*.010))*channels*2
        self._error=None
        try:
            self._check(self.api.waveOutOpen(ct.byref(self.handle),ct.c_uint32(-1),
                        ct.byref(fmt),None,None,0))
            self.append(sample)
            self._feeder=threading.Thread(target=self._feed,daemon=True,name="atri-wave-output")
            self._feeder.start()
        except Exception:
            self.close()
            raise

    def _check(self,code):
        if code:
            message=ct.create_unicode_buffer(256)
            self.api.waveOutGetErrorTextW(code,message,len(message))
            raise OSError(message.value or f"waveOut {code}")

    def append(self,pcm):
        with self._condition:
            if self._error:raise self._error
            if not self.handle:raise OSError("音频设备已关闭。")
            if pcm:
                self._pending.append(memoryview(bytes(pcm)))
                self._pump()
                self._condition.notify_all()

    def _take(self,size):
        pieces=[]
        while size and self._pending:
            head=self._pending[0]
            count=min(size,len(head))
            pieces.append(head[:count])
            if count==len(head):
                self._pending.popleft()
            else:
                self._pending[0]=head[count:]
            size-=count
        return b"".join(pieces)

    def _pump(self):
        while self.buffers and self.buffers[0][1].flags&1:
            header=self.buffers[0][1]
            self._check(self.api.waveOutUnprepareHeader(self.handle,ct.byref(header),ct.sizeof(header)))
            self.buffers.popleft()
        # Keep 40 ms at the driver. An interrupt can then release the actual
        # next samples without resetting in the middle of a waveform.
        while self._pending and len(self.buffers)<4:
            self._write(self._take(self._chunk_bytes))

    def _write(self,pcm):
        buffer=ct.create_string_buffer(pcm)
        header=_Header(ct.addressof(buffer),len(pcm),0,0,0,0,None,0)
        self._check(self.api.waveOutPrepareHeader(self.handle,ct.byref(header),ct.sizeof(header)))
        self.buffers.append((buffer,header))
        self._check(self.api.waveOutWrite(self.handle,ct.byref(header),ct.sizeof(header)))

    def _feed(self):
        with self._condition:
            try:
                while self.handle:
                    self._pump()
                    if not self._pending and not self.buffers:self._condition.notify_all()
                    self._condition.wait(.005 if self.buffers else None)
            except OSError as err:
                self._error=err
                self._condition.notify_all()

    def position(self):
        if self._error:raise self._error
        pos=_Position(2,0,0)  # TIME_SAMPLES
        self._check(self.api.waveOutGetPosition(self.handle,ct.byref(pos),ct.sizeof(pos)))
        if pos.kind==2:
            return self.offset+pos.value
        if pos.kind==4:  # TIME_BYTES
            return self.offset+pos.value//(2*self.channels)
        if pos.kind==1:  # TIME_MS
            return self.offset+round(pos.value*self.rate/1000)
        raise OSError("音频设备未返回可识别的播放位置。")

    @property
    def done(self):
        with self._condition:
            return not self._pending and (not self.buffers or bool(self.buffers[-1][1].flags&1))

    def release(self):
        """Drain the short driver queue and taper the following real samples on a user stop."""
        with self._condition:
            try:
                if self.handle and not self._error and not self.done and self.position()>self.offset:
                    tail=self._take(max(2,round(self.rate*.012))*self.channels*2)
                    self._pending.clear()
                    if tail:
                        samples=np.frombuffer(tail,"<i2").reshape(-1,self.channels)
                        gain=np.cos(np.linspace(0,np.pi/2,len(samples)))**2
                        gain[-1]=0
                        release=np.rint(samples*gain[:,None]).astype("<i2").tobytes()
                        self._pending.append(memoryview(release))
                    self._pump()
                    self._condition.notify_all()
                    # A disconnected driver must not keep the desktop waiting.
                    self._condition.wait_for(lambda:self.done or self._error,timeout=.12)
            except OSError:
                pass
            finally:
                self.close()

    def close(self):
        with self._condition:
            if not self.handle:return
            if self.buffers and not (self.buffers[-1][1].flags&1):
                self.api.waveOutReset(self.handle)
            for _,header in self.buffers:
                self.api.waveOutUnprepareHeader(self.handle,ct.byref(header),ct.sizeof(header))
            self.api.waveOutClose(self.handle)
            self.handle=ct.c_void_p()
            self.buffers.clear()
            self._pending.clear()
            self._condition.notify_all()


def _wav(data):
    with wave.open(io.BytesIO(data)) as sound:
        rate,channels,width=sound.getframerate(),sound.getnchannels(),sound.getsampwidth()
        raw=sound.readframes(sound.getnframes())
    if width==1:
        samples=(np.frombuffer(raw,"uint8").astype("int16")-128)<<8
    elif width==2:
        samples=np.frombuffer(raw,"<i2")
    elif width==3:
        b=np.frombuffer(raw,"uint8").reshape(-1,3).astype("int32")
        samples=(b[:,1]|b[:,2]<<8).astype("int16")
    elif width==4:
        samples=(np.frombuffer(raw,"<i4")>>16).astype("int16")
    else:
        raise ValueError("无法读取这段录音的 PCM 位深。")
    if not samples.size or samples.size%channels or rate<=0:
        raise ValueError("录音没有完整的采样帧。")
    return samples.astype("<i2",copy=False).tobytes(),rate,channels


_DECODE_OPUS="""
import av,io,sys,wave
out=io.BytesIO()
with av.open(sys.argv[1]) as src, wave.open(out,'wb') as wav:
    stream=src.streams.audio[0]
    rate=stream.codec_context.sample_rate
    layout=stream.codec_context.layout
    wav.setparams((len(layout.channels),2,rate,0,'NONE','not compressed'))
    convert=av.AudioResampler(format='s16',layout=layout,rate=rate)
    for frame in src.decode(stream):
        for pcm in convert.resample(frame):
            wav.writeframesraw(pcm.to_ndarray().tobytes())
    for pcm in convert.resample(None):
        wav.writeframesraw(pcm.to_ndarray().tobytes())
sys.stdout.buffer.write(out.getvalue())
"""


def _audio_file(root,path):
    if path.suffix.lower()==".wav":
        return _wav(path.read_bytes())
    ffmpeg=shutil.which("ffmpeg")
    if ffmpeg:
        command=[ffmpeg,"-v","error","-nostdin","-i",str(path),"-c:a","pcm_s16le","-f","wav","pipe:1"]
    else:
        command=[str(root/"tts"/".venv"/"Scripts"/"python.exe"),"-I","-c",_DECODE_OPUS,str(path)]
    proc=subprocess.run(command,capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
    if proc.returncode:
        raise ValueError("原声 Opus 解码失败。")
    return _wav(proc.stdout)


def _levels(pcm,rate,channels,declared):
    count=len(pcm)//(2*channels)
    mouth=declared.get("mouth_rms") or {}
    hop=int(mouth.get("hop_frames") or max(1,rate//20))
    rms=np.array(mouth.get("linear_rms",[]),dtype=float)
    if (declared.get("sample_rate_hz")!=rate or declared.get("frame_count")!=count
            or mouth.get("first_frame")!=0 or hop<=0 or len(rms)!=math.ceil(count/hop)
            or not np.isfinite(rms).all()):
        hop=max(1,rate//20)
        sound=np.frombuffer(pcm,"<i2").reshape(-1,channels).astype("float32")/32768
        rms=np.array([np.sqrt(np.mean(sound[p:p+hop]**2)) for p in range(0,count,hop)])
    peak=max(.025,float(np.percentile(rms,97)))
    return hop,np.clip((rms-.0025)/peak,0.,1.)**.66


def _japanese_alignment(meta):
    rows=meta.get("segments") or []
    return (meta.get("requested_language") in ("ja","Japanese","auto",None) and bool(rows)
            and all(row.get("language") in ("ja","Japanese") and is_japanese_reading(row.get("text"))
                    for row in rows))


def _bundle(source,request_id=None):
    with zipfile.ZipFile(source) as bundle:
        meta=json.loads(bundle.read("alignment.json"))
        if request_id is not None and meta.get("request_id")!=request_id:
            raise ValueError("语音请求编号不匹配。")
        declared=meta["audio"]
        pcm,rate,channels=_wav(bundle.read(declared["file"]))
    count=len(pcm)//(2*channels)
    if (declared["sample_rate_hz"]!=rate or declared["frame_count"]!=count
            or declared.get("channels",channels)!=channels):
        raise ValueError("合成语音与时间数据不一致。")
    previous=0
    for row in meta.get("segments") or ():
        begin,end=row["start_frame"],row["end_frame"]
        if not isinstance(begin,int) or not isinstance(end,int) or not previous<=begin<end<=count:
            raise ValueError("语音段边界超出音频或顺序有误。")
        previous=end
    return pcm,rate,channels,meta,declared


def _open_http(base,path,payload,timeout,accept,headers=None):
    body=json.dumps(payload,ensure_ascii=False).encode("utf8") if payload is not None else None
    req=Request(base+path,data=body,headers={"Content-Type":"application/json","Accept":accept,**(headers or {})})
    try:
        return urlopen(req,timeout=timeout)
    except HTTPError as err:
        try:
            detail=json.loads(err.read()).get("error")
        except (ValueError,UnicodeError):
            detail=None
        raise OSError(detail or f"语音服务返回 HTTP {err.code}") from err


def _json_http(base,path,payload=None,timeout=.8,accept="application/json"):
    with _open_http(base,path,payload,timeout,accept) as result:
        return result.read()


@dataclass
class _AudioBlock:
    pcm: bytes
    rate: int
    channels: int
    row: dict
    meta: dict
    hop: int
    levels: np.ndarray


def _scene_matches(payload,meta):
    if "parts" not in payload:
        return True
    actual=[{"text":row["text"],"style":row.get("style")} for row in meta["segments"]]
    source=meta.get("provenance") or {}
    return (source.get("mode")=="scene_defaults" and "take_id" not in source
            and meta.get("requested_language")==payload["language"]
            and ("seed" not in payload or source.get("seed")==payload["seed"])
            and actual==payload["parts"] and meta["text"]=="".join(row["text"] for row in actual))


def _read_stream(response,payload,cancel,notify):
    request_id=payload["request_id"]
    text=payload.get("text") or "".join(row["text"] for row in payload["parts"])
    parts=payload.get("parts")
    rows=[]
    rate=channels=end=0
    source=None
    for line in response:
        if cancel.is_set():raise InterruptedError()
        item=json.loads(line)
        if item.get("request_id")!=request_id:raise ValueError("语音流请求编号不匹配。")
        kind=item.get("type")
        if kind=="error":raise OSError(item.get("error") or "语音服务中止了本次合成。")
        if kind=="complete":
            alignment=item["alignment"]
            audio=alignment["audio"]
            if (not rows or alignment.get("request_id")!=request_id or alignment.get("segments")!=rows
                    or alignment.get("provenance")!=source or audio.get("frame_count")!=end
                    or audio.get("sample_rate_hz")!=rate or audio.get("channels")!=channels
                    or not _scene_matches(payload,alignment) or not _japanese_alignment(alignment)):
                raise ValueError("语音流与最终时间轴不一致。")
            notify(alignment)
            return
        if kind!="segment" or item.get("index")!=len(rows) or item.get("start_frame")!=end:
            raise ValueError("语音流分段顺序有误。")
        if not rows:
            rate,channels=item["sample_rate_hz"],item["channels"]
            source=item.get("provenance")
        if (type(rate) is not int or rate<=0 or type(channels) is not int or channels<=0
                or item["sample_rate_hz"]!=rate or item["channels"]!=channels
                or item.get("provenance")!=source or item.get("requested_language")!=payload.get("language")):
            raise ValueError("语音流采样格式或请求参数不一致。")
        row=item["segment"]
        if (row.get("language") not in ("ja","Japanese") or not is_japanese_reading(row.get("text"))):
            raise ValueError("该段缺少可核对的日语读稿。")
        begin,stop=row["start_frame"],row["end_frame"]
        if type(begin) is not int or type(stop) is not int or not end<=begin<stop:
            raise ValueError("语音段边界无效。")
        if parts is not None:
            expected=parts[len(rows)] if len(rows)<len(parts) else {}
            if (not source or source.get("mode")!="scene_defaults" or "take_id" in source
                    or ("seed" in payload and source.get("seed")!=payload["seed"])
                    or expected!={"text":row.get("text"),"style":row.get("style")}):
                raise ValueError("语音流分段与请求的原文或演出不一致。")
        pcm=base64.b64decode(item["pcm_s16le"],validate=True)
        if len(pcm)!=(stop-end)*channels*2:raise ValueError("语音流采样数与段界不一致。")
        hop,levels=_levels(pcm,rate,channels,{})
        meta={"request_id":request_id,"kind":"synthesized","text":text,"requested_language":payload["language"],
              "provenance":source,"word_timings":None}
        notify(_AudioBlock(pcm,rate,channels,row,meta,hop,levels))
        rows.append(row)
        end=stop
    raise EOFError("语音流在最终时间轴送达前中断。")


def _ensure_service(root,port,cancel):
    """Start the project server only when nobody is listening on its loopback port."""
    base=f"http://127.0.0.1:{port}"
    def listening():
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            try:
                probe.bind(("127.0.0.1",port))
            except OSError as err:
                if err.winerror in (10013,10048):return True
                raise
            return False
    def healthy():
        info=json.loads(_json_http(base,"/health",timeout=1.))
        return info.get("status")=="ok" and info.get("service")=="atri-tts"
    if cancel.is_set():raise InterruptedError()
    with _startup_lock:
        child=_launched.get(port)
        if not listening() and (child is None or child.poll() is not None):
            executable=root/"tts"/".venv"/"Scripts"/"python.exe"
            script=root/"tts"/"web.py"
            if not executable.is_file():
                raise OSError("本地日语语音环境尚未配置，请参阅 docs/local-setup.md。")
            if cancel.is_set():raise InterruptedError()
            child=subprocess.Popen([str(executable),str(script),"--port",str(port)],cwd=root,
                 stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                 creationflags=subprocess.CREATE_NO_WINDOW)
            _launched[port]=child
    end=time.monotonic()+18.
    while not cancel.is_set():
        if listening():
            if healthy():return
            raise OSError("语音端口已由其他本机服务使用。")
        if child is not None and child.poll() is not None:
            raise OSError(f"本机语音服务未能启动（退出码 {child.returncode}）。")
        if time.monotonic()>=end:
            raise TimeoutError("等待本机语音服务启动超时。")
        cancel.wait(.1)
    raise InterruptedError()


def _single_synthesis_block(payload):
    if "parts" in payload:
        return len(payload["parts"]) == 1
    # Natural Japanese is grouped within each line up to 100 characters by
    # the generator. Longer input may need multiple decoder passes.
    text = payload["text"].strip()
    return len(text.splitlines()) == 1 and len(text) <= 100


class Speech(QObject):
    ready=Signal(dict)
    alignment_ready=Signal(dict)
    ended=Signal()
    failed=Signal(str)
    segment=Signal(dict)
    approaching_segment=Signal(dict,float)
    _loaded=Signal(object)

    def __init__(self,owner,root):
        super().__init__(owner)
        self.root=Path(root)
        self.endpoint="http://127.0.0.1:8875"
        self.stream_synthesis=True
        self._local_port=8875
        self._token=0
        self._cancel=None
        self._request_id=None
        self._request_endpoint=None
        self._active=False
        self._muted=False
        self._unmute_range=None
        self._audible=True
        self._device=None
        self._track=None
        self._stream_blocks=[]
        self._stream_complete=False
        self._waiting=False
        self._next_segment=0
        self._announced_segment=None
        self._segment_starts=()
        self._loaded.connect(self._accept,Qt.ConnectionType.QueuedConnection)
        self._timer=QTimer(self,interval=16)
        self._timer.timeout.connect(self._tick)
        if owner is not None:
            owner.destroyed.connect(self.stop)

    @property
    def active(self):
        return self._active

    def _begin(self,work,audible,request_id=None,request_endpoint=None,*,incremental=False):
        self.stop()
        token=self._token
        cancel=self._cancel=threading.Event()
        self._active,self._audible,self._request_id=True,bool(audible),request_id
        self._request_endpoint=request_endpoint
        def notify(result):
            if not cancel.is_set():
                try:self._loaded.emit((token,result))
                except RuntimeError:pass
        def run():
            try:
                if cancel.is_set():return
                data=work(cancel,notify) if incremental else work(cancel)
                if data is None:return
                pcm,rate,channels,meta,declared=data
                if cancel.is_set():return
                count=len(pcm)//(2*channels)
                hop,levels=_levels(pcm,rate,channels,declared)
                meta.update(frame_count=count,sample_rate_hz=rate,duration_seconds=count/rate)
                result=(pcm,rate,channels,meta,hop,levels)
            except Exception as err:
                result=str(err)
            notify(result)
        threading.Thread(target=run,daemon=True,name="atri-speech-load").start()

    def original(self,clip,audible=True):
        cid=clip["id"] if isinstance(clip,dict) else str(clip)
        def read(cancel):
            if not re.fullmatch(r"[A-Za-z0-9_.-]+",cid) or ".." in cid:
                raise ValueError("原作语音编号无效。")
            record=original_catalog(self.root).get(cid)
            if record is None:raise ValueError("原作录音清单没有这个编号。")
            try:
                server=json.loads(_json_http(self.endpoint,"/api/clips/"+quote(cid,safe="")+"?mouth=1"))
            except (OSError,ValueError):
                server={}
            if cancel.is_set():raise InterruptedError()
            wav=self.root/"desktop"/"audio"/(cid+".wav")
            try:
                path=wav
                pcm,rate,channels=_audio_file(self.root,path)
                declared=server.get("desktop_wav") or {}
            except (OSError,ValueError,wave.Error,EOFError):
                declared=server.get("audio") or {}
                path=self.root/(record.get("file") or "assets/voice/"+cid+".opus")
                pcm,rate,channels=_audio_file(self.root,path)
            text=server.get("text_zh") or record.get("zh","")
            meta={"kind":"original","clip_id":cid,"language":"Japanese","text":text,"text_zh":text,
                  "text_ja":server.get("text_ja") or record.get("ja",""),"script":server.get("script"),
                  "source":str(path),"alignment_source":"service" if declared.get("mouth_rms") else "local_pcm",
                  "segments":[]}
            return pcm,rate,channels,meta,declared
        self._begin(read,audible)

    def synthesize(self,text,language="auto",emotion="natural",audible=True,*,continuous=False):
        if language not in ("auto","ja") or not is_japanese_reading(text):
            self.stop()
            self.failed.emit("这段文字缺少日语读稿。")
            return None
        playback = {"continuous": True} if continuous else {}
        return self._request_synthesis("/api/synthesize",{"text":text,"language":"ja","emotion":emotion},audible,**playback)

    def synthesize_scene(self,text,*,language,parts,audible=True,seed=None,continuous=False):
        """Speak the supplied reply exactly as written, with one direction per span."""
        if language!="ja" or not is_japanese_reading(text):
            raise ValueError("语境朗读需要日语读稿。")
        if not isinstance(parts,(list,tuple)) or not parts:
            raise ValueError("请提供与原文对应的语境分段。")
        clauses=[]
        for row in parts:
            if not isinstance(row,dict) or set(row)!={"text","style"}:
                raise ValueError("每个语境分段需要 text 和 style。")
            phrase,style=row["text"],row["style"]
            if (not is_japanese_reading(phrase)
                    or style not in ("natural","confident","worried","comfort")):
                raise ValueError("语境分段缺少可读文字或可用语气。")
            clauses.append({"text":phrase,"style":style})
        if "".join(row["text"] for row in clauses)!=text:
            raise ValueError("语境分段未完整保留回复的原文和空白。")
        payload={"language":language,"parts":clauses}
        if seed is not None:
            if type(seed) is not int or not -(2**63)<=seed<2**64:
                raise ValueError("语境朗读的随机种子超出范围。")
            payload["seed"]=seed
        playback = {"continuous": True} if continuous else {}
        return self._request_synthesis("/api/scene-synthesize",payload,audible,**playback)

    def _request_synthesis(self,route,payload,audible,*,continuous=False):
        request_id=uuid.uuid4().hex
        endpoint,port=self.endpoint,self._local_port
        # A slow next generation would leave a hole in speech already playing.
        # Only diary reads opt into reuse. Buffered chat uses an ordinary ZIP.
        stream=self.stream_synthesis and not continuous and _single_synthesis_block(payload)
        def generate(cancel,notify):
            if endpoint==f"http://127.0.0.1:{port}":
                _ensure_service(self.root,port,cancel)
            if cancel.is_set():raise InterruptedError()
            body=dict(payload,request_id=request_id)
            cache=None
            if stream:
                with _open_http(endpoint,route,body,600,"application/x-ndjson, application/zip") as response:
                    if response.headers.get_content_type()=="application/x-ndjson":
                        _read_stream(response,body,cancel,notify)
                        return
                    data=response.read()
            elif continuous:
                with _open_http(endpoint,route,body,600,"application/zip",{"X-Atri-Reuse-Audio":"1"}) as response:
                    data=response.read()
                    cache=response.headers.get("X-Atri-Audio-Cache")
            else:
                data=_json_http(endpoint,route,body,timeout=600,accept="application/zip")
            if cancel.is_set():raise InterruptedError()
            result=_bundle(io.BytesIO(data),request_id)
            if not _japanese_alignment(result[3]):
                raise ValueError("合成包缺少可核对的日语分段。")
            if not _scene_matches(payload,result[3]):
                raise ValueError("语境朗读包与本次回复的原文或演出分段不一致。")
            if cache in ("hit","miss"):
                result[3]["audio_cache"]=cache
            return result
        self._begin(generate,audible,request_id,endpoint,incremental=True)
        return request_id

    def replay_bundle(self,path,audible=True):
        def load(cancel):
            result=_bundle(Path(path))
            if not _japanese_alignment(result[3]):
                raise ValueError("这份回放包缺少可核对的日语分段。")
            result[3]["source"]=str(path)
            result[3]["replay"]=True
            return result
        self._begin(load,audible)

    @Slot(object)
    def _accept(self,payload):
        token,result=payload
        if token!=self._token or not self._active:return
        if isinstance(result,_AudioBlock):
            try:self._append_block(result)
            except OSError as err:
                self.stop();self.failed.emit(str(err))
            return
        if isinstance(result,dict):
            self._request_id=None
            self._stream_complete=True
            self.alignment_ready.emit(result)
            if token==self._token:self._tick()
            return
        if isinstance(result,str):
            self.stop()
            self.failed.emit(result)
            return
        self._request_id=None
        pcm,rate,channels,meta,hop,levels=result
        self._track=result
        self._next_segment=0
        self._announced_segment=None
        self._segment_starts=tuple(s["start_frame"] for s in meta.get("segments") or ())
        self._silent_frame=0
        self._silent_at=time.monotonic()
        try:
            if self._audible and not self._muted:
                self._device=WaveOutput(pcm,rate,channels)
        except OSError as err:
            self.stop();self.failed.emit(str(err));return
        meta["audible"]=self._device is not None
        self._timer.start()
        self.ready.emit(dict(meta))
        if token==self._token:self.alignment_ready.emit(dict(meta))
        if token==self._token:self._tick()

    def _append_block(self,block):
        first=not self._stream_blocks
        rate,channels=block.rate,block.channels
        if first:
            meta=dict(block.meta,segments=[],sample_rate_hz=rate,frame_count=0)
            self._track=(bytearray(),rate,channels,meta,0,None)
            self._segment_starts=()
            self._next_segment=0
            self._announced_segment=None
        pcm,_,_,meta,_,_=self._track
        offset=meta["frame_count"]
        # A device drained during inference must resume from its last real frame.
        drained=first or self._waiting or self._position()>=offset or (self._device and self._device.done)
        pcm.extend(block.pcm)
        self._stream_blocks.append((offset,block.hop,block.levels))
        self._segment_starts+=(block.row["start_frame"],)
        meta["segments"].append(block.row)
        meta["frame_count"]=block.row["end_frame"]
        meta["duration_seconds"]=meta["frame_count"]/rate
        if drained:
            self._silent_frame,self._silent_at=offset,time.monotonic()
        if self._device:
            self._device.append(block.pcm)
        elif self._audible and not self._muted:
            self._device=WaveOutput(pcm,rate,channels,offset=offset)
        self._waiting=False
        self._timer.start()
        token=self._token
        if first:
            meta["audible"]=self._device is not None
            self.ready.emit(dict(meta,segments=list(meta["segments"]),partial=True))
        if token==self._token:self._tick()

    def _position(self):
        if self._waiting:return self._silent_frame
        if self._device is not None:
            return self._device.position()
        return self._silent_frame+int((time.monotonic()-self._silent_at)*self._track[1])

    def level(self):
        if self._track is None or self._device is None:return False,0.
        try:
            frame=self._position()
        except OSError:
            return False,0.
        _,_,_,meta,hop,values=self._track
        if self._device.done or frame>=meta["frame_count"]:return False,0.
        gain=1.
        if self._unmute_range is not None:
            start,last=self._unmute_range
            if frame<=last:
                gain=math.sin(.5*math.pi*max(0,frame-start)/max(1,last-start))**2
            else:
                self._unmute_range=None
        end=meta["frame_count"]
        if self._segment_starts:
            idx=bisect_right(self._segment_starts,frame)-1
            if idx<0:return True,0.
            end=meta["segments"][idx]["end_frame"]
            if frame>=end:return True,0.
        if self._stream_blocks:
            # The row's leading gap belongs to its own PCM block.
            base,hop,values=self._stream_blocks[max(0,idx)]
            frame-=base
            end-=base
        exact=frame/hop
        first=min(len(values)-1,int(exact));last=min(len(values)-1,(end-1)//hop,first+1)
        return True,gain*float(values[first]+(values[last]-values[first])*(exact-first))

    @Slot()
    def _tick(self):
        if not self._track:return
        token=self._token
        meta=self._track[3]
        try:
            frame=self._position()
        except OSError as err:
            self.stop();self.failed.emit(str(err));return
        if self._device is not None and self._device.done:
            frame=meta["frame_count"]
        segments=meta.get("segments") or []
        while self._next_segment<len(segments) and frame>=segments[self._next_segment]["start_frame"]:
            row=segments[self._next_segment]
            self._next_segment+=1
            self.segment.emit(dict(row))
            if token!=self._token:return
        upcoming=self._next_segment
        if 0<upcoming<len(segments) and upcoming!=self._announced_segment:
            row=segments[upcoming]
            start=row["start_frame"]
            previous_end=segments[upcoming-1]["end_frame"]
            rate=self._track[1]
            remaining=(start-frame)/rate
            if previous_end<=frame and start-previous_end>=.075*rate and 0<remaining<=.100:
                self._announced_segment=upcoming
                self.approaching_segment.emit(dict(row),remaining)
                if token!=self._token:return
        if (self._device.done if self._device is not None else frame>=meta["frame_count"]):
            if self._stream_blocks and not self._stream_complete:
                # A later chunk resumes on the same media clock and driver.
                self._silent_frame=meta["frame_count"]
                self._waiting=True
                self._timer.stop()
            else:
                self.stop();self.ended.emit()

    def set_muted(self,muted):
        muted=bool(muted)
        if self._muted==muted:return
        self._muted=muted
        self._unmute_range=None
        if self._track is None or not self._audible:return
        try:
            frame=self._position()
            if muted and self._device:
                self._device.close();self._device=None
                self._silent_frame,self._silent_at=frame,time.monotonic()
            elif not muted and frame<self._track[3]["frame_count"]:
                pcm,rate,channels=self._track[:3]
                if frame>0:
                    # Only a user unmute in the middle of a track needs a new onset.
                    # Keep the virtual media origin, every frame, and the stored track.
                    data=memoryview(pcm)
                    start=frame*channels*2
                    end=min(len(data),start+round(rate*.012)*channels*2)
                    head=np.frombuffer(data[start:end],"<i2").reshape(-1,channels)
                    if head.any():
                        gain=np.sin(np.linspace(0,np.pi/2,len(head)))**2
                        edge=np.rint(head*gain[:,None]).astype("<i2").tobytes()
                        pcm=b"".join((data[:start],edge,data[end:]))
                        self._unmute_range=frame,frame+len(head)-1
                self._device=WaveOutput(pcm,rate,channels,offset=frame)
        except OSError as err:
            self.stop();self.failed.emit(str(err))

    def stop(self):
        self._token+=1
        self._active=False
        self._unmute_range=None
        self._timer.stop()
        if self._cancel:self._cancel.set()
        self._cancel=None
        request_id,self._request_id=self._request_id,None
        base,self._request_endpoint=self._request_endpoint,None
        if request_id:
            def cancel():
                for delay in (0.,.1,.3):
                    if delay:time.sleep(delay)
                    try:
                        _json_http(base,"/api/cancel",{"request_id":request_id},timeout=1.)
                        return
                    except OSError as err:
                        if getattr(err.__cause__,"code",None)!=404:return
                    except ValueError:return
            threading.Thread(target=cancel,daemon=True,name="atri-speech-cancel").start()
        if self._device:
            # Some alternate output implementations only expose immediate close.
            getattr(self._device,"release",self._device.close)()
        self._device=self._track=None
        self._stream_blocks.clear()
        self._stream_complete=self._waiting=False
