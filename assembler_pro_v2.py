"""
90SecondFinance Pro Assembler v6 — keyword-cache + organized assets
- Uses your existing yt-automation folders:
  audio, queues, keywords_cache, assets/pexels_videos, assets/pexels_images, music, output, done_queues, fonts, caption_cache
- Reads latest queue*.json from queues/
- For each job, loads matching keywords_cache/<filename>.json
- Uses visual_plan keywords in order so visuals follow the narration
- Downloads/caches Pexels videos into assets/pexels_videos and images into assets/pexels_images
- Never reuses the same asset inside the same output video
- Uses 3–5 second visual segments
- No grading, no hook card, no progress bar, no black overlay
- Soft professional transitions: crossfade + gentle fade
- Images get subtle Ken Burns zoom/drift
- Captions: Hormozi style, Montserrat Black Italic, no background box
- Caption clipping fixed with wide safe canvas + auto font shrink

Run:
  cd "G:\\My Drive\\yt-automation"
  python assembler_finance_pro_v5_keywords.py
"""

import os
import sys
import json
import time
import math
import random
import hashlib
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

# ─── Dependency install/check ────────────────────────────────────────────────
try:
    import requests
    import numpy as np
    import whisper
    from PIL import Image, ImageDraw, ImageFont
    if not hasattr(Image, "ANTIALIAS"):
        Image.ANTIALIAS = Image.Resampling.LANCZOS
    from moviepy.editor import (
        VideoFileClip, AudioFileClip, ImageClip, CompositeVideoClip,
        CompositeAudioClip, concatenate_videoclips, concatenate_audioclips,
        ColorClip
    )
    from moviepy.video.fx.all import fadein, fadeout
except ImportError:
    print("Installing dependencies...")
    subprocess.run([
        sys.executable, "-m", "pip", "install",
        "moviepy==1.0.3", "openai-whisper", "pillow", "numpy", "requests"
    ], check=True)
    import requests
    import numpy as np
    import whisper
    from PIL import Image, ImageDraw, ImageFont
    if not hasattr(Image, "ANTIALIAS"):
        Image.ANTIALIAS = Image.Resampling.LANCZOS
    from moviepy.editor import (
        VideoFileClip, AudioFileClip, ImageClip, CompositeVideoClip,
        CompositeAudioClip, concatenate_videoclips, concatenate_audioclips,
        ColorClip
    )
    from moviepy.video.fx.all import fadein, fadeout

# ─── Paths driven by env vars — works locally (Windows) and on GHA (Linux) ───
# On GHA:   ASSEMBLER_BASE_DIR=/tmp/assembler-work  (set by run_job.py)
# Locally:  leave unset → falls back to your Google Drive path
BASE_DIR          = Path(os.getenv("ASSEMBLER_BASE_DIR", r"G:\My Drive\yt-automation"))
AUDIO_DIR         = BASE_DIR / "audio"
QUEUES_DIR        = BASE_DIR / "queues"
KEYWORDS_DIR      = BASE_DIR / "keywords_cache"
ASSETS_DIR        = BASE_DIR / "assets"
PEXELS_VIDEOS_DIR = ASSETS_DIR / "pexels_videos"
PEXELS_IMAGES_DIR = ASSETS_DIR / "pexels_images"
MUSIC_DIR         = BASE_DIR / "music"
OUTPUT_DIR        = BASE_DIR / "output"
DONE_QUEUES_DIR   = BASE_DIR / "done_queues"
FONTS_DIR         = Path(os.getenv("ASSEMBLER_FONTS_DIR", str(BASE_DIR / "fonts")))
CAPTION_CACHE_DIR = BASE_DIR / "caption_cache"
PEXELS_KEY_FILE   = BASE_DIR / "pexels_api_key.txt"

