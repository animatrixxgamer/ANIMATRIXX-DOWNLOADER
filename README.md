# 𝕬𝖓𝖎𝖒𝖆𝖙𝖗𝖎𝖝𝖝 𝕲𝖆𝖒𝖊𝖗✪ — Animatrixx Anime Bot

Your personal **anime + K-drama library, powered by Telegram**.

This repository is built **phase by phase** against the full Animatrixx spec.
`main` currently contains **phases 1–8**: the application foundation, metadata
search, series/channel mapping, and the complete media pipeline
(**download queue → FFprobe/FFmpeg encoding → Telegram archive → cleanup**,
with persistent jobs and crash recovery).

> The bot never scrapes streaming sites and never bypasses access controls.
> Sources are URLs you are authorized to fetch, or files you upload yourself.

---

## 1. What is implemented

| Subsystem | Status |
|---|---|
| Validated configuration (fails on missing `BOT_TOKEN`, bad ports, bad profile) | ✅ |
| Structured logging with bot-token redaction | ✅ |
| SQLAlchemy async models + SQLite (users, series, channels, archive, **jobs, job events**) | ✅ |
| Administrator authorization, re-checked in every sensitive handler | ✅ |
| Anime search — AniList GraphQL (no API key) with TTL cache | ✅ |
| K-drama / movie search — TMDB (your own `TMDB_API_KEY`) | ✅ |
| `/addseries` conversation → verifies bot admin rights → maps series to a private channel | ✅ |
| `/library`, `/episode`, `/archiveinfo`, `/verifyarchive` | ✅ |
| Duplicate-episode protection (series + season + episode + resolution + profile) | ✅ |
| **SSRF-safe URL validation** (scheme/host/port/DNS/redirect checks) | ✅ |
| **Chunked, resumable, size-capped downloads with real progress** | ✅ |
| **Magic-byte file-type verification before anything is accepted** | ✅ |
| **Persistent job queue: 13 states, validated transitions, backoff retries** | ✅ |
| **Crash recovery on restart** (resume download, re-encode, re-upload, repair) | ✅ |
| **FFprobe inspection + 5 FFmpeg profiles** (original/high/balanced/small/target) | ✅ |
| **Resolution fitting that never upscales or stretches** (480p/720p/1080p) | ✅ |
| **Telegram archive upload** with pre-flight checks and idempotent retries | ✅ |
| **Temp cleanup after successful archiving** (never deletes active files) | ✅ |
| Inline keyboards, `/broadcast`, Docker deployment | ✅ |
| **Unicode font styling** for every heading (bold, italic, fraktur, double-struck, monospace, small caps) | ✅ |
| **Colourful buttons** - consistent emoji icons on every inline button | ✅ |
| **Welcome sticker**: `/setsticker` (reply to a sticker), `/sticker`, `/delsticker` | ✅ |
| **Local Bot API Server wiring** (`base_url` + `base_file_url`) for ~2 GB uploads | ✅ |
| **GitHub Actions CI** (pytest on 3.11/3.12 with FFmpeg, no secrets) | ✅ |
| Force-subscription, `/genlink` | ⏳ later phases |

### Commands

```
/start  /help  /whoami  /storage

# discovery
/search <title>   /anime <title>   /drama <title>   /movie <title>   /latest

# series & archive mapping
/addseries [anime|drama|movie] <title>   /myseries   /series <title>
/delseries <title>   /unmapped   /clearunmapped

# library
/library [title]   /episode <title> S01E04   /archiveinfo   /verifyarchive

# media pipeline
/download <url> [--series Title] [--ep S01E04] [--profile balanced] [--res 720p] [--out mkv] [--size 150]
/addlink <url>                 # alias for an authorized source URL
/encode <job_id> <profile> [--res 720p] [--size 150]
/profiles                      # what each profile does
/queue   /progress [id]   /cancel <id>   /retry <id>   /jobs
/pause   /resume

# administration
/users   /broadcast <text>

# appearance
/sticker   /setsticker (reply to a sticker)   /delsticker
```

