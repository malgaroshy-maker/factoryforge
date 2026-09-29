"""FactoryForge driver sidecar."""

__version__ = "0.1.0"

AUTHOR = "Mahamed Algaroshy"
AUTHOR_AR = "محمد الجروشي"


def credit_line(encoding: str | None = None) -> str:
    """The "developed by" credit, with the Arabic only if the console can show it.

    A Windows console on cp1252 raises UnicodeEncodeError on the Arabic name,
    and a credit line must never be the thing that kills the sidecar, so the
    Arabic is dropped when the stream's encoding cannot carry it.
    """
    import sys
    enc = encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        AUTHOR_AR.encode(enc)
    except (UnicodeEncodeError, LookupError):
        return f"developed by {AUTHOR}"
    return f"developed by {AUTHOR} ({AUTHOR_AR})"
