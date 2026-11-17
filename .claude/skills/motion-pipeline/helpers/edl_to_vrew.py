#!/usr/bin/env python3
"""EDL + word-level transcript -> Vrew project (.vrew).

The .vrew format is unofficial (reverse-engineered from Vrew 3.x project files,
project.json "version": 14). A .vrew is a zip with:
  project.json                       — files / transcript / props / statistics
  media/uc-0010-simple-textbox.html  — default caption textbox web-asset

Model:
  files[]            one VIDEO_AUDIO entry per EDL source, referenced by absolute
                     path (not embedded). originalClips = the full, uncut transcript.
  transcript.scenes  the edited timeline. A clip = one caption line, made of words:
                       type 0 = spoken word, type 1 = silence, type 2 = clip-end marker.
                     A cut is simply words that are absent here (time jumps), so the
                     removed parts stay restorable inside Vrew.

Usage:
  python edl_to_vrew.py footage/edit/edl.json \
      --words s1=footage/edit/transcripts/clip1.json \
      -o footage/edit/timeline.vrew

Transcript JSON accepted: Scribe-style {"words":[{"type":"word","text","start","end"}]}
or a plain list of {"text","start","end"} (hyperframes transcribe output, edge-tts
WordBoundary dumps, ...). Without --words, a source's transcript is looked up at
sources[].words in the EDL, then <edl dir>/transcripts/<video stem>.json (video-use
convention). Sources with no transcript become silent clips (b-roll).

If the EDL has "subtitles" (timeline seconds), their text becomes the caption of the
clip they overlap; otherwise Vrew shows the transcript words as captions.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import string
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE_DIR = HERE / "vrew_template"
ID_CHARS = string.ascii_letters + string.digits + "_-"

W_WORD, W_SILENCE, W_END = 0, 1, 2
MIN_SILENCE = 0.05  # gaps shorter than this are merged into the previous word


def rid(n: int = 10) -> str:
    return "".join(random.choice(ID_CHARS) for _ in range(n))


def r2(x: float) -> float:
    return round(float(x), 3)


# ── input loading ────────────────────────────────────────────────────────────

def load_words(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("words") or data.get("segments") or []
    out = []
    for w in data:
        if w.get("type", "word") != "word":
            continue
        text = str(w.get("text", w.get("word", ""))).strip()
        if not text:
            continue
        s, e = float(w["start"]), float(w["end"])
        if e < s:
            s, e = e, s
        out.append({"text": text, "start": s, "end": e})
    out.sort(key=lambda w: w["start"])
    return out


def probe(path: str) -> dict:
    """ffprobe metadata in Vrew's videoMetaInfo shape."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
             "-show_streams", path],
            capture_output=True, text=True, encoding="utf-8", check=True)
        j = json.loads(r.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"! ffprobe failed for {path}: {e}", file=sys.stderr)
        return {}
    meta: dict = {"duration": r2(float(j.get("format", {}).get("duration", 0)))}
    for s in j.get("streams", []):
        if s.get("codec_type") == "video" and "videoInfo" not in meta:
            num, _, den = s.get("avg_frame_rate", "30/1").partition("/")
            fps = float(num) / float(den or 1) if float(den or 1) else 30.0
            meta["videoInfo"] = {"codec": s.get("codec_name", "h264"),
                                 "frameRate": round(fps, 3),
                                 "size": {"width": s.get("width"), "height": s.get("height")}}
        elif s.get("codec_type") == "audio" and "audioInfo" not in meta:
            meta["audioInfo"] = {"codec": s.get("codec_name", "aac"),
                                 "sampleRate": int(s.get("sample_rate", 48000))}
    meta["_size"] = int(j.get("format", {}).get("size", 0))
    return meta


# ── clip building ────────────────────────────────────────────────────────────

def mk_word(media_id: str, text: str, start: float, dur: float, wtype: int) -> dict:
    return {"id": rid(), "text": text, "startTime": r2(start), "duration": r2(max(dur, 0)),
            "aligned": wtype != W_END, "type": wtype,
            "originalDuration": r2(max(dur, 0)), "originalStartTime": r2(start),
            "truncatedWords": [], "autoControl": False, "mediaId": media_id,
            "audioIds": [], "assetIds": []}


