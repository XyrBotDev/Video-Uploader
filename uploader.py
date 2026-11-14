import os
import sys
import time
import asyncio
import aiohttp
import aiofiles
import subprocess
import re
import glob
from urllib.parse import urlparse, unquote
from pyrogram import Client
from pyrogram.errors import FloodWait, UserDeactivated, AuthKeyUnregistered

API_ID = 6
API_HASH = "eb06d4abfb49dc3eeb1aeb98ae0f581e"
WORKERS = 32
CHUNK_SIZE = 20 * 1024 * 1024
PARALLEL_PARTS = 6
MAX_RETRIES = 5
TELEGRAM_LIMIT = 2 * 1024 * 1024 * 1024

SESSION_STRING = os.environ.get("SESSION_STRING", "")
LINKS_RAW = os.environ.get("LINK", "")
CAPTION = os.environ.get("CAPTION", "")
CUSTOM_FILENAME = os.environ.get("FILENAME", "")

YOUTUBE_RE = re.compile(
    r'(?:https?://)?(?:www\.)?(?:m\.)?'
    r'(?:youtube\.com/(?:watch\?v=|shorts/|embed/|live/)|youtu\.be/)'
    r'([a-zA-Z0-9_-]{11})'
)

def is_youtube_link(url):
    return bool(YOUTUBE_RE.search(url))

def get_youtube_id(url):
    m = YOUTUBE_RE.search(url)
    return m.group(1) if m else None

def parse_links(raw):
    links = []
    for line in raw.replace(",", "\n").splitlines():
        link = line.strip()
        if link and (link.startswith("http") or "t.me/" in link):
            links.append(link)
    return links

def parse_telegram_link(link):
    link = link.strip()
    if link.startswith("t.me/"):
        link = "https://" + link
    if "t.me/" not in link:
        return None, None
    try:
        parts = urlparse(link).path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "c":
            return int("-100" + parts[1]), int(parts[2])
        elif len(parts) >= 2:
            return parts[0], int(parts[1])
    except Exception:
        pass
    return None, None

def get_filename_from_url(url):
    fn = os.path.basename(unquote(urlparse(url).path))
    return fn if fn and '.' in fn else "file"

def human_size(b):
    if b == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    s = float(b)
    while s >= 1024 and i < 4:
        s /= 1024
        i += 1
    return f"{s:.2f} {units[i]}"

def format_time(seconds):
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}m"
    return f"{seconds/3600:.1f}h"

def make_progress_cb(prefix):
    last = [0]
    def cb(cur, tot):
        now = time.time()
        if now - last[0] >= 2 or cur == tot:
            last[0] = now
            if tot > 0:
                p = cur * 100 / tot
                f = int(p / 2)
                bar = "#" * f + "-" * (50 - f)
                print(f"\r{prefix} |{bar}| {p:.1f}% ({human_size(cur)}/{human_size(tot)})", end="", flush=True)
    return cb

