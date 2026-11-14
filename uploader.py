import os, sys, re, json, time, asyncio, aiohttp, aiofiles, subprocess, math
from pathlib import Path

API_ID = 6
API_HASH = "eb06d4abfb49dc3eeb1aeb98ae0f581e"
SESSION = os.environ.get("SESSION_STRING", "")
LINK_INPUT = os.environ.get("LINK", "")
CAPTION = os.environ.get("CAPTION", "")
FILENAME = os.environ.get("FILENAME", "")
YT_COOKIES = os.environ.get("YT_COOKIES", "")
MAX_SIZE = 2000 * 1024 * 1024
ME = None

def extract_yt_id(url):
    m = re.search(r'(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})', url)
    return m.group(1) if m else None

def is_youtube(url):
    return bool(re.search(r'youtube\.com|youtu\.be', url))

def is_tg_link(url):
    return bool(re.search(r't\.me/', url))

async def get_file_info(path):
    dur, w, h = 0, 0, 0
    try:
        p = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await p.communicate()
        d = json.loads(out)
        for s in d.get("streams", []):
            if s.get("codec_type") == "video":
                w = int(s.get("width", 0))
                h = int(s.get("height", 0))
                dur = int(float(s.get("duration", 0)))
                break
        if not dur:
            dur = int(float(d.get("format", {}).get("duration", 0)))
    except Exception:
        pass
    return dur, w, h

