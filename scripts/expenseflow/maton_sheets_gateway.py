import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .errors import ExpenseFlowError


DEFAULT_BASE_URL = "https://gateway.maton.ai/google-sheets/v4"


class MatonSheetsGateway:
    """Narrow Google Sheets v4 adapter for Kolo's Maton integration route."""

    def __init__(self, api_key=None, base_url=DEFAULT_BASE_URL, opener=None, sleeper=None, max_attempts=3):
        self.api_key = api_key or os.environ.get("MATON_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.opener = opener or urlopen
        self.sleeper = sleeper or time.sleep
        self.max_attempts = max(1, int(max_attempts))

    def get_metadata(self, spreadsheet_id):
        return self._request(
            "GET",
            f"/spreadsheets/{_path_part(spreadsheet_id)}",
            query={"fields": "spreadsheetId,properties.title,sheets.properties"},
            safe_to_retry=True,
        )

    def read_values(self, spreadsheet_id, a1_range):
        return self._request(
            "GET",
            f"/spreadsheets/{_path_part(spreadsheet_id)}/values/{_range_part(a1_range)}",
            safe_to_retry=True,
        )

    def update_values(self, spreadsheet_id, a1_range, values):
        return self._request(
            "PUT",
            f"/spreadsheets/{_path_part(spreadsheet_id)}/values/{_range_part(a1_range)}",
            query={"valueInputOption": "RAW"},
            body={"range": a1_range, "majorDimension": "ROWS", "values": values},
            safe_to_retry=False,
        )

    def append_values(self, spreadsheet_id, a1_range, values):
        return self._request(
            "POST",
            f"/spreadsheets/{_path_part(spreadsheet_id)}/values/{_range_part(a1_range)}:append",
            query={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            body={"range": a1_range, "majorDimension": "ROWS", "values": values},
            safe_to_retry=False,
        )

    def _request(self, method, path, query=None, body=None, safe_to_retry=False):
        if not self.api_key:
            raise ExpenseFlowError(
                "sheets_connection_missing",
                "The Kolo Google Sheets connection is unavailable in this runtime.",
            )
        url = self.base_url + path
        if query:
            url += "?" + urlencode(query)
        payload = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = Request(
            url,
            data=payload,
            method=method,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        attempts = self.max_attempts if safe_to_retry else 1
        for attempt in range(1, attempts + 1):
            try:
                with self.opener(request, timeout=30) as response:
                    return _decode_json(response.read(), response.headers.get("Content-Type", ""))
            except HTTPError as exc:
                error = _http_error(exc)
                if error.retryable and safe_to_retry and attempt < attempts:
                    self.sleeper(2 ** (attempt - 1))
                    continue
                raise error
            except (TimeoutError, URLError, OSError) as exc:
                error = ExpenseFlowError(
                    "sheets_outcome_unknown" if not safe_to_retry else "sheets_unavailable",
                    "Google Sheets did not return a conclusive response.",
                    retryable=safe_to_retry,
                    details={"error_type": type(exc).__name__},
                )
                if safe_to_retry and attempt < attempts:
                    self.sleeper(2 ** (attempt - 1))
                    continue
                raise error


def _path_part(value):
    return quote(str(value), safe="")


def _range_part(value):
    return quote(str(value), safe="!:$'(),-_")


def _decode_json(body, content_type=""):
    text = body.decode("utf-8") if isinstance(body, bytes) else str(body or "")
    if not text:
        return {}
    if "html" in str(content_type).lower() or text.lstrip().lower().startswith("<!doctype html"):
        raise ExpenseFlowError(
            "sheets_unsupported_endpoint",
            "The configured Maton route did not proxy this Google Sheets endpoint.",
        )
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExpenseFlowError(
            "invalid_sheets_json",
            "Google Sheets returned an invalid JSON response.",
            details={"error": exc.msg},
        )
    if not isinstance(value, dict):
        raise ExpenseFlowError("invalid_sheets_json", "Google Sheets returned an unexpected response shape.")
    return value


def _http_error(exc):
    body = exc.read()
    try:
        payload = _decode_json(body, exc.headers.get("Content-Type", ""))
    except ExpenseFlowError as decode_error:
        return decode_error
    status = int(exc.code)
    google_error = payload.get("error", {}) if isinstance(payload, dict) else {}
    details = {
        "http_status": status,
        "google_status": google_error.get("status"),
        "google_message": google_error.get("message"),
    }
    mapping = {
        400: ("sheets_invalid_request", "Google Sheets rejected the requested range or payload.", False),
        401: ("sheets_unauthenticated", "The Kolo Google Sheets connection needs to be reauthenticated.", False),
        403: ("sheets_permission_denied", "The connected Google account cannot access this spreadsheet.", False),
        404: ("sheets_not_found", "The configured Google spreadsheet was not found.", False),
        429: ("sheets_rate_limited", "Google Sheets temporarily rate-limited the export.", True),
    }
    code, message, retryable = mapping.get(
        status,
        (
            "sheets_unavailable" if status >= 500 else "sheets_request_failed",
            "Google Sheets could not complete the request.",
            status >= 500,
        ),
    )
    return ExpenseFlowError(code, message, retryable=retryable, details=details)
