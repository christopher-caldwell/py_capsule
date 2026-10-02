from datetime import datetime, timezone
import requests
import json
from lib.helpers import resolve_base_url

is_test = str(use_test_env).lower() == "true"
base_url = resolve_base_url(use_test_env=use_test_env, variant=variant)
ENV_URL = f"{base_url}/v1/hooks/inbound"
bearer = TEST_SERVICE_API_KEY if is_test else LIVE_SERVICE_API_KEY

_headers = {
    "Authorization": f"Bearer {bearer}"
}

try:
    response = requests.post(
        ENV_URL,
        headers=_headers,
        json={
            "event": "LIST_ENTRIES",
            "record_id": record_id
        }
    )
    response.raise_for_status()
    result = response.json()
    Session.log_event("API Response", {"Resp": str(result)})
    #print(str(result))

    if not result["success"]:
        error_message = result["error"]["message"]
        Session.log_event(error_message)
        print(error_message)
        return "Something went wrong"

    if (len(result["entries"]) == 0):
        return []

    entries = result.get("entries") or []
    # Exclude entries with terminal/irrelevant states
    #excluded_states = {"completed", "voided", "declined", "moved"}
    #entries = [e for e in entries if e.get("state") not in excluded_states]

    excluded_states = {"completed", "voided", "declined", "moved"}
    general_excluded_states = {"voided"}
    now = datetime.now(timezone.utc)

    _lapsed = lapsed is True  # only exact bool True triggers the lapsed path

    general_intakes, specialist_intakes, other_entries = [], [], []

    for e in entries:
        state = e.get("state")
        if state in excluded_states:
            continue
        if _lapsed:
            start = e.get("start")
            if not start or datetime.fromisoformat(start.replace("Z", "+00:00")) >= now:
                continue
        entry_type = e.get("type")
        if entry_type == "generalIntake":
            if state in general_excluded_states:
                continue
            if state == "paired" and (start := e.get("start")) and datetime.fromisoformat(start.replace("Z", "+00:00")) >= now:
                continue
            general_intakes.append(e)
        elif entry_type == "specialistIntake":
            specialist_intakes.append(e)
        else:
            other_entries.append(e)

    # If the record has a completed specialist intake (confirmed + past start),
    # don't offer earlier specialist or general intakes for re-planning
    has_past_specialist_intake = any(
        e.get("type") == "specialistIntake"
        and (e.get("state") == "confirmed" or e.get("state") == "completed")
        and e.get("start")
        and datetime.fromisoformat(e["start"].replace("Z", "+00:00")) < now
        for e in result.get("entries", [])
    )
    if has_past_specialist_intake:
        specialist_intakes = []
        general_intakes = []

    # Keep only the latest generalIntake if multiple
    if len(general_intakes) > 1:
        general_intakes = [max(general_intakes, key=lambda e: e.get("start", ""))]

    # If any generalIntake present, drop specialistIntakes
    if general_intakes:
        specialist_intakes = []

    # Filter out past generalIntakes --> EDIT: allow past, but filter at a later step
    """
    general_intakes = [
        e for e in general_intakes
        if (start := e.get("start"))
        and datetime.fromisoformat(start.replace("Z", "+00:00")) >= now
    ]
    """

    entries = other_entries + specialist_intakes + general_intakes

    if _lapsed:
        Session.set_value("lapsed_mode", True)
        if entries:
            entries = [max(entries, key=lambda e: e.get("start", ""))]

    Session.set_value("entries", entries)
    Session.set_value("entry_count", len(entries))
    return entries

except requests.exceptions.RequestException as e:
    Session.log_event(
        f"Request failed while listing entries {str(e)}"
    )
    return "Something went wrong"  # was missing 'return'

except Exception as e:
    Session.log_event(f"Unexpected error in list_entries: {str(e)}")
    return "Something went wrong: " + str(e)
