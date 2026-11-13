"""Super yuklovchi bot yordamchilari: havola tanish, sifat variantlari, nom ajratish.
Tashqi kutubxonasiz — alohida sinab ko'rish mumkin."""
import re
from urllib.parse import parse_qs, urlparse, urlunparse

URL_RE = re.compile(r"https?://[^\s<>\"']+")

# platforma -> host qismlari
PLATFORMS = {
    "instagram": ("instagram.com", "instagr.am"),
    "tiktok": ("tiktok.com",),
    "pinterest": ("pinterest.com", "pin.it", "pinterest.ru"),
    "facebook": ("facebook.com", "fb.watch", "fb.com", "m.facebook.com"),
    "twitter": ("twitter.com", "x.com", "t.co"),
    "likee": ("likee.video", "likee.com", "l.likee.video"),
    "rutube": ("rutube.ru",),
    "youtube": ("youtube.com", "youtu.be", "music.youtube.com", "m.youtube.com"),
}
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VID_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
AUD_EXT = {".mp3", ".m4a", ".ogg", ".opus", ".wav"}


def first_url(text: str) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(").,;!?") if m else None


def platform(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    for name, hosts in PLATFORMS.items():
        if any(host == h or host.endswith("." + h) for h in hosts):
            return name
    return None


def yt_id(url: str) -> str | None:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host == "youtu.be":
        v = u.path.strip("/")[:11]
        return v if len(v) == 11 else None
    m = re.search(r"/(?:shorts|embed|live)/([\w-]{11})", u.path)
    if m:
        return m.group(1)
    v = parse_qs(u.query).get("v", [""])[0]
    return v if len(v) == 11 else None


def is_story(url: str) -> bool:
    return platform(url) == "instagram" and "/stories/" in url


def norm_url(url: str) -> str:
    """Kesh kaliti uchun: izlovchi parametrlarsiz havola."""
    u = urlparse(url)
    if platform(url) == "youtube":
        v = yt_id(url)
        return f"yt:{v}" if v else url
    return urlunparse((u.scheme, (u.hostname or "").lower(), u.path.rstrip("/"), "", "", ""))


def fmt_size(n) -> str:
    if not n:
        return "?"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{max(1, n // 1024)} KB"


def fmt_dur(sec) -> str:
    if not sec:
        return ""
    sec = int(sec)
    return f" {sec // 60}:{sec % 60:02d}" if sec < 3600 else f" {sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def split_title(title: str, uploader: str = "", artist: str = "", track: str = "") -> tuple[str, str]:
    """(ijrochi, qo'shiq nomi). Avval yt-dlp'ning artist/track maydonlari, so'ng «Ijrochi - Nom» ko'rinishi."""
    if track and artist:
        return artist.split(",")[0].strip(), track.strip()
    t = re.sub(r"\s*[\(\[](official|lyrics?|lyric video|audio|video|music video|clip|hd|hq|4k|mv)[^\)\]]*[\)\]]", "", title or "", flags=re.I).strip()
    for sep in (" - ", " – ", " — ", " | "):
        if sep in t:
            a, n = t.split(sep, 1)
            if a.strip() and n.strip():
                return a.strip(), n.strip()
    up = re.sub(r"\s*-\s*Topic$", "", uploader or "", flags=re.I).strip()
    return (artist or up), (t or "audio")


def _size(f: dict, duration) -> int | None:
    s = f.get("filesize") or f.get("filesize_approx")
    if s:
        return int(s)
    tbr = f.get("tbr")
    if tbr and duration:
        return int(tbr * 1000 / 8 * duration)
    return None


def build_options(info: dict, max_bytes: int, max_height: int = 1080) -> list[dict]:
    """YouTube sifat variantlari: [{'data': '360'|'mp3', 'label': '🎬 360p · 12.3 MB', 'size': int}, ...].
    Telegramga sig'maydigan (max_bytes dan katta) variantlar chiqarilmaydi."""
    dur = info.get("duration")
    fmts = info.get("formats") or []
    audios = [f for f in fmts if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")]
    a_best = max(audios, key=lambda f: f.get("abr") or f.get("tbr") or 0, default=None)
    a_size = _size(a_best, dur) if a_best else None
    by_h: dict[int, int | None] = {}
    for f in fmts:
        h = f.get("height")
        if not h or f.get("vcodec") in (None, "none") or h > max_height:
            continue
        s = _size(f, dur)
        if s is not None and f.get("acodec") in (None, "none"):
            s += a_size or 0
        cur = by_h.get(h, -1)
        if cur == -1 or (s is not None and (cur is None or s < cur)):
            by_h[h] = s
    out = []
    for h in sorted(by_h):
        s = by_h[h]
        if s is not None and s > max_bytes:
            continue
        out.append({"data": str(h), "size": s, "label": f"🎬 {h}p · {fmt_size(s)}" if s else f"🎬 {h}p"})
    if dur:
        mp3 = int(dur * 192_000 / 8)
        if mp3 <= max_bytes:
            out.append({"data": "mp3", "size": mp3, "label": f"🎵 MP3 · {fmt_size(mp3)}"})
    elif a_size is None or a_size <= max_bytes:
        out.append({"data": "mp3", "size": a_size, "label": "🎵 MP3"})
    return out


def pick_group_height(options: list[dict], cap: int = 720) -> str | None:
    """Guruhlarda: so'rovsiz — cap dan oshmaydigan eng yaxshi video varianti."""
    vids = [int(o["data"]) for o in options if o["data"].isdigit() and int(o["data"]) <= cap]
    return str(max(vids)) if vids else (next((o["data"] for o in options if o["data"].isdigit()), None))


def chunk(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]
