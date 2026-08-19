import mimetypes
import re
from pathlib import PurePath

from .errors import ExpenseFlowError
from .models import utc_now


ALLOWED_RECEIPT_CONTENT_TYPES = {
    "application/pdf",
    "image/heic",
    "image/jpeg",
    "image/png",
    "image/webp",
}


def normalize_receipt_attachment(data, settings=None):
    settings = settings or {}
    if not isinstance(data, dict):
        raise ExpenseFlowError("invalid_receipt_attachment", "Receipt attachment must be an object.")
    object_id = str(
        data.get("object_store_object_id")
        or data.get("objectStoreObjectId")
        or data.get("receipt_ref")
        or ""
    ).strip()
    reference = str(data.get("reference") or data.get("receipt_url") or "").strip()
    if not object_id and reference.startswith("kolo://obj/"):
        object_id = reference.removeprefix("kolo://obj/").strip()
    if not object_id:
        raise ExpenseFlowError(
            "missing_receipt_object_id",
            "Receipt attachment requires a Kolo object-store ID.",
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", object_id):
        raise ExpenseFlowError("invalid_receipt_object_id", "Receipt object-store ID has an invalid format.")
    expected_reference = f"kolo://obj/{object_id}"
    if reference and reference != expected_reference:
        raise ExpenseFlowError(
            "receipt_reference_mismatch",
            "Receipt reference does not match the supplied object-store ID.",
        )
    reference = reference or expected_reference

    filename = PurePath(str(data.get("filename") or "receipt")).name
    content_type = str(data.get("content_type") or data.get("contentType") or "").strip().lower()
    if not content_type:
        content_type = (mimetypes.guess_type(filename)[0] or "").lower()
    if content_type not in ALLOWED_RECEIPT_CONTENT_TYPES:
        raise ExpenseFlowError(
            "unsupported_receipt_type",
            "Receipt must be a PDF or supported image type.",
            details={"content_type": content_type or None},
        )

    size_bytes = _optional_nonnegative_int(data.get("size_bytes", data.get("sizeBytes")), "size_bytes")
    max_receipt_bytes = _optional_nonnegative_int(settings.get("max_receipt_bytes"), "max_receipt_bytes")
    if size_bytes is not None and max_receipt_bytes is not None and size_bytes > max_receipt_bytes:
        raise ExpenseFlowError(
            "receipt_too_large",
            "Receipt exceeds the organization's configured size limit.",
            details={"size_bytes": size_bytes, "max_receipt_bytes": max_receipt_bytes},
        )

    sha256 = str(data.get("sha256") or "").strip().lower() or None
    if sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ExpenseFlowError("invalid_receipt_hash", "Receipt SHA-256 must be 64 hexadecimal characters.")

    return {
        "attachment_id": sha256 or object_id,
        "object_store_object_id": object_id,
        "reference": reference,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "stored_at": str(data.get("stored_at") or utc_now()),
    }


def _optional_nonnegative_int(value, field):
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ExpenseFlowError(f"invalid_{field}", f"{field} must be a non-negative integer.")
    if parsed < 0:
        raise ExpenseFlowError(f"invalid_{field}", f"{field} must be a non-negative integer.")
    return parsed
