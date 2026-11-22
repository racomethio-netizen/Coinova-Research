"""Return freed heap memory to the operating system (512 MB hosting)."""

from __future__ import annotations

import gc


def release() -> None:
    """Collect garbage and ask glibc to hand freed pages back (no-op elsewhere)."""
    gc.collect()
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass
