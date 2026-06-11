#!/usr/bin/env python3
"""
Daily itinerary summary for Thailand Trip.
Reads the Google Sheet and outputs a formatted daily briefing with weather.
Designed to be called by cron at 7am SGT.
"""
import json, sys, os, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/opt/hermes/.hermes/skills/productivity/google-workspace/scripts')
import google_api

SGT = timezone(timedelta(hours=8))
SID = '1iis0wf9BQ-6pvDjmfvtzAjtvwTyCNlkPXBPCQXkbzQM'

# June 2026 historical weather data for Chiang Mai (from research)
# Source: easeweather.com / world-weather.info
JUNE_WEATHER = {
    # day: (high_C, low_C, rain_mm, condition)
    1: (34, 23, 3.3, "Patchy rain possible"),
    2: (34, 23, 3.7, "Cloudy"),
    3: (33, 23, 1.3, "Light rain shower"),
    4: (35, 23, 3.1, "Patchy rain possible"),
    5: (33, 23, 5.3, "Heavy rain at times"),
    6: (32, 23, 7.5, "Moderate/heavy rain"),
    7: (31, 23, 8.0, "Moderate/heavy rain"),
    8: (31, 23, 10.8, "Moderate/heavy rain"),
    9: (32, 23, 6.4, "Patchy rain possible"),
    10: (30, 23, 10.6, "Light rain shower"),
    11: (28, 22, 18.3, "Moderate rain"),
    12: (27, 22, 6.4, "Moderate rain"),
    13: (29, 22, 4.2, "Patchy rain possible"),
    14: (31, 23, 5.6, "Moderate rain"),
    15: (32, 23, 10.5, "Moderate rain"),
    16: (31, 23, 2.1, "Patchy rain possible"),
    17: (30, 22, 1.8, "Patchy rain possible"),
    18: (29, 22, 2.5, "Patchy rain possible"),
    19: (30, 22, 3.0, "Patchy rain possible"),
    20: (31, 23, 1.5, "Patchy rain possible"),
    21: (30, 22, 2.0, "Patchy rain possible"),
    22: (29, 22, 3.5, "Patchy rain possible"),
    23: (30, 22, 4.0, "Patchy rain possible"),
    24: (31, 23, 2.8, "Patchy rain possible"),
    25: (31, 23, 4.7, "Light rain shower"),
    26: (31, 23, 10.9, "Light rain shower"),
    27: (29, 22, 10.2, "Moderate rain"),
    28: (32, 23, 4.3, "Light rain shower"),
    29: (32, 23, 2.6, "Patchy rain possible"),
    30: (31, 23, 3.2, "Cloudy"),
}

# Wet weather contingency plans by day
# day: (indoor_backup, covered_alternatives, notes)
WET_WEATHER = {
    14: {
        "indoor": "The House by Ginger (booked dinner), Warorot Market",
        "covered": "Sunday Walking Street Market (partially covered, works with umbrella)",
        "skip_if_heavy": "Wat Chedi Luang outdoor visit — do after rain eases",
        "swap_to": "Move Wat Chedi Luang to 16 Jun if needed"
    },
    15: {
        "indoor": "Cooking class (800-1,200 THB, 08:30-13:00) + Warorot Market (covered)",
        "covered": "One Nimman / Central Festival / Maya Mall shopping",
        "skip_if_heavy": "Mon Jam viewpoints — foggy/low visibility in heavy rain",
        "swap_to": "Swap Mon Jam → Doi Suthep (covered funicular, indoor museum)"
    },
    16: {
        "indoor": "Art in Paradise 3D Museum, MAIIAM Contemporary Art Museum (Tue-Sun 10-18)",
        "covered": "Warorot Market, One Nimman",
        "skip_if_heavy": "None — best weather day, proceed as planned",
        "swap_to": "Great day for all outdoor activities — prioritize Doi Suthep AM"
    },
    17: {
        "indoor": "Chiang Mai Art Museum (Mae On), cooking class",
        "covered": "Warorot Market, Jing Jai Market",
        "skip_if_heavy": "Mae Kampong Waterfall (slippery trails)",
        "swap_to": "Skip Doi Saket Hot Spring — focus on Mae On village instead"
    },
    18: {
        "indoor": "Pang Dee Dee has covered areas, Laddawan Village check-in",
        "covered": "Black House (indoor museum) if rain in Chiang Rai",
        "skip_if_heavy": "None expected — rain likely light",
        "swap_to": "Bring raincoat for elephant walk portions"
    },
    19: {
        "indoor": "Black House (indoor museum), Choui Fong Cafe (covered)",
        "covered": "Blue Temple indoor hall is rain-proof",
        "skip_if_heavy": "Choui Fong terrace — skip if heavy rain",
        "swap_to": "Black House is perfect rainy day activity"
    },
}

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

