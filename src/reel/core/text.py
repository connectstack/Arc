"""Text hygiene: strings that arrive from JSON, an LLM or a pasted script may hold characters nothing downstream can use.

* **lone surrogates** (half an emoji, e.g. a string cut in the middle of one by a UTF-16 ``slice``): Python keeps
  them, but encoding them to UTF-8 raises ``UnicodeEncodeError`` inside skia, ``say`` or ``json``;
* **control characters** (NUL, bell, escape ...): a NUL cannot even be passed on a command line.

:func:`clean_text` joins valid surrogate pairs back into the character they encode and drops what is left;
control characters other than tab and newline become a space.  The spec loader applies it to every string of a
spec (:func:`clean_data`) and the lint pass reports what was changed (:func:`bad_text_reason`).
"""

from __future__ import annotations

import re
from typing import Any

_SURROGATE = re.compile("[\ud800-\udfff]")
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # everything below space but \t \n \r


def bad_text_reason(s: str) -> str | None:
    """Why ``s`` would be changed by :func:`clean_text` (``None`` when it would not)."""
    if s.isascii() and not _CONTROL.search(s):
        return None
    lone = _SURROGATE.search(_join_pairs(s)) if _SURROGATE.search(s) else None
    if lone:
        return "a lone UTF-16 surrogate (half of an emoji or other character)"
    if _CONTROL.search(s):
        return "a control character"
    return None


def _join_pairs(s: str) -> str:
    """Surrogate pairs (``"\\ud83d\\ude00"``) as the one character they encode; lone surrogates stay."""
    return s.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


def clean_text(s: str) -> str:
    """``s`` without lone surrogates and control characters (a no-op, and fast, for ordinary text)."""
    if s.isascii() and not _CONTROL.search(s):
        return s
    if _SURROGATE.search(s):
        s = _SURROGATE.sub("", _join_pairs(s))
    return _CONTROL.sub(" ", s)


def clean_data(obj: Any) -> Any:
    """:func:`clean_text` applied to every string (and dict key) inside parsed JSON; other values pass through."""
    if isinstance(obj, str):
        return clean_text(obj)
    if isinstance(obj, dict):
        return {(clean_text(k) if isinstance(k, str) else k): clean_data(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_data(v) for v in obj]
    return obj
