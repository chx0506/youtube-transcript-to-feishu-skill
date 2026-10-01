#!/usr/bin/env python3
"""
Fetch a YouTube video's transcript and metadata, robustly, from a sandbox whose
IP is blocked by YouTube's bot-verification (yt-dlp / youtube-transcript-api fail
with "Sign in to confirm you're not a bot" / "RequestBlocked").

Strategy (in order, first success wins):
  1. kome.ai server-side transcript API  -> clean text, handles multi-hour videos
  2. yt-dlp subtitle download             -> fallback if the API is down
  3. youtube-transcript-api               -> last resort

Title/author come from YouTube's oembed endpoint (no API key, rarely blocked).

Output: a JSON object on stdout:
  {
    "ok": true,
    "video_id": "...",
    "url": "https://www.youtube.com/watch?v=...",
    "title": "...",
    "author": "...",
    "publish_date": "2024年06月21日",  # normalized to 年-月-日, may be null
    "duration": "12m 19s",        # may be null
    "language": "en",             # best guess of source language, may be null
    "source": "kome.ai",          # which extractor succeeded
    "transcript": "full text..."
  }
On failure: {"ok": false, "error": "...", "tried": [...]}  (exit code 1)

Usage:
  python3 fetch_transcript.py "<youtube url or id>" [--out transcript.json]
"""
import sys, os, re, json, argparse, urllib.request, urllib.parse, subprocess, tempfile, glob

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def extract_video_id(s: str) -> str:
    s = s.strip()
    # bare 11-char id
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    patterns = [
        r"(?:v=|/videos/|youtu\.be/|/embed/|/shorts/|/live/)([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, s)
        if m:
            return m.group(1)
    # query parse fallback
    try:
        q = urllib.parse.urlparse(s)
        v = urllib.parse.parse_qs(q.query).get("v", [None])[0]
        if v and re.fullmatch(r"[A-Za-z0-9_-]{11}", v):
            return v
    except Exception:
        pass
    raise ValueError(f"Could not extract a YouTube video id from: {s!r}")


def get_metadata(video_id: str):
    """Title + author via oembed. Returns (title, author) or (None, None)."""
    try:
        url = ("https://www.youtube.com/oembed?url="
               + urllib.parse.quote(f"https://www.youtube.com/watch?v={video_id}")
               + "&format=json")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        meta = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore"))
        return meta.get("title"), meta.get("author_name")
    except Exception:
        return None, None


def _to_cn_date(raw: str):
    """Normalize a date-ish string to 'YYYY年MM月DD日'. Returns None on failure."""
    if not raw:
        return None
    # ISO 8601 (2024-06-21, 2024-06-21T12:00:00Z, etc.)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return f"{m.group(1)}年{m.group(2)}月{m.group(3)}日"
    # "Jun 21, 2024" / "June 21, 2024"
    months = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05",
              "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10",
              "nov": "11", "dec": "12"}
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", raw)
    if m:
        mo = months.get(m.group(1)[:3].lower())
        if mo:
            return f"{m.group(3)}年{mo}月{int(m.group(2)):02d}日"
    return None


def get_publish_date(video_id: str):
    """
    Best-effort publish date of the video, normalized to 'YYYY年MM月DD日'.
    Scrapes the watch-page HTML (publishDate / uploadDate / datePublished).
    Returns the CN-formatted string, or None if it can't be determined.
    """
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept-Language": "en-US,en;q=0.9"})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        for key in ("publishDate", "uploadDate", "datePublished"):
            m = re.search(r'"%s"\s*:\s*"([^"]+)"' % key, html)
            if m:
                cn = _to_cn_date(m.group(1))
                if cn:
                    return cn
    except Exception:
        pass
    return None


