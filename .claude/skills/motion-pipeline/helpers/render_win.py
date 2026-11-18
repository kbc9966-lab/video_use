#!/usr/bin/env python3
"""Run video-use's render.py with Windows / Korean fixes, without touching the
upstream video-use checkout (video-use Hard Rule 12).

Same CLI as render.py:
  python render_win.py <render_edl.json> -o final.mp4 [--fps 30] [--preview] ...

Fixes applied:
  1. UTF-8 mode — render.py reads the EDL with the locale encoding (cp949 on
     Korean Windows) and crashes on Korean text. Re-execs with PYTHONUTF8=1.
  2. Subtitle path in the ffmpeg filtergraph — render.py embeds a Windows path
     with backslashes, which ffmpeg's filter parser eats as escapes. Separators
     become "/" while the "C\\:" drive-colon escape is kept.
  3. Subtitle style — render.py forces Helvetica (no Hangul glyphs). We pass
     only Fontname so an .ass file keeps its own styling (size, outline,
     PlayRes); plain .srt files get a Korean font.
     Override with RENDER_SUB_FONT (default "Malgun Gothic").

Video-use render EDL subtitles may point to an .ass (recommended, author it at
the output resolution, e.g. PlayResX 1080 / PlayResY 1920) or an .srt.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

if not sys.flags.utf8_mode:
    env = dict(os.environ, PYTHONUTF8="1")
    sys.exit(subprocess.call([sys.executable, *sys.argv], env=env))

helpers = Path(os.environ.get("VIDEO_USE_DIR", Path.home() / "Developer" / "video-use")) / "helpers"
sys.path.insert(0, str(helpers))
import render  # noqa: E402

render.SUB_FORCE_STYLE = f"Fontname={os.environ.get('RENDER_SUB_FONT', 'Malgun Gothic')}"

_real_run = render.subprocess.run


def _run(cmd, *a, **kw):
    if isinstance(cmd, list):
        cmd = [re.sub(r"\\(?!:)", "/", c) if isinstance(c, str) and "subtitles='" in c else c
               for c in cmd]
    return _real_run(cmd, *a, **kw)


render.subprocess.run = _run
sys.argv = ["render.py", *sys.argv[1:]]
render.main()
