---
name: budget-tracker
description: "Interactive expense tracking via Telegram DMs. Parse natural language expense messages and write to Google Sheets Budget Tracker. Trigger when user mentions spending, buying, paying, expensing, or logging a transaction."
---

# Budget Tracker

Interactive expense logging via Telegram. Parse natural language, confirm ambiguous fields, write row to Google Sheets.

## Spreadsheet Info
- **Spreadsheet ID**: `1ET7l9dwYouJygbLApuzHBJ5ZaWc3XtrZ1oLVfLtrvv8`
- **Spreadsheet URL**: `https://docs.google.com/spreadsheets/d/1ET7l9dwYouJygbLApuzHBJ5ZaWc3XtrZ1oLVfLtrvv8/edit`
- **Sheet name**: `Expenses` (tab 1)
- **Columns**: Date | Person | Category | Description | Amount (SGD) | Payment Method | Receipt | Notes

## Scripts

- `scripts/budget_tracker.py` — Append an expense row to the Sheet. Called with `--date`, `--person`, `--category`, `--description`, `--amount`, `--payment`, `--receipt`, `--notes`.
- `scripts/budget_today_check.py` — Check if any expenses were logged today (SGT). Prints `FOUND:N` or `NONE`. Used by the daily reminder cron job.

## Reminder Cron Job

A daily reminder runs at 9pm SGT (13:00 UTC) via cron job `bbd34da30b6e`.
It checks today's expenses using `budget_today_check.py` and sends a Telegram
message — either prompting to log expenses or confirming that some were already logged.

## Recognized Users
| Telegram ID | Person |
|---|---|
| 137588943 | You |
| 175822942 | Wife |

**Important:** When a recognized user logs an expense, ALWAYS use their mapped Person name — never default to "You" for all users. Each Telegram ID has its own identity.

