# 𝕬𝖓𝖎𝖒𝖆𝖙𝖗𝖎𝖝𝖝 𝕲𝖆𝖒𝖊𝐫✪ — Animatrixx Anime Bot

Your personal **anime + K-drama library, powered by Telegram**.

This repository is being built **phase by phase** against the full Animatrixx spec.
What is in `main` today is **Phase 1–4**: the application skeleton, configuration,
database, metadata search (anime + K-drama + movies), the series/archive-channel
mapping flow, the library view, and administrator security.

> The download / FFmpeg / upload pipeline is **not implemented yet**. The bot never
> pretends a video exists when it does not, and it never scrapes streaming sites.

---

## 1. What is implemented

| Subsystem | Status |
|---|---|
| Validated configuration (pydantic-settings, fails on missing `BOT_TOKEN`) | ✅ |
| Structured logging with bot-token redaction | ✅ |
| SQLAlchemy async models + SQLite (users, admins, series, channels, archive rows) | ✅ |
| Administrator authorization (`OWNER_ID` + `ADMIN_IDS` + DB admins), re-checked in every sensitive handler | ✅ |
| Anime search — AniList GraphQL (no API key) with TTL cache | ✅ |
| K-drama / movie search — TMDB (your own `TMDB_API_KEY`) | ✅ |
| `/addseries` guided conversation → verifies bot admin rights → maps series to a private channel | ✅ |
| `/library`, `/episode`, `/archiveinfo`, `/verifyarchive` | ✅ |
| Duplicate-episode protection (series + season + episode + resolution + profile) | ✅ |
| Inline keyboards with pagination, expiry-token callback data, callback authorization | ✅ |
| `/broadcast` with flood control and delivered/failed counts | ✅ |
| Unit tests (no Telegram credentials required) | ✅ |
| Docker + Docker Compose deployment | ✅ |
| Download queue, FFmpeg encoding, Telegram archive uploads, auto-cleanup, force-subscribe | ⏳ later phases |

### Commands

```
/start  /help  /whoami  /storage
/search <title>   /anime <title>   /drama <title>   /movie <title>   /latest
/addseries [anime|drama|movie] <title>   /myseries   /series <title>
/delseries <title>   /unmapped   /clearunmapped
/library [title]   /episode <title> S01E04   /archiveinfo   /verifyarchive
/users   /broadcast <text>
```

---

## 2. Project structure

```
animatrixx-anime-bot/
├── app/
│   ├── main.py                 # entry point: python -m app.main
│   ├── bot.py                  # application factory + startup/shutdown
│   ├── config.py               # validated settings
│   ├── logging_config.py       # console + rotating file logs, token redaction
│   ├── handlers/
│   │   ├── start.py            # /start /help /storage
│   │   ├── search.py           # /search /anime /drama /movie /latest
│   │   ├── callbacks.py        # inline-button dispatch
│   │   ├── series.py           # /addseries conversation + series commands
│   │   ├── library.py          # /library /episode /archiveinfo /verifyarchive
│   │   ├── admin.py            # /users /broadcast /whoami
│   │   └── __init__.py         # all handler wiring in one place
│   ├── services/
│   │   ├── anime_metadata.py   # AniList client + TTL cache + pure parsers
│   │   ├── movie_metadata.py   # TMDB client (K-drama / movies)
│   │   ├── result_store.py     # server-side search results behind a token
│   │   └── permissions.py      # is_admin + @admin_only decorator
│   ├── database/
│   │   ├── models.py           # User, Administrator, Series, ArchiveChannel, ArchivedMedia
│   │   ├── session.py          # engine/factory/init_db/session_scope
│   │   └── repositories.py     # all queries, duplicate detection
│   ├── keyboards/inline.py     # inline keyboards
│   └── utils/                  # formatting (HTML-safe) + validation
├── tests/                      # pytest + pytest-asyncio
├── Dockerfile  docker-compose.yml  pytest.ini
├── requirements.txt  .env.example  .gitignore
└── README.md
```

---

## 3. Installation (local, no Docker)

```bash
python3.11+ -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env
python -m app.main
```

FFmpeg is not required yet — it becomes mandatory in the encoding phase.

---

## 4. Environment variables

See [.env.example](.env.example). Required at startup:

