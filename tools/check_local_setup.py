"""Read-only preflight for the diagram, Windows companion and optional integrations."""
import argparse
import ctypes
import importlib
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
HELD = "assets/props/held-diary/"
DESKTOP_MODULES = {"numpy": "numpy", "Pillow": "PIL.Image", "PySide6": "PySide6.QtWidgets"}
VOICE_MODULES = ("torch", "qwen_tts", "av", "numpy", "soundfile", "pyopenjtalk", "parselmouth")
MODEL = "tts/models/Qwen3-TTS-12Hz-1.7B-Base/"
HELD_FILES = (
    "manifest.json", "book/book.json", "actions.json", "small-review/book/manifest.json",
    "action-hand/turn/turn.json", "action-hand/write/performance.json", "page-turn/page.json",
    "pen-write/write.json", "write-interrupt/manifest.json",
)


def missing_files(paths):
    return [str(path) for path in paths if not (ROOT / path).is_file()]


def read_catalog(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8-sig"))


def python_modules(demo):
    modules = dict(DESKTOP_MODULES)
    if not demo:
        modules["SciPy"] = "scipy.ndimage"
    problems = []
    for package, module in modules.items():
        try:
            importlib.import_module(module)
        except (ImportError, OSError) as exc:
            problems.append(f"{package}: {exc}")
    return problems


def desktop_art():
    catalogs = (
        "data/staging.json", "assets/sprites/manifest.json", "assets/sprites/layers.json",
        "assets/voice/manifest.json", "assets/voice/all.json", "assets/props/manifest.json",
        *(HELD + item for item in HELD_FILES),
    )
    missing = missing_files(["assets/stage/official-atri.png", "assets/sprites/d1-p1-f18e483b6.webp"])
    for name in catalogs:
        if not (ROOT / name).is_file():
            missing.append(name)
            continue
        try:
            data = read_catalog(name)
            if name == "assets/sprites/manifest.json":
                white = [item["file"] for item in data if item["dress"] == "d1"]
                if not white:
                    missing.append(f"{name}: no d1 sprites")
                missing.extend(missing_files("assets/sprites/" + path for path in white))
            elif name == "assets/sprites/layers.json" and (ROOT / "assets/sprites/manifest.json").is_file():
                sprites = read_catalog("assets/sprites/manifest.json")
                required = {(data["poses"][item["pose"]], layer) for item in sprites
                            if item["dress"] == "d1" for layer in item.get("layerIds", [])}
                layers = {(name, row["layer_id"]): row["file"] for name, layerset in data["sets"].items()
                          for row in layerset["layers"]}
                missing.extend(missing_files("assets/sprites/" + layers[key] for key in sorted(required)))
            elif name == HELD + "small-review/book/manifest.json":
                paths = [data["layers"][key]["file"] for key in ("grip_back", "grip_front")]
                missing.extend(missing_files(HELD + "small-review/book/" + path for path in paths))
            elif name == HELD + "write-interrupt/manifest.json":
                paths = [data["files"][key] for key in ("lift", "playback")]
                missing.extend(missing_files(HELD + "write-interrupt/" + path for path in paths))
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError) as exc:
            missing.append(f"{name}: {exc}")
    ambience = "assets/ambience/manifest.json"
    try:
        scenes = read_catalog(ambience)["scenes"] if (ROOT / ambience).is_file() else {}
        for key, fallback in (("classroom", "assets/stage/classroom.webp"), ("coast", "assets/scenery/bg007.webp")):
            art = scenes.get(key, {}).get("file")
            if not art or not (ROOT / "assets/ambience" / art).is_file():
                missing.extend(missing_files([fallback]))
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError) as exc:
        missing.append(f"{ambience}: {exc}")
    return missing


def feature_art():
    missing = missing_files(("data/behaviors.json", "assets/scenery/bg002.webp",
                             "assets/motion/pose-studies/small-up-step/manifest.json",
                             "assets/motion/pose-studies/small-walk-review/manifest.json"))
    for stem in ("p1-s", "p2-s", "p3-s", "p1-body", "p2-body"):
        prefix = "assets/motion/kick/" + stem
        if all(not (ROOT / (prefix + suffix)).is_file() for suffix in (".npy", ".npz")):
            missing.append(f"{prefix}.npy or .npz")
    directory = "assets/props/diary-reading/"
    for name in (directory + "manifest.json", directory + "handwriting/manifest.json",
                 directory + "stickers/manifest.json", "assets/ambience/manifest.json"):
        if not (ROOT / name).is_file():
            missing.append(name)
            continue
        try:
            cat = read_catalog(name)
            if name.endswith("handwriting/manifest.json"):
                paths = [cat["file"]]
            elif name.endswith("stickers/manifest.json"):
                paths = [item["file"] for item in cat["assets"].values()]
            elif name.startswith("assets/ambience/"):
                paths = []
                for scene in cat["scenes"].values():
                    paths.append(scene["file"])
                    paths.extend(item["file"] for item in scene.get("variants", {}).values())
            else:
                paths = [cat[key]["file"] for key in ("paper", "originalNovel")]
            missing.extend(missing_files(Path(name).parent / path for path in paths))
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError) as exc:
            missing.append(f"{name}: {exc}")
    return missing