`/download` also accepts a **reply to a video/document you own** — the bot
archives that upload instead of fetching a URL.

---

## 2. How the pipeline works

```
        ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
queued →│ DOWNLOAD │→ │  ENCODE  │→ │  UPLOAD  │→ │ CLEANUP  │→ completed
        └──────────┘   └──────────┘   └──────────┘   └──────────┘
        SSRF checks     ffprobe       pre-flight     deletes temp
        Range resume    5 profiles    channel admin  files only
        size cap        no upscaling  DB commit 1st  when AUTODEL on
        magic bytes     2-pass target recorded ids
```

* All state lives in **SQLite** (`media_jobs` + `job_events`), so a crash or
  `docker restart` never loses a job: `/retry <id>` works after recovery.
* Each stage runs under its own concurrency limit
  (`MAX_CONCURRENT_DOWNLOADS/ENCODINGS/UPLOADS`).
* Progress is written to the DB and a single Telegram status bubble, throttled
  to `PROGRESS_UPDATE_INTERVAL` so Telegram never rate-limits you.
* Permanent failures (403/404, blocked destination, wrong file type, file over
  the upload limit) are **not** retried; transient ones retry with exponential
  backoff up to `MAX_JOB_ATTEMPTS`.

### Security rules enforced on every download

Only `http/https`, no credentials in URLs, no private/loopback/link-local/
CGNAT/metadata addresses, hostname shortcuts (`localhost`, `*.local`,
`*.internal`) blocked, port allow-list, **every redirect hop re-validated**,
connect/read timeouts, `MAX_DOWNLOAD_SIZE_MB` enforced while streaming, and the
payload's **magic bytes** checked before it is accepted (HTML, ZIP, PDF, ELF,
EXE are rejected). Files are written only inside `TEMP_DIR` and are never
executed. `ALLOW_PRIVATE_DOWNLOADS=true` exists for local testing only.

---

### Look and feel: fonts, colour, stickers

Telegram's HTML parser has **no colour tags**, so colour and personality come
from three places instead:

* **Fonts** — every boxed heading is set in a Unicode typeface from
  `app/utils/fonts.py`: `bold` (default), `bold_sans`, `italic`, `bold_italic`,
  `fraktur`, `double_struck`, `monospace`, `small_caps`.
  ```python
  box("ANIMATRIXX ANIME")            # ╭━「 𝐀𝐍𝐈𝐌𝐀𝐓𝐑𝐈𝐗𝐗 𝐀𝐍𝐈𝐌𝐄 」━━╮
  box("QUEUE", font=None)            # plain ASCII when you want it
  stylize("Solo Leveling", "double_struck")
  ```
* **Buttons** — Telegram owns button styling, so buttons cannot carry coloured
  text. Every button therefore starts with a consistent emoji icon
  (`🎬 title`, `◀️ Prev`, `▶️ Next`, `📚 Find in Library`, `✅ Confirm`,
  `🛑 Cancel`, `❎ Close`), which is where the colour shows up.
* **Stickers** — reply to any sticker with `/setsticker` (admin) to save it as
  the welcome sticker; the bot sends it on every `/start`. `/sticker` replays
  it, `/delsticker` removes it. It is stored in the `bot_settings` table, and a
  deleted sticker set never breaks `/start`.

---

## 3. Project structure

