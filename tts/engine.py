"""Synthesize new speech with a locally installed Qwen3-TTS Base model."""

import argparse
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import replace
from functools import lru_cache
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from time import perf_counter

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
MODEL = HERE / "models" / "Qwen3-TTS-12Hz-1.7B-Base"
REFERENCE = "ATR_b121_009"
JAPANESE_REFERENCES = {
    "natural": REFERENCE,
    "cheerful": "ATR_b204_079",
    "gentle": "ATR_b206_107",
    "proud": "ATR_b102_062",
}
LANGUAGES = {"zh": "Chinese", "ja": "Japanese", "en": "English"}
INFERENCE_LOCK = threading.Lock()
HIRAGANA = str.maketrans({c: c - 0x60 for c in range(0x30A1, 0x30F7)})


def resolve_emotion(text, language, emotion="natural"):
    if emotion not in ("auto", *JAPANESE_REFERENCES):
        raise ValueError("语气可选按句意、自然、轻快、温柔或得意。")
    if language != "ja" and (language != "auto" or not re.search(r"[ぁ-ヿ]", text)):
        return "natural"
    if emotion != "auto":
        return emotion
    if re.search(r"大丈夫|だいじょうぶ|安心|無理し|無理せず|おやすみ|休んで|休みましょう|休もう|心配|悲し|そばに|泣かない|お大事に", text):
        return "gentle"
    if re.search(r"危な|危険|逃げ|やめて|やめましょう|怖い|こわい|助けて", text):
        return "natural"
    if re.search(r"(?:わたし|私|あたし)(?:は|が|って|、|こう見えても|の方が)*高性能|高性能な(?:わたし|私)|エッヘン|えっへん|フフーン|ふふん|(?:^|[。！「])高性能ですから", text):
        return "proud"
    if re.search(r"楽しみ|うれしい|嬉しい|やった|わーい|行こう|行きませんか|ましょう|見てください[!！]|おめでとう|♪", text):
        return "cheerful"
    return "natural"


def reference_for(language="auto", emotion="natural"):
    voice_id = JAPANESE_REFERENCES.get(emotion, REFERENCE) if language in ("auto", "ja") else REFERENCE
    return voice_id, PROJECT / "assets" / "voice" / f"{voice_id}.opus"


def japanese_readings(text):
    import pyopenjtalk

    text = text.replace("夏生さん", "なつきさん").replace("水菜萌", "みなも")
    parts, pos = [], 0
    for token in pyopenjtalk.run_frontend(text):
        word = token["string"]
        i = text.find(word, pos)
        if i < 0:
            continue
        reading = token.get("read", "")
        # With kanji input Qwen dropped the noun 続き in a quoted diary entry.
        # Preserve verbs and compounds that share its spelling or stem.
        kana_noun = re.fullmatch(r"[一-鿿々]{2,}", word) or (word == "続き" and reading == "ツヅキ")
        if token["pos"] == "名詞" and kana_noun and re.fullmatch(r"[ァ-ヺー]+", reading):
            reading = reading.translate(HIRAGANA)
        else:
            reading = word
        parts.extend((text[pos:i], reading))
        pos = i + len(word)
    return "".join(parts) + text[pos:]


def language_for(sentence, choice):
    if choice != "auto":
        return LANGUAGES[choice]
    if re.search(r"[ぁ-ヿ]", sentence):
        return "Japanese"
    if re.search(r"[가-힣]", sentence):
        return "Korean"
    if re.search(r"[\u3400-\u9fff]", sentence):
        return "Chinese"
    if re.fullmatch(r"[\d\s\W_]+", sentence):
        return "Chinese"
    if sentence.isascii() and re.search(r"[A-Za-z]", sentence):
        return "English"
    return "Auto"


def chunks(text):
    # Keep sentence endings and closing quotes with the spoken text.
    end = r'''[。！？!?]+[」』”’）)]*|(?<=\w)\.(?=\s|$)|\n+'''
    start = 0
    for match in re.finditer(end, text):
        yield from split_long(text[start:match.end()])
        start = match.end()
    yield from split_long(text[start:])


def split_long(sentence):
    sentence = sentence.strip()
    limit = 100 if re.search(r"[\u3000-\u9fff]", sentence) else 230
    while len(sentence) > limit:
        window = sentence[:limit]
        places = list(re.finditer(r"[,，、；;：:\s]+", window))
        cut = places[-1].end() if places and places[-1].end() >= limit // 3 else limit
        head, sentence = sentence[:cut].strip(), sentence[cut:].strip()
        if any(c.isalnum() for c in head):
            yield head
    if any(c.isalnum() for c in sentence):
        yield sentence


