#!/usr/bin/env python3
"""
NZAA (Auckland Airport) arrival/landing confirmation watcher.

Uses airplanes.live's free public API (no key needed, and unlike OpenSky
it doesn't block cloud-hosted callers like GitHub Actions runners).
It also returns registration + aircraft type directly per aircraft, so
no separate metadata lookup call is needed.

An aircraft counts as "arrived" if EITHER:
  - alt_baro reports "ground" (airplanes.live's on-ground indicator), OR
  - its barometric altitude is below LOW_ALTITUDE_FT (catches short final
    / just touched down, in case the ground flag lags).

Notification logic is shared with check_departures.py - see nzaa_common.py.
"""

import os
import json
import sys
from datetime import datetime, timezone
import requests

from nzaa_common import (
    load_common_types,
    load_known_specials,
    load_seen_registrations,
    save_seen_registrations,
    decide_notification,
)

# NZAA (Auckland Airport) coordinates
NZAA_LAT = -37.008
NZAA_LON = 174.792
RADIUS_NM = 10  # nautical miles around NZAA to scan

LOW_ALTITUDE_FT = 500  # catches "on short final / just touched down"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

DAILY_STATE_FILE = "seen.json"

AIRPLANES_LIVE_URL = f"https://api.airplanes.live/v2/point/{NZAA_LAT}/{NZAA_LON}/{RADIUS_NM}"


def load_daily_state():
    if os.path.exists(DAILY_STATE_FILE):
        with open(DAILY_STATE_FILE) as f:
            return json.load(f)
    return {}


def save_daily_state(data):
    with open(DAILY_STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def fetch_aircraft():
    resp = requests.get(AIRPLANES_LIVE_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("ac") or []


def send_notification(title, message):
    if not NTFY_TOPIC:
        print(f"[NO NTFY_TOPIC SET] Would have notified: {title} - {message}")
        return
    try:
        requests.post(
            NTFY_URL,
            data=message.encode("utf-8"),
            headers={"Title": title.encode("utf-8"), "Priority": "default", "Tags": "airplane"},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"Failed to send notification: {e}")


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_seen = load_daily_state()
    daily_seen = {today: daily_seen.get(today, [])}

    common_types = load_common_types()
    known_specials = load_known_specials()
    seen_registrations = load_seen_registrations()

    try:
        aircraft_list = fetch_aircraft()
    except requests.RequestException as e:
        print(f"Error fetching data from airplanes.live: {e}")
        sys.exit(0)

    print(f"[diagnostic] airplanes.live returned {len(aircraft_list)} aircraft total within {RADIUS_NM}nm of NZAA this run.")

    ground_count = 0
    low_alt_count = 0
    checked_count = 0

    for ac in aircraft_list:
        hex_id = (ac.get("hex") or "").strip().lower()
        alt_baro = ac.get("alt_baro")

        is_on_ground = alt_baro == "ground"
        is_low_alt = isinstance(alt_baro, (int, float)) and alt_baro < LOW_ALTITUDE_FT

        if is_on_ground:
            ground_count += 1
        if is_low_alt:
            low_alt_count += 1

        if not hex_id or not (is_on_ground or is_low_alt):
            continue
        if hex_id in daily_seen[today]:
            continue

        checked_count += 1
        callsign = (ac.get("flight") or "").strip() or "unknown callsign"
        registration = (ac.get("r") or "").strip()
        typecode = (ac.get("t") or "").strip().upper()
        operator = ac.get("ownOp") or "unknown operator"
        model = ac.get("desc") or typecode or "unknown type"

        if registration:
            should_notify, reason = decide_notification(
                registration, typecode, common_types, known_specials, seen_registrations, today
            )
            if should_notify:
                title = f"New/unexpected plane at NZAA: {registration}"
                message = (
                    f"{operator} {model}\n"
                    f"Reg: {registration}  Callsign: {callsign}\n"
                    f"Why flagged: {reason}"
                )
                print(f"NOTIFY -> {title} | {message}")
                send_notification(title, message)
        else:
            print(f"[diagnostic] hex={hex_id} callsign={callsign} matched ground/low-alt but had no registration in the response.")

        daily_seen[today].append(hex_id)

    print(f"[diagnostic] on_ground={ground_count}, low_altitude(<{LOW_ALTITUDE_FT}ft)={low_alt_count}, newly checked this run={checked_count}")

    save_daily_state(daily_seen)
    save_seen_registrations(seen_registrations)


if __name__ == "__main__":
    main()
