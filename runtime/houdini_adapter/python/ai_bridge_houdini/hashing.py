import hashlib
import json


def stable_hash(value):
    if isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=repr).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
