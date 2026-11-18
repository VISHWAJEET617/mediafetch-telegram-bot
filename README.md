# Media Fetch Telegram Bot

Free public Telegram downloader for YouTube videos and Shorts, Instagram Reels/video posts, and X videos. Regular YouTube videos show colored quality buttons; Shorts, Reels, and X select the highest available quality automatically.

## UI

- Blue: primary download/recommended-quality action
- Green: highest quality and Forward
- Red: Cancel
- Default: secondary quality choices
- Commands: `/start`, `/help`, `/cancel`

The completed video contains only a green **Forward** button. It opens Telegram's native chat picker and resends the cached Telegram `file_id` through inline mode.

## Required variables

```text
TELEGRAM_BOT_TOKEN=...
PUBLIC_ACCESS=true
ALLOWED_USER_IDS=
DATA_DIR=/data
MAX_FILE_BYTES=49000000
MAX_DURATION_SECONDS=2700
MAX_CONCURRENT_DOWNLOADS=2
```

`MAX_FILE_BYTES=49000000` is appropriate for Telegram's hosted Bot API. For large uploads, deploy the included `telegram-api/Dockerfile` (the prebuilt Bot API 10.3 image), set `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and `TELEGRAM_LOCAL=1`, then configure the bot with:

```text
BOT_API_BASE=http://telegram-bot-api.railway.internal:8081
MAX_FILE_BYTES=1990000000
YTDLP_POT_PROVIDER_URL=http://youtube-pot-provider.railway.internal:4416
```

Before switching from Telegram's hosted API, call `logOut` once against `https://api.telegram.org`. The local API service runs with `--local` and accepts uploads up to 2000 MB.

For stronger cookie-free YouTube reliability, run `brainicism/bgutil-ytdlp-pot-provider:1.3.2-node` as the private `youtube-pot-provider` service. The bot loads its matching Python plugin and requests per-video PO tokens automatically; browser and account cookies remain disabled.

## Local test

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

## Railway

The image includes FFmpeg and Deno. Mount a Railway volume at `/data`, configure the required variables, and deploy from the repository root. The service exposes `/health` on Railway's injected `PORT` and uses Telegram long polling.

Enable inline mode with BotFather so the Forward button can return cached video results.