def utterances(text, choice):
    for paragraph in text.splitlines():
        group, current = "", None
        for part in chunks(paragraph):
            language = language_for(part, choice)
            gap = " " if group.endswith((".", "!", "?")) and part[0].isascii() else ""
            combined = group + gap + part
            limit = 100 if re.search(r"[\u3000-\u9fff]", combined) else 230
            if group and (language != current or len(combined) > limit):
                yield group, current
                group = part
            else:
                group = combined
            current = language
        if group:
            yield group, current


@lru_cache(maxsize=1)
def model():
    import torch
    from qwen_tts import Qwen3TTSModel

    if not all((MODEL / f).is_file() for f in ("model.safetensors", "speech_tokenizer/model.safetensors")):
        raise FileNotFoundError(f"本机模型文件不齐全：{MODEL}")
    gpu = torch.cuda.is_available()
    return Qwen3TTSModel.from_pretrained(
        str(MODEL),
        device_map="cuda:0" if gpu else "cpu",
        dtype=torch.bfloat16 if gpu and torch.cuda.is_bf16_supported() else torch.float32,
        attn_implementation="sdpa",
        local_files_only=True,
    )


@lru_cache(maxsize=8)
def voice_prompt(voice_id, embedding_only=False):
    import soundfile as sf

    rows = json.loads((PROJECT / "assets/voice/all.json").read_text(encoding="utf-8"))
    entry = next(row for row in rows if row["id"] == voice_id)
    sound, rate = sf.read(PROJECT / entry["file"], dtype="float32")
    items = model().create_voice_clone_prompt(
        ref_audio=(sound, rate),
        ref_text=None if embedding_only else entry["ja"],
        x_vector_only_mode=embedding_only,
    )
    if embedding_only:
        return [replace(item, ref_text=None, ref_code=None) for item in items]
    return items


def check_cancelled(event):
    if event is not None and event.is_set():
        raise CancelledError()


@contextmanager
def inference_slot(event):
    if event is None:
        INFERENCE_LOCK.acquire()
    else:
        while not INFERENCE_LOCK.acquire(timeout=.1):
            check_cancelled(event)
    try:
        check_cancelled(event)
        yield
    finally:
        INFERENCE_LOCK.release()


def speak_part(part, language, prompt, *, cancel_event=None):
    import numpy as np

    loaded = model()
    check_cancelled(cancel_event)
    # Qwen drives its talker once per codec frame inside generate_voice_clone.
    hook = (loaded.model.talker.register_forward_pre_hook(lambda *_: check_cancelled(cancel_event))
            if cancel_event is not None else None)
    try:
        waves, rate = loaded.generate_voice_clone(
            text=part, language=language, voice_clone_prompt=prompt,
            max_new_tokens=max(160, min(2048, 14 * len(part) + 120)),
        )
    finally:
        if hook is not None:
            hook.remove()
    wave = np.asarray(waves[0], dtype=np.float32)
    if wave.ndim != 1 or wave.size == 0 or not np.isfinite(wave).all():
        raise RuntimeError("合成器返回了无效的音频。")
    peak = np.max(np.abs(wave))
    if peak < 1e-5:
        raise RuntimeError("合成器返回了静音。")
    if peak > 0.999:
        wave = wave * (0.999 / peak)
    return wave, rate


class RenderTiming:
    """Measure blocking wall time in the engine, optionally synchronizing GPU stage boundaries."""

    def __init__(self, report):
        self.report = report
        self.started = perf_counter()
        if report is not None:
            report["segments"] = []

    def imports_done(self):
        if self.report is not None:
            self.report["runtime_import_s"] = perf_counter() - self.started

    def model_ready(self, queued):
        if self.report is None:
            return
        now = perf_counter()
        self.report["queue_s"] = now - queued
        cached = bool(model.cache_info().currsize)
        self.report["model_cached"] = cached
        if not cached:
            model()
            self.synchronize()
        self.report["model_load_s"] = 0.0 if cached else perf_counter() - now

    @staticmethod
    def synchronize():
        import torch

        if torch.cuda.is_initialized():
            torch.cuda.synchronize()

    def reference(self, voice_id, embedding_only):
        if self.report is None:
            return voice_prompt(voice_id, embedding_only), {}
        before = voice_prompt.cache_info()
        start = perf_counter()
        prompt = voice_prompt(voice_id, embedding_only)
        cached = voice_prompt.cache_info().hits > before.hits
        if not cached:
            self.synchronize()
        return prompt, {"reference_s": perf_counter() - start, "reference_cached": cached}

    def segment_ready(self, row, detail, post_start):
        if self.report is not None:
            now = perf_counter()
            self.report["segments"].append(dict(detail, reference_clip_id=row["reference_clip_id"],
                                                 postprocess_s=now - post_start, ready_s=now - self.started))

    def done(self):
        if self.report is not None:
            self.report["total_s"] = perf_counter() - self.started


