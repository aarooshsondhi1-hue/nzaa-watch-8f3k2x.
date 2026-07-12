"""
Shared helpers for the NZAA plane watcher scripts.

Notification decision logic (see README for the reasoning), in priority order:
1. Registration is in always_notify.json -> ALWAYS NOTIFY, every single
   time it's seen, even if it's a ZK- registration. For special-livery
   aircraft you specifically want to go see whenever they show up.
2. Registration starts with ZK- (NZ-registered) -> SUPPRESS entirely,
   no notification ever, regardless of type. Most of Air NZ's fleet and
   every local GA plane carries this prefix.
3. Registration is in known_specials.json -> SUPPRESS, never notify.
   You already know about it and it's not worth a repeat alert.
4. Registration never seen before -> NOTIFY once, as "first time seeing
   this aircraft". Recorded so it won't fire again for that reason.
5. Aircraft TYPE isn't in common_types.txt -> NOTIFY as a rare type,
   regardless of registration history.
6. Otherwise -> no notification (an ordinary, already-seen, common-type visitor).
"""

import json
import os

COMMON_TYPES_FILE = "common_types.txt"
KNOWN_SPECIALS_FILE = "known_specials.json"
ALWAYS_NOTIFY_FILE = "always_notify.json"
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


def load_always_notify(path=ALWAYS_NOTIFY_FILE):
    data = load_json(path, {})
    return {k: v for k, v in data.items() if not k.startswith("_")}


def load_seen_registrations(path=SEEN_REGISTRATIONS_FILE):
    return load_json(path, {})


def save_seen_registrations(data, path=SEEN_REGISTRATIONS_FILE):
    save_json(path, data)


def _record_sighting(reg_key, seen_registrations, today):
    if not reg_key:
        return
    if reg_key not in seen_registrations:
        seen_registrations[reg_key] = {"first_seen": today, "count": 1}
    else:
        seen_registrations[reg_key]["count"] = seen_registrations[reg_key].get("count", 0) + 1


def decide_notification(registration, typecode, common_types, known_specials, seen_registrations, today,
                         always_notify=None):
    """
    Returns (should_notify: bool, reason: str or None).
    Also mutates seen_registrations in place to record this sighting
    (caller is responsible for saving it after the run).
    """
    always_notify = always_notify or {}
    reg_key = (registration or "").upper()
    typecode = (typecode or "").upper()

    always_notify_upper = {k.upper(): v for k, v in always_notify.items()}
    if reg_key and reg_key in always_notify_upper:
        _record_sighting(reg_key, seen_registrations, today)
        description = always_notify_upper[reg_key]
        return True, description or "on your always-notify list"

    is_nz_registered = reg_key.startswith("ZK-") or reg_key.startswith("ZK")
    if is_nz_registered:
        # Full suppression - most of Air NZ's fleet and every local GA
        # plane carries this prefix, so it's never inherently "special"
        # by itself. Anything ZK- you DO want flagged should go in
        # always_notify.json instead (checked above, takes priority).
        _record_sighting(reg_key, seen_registrations, today)
        return False, None

    if reg_key and reg_key in {k.upper() for k in known_specials}:
        # Known regular - make sure it's on record, but never notify.
        _record_sighting(reg_key, seen_registrations, today)
        return False, None

    is_first_sighting = bool(reg_key) and reg_key not in seen_registrations
    is_rare_type = bool(typecode) and typecode not in common_types

    _record_sighting(reg_key, seen_registrations, today)

    if is_first_sighting:
        return True, "first time this registration has been seen"
    if is_rare_type:
        return True, f"uncommon type for NZAA ({typecode or 'type unknown'})"
    return False, None
