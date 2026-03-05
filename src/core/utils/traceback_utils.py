"""Utility functions for capturing full stack traces"""

import sys
import traceback


def get_full_stack() -> str:
    """
    Capture the complete stack trace including all frames before the exception.

    This is more comprehensive than traceback.format_exc() which only shows
    frames from the exception point onwards.

    Returns:
        str: Complete formatted stack trace
    """
    exc = sys.exc_info()[0]
    stack = traceback.extract_stack()[:-1]  # Exclude this function itself

    if exc is not None:  # An exception is present
        # Remove the call to this function from the stack
        del stack[-1]

    # Build the traceback string
    trc = "Traceback (most recent call last):\n"
    stackstr = trc + "".join(traceback.format_list(stack))

    if exc is not None:
        # Append the exception info, removing duplicate "Traceback" header
        stackstr += "  " + traceback.format_exc().lstrip(trc)

    return stackstr