def mk_clip(words: list[dict], caption: str = "") -> dict:
    return {"words": words, "captionMode": "TRANSCRIPT",
            "captions": [{"text": [{"insert": (caption + "\n") if caption else "\n"}]},
                         {"text": [{"insert": "\n"}]}],
            "assetIds": [], "dirty": {"blankDeleted": False, "caption": False, "video": False},
            "translationModified": {"result": False, "source": False},
            "id": rid(), "playbackRate": 1, "audioIds": []}


def group_lines(words: list[dict], max_chars: int, max_gap: float) -> list[list[dict]]:
    """Split words into caption lines: on long pauses, sentence ends, or length."""
    lines, cur, n = [], [], 0
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > max_gap or n + len(w["text"]) > max_chars):
            lines.append(cur); cur, n = [], 0
        cur.append(w); n += len(w["text"]) + 1
        if w["text"][-1:] in ".?!。…":
            lines.append(cur); cur, n = [], 0
    if cur:
        lines.append(cur)
    return lines


def build_clips(media_id: str, words: list[dict], t0: float, t1: float,
                max_chars: int, max_gap: float) -> list[dict]:
    """Clips covering [t0, t1] of a source: words + silences + end markers."""
    inside = [w for w in words if t0 <= (w["start"] + w["end"]) / 2 < t1]
    if not inside:
        return [mk_clip([mk_word(media_id, "", t0, t1 - t0, W_SILENCE),
                         mk_word(media_id, "", t1, 0, W_END)])]
    lines = group_lines(inside, max_chars, max_gap)
    clips, pos = [], t0
    for i, line in enumerate(lines):
        end_bound = t1 if i == len(lines) - 1 else lines[i + 1][0]["start"]
        ws = []
        for w in line:
            s = max(w["start"], pos)
            if s - pos >= MIN_SILENCE:
                ws.append(mk_word(media_id, "", pos, s - pos, W_SILENCE))
            elif s > pos and ws:  # tiny gap: stretch the previous word (Vrew keeps words contiguous)
                ws[-1]["duration"] = ws[-1]["originalDuration"] = r2(s - ws[-1]["startTime"])
            else:
                s = pos
            e = min(max(w["end"], s), end_bound)
            ws.append(mk_word(media_id, w["text"], s, e - s, W_WORD))
            pos = e
        if end_bound - pos > 0.01:  # trailing silence up to next line / cut end
            ws.append(mk_word(media_id, "", pos, end_bound - pos, W_SILENCE))
            pos = end_bound
        ws.append(mk_word(media_id, "", pos, 0, W_END))
        clips.append(mk_clip(ws))
    return clips


def clip_span(clip: dict) -> float:
    return sum(w["duration"] for w in clip["words"])


def apply_subtitles(clips: list[dict], subtitles: list[dict]) -> None:
    """Put EDL subtitle text (timeline seconds) on the clips they overlap most."""
    t, spans = 0.0, []
    for c in clips:
        d = clip_span(c); spans.append((t, t + d)); t += d
    for sub in subtitles:
        s, e = float(sub["start"]), float(sub["end"])
        best, ov = None, 0.0
        for i, (a, b) in enumerate(spans):
            o = min(b, e) - max(a, s)
            if o > ov:
                best, ov = i, o
        if best is None:
            continue
        ins = clips[best]["captions"][0]["text"][0]
        prev = ins["insert"].rstrip("\n")
        ins["insert"] = (prev + " " if prev else "") + sub["text"].strip() + "\n"


# ── project assembly ─────────────────────────────────────────────────────────

