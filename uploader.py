import os
import sys
import time
import asyncio
import aiohttp
import aiofiles
import subprocess
import random
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
            chat_id = int("-100" + parts[1])
            msg_id = int(parts[2])
            return chat_id, msg_id
        elif len(parts) >= 2:
            chat_name = parts[0]
            msg_id = int(parts[1])
            return chat_name, msg_id
    except:
        pass
    return None, None

def get_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path)
    filename = os.path.basename(path)
    if not filename or '.' not in filename:
        filename = "downloaded_file"
    return filename

def human_size(size_bytes: int) -> str:
    if size_bytes == 0: return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024; i += 1
    return f"{size:.2f} {units[i]}"

def progress_bar(current, total, prefix=""):
    if total > 0:
        percent = current * 100 / total
        filled = int(percent / 2)
        bar = "█" * filled + "░" * (50 - filled)
        print(f"\r{prefix} |{bar}| {percent:.1f}% ({human_size(current)}/{human_size(total)})", end="", flush=True)

# --- THUMBNAIL FIX ---
def get_video_metadata(filepath):
    """ffprobe se duration, width, height nikalega"""
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,duration", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1", filepath]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        out = result.stdout
        width, height, duration = 0, 0, 0
        for line in out.splitlines():
            if "width=" in line: width = int(line.split("=")[1])
            if "height=" in line: height = int(line.split("=")[1])
            if "duration=" in line: 
                try: duration = int(float(line.split("=")[1]))
                except: pass
        return duration, width, height
    except: return 0, 0, 0

def generate_thumbnail(filepath):
    """video se 1 sec pe thumbnail banayega"""
    thumb_path = "/tmp/thumb.jpg"
    try:
        if os.path.exists(thumb_path): os.remove(thumb_path)
        # 1 sec par se ek frame nikal ke 320px thumbnail banao
        cmd = ["ffmpeg", "-y", "-i", filepath, "-ss", "00:00:01.000", "-vframes", "1", "-vf", "scale=320:320:force_original_aspect_ratio=decrease", "-q:v", "2", thumb_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            print(f"\n🖼️ Thumbnail generated: {human_size(os.path.getsize(thumb_path))}")
            return thumb_path
    except Exception as e:
        print(f"\n⚠️ Thumbnail failed: {e}")
    return None

def make_progress_callback(prefix: str):
    last_time = [0]
    def callback(current, total):
        now = time.time()
        if now - last_time[0] >= 2 or current == total:
            last_time[0] = now
            progress_bar(current, total, prefix)
    return callback

async def download_file(url: str, filename: str) -> str:
    filepath = os.path.join("/tmp", filename)
    print(f"\n📥 Downloading: {filename}")
    timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, allow_redirects=True) as response:
            if response.status != 200: raise Exception(f"Download failed! HTTP Status: {response.status}")
            total_size = int(response.headers.get('content-length', 0))
            print(f"📦 File size: {human_size(total_size) if total_size else 'Unknown'}")
            downloaded = 0
            start_time = time.time()
            async with aiofiles.open(filepath, 'wb') as f:
                async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                    await f.write(chunk); downloaded += len(chunk)
                    if total_size > 0: progress_bar(downloaded, total_size, "📥 Download")
    elapsed = time.time() - start_time
    file_size = os.path.getsize(filepath)
    print(f"\n✅ Download complete! {human_size(file_size)} in {elapsed:.1f}s")
    return filepath

async def handle_telegram_link(app: Client, chat_id, msg_id: int) -> str:
    print(f"\n📱 Fetching Telegram message: {chat_id} / {msg_id}")
    messages = await app.get_messages(chat_id, msg_id)
    if not messages: raise Exception("Message not found!")
    msg = messages
    if msg.media:
        print(f"📎 Media found! Downloading from Telegram...")
        filepath = await app.download_media(msg, file_name="/tmp/", progress=make_progress_callback("📥 TG Download"))
        print(f"\n✅ Downloaded: {filepath}")
        return filepath
    elif msg.text and msg.text.strip().startswith("http"):
        filename = CUSTOM_FILENAME if CUSTOM_FILENAME else get_filename_from_url(msg.text.strip())
        return await download_file(msg.text.strip(), filename)
    else: raise Exception("Message has no media!")

