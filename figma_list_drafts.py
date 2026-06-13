from curl_cffi import requests
import json
import re
import base64
from urllib.parse import unquote


BASE_URL = "https://www.figma.com"


def run(headers, user_input):
    """List all draft file names from the user's Figma account."""

    cookie = headers.get("Cookie", "")
    if not cookie:
        return {'status_code': 400, 'body': {'error': 'Cookie header is required'}}

    # Extract team_id and user_id from recent_user_data cookie
    team_id, user_id = _extract_ids_from_cookie(cookie)
    if not team_id or not user_id:
        return {'status_code': 400, 'body': {'error': 'Could not extract team_id or user_id from cookies'}}

    # Step 1: Get the drafts folder ID from user state
    try:
        state_data = _fetch_user_state(cookie, team_id)
    except AuthError:
        return {'status_code': 401, 'body': {'error': 'Session expired or unauthorized'}}
    except ApiError as e:
        return {'status_code': e.status_code, 'body': {'error': str(e)}}

    drafts_folder_id = state_data.get("meta", {}).get("drafts_folder_id")
    if not drafts_folder_id:
        return {'status_code': 500, 'body': {'error': 'Could not find drafts folder ID'}}

    # Step 2: Get files in the drafts folder
    try:
        files_data = _fetch_drafts_files(cookie, user_id, drafts_folder_id)
    except AuthError:
        return {'status_code': 401, 'body': {'error': 'Session expired or unauthorized'}}
    except ApiError as e:
        return {'status_code': e.status_code, 'body': {'error': str(e)}}

    # Extract draft names
    files = files_data.get("meta", {}).get("files", [])
    draft_names = [f.get("name", "Untitled") for f in files]

    return {
        'status_code': 200,
        'body': {
            'drafts': draft_names,
            'count': len(draft_names)
        }
    }

# === PRIVATE ===


class AuthError(Exception):
    """Raised when authentication fails."""
    pass


class ApiError(Exception):
    """Raised when an API call fails."""
    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code


def _fetch_user_state(cookie, team_id):
    """Fetch user state to get the drafts folder ID."""
    state_url = f"{BASE_URL}/api/user/state?team_id={team_id}"
    state_headers = {
        "Cookie": cookie,
        "Accept": "application/json",
        "x-csrf-bypass": "yes",
    }

    state_response = requests.get(
        state_url,
        headers=state_headers,
        impersonate="chrome131",
        timeout=30
    )

    if _is_auth_failure(state_response):
        raise AuthError("Session expired or unauthorized")

    try:
        state_data = state_response.json()
    except Exception:
        raise ApiError("Failed to parse user state response", 500)

    if state_data.get("error"):
        raise ApiError("Failed to get user state", state_response.status_code)

    return state_data


def _fetch_drafts_files(cookie, user_id, drafts_folder_id):
    """Fetch paginated files from the drafts folder."""
    files_url = f"{BASE_URL}/api/folders/{drafts_folder_id}/paginated_files"
    files_params = {
        "folderId": drafts_folder_id,
        "sort_column": "touched_at",
        "sort_order": "desc",
        "fetch_only_trashed_with_folder_files": "false",
        "page_size": "100",
        "skip_fetching_repo_branches": "true",
        "file_type": ""
    }

    files_headers = {
        "Cookie": cookie,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-csrf-bypass": "yes",
        "x-figma-user-id": user_id,
    }

    files_response = requests.get(
        files_url,
        params=files_params,
        headers=files_headers,
        impersonate="chrome131",
        timeout=30
    )

    if _is_auth_failure(files_response):
        raise AuthError("Session expired or unauthorized")

    try:
        files_data = files_response.json()
    except Exception:
        raise ApiError("Failed to parse files response", 500)

    if files_data.get("error"):
        raise ApiError("Failed to get drafts", files_response.status_code)

    return files_data


def _extract_ids_from_cookie(cookie_string):
    """Extract team_id and user_id from the recent_user_data cookie."""
    team_id = None
    user_id = None

    # Parse cookie string to find recent_user_data
    match = re.search(r'recent_user_data=([^;]+)', cookie_string)
    if match:
        try:
            # URL decode the cookie value
            encoded_value = match.group(1)
            decoded_value = unquote(encoded_value)

            # Remove surrounding quotes if present
            if decoded_value.startswith('"') and decoded_value.endswith('"'):
                decoded_value = decoded_value[1:-1]

            # The value is base64 encoded JSON
            decoded_bytes = base64.b64decode(decoded_value)
            decoded_str = decoded_bytes.decode('utf-8')
            data = json.loads(decoded_str)

            # Get user_id from fileBrowserUserId
            user_id = data.get("fileBrowserUserId")

            # Get team_id from userIdToPlan
            user_id_to_plan = data.get("userIdToPlan", {})
            if user_id and user_id in user_id_to_plan:
                plan_info = user_id_to_plan[user_id]
                if isinstance(plan_info, list) and len(plan_info) >= 2:
                    team_id = plan_info[1]
        except (json.JSONDecodeError, KeyError, IndexError, ValueError):
            pass

    return team_id, user_id


def _is_auth_failure(response):
    """Check if the response indicates an authentication failure."""
    # Check for 401 status
    if response.status_code == 401:
        return True

    # Check for redirect to login page
    if response.status_code in (301, 302, 303, 307, 308):
        location = response.headers.get("Location", "")
        if "login" in location.lower():
            return True

    # Check response body for login indicators
    try:
        if response.status_code == 200:
            text = response.text[:1000]
            if '"error":true' in text and ('login' in text.lower() or 'auth' in text.lower()):
                return True
    except Exception:
        pass

    return False
