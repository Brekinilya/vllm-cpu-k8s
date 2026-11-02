import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from huggingface_hub import snapshot_download

MARKER = ".download-complete.json"
DEFAULT_PATTERNS = "*.json,*.safetensors,*.txt,*.model,*.tiktoken,*.jinja"


def log(message: str) -> None:
    print(f"[model-downloader] {message}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download model weights from Hugging Face into a local directory once.")
    parser.add_argument("--model-id", default=os.environ.get("MODEL_ID"))
    parser.add_argument("--revision", default=os.environ.get("MODEL_REVISION", "main"))
    parser.add_argument("--target", default=os.environ.get("MODEL_DIR"))
    parser.add_argument("--allow-patterns", default=os.environ.get("MODEL_ALLOW_PATTERNS", DEFAULT_PATTERNS))
    args = parser.parse_args()
    if not args.model_id or not args.target:
        parser.error("model id and target directory are required (MODEL_ID / MODEL_DIR)")
    return args


def read_marker(target: Path) -> dict | None:
    try:
        return json.loads((target / MARKER).read_text())
    except (OSError, ValueError):
        return None


def is_cached(target: Path, model_id: str, revision: str) -> bool:
    marker = read_marker(target)
    return bool(marker) and marker.get("model_id") == model_id and marker.get("revision") == revision


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def download(model_id: str, revision: str, target: Path, patterns: list[str]) -> None:
    staging = target.with_name(f".{target.name}.partial")
    staging.parent.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("HF_TOKEN") or None

    started = time.monotonic()
    snapshot_download(
        repo_id=model_id,
        revision=revision,
        local_dir=staging,
        allow_patterns=patterns,
        token=token,
    )
    shutil.rmtree(staging / ".cache", ignore_errors=True)

    marker = {
        "model_id": model_id,
        "revision": revision,
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (staging / MARKER).write_text(json.dumps(marker, indent=2))

    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)

    size_gib = directory_size(target) / 2**30
    log(f"downloaded {size_gib:.2f} GiB in {time.monotonic() - started:.0f}s")


def main() -> int:
    args = parse_args()
    target = Path(args.target)

    if is_cached(target, args.model_id, args.revision):
        log(f"cache hit: {args.model_id}@{args.revision} is already in {target}, network is not used")
        return 0

    log(f"cache miss: downloading {args.model_id}@{args.revision} into {target}")
    patterns = [pattern.strip() for pattern in args.allow_patterns.split(",") if pattern.strip()]
    download(args.model_id, args.revision, target, patterns)
    log("model is ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
