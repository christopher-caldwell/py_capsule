import requests
from datetime import datetime, timedelta, timezone
import zoneinfo
from lib.helpers import resolve_base_url

is_test = str(use_test_env).lower() == "true"
base_url = resolve_base_url(use_test_env=use_test_env, variant=variant)
url = f"{base_url}/v1/hooks/voice"
bearer = TEST_SERVICE_API_KEY if is_test else LIVE_SERVICE_API_KEY
headers = {"Authorization": f"Bearer {bearer}"}

WINDOW_HOURS = {
    "morning": (8, 12),
    "afternoon": (12, 16),
    "evening": (16, 20),
    "asap": (8, 20),
}

def fits_window(opening: dict, window: str, caller_tz: str) -> bool:
    if not window:
        return True
    if not caller_tz:
        return True
    opening_dt_local = datetime.fromisoformat(opening["start"]).astimezone(zoneinfo.ZoneInfo(caller_tz))
    win_start, win_end = WINDOW_HOURS.get(window.strip().lower(), (9, 20))
    return win_start <= opening_dt_local.hour < win_end

try:
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=30)
    start_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_iso = end.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    target_date = datetime.strptime(target_day, "%Y-%m-%d").replace(tzinfo=timezone.utc) if target_day else now
    window_key = target_window.strip().lower() if target_window else "asap"
    win_start, win_end = WINDOW_HOURS.get(window_key, (8, 20))
    win_start_dt = target_date.replace(hour=win_start, minute=0, second=0, microsecond=0)
    win_end_dt = target_date.replace(hour=win_end, minute=0, second=0, microsecond=0)
    win_start_iso = win_start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    win_end_iso = win_end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    print(win_start_iso)
    print(win_end_iso)

    payload = {
        "args": {
            "event": "FIND_OPENINGS",
            "contextId": context_id,
            "requestId": request_id,
            "rangeStart": start_iso,
            "rangeEnd": end_iso,
            "windows": [{"start": win_start_iso, "end": win_end_iso}]
        }
    }
    Session.log_event("Find Openings Request Payload", {"Payload": str(payload)})

    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    result = response.json()
    Session.log_event("Find Openings API Response", {"Response": str(result)})
    print(str(result))
    openings = result.get("openings", [])

    matched = [
        opening for opening in openings
        if fits_window(opening, target_window, caller_timezone)
    ][:5]

    Session.set_value("window_empty", len(matched) == 0)

    if len(matched) == 0:
        matched = openings[:5]

    Session.set_value("none_found", len(openings) == 0)
    Session.set_value("offered_openings", matched)
    Session.set_value("requested_window", target_window)
    Session.set_value("requested_day", target_day)
    Session.log_event("FIND_OPENINGS succeeded", {"opening_count": len(openings)})

    return {"openings": matched}

except Exception as e:
    Session.log_failure("find_openings_failed", {"error": str(e)})
    return {"openings": [], "error": str(e)}
