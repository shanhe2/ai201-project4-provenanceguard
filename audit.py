import json
import os

LOG_PATH = os.path.join(os.path.dirname(__file__), "audit_log.jsonl")


def append_entry(entry: dict) -> None:
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def read_entries(limit: int = 50, offset: int = 0) -> tuple:
    if not os.path.exists(LOG_PATH):
        return [], 0
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    entries = [json.loads(ln) for ln in lines]
    return entries[offset: offset + limit], len(entries)


def entry_exists(content_id: str) -> bool:
    if not os.path.exists(LOG_PATH):
        return False
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip() and json.loads(line).get("content_id") == content_id:
                return True
    return False


def update_entry(content_id: str, updates: dict) -> bool:
    if not os.path.exists(LOG_PATH):
        return False
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    found = False
    new_lines = []
    for line in lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("content_id") == content_id:
            entry.update(updates)
            found = True
        new_lines.append(json.dumps(entry) + "\n")
    if found:
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    return found
