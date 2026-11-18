#!/usr/bin/env python3
"""Image -> video with the Higgsfield API (official higgsfield-client SDK).

Credentials: HF_KEY="<key id>:<secret>" (or HF_API_KEY + HF_API_SECRET), read from the
environment or from the repo-root .env (git-ignored). Values are never printed.

Usage:
  python higgsfield_i2v.py image.png -o out.mp4 --prompt "..." \
      [--model bytedance/seedance-2.5/image-to-video] [--duration 5] \
      [--resolution 1080p] [--audio]

Writes out.mp4 plus out.json (request id, model, arguments, source video URL).
Each run spends Higgsfield credits.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]


def load_env() -> None:
    env = REPO / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description="Higgsfield image-to-video")
    ap.add_argument("image", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--model", default="bytedance/seedance-2.5/image-to-video")
    ap.add_argument("--duration", type=int, default=5)
    ap.add_argument("--resolution", default="1080p")
    ap.add_argument("--audio", action="store_true", help="let the model generate audio")
    a = ap.parse_args()

    load_env()
    if not (os.environ.get("HF_KEY") or (os.environ.get("HF_API_KEY") and os.environ.get("HF_API_SECRET"))):
        sys.exit("Higgsfield credentials missing: set HF_KEY=<id>:<secret> in .env")

    import higgsfield_client  # after env is loaded

    print(f"uploading {a.image.name} ...", flush=True)
    image_url = higgsfield_client.upload_file(str(a.image))

    args = {"image_url": image_url, "duration": a.duration,
            "resolution": a.resolution, "generate_audio": a.audio}
    if a.prompt:
        args["prompt"] = a.prompt
    print(f"submitting {a.model} ({a.duration}s, {a.resolution}) — this spends credits ...", flush=True)
    result = higgsfield_client.subscribe(a.model, arguments=args)

    status = result.get("status") if isinstance(result, dict) else None
    video_url = ((result or {}).get("video") or {}).get("url") if isinstance(result, dict) else None
    meta = {"model": a.model, "arguments": {k: v for k, v in args.items() if k != "image_url"},
            "status": status, "request_id": (result or {}).get("request_id"),
            "video_url": video_url}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if not video_url:
        sys.exit(f"no video in result (status={status}); see {a.output.with_suffix('.json')}")

    print("downloading result ...", flush=True)
    urllib.request.urlretrieve(video_url, a.output)
    print(f"✓ {a.output}")


if __name__ == "__main__":
    main()