def get_weather_forecast(date_sgt):
    """Get weather for a specific date in June 2026."""
    day = date_sgt.day
    if day in JUNE_WEATHER:
        high, low, rain, condition = JUNE_WEATHER[day]
        
        # Rain probability estimate based on mm
        if rain < 2:
            rain_prob = "Low"
            rain_emoji = "🌤️"
        elif rain < 5:
            rain_prob = "Moderate"
            rain_emoji = "🌦️"
        elif rain < 10:
            rain_prob = "High"
            rain_emoji = "🌧️"
        else:
            rain_prob = "Very High"
            rain_emoji = "⛈️"
        
        return {
            'high': high,
            'low': low,
            'rain_mm': rain,
            'condition': condition,
            'rain_prob': rain_prob,
            'rain_emoji': rain_emoji,
        }
    return None

def get_today_activities(svc, today_str):
    activities = []
    for sheet_name in ['Chiang Mai', 'Chiang Rai']:
        data = read_sheet(svc, sheet_name, 'A2:I50')
        for row in data:
            if len(row) >= 6:
                date = row[0] if row[0] else ''
                if date == today_str:
                    activities.append({
                        'date': date,
                        'day': row[1] if len(row) > 1 else '',
                        'time': row[2] if len(row) > 2 else '',
                        'activity': row[3] if len(row) > 3 else '',
                        'type': row[4] if len(row) > 4 else '',
                        'details': row[5] if len(row) > 5 else '',
                        'status': row[6] if len(row) > 6 else '',
                        'cost': row[7] if len(row) > 7 else '',
                        'notes': row[8] if len(row) > 8 else '',
                        'source': sheet_name
                    })
    
    def time_key(a):
        t = a['time']
        if not t or t == 'KIV':
            return '99:99'
        t = t.replace('~', '').strip()
        try:
            if 'pm' in t.lower() or 'am' in t.lower():
                t = t.lower().replace(' ', '')
                if 'pm' in t:
                    hour = int(t.replace('pm', ''))
                    if hour != 12: hour += 12
                    return f'{hour:02d}:00'
                else:
                    return f'{int(t.replace("am", "")):02d}:00'
            parts = t.split(':')
            return f'{int(parts[0]):02d}:{parts[1] if len(parts) > 1 else "00"}'
        except:
            return '99:99'
    
    activities.sort(key=time_key)
    return activities

def get_upcoming_flights(svc, today_str):
    ex_data = read_sheet(svc, 'expenses', 'A2:E30')
    flights = []
    for row in ex_data:
        if len(row) >= 3 and row[0] == 'Flights':
            flights.append({'item': row[1], 'date': row[2]})
    return [f for f in flights if f['date'] and f['date'] >= today_str]

def get_expense_summary(svc):
    ex_data = read_sheet(svc, 'expenses', 'A25:G28')
    summary = {}
    for row in ex_data:
        if len(row) >= 2:
            label = row[1] if row[1] else ''
            if 'TOTALS' in label:
                summary['total_thb'] = row[3] if len(row) > 3 else '0'
                summary['total_sgd'] = row[4] if len(row) > 4 else '0'
            elif 'Jian Ming' in label:
                summary['jian_ming'] = row[6] if len(row) > 6 else '0'
            elif 'Sheryl' in label:
                summary['sheryl'] = row[6] if len(row) > 6 else '0'
    return summary

def type_icon(t):
    icons = {
        'travel': '✈️', 'temple': '🛕', 'food': '🍜', 'activity': '🎯',
        'shopping': '🛍️', 'accommodation': '🏨', 'kiv': '💡', 'temple/art': '🎨',
    }
    return icons.get(t.lower(), '📍')

