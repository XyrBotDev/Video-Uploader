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

def yt_progress_hook(d):
    if d['status'] == 'downloading':
        total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
        downloaded = d.get('downloaded_bytes', 0)
        speed = d.get('speed') or 0
        eta = d.get('eta') or 0
        if total > 0:
            p = downloaded * 100 / total
            f = int(p / 2)
            bar = "#" * f + "-" * (50 - f)
            print(f"\rYT |{bar}| {p:.1f}% {human_size(int(speed))}/s ETA:{format_time(eta)}", end="", flush=True)
    elif d['status'] == 'finished':
        print("\nDone merging...")

def download_youtube_ytdlp(url):
    import yt_dlp
    print(f"\nYouTube detected!")
    print(f"URL: {url}")
    for f in glob.glob("/tmp/yt_download.*"):
        try:
            os.remove(f)
        except Exception:
            pass
    clients_to_try = [
        ['mediaconnect'],
        ['web_safari'],
        ['web_embedded'],
        ['ios'],
        ['android'],
    ]
    for client_list in clients_to_try:
        client_name = client_list[0]
        print(f"\nTrying yt-dlp client: {client_name}")
        ydl_opts = {
            'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[ext=mp4]/best',
            'outtmpl': '/tmp/yt_download.%(ext)s',
            'merge_output_format': 'mp4',
            'writethumbnail': True,
            'thumbnail_format': 'jpg',
            'convert-thumbnails': 'jpg',
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'socket_timeout': 30,
            'retries': 3,
            'fragment_retries': 3,
            'nocheckcertificate': True,
            'progress_hooks': [yt_progress_hook],
            'extractor_args': {
                'youtube': {
                    'player_client': client_list,
                }
            },
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get('title', 'YouTube Video')
                duration = info.get('duration', 0)
                uploader = info.get('uploader', '')
                views = info.get('view_count', 0)
                video_path = ydl.prepare_filename(info)
                base = os.path.splitext(video_path)[0]
                mp4_path = base + ".mp4"
                if os.path.exists(mp4_path):
                    video_path = mp4_path
                thumb_path = base + ".jpg"
                if not os.path.exists(thumb_path):
                    webp = base + ".webp"
                    if os.path.exists(webp):
                        subprocess.run(["ffmpeg", "-y", "-i", webp, thumb_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                        try:
                            os.remove(webp)
                        except Exception:
                            pass
                if not os.path.exists(video_path):
                    candidates = glob.glob("/tmp/yt_download.*")
                    for c in candidates:
                        if c.endswith(('.mp4', '.mkv', '.webm')):
                            video_path = c
                            break
                if os.path.exists(video_path) and os.path.getsize(video_path) > 10000:
                    print(f"\nSuccess with client: {client_name}")
                    print(f"Title: {title}")
                    print(f"Size: {human_size(os.path.getsize(video_path))}")
                    if os.path.exists(thumb_path):
                        print(f"Thumbnail: {human_size(os.path.getsize(thumb_path))}")
                    else:
                        thumb_path = None
                    yt_caption = f"**{title}**\n"
                    if uploader:
                        yt_caption += f"Channel: {uploader}\n"
                    if views:
                        yt_caption += f"Views: {views}\n"
                    if duration:
                        yt_caption += f"Duration: {format_time(duration)}\n"
                    yt_caption += f"Link: {url}"
                    return video_path, thumb_path, yt_caption, duration
                else:
                    print(f"  Client {client_name} returned empty file")
        except Exception as e:
            err = str(e)[:100]
            print(f"  Client {client_name} failed: {err}")
            continue
    return None

async def download_youtube_api(url):
    video_id = get_youtube_id(url)
    print(f"\nTrying API method for: {video_id}")
    piped_instances = [
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.adminforge.de",
        "https://pipedapi.r4fo.com",
        "https://pipedapi.darkness.services",
        "https://api.piped.privacydev.net",
        "https://watchapi.whatever.social",
    ]
    for pinst in piped_instances:
        try:
            api = f"{pinst}/streams/{video_id}"
            print(f"Trying Piped: {pinst}")
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(api, ssl=False) as resp:
                    status = resp.status
                    if status != 200:
                        body = await resp.text()
                        print(f"  Status: {status} - {body[:80]}")
                        continue
                    data = await resp.json()
                    title = data.get("title", "YouTube Video")
                    author = data.get("uploader", "")
                    duration = data.get("duration", 0)
                    views = data.get("views", 0)
                    vstreams = data.get("videoStreams", [])
                    best = None
                    best_h = 0
                    for vs in vstreams:
                        vurl = vs.get("url", "")
                        mime = vs.get("mimeType", "")
                        h = vs.get("height", 0)
                        if "mp4" in mime and vurl and h <= 1080 and h > best_h:
                            best_h = h
                            best = vs
                    if not best:
                        for vs in vstreams:
                            vurl = vs.get("url", "")
                            if vurl and not vs.get("videoOnly", False):
                                best = vs
                                break
                    if not best or not best.get("url"):
                        print(f"  No video stream found")
                        continue
                    download_url = best["url"]
                    print(f"  Found: {best.get('quality','')} {best.get('mimeType','')}")
                    filename = f"yt_{video_id}.mp4"
                    filepath = os.path.join("/tmp", filename)
                    print(f"  Downloading: {title}")
                    dl_timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)
                    async with aiohttp.ClientSession(timeout=dl_timeout) as dl_session:
                        async with dl_session.get(download_url, ssl=False) as dl_resp:
                            if dl_resp.status != 200:
                                print(f"  Download failed: HTTP {dl_resp.status}")
                                continue
                            total = int(dl_resp.headers.get("content-length", 0))
                            print(f"  Size: {human_size(total) if total else 'Unknown'}")
                            dl = 0
                            t0 = time.time()
                            async with aiofiles.open(filepath, "wb") as f:
                                async for chunk in dl_resp.content.iter_chunked(CHUNK_SIZE):
                                    await f.write(chunk)
                                    dl += len(chunk)
                                    if total:
                                        p = dl * 100 / total
                                        el = time.time() - t0
                                        sp = dl / el if el > 0 else 0
                                        eta = (total - dl) / sp if sp > 0 else 0
                                        fi = int(p / 2)
                                        bar = "#" * fi + "-" * (50 - fi)
                                        print(f"\r  YT |{bar}| {p:.1f}% {human_size(int(sp))}/s ETA:{format_time(eta)}", end="", flush=True)
                            print()
                    size = os.path.getsize(filepath)
                    if size < 10000:
                        print(f"  File too small: {size} bytes")
                        os.remove(filepath)
                        continue
                    print(f"  Downloaded: {human_size(size)}")
                    thumb_path = f"/tmp/yt_{video_id}_thumb.jpg"
                    thumb_urls = [
                        f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                        f"https://img.youtube.com/vi/{video_id}/sddefault.jpg",
                        f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                    ]
                    thumb_found = False
                    async with aiohttp.ClientSession() as ts:
                        for turl in thumb_urls:
                            try:
                                async with ts.get(turl) as tr:
                                    if tr.status == 200:
                                        tdata = await tr.read()
                                        if len(tdata) > 5000:
                                            async with aiofiles.open(thumb_path, "wb") as tf:
                                                await tf.write(tdata)
                                            print(f"  Thumbnail: {human_size(len(tdata))}")
                                            thumb_found = True
                                            break
                            except Exception:
                                continue
                    if not thumb_found:
                        thumb_path = None
                    yt_caption = f"**{title}**\n"
                    if author:
                        yt_caption += f"Channel: {author}\n"
                    if views:
                        yt_caption += f"Views: {views}\n"
                    if duration:
                        yt_caption += f"Duration: {format_time(duration)}\n"
                    yt_caption += f"Link: {url}"
                    return filepath, thumb_path, yt_caption, duration
        except Exception as e:
            print(f"  Failed: {e}")
            continue
    return None

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
    print("Telegram Uploader v7 (Multi-Method YouTube)")
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
                    print("\n--- Method 1: yt-dlp (5 clients) ---")
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(None, download_youtube_ytdlp, link)
                    if result:
                        filepath, thumb, yt_caption, duration = result
                        if not CAPTION:
                            caption = yt_caption
                    else:
                        print("\n--- Method 2: Piped API (6 instances) ---")
                        result = await download_youtube_api(link)
                        if result:
                            filepath, thumb, yt_caption, duration = result
                            if not CAPTION:
                                caption = yt_caption
                        else:
                            raise Exception("All YouTube download methods failed! YouTube ne cloud IPs block kiye hain. Direct URL ya Telegram link use karo.")
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