async def download_youtube_cobalt(url):
    video_id = get_youtube_id(url)
    print(f"\nYouTube video detected!")
    print(f"Video ID: {video_id}")
    print(f"URL: {url}")
    print("Using Cobalt API (no cookies needed)...")
    cobalt_urls = [
        "https://api.cobalt.tools",
        "https://cobalt-api.kwiatekmiki.com",
        "https://cobalt.api.timelessnesses.me",
    ]
    download_url = None
    filename = f"yt_{video_id}.mp4"
    for api_url in cobalt_urls:
        try:
            print(f"Trying: {api_url}")
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                }
                payload = {
                    "url": url,
                    "videoQuality": "1080",
                    "filenameStyle": "basic",
                }
                async with session.post(api_url, json=payload, headers=headers) as resp:
                    data = await resp.json()
                    if resp.status == 200 and data.get("url"):
                        download_url = data["url"]
                        print(f"Got download link from Cobalt!")
                        break
                    elif data.get("status") == "tunnel" and data.get("url"):
                        download_url = data["url"]
                        print(f"Got tunnel link from Cobalt!")
                        break
                    else:
                        print(f"Response: {data}")
        except Exception as e:
            print(f"Failed: {e}")
            continue
    if not download_url:
        raise Exception("All Cobalt APIs failed. YouTube download not available.")
    filepath = os.path.join("/tmp", filename)
    print(f"Downloading video...")
    timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(download_url) as resp:
            if resp.status != 200:
                raise Exception(f"Download failed: HTTP {resp.status}")
            total = int(resp.headers.get("content-length", 0))
            print(f"Size: {human_size(total) if total else 'Unknown'}")
            dl = 0
            t0 = time.time()
            async with aiofiles.open(filepath, "wb") as f:
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    await f.write(chunk)
                    dl += len(chunk)
                    if total:
                        p = dl * 100 / total
                        el = time.time() - t0
                        sp = dl / el if el > 0 else 0
                        eta = (total - dl) / sp if sp > 0 else 0
                        fi = int(p / 2)
                        bar = "#" * fi + "-" * (50 - fi)
                        print(f"\rYT |{bar}| {p:.1f}% {human_size(int(sp))}/s ETA:{format_time(eta)}", end="", flush=True)
            print()
    elapsed = time.time() - t0
    size = os.path.getsize(filepath)
    print(f"Download complete! {human_size(size)} in {format_time(elapsed)}")
    thumb_path = f"/tmp/yt_{video_id}_thumb.jpg"
    thumb_urls = [
        f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/sddefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
    ]
    thumb_found = False
    async with aiohttp.ClientSession() as session:
        for turl in thumb_urls:
            try:
                async with session.get(turl) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        if len(data) > 5000:
                            async with aiofiles.open(thumb_path, "wb") as f:
                                await f.write(data)
                            print(f"Thumbnail downloaded: {human_size(len(data))}")
                            thumb_found = True
                            break
            except Exception:
                continue
    if not thumb_found:
        print("No thumbnail found")
        thumb_path = None
    duration = 0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1", filepath],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15
        )
        for line in r.stdout.splitlines():
            if "duration=" in line:
                duration = int(float(line.split("=")[1]))
    except Exception:
        pass
    yt_caption = f"YouTube Video\nDuration: {format_time(duration)}\nLink: {url}"
    return filepath, thumb_path, yt_caption, duration

async def download_part(session, url, start, end, part_path, sem):
    async with sem:
        headers = {"Range": f"bytes={start}-{end}"}
        for attempt in range(MAX_RETRIES):
            try:
                timeout = aiohttp.ClientTimeout(total=None, sock_read=120)
                async with session.get(url, headers=headers, timeout=timeout) as resp:
                    if resp.status in (200, 206):
                        async with aiofiles.open(part_path, 'wb') as f:
                            async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                                await f.write(chunk)
                        return os.path.getsize(part_path)
            except Exception:
                await asyncio.sleep(2 ** attempt)
        return 0

async def parallel_download(url, filename):
    filepath = os.path.join("/tmp", filename)
    print(f"\nParallel Download: {filename}")
    timeout = aiohttp.ClientTimeout(total=None, connect=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.head(url, allow_redirects=True) as resp:
            total = int(resp.headers.get('content-length', 0))
            accept_range = resp.headers.get('accept-ranges', '')
        if total == 0 or 'bytes' not in accept_range:
            return await single_download(url, filename)
        print(f"Size: {human_size(total)} Parts: {PARALLEL_PARTS}")
        part_size = total // PARALLEL_PARTS
        parts = []
        for i in range(PARALLEL_PARTS):
            s = i * part_size
            e = (i + 1) * part_size - 1 if i < PARALLEL_PARTS - 1 else total - 1
            parts.append((s, e, f"{filepath}.part{i}"))
        sem = asyncio.Semaphore(PARALLEL_PARTS)
        t0 = time.time()
        tasks = [download_part(session, url, s, e, p, sem) for s, e, p in parts]
        await asyncio.gather(*tasks)
        print("\nMerging parts...")
        async with aiofiles.open(filepath, 'wb') as out:
            for _, _, pp in parts:
                if os.path.exists(pp):
                    async with aiofiles.open(pp, 'rb') as pf:
                        while True:
                            chunk = await pf.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            await out.write(chunk)
                    os.remove(pp)
    elapsed = time.time() - t0
    size = os.path.getsize(filepath)
    speed = int(size / elapsed) if elapsed > 0 else 0
    print(f"Done! {human_size(size)} in {format_time(elapsed)} ({human_size(speed)}/s)")
    return filepath

async def single_download(url, filename):
    filepath = os.path.join("/tmp", filename)
    timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt in range(MAX_RETRIES):
            try:
                async with session.get(url, allow_redirects=True) as resp:
                    if resp.status != 200:
                        raise Exception(f"HTTP {resp.status}")
                    total = int(resp.headers.get('content-length', 0))
                    print(f"Size: {human_size(total) if total else 'Unknown'}")
                    dl = 0
                    t0 = time.time()
                    async with aiofiles.open(filepath, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                            await f.write(chunk)
                            dl += len(chunk)
                            if total:
                                p = dl * 100 / total
                                el = time.time() - t0
                                sp = dl / el if el > 0 else 0
                                eta = (total - dl) / sp if sp > 0 else 0
                                fi = int(p / 2)
                                bar = "#" * fi + "-" * (50 - fi)
                                print(f"\rDL |{bar}| {p:.1f}% {human_size(int(sp))}/s ETA:{format_time(eta)}", end="", flush=True)
                    print()
                    return filepath
            except Exception as e:
                print(f"\nRetry {attempt+1}: {e}")
                await asyncio.sleep(2 ** attempt)
    raise Exception("Download failed!")

def get_video_metadata(filepath):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,duration",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1", filepath],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15
        )
        w = 0
        h = 0
        d = 0
        for line in r.stdout.splitlines():
            if "width=" in line:
                w = int(line.split("=")[1])
            if "height=" in line:
                h = int(line.split("=")[1])
            if "duration=" in line:
                try:
                    d = int(float(line.split("=")[1]))
                except Exception:
                    pass
        return d, w, h
    except Exception:
        return 0, 0, 0

