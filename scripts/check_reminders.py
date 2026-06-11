#!/usr/bin/env python3
"""
Check for upcoming activities and send reminders.
Designed to run every 15 minutes via cron.
Sends reminders 1hr and 2hrs before scheduled activities.
"""
import json, sys, os, time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/opt/hermes/.hermes/skills/productivity/google-workspace/scripts')
import google_api

SGT = timezone(timedelta(hours=8))
SID = '1iis0wf9BQ-6pvDjmfvtzAjtvwTyCNlkPXBPCQXkbzQM'

# Track sent reminders to avoid duplicates
REMINDER_STATE_FILE = '/opt/hermes/scripts/reminder_state.json'

def load_state():
    """Load reminder state."""
    try:
        with open(REMINDER_STATE_FILE) as f:
            return json.load(f)
    except:
        return {'sent': []}

def save_state(state):
    """Save reminder state."""
    with open(REMINDER_STATE_FILE, 'w') as f:
        json.dump(state, f)

def get_svc():
    return google_api.build_service('sheets', 'v4')

def read_sheet(svc, sheet_name, range_str):
    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=SID, range=f'{sheet_name}!{range_str}'
        ).execute()
        return result.get('values', [])
    except:
        return []

def parse_time(time_str, date_str):
    """Parse time string to datetime. Returns None if unparseable."""
    if not time_str or time_str == 'KIV':
        return None
    
    time_str = time_str.strip().replace('~', '')
    
    try:
        # Try HH:MM format
        if ':' in time_str:
            parts = time_str.split(':')
            hour = int(parts[0])
            minute = int(parts[1])
        else:
            # Try "4pm" format
            time_lower = time_str.lower().replace(' ', '')
            if 'pm' in time_lower:
                hour = int(time_lower.replace('pm', ''))
                if hour != 12:
                    hour += 12
                minute = 0
            elif 'am' in time_lower:
                hour = int(time_lower.replace('am', ''))
                minute = 0
            else:
                return None
        
        # Parse date
        date_parts = date_str.split('-')
        year = int(date_parts[0])
        month = int(date_parts[1])
        day = int(date_parts[2])
        
        return datetime(year, month, day, hour, minute, tzinfo=SGT)
    except:
        return None

def check_reminders():
    """Check for activities coming up in 1hr and 2hrs."""
    now = datetime.now(SGT)
    state = load_state()
    reminders = []
    
    svc = get_svc()
    
    # Check both sheets
    for sheet_name in ['Chiang Mai', 'Chiang Rai']:
        data = read_sheet(svc, sheet_name, 'A2:I50')
        for i, row in enumerate(data):
            if len(row) < 4:
                continue
            
            date_str = row[0] if row[0] else ''
            time_str = row[2] if len(row) > 2 else ''
            activity = row[3] if len(row) > 3 else ''
            details = row[5] if len(row) > 5 else ''
            status = row[6] if len(row) > 6 else ''
            
            if not activity or status == 'kiv':
                continue
            
            activity_dt = parse_time(time_str, date_str)
            if not activity_dt:
                continue
            
            # Check if activity is today
            if activity_dt.date() != now.date():
                continue
            
            # Calculate time difference in minutes
            diff_minutes = (activity_dt - now).total_seconds() / 60
            
            # Create unique ID for this reminder
            reminder_id = f"{date_str}_{time_str}_{activity}"
            
            # 2-hour reminder (between 110-130 minutes before)
            if 110 <= diff_minutes <= 130:
                key = f"{reminder_id}_2hr"
                if key not in state['sent']:
                    reminders.append({
                        'key': key,
                        'type': '2hr',
                        'time': time_str,
                        'activity': activity,
                        'details': details[:100],
                        'sheet': sheet_name
                    })
            
            # 1-hour reminder (between 50-70 minutes before)
            if 50 <= diff_minutes <= 70:
                key = f"{reminder_id}_1hr"
                if key not in state['sent']:
                    reminders.append({
                        'key': key,
                        'type': '1hr',
                        'time': time_str,
                        'activity': activity,
                        'details': details[:100],
                        'sheet': sheet_name
                    })
    
    # Mark reminders as sent
    for r in reminders:
        state['sent'].append(r['key'])
    
    # Clean up old entries (keep last 100)
    state['sent'] = state['sent'][-100:]
    save_state(state)
    
    return reminders

if __name__ == '__main__':
    reminders = check_reminders()
    if reminders:
        for r in reminders:
            emoji = '⏰' if r['type'] == '1hr' else '🔔'
            msg = f"{emoji} **{r['type']} Reminder**\n\n"
            msg += f"📍 **{r['activity']}**\n"
            msg += f"🕐 {r['time']} ({r['sheet']})\n"
            if r['details']:
                msg += f"📝 {r['details']}\n"
            print(msg)
    else:
        print("NO_REMINDERS")