def format_daily_summary(today_sgt):
    svc = get_svc()
    today_str = today_sgt.strftime('%Y-%m-%d')
    date_display = today_sgt.strftime('%d %b (%a)')
    
    activities = get_today_activities(svc, today_str)
    flights = get_upcoming_flights(svc, today_str)
    expenses = get_expense_summary(svc)
    weather = get_weather_forecast(today_sgt)
    
    trip_start = datetime(2026, 6, 14, tzinfo=SGT)
    trip_end = datetime(2026, 6, 19, 23, 59, tzinfo=SGT)
    
    if today_sgt.date() < trip_start.date():
        days_until = (trip_start.date() - today_sgt.date()).days
        return f"🏖️ **Thailand Trip Countdown**\n\n{days_until} days until departure!\n\n✈️ Flight TR674: 14 Jun 14:30 SG>CM\n✈️ Flight TR671: 19 Jun 19:20 CR>SG\n\n_Today: {date_display}_"
    
    if today_sgt.date() > trip_end.date():
        return f"🏖️ **Thailand Trip**\n\nTrip completed on 19 June 2026!\n\n_Today: {date_display}_"
    
    lines = []
    lines.append(f"🏖️ **Thailand Trip — Day Briefing**")
    lines.append(f"📅 {date_display}")
    lines.append("")
    
    # Weather section
    if weather:
        lines.append(f"**🌤️ Weather Today**")
        lines.append(f"{weather['rain_emoji']} {weather['condition']}")
        lines.append(f"🌡️ {weather['high']}°C / {weather['low']}°C  |  🌧️ Rain: {weather['rain_prob']} ({weather['rain_mm']}mm)")
        
        # Weather tips based on conditions
        if weather['rain_mm'] > 10:
            lines.append(f"⚠️ Heavy rain expected — bring waterproof gear, consider indoor alternatives")
        elif weather['rain_mm'] > 5:
            lines.append(f"🌦️ Likely afternoon showers — carry raincoat/umbrella")
        elif weather['rain_mm'] > 2:
            lines.append(f"🌤️ Possible light rain — pack a light rain jacket")
        else:
            lines.append(f"☀️ Mostly dry — good day for outdoor activities!")
        
        # Humidity note
        lines.append(f"💧 Humidity: ~79% — stay hydrated, wear breathable clothing")
        
        # Wet weather contingency for this specific day
        day = today_sgt.day
        if day in WET_WEATHER and weather['rain_mm'] > 2:
            ww = WET_WEATHER[day]
            lines.append("")
            lines.append(f"**🌧️ Wet Weather Plan (Day {day})**")
            if ww.get('swap_to') and weather['rain_mm'] > 5:
                lines.append(f"🔄 {ww['swap_to']}")
            if ww.get('skip_if_heavy'):
                lines.append(f"⛔ Skip if heavy: {ww['skip_if_heavy']}")
            if ww.get('indoor') and weather['rain_mm'] > 5:
                lines.append(f"🏠 Indoor backup: {ww['indoor']}")
            if ww.get('covered'):
                lines.append(f"☂️ Covered options: {ww['covered']}")
        
        lines.append("")
     
    if not activities:
        lines.append("_No activities scheduled for today._")
        lines.append("")
        lines.append("💡 Suggestions:")
        lines.append("• Free & easy day — explore the area")
        lines.append("• Check KIV items from the itinerary")
        lines.append("• Spa day or cooking class")
    else:
        morning, afternoon, evening, all_day, kiv = [], [], [], [], []
        
        for a in activities:
            t = a.get('time', '').strip()
            status = a.get('status', '')
            if status == 'kiv':
                kiv.append(a)
            elif not t:
                all_day.append(a)
            elif any(x in t for x in ['06', '07', '08', '09', '10', '11', 'AM', 'am']):
                morning.append(a)
            elif any(x in t for x in ['12', '13', '14', '15', '16', 'PM', 'pm']) and '19' not in t and '20' not in t:
                afternoon.append(a)
            else:
                evening.append(a)
        
        if morning:
            lines.append("**🌅 Morning**")
            for a in morning:
                icon = type_icon(a['type'])
                time_str = f" _{a['time']}_" if a['time'] else ""
                lines.append(f"{icon} **{a['activity']}**{time_str}")
                if a['details']:
                    lines.append(f"   {a['details'][:100]}")
            lines.append("")
        
        if afternoon:
            lines.append("**☀️ Afternoon**")
            for a in afternoon:
                icon = type_icon(a['type'])
                time_str = f" _{a['time']}_" if a['time'] else ""
                lines.append(f"{icon} **{a['activity']}**{time_str}")
                if a['details']:
                    lines.append(f"   {a['details'][:100]}")
            lines.append("")
        
        if evening:
            lines.append("**🌙 Evening**")
            for a in evening:
                icon = type_icon(a['type'])
                time_str = f" _{a['time']}_" if a['time'] else ""
                lines.append(f"{icon} **{a['activity']}**{time_str}")
                if a['details']:
                    lines.append(f"   {a['details'][:100]}")
            lines.append("")
        
        if all_day:
            for a in all_day:
                icon = type_icon(a['type'])
                lines.append(f"{icon} **{a['activity']}**")
                if a['details']:
                    lines.append(f"   {a['details'][:100]}")
            lines.append("")
        
        if kiv:
            lines.append("**💡 KIV (Options)**")
            for a in kiv:
                lines.append(f"• {a['activity']}")
                if a['details']:
                    lines.append(f"  _{a['details'][:80]}_")
            lines.append("")
    
    for f in flights:
        lines.append(f"✈️ **Flight**: {f['item']} — {f['date']}")
    
    if expenses:
        has_values = False
        exp_lines = []
        for key, label in [('total_thb', 'THB'), ('total_sgd', 'SGD'), ('jian_ming', 'Jian Ming'), ('sheryl', 'Sheryl')]:
            val = expenses.get(key, '')
            if val and val not in ('', '0') and not val.startswith('='):
                exp_lines.append(f"{label}: {val}")
                has_values = True
        if has_values:
            lines.append("")
            lines.append("**💰 Trip Expenses So Far**")
            lines.extend(exp_lines)
    
    lines.append("")
    lines.append("_Hermes • 7:00 AM SGT_")
    
    return '\n'.join(lines)

if __name__ == '__main__':
    now_sgt = datetime.now(SGT)
    output = format_daily_summary(now_sgt)
    print(output)
