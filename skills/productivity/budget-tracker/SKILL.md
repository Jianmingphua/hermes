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

## Script Reference

### `scripts/budget_tracker.py`

Three mutually exclusive operations controlled by flags:

| Operation | Flags | Purpose |
|-----------|-------|---------|
| **Append** | `--category`, `--description`, `--amount`, plus optional `--date`, `--person`, `--payment`, `--receipt`, `--notes`, `--discount`, `--discount-amount` | Add a new expense row |
| **Edit cell** | `--edit-row N --field <name> --value <val>` | Fix a single cell in-place without deleting the row |
| **Delete** | `--delete-row N` | Remove an entire row |

**Defaults:**
- `--date` → today (SGT). No need to pass most of the time.
- `--person` → `"You"` (overridable per recognized user)
- `--payment` → auto-detected from description keywords (PayNow, NETS, Visa, etc.), falls back to `Cash`
- `--receipt` → `"No"`

**Discount flags (use when user mentions discount):**
- `--discount 15` → amount is **pre-discount** price; paid amount (`amount × 0.85`) auto-added to notes
- `--discount-amount 2` → flat $2 subtracted from amount

### `scripts/budget_today_check.py`
Check if any expenses were logged today (SGT). Prints `FOUND:N` or `NONE`. Used by the daily reminder cron job.

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
   - **Discount handling — RULE**: If the user mentions a discount *in the same message as the amount* (e.g., "$11.50, there's a 15% discount"), parse and log with `--discount 15` immediately — the stated amount is the **pre-discount** price. If the user sends the amount first and the discount as a **follow-up correction** (e.g., user: "$11.50 for lunch" → agent logs → user: "there's a 15% discount not included"), edit the existing row: use `--edit-row N --field notes --value "...discount clarification..."` while preserving the original amount. Do NOT ask "was that before or after discount?" — record what the user stated and let follow-up corrections drive edits.
5. **Payment Method**: Keywords:
   - If no keyword found, default to **Cash**.
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

**Confirm only when ambiguous** — not for every expense.

- **No confirmation needed**: User gives a clear amount and description in one message (e.g., "lunch $11.50", "grab $8"). Parse, log immediately, reply with the logged summary.
- **Confirm**: Amount is missing, unclear, or the message contains multiple expenses. Also confirm if the parsed description looks wrong or truncated.
- **Follow-up corrections**: If the user corrects a field after logging, use `--edit-row N --field <name> --value <val>` — never delete+re-append for single-field fixes.

**Default payment method is Cash** if no payment keyword is detected in the description.

For the rare case a confirmation IS needed, use this format:

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

### Pitfalls (read these before every write)

**🚨 Pitfall 1 — `default=today_sgt` vs `default=today_sgt()`**: When using a function as an argparse default, you must **call** it (`today_sgt()`, a string) not pass the function object (`today_sgt`, a callable). The function object serializes to a JSON `function` type and crashes the Google Sheets API with `TypeError: Object of type function is not JSON serializable`. If you see this error, check all `default=` arguments in argparse.

**🚨 Pitfall 2 — stale `.pyc` cache**: After editing a Python script, clear `__pycache__` and any `.pyc` files. Stale bytecode from a different Python version (e.g., `.cpython-311.pyc` on a 3.12 install) silently shadows the updated source. Fix: `find /path/to/scripts -name "*.pyc" -delete; rm -rf /path/to/scripts/__pycache__`.

**🚨 Pitfall 3 — user corrections should use `--edit-row`, not delete+re-append**: When the user corrects a field (amount, description, etc.) on an already-logged expense, use `--edit-row N --field <name> --value <val>` to fix the single cell. Only use delete+re-append if the entire row is wrong. This avoids row-number shifts and preserves the original row position.

### Step 1: Append the row
```bash
# Simple expense — no confirmation needed for clear single-item messages
python3 /opt/hermes/scripts/budget_tracker.py \
  --category "Food & Dining" \
  --description "Lunch" \
  --amount "11.50" \
  --discount "15"
```

With `--discount`, the original amount is stored and the paid amount (after discount) is auto-calculated in the notes. Use `--discount-amount` for flat discounts.

Expected output: `APPENDED:Expenses!A2:H2`

### Step 2: Handle corrections

If the user sends a follow-up correction (wrong amount, wrong description, discount clarification, wrong person):

1. Find the row (usually the most recent append)
2. Use `--edit-row N --field <name> --value <val>` to fix just that cell
3. Confirm the correction

E.g., user says "11.5 is before the discount" → edit the notes field to add discount info:
```bash
python3 /opt/hermes/scripts/budget_tracker.py --edit-row 24 --field notes --value "Lunch | 15% discount applied, paid ~9.78"
```

### Step 3: Verify and reply

Read back the row to confirm the data matches.

### Step 4: Reply
On success: "✅ Logged: Lunch ($11.50, Cash) — 3 Jun 2026"
On edit: "✅ Updated row 24: Notes → 'Lunch | 15% discount applied, paid ~9.78'"
On error: "❌ Failed to log. Error: {details}"

- Multiple expenses

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
