"""
Shared helpers for the NZAA plane watcher scripts.

Notification decision logic (see README for the reasoning):
1. Registration is in known_specials.json -> SUPPRESS. You already know
   about it and don't want repeat notifications for it.
2. Registration has never been seen before (not in seen_registrations.json)
   -> NOTIFY as "first time seeing this aircraft". Recorded so it won't
   fire again next time.
3. Otherwise, if the aircraft TYPE isn't in common_types.txt -> NOTIFY as
   a rare type, regardless of registration history.
4. Otherwise -> no notification (an ordinary, already-seen, common-type visitor).
"""

import json
import os

COMMON_TYPES_FILE = "common_types.txt"
KNOWN_SPECIALS_FILE = "known_specials.json"
SEEN_REGISTRATIONS_FILE = "seen_registrations.json"


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_common_types(path=COMMON_TYPES_FILE):
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {line.strip().upper() for line in f if line.strip() and not line.startswith("#")}


def load_known_specials(path=KNOWN_SPECIALS_FILE):
    data = load_json(path, {})
    return {k: v for k, v in data.items() if not k.startswith("_")}


def load_seen_registrations(path=SEEN_REGISTRATIONS_FILE):
    return load_json(path, {})


def save_seen_registrations(data, path=SEEN_REGISTRATIONS_FILE):
    save_json(path, data)


def decide_notification(registration, typecode, common_types, known_specials, seen_registrations, today):
    """
    Returns (should_notify: bool, reason: str or None).
    Also mutates seen_registrations in place to record this sighting
    (caller is responsible for saving it after the run).
    """
    reg_key = (registration or "").upper()
    typecode = (typecode or "").upper()

    if reg_key and reg_key in {k.upper() for k in known_specials}:
        # Known regular - make sure it's on record, but never notify.
        if reg_key not in seen_registrations:
            seen_registrations[reg_key] = {"first_seen": today, "count": 1}
        else:
            seen_registrations[reg_key]["count"] = seen_registrations[reg_key].get("count", 0) + 1
        return False, None

    is_first_sighting = bool(reg_key) and reg_key not in seen_registrations
    is_rare_type = bool(typecode) and typecode not in common_types

    if reg_key:
        if reg_key not in seen_registrations:
            seen_registrations[reg_key] = {"first_seen": today, "count": 1}
        else:
            seen_registrations[reg_key]["count"] = seen_registrations[reg_key].get("count", 0) + 1

    if is_first_sighting:
        return True, "first time this registration has been seen"
    if is_rare_type:
        return True, f"uncommon type for NZAA ({typecode or 'type unknown'})"
    return False, None