| Variable | Meaning |
|---|---|
| `BOT_TOKEN` | From @BotFather. **Required** — startup fails without it. |
| `OWNER_ID` | Your numeric Telegram id (`@userinfobot`). Full admin rights. |
| `ADMIN_IDS` | Optional comma-separated extra admin ids. |
| `DATABASE_URL` | Default `sqlite+aiosqlite:///./data/animatrixx.db`. |
| `TMDB_API_KEY` | Optional. Enables `/drama` and `/movie`. Free at themoviedb.org. |
| `METADATA_CACHE_TTL` | Seconds to cache provider responses (default `3600`). |
| `TEMP_DIR` / `LOG_DIR` / `LOG_LEVEL` | Working directories and verbosity. |

`.env` is git-ignored. Never commit tokens.

---

## 5. Creating the bot (BotFather)

1. In Telegram open **@BotFather** → `/newbot`.
2. Pick a display name, e.g. `Animatrixx Anime Bot`.
3. Pick a username, e.g. `AnimatrixxAnimeBot`.
4. Copy the token into `.env` → `BOT_TOKEN=…`.
5. Get your own id from **@userinfobot** → `OWNER_ID=…`.
6. Optional: `/setdescription`, `/setabouttext`, `/setuserpic` for branding.

Restart the bot after changing `.env`.

---

## 6. Creating and mapping private archive channels

For **each series** you archive:

1. Telegram → **New Channel** → *Private* → name it (e.g. `Solo Leveling Archive`).
2. Channel → **Administrators** → **Add Admin** → search your bot → give it
   permission to *post messages* (and *edit/delete* if you want repairs later).
3. In the bot: `/addseries anime Solo Leveling`.
4. When asked for the channel, **forward any message from that channel to the bot**
   (forwarding is the reliable way — or send `@channelname` / the numeric chat id
   starting with `-100…`).
5. The bot verifies its own admin rights and confirms the mapping.

`/myseries` lists everything, `/unmapped` shows series with no channel,
`/verifyarchive` re-checks access to every channel after you change admins.

`/delseries` only removes the **database mapping** — the Telegram channel and its
files are never touched (it asks for confirmation first).

---

## 7. Running tests

```bash
pip install -r requirements.txt
pytest
```

Unit tests mock every external API — **no bot token is needed**.

---

## 8. Deploying to a VPS (Docker)

```bash
git clone https://github.com/animatrixxgamer/ANIMATRIXX-DOWNLOADER.git
cd ANIMATRIXX-DOWNLOADER
cp .env.example .env && nano .env
docker compose up -d --build
docker compose logs -f animatrixx
```

Persistent volumes: `./data` (SQLite), `./logs`, `animatrixx-temp` (scratch media).

Plain-system alternative:

```bash
sudo apt install -y python3.12 python3-venv ffmpeg
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.main          # run under systemd / pm2 / screen for restart-on-crash
```

---

## 9. Known limitations (honest list)

- **No media pipeline yet**: no download queue, no FFmpeg encoding, no Telegram
  uploads, no automatic VPS cleanup, no force-subscription, no `/genlink`.
  Those are the remaining spec phases.
- **Telegram upload limit**: the hosted Bot API caps uploads far below
  multi-hundred-MB anime files. A Local Bot API Server is required for large
  files — that arrives with the upload phase. Nothing here claims otherwise.
- **Telegram file IDs are not backups**: archive records point at messages that
  can be deleted; `/verifyarchive` detects broken references.
- **Schedule/airing data** (`/airing`, `/schedule`) is not implemented; `/latest`
  shows currently-airing titles from AniList only.
- Migrations are `create_all` for now; Alembic lands before schema changes
  reach a live database.
- Not yet tested against real Telegram: **live polling, photo-caption search
  results, and channel verification need your `BOT_TOKEN` to confirm.**

## 10. Checklist for you

- [ ] Create the bot in BotFather → put `BOT_TOKEN` in `.env`
- [ ] Put your numeric id in `OWNER_ID`
- [ ] (Optional) Add a TMDB key for K-drama/movie search
- [ ] Create the first private archive channel and add the bot as admin
- [ ] Run `pytest`, then `python -m app.main` and say `/start`
- [ ] Confirm `/verifyarchive` reports your channel as ✅ active