async def split_file(path, max_bytes=MAX_SIZE):
    size = os.path.getsize(path)
    if size <= max_bytes:
        return [path]
    parts = math.ceil(size / max_bytes)
    name = Path(path).stem
    ext = Path(path).suffix
    folder = os.path.dirname(path)
    result = []
    for i in range(parts):
        out = os.path.join(folder, f"{name}_part{i+1}{ext}")
        p = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", path,
            "-ss", str(i * (size // parts)),
            "-t", str(size // parts),
            "-c", "copy", out,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await p.communicate()
        if os.path.exists(out):
            result.append(out)
    return result if result else [path]

async def download_yt_thumb(video_id, folder):
    url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
    path = os.path.join(folder, f"{video_id}_thumb.jpg")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                if r.status == 200:
                    data = await r.read()
                    if len(data) > 5000:
                        async with aiofiles.open(path, "wb") as f:
                            await f.write(data)
                        return path
    except Exception:
        pass
    return None

async def download_tg_post(url, client, work_dir):
    m = re.search(r't\.me/([a-zA-Z0-9_]+)/(\d+)', url)
    if not m:
        m = re.search(r't\.me/c/(\d+)/(\d+)', url)
        if not m:
            return None, None, None, "Invalid Telegram link"
        chat_id = int("-100" + m.group(1))
        msg_id = int(m.group(2))
    else:
        chat_id = m.group(1)
        msg_id = int(m.group(2))
    try:
        msg = await client.get_messages(chat_id, msg_id)
    except Exception as e:
        return None, None, None, f"Cannot access post: {e}"
    if not (msg.video or msg.document or msg.animation or msg.photo or msg.audio):
        return None, None, None, "No media in this post"
    thumb_path = None
    if msg.video and msg.video.thumbs:
        try:
            thumb_path = await msg.video.thumbs[-1].download(work_dir)
        except Exception:
            pass
    print(f"  Downloading from Telegram...")
    file_path = await msg.download(work_dir)
    if not file_path:
        return None, None, None, "Download failed"
    cap = msg.caption or ""
    return file_path, thumb_path, cap, None

async def download_direct(url, work_dir):
    fname = FILENAME if FILENAME else url.split("/")[-1].split("?")[0] or "file"
    if "." not in fname:
        fname += ".mp4"
    path = os.path.join(work_dir, fname)
    print(f"  Downloading direct URL...")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=600) as r:
                if r.status != 200:
                    return None, None, f"HTTP {r.status}"
                async with aiofiles.open(path, "wb") as f:
                    async for chunk in r.content.iter_chunked(1024*1024):
                        await f.write(chunk)
    except Exception as e:
        return None, None, str(e)
    if os.path.getsize(path) < 1000:
        os.remove(path)
        return None, None, "File too small"
    return path, None, None
async def download_youtube(url, work_dir):
    video_id = extract_yt_id(url)
    if not video_id:
        return None, None, "Invalid YouTube URL"
    print(f"YouTube detected! ID: {video_id}")
    cookie_file = None
    cookie_args = []
    if YT_COOKIES.strip():
        cookie_file = os.path.join(work_dir, "cookies.txt")
        with open(cookie_file, "w") as f:
            f.write(YT_COOKIES)
        cookie_args = ["--cookies", cookie_file]
        print("  Cookies loaded from YT_COOKIES secret")
    else:
        print("  WARNING: No cookies in YT_COOKIES secret!")
    clients = ["web", "tv", "mweb", "web_safari", "web_embedded", "ios", "android"]
    for client in clients:
        tag = f"{client}+cookies" if cookie_args else client
        print(f"  Trying yt-dlp: {tag}")
        out_tpl = os.path.join(work_dir, "%(id)s.%(ext)s")
        cmd = [
            "yt-dlp", "--no-playlist",
            "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", out_tpl,
            "--extractor-args", f"youtube:player_client={client}",
            "--no-warnings", "--force-ipv4",
            "--socket-timeout", "30",
            "--retries", "2",
        ] + cookie_args + [url]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            for fn in os.listdir(work_dir):
                if fn.endswith(".mp4") and os.path.getsize(os.path.join(work_dir, fn)) > 10000:
                    fp = os.path.join(work_dir, fn)
                    thumb = await download_yt_thumb(video_id, work_dir)
                    print(f"  Downloaded: {fn} ({os.path.getsize(fp)//1024//1024}MB)")
                    return fp, thumb, None
        err = stderr.decode(errors="ignore").strip()
        last = err.split("\n")[-1][:120] if err else "unknown"
        print(f"    Failed: {last}")
    print("  All yt-dlp clients failed. Trying Piped API...")
    piped_instances = [
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.adminforge.de",
        "https://pipedapi.r4fo.com",
        "https://api.piped.privacydev.net",
    ]
    for inst in piped_instances:
        print(f"  Trying Piped: {inst}")
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{inst}/streams/{video_id}", timeout=15) as r:
                    if r.status != 200:
                        print(f"    Status: {r.status}")
                        continue
                    data = await r.json()
                    vs = data.get("videoStreams", [])
                    best = None
                    for v in vs:
                        if v.get("mimeType", "").startswith("video/mp4") and v.get("url"):
                            h = int(v.get("height", 0))
                            if h <= 1080:
                                if not best or h > int(best.get("height", 0)):
                                    best = v
                    if not best:
                        for v in vs:
                            if v.get("url") and int(v.get("height", 0)) <= 1080:
                                best = v
                                break
                    if not best or not best.get("url"):
                        print("    No video stream found")
                        continue
                    dl_url = best["url"]
                    fp = os.path.join(work_dir, f"{video_id}.mp4")
                    async with s.get(dl_url, timeout=600) as dr:
                        if dr.status != 200:
                            print(f"    Download HTTP {dr.status}")
                            continue
                        async with aiofiles.open(fp, "wb") as f:
                            async for chunk in dr.content.iter_chunked(1024*1024):
                                await f.write(chunk)
                    if os.path.exists(fp) and os.path.getsize(fp) > 10000:
                        thumb = await download_yt_thumb(video_id, work_dir)
                        print(f"  Piped OK: {os.path.getsize(fp)//1024//1024}MB")
                        return fp, thumb, None
                    else:
                        print("    Downloaded file too small")
        except Exception as e:
            print(f"    Error: {str(e)[:80]}")
    return None, None, "All YouTube methods failed (yt-dlp + Piped)"

async def upload_to_saved(client, path, thumb, caption, is_video=True):
    size = os.path.getsize(path)
    name = FILENAME if FILENAME else os.path.basename(path)
    if size > MAX_SIZE:
        parts = await split_file(path)
        for i, p in enumerate(parts):
            cap = f"{caption}\n\n(Part {i+1}/{len(parts)})" if caption else f"Part {i+1}/{len(parts)}"
            await _send(client, p, thumb, cap, is_video)
    else:
        await _send(client, path, thumb, caption, is_video)

async def _send(client, path, thumb, caption, is_video):
    dur, w, h = await get_file_info(path) if is_video else (0, 0, 0)
    kwargs = {"chat_id": "me", "caption": caption or None}
    if is_video and path.lower().endswith((".mp4", ".mkv", ".avi", ".mov", ".webm")):
        kwargs.update({"duration": dur, "width": w, "height": h, "supports_streaming": True})
        if thumb:
            kwargs["thumb"] = thumb
        await client.send_video(path, **kwargs)
    elif path.lower().endswith((".mp3", ".ogg", ".flac", ".wav", ".m4a")):
        await client.send_audio(path, **kwargs)
    elif path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        await client.send_photo(path, **kwargs)
    else:
        await client.send_document(path, **kwargs)
    print(f"  Uploaded: {os.path.basename(path)} ({os.path.getsize(path)//1024//1024}MB)")

async def main():
    from pyrogram import Client
    global ME
    print("=" * 60)
    print("Telegram Uploader v8 (YouTube + Cookies)")
    print("=" * 60)
    links = [l.strip() for l in LINK_INPUT.split("\n") if l.strip()]
    if not links:
        print("No links provided! Set LINK input.")
        return
    print(f"Total links: {len(links)}")
    for i, l in enumerate(links, 1):
        print(f"   {i}. {l}")
    app = Client("session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION)
    await app.start()
    ME = await app.get_me()
    print(f"Logged in: {ME.first_name}")
    print("=" * 60)
    ok, fail = 0, 0
    for i, link in enumerate(links, 1):
        print(f"\n[{i}/{len(links)}] {link}")
        print("=" * 60)
        work = os.path.join(tempfile.gettempdir(), f"up_{i}_{int(time.time())}")
        os.makedirs(work, exist_ok=True)
        try:
            fp, thumb, cap, err = None, None, "", None
            if is_tg_link(link):
                print("--- Telegram Post ---")
                fp, thumb, cap, err = await download_tg_post(link, app, work)
            elif is_youtube(link):
                print("--- YouTube Download (cookies + multi-client) ---")
                fp, thumb, err = await download_youtube(link, work)
                cap = CAPTION
            else:
                print("--- Direct URL ---")
                fp, thumb, err = await download_direct(link, work)
                cap = CAPTION
            if err:
                print(f"Error: {err}")
                fail += 1
                continue
            if not fp or not os.path.exists(fp):
                print("Error: No file downloaded")
                fail += 1
                continue
            final_cap = CAPTION if CAPTION else cap
            is_vid = fp.lower().endswith((".mp4", ".mkv", ".avi", ".mov", ".webm"))
            await upload_to_saved(app, fp, thumb, final_cap, is_vid)
            ok += 1
        except Exception as e:
            print(f"Error: {e}")
            fail += 1
        finally:
            shutil.rmtree(work, ignore_errors=True)
    print("\n" + "=" * 60)
    print("ALL DONE!")
    print(f"   Success: {ok}")
    print(f"   Failed:  {fail}")
    print(f"   Total:   {len(links)}")
    print("=" * 60)
    await app.stop()

import shutil, tempfile
if __name__ == "__main__":
    asyncio.run(main())
