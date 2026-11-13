import os
import sys
import time
import asyncio
import aiohttp
import aiofiles
import subprocess
import re
from urllib.parse import urlparse, unquote
from pyrogram import Client
from pyrogram.errors import FloodWait, UserDeactivated, AuthKeyUnregistered

# ─── CONFIG ───
API_ID = 6
API_HASH = "eb06d4abfb49dc3eeb1aeb98ae0f581e"
WORKERS = 32
CHUNK_SIZE = 20 * 1024 * 1024  # 20MB
PARALLEL_PARTS = 6
MAX_RETRIES = 5
TELEGRAM_LIMIT = 2 * 1024 * 1024 * 1024  # 2GB

SESSION_STRING = os.environ.get("SESSION_STRING", "")
LINKS_RAW = os.environ.get("LINKS", "")
CAPTION = os.environ.get("CAPTION", "")
CUSTOM_FILENAME = os.environ.get("FILENAME", "")


# ─── HELPERS ───
def parse_links(raw: str) -> list:
    """Comma ya newline se multiple links parse karega"""
    links = []
    for line in raw.replace(",", "\n").splitlines():
        link = line.strip()
        if link and (link.startswith("http") or "t.me/" in link):
            links.append(link)
    return links


def parse_telegram_link(link: str):
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
    except:
        pass
    return None, None


def get_filename_from_url(url: str) -> str:
    fn = os.path.basename(unquote(urlparse(url).path))
    return fn if fn and '.' in fn else "file"


def human_size(b: int) -> str:
    if b == 0: return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0; s = float(b)
    while s >= 1024 and i < 4: s /= 1024; i += 1
    return f"{s:.2f} {units[i]}"


def format_time(seconds: float) -> str:
    if seconds < 60: return f"{seconds:.0f}s"
    if seconds < 3600: return f"{seconds/60:.1f}m"
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
                print(f"\r{prefix} |{'█'*f}{'░'*(50-f)}| {p:.1f}% ({human_size(cur)}/{human_size(tot)})", end="", flush=True)
    return cb


# ─── PARALLEL DOWNLOAD (SPEED BOOST) ───
async def download_part(session, url, start, end, part_path, sem):
    """Ek part download karega (range request)"""
    async with sem:
        headers = {"Range": f"bytes={start}-{end}"}
        for attempt in range(MAX_RETRIES):
            try:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=None, sock_read=120)) as resp:
                    if resp.status in (200, 206):
                        async with aiofiles.open(part_path, 'wb') as f:
                            async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                                await f.write(chunk)
                        return os.path.getsize(part_path)
            except Exception as e:
                wait = 2 ** attempt
                print(f"\n⚠️ Part retry {attempt+1}/{MAX_RETRIES} (wait {wait}s): {e}")
                await asyncio.sleep(wait)
        return 0


async def parallel_download(url: str, filename: str) -> str:
    """File ko multiple parts me ek saath download karega"""
    filepath = os.path.join("/tmp", filename)
    print(f"\n📥 Parallel Download: {filename}")

    timeout = aiohttp.ClientTimeout(total=None, connect=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Pehle file size pata karo
        async with session.head(url, allow_redirects=True) as resp:
            total = int(resp.headers.get('content-length', 0))
            accept_range = resp.headers.get('accept-ranges', '')

        if total == 0 or 'bytes' not in accept_range:
            print("⚠️ Range requests not supported, falling back to single download")
            return await single_download(url, filename)

        print(f"📦 Size: {human_size(total)} | Parts: {PARALLEL_PARTS}")

        # Parts me divide karo
        part_size = total // PARALLEL_PARTS
        parts = []
        for i in range(PARALLEL_PARTS):
            start = i * part_size
            end = (i + 1) * part_size - 1 if i < PARALLEL_PARTS - 1 else total - 1
            part_path = f"{filepath}.part{i}"
            parts.append((start, end, part_path))

        # Parallel download
        sem = asyncio.Semaphore(PARALLEL_PARTS)
        t0 = time.time()
        tasks = [download_part(session, url, s, e, p, sem) for s, e, p in parts]
        results = await asyncio.gather(*tasks)

        # Merge parts
        print(f"\n🔗 Merging {PARALLEL_PARTS} parts...")
        async with aiofiles.open(filepath, 'wb') as out:
            for _, _, part_path in parts:
                if os.path.exists(part_path):
                    async with aiofiles.open(part_path, 'rb') as pf:
                        while True:
                            chunk = await pf.read(CHUNK_SIZE)
                            if not chunk: break
                            await out.write(chunk)
                    os.remove(part_path)

    elapsed = time.time() - t0
    size = os.path.getsize(filepath)
    speed = size / elapsed if elapsed > 0 else 0
    print(f"✅ Done! {human_size(size)} in {format_time(elapsed)} ({human_size(int(speed))}/s)")
    return filepath


async def single_download(url: str, filename: str) -> str:
    """Normal single-stream download (fallback)"""
    filepath = os.path.join("/tmp", filename)
    timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt in range(MAX_RETRIES):
            try:
                async with session.get(url, allow_redirects=True) as resp:
                    if resp.status != 200:
                        raise Exception(f"HTTP {resp.status}")
                    total = int(resp.headers.get('content-length', 0))
                    print(f"📦 Size: {human_size(total) if total else 'Unknown'}")
                    dl = 0; t0 = time.time()
                    async with aiofiles.open(filepath, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                            await f.write(chunk); dl += len(chunk)
                            if total:
                                p = dl * 100 / total
                                elapsed = time.time() - t0
                                speed = dl / elapsed if elapsed > 0 else 0
                                eta = (total - dl) / speed if speed > 0 else 0
                                filled = int(p / 2)
                                print(f"\r📥 |{'█'*filled}{'░'*(50-filled)}| {p:.1f}% {human_size(int(speed))}/s ETA:{format_time(eta)}", end="", flush=True)
                    print()
                    return filepath
            except Exception as e:
                wait = 2 ** attempt
                print(f"\n⚠️ Retry {attempt+1}/{MAX_RETRIES} (wait {wait}s): {e}")
                await asyncio.sleep(wait)
    raise Exception("Download failed after all retries!")


# ─── THUMBNAIL ───
def get_video_metadata(filepath):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,duration",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1", filepath],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15
        )
        w = h = d = 0
        for line in r.stdout.splitlines():
            if "width=" in line: w = int(line.split("=")[1])
            if "height=" in line: h = int(line.split("=")[1])
            if "duration=" in line:
                try: d = int(float(line.split("=")[1]))
                except: pass
        return d, w, h
    except:
        return 0, 0, 0


def generate_thumbnail_ffmpeg(filepath):
    thumb = "/tmp/thumb_ffmpeg.jpg"
    try:
        if os.path.exists(thumb): os.remove(thumb)
        subprocess.run(
            ["ffmpeg", "-y", "-i", filepath, "-ss", "00:00:01.000",
             "-vframes", "1", "-vf", "scale=320:-1", "-q:v", "2", thumb],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20
        )
        if os.path.exists(thumb) and os.path.getsize(thumb) > 0:
            return thumb
    except:
        pass
    return None


async def download_original_thumb(app, msg):
    thumbs = None
    if msg.video and msg.video.thumbs: thumbs = msg.video.thumbs
    elif msg.document and msg.document.thumbs: thumbs = msg.document.thumbs
    elif msg.animation and msg.animation.thumbs: thumbs = msg.animation.thumbs
    if not thumbs: return None
    best = thumbs[-1]
    print(f"🖼️ Original thumb: {best.width}x{best.height}")
    try:
        path = await app.download_media(best, file_name="/tmp/orig_thumb_")
        if path and os.path.exists(path) and os.path.getsize(path) > 0:
            if path.endswith(".webp"):
                jpg = "/tmp/orig_thumb.jpg"
                subprocess.run(["ffmpeg", "-y", "-i", path, jpg],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                if os.path.exists(jpg): os.remove(path); path = jpg
            return path
    except:
        pass
    return None


# ─── FILE SPLIT (>2GB) ───
def split_file(filepath, max_size=TELEGRAM_LIMIT):
    """Badi file ko 2GB parts me split karega"""
    size = os.path.getsize(filepath)
    if size <= max_size:
        return [filepath]

    print(f"\n✂️ File too large ({human_size(size)}), splitting into parts...")
    parts = []
    part_num = 1
    base, ext = os.path.splitext(filepath)

    with open(filepath, 'rb') as f:
        while True:
            part_path = f"{base}.part{part_num:03d}{ext}"
            written = 0
            with open(part_path, 'wb') as pf:
                while written < max_size:
                    chunk = f.read(min(CHUNK_SIZE, max_size - written))
                    if not chunk: break
                    pf.write(chunk)
                    written += len(chunk)
            if written > 0:
                parts.append(part_path)
                print(f"   Part {part_num}: {human_size(written)}")
                part_num += 1
            else:
                break

    print(f"✅ Split into {len(parts)} parts")
    return parts


# ─── UPLOAD ───
async def upload_single_file(app, filepath, caption, orig_thumb=None, part_info=""):
    """Ek file upload karega with retry"""
    file_size = os.path.getsize(filepath)
    filename = os.path.basename(filepath)
    ext = filename.lower().split('.')[-1] if '.' in filename else ''

    video_exts = {'mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'wmv', '3gp'}
    audio_exts = {'mp3', 'flac', 'wav', 'aac', 'ogg', 'm4a', 'wma', 'opus'}
    photo_exts = {'jpg', 'jpeg', 'png', 'bmp', 'webp'}

    final_caption = caption if caption else filename
    if part_info:
        final_caption = f"{final_caption}\n{part_info}"

    thumb = None
    duration = width = height = 0

    for attempt in range(MAX_RETRIES):
        try:
            if ext in video_exts:
                duration, width, height = get_video_metadata(filepath)
                if orig_thumb and os.path.exists(orig_thumb):
                    thumb = orig_thumb
                else:
                    thumb = generate_thumbnail_ffmpeg(filepath)
                await app.send_video(
                    chat_id="me", video=filepath,
                    caption=final_caption, file_name=filename,
                    thumb=thumb, duration=duration,
                    width=width, height=height,
                    supports_streaming=True,
                    progress=make_progress_cb(f"📤 {part_info or 'Upload'}")
                )
            elif ext in audio_exts:
                await app.send_audio(
                    chat_id="me", audio=filepath,
                    caption=final_caption, file_name=filename,
                    progress=make_progress_cb(f"📤 {part_info or 'Upload'}")
                )
            elif ext in photo_exts and file_size < 10 * 1024 * 1024:
                await app.send_photo(
                    chat_id="me", photo=filepath,
                    caption=final_caption,
                    progress=make_progress_cb(f"📤 {part_info or 'Upload'}")
                )
            else:
                if orig_thumb and os.path.exists(orig_thumb):
                    thumb = orig_thumb
                await app.send_document(
                    chat_id="me", document=filepath,
                    caption=final_caption, file_name=filename,
                    force_document=True, thumb=thumb,
                    progress=make_progress_cb(f"📤 {part_info or 'Upload'}")
                )
            return True  # Success

        except FloodWait as e:
            print(f"\n⏳ FloodWait: {e.value}s")
            await asyncio.sleep(e.value + 2)
        except Exception as e:
            wait = 2 ** attempt
            print(f"\n⚠️ Upload retry {attempt+1}/{MAX_RETRIES} (wait {wait}s): {e}")
            await asyncio.sleep(wait)

    print(f"❌ Upload failed after {MAX_RETRIES} retries: {filename}")
    return False


# ─── MAIN ───
async def main():
    print("=" * 60)
    print("🚀 Telegram Uploader v2 (Enhanced)")
    print("=" * 60)

    if not SESSION_STRING:
        print("❌ SESSION_STRING missing!"); sys.exit(1)

    links = parse_links(LINKS_RAW)
    if not links:
        print("❌ No valid links found!"); sys.exit(1)

    print(f"\n📋 Total links: {len(links)}")
    for i, l in enumerate(links, 1):
        print(f"   {i}. {l[:80]}...")

    app = Client(
        "uploader", api_id=API_ID, api_hash=API_HASH,
        session_string=SESSION_STRING, in_memory=True,
        workers=WORKERS
    )

    async with app:
        # Session validation
        try:
            me = await app.get_me()
            print(f"\n✅ Logged in: {me.first_name} (ID: {me.id})")
        except (UserDeactivated, AuthKeyUnregistered) as e:
            print(f"❌ Session invalid: {e}")
            print("   Naya session string generate karo!")
            sys.exit(1)

        total_start = time.time()
        success = 0
        failed = 0

        for idx, link in enumerate(links, 1):
            print(f"\n{'='*60}")
            print(f"📌 [{idx}/{len(links)}] Processing: {link[:70]}")
            print(f"{'='*60}")

            orig_thumb = None
            filepath = None

            try:
                chat_id, msg_id = parse_telegram_link(link)

                if chat_id is not None and msg_id is not None:
                    msg = await app.get_messages(chat_id, msg_id)
                    if not msg: raise Exception("Message not found!")
                    if msg.media:
                        orig_thumb = await download_original_thumb(app, msg)
                        filepath = await app.download_media(
                            msg, file_name="/tmp/",
                            progress=make_progress_cb("📥 TG Download")
                        )
                    elif msg.text and msg.text.strip().startswith("http"):
                        fn = get_filename_from_url(msg.text.strip())
                        filepath = await parallel_download(msg.text.strip(), fn)
                    else:
                        raise Exception("No media!")

                elif link.startswith("http"):
                    fn = CUSTOM_FILENAME if (CUSTOM_FILENAME and len(links) == 1) else get_filename_from_url(link)
                    filepath = await parallel_download(link, fn)
                else:
                    print(f"❌ Invalid link format"); failed += 1; continue

                if not filepath or not os.path.exists(filepath):
                    raise Exception("File not found after download!")

                # Rename if single file + custom name
                if CUSTOM_FILENAME and len(links) == 1:
                    np = os.path.join(os.path.dirname(filepath), CUSTOM_FILENAME)
                    if np != filepath:
                        os.rename(filepath, np); filepath = np

                # Split if >2GB
                parts = split_file(filepath)

                for pi, part in enumerate(parts):
                    part_info = f"Part {pi+1}/{len(parts)}" if len(parts) > 1 else ""
                    ok = await upload_single_file(app, part, CAPTION, orig_thumb, part_info)
                    if ok:
                        success += 1
                    else:
                        failed += 1
                    # Cleanup part
                    if part != filepath and os.path.exists(part):
                        os.remove(part)

            except Exception as e:
                print(f"❌ Error: {e}")
                failed += 1

            # Cleanup
            if filepath and os.path.exists(filepath):
                try: os.remove(filepath)
                except: pass
            if orig_thumb and os.path.exists(orig_thumb):
                try: os.remove(orig_thumb)
                except: pass

        total_time = time.time() - total_start
        print(f"\n{'='*60}")
        print(f"🎉 ALL DONE!")
        print(f"   ✅ Success: {success}")
        print(f"   ❌ Failed:  {failed}")
        print(f"   ⏱️ Total:   {format_time(total_time)}")
        print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
