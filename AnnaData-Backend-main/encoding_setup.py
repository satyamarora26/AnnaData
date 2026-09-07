"""Force UTF-8 on stdout/stderr.

Farmer queries and answers are routinely Devanagari, Gurmukhi, Telugu and so on.
On a non-UTF-8 console (Windows cp1252 by default) a bare print() of that text
raises UnicodeEncodeError, which killed the request mid-flight. Applied at
import so every print in the process is safe.
"""
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