If an expense was logged under the wrong person (e.g., Wife's expense under "You"), fix it immediately:
1. Read the Sheet to find the misattributed row
2. Update the Person cell directly via the Sheets API
3. Confirm the correction to the user

## Trigger Conditions
The user mentions any spending/expense activity:
- "spent", "paid", "bought", "log expense", "expense", "spending"
- "lunch $12", "grab $8", "NTUC $45"
- "just paid", "grabbed", any sentence with $/SGD/dollar amounts
- Short single-line descriptions of purchases

## Authorized Users
- Only recognized Telegram user IDs (see table above) are allowed to log expenses.
- If an unrecognized user sends an expense message, respond: "Sorry, you're not authorized to use the budget tracker. Ask OWL's owner to add you."

## Token Efficiency Rule

**Always use the script** (`/opt/hermes/scripts/budget_tracker.py`) for append and delete operations. Never inline Google API calls via `terminal(python3 -c ...)` — it burns ~10x more tokens (~800-1,200 vs ~50-100 per call) because the full Python source is dumped into the context window every time. The script is loaded by the OS, not the model. Exception: one-off exploratory queries (e.g. checking sheet tab IDs) that don't warrant a script change.

## Expense Parsing

### Parse these fields from natural language:
1. **Date**: "today" → today, "yesterday" → yesterday, "Mon/Tue/..." → most recent, "28 May" → that date. Default: today.
2. **Person**: Default "You" for recognized user. If user says "wife: lunch $10" or "for wife", use "Wife".
3. **Description**: The item description. Remove amount/date/payment keywords first. E.g., "lunch $12 cash" → "Lunch".
4. **Amount**: Look for `$XX`, `XX SGD`, `S$XX`, `XX dollars`. Default currency: SGD.
5. **Payment Method**: Keywords:
   - cash → Cash
   - card, credit, visa, mastercard → Credit Card
   - paynow, paylah, pay now, pay lah → PayNow / PayLah
   - grab, gojek → PayNow / PayLah (default)
   - nets → Nets
   - bank transfer, transfer → Bank Transfer
   - giro → GIRO
   - apple pay, google pay → Apple / Google Pay
   - Default if unclear: Credit Card
6. **Category** (derived, don't ask):
   - lunch/dinner/breakfast/coffee/food/meal/eat/restaurant/shoppee → Food & Dining
   - taxi/grab/mrt/bus/transport/petrol/gas/uber → Transport
   - ntuc/shengsiong/giant/cold storage/groceries/supermarket → Groceries
   - hospital/clinic/doctor/pharmacy/medicine/health → Health
   - electricity/water/utilities/bill/internet/phone → Bills & Subscriptions
   - shirt/pants/shoes/shopee/lazada/uniqlo/zara/shopping → Shopping
   - movie/netflix/spotify/game/entertainment → Entertainment
   - gym/workout/fitness/sport → Fitness
   - school/course/book/education/udemy → Education
   - flight/hotel/travel/vacation → Travel
   - gift/present/donation/donate → Gifts & Donations
   - repair/fix/plumber/electrician → Maintenance
   - salary/paycheck/income/dividend → Income
   - Default: Other
7. **Receipt**: Default "No". Only set "Yes" if user mentions "got receipt", "receipt", "keep receipt".

## Confirm Before Writing

ALWAYS show a full summary and wait for explicit user confirmation before writing to the Sheet. Never write without user confirmation.

Show the summary using this exact format (use Telegram HTML parse mode via tg_inline_kb.py if available, otherwise plain text):

```
📝 New Expense:
━━━━━━━━━━━━━━━━━━━━
  Date:     28 May 2026
  Person:   You
  Desc:     Lunch at hawker
  Amount:   $12.00
  Payment:  Cash
  Category: Food & Dining
  Receipt:  No
━━━━━━━━━━━━━━━━━━━━

Reply with:
  1️⃣ yes     → log it
  2️⃣ no      → cancel
  3️⃣ edit    → change a field
```

Accepted confirmations: "1", "yes", "confirm", "ok", "yeah", "yup"
Accepted cancellations: "2", "no", "cancel", "nope", "don't", "skip"
Edit: "3", "edit", or "edit <field name>"

If user says no/cancel → reply "❌ Cancelled." and do nothing.

If user says edit → ask which field, get the new value, re-show the summary with the updated field highlighted, wait for confirmation again.

If any field is ambiguous (amount not clear, date unclear, person not default), ask for that field specifically BEFORE showing the summary. Only show the summary once all fields are resolved.

**Format:** Send the confirmation as a clear text message with numbered options. Keep it scannable — use box-drawing lines and aligned fields.


## Deleting an Expense

**Always confirm before deleting. Never delete without explicit user confirmation.**

**Delete confirmation format:**
```
🗑️ Delete this expense?
━━━━━━━━━━━━━━━━━━━━
  Row 3: 28 May 2026
  Person: You
  Desc:   Starbucks
  Amount: $6.50
  Payment: Credit Card
━━━━━━━━━━━━━━━━━━━━

Reply with:
  1️⃣ yes  → confirm delete
  2️⃣ no   → cancel
```

**Workflow:**
1. Find the row to delete (read the Sheet, match by date/description/amount)
2. Show the user the exact row with confirmation prompt (format above)
3. On "yes": run `python3 /opt/hermes/scripts/budget_tracker.py --delete-row N`
4. On "no": reply "❌ Cancelled."
5. On success: "✅ Deleted row 3 — Starbucks $6.50"



## Writing to Sheet

### Step 1: Append the row
```bash
python3 /opt/hermes/scripts/budget_tracker.py \
  --date "2026-05-28" \
  --person "You" \
  --category "Food & Dining" \
  --description "Lunch at hawker" \
  --amount "12.00" \
  --payment "Cash" \
  --receipt "No" \
  --notes ""
```

Expected output: `APPENDED:Expenses!A2:H2`

### Step 2: Verify the row was written
Read back the last row to confirm the data matches.

### Step 3: Reply
On success: "✅ Logged: Lunch at hawker ($12.00, Cash) — 28 May 2026"
On error: "❌ Failed to log. Error: {details}"

### IMPORTANT — Script Creation Pitfall
When creating or editing Python scripts that reference `~/.hermes/google_token.json` or similar paths:
- The `write_file` tool **mangles** path strings, replacing segments like `os.path.expanduser("~/.hermes/...")` with truncated versions like `os.pat...`
- **Workaround via base64** (use `execute_code` to write files that contain these strings):
  ```python
  import base64
  script = '''#!/usr/bin/env python3
  import os
  _home = os.path.expanduser("~")
  _token = os.path.join(_home, ".hermes", "google_token.json")
  # ... rest of script ...
  '''
  encoded = base64.b64encode(script.encode()).decode()
  # Then write:
  with open("/path/to/output.py", "w") as f:
      f.write(base64.b64decode(encoded).decode())
  ```
- **Alternative:** Avoid `os.path.expanduser` by using `os.environ.get("HERMES_HOME", "/opt/hermes")` or hardcoding `/opt/hermes/.hermes/...` if the environment is stable

See `references/write-file-workaround.md` for full details.

## Cron Job Reminders

When creating cron jobs that should deliver to Telegram:
- **Always** set `deliver` to the target (e.g., `'telegram'`) — default is `'local'` which means NO delivery
- Use `enabled_toolsets: ["terminal"]` for lightweight polling scripts
- The cron job's final response text IS the delivered message — put user-facing content there

Example cron job config:
```
schedule: "0 13 * * *"    # 9pm SGT = 13:00 UTC
deliver: "telegram"
skills: ["budget-tracker"]
enabled_toolsets: ["terminal"]
```

See `references/write-file-workaround.md` for the base64 technique needed when writing scripts via `execute_code`.
- Multiple expenses in one message ("lunch $12, taxi $8") → log each separately, confirm each
- Negatives or refunds ("refund $20 from grab") → negative amount, payment same method, description prefixed "Refund: "
- Income ("salary $5000") → Category: Income, Receipt: No
- If no amount found → ask: "How much was it?"
- If no description after removing amount/date/payment → ask: "What was this expense for?"
- Re-prompt on unclear input (max 2 attempts, then skip and ask user to rephrase)

## Voice Memo Transcription Handling

Voice messages arrive as pre-transcribed text from the STT layer. Transcription errors are common — especially on short phrases, brand names, and accents.

**Rules for voice input:**
1. **Always interpret voice memos as English first.** Never assume Malay, BM, or other languages unless the user explicitly uses non-English words in their *text* messages too.
2. **If the transcription sounds like a different language** (e.g., "suruh kagak" for "can of Coke"), map it to the most plausible English phrase and proceed — do NOT ask "is this Malay?" or attempt translation.
3. **Apply common STT error corrections** before parsing:
   - Misheard brand/item names → correct to likely English words (e.g., "suruh" → part of "saya mempunyai" not a place name)
   - Garbled words after removing amount/date → if the description doesn't make sense, ask once; don't invent a Malay translation
4. **Confirm as normal** — the confirmation step catches most transcription errors. If your parsed description looks wrong, the user will say "no" or "edit" at confirmation time.
5. **Short phrases are most error-prone**: Single-item voice memos ("$1.20 can of Coke") produce the worst transcriptions. For short phrases, correct-and-proceed — do NOT ask "did you mean X in Malay?" Map garbled output to the most plausible English phrase, confirm the parsed expense as normal, and let the user correct at confirmation time if wrong.
6. **STT quality tip**: If voice transcription is consistently poor, suggest upgrading the local model: `hermes config set stt.local.model small` (from default `base`). Users on ARM64 can use `small`; `medium` is noticeably slower. Alternatively, switch to Groq Whisper (free tier, set `GROQ_API_KEY` + `hermes config set stt.provider groq`).
