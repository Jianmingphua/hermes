# Hermes Agent — Setup Journey & Achievements

> From stock Hermes installation to a fully autonomous multi-agent system.
> Documented: June 2026

---

## Table of Contents

1. [Infrastructure & Platform](#infrastructure--platform)
2. [Telegram Gateway & User Management](#telegram-gateway--user-management)
3. [Google Workspace Integration](#google-workspace-integration)
4. [Budget Tracker System](#budget-tracker-system)
5. [SG Weather Monitoring](#sg-weather-monitoring)
6. [RSS Feed Monitoring](#rss-feed-monitoring)
7. [Oracle Autonomous Database](#oracle-autonomous-database)
8. [Automated Forex Trading Bot](#automated-forex-trading-bot)
9. [Skills & Automation Library](#skills--automation-library)
10. [Cron Job Architecture](#cron-job-architecture)
11. [TTS / Voice Configuration](#tts--voice-configuration)
12. [Planned / In-Progress](#planned--in-progress)

---

## Infrastructure & Platform

- **Host**: Oracle Cloud Infrastructure (OCI) ARM64 VPS, Ubuntu Linux
- **Runtime**: Python 3.11 (system), Python 3.12 (pip), `uv` package manager
- **Container**: Docker (for Firecrawl self-hosting)
- **Model**: `nvidia/nemotron-3-super-120b-a12b:free` via OpenRouter
- **Hermes Home**: `/opt/hermes` (`~/.hermes`)
- **Config**: `~/.hermes/config.yaml`

---

## Telegram Gateway & User Management

- **Primary channel**: Telegram DM
- **DM pairing flow** enabled — unknown users receive pairing codes
- **Paired users**:
  - **User** — Telegram ID: `<USER_TG_ID>` → maps to "You" in Budget Tracker
  - **Wife** — Telegram ID: `<WIFE_TG_ID>` → maps to "Wife" in Budget Tracker
- **Quiet hours**: 22:00–08:00 SGT (14:00–00:00 UTC) — all cron jobs respect this window
- **Secondary profile**: Restricted profile created for spouse (awaiting per-user profile routing — Hermes issue #33548)

---

## Google Workspace Integration

- **Auth**: OAuth 2.0 (token stored locally)
- **Access**: Gmail, Calendar, Drive, Docs, Sheets via `gws` CLI
- **Hermes folder**: `<GDRIVE_FOLDER_ID>` (root Google Drive folder)
- **Budget Tracker folder**: "Budget Tracker" subfolder inside Hermes

---

## Budget Tracker System

### Overview
Interactive expense logging via Telegram DM. Natural language parsing → Google Sheets.

### Spreadsheet
- **Sheet ID**: `<BUDGET_SHEET_ID>`
- **URL**: `https://docs.google.com/spreadsheets/d/<BUDGET_SHEET_ID>/edit`
- **Tab**: `Expenses`
- **Columns**: Date | Person | Category | Description | Amount (SGD) | Payment Method | Receipt | Notes

### Scripts
| Script | Purpose |
|--------|---------|
| `/opt/hermes/scripts/budget_tracker.py` | Append, edit, or delete expense rows |
| `/opt/hermes/scripts/budget_today_check.py` | Check if expenses were logged today (SGT) |

### Features
- **Natural language parsing**: "lunch $12 cash" → auto-extracts amount, payment, category
- **Multi-user**: User → "You", Wife → "Wife"
- **Confirmation flow**: Always shows summary before writing; user confirms with "yes"/"no"/"edit"
- **Edit support**: `--edit-row N --field amount --value 11.50`
- **Delete support**: `--delete-row N` with confirmation
- **Discount handling**: `--discount 15` or `--discount-amount 2.50`
- **Auto payment detection**: PayNow, NETS, Credit Card, Cash, etc.
- **Auto category detection**: Food & Dining, Transport, Groceries, Health, Bills, Shopping, etc.
- **Voice memo support**: STT-transcribed text with error correction heuristics

### Cron
- **Daily reminder** at 9pm SGT (13:00 UTC) — checks today's expenses, sends summary or reminder
- **Job ID**: `<CRON_JOB_ID>`
- **Silent during**: 22:00–08:00 SGT

---

## SG Weather Monitoring

### Overview
Real-time weather alerts for Singapore via NEA (National Environment Agency) APIs.

### APIs Used
- **v2 API** (`/environment/air-temperature`) — returns all station temperatures
- **v1 API** (`/environment/2-hour-weather-forecast`) — area-level 2-hour forecast

### Monitored Areas
- **Tampines** (user's location)
- **Seletar**

### Alert Conditions
Light Rain, Light Showers, Showers, Rain, Moderate Rain/Showers, Heavy Rain/Showers, Thundery Showers, Thunderstorm, Storm

### Script
- `/opt/hermes/scripts/sg_weather_alert.py`

### Cron
- **Every 15 minutes**
- **Job ID**: `<CRON_JOB_ID>`
- **Delivery**: Telegram (silent unless alert — only delivers on change)

---

## RSS Feed Monitoring

### Overview
Monitors a blog for new articles, pushes to Telegram.

### Tool
- `blogwatcher-cli` at `/opt/hermes/bin/blogwatcher-cli`
- Local DB: `~/.blogwatcher-cli/blogwatcher-cli.db`

### Cron
- **Every 1 hour**
- **Job ID**: `<CRON_JOB_ID>`
- **Delivery**: Telegram (silent if no new articles)
- **Sleep window**: 22:00–08:00 SGT (no delivery)

---

## Oracle Autonomous Database

### Instance
- **Type**: Oracle AI Database 26ai
- **Region**: `<ORACLE_REGION>`
- **Port**: `<ORACLE_PORT>`
- **Network**: Private subnet, VCN whitelisted
- **Auth**: TLS walletless connection

### Schemas (3 PDBs)
| Schema | Purpose |
|--------|---------|
| `HERMES_MEMORY` | Structured agent state, user preferences, session summaries, facts |
| `KNOWLEDGE_BASE` | Multimodal KB: documents, embeddings metadata, source references |
| `VECTOR_STORE` | Vector embeddings + IVF cosine indexes (384–3072 dimensions) |

### Scripts
| Script | Purpose |
|--------|---------|
| `~/.hermes/scripts/oracle_db.py` | DB helper (memory + embeddings) |
| `~/.hermes/scripts/kb_ingest.py` | KB ingestion pipeline (URL, file, raw text) |
| `~/.hermes/scripts/semantic_search.py` | Hybrid search (vector + full-text) |

### Capabilities
- **Vector search**: ANN via Oracle VECTOR indexes (cosine distance)
- **Full-text search**: Oracle Text CONTAINS with relevance scoring
- **Hybrid search**: Weighted combination of both
- **KB ingestion**: Content extraction → smart chunking → embedding → storage
- **Persistent memory**: Key-value store, session logging, state management

### Skills
- `oracle-adb` skill at `~/.hermes/skills/devops/oracle-adb/`
- `oracle-autonomous-db` skill at `~/.hermes/skills/devops/oracle-autonomous-db/`

### Known Pitfalls
- MERGE requires unique bind positions (`:1-:6` + `:7-:12`)
- LOB `.read()` before connection close
- `NUM_ROWS` is stale after DML
- `NEIGHBOR PARTITIONS` for vector index
- sklearn RP default for embeddings

---

## Automated Forex Trading Bot

### Overview
Fully autonomous forex trading bot running on OANDA demo account with multi-confirmation signal scoring and 9 safety layers.

### Account
- **Broker**: OANDA
- **Account ID**: `<OANDA_ACCOUNT_ID>`
- **Balance**: 100,000 SGD (demo/practice)
- **Mode**: Live execution (`--execute`)

### Trading Configuration
| Parameter | Value |
|-----------|-------|
| **Pairs** | EUR/USD, GBP/USD, USD/JPY |
| **Timeframe** | M15 (15-minute candles) |
| **Candles fetched** | 500 per scan per pair |
| **Risk per trade** | 0.5% of balance |
| **Max daily loss** | 3% |
| **Max positions** | 3 concurrent |
| **Max correlated** | 2 same direction |
| **Stop Loss** | 2× ATR |
| **Take Profit** | 3× ATR (1.5:1 R:R) |
| **Min position** | 1,000 units |
| **Max position** | 100,000 units |
| **Min confidence** | 0.4 (40%) |
| **Min confirmations** | 2 of 4 |

### Signal Scoring (Multi-Confirmation)
| Indicator | Score | Confirmation |
|-----------|-------|-------------|
| EMA 20/50 crossover | ±3.0 | ✅ |
| MACD crossover | ±2.5 | ✅ |
| RSI extreme (<25 or >75) | ±2.5 | ✅ |
| Bollinger Band touch | ±2.0 | ✅ |
| 200 EMA trend | ±0.5 | — |
| ADX modifier (>30 ×1.2, <20 ×0.8) | Modifier | — |

**Signal thresholds**: BUY if score ≥ +2.5 AND confirmations ≥ 2; SELL if score ≤ -2.5 AND confirmations ≥ 2

**Confidence** = (|score| / 7.0) + (confirmations × 0.1)

**Quality tiers**: HIGH (≥3 confirmations + EMA/MACD), MEDIUM (≥2), LOW (1), NONE (0)

### Safety Layers (in order)
1. **Session Filter** — Only trade during high-liquidity sessions (London/NY for EUR/USD, GBP/USD; Tokyo/London for USD/JPY)
2. **News Filter** — Block 15min before / 30min after NFP, FOMC, ECB, BOE events
3. **Duplicate Check** — Don't open same pair twice
4. **Spread Monitor** — Abort if spread too wide (EUR/USD >2p, GBP/USD >2.5p, etc.)
5. **Circuit Breaker** — Pause after 3 consecutive losses
6. **Correlation Check** — Max 2 correlated positions same direction
7. **Max Positions** — Hard cap at 3 concurrent
8. **Position Sizing** — 0.5% risk per trade, ATR-based stops
9. **SL/TP on every order** — No naked positions

### Project Structure
```
/opt/hermes/forex-trading-bot/
├── src/
│   ├── main.py              # Main pipeline orchestrator
│   ├── config.py            # Configuration loader (.env)
│   ├── oanda_client.py      # OANDA REST API v20 wrapper
│   ├── indicators.py        # Technical indicators + signal scoring
│   ├── signal_generator.py  # Full analysis pipeline per pair
│   ├── risk_manager.py      # Position sizing + trade validation
│   ├── news_filter.py       # Economic calendar event filter
│   ├── session_filter.py    # Trading session filter
│   ├── circuit_breaker.py   # Consecutive loss protection
│   ├── spread_monitor.py    # Spread threshold monitor
│   ├── position_state.py    # Cross-run position tracking
│   ├── trade_monitor.py     # Trade lifecycle + P&L recording
│   ├── notifier.py          # Telegram message formatting
│   └── backtest.py          # Backtesting with spread model
├── config/
│   ├── .env                 # API keys, account ID, settings
│   └── .env.template        # Template for new setups
├── tests/
│   ├── test_oanda_connection.py  # 16 tests (API, indicators, signals)
│   └── test_safety_filters.py    # 20 tests (safety filters)
├── logs/
│   ├── bot_YYYYMMDD.log         # Daily bot activity log
│   ├── signals_*.json           # Signal history per run
│   ├── position_state.json      # Tracked open positions
│   ├── active_trades.json       # Monitored trades + history
│   └── circuit_breaker.json     # Circuit breaker state
├── requirements.txt
└── venv/                    # Python virtual environment
```

### Backtest Results
| Pair | TF | P&L% | Max DD | Trades |
|------|-----|------|--------|--------|
| EUR/USD | M30 | +16.25% | 17.8% | 1 |
| EUR/GBP | H1 | +8.60% | 12.5% | 1 |
| GBP/USD | H4 | +4.78% | 10.3% | 1 |
| EUR/USD | H1 | +3.97% | 20.5% | 1 |
| EUR/USD | H4 | +2.01% | 8.0% | 1 |
| GBP/USD | H1 | -1.45% | 18.9% | 1 |
| EUR/USD | M15 | -1.73% | 17.2% | 1 |
| USD/JPY | M15 | -11.14% | 18.6% | 1 |

### Cron
- **Every 15 minutes** (matching M15 timeframe)
- **Job ID**: `<CRON_JOB_ID>`
- **Name**: `forex-bot-m15`
- **Delivery**: Telegram alert only when orders are placed (watchdog pattern — silent otherwise)
- **Live execution**: `--execute` flag active

### Key Commands
```bash
# Run once (live)
cd /opt/hermes/forex-trading-bot && source venv/bin/activate
PYTHONPATH=/opt/hermes/forex-trading-bot python src/main.py --mode once --execute

# Check status
python src/main.py --status          # Circuit breaker
python src/main.py --positions       # Open positions
python src/main.py --trades          # Trade history

# Reset circuit breaker
python src/main.py --reset-cb

# Backtest
python src/backtest.py

# Tests (36/36 passing)
python -m pytest tests/ -v
```

### Bugs Fixed During Development
1. **Confirmation threshold**: Added `confs >= 2` requirement in `main.py` — previously any signal with conf ≥0.4 would trade regardless of confirmation count
2. **Price precision**: JPY pairs now round to 3 decimal places (OANDA requirement) — previously caused `TAKE_PROFIT_ON_FILL_PRICE_PRECISION_EXCEEDED` rejections

### Skills Created
- `automated-trading-systems` skill (data-science/) with:
  - `references/oanda-api-patterns.md`
  - `references/forex-strategies.md`
  - `templates/requirements.txt`

---

## Skills & Automation Library

### Active Skills (~70 across 25 domains)

| Domain | Key Skills |
|--------|-----------|
| **DevOps** | Cron watchdog, disk mgmt, Docker self-hosting, git-backup, webhook subs, Oracle ADB |
| **Data Science** | Jupyter live kernel, automated trading systems |
| **ML Ops** | llama.cpp, vLLM, DSPy, W&B, AudioCraft, SAM, eval harness |
| **GitHub** | PR workflow, code review, issues, repo mgmt, auth |
| **Productivity** | Google Workspace (gws CLI), Budget Tracker, Airtable, Notion, Linear, PowerPoint, SG Weather, teams meeting pipeline |
| **Research** | arXiv, blogwatcher, Polymarket, LLM Wiki |
| **Media** | YouTube transcripts, GIF search, Spotify, HeartMuLa, songsee |
| **Social** | X/Twitter (xurl), yuanbao |
| **Software Dev** | Debugging (pdb/debugpy/node), TDD, planning, subagent-driven dev |
| **Smart Home** | OpenHue (Philips Hue) |
| **Email** | Himalaya (IMAP/SMTP) |
| **MCP** | Native MCP client (stdio/HTTP) |
| **Note-taking** | Obsidian vault |
| **Gaming** | Minecraft modpack server, Pokemon player |
| **Creative** | ASCII art, Excalidraw, p5.js, Manim, pixel art, comfyui, design |
| **Hermes Config** | `hermes-agent`, skill authoring, s6 container supervision, TUI debugging |
| **Red Teaming** | GODMODE jailbreak skill |
| **Dogfood** | Exploratory QA |

---

## Cron Job Architecture

| Job ID | Name | Schedule | Purpose | Delivery |
|--------|------|----------|---------|----------|
| `<CRON_JOB_ID>` | SG Weather Alert | Every 15 min | Monitor Tampines & Seletar weather | Telegram (silent unless alert) |
| `<CRON_JOB_ID>` | RSS Feed Monitor | Every 1 hour | Scan blog for new articles | Telegram (silent if no new articles) |
| `<CRON_JOB_ID>` | Budget Tracker Daily Reminder | Daily 9pm SGT | Check today's expenses, send summary | Telegram (silent 22:00–08:00 SGT) |
| `<CRON_JOB_ID>` | forex-bot-m15 | Every 15 min | Run forex trading bot scan + execute | Telegram (silent unless orders placed) |

### Cron Design Principles
- **Watchdog pattern**: All cron jobs stay silent unless there's something to report
- **Sleep window**: 22:00–08:00 SGT respected by all jobs
- **Token efficiency**: Scripts are loaded by OS, not inlined in prompts
- **Self-healing**: Cron jobs can fix bugs autonomously (e.g., forex bot fixed its own confirmation threshold and price precision bugs during a cron run)

---

## TTS / Voice Configuration

- **TTS Voice**: `en-SG-LunaNeural` (Singapore English)
- **STT Provider**: Local Whisper
- **STT Model**: `medium`
- **STT Language**: English
- **Voice commands**: `/voice tts` (always-on), `/voice off` (disable)

---

## Planned / In-Progress

### Tier 1 — Easy Wins (build in hours)
- [ ] **Morning Daily Briefing** — 8am SGT cron: calendar + weather + urgent emails + news → one Telegram message
- [ ] **Email Triage Agent** — Scan inbox, categorize, send summary with suggested replies

### Tier 2 — Medium Effort (build in a few days)
- [ ] **SG Environmental Dashboard** — PSI, UV index, dengue cluster alerts, MRT disruption monitoring
- [ ] **Expense Analytics Agent** — Weekly/monthly spending reports from Budget Tracker sheet
- [ ] **Document Triage Agent** — Drop a link/PDF → summarize → file to Drive

### Tier 3 — Bigger Projects (build over weeks)
- [ ] **Home Assistant Integration** — Control smart home via Telegram (Tapo switch via cloud API)
- [ ] **Multi-Agent Research Pipeline** — Orchestrator spawns researcher sub-agents → synthesizes → delivers
- [ ] **Hermes Self-Health Monitor** — Watch server health, disk, memory, cron job status

### Infrastructure
- [ ] Per-user profile routing (Hermes issue #33548) — spouse profile ready, waiting on feature

---

## Timeline

| Date | Milestone |
|------|-----------|
| May 28 | Google Workspace OAuth, Budget Tracker created, SG Weather monitoring + cron, RSS feed cron |
| May 29 | Budget Tracker daily reminder cron, voice memo expense logging |
| May 30 | Tools audit, Oracle ADB setup (3 schemas, KB pipeline, semantic search), config summary |
| June 3 | Forex trading bot: research → build → backtest → deploy on OANDA demo |
| June 4 | Forex bot: M15 reconfiguration, bug fixes (confirmation threshold, price precision), 36/36 tests pass, live execution |

---

*Last updated: June 4, 2026*