```
animatrixx-anime-bot/
├── app/
│   ├── main.py bot.py config.py logging_config.py
│   ├── handlers/            # start, search, callbacks, series, library, admin, media
│   ├── services/
│   │   ├── anime_metadata.py movie_metadata.py result_store.py permissions.py
│   │   ├── urls.py          # SSRF validation + URL redaction for logs
│   │   ├── downloader.py    # chunked streaming, resume, size cap, magic bytes
│   │   ├── encoder.py       # ffprobe + ffmpeg profiles + progress parsing
│   │   ├── telegram_storage.py  # pre-flight checks + idempotent upload
│   │   ├── progress.py      # throttled status message
│   │   └── cleanup.py       # dry-run planning, active-file protection
│   ├── workers/
│   │   ├── states.py        # JobState, transitions, backoff, stage planning
│   │   └── job_manager.py   # dispatcher, stage runners, crash recovery
│   ├── database/            # models, session, repositories, job_repositories
│   ├── keyboards/ inline.py
│   └── utils/               # formatting (HTML-safe), validation
├── tests/                   # 197 tests, no Telegram credentials required
├── Dockerfile  docker-compose.yml  pytest.ini
├── requirements.txt  .env.example  .gitignore  README.md
```

---

## 4. Installation (local)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo apt install -y ffmpeg            # FFprobe/FFmpeg are required for encoding
cp .env.example .env                  # then edit .env
python -m app.main
```

Without a system FFmpeg you can point the bot at a static build:

```bash
FFMPEG_BINARY=$HOME/tools/ffmpeg FFPROBE_BINARY=$HOME/tools/ffprobe python -m app.main
```

---

## 5. Environment variables

See [.env.example](.env.example) — it loads as-is (verified). Highlights:

| Variable | Meaning |
|---|---|
| `BOT_TOKEN` | **Required.** From @BotFather. |
| `OWNER_ID` / `ADMIN_IDS` | Who may run admin commands. |
| `TMDB_API_KEY` | Optional; enables `/drama` and `/movie`. |
| `MAX_DOWNLOAD_SIZE_MB` | Hard cap enforced while streaming. |
| `MAX_CONCURRENT_*` | Per-stage worker limits (keep encodings at 1 on small VPSes). |
| `MAX_JOB_ATTEMPTS` / `RETRY_BACKOFF_SECONDS` | Retry policy for transient errors. |
| `DEFAULT_ENCODING_PROFILE` | `original` / `high` / `balanced` / `small` / `target`. |
| `TELEGRAM_UPLOAD_LIMIT_MB` | 49 MB on the hosted Bot API (conservative). |
| `LOCAL_BOT_API_ENABLED` | Flip to `true` together with `LOCAL_BOT_API_MAX_MB=2000`. |
| `AUTODEL_ENABLED` | Delete temp files **after** a successful archive. |

---

## 6. Creating the bot (BotFather)

1. **@BotFather** → `/newbot` → e.g. `Animatrixx Anime Bot` / `AnimatrixxAnimeBot`.
2. Copy the token into `.env` → `BOT_TOKEN=…`.
3. Get your numeric id from **@userinfobot** → `OWNER_ID=…`.
4. Restart the bot after changing `.env`.

## 7. Creating and mapping private archive channels

1. Telegram → **New Channel** → *Private* → e.g. `Solo Leveling Archive`.
2. Channel → **Administrators** → **Add Admin** → your bot → allow *posting*.
3. In the bot: `/addseries anime Solo Leveling`.
4. When asked, **forward a message from that channel** to the bot (or send
   `@channelname` / the `-100…` chat id). The bot verifies it can post there.
5. `/verifyarchive` re-checks every channel; `/delseries` removes only the
   database mapping — never the channel or its files.

---

## 8. Running tests

```bash
pip install -r requirements.txt
pytest
```

197 tests, **no Telegram credentials needed**. They cover config validation,
permissions, AniList/TMDB parsing, SSRF rules, real HTTP downloads against a
local server, FFmpeg argument construction, progress maths, crash-recovery
decisions, cleanup safety, message rendering, and an **end-to-end pipeline
test** (download → encode → archive → cleanup) that runs when FFmpeg is found.
Set `FFMPEG_BINARY` / `FFPROBE_BINARY` if FFmpeg is not on `PATH`.

---

## 9. Deploying to a VPS

```bash
git clone https://github.com/animatrixxgamer/ANIMATRIXX-DOWNLOADER.git
cd ANIMATRIXX-DOWNLOADER
cp .env.example .env && nano .env
docker compose up -d --build
docker compose logs -f animatrixx
```

Volumes: `./data` (SQLite), `./logs`, `animatrixx-temp` (scratch media).
Without Docker: `python3 -m venv .venv && pip install -r requirements.txt`,
install `ffmpeg`, then run `python -m app.main` under systemd/pm2 so it
restarts after crashes (the queue itself recovers interrupted jobs).

---

## 10. Large-file uploads (Local Bot API Server)

The **hosted Telegram Bot API caps bot uploads near 50 MB** — far below a
multi-hundred-MB episode. The bot therefore refuses anything above
`TELEGRAM_UPLOAD_LIMIT_MB` (49 MB by default) with an explicit message instead
of failing halfway through an upload.

To lift the ceiling to **~2 GB**, run your own Bot API Server
(official `tdlib/telegram-bot-api`, packaged by aiogram):

1. Get `api_id` / `api_hash` at <https://my.telegram.org> → *API development
   tools* (<https://core.telegram.org/api/obtaining_api_id>).
2. Put them in `.env`: `TELEGRAM_API_ID=…` and `TELEGRAM_API_HASH=…`.
3. Start the server next to the bot:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.localapi.yml up -d
   ```
