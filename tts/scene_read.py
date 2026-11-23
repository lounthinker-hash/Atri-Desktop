"""Read a line or a sequence of directed clauses with local Qwen voice-clone prompts."""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import re
import tempfile
from time import perf_counter

if __package__:
    from . import engine
else:
    import engine


@dataclass(frozen=True)
class Performance:
    text: str
    style: str
    reference: str
    speaker_only: bool = False
    model_text: str | None = None


REFERENCES = {
    "ja": {"natural": engine.REFERENCE, "confident": "ATR_b114_014",
           "worried": "ATR_b405_102", "comfort": "ATR_b301_017"},
    "zh": {"confident": "ATR_b402_026", "worried": "ATR_b114_064", "comfort": "ATR_b114_016"},
}
TAKES_FILE = Path(__file__).resolve().parent / "试听" / "语境对听" / "测量.json"


def take_ids() -> list[str]:
    samples = json.loads(TAKES_FILE.read_text(encoding="utf-8-sig"))["samples"]
    return [key for key, sample in samples.items() if isinstance(sample.get("render"), dict)]


def load_take(take_id: str) -> tuple[str, list[Performance], int]:
    batch = json.loads(TAKES_FILE.read_text(encoding="utf-8-sig"))
    sample = batch["samples"].get(take_id)
    if sample is None or not isinstance(sample.get("render"), dict):
        raise ValueError(f"没有名为 {take_id} 的可复现试听样本。")
    record = sample["render"]
    seed = record.get("seed", sample.get("seed", batch.get("seed")))
    if type(seed) is not int:
        raise ValueError(f"试听样本 {take_id} 缺少有效的随机种子。")
    try:
        language = record["language"]
        parts = [
            Performance(segment["text"], segment["style"], segment["reference_clip_id"],
                        segment["speaker_only"], segment["model_text"])
            for segment in record["segments"]
        ]
        if (language not in REFERENCES or not parts or "".join(p.text for p in parts) != record["text"]
                or any(not isinstance(p.model_text, str) or not p.model_text
                       or not isinstance(p.reference, str) or not p.reference
                       or not isinstance(p.speaker_only, bool) for p in parts)):
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"试听样本 {take_id} 的演出记录不完整。") from exc
    return language, parts, seed


def take_plan(take_id: str) -> dict:
    language, parts, seed = load_take(take_id)
    return {"take_id": take_id, "text": "".join(p.text for p in parts), "language": language, "seed": seed,
            "segments": [{"text": p.text, "model_text": p.model_text, "style": p.style,
                          "reference_clip_id": p.reference, "speaker_only": p.speaker_only} for p in parts]}


def direct(text, language, style):
    reference = REFERENCES[language][style]
    if language == "ja" and style == "worried" and re.search(r"ですか[、。！？!?\s]|ですか$|[？?]", text):
        reference = "ATR_b401_043"
    return Performance(text, style, reference, speaker_only=(language == "zh"))


def quiet_edges(samples, rate):
    """Leave 40 ms of low-level audio beside each active edge when joining clauses."""
    import numpy as np

    hop = max(1, round(rate * .01))
    padded = np.pad(samples, (0, -len(samples) % hop))
    rms = np.sqrt(np.mean(padded.reshape(-1, hop) ** 2, axis=1))
    active = np.flatnonzero(rms > min(.003, rms.max() * .04))
    if not len(active):
        return 0, len(samples)
    return max(0, (int(active[0]) - 4) * hop), min(len(samples), (int(active[-1]) + 5) * hop)


