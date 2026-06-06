#!/usr/bin/env python3
"""
Expense Daily Reminder — no_agent cron script.
Outputs a reminder to log expenses if none found today, otherwise silent.

Runs the budget_today_check.py script and outputs
a user-facing message only when no expenses were logged.
"""
import subprocess
import sys
from datetime import datetime

today = datetime.now().strftime("%A, %d %B")
check = "/opt/hermes/scripts/budget_today_check.py"

try:
    result = subprocess.run(
        ["python3", check],
        capture_output=True, text=True, timeout=15,
    )
    output = result.stdout.strip()
except Exception as e:
    # Script failed — skip silently
    sys.exit(0)

if "NONE" in output:
    print(f"📝 No expenses logged for {today}.")
    print()
    print("Reply with your expenses for today (e.g., 'lunch $12', 'grab $8')")
    print("or type /cancel to dismiss.")

# Exit 0 always — no_agent: 0+empty=silent, 0+output=deliver
sys.exit(0)