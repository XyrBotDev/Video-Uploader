import os
import sys
import time
import asyncio
import aiohttp
import aiofiles
from urllib.parse import urlparse, unquote
from pyrogram import Client
from pyrogram.errors import FloodWait

# ──────────────────────────────────────────────
# Telegram Official App Credentials (Hardcoded)
# ──────────────────────────────────────────────
API_ID = 6  # Telegram official Android
API_HASH = "eb06d4abfb49dc3eeb1aeb98ae0f581e"  # Telegram official Android

# ──────────────────────────────────────────────
# Environment Variables
# ──────────────────────────────────────────────
SESSION_STRING = os.environ.get("SESSION_STRING", "")
LINK = os.environ.get("LINK", "")
CAPTION = os.environ.get("CAPTION", "")
CUSTOM_FILENAME = os.environ.get("FILENAME", "")

# ──────────────────────────────────────────────
# Download chunk size (fast download - 10MB chunks)
# ──────────────────────────────────────────────
CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB


def parse_telegram_link(link: str):
    """
    Parse telegram post link like:
    - https://t.me/channel_name/123
    - https://t.me/c/1234567890/123 (private channel)
    - t.me/channel_name/123
    Returns (chat_identifier, message_id) or (None, None)
    """
    link = link.strip()
    
    # Normalize
    if link.startswith("t.me/"):
        link = "https://" + link
    
    if "t.me/" not in link:
        return None, None
    
    try:
        parsed = urlparse(link)
        parts = parsed.path.strip("/").split("/")
        
        if len(parts) >= 3 and parts[0] == "c":
            # Private channel: /c/1234567890/123
            chat_id = int("-100" + parts[1])
            msg_id = int(parts[2])
            return chat_id, msg_id
        elif len(parts) >= 2:
            # Public channel: /channel_name/123
            chat_name = parts[0]
            msg_id = int(parts[1])
            return chat_name, msg_id
    except (ValueError, IndexError):
        pass
    
    return None, None


def get_filename_from_url(url: str) -> str:
    """Extract filename from URL"""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    filename = os.path.basename(path)
    if not filename or '.' not in filename:
        filename = "downloaded_file"
    return filename


def human_size(size_bytes: int) -> str:
    """Convert bytes to human readable format"""
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"


def progress_bar(current, total, prefix=""):
    """Simple progress display"""
    if total > 0:
        percent = current * 100 / total
        filled = int(percent / 2)
        bar = "█" * filled + "░" * (50 - filled)
        print(f"\r{prefix} |{bar}| {percent:.1f}% ({human_size(current)}/{human_size(total)})", end="", flush=True)


async def download_file(url: str, filename: str) -> str:
    """Download file from direct URL with fast chunked download"""
    filepath = os.path.join("/tmp", filename)
    
    print(f"\n📥 Downloading: {filename}")
    print(f"🔗 URL: {url[:100]}...")
    
    timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, allow_redirects=True) as response:
            if response.status != 200:
                raise Exception(f"Download failed! HTTP Status: {response.status}")
            
            total_size = int(response.headers.get('content-length', 0))
            
            if total_size > 0:
                print(f"📦 File size: {human_size(total_size)}")
            else:
                print("📦 File size: Unknown")
            
            downloaded = 0
            start_time = time.time()
            
            async with aiofiles.open(filepath, 'wb') as f:
                async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                    await f.write(chunk)
                    downloaded += len(chunk)
                    
                    if total_size > 0:
                        progress_bar(downloaded, total_size, "📥 Download")
    
    elapsed = time.time() - start_time
    file_size = os.path.getsize(filepath)
    speed = file_size / elapsed if elapsed > 0 else 0
    
    print(f"\n✅ Download complete!")
    print(f"📦 Size: {human_size(file_size)}")
    print(f"⚡ Speed: {human_size(int(speed))}/s")
    print(f"⏱️ Time: {elapsed:.1f}s")
    
    return filepath


def make_progress_callback(prefix: str):
    """Create upload/download progress callback for pyrogram"""
    last_time = [0]
    
    def callback(current, total):
        now = time.time()
        if now - last_time[0] >= 2 or current == total:  # Update every 2 seconds
            last_time[0] = now
            progress_bar(current, total, prefix)
    
    return callback


async def handle_telegram_link(app: Client, chat_id, msg_id: int) -> str:
    """Download media from telegram post and forward/re-upload to Saved Messages"""
    
    print(f"\n📱 Fetching Telegram message...")
    print(f"   Chat: {chat_id}")
    print(f"   Message ID: {msg_id}")
    
    try:
        messages = await app.get_messages(chat_id, msg_id)
    except Exception as e:
        raise Exception(f"Cannot fetch message: {e}")
    
    if not messages:
        raise Exception("Message not found!")
    
    msg = messages
    
    # Check if message has downloadable media
    if msg.media:
        print(f"📎 Media found! Downloading from Telegram...")
        
        filepath = await app.download_media(
            msg,
            file_name="/tmp/",
            progress=make_progress_callback("📥 TG Download")
        )
        print(f"\n✅ Downloaded: {filepath}")
        return filepath
    
    elif msg.text:
        # If message contains a URL, download from that URL
        text = msg.text.strip()
        if text.startswith("http://") or text.startswith("https://"):
            filename = get_filename_from_url(text)
            if CUSTOM_FILENAME:
                filename = CUSTOM_FILENAME
            return await download_file(text, filename)
        else:
            raise Exception(f"Message has text but no URL: {text[:200]}")
    else:
        raise Exception("Message has no media or downloadable content!")


