"""Minimal structured logger — every event is a single JSON-able line."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any


def log(event: str, **kw: Any) -> None:
    payload = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event, **kw}
    print(json.dumps(payload, ensure_ascii=False, default=str), file=sys.stderr, flush=True)
