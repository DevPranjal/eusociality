"""Deterministic measurement control, never a native-agent research outcome."""

import json
import sys

try:
    incoming = json.load(sys.stdin)
    if not isinstance(incoming, dict) or not isinstance(incoming.get("records"), list):
        raise ValueError("invalid_input")
    public = []
    for record in incoming["records"]:
        if not isinstance(record, dict):
            raise ValueError("invalid_input")
        for name in ("id", "title"):
            if not isinstance(record.get(name), str) or not record[name].strip():
                raise ValueError("invalid_input")
        if record.get("status") not in ("open", "closed"):
            raise ValueError("invalid_input")
        labels = record.get("labels", [])
        if not isinstance(labels, list) or not all(isinstance(item, str) for item in labels):
            raise ValueError("invalid_input")
        public.append({
            "id": record["id"], "title": record["title"],
            "status": record["status"], "labels": labels,
        })
    print(json.dumps({"records": public}))
except ValueError:
    print(json.dumps({"error": "invalid_input"}))
    sys.exit(2)