async def upload_to_saved_messages(app: Client, filepath: str, caption: str):
    """Upload file to Saved Messages with fast upload"""
    
    file_size = os.path.getsize(filepath)
    filename = os.path.basename(filepath)
    
    print(f"\n📤 Uploading to Saved Messages...")
    print(f"📄 File: {filename}")
    print(f"📦 Size: {human_size(file_size)}")
    
    # Determine file type by extension
    ext = filename.lower().split('.')[-1] if '.' in filename else ''
    
    video_exts = {'mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'wmv', '3gp'}
    audio_exts = {'mp3', 'flac', 'wav', 'aac', 'ogg', 'm4a', 'wma', 'opus'}
    photo_exts = {'jpg', 'jpeg', 'png', 'bmp', 'webp'}
    
    start_time = time.time()
    me = await app.get_me()
    
    final_caption = caption if caption else filename
    
    try:
        if ext in video_exts:
            print(f"🎬 Sending as VIDEO...")
            await app.send_video(
                chat_id="me",
                video=filepath,
                caption=final_caption,
                file_name=filename,
                supports_streaming=True,
                progress=make_progress_callback("📤 Upload")
            )
        
        elif ext in audio_exts:
            print(f"🎵 Sending as AUDIO...")
            await app.send_audio(
                chat_id="me",
                audio=filepath,
                caption=final_caption,
                file_name=filename,
                progress=make_progress_callback("📤 Upload")
            )
        
        elif ext in photo_exts and file_size < 10 * 1024 * 1024:
            print(f"🖼️ Sending as PHOTO...")
            await app.send_photo(
                chat_id="me",
                photo=filepath,
                caption=final_caption,
                progress=make_progress_callback("📤 Upload")
            )
        
        else:
            print(f"📄 Sending as DOCUMENT...")
            await app.send_document(
                chat_id="me",
                document=filepath,
                caption=final_caption,
                file_name=filename,
                force_document=True,
                progress=make_progress_callback("📤 Upload")
            )
    
    except FloodWait as e:
        print(f"\n⏳ FloodWait: Waiting {e.value} seconds...")
        await asyncio.sleep(e.value + 1)
        # Retry once
        await app.send_document(
            chat_id="me",
            document=filepath,
            caption=final_caption,
            file_name=filename,
            force_document=True,
            progress=make_progress_callback("📤 Upload (Retry)")
        )
    
    elapsed = time.time() - start_time
    speed = file_size / elapsed if elapsed > 0 else 0
    
    print(f"\n\n✅ Upload complete!")
    print(f"⚡ Speed: {human_size(int(speed))}/s")
    print(f"⏱️ Time: {elapsed:.1f}s")
    print(f"👤 Sent to: Saved Messages ({me.first_name})")


async def main():
    """Main function"""
    
    print("=" * 60)
    print("🚀 Telegram File Uploader")
    print("=" * 60)
    
    # ── Validate inputs ──
    if not SESSION_STRING:
        print("❌ SESSION_STRING not found!")
        print("   Add it as GitHub Secret: Settings > Secrets > SESSION_STRING")
        sys.exit(1)
    
    if not LINK:
        print("❌ No link provided!")
        sys.exit(1)
    
    print(f"🔗 Link: {LINK}")
    if CAPTION:
        print(f"📝 Caption: {CAPTION}")
    if CUSTOM_FILENAME:
        print(f"📄 Custom filename: {CUSTOM_FILENAME}")
    
    # ── Initialize Pyrogram Client ──
    app = Client(
        name="uploader_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
        in_memory=True,
        workers=8
    )
    
    async with app:
        me = await app.get_me()
        print(f"\n✅ Logged in as: {me.first_name} (ID: {me.id})")
        
        filepath = None
        
        # ── Check if it's a Telegram link ──
        chat_id, msg_id = parse_telegram_link(LINK)
        
        if chat_id is not None and msg_id is not None:
            # It's a Telegram post link
            print(f"\n📱 Detected Telegram link")
            filepath = await handle_telegram_link(app, chat_id, msg_id)
        
        elif LINK.startswith("http://") or LINK.startswith("https://"):
            # It's a direct download URL
            print(f"\n🌐 Detected direct URL")
            filename = CUSTOM_FILENAME if CUSTOM_FILENAME else get_filename_from_url(LINK)
            filepath = await download_file(LINK, filename)
        
        else:
            print(f"❌ Invalid link format: {LINK}")
            print("   Supported formats:")
            print("   - https://example.com/file.zip (Direct URL)")
            print("   - https://t.me/channel/123 (Telegram post)")
            print("   - https://t.me/c/1234567890/123 (Private channel)")
            sys.exit(1)
        
        if filepath and os.path.exists(filepath):
            # Rename if custom filename given
            if CUSTOM_FILENAME and os.path.basename(filepath) != CUSTOM_FILENAME:
                new_path = os.path.join(os.path.dirname(filepath), CUSTOM_FILENAME)
                os.rename(filepath, new_path)
                filepath = new_path
                print(f"📄 Renamed to: {CUSTOM_FILENAME}")
            
            # Upload to Saved Messages
            await upload_to_saved_messages(app, filepath, CAPTION)
            
            # Cleanup
            try:
                os.remove(filepath)
                print(f"🗑️ Temp file cleaned up")
            except:
                pass
        else:
            print("❌ No file to upload!")
            sys.exit(1)
    
    print("\n" + "=" * 60)
    print("🎉 All done!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