def build_project(edl: dict, words_by_src: dict[str, list[dict]],
                  max_chars: int, max_gap: float) -> dict:
    tpl = json.loads((TEMPLATE_DIR / "template.json").read_text(encoding="utf-8"))
    props = copy.deepcopy(tpl["props"])
    width, height = int(edl.get("width", 1920)), int(edl.get("height", 1080))
    props["videoSize"] = {"width": width, "height": height}
    props["initProjectVideoSize"] = {"width": width, "height": height}
    props["videoRatio"] = width / height
    props["analyzeDate"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    files, media_ids = [], {}
    for src in edl["sources"]:
        mid = str(uuid.uuid4())
        media_ids[src["id"]] = mid
        path = str(Path(src["path"]).resolve())
        meta = probe(path)
        size = meta.pop("_size", 0)
        if "duration" not in meta or not meta["duration"]:
            meta["duration"] = float(src.get("duration", 0))
        words = words_by_src.get(src["id"], [])
        original = build_clips(mid, words, 0.0, meta["duration"], max_chars, max_gap)
        files.append({"version": 5, "type": "VIDEO_AUDIO", "path": path, "mediaId": mid,
                      "originalClips": original,
                      "audioLanguage": edl.get("language", "ko") if words else "None",
                      "fileSize": size, "fileLocation": "LOCAL", "sourceOrigin": "USER",
                      "importMethod": "fileSystem", "videoMetaInfo": meta})
        props["mediaEffectMap"][mid] = copy.deepcopy(tpl["defaultMediaEffect"])

    clips = []
    for cut in edl["cuts"]:
        sid = cut["source_id"]
        clips += build_clips(media_ids[sid], words_by_src.get(sid, []),
                             float(cut["in"]), float(cut["out"]), max_chars, max_gap)
    if edl.get("subtitles"):
        apply_subtitles(clips, edl["subtitles"])

    now = datetime.now(timezone.utc)
    local = now.astimezone().isoformat(timespec="seconds")
    stats = copy.deepcopy(tpl["statistics"])
    ver = tpl["appVersion"]
    stats["saveInfo"] = {"created": {"version": ver, "date": local, "stage": "release"},
                         "updated": {"version": ver, "date": local, "stage": "release"},
                         "loadCount": 0, "saveCount": 0}
    return {"version": tpl["version"], "files": files,
            "transcript": {"scenes": [{"id": rid(), "clips": clips, "name": "",
                                       "dirty": {"video": False}}]},
            "props": props,
            "comment": f"{ver}\t{now.isoformat(timespec='milliseconds').replace('+00:00', 'Z')}",
            "projectId": str(uuid.uuid4()), "statistics": stats,
            "lastTTSSettings": tpl["lastTTSSettings"]}


def write_vrew(project: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("project.json", json.dumps(project, ensure_ascii=False, separators=(",", ":")))
        z.write(TEMPLATE_DIR / "uc-0010-simple-textbox.html", "media/uc-0010-simple-textbox.html")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # Korean Windows console is cp949
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description="EDL + word transcript -> Vrew project (.vrew)")
    ap.add_argument("edl", type=Path)
    ap.add_argument("--words", action="append", default=[], metavar="SRC_ID=PATH",
                    help="word-level transcript for an EDL source (repeatable)")
    ap.add_argument("-o", "--output", type=Path, default=None,
                    help="output .vrew (default: <edl dir>/timeline.vrew)")
    ap.add_argument("--max-chars", type=int, default=30, help="max characters per caption line")
    ap.add_argument("--max-gap", type=float, default=0.6, help="pause (s) that starts a new line")
    a = ap.parse_args()

    edl = json.loads(a.edl.read_text(encoding="utf-8"))
    words = {}
    for spec in a.words:
        sid, _, p = spec.partition("=")
        if not p:
            ap.error(f"--words expects SRC_ID=PATH, got {spec!r}")
        words[sid] = load_words(Path(p))
    unknown = set(words) - {s["id"] for s in edl["sources"]}
    if unknown:
        ap.error(f"--words source ids not in EDL: {sorted(unknown)}")
    # auto-discover: EDL sources[].words, then video-use's <edit>/transcripts/<stem>.json
    for src in edl["sources"]:
        if src["id"] in words:
            continue
        cands = [Path(src["words"])] if src.get("words") else []
        cands.append(a.edl.parent / "transcripts" / f"{Path(src['path']).stem}.json")
        for c in cands:
            if not c.is_absolute():
                c = a.edl.parent / c
            if c.is_file():
                words[src["id"]] = load_words(c)
                break
        else:
            print(f"! {src['id']}: no word transcript found — silent clip (b-roll)", file=sys.stderr)

    project = build_project(edl, words, a.max_chars, a.max_gap)
    out = a.output or a.edl.parent / "timeline.vrew"
    write_vrew(project, out)
    clips = project["transcript"]["scenes"][0]["clips"]
    total = sum(clip_span(c) for c in clips)
    print(f"✓ {out}  ({len(clips)} clips, {total:.2f}s, {len(project['files'])} source(s))")


if __name__ == "__main__":
    main()
