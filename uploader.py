import os
import sys
import time
import asyncio
import aiohttp
import aiofiles
import subprocess
from urllib.parse import urlparse, unquote
from pyrogram import Client
from pyrogram.errors import FloodWait

API_ID = 6
API_HASH = "eb06d4abfb49dc3eeb1aeb98ae0f581e"

SESSION_STRING = os.environ.get("SESSION_STRING", "")
LINK = os.environ.get("LINK", "")
CAPTION = os.environ.get("CAPTION", "")
CUSTOM_FILENAME = os.environ.get("FILENAME", "")

CHUNK_SIZE = 10 * 1024 * 1024


def parse_telegram_link(link: str):
    link = link.strip()
    if link.startswith("t.me/"):
        link = "https://" + link
    if "t.me/" not in link:
        return None, None
    try:
        parsed = urlparse(link)
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "c":
            return int("-100" + parts[1]), int(parts[2])
        elif len(parts) >= 2:
            return parts[0], int(parts[1])
    except:
        pass
    return None, None


def get_filename_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    fn = os.path.basename(path)
    return fn if fn and '.' in fn else "downloaded_file"


def human_size(b: int) -> str:
    if b == 0: return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0; s = float(b)
    while s >= 1024 and i < 4: s /= 1024; i += 1
    return f"{s:.2f} {units[i]}"


def progress_bar(current, total, prefix=""):
    if total > 0:
        p = current * 100 / total
        f = int(p / 2)
        print(f"\r{prefix} |{'█'*f}{'░'*(50-f)}| {p:.1f}% ({human_size(current)}/{human_size(total)})", end="", flush=True)


def make_progress_cb(prefix):
    last = [0]
    def cb(cur, tot):
        now = time.time()
        if now - last[0] >= 2 or cur == tot:
            last[0] = now
            progress_bar(cur, tot, prefix)
    return cb


