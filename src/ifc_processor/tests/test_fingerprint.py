# ifc_processor/tests/test_fingerprint.py
"""The fingerprint is a plain SHA-256 of the bytes: stable, and sensitive to one byte."""

import hashlib

from ifc_processor.services.fingerprint import compute_fingerprint


def test_fingerprint_is_sha256_of_the_bytes(tmp_path):
    """Matches hashlib on the same bytes, so any tool can recompute it."""
    path = tmp_path / "a.ifc"
    path.write_bytes(b"ISO-10303-21;\nDATA;\nENDSEC;\n")

    assert compute_fingerprint(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_fingerprint_changes_when_one_byte_changes(tmp_path):
    """A single appended byte yields a different fingerprint."""
    path = tmp_path / "a.ifc"
    path.write_bytes(b"abc")
    before = compute_fingerprint(path)
    path.write_bytes(b"abcd")

    assert compute_fingerprint(path) != before