def render(parts, language, output, *, seed=213, cancel_event=None, on_segment=None, timings=None):
    timer = engine.RenderTiming(timings)
    engine.check_cancelled(cancel_event)
    import soundfile as sf
    import torch
    timer.imports_done()

    parts = list(parts)
    if language not in REFERENCES or not parts:
        raise ValueError("请选择日语或普通话，并提供台词。")
    for part in parts:
        if part.style not in REFERENCES[language] or not any(c.isalnum() for c in part.text):
            raise ValueError("语境或台词无效。")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".partial.wav", delete=False) as handle:
        partial = Path(handle.name)
    segments = []
    try:
        queued = perf_counter()
        with engine.inference_slot(cancel_event), torch.inference_mode(), sf.SoundFile(
                partial, "w", samplerate=24000, channels=1, subtype="PCM_16") as stream:
            timer.model_ready(queued)
            for i, part in enumerate(parts):
                engine.check_cancelled(cancel_event)
                prompt, detail = timer.reference(part.reference, part.speaker_only)
                prep = perf_counter()
                text = part.model_text
                if text is None:
                    text = engine.japanese_readings(part.text) if language == "ja" else part.text
                engine.check_cancelled(cancel_event)
                torch.manual_seed(seed)
                infer = perf_counter()
                samples, rate = engine.speak_part(text, engine.LANGUAGES[language], prompt, cancel_event=cancel_event)
                post = perf_counter()
                detail.update(text_prepare_s=infer - prep, inference_s=post - infer)
                engine.check_cancelled(cancel_event)
                if rate != stream.samplerate:
                    raise ValueError(f"Unexpected Qwen sample rate {rate}")
                if language == "zh":
                    samples = engine.mandarin_pitch(samples, rate)
                first, last = quiet_edges(samples, rate)
                start_sample = first if i else 0
                end_sample = last if i + 1 < len(parts) else len(samples)
                start, end, pcm = engine.write_segment(stream, samples[start_sample:end_sample],
                                                       round(rate * .15) if i else 0, on_segment is not None)
                row = {"text": part.text, "model_text": text, "style": part.style,
                       "reference_clip_id": part.reference, "speaker_only": part.speaker_only,
                       "start_frame": start, "end_frame": end}
                timer.segment_ready(row, detail, post)
                segments.append(row)
                if on_segment is not None:
                    engine.check_cancelled(cancel_event)
                    on_segment(dict(row, language=engine.LANGUAGES[language]), pcm, rate)
        engine.check_cancelled(cancel_event)
        partial.replace(output)
        timer.done()
    finally:
        partial.unlink(missing_ok=True)
    return {"text": "".join(p.text for p in parts), "language": language, "seed": seed, "sample_rate_hz": rate,
            "frame_count": segments[-1]["end_frame"], "segments": segments, "word_timings": None}


def main():
    parser = argparse.ArgumentParser(description="按语境朗读新句，或复现已有试听样本。")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--take", metavar="ID", help="按保存的演出记录复现样本")
    modes.add_argument("--list-takes", action="store_true", help="列出可复现的样本 ID")
    parser.add_argument("--language", choices=REFERENCES)
    parser.add_argument("--part", nargs=2, metavar=("STYLE", "TEXT"), action="append",
                        help="confident（自信）、worried（担心）、comfort（安慰）或日语 natural（自然）")
    result = parser.add_mutually_exclusive_group()
    result.add_argument("--output", type=Path)
    result.add_argument("--plan", action="store_true", help="查看命名样本的固定演出参数")
    parser.add_argument("--seed", type=int, help="自选台词的随机种子，默认 213")
    args = parser.parse_args()
    if (args.take is not None or args.list_takes) and any(
            arg is not None for arg in (args.language, args.part, args.seed)):
        parser.error("--take 和 --list-takes 不能和 --language、--part 或 --seed 混用。")
    if args.list_takes and (args.plan or args.output is not None):
        parser.error("--list-takes 不能和 --plan 或 --output 混用。")
    if args.plan and args.take is None:
        parser.error("--plan 需要 --take ID。")
    if not (args.plan or args.list_takes or args.output is not None):
        parser.error("生成 WAV 需要 --output。")
    try:
        if args.list_takes:
            response = take_ids()
        elif args.plan:
            response = take_plan(args.take)
        else:
            if args.take is not None:
                language, parts, seed = load_take(args.take)
            else:
                if args.language is None or not args.part:
                    parser.error("朗读新句需要 --language 和至少一个 --part。")
                language = args.language
                parts = [direct(text, language, style) for style, text in args.part]
                seed = 213 if args.seed is None else args.seed
            response = render(parts, language, args.output, seed=seed)
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
