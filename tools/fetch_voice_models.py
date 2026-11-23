"""Install the exact public base weights consumed by the desktop voice engines."""
import argparse
from contextlib import contextmanager
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen


TOOLS = Path(__file__).resolve().parent
MIB = 1024 * 1024


def matches(path, entry):
    if not path.is_file() or path.stat().st_size != entry["bytes"]:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == entry["sha256"]


@contextmanager
def install_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".model-download.lock").open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                if handle.tell() == 0:
                    handle.write(b"1")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise OSError(f"Another installer is using {directory}.") from exc
        yield


def download(url, path, entry):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    size = entry["bytes"]
    offset = part.stat().st_size if part.exists() else 0
    if offset >= size:
        if matches(part, entry):
            part.replace(path)
            return
        part.unlink()
        offset = 0
    headers = {"User-Agent": "Atri-Desktop model installer", "Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    with urlopen(Request(url, headers=headers), timeout=120) as response:
        if response.status == 206:
            span = response.headers.get("Content-Range", "")
            if not span.startswith(f"bytes {offset}-") or not span.endswith(f"/{size}"):
                raise OSError(f"Unexpected byte range for {path.name}: {span}")
        elif response.status == 200:
            offset = 0
        else:
            raise OSError(f"Unexpected HTTP status {response.status} for {path.name}")
        last_report = time.monotonic()
        with part.open("ab" if offset else "wb") as out:
            while block := response.read(MIB):
                out.write(block)
                if time.monotonic() - last_report > 8:
                    print(f"  {out.tell() / MIB:.0f} / {size / MIB:.0f} MiB", flush=True)
                    last_report = time.monotonic()
    if part.stat().st_size < size:
        raise OSError(f"Incomplete {path.name}. Run the same command to resume.")
    if not matches(part, entry):
        part.unlink()
        raise OSError(f"SHA-256 or size mismatch for {path.name}. Run the same command to retry.")
    part.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", nargs="?", choices=("qwen", "rvc", "all"), default="qwen")
    parser.add_argument("--verify-only", action="store_true", help="check installed bytes offline")
    parser.add_argument("--root", type=Path, default=TOOLS.parent, help="target project root")
    args = parser.parse_args()
    groups = json.loads((TOOLS / "voice-models.lock.json").read_text(encoding="utf-8"))
    invalid = 0
    try:
        for name, group in groups.items():
            if args.group not in (name, "all"):
                continue
            directory = args.root / group["directory"]
            files = group["files"]
            if args.verify_only:
                for filename, entry in files.items():
                    ok = matches(directory / filename, entry)
                    print(f"{'OK' if ok else 'missing or changed'}  {name}/{filename}")
                    invalid += int(not ok)
                continue
            with install_lock(directory):
                for filename, entry in files.items():
                    path = directory / filename
                    if not matches(path, entry):
                        print(f"Fetching {name}/{filename} ({entry['bytes'] / MIB:.2f} MiB)", flush=True)
                        url = f"https://huggingface.co/{group['repo']}/resolve/{group['revision']}/{filename}"
                        download(url, path, entry)
                    print(f"OK  {name}/{filename}", flush=True)
    except (OSError, HTTPException, ValueError) as exc:
        print(f"Model installation failed: {exc}", file=sys.stderr)
        return 1
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
