import hashlib
import json
from typing import Any


def stable_hash(value: Any) -> str:
    if isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=repr).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
