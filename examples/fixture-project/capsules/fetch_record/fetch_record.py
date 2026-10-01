import requests
import json
from lib.helpers import resolve_base_url

is_test = str(use_test_env).lower() == "true"
base_url = resolve_base_url(use_test_env=use_test_env, variant=variant)
url = f"{base_url}/v1/hooks/inbound"
api_key = TEST_SERVICE_API_KEY if is_test else LIVE_SERVICE_API_KEY
_headers = {
    "Authorization": f"Bearer {api_key}"
}
try:
    response = requests.post(
        url,
        headers=_headers,
        json={
            "event": "FETCH_RECORD",
            "record_id": record_id
        }
    )
    response.raise_for_status()
    result = response.json()

    if not result["success"]:
        error_message = result["error"]["message"]
        Session.log_event(error_message)
        print(error_message)
        return "Something went wrong"

    data = result["data"]
    return data

except requests.exceptions.RequestException as e:
    Session.log_event(
        f"Request failed while fetching record {record_id}"
    )
    return "Request failed while fetching record"