def via_kome(video_id: str):
    """kome.ai server-side transcript API. Returns dict or raises."""
    url = "https://kome.ai/api/transcript"
    body = json.dumps({"video_id": f"https://www.youtube.com/watch?v={video_id}",
                       "format": True}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"User-Agent": UA, "Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=120).read().decode("utf-8", "ignore")
    j = json.loads(resp)
    text = (j.get("transcript") or "").strip()
    if not text:
        raise RuntimeError("kome.ai returned empty transcript")
    return {"transcript": text, "duration": j.get("length"),
            "has_more": j.get("hasMore"), "source": "kome.ai"}


def _vtt_to_text(vtt: str) -> str:
    lines = []
    for ln in vtt.splitlines():
        ln = ln.strip()
        if (not ln or ln == "WEBVTT" or "-->" in ln
                or ln.isdigit() or ln.startswith(("Kind:", "Language:", "NOTE"))):
            continue
        ln = re.sub(r"<[^>]+>", "", ln)        # inline timing tags
        ln = re.sub(r"&nbsp;?", " ", ln)
        if lines and lines[-1] == ln:           # dedupe rolling captions
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


def via_ytdlp(video_id: str):
    workdir = tempfile.mkdtemp(prefix="ytsub_")
    out = os.path.join(workdir, "%(id)s.%(ext)s")
    cmd = ["yt-dlp", "--no-check-certificates", "--skip-download",
           "--write-auto-subs", "--write-subs", "--sub-langs", "en.*,en",
           "--sub-format", "vtt/srv3/best", "-o", out,
           f"https://www.youtube.com/watch?v={video_id}"]
    subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    files = glob.glob(os.path.join(workdir, "*.vtt")) + glob.glob(os.path.join(workdir, "*.srt"))
    if not files:
        raise RuntimeError("yt-dlp produced no subtitle file")
    with open(files[0], encoding="utf-8", errors="ignore") as f:
        text = _vtt_to_text(f.read())
    if not text:
        raise RuntimeError("yt-dlp subtitle parsed empty")
    lang = None
    m = re.search(r"\.([a-zA-Z-]+)\.(vtt|srt)$", os.path.basename(files[0]))
    if m:
        lang = m.group(1)
    return {"transcript": text, "duration": None, "language": lang, "source": "yt-dlp"}


def via_transcript_api(video_id: str):
    from youtube_transcript_api import YouTubeTranscriptApi
    api = YouTubeTranscriptApi()
    data = api.fetch(video_id)
    text = "\n".join(s.text for s in data.snippets if s.text.strip())
    if not text.strip():
        raise RuntimeError("youtube-transcript-api returned empty")
    return {"transcript": text, "duration": None,
            "language": getattr(data, "language_code", None),
            "source": "youtube-transcript-api"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="YouTube URL or 11-char video id")
    ap.add_argument("--out", help="write JSON result to this file as well as stdout")
    args = ap.parse_args()

    try:
        video_id = extract_video_id(args.url)
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)

    title, author = get_metadata(video_id)
    publish_date = get_publish_date(video_id)

    tried, result, last_err = [], None, None
    for fn in (via_kome, via_ytdlp, via_transcript_api):
        name = fn.__name__
        try:
            result = fn(video_id)
            tried.append({"method": name, "ok": True})
            break
        except Exception as e:
            tried.append({"method": name, "ok": False, "error": str(e)[:200]})
            last_err = str(e)

    if not result:
        out = {"ok": False, "video_id": video_id,
               "url": f"https://www.youtube.com/watch?v={video_id}",
               "title": title, "author": author, "publish_date": publish_date,
               "error": f"all extractors failed; last error: {last_err}",
               "tried": tried}
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(1)

    out = {"ok": True, "video_id": video_id,
           "url": f"https://www.youtube.com/watch?v={video_id}",
           "title": title, "author": author,
           "publish_date": publish_date,
           "duration": result.get("duration"),
           "language": result.get("language"),
           "source": result.get("source"),
           "transcript": result["transcript"],
           "char_count": len(result["transcript"]),
           "tried": tried}
    payload = json.dumps(out, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload)
    # stdout: print metadata summary + note where the full text is, to avoid flooding context
    summary = dict(out)
    summary["transcript"] = (out["transcript"][:500] + " …[truncated, see --out file]") \
        if (args.out and len(out["transcript"]) > 500) else out["transcript"]
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