def voice_environment():
    name = "tts/.venv/Scripts/python.exe"
    if not (ROOT / name).is_file():
        return [name + " (create the separate Python 3.11 voice environment)"]
    probe = (
        "import importlib.util,sys\n"
        "for module in sys.argv[1:]:\n"
        " if importlib.util.find_spec(module) is None: print(module)\n"
    )
    result = subprocess.run([str(ROOT / name), "-I", "-c", probe, *VOICE_MODULES],
                            capture_output=True, text=True, errors="replace", timeout=30)
    if result.returncode:
        return [f"voice Python exited with status {result.returncode}"]
    return result.stdout.splitlines()


def voice_data():
    missing = missing_files(MODEL + item for item in (
        "config.json", "model.safetensors", "generation_config.json",
        "preprocessor_config.json", "tokenizer_config.json", "merges.txt", "vocab.json",
        "speech_tokenizer/config.json", "speech_tokenizer/model.safetensors",
        "speech_tokenizer/preprocessor_config.json",
    ))
    name = "assets/voice/all.json"
    if not (ROOT / name).is_file():
        return [*missing, name]
    from tts import engine, scene_read
    reference_ids = set(engine.JAPANESE_REFERENCES.values()) | set(scene_read.REFERENCES["ja"].values())
    try:
        entries = {item["id"]: item for item in read_catalog(name)}
        for reference in sorted(reference_ids):
            if reference not in entries:
                missing.append(f"{name}: {reference}")
            else:
                item = entries[reference]
                missing.extend(missing_files([item["file"]]))
                if not item.get("ja"):
                    missing.append(f"{name}: missing ja for {reference}")
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        missing.append(f"{name}: {exc}")
    return missing


def native_components():
    missing = missing_files(["desktop/observer/ScreenContextWorker.exe"])
    for filename, symbol in (("warp_rgba.dll", "warp_rgba"), ("kick_sampler.dll", "surface")):
        relative = "desktop/renderer/" + filename
        if not (ROOT / relative).is_file():
            missing.append(relative)
        elif os.name == "nt":
            try:
                getattr(ctypes.CDLL(str(ROOT / relative)), symbol)
            except (OSError, AttributeError) as exc:
                missing.append(f"{relative}: {exc}")
    return missing


def codex_cli():
    from desktop_mind import _codex_cli, MindError
    try:
        _codex_cli()
    except MindError:
        return ["Codex CLI executable (PATH or installed Windows app)"]
    return []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    for flag in ("demo", "desktop", "voice", "full"):
        group.add_argument("--" + flag, dest="mode", action="store_const", const=flag)
    parser.set_defaults(mode="full")
    mode = parser.parse_args().mode
    sys.path.insert(0, str(ROOT))
    errors = []
    def report(label, problems):
        problems = list(dict.fromkeys(problems))
        print(f"[{'MISSING' if problems else 'OK'}] {label}")
        for entry in problems[:24]:
            print(f"  - {entry}")
        if len(problems) > 24:
            print(f"  ... {len(problems) - 24} additional entries")
        errors.extend(problems)

    runtime = []
    if sys.version_info < (3, 11):
        runtime.append("Python 3.11 or newer")
    if mode != "demo" and (os.name != "nt" or struct.calcsize("P") != 8
                           or platform.machine().lower() not in ("amd64", "x86_64")):
        runtime.append("64-bit Python on Windows x64")
    report("platform and Python", runtime)
    if mode in ("demo", "desktop", "full"):
        report("desktop Python packages", python_modules(mode == "demo"))
    if mode in ("desktop", "full"):
        report("local desktop art and startup catalogs", desktop_art())
        report("diary, scenery, and motion data", feature_art())
        report("Windows OCR worker and native renderers", native_components())
    if mode in ("voice", "full"):
        try:
            voice = voice_environment()
        except (OSError, subprocess.SubprocessError) as exc:
            voice = [str(exc)]
        report("voice Python environment and packages", voice)
        report("local model and Japanese reference audio", voice_data())
    if mode == "full":
        report("optional original audio examples and script", missing_files(["tts/original_examples.json", "data/game.js"]))
        if sys.version_info >= (3, 11):
            report("Codex CLI discovery (login checked separately)", codex_cli())
    print("See docs/local-setup.md for installation and feature tests.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
