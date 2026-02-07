"""IFC file validation utilities."""

import logging
import os
from typing import Tuple

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils.deconstruct import deconstructible

logger = logging.getLogger(__name__)

# Maximum file size (100 MB)
MAX_IFC_FILE_SIZE = 100 * 1024 * 1024

# IFC file signatures (STEP format)
IFC_SIGNATURES = [
    b"ISO-10303-21",  # Standard STEP header
    b"FILE_DESCRIPTION",  # Alternative start
]


def validate_ifc_file(file: UploadedFile) -> Tuple[bool, str]:
    """
    Validate that an uploaded file is a valid IFC file.

    Checks:
    1. File extension is exactly .ifc
    2. File size is within limits
    3. File content starts with valid IFC/STEP header

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

    # 2. Check file size
    if file.size > MAX_IFC_FILE_SIZE:
        size_mb = file.size / (1024 * 1024)
        max_mb = MAX_IFC_FILE_SIZE / (1024 * 1024)
        return False, f"File too large ({size_mb:.1f} MB). Maximum size is {max_mb:.0f} MB."

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
            header_str = header.decode('utf-8')
        except UnicodeDecodeError:
            header_str = header.decode('latin-1')

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
    """

    def __init__(self, max_size: int = MAX_IFC_FILE_SIZE):
        self.max_size = max_size

    def __call__(self, file: UploadedFile) -> None:
        is_valid, error_message = validate_ifc_file(file)
        if not is_valid:
            raise ValidationError(error_message)

    def __eq__(self, other):
        return isinstance(other, IFCFileValidator) and self.max_size == other.max_size