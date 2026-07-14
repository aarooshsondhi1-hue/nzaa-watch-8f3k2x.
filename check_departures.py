#!/usr/bin/env python3
"""
NZAA-bound departure watcher (early warning).

Checks all flights currently heading TO NZAA (arr_iata=AKL) and notifies
you the moment one that matches your "special" criteria shows status
"en-route" (already left its origin airport and is flying) - giving you
roughly the flight's full duration as lead time.

Notification logic (see nzaa_common.py / README):
  - Registration on always_notify.json -> notified EVERY time.
  - Registration in known_specials.json -> never notified.
  - NZ-registered (ZK-...) and not a rare type -> never notified.
  - Registration never seen before -> notified once ("first time seeing
    this aircraft"), then goes quiet on future visits.
  - Otherwise, uncommon aircraft type -> notified every time.
  - Otherwise -> silent.

Data source: AirLabs (https://airlabs.co). Free plan = 1,000 requests a
month - 10x aviationstack's old 100/month limit.
"""

import os
import json
import math
from datetime import datetime, timezone
import requests

from nzaa_common import (
    load_common_types,
    load_known_specials,
    load_always_notify,
    load_seen_registrations,
    save_seen_registrations,
    decide_notification,
)

API_KEY = os.environ.get("AIRLABS_KEY", "")
API_URL = "https://airlabs.co/api/v9/flights"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

# Custom notification icon (shown instead of ntfy's default logo).
NTFY_ICON_URL = "https://raw.githubusercontent.com/aarooshsondhi1-hue/nzaa-watch-8f3k2x./main/file_0000000034a07207b3999d5a6465d8bc.png"

DAILY_STATE_FILE = "seen_departures.json"

# NZAA (Auckland Airport) coordinates - used for the ETA estimate below.
NZAA_LAT = -37.008
NZAA_LON = 174.792

# Statuses that mean "has already left the ground" - AirLabs uses
# lowercase status strings like "en-route", "scheduled", "landed".
DEPARTED_STATUSES = {"en-route", "landed"}

# Typical CRUISE speed in km/h per ICAO type code - deliberately NOT
# using the aircraft's live/instantaneous speed field, since that was
# found (in testing) to sometimes reflect taxi or initial-climb speed
# right at the moment a flight flips to "departed", producing wildly
# inflated ETAs (a plane doing 20 km/h over a 5,000km route "estimates"
# at 44 days). A fixed assumed cruise speed per type avoids that
# entirely, at the cost of being a rougher approximation for any given
# flight (headwinds/tailwinds, routing, etc. aren't accounted for).
CRUISE_SPEED_KMH = {
    "A320": 830, "A20N": 830, "A321": 830, "A21N": 830,
    "AT72": 500, "AT76": 500, "DH8D": 550,
    "B738": 830, "B739": 830, "B737": 830,
    "B772": 900, "B773": 905, "B77W": 905,
    "B788": 890, "B789": 913,
    "A332": 870, "A333": 871, "A339": 900,
    "B763": 851, "B764": 851,
    "A359": 903, "A35K": 903,
    "E190": 829, "E195": 829,
}
DEFAULT_CRUISE_SPEED_KMH = 850  # reasonable generic jet cruise speed for any type not listed above


def haversine_km(lat1, lon1, lat2, lon2):
    r_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r_km * c


def estimate_eta_minutes(lat, lon, typecode):
    """ETA using live position + an assumed cruise speed for the type
    (not the live speed field - see comment on CRUISE_SPEED_KMH above)."""
    if lat is None or lon is None:
        return None
    speed_kmh = CRUISE_SPEED_KMH.get(typecode, DEFAULT_CRUISE_SPEED_KMH)
    distance_km = haversine_km(lat, lon, NZAA_LAT, NZAA_LON)
    return (distance_km / speed_kmh) * 60


def load_daily_state():
    if os.path.exists(DAILY_STATE_FILE):
        with open(DAILY_STATE_FILE) as f:
            return json.load(f)
    return {}


def save_daily_state(data):
    with open(DAILY_STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def fetch_inbound_flights():
    if not API_KEY:
        print("ERROR: AIRLABS_KEY is not set. Skipping this run.")
        return []
    params = {"api_key": API_KEY, "arr_iata": "AKL"}
    resp = requests.get(API_URL, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    # AirLabs typically wraps results in {"response": [...]}; be
    # defensive in case a bare list ever comes back instead.
    if isinstance(payload, dict):
        if "error" in payload:
            print(f"AirLabs API error: {payload['error']}")
            return []
        return payload.get("response") or []
    return payload or []


def send_notification(title, message):
    if not NTFY_TOPIC:
        print(f"[NO NTFY_TOPIC SET] Would have notified: {title} - {message}")
        return
    try:
        requests.post(
            NTFY_URL,
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": "high",
                "Tags": "airplane,departure",
                "Icon": NTFY_ICON_URL,
            },
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"Failed to send notification: {e}")


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    daily_seen = load_daily_state()
    daily_seen = {today: daily_seen.get(today, [])}  # drop older days

    common_types = load_common_types()
    known_specials = load_known_specials()
    always_notify = load_always_notify()
    seen_registrations = load_seen_registrations()

    try:
        flights = fetch_inbound_flights()
    except requests.RequestException as e:
        print(f"Error fetching data from AirLabs: {e}")
        return

    print(f"[diagnostic] AirLabs returned {len(flights)} total flights inbound to AKL this run.")

    departed_count = 0
    no_registration_count = 0
    already_seen_count = 0
    checked_count = 0

    for f in flights:
        status = (f.get("status") or "").lower()
        flight_num = f.get("flight_iata") or f.get("flight_icao") or "unknown flight"

        if status not in DEPARTED_STATUSES:
            continue
        departed_count += 1

        if flight_num in daily_seen[today]:
            already_seen_count += 1
            continue

        registration = (f.get("reg_number") or "").strip()
        typecode = (f.get("aircraft_icao") or "").strip().upper()

        dep_airport = f.get("dep_iata") or f.get("dep_icao") or "unknown origin"
        dep_time = f.get("dep_time_utc") or f.get("dep_time")

        eta = f.get("arr_time_utc") or f.get("arr_time")
        if not eta:
            estimated = estimate_eta_minutes(f.get("lat"), f.get("lng"), typecode)
            if estimated is not None:
                eta = f"~{estimated:.0f} min (estimated)"

        airline = f.get("airline_icao") or f.get("airline_iata") or "unknown operator"

        if registration:  # only judge flights where we actually know the tail
            checked_count += 1
            should_notify, reason = decide_notification(
                registration, typecode, common_types, known_specials, seen_registrations, today,
                always_notify=always_notify,
            )
            if should_notify:
                title = f"New/unexpected plane heading to NZAA: {registration}"
                dep_line = f"From: {dep_airport}"
                if dep_time:
                    dep_line += f"  Departed: {dep_time}"
                lines = [
                    f"{airline} {flight_num} ({typecode or 'type unknown'})",
                    f"Reg: {registration}",
                    dep_line,
                ]
                if eta:
                    lines.append(f"ETA Auckland: {eta}")
                lines.append(f"Why flagged: {reason}")
                message = "\n".join(lines)
                print(f"NOTIFY -> {title} | {message}")
                send_notification(title, message)
        else:
            no_registration_count += 1

        daily_seen[today].append(flight_num)

    print(f"[diagnostic] departed={departed_count}, already logged today={already_seen_count}, "
          f"missing registration data={no_registration_count}, newly judged this run={checked_count}")

    save_daily_state(daily_seen)
    save_seen_registrations(seen_registrations)


if __name__ == "__main__":
    main()