def write_segment(stream, samples, gap_frames, streaming):
    """Return the segment bounds and, on request, the exact newly written PCM including its gap."""
    import soundfile as sf

    # Both paths use libsndfile's float-to-PCM16 conversion.
    if streaming:
        buffer = io.BytesIO()
        sf.write(buffer, samples, stream.samplerate, format="RAW", subtype="PCM_16", endian="LITTLE")
        pcm = bytes(2 * gap_frames) + buffer.getvalue()
        start = stream.tell() + gap_frames
        stream.buffer_write(pcm, dtype="int16")
    else:
        if gap_frames:
            stream.buffer_write(bytes(2 * gap_frames), dtype="int16")
        start = stream.tell()
        stream.write(samples)
        pcm = None
    return start, stream.tell(), pcm


def mandarin_pitch(wave, rate):
    import numpy as np
    import parselmouth
    from parselmouth.praat import call

    sound = parselmouth.Sound(wave, sampling_frequency=rate)
    manipulation = call(sound, "To Manipulation", 0.005, 110, 850)
    tier = call(manipulation, "Extract pitch tier")
    if not call(tier, "Get number of points"):
        return wave
    # A small uniform shift preserves the shape of Mandarin tones and the duration.
    call(tier, "Multiply frequencies", sound.xmin, sound.xmax, 2 ** (1.5 / 12))
    call([tier, manipulation], "Replace pitch tier")
    out = call(manipulation, "Get resynthesis (overlap-add)").values[0].astype(np.float32)
    return out / max(1.0, float(np.max(np.abs(out))) / 0.999)


def synthesize(text: str, language: str, output_path: Path, emotion: str = "natural",
               *, segments=None, cancel_event=None, on_segment=None, timings=None) -> None:
    timer = RenderTiming(timings)
    import soundfile as sf
    import torch
    timer.imports_done()

    if language not in {"auto", *LANGUAGES}:
        raise ValueError("请选择自动、中文、日语或英语。")
    if emotion not in ("auto", *JAPANESE_REFERENCES):
        raise ValueError("未知语气。")
    parts = list(utterances(text, language))
    if not parts:
        raise ValueError("请输入含文字或数字的句子。")
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = []
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".wav", delete=False) as handle:
        partial = Path(handle.name)
    try:
        queued = perf_counter()
        with inference_slot(cancel_event), torch.inference_mode():
            timer.model_ready(queued)
            planned = []
            for part, lang in parts:
                check_cancelled(cancel_event)
                style = resolve_emotion(part, "ja", emotion) if lang == "Japanese" else "natural"
                voice_id = JAPANESE_REFERENCES[style]
                prompt, detail = timer.reference(voice_id, lang != "Japanese")
                planned.append((part, lang, style, voice_id, prompt, detail))
            torch.manual_seed(213)
            stream = None
            try:
                for part, spoken_language, style, voice_id, prompt, detail in planned:
                    check_cancelled(cancel_event)
                    prep = perf_counter()
                    original_part = part
                    if spoken_language == "Japanese":
                        part = japanese_readings(part)
                        if style == "cheerful":
                            part = re.sub(r"。(?=[」』”’）)]*$)", "！", part)
                        elif style == "gentle":
                            part = re.sub(r"[!！]+(?=[」』”’）)]*$)", "。", part)
                    infer = perf_counter()
                    wave, rate = speak_part(part, spoken_language, prompt, cancel_event=cancel_event)
                    post = perf_counter()
                    detail.update(text_prepare_s=infer - prep, inference_s=post - infer)
                    check_cancelled(cancel_event)
                    if spoken_language == "Chinese":
                        wave = mandarin_pitch(wave, rate)
                    gap = 0 if stream is None else round(rate * .15)
                    if stream is None:
                        stream = sf.SoundFile(partial, "w", samplerate=rate, channels=1, subtype="PCM_16")
                    start, end, pcm = write_segment(stream, wave, gap, on_segment is not None)
                    row = {"text": original_part, "model_text": part, "language": spoken_language,
                           "emotion": style, "reference_clip_id": voice_id, "start_frame": start, "end_frame": end}
                    timer.segment_ready(row, detail, post)
                    rendered.append(row)
                    if on_segment is not None:
                        check_cancelled(cancel_event)
                        on_segment(row.copy(), pcm, rate)
            finally:
                if stream is not None:
                    stream.close()
        check_cancelled(cancel_event)
        partial.replace(target)
        if segments is not None:
            segments.extend(rendered)
        timer.done()
    finally:
        partial.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="用本地亚托莉原声作为参考朗读文字")
    parser.add_argument("text")
    parser.add_argument("--language", choices=["auto", *LANGUAGES], default="auto")
    parser.add_argument("--emotion", choices=["auto", *JAPANESE_REFERENCES], default="natural")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    synthesize(args.text, args.language, args.output, emotion=args.emotion)