async def upload_to_saved_messages(app: Client, filepath: str, caption: str):
    file_size = os.path.getsize(filepath)
    filename = os.path.basename(filepath)
    print(f"\n📤 Uploading to Saved Messages... {filename} ({human_size(file_size)})")
    ext = filename.lower().split('.')[-1] if '.' in filename else ''
    video_exts = {'mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'wmv', '3gp'}
    audio_exts = {'mp3', 'flac', 'wav', 'aac', 'ogg', 'm4a', 'wma', 'opus'}
    photo_exts = {'jpg', 'jpeg', 'png', 'bmp', 'webp'}
    start_time = time.time()
    me = await app.get_me()
    final_caption = caption if caption else filename

    thumb = None
    duration = 0
    width = 0
    height = 0

    try:
        if ext in video_exts:
            print(f"🎬 Video detected, generating thumbnail...")
            duration, width, height = get_video_metadata(filepath)
            print(f"   Duration: {duration}s, Resolution: {width}x{height}")
            thumb = generate_thumbnail(filepath)
            print(f"🎬 Sending as VIDEO...")
            await app.send_video(chat_id="me", video=filepath, caption=final_caption, file_name=filename, thumb=thumb, duration=duration, width=width, height=height, supports_streaming=True, progress=make_progress_callback("📤 Upload"))
        elif ext in audio_exts:
            await app.send_audio(chat_id="me", audio=filepath, caption=final_caption, file_name=filename, progress=make_progress_callback("📤 Upload"))
        elif ext in photo_exts and file_size < 10 * 1024 * 1024:
            await app.send_photo(chat_id="me", photo=filepath, caption=final_caption, progress=make_progress_callback("📤 Upload"))
        else:
            await app.send_document(chat_id="me", document=filepath, caption=final_caption, file_name=filename, force_document=True, thumb=thumb, progress=make_progress_callback("📤 Upload"))
    except FloodWait as e:
        print(f"\n⏳ FloodWait: {e.value}s"); await asyncio.sleep(e.value + 1)
        await app.send_document(chat_id="me", document=filepath, caption=final_caption, file_name=filename, force_document=True, thumb=thumb, progress=make_progress_callback("📤 Upload (Retry)"))
    
    elapsed = time.time() - start_time
    print(f"\n\n✅ Upload complete! Speed: {human_size(int(file_size/elapsed))}/s Time: {elapsed:.1f}s")
    if thumb and os.path.exists(thumb): os.remove(thumb)

async def main():
    print("="*60 + "\n🚀 Telegram File Uploader with Thumbnail Fix\n" + "="*60)
    if not SESSION_STRING or not LINK:
        print("❌ SESSION_STRING or LINK missing!"); sys.exit(1)
    app = Client(name="uploader_bot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True, workers=8)
    async with app:
        me = await app.get_me()
        print(f"\n✅ Logged in as: {me.first_name}")
        chat_id, msg_id = parse_telegram_link(LINK)
        if chat_id is not None and msg_id is not None:
            filepath = await handle_telegram_link(app, chat_id, msg_id)
        elif LINK.startswith("http"):
            filename = CUSTOM_FILENAME if CUSTOM_FILENAME else get_filename_from_url(LINK)
            filepath = await download_file(LINK, filename)
        else: print("❌ Invalid link"); sys.exit(1)

        if CUSTOM_FILENAME and os.path.basename(filepath) != CUSTOM_FILENAME:
            new_path = os.path.join(os.path.dirname(filepath), CUSTOM_FILENAME)
            os.rename(filepath, new_path); filepath = new_path
        await upload_to_saved_messages(app, filepath, CAPTION)
        try: os.remove(filepath)
        except: pass
    print("\n🎉 All done!")

if __name__ == "__main__":
    asyncio.run(main())
