"""IFC file validation utilities."""

import logging
import os
import re

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils.deconstruct import deconstructible

logger = logging.getLogger(__name__)

# Size enforcement is owned by the upload view (see FileUploadView), which
# reads the admin-tunable cap from core.models.SiteStorageConfig. The
# validator now only checks content format.

# IFC file signatures (STEP format)
IFC_SIGNATURES = [
    b"ISO-10303-21",  # Standard STEP header
    b"FILE_DESCRIPTION",  # Alternative start
]


def sniff_schema(file_obj) -> str:
    """Read the FILE_SCHEMA declaration from an IFC file header without full parsing.

    Returns the schema identifier string (e.g. 'IFC4', 'IFC2X3') or empty string
    if it cannot be determined. Resets the file pointer after reading.
    """
    try:
        chunk = file_obj.read(4096)
        file_obj.seek(0)
        if isinstance(chunk, bytes):
            chunk = chunk.decode("ascii", errors="ignore")
        match = re.search(
            r"FILE_SCHEMA\s*\(\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\)",
            chunk,
            re.IGNORECASE,
        )
        return match.group(1).upper() if match else ""
    except Exception:
        return ""


def validate_ifc_file(file: UploadedFile) -> tuple[bool, str]:
    """
    Validate that an uploaded file is a valid IFC file.

    Checks:
    1. File extension is exactly .ifc
    2. File is non-empty
    3. File content starts with valid IFC/STEP header

    Size policy is enforced separately at the view layer using the
    admin-tunable cap on ``core.models.SiteStorageConfig.per_file_cap_bytes``
    so the limit can be raised/lowered without code changes.

    Args:
        file: The uploaded file to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    # 1. Check extension (must be exactly .ifc at the end)
    filename = file.name.lower()
    _, ext = os.path.splitext(filename)

    if ext != ".ifc":
        return False, f"Invalid file extension '{ext}'. Only .ifc files are allowed."

    # 2. Reject empty uploads (content check needs at least a header).
    if file.size == 0:
        return False, "File is empty."

    # 3. Check file content (read first 1KB to check header)
    try:
        # Save current position
        current_pos = file.tell()
        file.seek(0)

        # Read first chunk
        header = file.read(1024)

        # Reset position
        file.seek(current_pos)

        # Check for valid IFC signature
        if not _has_valid_ifc_signature(header):
            return False, "Invalid IFC file format. File does not contain valid IFC/STEP header."

    except Exception as e:
        logger.warning(f"Error reading file header: {e}")
        return False, "Could not read file content for validation."

    return True, ""


def _has_valid_ifc_signature(header: bytes) -> bool:
    """
    Check if the file header contains a valid IFC signature.

    IFC files use the STEP format and should contain
    'ISO-10303-21' or 'FILE_DESCRIPTION' near the beginning.
    """
    # Decode to string for easier searching (IFC is text-based)
    try:
        # Try UTF-8 first, then latin-1 as fallback
        try:
            header_str = header.decode("utf-8")
        except UnicodeDecodeError:
            header_str = header.decode("latin-1")

        header_upper = header_str.upper()

        # Check for STEP/IFC signatures
        if "ISO-10303-21" in header_upper:
            return True
        if "FILE_DESCRIPTION" in header_upper:
            return True
        if "IFC2X" in header_upper or "IFC4" in header_upper:
            return True

    except Exception as e:
        logger.debug(f"Could not decode header: {e}")

        # Fallback: check bytes directly
        for signature in IFC_SIGNATURES:
            if signature in header.upper():
                return True

    return False


@deconstructible
class IFCFileValidator:
    """
    Django validator for IFC file fields.

    Usage in model:
        file = models.FileField(validators=[IFCFileValidator()])

    Content-only: size enforcement lives in the upload view (admin-tunable
    via ``SiteStorageConfig.per_file_cap_bytes``).
    """

    def __call__(self, file: UploadedFile) -> None:
        is_valid, error_message = validate_ifc_file(file)
        if not is_valid:
            raise ValidationError(error_message)

    def __eq__(self, other):
        return isinstance(other, IFCFileValidator)
