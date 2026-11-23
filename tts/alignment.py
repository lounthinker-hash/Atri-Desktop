"""Sample-frame metadata for the desktop player and original game recordings."""

from functools import lru_cache
import json
from pathlib import Path
import zipfile

PROJECT = Path(__file__).resolve().parent.parent
EXAMPLES = Path(__file__).with_name("original_examples.json")


def audio_info(path, *, mouth=False):
    import numpy as np
    import soundfile as sf

    if Path(path).suffix.lower() == ".opus":
        import av

        with av.open(str(path)) as container:
            stream = container.streams.audio[0]
            rate = stream.codec_context.sample_rate
            channels = stream.codec_context.channels
            count, chunks = 0, []
            for frame in container.decode(stream):
                count += frame.samples
                if mouth:
                    # FFmpeg's Opus decoder returns normalized planar float PCM.
                    if frame.format.name != "fltp":
                        raise ValueError(f"Unexpected Opus PCM format {frame.format.name}")
                    chunks.append(frame.to_ndarray().T)
            if mouth:
                samples = np.concatenate(chunks)
    else:
        with sf.SoundFile(path) as f:
            rate, count, channels = f.samplerate, len(f), f.channels
            if mouth:
                samples = f.read(dtype="float32", always_2d=True)
    info = {"sample_rate_hz": rate, "frame_count": count, "channels": channels,
            "duration_seconds": count / rate}
    if mouth:
        hop = rate // 20
        levels = [round(float(np.sqrt(np.mean(np.square(samples[i:i+hop], dtype=np.float64)))), 6)
                  for i in range(0, count, hop)]
        info["mouth_rms"] = {"first_frame": 0, "hop_frames": hop, "linear_rms": levels}
    return info


@lru_cache(maxsize=1)
def original_index():
    records = json.loads((PROJECT / "assets/voice/all.json").read_text(encoding="utf-8"))
    source = PROJECT / "data/game.js"
    if not source.is_file():
        return {r["id"]: r for r in records}, {}
    game = source.read_text(encoding="utf-8-sig")
    rows = json.JSONDecoder().raw_decode(game[game.index("{"):])[0]["rows"]
    spoken = {r["voice"]: r for r in rows if r.get("own") and r.get("voice")}
    return {r["id"]: r for r in records}, spoken


@lru_cache(maxsize=1)
def staging_index():
    staging = json.loads((PROJECT / "data/staging.json").read_text(encoding="utf-8-sig"))
    return staging["byVoice"]


def original_metadata(clip_id, *, mouth=False):
    records, spoken = original_index()
    record = records[clip_id]
    script = spoken.get(clip_id)
    opus = PROJECT / record["file"]
    wav = PROJECT / "desktop/audio" / (clip_id + ".wav")
    result = {
        "schema_version": 1, "kind": "original", "clip_id": clip_id,
        "text_ja": record["ja"], "text_zh": record["zh"],
        "script": None, "staging_frame_index": staging_index().get(clip_id), "word_timings": None,
        "audio": {"file": record["file"], **audio_info(opus, mouth=mouth)},
        "desktop_wav": None,
    }
    if script:
        result["script"] = {"scene": script["chapter"], "title": record["title"],
                            "branch": script["label"], "row": script["row"],
                            "raw_ja": script["lines"][1], "raw_zh": script["lines"][0]}
    if wav.is_file():
        result["desktop_wav"] = {"file": wav.relative_to(PROJECT).as_posix(), **audio_info(wav, mouth=mouth)}
    return result


def render_metadata(wav, text, language, emotion, segments, request_id, *, provenance=None):
    metadata = {
        "schema_version": 1, "kind": "synthesized", "request_id": request_id,
        "text": text, "requested_language": language, "emotion": emotion,
        "audio": {"file": "audio.wav", **audio_info(wav, mouth=True)},
        "segments": segments, "word_timings": None,
    }
    if provenance is not None:
        metadata["provenance"] = provenance
    return metadata


def make_render_bundle(path, wav, text, language, emotion, segments, request_id, *, provenance=None):
    metadata = render_metadata(wav, text, language, emotion, segments, request_id, provenance=provenance)
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.write(wav, "audio.wav")
        bundle.writestr("alignment.json", json.dumps(metadata, ensure_ascii=False),
                        compress_type=zipfile.ZIP_DEFLATED)
