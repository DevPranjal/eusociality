import io
import sys

from .cli import app

for stream in (sys.stdin, sys.stdout, sys.stderr):
    if isinstance(stream, io.TextIOWrapper):
        stream.reconfigure(encoding="utf-8")

app()
