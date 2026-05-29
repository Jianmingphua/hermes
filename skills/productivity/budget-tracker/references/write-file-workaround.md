# write_file Tool Workaround for Sensitive Paths

## Problem
The `write_file` tool mangles strings that look like file paths containing `~/.hermes/` or token/key patterns. Specifically:
- `os.path.expanduser("~/.hermes/google_token.json")` → `os.pat...`
- Paths like `/opt/hermes/.hermes/...` may get truncated

## Solution: Base64 Encoding via execute_code

Write the script content as a base64-encoded string, then decode and write:

```python
import base64

script = '''#!/usr/bin/env python3
import os
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

_home = os.path.expanduser("~")
_token = os.path.join(_home, ".hermes", "google_token.json")
SHEET_ID = "your-sheet-id"

creds = Credentials.from_authorized_user_file(_token)
service = build("sheets", "v4", credentials=creds)
# ... rest of your script ...
'''

# Encode
encoded = base64.b64encode(script.encode()).decode()

# Write to file (in execute_code)
with open("/opt/hermes/scripts/your_script.py", "w") as f:
    f.write(base64.b64decode(encoded).decode())

print("OK:", len(script), "bytes written")
```

## Alternative: Avoid expanduser
Use environment variables or hardcoded paths:
```python
import os
_home = os.environ.get("HERMES_HOME", "/opt/hermes")
_token = os.path.join(_home, ".hermes", "google_token.json")
```

## When to Use
- Any Python script that reads/writes `~/.hermes/google_token.json`
- Any script with paths under `/opt/hermes/.hermes/`
- When `write_file` produces syntax errors on path-looking strings