# ───────── THUMBNAIL HELPERS ─────────

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
    """Fallback: video ke andar se frame nikalega"""
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
    """
    Telegram post se ORIGINAL thumbnail download karega.
    msg.video.thumbs / msg.document.thumbs / msg.animation.thumbs se.
    """
    thumbs = None

    if msg.video and msg.video.thumbs:
        thumbs = msg.video.thumbs
    elif msg.document and msg.document.thumbs:
        thumbs = msg.document.thumbs
    elif msg.animation and msg.animation.thumbs:
        thumbs = msg.animation.thumbs

    if not thumbs:
        print("⚠️ Original thumbnail nahi mila post me")
        return None

    # Sabse bada thumbnail lo (last element = highest resolution)
    best_thumb = thumbs[-1]
    print(f"🖼️ Original thumbnail mila: {best_thumb.width}x{best_thumb.height}")

    try:
        thumb_path = await app.download_media(best_thumb, file_name="/tmp/orig_thumb_")
        if thumb_path and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            # .webp ko .jpg me convert karo (Telegram kabhi kabhi webp deta hai)
            if thumb_path.endswith(".webp"):
                jpg_path = "/tmp/orig_thumb.jpg"
                subprocess.run(
                    ["ffmpeg", "-y", "-i", thumb_path, jpg_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
                )
                if os.path.exists(jpg_path):
                    os.remove(thumb_path)
                    thumb_path = jpg_path
            print(f"✅ Original thumbnail downloaded: {human_size(os.path.getsize(thumb_path))}")
            return thumb_path
    except Exception as e:
        print(f"⚠️ Thumbnail download fail: {e}")

    return None


# ───────── DOWNLOAD ─────────

async def download_file(url: str, filename: str) -> str:
    filepath = os.path.join("/tmp", filename)
    print(f"\n📥 Downloading: {filename}")
    timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            total = int(resp.headers.get('content-length', 0))
            print(f"📦 Size: {human_size(total) if total else 'Unknown'}")
            dl = 0; t0 = time.time()
            async with aiofiles.open(filepath, 'wb') as f:
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    await f.write(chunk); dl += len(chunk)
                    if total: progress_bar(dl, total, "📥 Download")
    print(f"\n✅ Done! {human_size(os.path.getsize(filepath))} in {time.time()-t0:.1f}s")
    return filepath


# ───────── TELEGRAM LINK HANDLER ─────────

async def handle_telegram_link(app, chat_id, msg_id):
    """Returns (filepath, original_thumb_path)"""
    print(f"\n📱 Fetching: {chat_id} / {msg_id}")
    msg = await app.get_messages(chat_id, msg_id)
    if not msg: raise Exception("Message not found!")

    filepath = None
    orig_thumb = None

    if msg.media:
        # Pehle original thumbnail download karo
        orig_thumb = await download_original_thumb(app, msg)

        # Phir media download karo
        print("📎 Downloading media...")
        filepath = await app.download_media(
            msg, file_name="/tmp/",
            progress=make_progress_cb("📥 TG Download")
        )
        print(f"\n✅ Media downloaded: {filepath}")

    elif msg.text and msg.text.strip().startswith("http"):
        fn = CUSTOM_FILENAME if CUSTOM_FILENAME else get_filename_from_url(msg.text.strip())
        filepath = await download_file(msg.text.strip(), fn)
    else:
        raise Exception("No media found!")

    return filepath, orig_thumb


# ───────── UPLOAD ─────────

async def upload_to_saved_messages(app, filepath, caption, orig_thumb=None):
    file_size = os.path.getsize(filepath)
    filename = os.path.basename(filepath)
    ext = filename.lower().split('.')[-1] if '.' in filename else ''
    print(f"\n📤 Uploading: {filename} ({human_size(file_size)})")

    video_exts = {'mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'wmv', '3gp'}
    audio_exts = {'mp3', 'flac', 'wav', 'aac', 'ogg', 'm4a', 'wma', 'opus'}
    photo_exts = {'jpg', 'jpeg', 'png', 'bmp', 'webp'}

    final_caption = caption if caption else filename
    thumb = None
    duration = width = height = 0

    try:
        if ext in video_exts:
            duration, width, height = get_video_metadata(filepath)
            print(f"   Duration: {duration}s | {width}x{height}")

            # ✅ Pehle ORIGINAL thumbnail use karo
            if orig_thumb and os.path.exists(orig_thumb):
                thumb = orig_thumb
                print(f"🖼️ Using ORIGINAL thumbnail from source")
            else:
                # Fallback: ffmpeg se banao
                print(f"🖼️ Original nahi mila, ffmpeg se bana raha hu...")
                thumb = generate_thumbnail_ffmpeg(filepath)

            await app.send_video(
                chat_id="me", video=filepath,
                caption=final_caption, file_name=filename,
                thumb=thumb, duration=duration,
                width=width, height=height,
                supports_streaming=True,
                progress=make_progress_cb("📤 Upload")
            )

        elif ext in audio_exts:
            await app.send_audio(
                chat_id="me", audio=filepath,
                caption=final_caption, file_name=filename,
                progress=make_progress_cb("📤 Upload")
            )

        elif ext in photo_exts and file_size < 10 * 1024 * 1024:
            await app.send_photo(
                chat_id="me", photo=filepath,
                caption=final_caption,
                progress=make_progress_cb("📤 Upload")
            )

        else:
            if orig_thumb and os.path.exists(orig_thumb):
                thumb = orig_thumb
            await app.send_document(
                chat_id="me", document=filepath,
                caption=final_caption, file_name=filename,
                force_document=True, thumb=thumb,
                progress=make_progress_cb("📤 Upload")
            )

    except FloodWait as e:
        print(f"\n⏳ FloodWait: {e.value}s")
        await asyncio.sleep(e.value + 1)
        await app.send_document(
            chat_id="me", document=filepath,
            caption=final_caption, file_name=filename,
            force_document=True, thumb=thumb,
            progress=make_progress_cb("📤 Retry")
        )

    # Cleanup thumbs
    for t in [thumb, orig_thumb]:
        if t and os.path.exists(t):
            try: os.remove(t)
            except: pass


# ───────── MAIN ─────────

async def main():
    print("=" * 60)
    print("🚀 Telegram Uploader (Original Thumbnail Support)")
    print("=" * 60)

    if not SESSION_STRING or not LINK:
        print("❌ SESSION_STRING ya LINK missing!"); sys.exit(1)

    app = Client(
        "uploader", api_id=API_ID, api_hash=API_HASH,
        session_string=SESSION_STRING, in_memory=True, workers=8
    )

    async with app:
        me = await app.get_me()
        print(f"\n✅ Logged in: {me.first_name}")

        orig_thumb = None

        chat_id, msg_id = parse_telegram_link(LINK)
        if chat_id is not None and msg_id is not None:
            filepath, orig_thumb = await handle_telegram_link(app, chat_id, msg_id)
        elif LINK.startswith("http"):
            fn = CUSTOM_FILENAME if CUSTOM_FILENAME else get_filename_from_url(LINK)
            filepath = await download_file(LINK, fn)
        else:
            print("❌ Invalid link!"); sys.exit(1)

        if CUSTOM_FILENAME and os.path.basename(filepath) != CUSTOM_FILENAME:
            np = os.path.join(os.path.dirname(filepath), CUSTOM_FILENAME)
            os.rename(filepath, np); filepath = np

        await upload_to_saved_messages(app, filepath, CAPTION, orig_thumb)

        try: os.remove(filepath)
        except: pass

    print("\n🎉 Done!")


if __name__ == "__main__":
    asyncio.run(main())