4. In `.env` switch the bot over:
   ```dotenv
   LOCAL_BOT_API_ENABLED=true
   LOCAL_BOT_API_BASE_URL=http://telegram-bot-api:8081/bot
   LOCAL_BOT_API_FILE_URL=http://telegram-bot-api:8081/file/bot
   TELEGRAM_UPLOAD_LIMIT_MB=2000
   ```
   (outside Docker: `http://localhost:8081/…`)
5. `docker compose restart animatrixx` — PTB now sends **both** API calls and
   file downloads through your server; the pre-flight limit becomes 2000 MB
   and `UPLOAD_TIMEOUT_SECONDS` (600) governs the push.

**Reality check:** uploaded files are stored on the server volume
(`bot-api-data`), so budget disk and bandwidth; a Telegram file ID is **not**
a backup; switching back is `LOCAL_BOT_API_ENABLED=false` + restart. Your bot
token never changes.

### Continuous integration

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs on every push and PR:
Python 3.11 + 3.12, installs FFmpeg, byte-compiles the sources, runs the full
suite (including the end-to-end pipeline test) and verifies `.env.example`
still loads. **No secrets are required** — every test mocks Telegram.

---

## 11. Known limitations (honest list)

- **Not yet tested against live Telegram.** Polling, real uploads, channel
  verification and `/broadcast` need your `BOT_TOKEN`. The upload stage is
  verified end-to-end with a fake Telegram bot; the real API call is not.
- **Force-subscription, `/genlink`, `/batch`, watermarks, thumbnails,
  scheduled tasks, and the Local Bot API Server deployment** are still to come.
- Automatic age-based cleanup of *stale partial downloads* (`/cleanup`,
  `/autodel`) is implemented as a service for per-job cleanup only; the
  scheduled sweep is a later phase.
- Migrations are `create_all` for now; Alembic lands before schema changes
  reach a live database.
- `target` size is a two-pass *target*, not a guarantee — quality may vary.

## 12. Checklist for you

- [ ] Create the bot in BotFather → `BOT_TOKEN` in `.env`
- [ ] `OWNER_ID` = your numeric id
- [ ] (Optional) `TMDB_API_KEY` for K-drama/movie search
- [ ] Install FFmpeg (`apt install ffmpeg`) or set `FFMPEG_BINARY`
- [ ] `pytest` → 197 passed
- [ ] `python -m app.main` → `/start`, `/anime solo leveling`
- [ ] Create a private archive channel, add the bot, `/addseries`
- [ ] `/download <your authorized url> --series Title --ep S01E01`
- [ ] `/queue` → watch it reach `completed`, then `/verifyarchive`
