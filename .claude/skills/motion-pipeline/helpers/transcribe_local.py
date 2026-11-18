#!/usr/bin/env python3
"""Local word-level transcription with faster-whisper -> Scribe-compatible JSON.

Fallback for when `npx hyperframes transcribe` reports whisper_unavailable
(no whisper.cpp binary, e.g. on Windows without build tools). No API key.

Output: <edit_dir>/transcripts/<video_stem>.json  (video-use cache convention)
  {"language": "ko", "model": "...", "words": [
      {"type": "word", "text": "안녕하세요", "start": 0.12, "end": 0.84, "speaker_id": "speaker_0"},
      {"type": "spacing", "text": " ", "start": 0.84, "end": 1.20}, ...]}

Usage:
  python transcribe_local.py footage/clip.mp4                 # -> footage/edit/transcripts/clip.json
  python transcribe_local.py footage/clip.mp4 --model medium  # lighter model
  python transcribe_local.py footage/clip.mp4 --force         # ignore cache

Models (first run downloads from Hugging Face): large-v3-turbo (default, ~1.6GB,
good Korean accuracy, low RAM with int8), large-v3 (~3GB), medium, small.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description="faster-whisper -> Scribe-style word JSON")
    ap.add_argument("video", type=Path)
    ap.add_argument("--edit-dir", type=Path, default=None,
                    help="default: <video dir>/edit")
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--language", default="ko")
    ap.add_argument("--compute-type", default="int8", help="int8 (low RAM) | float32 | ...")
    ap.add_argument("--force", action="store_true", help="re-transcribe even if cached")
    a = ap.parse_args()

    edit = a.edit_dir or a.video.resolve().parent / "edit"
    out = edit / "transcripts" / f"{a.video.stem}.json"
    if out.exists() and not a.force:
        print(f"cached: {out}")
        return

    from faster_whisper import WhisperModel  # heavy import, after cache check

    # Decode with ffmpeg ourselves: faster-whisper's PyAV path breaks on newer `av`
    # releases (open() got an unexpected keyword argument 'metadata_errors').
    import subprocess
    import numpy as np
    pcm = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(a.video), "-vn",
         "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

    print(f"loading {a.model} ({a.compute_type}) ...", flush=True)
    model = WhisperModel(a.model, device="cpu", compute_type=a.compute_type)
    segments, info = model.transcribe(
        audio, language=a.language, word_timestamps=True,
        vad_filter=False,            # keep real pauses; cuts come from gaps
        condition_on_previous_text=False,
        initial_prompt=None,
    )

    words: list[dict] = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if not text:
                continue
            start, end = round(w.start, 3), round(w.end, 3)
            if words and start > words[-1]["end"]:
                words.append({"type": "spacing", "text": " ",
                              "start": words[-1]["end"], "end": start})
            words.append({"type": "word", "text": text, "start": start, "end": end,
                          "speaker_id": "speaker_0", "probability": round(w.probability, 3)})
        print(f"  [{seg.start:6.2f}-{seg.end:6.2f}] {seg.text.strip()}", flush=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"language": info.language, "model": a.model,
                               "duration": round(info.duration, 3), "words": words},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(1 for w in words if w["type"] == "word")
    print(f"✓ {out}  ({n} words, {info.duration:.1f}s)")


if __name__ == "__main__":
    main()