def generate_thumbnail_ffmpeg(filepath):
    thumb = "/tmp/thumb_ffmpeg.jpg"
    try:
        if os.path.exists(thumb):
            os.remove(thumb)
        subprocess.run(
            ["ffmpeg", "-y", "-i", filepath, "-ss", "00:00:01.000",
             "-vframes", "1", "-vf", "scale=320:-1", "-q:v", "2", thumb],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20
        )
        if os.path.exists(thumb) and os.path.getsize(thumb) > 0:
            return thumb
    except Exception:
        pass
    return None

async def download_original_thumb(app, msg):
    thumbs = None
    if msg.video and msg.video.thumbs:
        thumbs = msg.video.thumbs
    elif msg.document and msg.document.thumbs:
        thumbs = msg.document.thumbs
    elif msg.animation and msg.animation.thumbs:
        thumbs = msg.animation.thumbs
    if not thumbs:
        return None
    best = thumbs[-1]
    try:
        path = await app.download_media(best, file_name="/tmp/orig_thumb_")
        if path and os.path.exists(path) and os.path.getsize(path) > 0:
            if path.endswith(".webp"):
                jpg = "/tmp/orig_thumb.jpg"
                subprocess.run(["ffmpeg", "-y", "-i", path, jpg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                if os.path.exists(jpg):
                    os.remove(path)
                    path = jpg
            return path
    except Exception:
        pass
    return None

def split_file(filepath, max_size=TELEGRAM_LIMIT):
    size = os.path.getsize(filepath)
    if size <= max_size:
        return [filepath]
    print(f"\nSplitting {human_size(size)}...")
    parts = []
    pn = 1
    base, ext = os.path.splitext(filepath)
    with open(filepath, 'rb') as f:
        while True:
            pp = f"{base}.part{pn:03d}{ext}"
            w = 0
            with open(pp, 'wb') as pf:
                while w < max_size:
                    chunk = f.read(min(CHUNK_SIZE, max_size - w))
                    if not chunk:
                        break
                    pf.write(chunk)
                    w += len(chunk)
            if w > 0:
                parts.append(pp)
                print(f"   Part {pn}: {human_size(w)}")
                pn += 1
            else:
                break
    return parts

async def upload_single_file(app, filepath, caption, thumb=None, duration=0, part_info=""):
    file_size = os.path.getsize(filepath)
    filename = os.path.basename(filepath)
    ext = filename.lower().split('.')[-1] if '.' in filename else ''
    video_exts = {'mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'wmv', '3gp'}
    audio_exts = {'mp3', 'flac', 'wav', 'aac', 'ogg', 'm4a', 'wma', 'opus'}
    photo_exts = {'jpg', 'jpeg', 'png', 'bmp', 'webp'}
    final_caption = caption if caption else filename
    if part_info:
        final_caption = final_caption + "\n" + part_info
    for attempt in range(MAX_RETRIES):
        try:
            if ext in video_exts:
                d, w, h = get_video_metadata(filepath)
                if not duration:
                    duration = d
                if not thumb:
                    thumb = generate_thumbnail_ffmpeg(filepath)
                await app.send_video(
                    chat_id="me", video=filepath, caption=final_caption,
                    file_name=filename, thumb=thumb, duration=duration,
                    width=w, height=h, supports_streaming=True,
                    progress=make_progress_cb("Upload " + part_info)
                )
            elif ext in audio_exts:
                await app.send_audio(
                    chat_id="me", audio=filepath, caption=final_caption,
                    file_name=filename, progress=make_progress_cb("Upload")
                )
            elif ext in photo_exts and file_size < 10 * 1024 * 1024:
                await app.send_photo(
                    chat_id="me", photo=filepath, caption=final_caption,
                    progress=make_progress_cb("Upload")
                )
            else:
                await app.send_document(
                    chat_id="me", document=filepath, caption=final_caption,
                    file_name=filename, force_document=True, thumb=thumb,
                    progress=make_progress_cb("Upload")
                )
            return True
        except FloodWait as e:
            print(f"\nFloodWait: {e.value}s")
            await asyncio.sleep(e.value + 2)
        except Exception as e:
            print(f"\nRetry {attempt+1}: {e}")
            await asyncio.sleep(2 ** attempt)
    return False

async def main():
    print("=" * 60)
    print("Telegram Uploader v5 (Cobalt API - No Cookies No yt-dlp)")
    print("=" * 60)
    if not SESSION_STRING:
        print("SESSION_STRING missing!")
        sys.exit(1)
    if not LINKS_RAW:
        print("LINK missing!")
        sys.exit(1)
    links = parse_links(LINKS_RAW)
    if not links:
        print("No valid links!")
        sys.exit(1)
    print(f"\nTotal links: {len(links)}")
    for i, l in enumerate(links, 1):
        print(f"   {i}. {l[:80]}")
    app = Client(
        "uploader", api_id=API_ID, api_hash=API_HASH,
        session_string=SESSION_STRING, in_memory=True, workers=WORKERS
    )
    async with app:
        try:
            me = await app.get_me()
            print(f"\nLogged in: {me.first_name}")
        except (UserDeactivated, AuthKeyUnregistered):
            print("Session invalid!")
            sys.exit(1)
        t_start = time.time()
        success = 0
        failed = 0
        for idx, link in enumerate(links, 1):
            print(f"\n{'='*60}")
            print(f"[{idx}/{len(links)}] {link[:70]}")
            print(f"{'='*60}")
            filepath = None
            thumb = None
            caption = CAPTION
            duration = 0
            try:
                if is_youtube_link(link):
                    filepath, thumb, yt_caption, duration = await download_youtube_cobalt(link)
                    if not CAPTION:
                        caption = yt_caption
                else:
                    chat_id, msg_id = parse_telegram_link(link)
                    if chat_id is not None and msg_id is not None:
                        msg = await app.get_messages(chat_id, msg_id)
                        if not msg:
                            raise Exception("Message not found!")
                        if msg.media:
                            thumb = await download_original_thumb(app, msg)
                            filepath = await app.download_media(
                                msg, file_name="/tmp/",
                                progress=make_progress_cb("TG Download")
                            )
                        elif msg.text and msg.text.strip().startswith("http"):
                            fn = get_filename_from_url(msg.text.strip())
                            filepath = await parallel_download(msg.text.strip(), fn)
                        else:
                            raise Exception("No media!")
                    elif link.startswith("http"):
                        if CUSTOM_FILENAME and len(links) == 1:
                            fn = CUSTOM_FILENAME
                        else:
                            fn = get_filename_from_url(link)
                        filepath = await parallel_download(link, fn)
                    else:
                        print("Invalid link")
                        failed += 1
                        continue
                if not filepath or not os.path.exists(filepath):
                    raise Exception("File not found!")
                if CUSTOM_FILENAME and len(links) == 1:
                    np = os.path.join(os.path.dirname(filepath), CUSTOM_FILENAME)
                    if np != filepath:
                        os.rename(filepath, np)
                        filepath = np
                parts = split_file(filepath)
                for pi, part in enumerate(parts):
                    if len(parts) > 1:
                        pi_info = f"Part {pi+1}/{len(parts)}"
                    else:
                        pi_info = ""
                    ok = await upload_single_file(app, part, caption, thumb, duration, pi_info)
                    if ok:
                        success += 1
                    else:
                        failed += 1
                    if part != filepath and os.path.exists(part):
                        os.remove(part)
            except Exception as e:
                print(f"Error: {e}")
                failed += 1
            for f in [filepath, thumb]:
                if f and os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception:
                        pass
        print(f"\n{'='*60}")
        print("ALL DONE!")
        print(f"   Success: {success}")
        print(f"   Failed:  {failed}")
        print(f"   Total:   {format_time(time.time()-t_start)}")
        print(f"{'='*60}")

if __name__ == "__main__":
    asyncio.run(main())
