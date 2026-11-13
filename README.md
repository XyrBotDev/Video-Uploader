# 📤 Telegram File Uploader via GitHub Actions

> **⚠️ Disclaimer:** This project is created **strictly for educational and personal learning purposes** to demonstrate how GitHub Actions, Pyrogram, and Telegram API can be integrated together. The author is not responsible for any misuse of this tool. Use responsibly and in compliance with [Telegram's Terms of Service](https://telegram.org/tos) and [GitHub's Acceptable Use Policy](https://docs.github.com/en/site-policy/acceptable-use-policies).

---

## 📖 About

A lightweight automation tool that uploads files directly to your **Telegram Saved Messages** using **GitHub Actions** as a free cloud runner. Simply provide a direct download URL or a Telegram post link, and the workflow handles everything — downloading, generating thumbnails, and uploading.

No server required. No hosting cost. Just push and run.

---

## ✨ Features

| Feature | Description |
|---|---|
| 🔗 **Direct URL Download** | Download any file from a public URL |
| 📱 **Telegram Post Link** | Fetch media from public/private channel posts |
| 🖼️ **Original Thumbnail** | Preserves the original thumbnail from Telegram posts |
| 🎬 **Video Metadata** | Auto-detects duration, resolution for proper streaming |
| ⚡ **Fast Transfer** | 10MB chunked downloads with TgCrypto acceleration |
| 📂 **Auto File Type** | Detects video/audio/photo/document automatically |
| 🏷️ **Custom Caption** | Add your own caption to uploaded files |
| 📝 **Custom Filename** | Rename files before uploading |
| 🔄 **FloodWait Handling** | Automatically waits and retries on Telegram rate limits |
| 🆓 **Zero Cost** | Runs on GitHub Actions free tier |

---

## 🏗️ Project Structure

```
telegram-uploader/
├── .github/
│   └── workflows/
│       └── upload.yml        # GitHub Actions workflow
├── uploader.py               # Main Python script
├── requirements.txt          # Python dependencies
└── README.md                 # This file
```

---

## 🚀 Setup Guide

### Step 1: Generate Session String

Run this script locally on your machine **once** to get your Pyrogram session string:

```bash
pip install pyrogram tgcrypto
```

```python
from pyrogram import Client
import asyncio

async def main():
    async with Client(
        "session",
        api_id=6,
        api_hash="eb06d4abfb49dc3eeb1aeb98ae0f581e",
        in_memory=True
    ) as app:
        print(await app.export_session_string())

asyncio.run(main())
```

> You will be asked for your phone number and OTP. Save the output string securely.

### Step 2: Fork / Clone This Repository

```bash
git clone https://github.com/YOUR_USERNAME/telegram-uploader.git
cd telegram-uploader
```

### Step 3: Add Session String as GitHub Secret

1. Go to your repository on GitHub
2. Navigate to **Settings → Secrets and variables → Actions**
3. Click **"New repository secret"**
4. Set:
   - **Name:** `SESSION_STRING`
   - **Value:** *(paste the string from Step 1)*
5. Click **Add secret**

> 🔒 GitHub Secrets are encrypted and never exposed in logs or to collaborators.

### Step 4: Push All Files

```bash
git add .
git commit -m "initial setup"
git push origin main
```

---

## 📋 How to Use

1. Go to the **Actions** tab in your repository
2. Select **"Telegram File Uploader"** from the left sidebar
3. Click **"Run workflow"**
4. Fill in the inputs:

| Input | Required | Example |
|---|---|---|
| `link` | ✅ Yes | `https://example.com/file.mp4` or `https://t.me/channel/123` |
| `caption` | ❌ No | `My awesome video` |
| `filename` | ❌ No | `movie.mp4` |

5. Click **"Run workflow"** and wait for completion
6. Check your **Telegram Saved Messages** — the file will be there!

---

## 🔗 Supported Link Formats

```
✅ Direct File URL
   https://cdn.example.com/video.mp4
   https://example.com/archive.zip

✅ Public Telegram Channel Post
   https://t.me/channel_name/123

✅ Private Telegram Channel Post
   https://t.me/c/1234567890/456
   (You must be a member of the channel)
```

---

## ⚙️ Technical Details

- **Runtime:** Python 3.11 on Ubuntu (GitHub-hosted runner)
- **Library:** [Pyrogram](https://docs.pyrogram.org/) v2.0.106 with [TgCrypto](https://github.com/pyrogram/tgcrypto)
- **Download Engine:** `aiohttp` with 10MB chunked streaming
- **Thumbnail Engine:** `ffmpeg` (auto-installed on runner)
- **Timeout:** 350 minutes per run (GitHub limit is 360)
- **Workers:** 8 parallel Pyrogram workers for faster uploads

---

## 📊 GitHub Actions Free Tier Limits

| Plan | Public Repo | Private Repo |
|---|---|---|
| **Free** | ♾️ Unlimited minutes | 2,000 minutes/month |
| **Storage** | 500 MB | 500 MB |

> Each typical run (500MB file) takes approximately 5–8 minutes.

---

## ⚠️ Important Notes

- **Do NOT** hardcode your session string in any file. Always use GitHub Secrets.
- **Do NOT** run excessive workflows in a short period to avoid rate limiting.
- **Do NOT** use this tool for uploading copyrighted or illegal content.
- Session strings grant **full access** to your Telegram account. Keep them private.
- If your session is compromised, revoke it immediately from **Telegram → Settings → Devices → Terminate all other sessions**.

---

## 🛠️ Troubleshooting

| Problem | Solution |
|---|---|
| "Run workflow" button missing | Ensure `.github/workflows/upload.yml` exists on the `main` branch |
| `SESSION_STRING not found` | Check Settings → Secrets → Actions |
| `Message not found` | Verify the Telegram link and ensure you are a member of the channel |
| `FloodWait` error | The script auto-retries. If persistent, wait a few hours |
| No thumbnail on video | Original thumbnail is preserved from Telegram posts. Direct URLs use ffmpeg fallback |
| Workflow timeout | Files larger than 2GB may exceed the time limit |

---

## 📜 License

This project is licensed under the **MIT License**.

---

## 🎓 Educational Purpose Statement

This repository was created as a **learning project** to explore and understand:

- GitHub Actions CI/CD pipeline automation
- Telegram MTProto API integration via Pyrogram
- Asynchronous file handling in Python
- Cloud-based workflow orchestration
- Secure secrets management in version control

It is **not intended** for commercial use, mass automation, or any activity that violates the terms of service of Telegram or GitHub.

---

<p align="center">Made with ❤️ for learning purposes</p>