for d in [AUDIO_DIR, QUEUES_DIR, KEYWORDS_DIR, ASSETS_DIR, PEXELS_VIDEOS_DIR, PEXELS_IMAGES_DIR, MUSIC_DIR, OUTPUT_DIR, DONE_QUEUES_DIR, FONTS_DIR, CAPTION_CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── Settings ────────────────────────────────────────────────────────────────
SHORTS_SIZE = (1080, 1920)
FPS = 30

MIN_SEGMENT = 3.0
MAX_SEGMENT = 5.0
TRANSITION = 0.32

# Captions: safe from side clipping
CAPTION_Y = int(SHORTS_SIZE[1] * 0.66)          # slightly below center
CAPTION_SAFE_WIDTH = int(SHORTS_SIZE[0] * 0.82) # keep away from sides
CAPTION_CANVAS_W = int(SHORTS_SIZE[0] * 0.94)   # extra canvas prevents italic clipping
CAPTION_FONT_SIZE = 82
CAPTION_MIN_FONT_SIZE = 58
CAPTION_STROKE = 7
CAPTION_WORD_GAP = 20
CAPTION_LINE_GAP = 6

CAPTION_WHITE = (255, 255, 255, 255)
CAPTION_YELLOW = (255, 214, 10, 255)
CAPTION_STROKE_COLOR = (0, 0, 0, 255)

WATERMARK_TEXT = "@90SecondFinance"
MUSIC_VOLUME = 0.01
VOICE_VOLUME = 1.0

RUN_ONCE = True

# ─── Utility ────────────────────────────────────────────────────────────────
def read_pexels_key() -> str:
    env_key = os.getenv("PEXELS_API_KEY", "").strip()
    if env_key:
        return env_key
    if PEXELS_KEY_FILE.exists():
        return PEXELS_KEY_FILE.read_text(encoding="utf-8").strip()
    return ""

PEXELS_API_KEY = read_pexels_key()


def hash_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()[:12]


def safe_slug(text: str, limit: int = 70) -> str:
    s = "".join(c.lower() if c.isalnum() else "-" for c in str(text or ""))
    s = "-".join([x for x in s.split("-") if x])
    return s[:limit] or "asset"


def clean_keyword(text: str) -> str:
    text = str(text or "").strip()
    text = text.replace("#", "").replace("@", "")
    return " ".join(text.split())[:80]


def download_file(url: str, path: Path, timeout: int = 90) -> bool:
    try:
        if path.exists() and path.stat().st_size > 10000:
            return True
        r = requests.get(url, stream=True, timeout=timeout)
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
        return path.exists() and path.stat().st_size > 10000
    except Exception as e:
        print(f"  [download failed] {url[:70]}... {e}")
        return False

# ─── Queue + keyword cache ───────────────────────────────────────────────────
def latest_queue_file() -> Optional[Path]:
    files = list(QUEUES_DIR.glob("queue*.json")) + list(BASE_DIR.glob("queue*.json"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def load_queue(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("jobs", []) or data.get("queue", []) or []
    return []


def save_queue(path: Path, jobs: List[Dict[str, Any]]) -> None:
    path.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")


def load_keyword_cache(job: Dict[str, Any]) -> Dict[str, Any]:
    """Loads keywords_cache/<filename>.json if it exists."""
    filename = str(job.get("filename") or "").strip()
    candidates = []
    if filename:
        candidates.append(KEYWORDS_DIR / f"{filename}.json")
        candidates.append(KEYWORDS_DIR / f"{Path(filename).stem}.json")

    # fallback if n8n stored a custom filename in queue
    for k in ["keywordsFileName", "keywordFileName", "visualPlanFileName"]:
        if job.get(k):
            candidates.append(KEYWORDS_DIR / str(job[k]))

    for p in candidates:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  [keyword cache read failed] {p}: {e}")

    return {}


def fallback_visual_plan(job: Dict[str, Any], duration: float) -> List[Dict[str, Any]]:
    kws = job.get("search_keywords") or []
    if isinstance(kws, str):
        kws = [x.strip() for x in kws.split(",") if x.strip()]

    title = job.get("title", "")
    main_entity = job.get("main_entity", "")
    base = [main_entity, title] + list(kws)

    fallback = [
        "personal finance money",
        "budget planning",
        "stock market chart",
        "banking app phone",
        "saving money cash",
        "credit card payment",
        "financial planning laptop",
        "business person office",
        "calculator money",
        "investment graph",
        "debt bills table",
        "wallet cash",
    ]

    keywords = []
    for q in base + fallback:
        q = clean_keyword(q)
        if q and q.lower() not in [x.lower() for x in keywords]:
            keywords.append(q)

    plan = []
    t = 0.0
    i = 0
    while t < duration - 0.2:
        seg = random.uniform(MIN_SEGMENT, MAX_SEGMENT)
        end = min(duration, t + seg)
        plan.append({"start": round(t, 2), "end": round(end, 2), "keyword": keywords[i % len(keywords)]})
        t = end
        i += 1
    return plan


def normalize_visual_plan(job: Dict[str, Any], duration: float) -> List[Dict[str, Any]]:
    cache = load_keyword_cache(job)
    raw = cache.get("visual_plan") or job.get("visual_plan") or []

    if not isinstance(raw, list) or not raw:
        return fallback_visual_plan(job, duration)

    plan = []
    t = 0.0
    seen_keywords = set()

    for seg in raw:
        if not isinstance(seg, dict):
            continue
        keyword = clean_keyword(seg.get("keyword") or seg.get("search") or seg.get("query") or "")
        if not keyword:
            continue

        # Don't repeat same keyword inside one video unless we run out.
        keyword_key = keyword.lower()
        if keyword_key in seen_keywords:
            continue
        seen_keywords.add(keyword_key)

        raw_dur = None
        try:
            raw_dur = float(seg.get("end", 0)) - float(seg.get("start", 0))
        except Exception:
            raw_dur = None
        dur = raw_dur if raw_dur and raw_dur > 0 else random.uniform(MIN_SEGMENT, MAX_SEGMENT)
        dur = max(MIN_SEGMENT, min(MAX_SEGMENT, dur))

        end = min(duration, t + dur)
        if end - t < 1.0:
            break

        plan.append({"start": round(t, 2), "end": round(end, 2), "keyword": keyword})
        t = end
        if t >= duration - 0.2:
            break

    # If plan is too short, extend with fallback keywords.
    if t < duration - 0.2:
        fallback = fallback_visual_plan(job, duration)
        for seg in fallback:
            if t >= duration - 0.2:
                break
            keyword = clean_keyword(seg.get("keyword", ""))
            if not keyword or keyword.lower() in seen_keywords:
                continue
            dur = random.uniform(MIN_SEGMENT, MAX_SEGMENT)
            end = min(duration, t + dur)
            if end - t < 1.0:
                break
            plan.append({"start": round(t, 2), "end": round(end, 2), "keyword": keyword})
            seen_keywords.add(keyword.lower())
            t = end

    return plan

# ─── Pexels search/cache in assets/ only ─────────────────────────────────────
def pexels_search_videos(query: str, per_page: int = 10) -> List[Dict[str, Any]]:
    if not PEXELS_API_KEY:
        return []
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": per_page, "orientation": "portrait"},
            timeout=200,
        )
        r.raise_for_status()
        return r.json().get("videos", []) or []
    except Exception as e:
        print(f"  [Pexels video search failed] {query}: {e}")
        return []


def pexels_search_images(query: str, per_page: int = 10) -> List[Dict[str, Any]]:
    if not PEXELS_API_KEY:
        return []
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": per_page, "orientation": "portrait"},
            timeout=200,
        )
        r.raise_for_status()
        return r.json().get("photos", []) or []
    except Exception as e:
        print(f"  [Pexels image search failed] {query}: {e}")
        return []


def best_video_url(video: Dict[str, Any]) -> Optional[str]:
    files = video.get("video_files", []) or []
    if not files:
        return None
    files = sorted(files, key=lambda f: (
        abs((f.get("width") or 1080) - 1080),
        -int(f.get("height") or 0),
    ))
    return files[0].get("link")


def image_url(photo: Dict[str, Any]) -> Optional[str]:
    src = photo.get("src", {}) or {}
    return src.get("large2x") or src.get("portrait") or src.get("large") or src.get("original")


def cached_assets_for_keyword(keyword: str) -> List[Dict[str, Any]]:
    """Find already cached assets in assets/pexels_videos and assets/pexels_images."""
    slug = safe_slug(keyword, 45)
    found = []

    for folder, kind in [(PEXELS_VIDEOS_DIR, "video"), (PEXELS_IMAGES_DIR, "image")]:
        for p in folder.glob(f"*{slug}*"):
            if not p.is_file():
                continue
            if kind == "video" and p.suffix.lower() not in [".mp4", ".mov", ".m4v"]:
                continue
            if kind == "image" and p.suffix.lower() not in [".jpg", ".jpeg", ".png", ".webp"]:
                continue
            found.append({"kind": kind, "path": p, "query": keyword, "source": "cache"})

    random.shuffle(found)
    return found


def fetch_asset_for_keyword(keyword: str, used_paths: set, prefer_video: bool = True) -> Optional[Dict[str, Any]]:
    """Returns one unique local asset for a keyword. Uses cache first, then Pexels."""
    keyword = clean_keyword(keyword)
    if not keyword:
        return None

    # Cache first.
    cached = cached_assets_for_keyword(keyword)
    for asset in cached:
        if str(asset["path"]) not in used_paths:
            used_paths.add(str(asset["path"]))
            return asset

    candidates = []
    videos = pexels_search_videos(keyword, per_page=8)
    images = pexels_search_images(keyword, per_page=8)

    for v in videos:
        url = best_video_url(v)
        if url:
            candidates.append(("video", url))
    for im in images:
        url = image_url(im)
        if url:
            candidates.append(("image", url))

    if prefer_video:
        candidates.sort(key=lambda x: 0 if x[0] == "video" else 1)
    else:
        candidates.sort(key=lambda x: 0 if x[0] == "image" else 1)

    # Add some randomness while still respecting preference.
    if len(candidates) > 3:
        top = candidates[:4]
        rest = candidates[4:]
        random.shuffle(top)
        candidates = top + rest

    slug = safe_slug(keyword, 45)
    for kind, url in candidates:
        ext = ".mp4" if kind == "video" else ".jpg"
        folder = PEXELS_VIDEOS_DIR if kind == "video" else PEXELS_IMAGES_DIR
        path = folder / f"{kind}_{slug}_{hash_text(url)}{ext}"
        if str(path) in used_paths:
            continue
        if download_file(url, path):
            used_paths.add(str(path))
            return {"kind": kind, "path": path, "query": keyword, "source": "pexels", "url": url}

    return None

# ─── Audio ───────────────────────────────────────────────────────────────────
def resolve_audio_path(job: Dict[str, Any]) -> Path:
    audio = str(job.get("audioFile") or job.get("localAudioFile") or "")
    if audio:
        p = Path(audio)
        if p.exists():
            return p
        p2 = AUDIO_DIR / p.name
        if p2.exists():
            return p2
    return AUDIO_DIR / f"{job.get('filename', '')}.mp3"


def build_audio_mix(voice_path: Path, duration: float):
    voice = AudioFileClip(str(voice_path)).volumex(VOICE_VOLUME)
    voice = voice.audio_fadein(0.04).audio_fadeout(0.25)

    music_files = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav")) + list(MUSIC_DIR.glob("*.m4a"))
    if not music_files:
        return voice

    music_path = random.choice(music_files)
    music = AudioFileClip(str(music_path)).volumex(MUSIC_VOLUME)

    if music.duration < duration:
        loops = int(duration / music.duration) + 1
        music = concatenate_audioclips([music] * loops)

    start = random.uniform(0, max(0, music.duration - duration - 0.5)) if music.duration > duration + 0.5 else 0
    music = music.subclip(start, start + duration).audio_fadein(1.1).audio_fadeout(1.5)
    return CompositeAudioClip([music, voice])

# ─── Visual clips ────────────────────────────────────────────────────────────
def fit_video_clip(path: Path, dur: float):
    clip = VideoFileClip(str(path), audio=False)
    if clip.duration <= 0.5:
        raise ValueError("video too short")

    start_max = max(0, clip.duration - dur - 0.1)
    start = random.uniform(0, start_max) if start_max > 0 else 0
    clip = clip.subclip(start, min(start + dur, clip.duration))

    target_w, target_h = SHORTS_SIZE
    w, h = clip.size
    scale = max(target_w / w, target_h / h)
    clip = clip.resize(scale)
    w2, h2 = clip.size
    x1 = max(0, (w2 - target_w) / 2)
    y1 = max(0, (h2 - target_h) / 2)
    clip = clip.crop(x1=x1, y1=y1, x2=x1 + target_w, y2=y1 + target_h)

    # No grading. Only subtle scale motion.
    zoom_amount = random.uniform(0.004, 0.012)
    clip = clip.resize(lambda t: 1 + zoom_amount * (t / max(dur, 0.1)))
    clip = clip.crop(x_center=target_w / 2, y_center=target_h / 2, width=target_w, height=target_h)

    return clip.set_duration(dur)


def fit_image_clip(path: Path, dur: float):
    target_w, target_h = SHORTS_SIZE
    img = Image.open(path).convert("RGB")
    w, h = img.size
    scale = max(target_w / w, target_h / h) * 1.15  # extra canvas for drift
    img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    arr = np.array(img)

    base = ImageClip(arr).set_duration(dur)

    # Ken Burns: slow zoom + drift
    zoom_start = random.uniform(1.00, 1.018)
    zoom_end = random.uniform(1.055, 1.085)
    base = base.resize(lambda t: zoom_start + (zoom_end - zoom_start) * (t / max(dur, 0.1)))

    max_dx = min(90, max(0, base.w - target_w) // 4)
    max_dy = min(120, max(0, base.h - target_h) // 4)

    x0 = -((base.w - target_w) / 2) + random.uniform(-max_dx, max_dx)
    y0 = -((base.h - target_h) / 2) + random.uniform(-max_dy, max_dy)
    x1 = -((base.w - target_w) / 2) + random.uniform(-max_dx, max_dx)
    y1 = -((base.h - target_h) / 2) + random.uniform(-max_dy, max_dy)

    moving = base.set_position(lambda t: (
        x0 + (x1 - x0) * (t / max(dur, 0.1)),
        y0 + (y1 - y0) * (t / max(dur, 0.1)),
    ))

    bg = ColorClip(SHORTS_SIZE, color=(0, 0, 0)).set_duration(dur)
    return CompositeVideoClip([bg, moving], size=SHORTS_SIZE).set_duration(dur)


def fallback_clip(dur: float):
    # Minimal neutral fallback; should rarely appear.
    return ColorClip(SHORTS_SIZE, color=(18, 18, 20)).set_duration(dur)


def make_visual_sequence_from_plan(job: Dict[str, Any], plan: List[Dict[str, Any]], duration: float):
    clips = []
    used_paths = set()

    # If no plan, create one.
    if not plan:
        plan = fallback_visual_plan(job, duration)

    elapsed = 0.0
    for idx, seg in enumerate(plan):
        if elapsed >= duration - 0.2:
            break

        keyword = clean_keyword(seg.get("keyword", ""))
        seg_dur = float(seg.get("end", 0)) - float(seg.get("start", 0))
        if seg_dur <= 0:
            seg_dur = random.uniform(MIN_SEGMENT, MAX_SEGMENT)
        seg_dur = max(MIN_SEGMENT, min(MAX_SEGMENT, seg_dur))
        seg_dur = min(seg_dur, duration - elapsed + (TRANSITION if clips else 0))
        if seg_dur < 1.0:
            break

        # Mix stills and videos. Images often look better for finance with Ken Burns.
        prefer_video = (idx % 3 != 1)
        asset = fetch_asset_for_keyword(keyword, used_paths, prefer_video=prefer_video)

        try:
            if asset and asset["kind"] == "video":
                clip = fit_video_clip(asset["path"], seg_dur)
            elif asset and asset["kind"] == "image":
                clip = fit_image_clip(asset["path"], seg_dur)
            else:
                clip = fallback_clip(seg_dur)
        except Exception as e:
            print(f"  [visual asset failed] {keyword}: {e}")
            clip = fallback_clip(seg_dur)

        # Professional transition stack: soft crossfade + small fade.
        # No flashy distortions, no hard cuts.
        if clips:
            clip = clip.crossfadein(TRANSITION)
        clip = clip.fx(fadein, 0.06).fx(fadeout, 0.10)

        clips.append(clip)
        elapsed += seg_dur - (TRANSITION if len(clips) > 1 else 0)

    # Fill any missing tail without repeating assets if possible.
    while elapsed < duration - 0.2:
        seg_dur = min(random.uniform(MIN_SEGMENT, MAX_SEGMENT), duration - elapsed + (TRANSITION if clips else 0))
        clip = fallback_clip(seg_dur)
        if clips:
            clip = clip.crossfadein(TRANSITION)
        clip = clip.fx(fadein, 0.06).fx(fadeout, 0.10)
        clips.append(clip)
        elapsed += seg_dur - (TRANSITION if len(clips) > 1 else 0)

    if not clips:
        return fallback_clip(duration)

    seq = concatenate_videoclips(clips, method="compose", padding=-TRANSITION)
    if seq.duration < duration:
        seq = concatenate_videoclips([seq, fallback_clip(duration - seq.duration)], method="compose")
    return seq.subclip(0, duration)

# ─── Captions ────────────────────────────────────────────────────────────────
def load_caption_font(size: int):
    candidates = [
        # GHA: fonts/ folder committed to repo (ASSEMBLER_FONTS_DIR points here)
        FONTS_DIR / "Montserrat-BlackItalic.ttf",
        FONTS_DIR / "Montserrat-Black-Italic.ttf",
        FONTS_DIR / "MontserratBlackItalic.ttf",
        # Linux system fallback
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"),
        # Windows local dev
        Path(r"C:\Windows\Fonts\Montserrat-BlackItalic.ttf"),
        Path(r"C:\Windows\Fonts\arialbi.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
    ]
    for p in candidates:
        try:
            if p.exists():
                return ImageFont.truetype(str(p), size=size)
        except Exception:
            pass
    try:
        return ImageFont.truetype("DejaVuSans-BoldOblique.ttf", size=size)
    except Exception:
        return ImageFont.load_default()


def transcribe_words(audio_path: Path) -> List[Dict[str, Any]]:
    try:
        model = whisper.load_model("small")
        result = model.transcribe(str(audio_path), word_timestamps=True, fp16=False)
        words = []
        for seg in result.get("segments", []):
            for w in seg.get("words", []) or []:
                text = str(w.get("word", "")).strip()
                if text:
                    words.append({
                        "word": text,
                        "start": float(w.get("start", 0)),
                        "end": float(w.get("end", 0)),
                    })
        return words
    except Exception as e:
        print(f"  [Whisper failed] {e}")
        return []


def group_words(words: List[Dict[str, Any]], duration: float) -> List[Dict[str, Any]]:
    groups = []
    cur = []
    start = None

    for w in words:
        if w["start"] >= duration:
            break
        if start is None:
            start = w["start"]
        cur.append(w)

        group_text = " ".join(x["word"] for x in cur)
        too_many_words = len(cur) >= random.choice([3, 4, 4, 5])
        too_long_time = (w["end"] - start) >= 1.25
        too_long_chars = len(group_text) >= 28
        punctuation = w["word"].endswith((".", ",", "!", "?")) and len(cur) >= 2

        if too_many_words or too_long_time or too_long_chars or punctuation:
            groups.append({
                "text": group_text,
                "start": max(0, start),
                "end": min(duration - 0.05, max(w["end"], start + 0.25)),
            })
            cur = []
            start = None

    if cur and start is not None:
        groups.append({
            "text": " ".join(x["word"] for x in cur),
            "start": max(0, start),
            "end": min(duration - 0.05, max(cur[-1]["end"], start + 0.25)),
        })

    return [g for g in groups if g["end"] > g["start"]]


def split_caption_lines(words: List[str]) -> List[List[str]]:
    # Max 2 lines, balanced; shorter lines prevent side clipping.
    if len(words) <= 3:
        return [words]
    mid = math.ceil(len(words) / 2)
    return [words[:mid], words[mid:]]


def measure_line(draw: ImageDraw.ImageDraw, line: List[str], font, stroke: int) -> int:
    widths = []
    for word in line:
        bbox = draw.textbbox((0, 0), word, font=font, stroke_width=stroke)
        widths.append(bbox[2] - bbox[0])
    return sum(widths) + max(0, len(line) - 1) * CAPTION_WORD_GAP


def render_caption_image(text: str) -> Path:
    raw_words = [w.strip().upper() for w in text.replace("\n", " ").split() if w.strip()]
    if not raw_words:
        raw_words = [""]

    # Highlight first number/money/percent word, else last word.
    highlight_idx = len(raw_words) - 1
    for i, w in enumerate(raw_words):
        if any(ch.isdigit() for ch in w) or "$" in w or "%" in w:
            highlight_idx = i
            break

    # Auto-shrink font until every line fits safe width.
    font_size = CAPTION_FONT_SIZE
    stroke = CAPTION_STROKE
    dummy = Image.new("RGBA", (CAPTION_CANVAS_W, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(dummy)

    while True:
        font = load_caption_font(font_size)
        lines = split_caption_lines(raw_words)
        max_line_w = max(measure_line(draw, line, font, stroke) for line in lines)
        if max_line_w <= CAPTION_SAFE_WIDTH or font_size <= CAPTION_MIN_FONT_SIZE:
            break
        font_size -= 4
        stroke = max(4, int(stroke * 0.92))

    # Extra padding prevents italic overhang clipping.
    pad_x = 90
    pad_y = 44
    line_h = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), "TEST", font=font, stroke_width=stroke)
        line_h = max(line_h, bbox[3] - bbox[1] + 12)

    canvas_w = CAPTION_CANVAS_W
    canvas_h = pad_y * 2 + len(lines) * line_h + max(0, len(lines) - 1) * CAPTION_LINE_GAP
    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    global_idx = 0
    y = pad_y
    for line in lines:
        line_w = measure_line(draw, line, font, stroke)
        x = (canvas_w - line_w) // 2

        for word in line:
            bbox = draw.textbbox((0, 0), word, font=font, stroke_width=stroke)
            ww = bbox[2] - bbox[0]
            color = CAPTION_YELLOW if global_idx == highlight_idx else CAPTION_WHITE
            # Draw with positive padded coordinates; bbox negative values won't clip.
            draw.text(
                (x, y),
                word,
                font=font,
                fill=color,
                stroke_width=stroke,
                stroke_fill=CAPTION_STROKE_COLOR,
            )
            x += ww + CAPTION_WORD_GAP
            global_idx += 1
        y += line_h + CAPTION_LINE_GAP

    path = CAPTION_CACHE_DIR / f"caption_{hash_text(text + str(font_size))}.png"
    img.save(path)
    return path


def caption_clip(group: Dict[str, Any]):
    path = render_caption_image(group["text"])
    dur = max(0.15, group["end"] - group["start"])
    clip = ImageClip(str(path)).set_start(group["start"]).set_duration(dur)
    clip = clip.set_position(("center", CAPTION_Y))
    # Pop animation without causing clipping.
    clip = clip.resize(lambda t: 0.965 + 0.035 * min(1, t / 0.10))
    return clip


def make_captions(audio_path: Path, duration: float):
    words = transcribe_words(audio_path)
    groups = group_words(words, duration)
    return [caption_clip(g) for g in groups]


def make_watermark(duration: float):
    font = load_caption_font(34)
    img = Image.new("RGBA", (560, 82), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text(
        (0, 12),
        WATERMARK_TEXT,
        font=font,
        fill=(255, 255, 255, 150),
        stroke_width=2,
        stroke_fill=(0, 0, 0, 120),
    )
    path = CAPTION_CACHE_DIR / "watermark_finance.png"
    img.save(path)
    return ImageClip(str(path)).set_duration(duration).set_position((36, 36))

# ─── Build video ─────────────────────────────────────────────────────────────
def build_video(job: Dict[str, Any]) -> Optional[Path]:
    filename = str(job.get("filename") or f"render_{int(time.time())}")
    audio_path = resolve_audio_path(job)
    output_path = OUTPUT_DIR / f"{filename}.mp4"

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Building: {filename}")
    print(f"  Title: {job.get('title', '')}")

    if not audio_path.exists():
        print(f"  [SKIP] Audio not found: {audio_path}")
        return None

    voice = AudioFileClip(str(audio_path))
    duration = max(0.1, voice.duration - 0.08)  # avoid reading past MP3 end
    voice.close()

    plan = normalize_visual_plan(job, duration)
    print(f"  Visual plan segments: {len(plan)}")
    if plan:
        print("  First keywords:", ", ".join([p["keyword"] for p in plan[:5]]))

    visual = make_visual_sequence_from_plan(job, plan, duration)
    captions = make_captions(audio_path, duration)
    watermark = make_watermark(duration)
    audio_mix = build_audio_mix(audio_path, duration)

    final = CompositeVideoClip([visual, watermark] + captions, size=SHORTS_SIZE).set_audio(audio_mix).set_duration(duration)

    final.write_videofile(
        str(output_path),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="veryfast",
        threads=4,
        ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        logger=None,
    )

    meta_path = OUTPUT_DIR / f"{filename}.json"
    meta_path.write_text(json.dumps({
        "title": job.get("title", filename),
        "description": f"{job.get('body', '')}\n\n{' '.join(job.get('hashtags', []))}",
        "hashtags": job.get("hashtags", []),
        "filename": str(output_path),
        "keyword_cache": str(KEYWORDS_DIR / f"{filename}.json"),
        "visual_plan_used": plan,
        "built_at": datetime.now().isoformat(),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    try:
        final.close()
        visual.close()
        audio_mix.close()
    except Exception:
        pass

    print(f"  [DONE] → {output_path}")
    return output_path


def process_queue_once() -> None:
    qfile = latest_queue_file()
    if not qfile:
        print("No queue*.json found in queues folder.")
        return

    print(f"Using queue: {qfile}")
    jobs = load_queue(qfile)
    pending = [j for j in jobs if j.get("status") == "ready_for_assembly"]

    if not pending:
        print("No ready_for_assembly jobs found.")
        return

    print(f"Found {len(pending)} job(s).")
    for job in pending:
        try:
            result = build_video(job)
            job["status"] = "done" if result else "error"
            job["built_at"] = datetime.now().isoformat()
            if result:
                job["outputFile"] = str(result)
        except Exception as e:
            print(f"  [ERROR] {job.get('filename')}: {e}")
            job["status"] = "error"
            job["error"] = str(e)

    save_queue(qfile, jobs)

    # Move completed queue out of queues/ so it will not be picked again next run.
    if all(j.get("status") != "ready_for_assembly" for j in jobs):
        try:
            destination = DONE_QUEUES_DIR / qfile.name
            if destination.exists():
                destination = DONE_QUEUES_DIR / f"{qfile.stem}_processed_{datetime.now().strftime('%Y%m%d_%H%M%S')}{qfile.suffix}"
            shutil.move(str(qfile), str(destination))
            print(f"Moved queue to: {destination}")
        except Exception as e:
            print(f"  [queue move failed] {e}")


if __name__ == "__main__":
    print("=" * 70)
    print("  90SecondFinance Pro Assembler v6 — keyword-cache + organized assets")
    print(f"  Base:          {BASE_DIR}")
    print(f"  Audio:         {AUDIO_DIR}")
    print(f"  Queues:        {QUEUES_DIR}")
    print(f"  Keywords:      {KEYWORDS_DIR}")
    print(f"  Video assets:  {PEXELS_VIDEOS_DIR}")
    print(f"  Image assets:  {PEXELS_IMAGES_DIR}")
    print(f"  Music:         {MUSIC_DIR}")
    print(f"  Output:        {OUTPUT_DIR}")
    print("  Press Ctrl+C to stop")
    print("=" * 70)

    while True:
        try:
            process_queue_once()
            if RUN_ONCE:
                break
            time.sleep(60)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"[Queue error] {e}")
            if RUN_ONCE:
                break
            time.sleep(60)
