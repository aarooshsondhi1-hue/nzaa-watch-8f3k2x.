# NZAA Special Plane Watcher

Sends a push notification to your phone when a genuinely unexpected,
military, cargo, or specifically-flagged special-livery aircraft is
heading to or lands at Auckland Airport (NZAA) - while staying quiet
about ordinary, already-known traffic. Runs for free on GitHub Actions -
no APK, no server of your own required.

## The three files that control what counts as "special"

- **`always_notify.json`** - registrations that notify EVERY time, no
  exceptions. For specific special-livery aircraft (Air NZ's black
  liveries, Cathay's 80th Anniversary A350, Emirates/Qatar specials,
  etc.) you want to know about on every single visit, not just once.
- **`known_specials.json`** - registrations to fully SUPPRESS, never
  notify. For anything you've decided you're bored of. Currently empty.
- **`common_types.txt`** - ICAO type codes treated as routine NZAA
  traffic (domestic Air NZ fleet, common long-haul widebodies). Anything
  NOT in this list counts as an "uncommon type" and triggers a
  notification every time it shows up - this is how military transports,
  cargo freighters, and other rare types get caught automatically, since
  their type codes (C17, B744, P8, etc.) are never on this list.

## How a notification decision is made (in priority order)

1. **On `always_notify.json`?** → always notify, every time.
2. **NZ-registered (`ZK-...`)?** → fully suppressed, no exceptions
   (unless it was already caught by rule 1 above - your 4 Air NZ special
   liveries are ZK- registered but still notify because they're on the
   always-notify list, checked first).
3. **On `known_specials.json`?** → suppressed.
4. **Never seen this registration before?** → notify once ("first time
   seeing this registration"), then goes quiet on future visits.
5. **Uncommon aircraft type** (not in `common_types.txt`)? → notify,
   every time this happens - this check works even without a known
   registration, so military aircraft that don't broadcast a tail
   number still get caught based on type alone.
6. Otherwise → silent.

An aircraft with a blank registration (common for military aircraft,
which often withhold it deliberately) still gets judged on type alone
via rule 5 - it just displays as "(not broadcast - possibly military)"
instead of a tail number in the notification.

## Three ways you get notified

| | Confirmed arrival | Early-warning departure | Possible/unconfirmed inbound |
|---|---|---|---|
| Fires when | Aircraft is on the ground at NZAA | Flight has left its origin airport, still flying | Aircraft's live heading + speed suggest it's ~≤40 min from NZAA, even with no published destination |
| Lead time | ~Zero - it's already there | Roughly the flight's full duration | Up to ~40 minutes |
| Script | `nzaa_watcher.py` | `check_departures.py` | `nzaa_watcher.py` (same script, second detection) |
| Data source | airplanes.live (free, no key) | AirLabs (free, 1,000 req/month) | airplanes.live |
| Check frequency | Every 5 minutes | Every hour | Every 5 minutes |

The **possible/unconfirmed** detection exists specifically for military
and cargo flights, which frequently show "N/A" for destination on
trackers like Flightradar24 since they don't file a public schedule.
Instead of relying on a destination field that doesn't exist for these,
it calculates: is this aircraft's current track pointed at NZAA (within
15°), and is it close enough to arrive within 40 minutes at its current
speed? If yes, and it passes the same notification rules above, you get
a clearly-labeled "POSSIBLE/unconfirmed" alert with distance and ETA.

**This is a geometry-based guess, not a real destination field** -
a flight merely transiting the area, or actually headed to a nearby
airport (Ardmore, Whenuapai, Hamilton) instead of NZAA, can occasionally
match by coincidence. That's why it's always labeled "possible" - treat
it as an early heads-up, with the separate CONFIRMED notification (or
its absence, if it turns out to be headed elsewhere) as the real answer.

Known residual risk: airplanes.live has, in testing, occasionally served
a stale or mismatched position for an aircraft (one case showed a
private jet as 89nm from NZAA while it was actually in Europe). A
freshness check (`seen_pos` under 60 seconds) filters out most of this,
but can't guarantee catching every bad record from a free data source.

## Why not a real Android app / APK?

A custom APK would need to be built, signed, and you'd have to sideload
it (Android blocks unknown-source installs by default) or publish it to
the Play Store, which requires a developer account. This setup gets you
the same result - a phone notification - using two free pieces: **ntfy**
(push notifications) and **GitHub Actions** (the scheduled "server").

## Setup

### 1. Get notifications on your phone (ntfy)
1. Install the **ntfy** app (Google Play or F-Droid, `io.heckel.ntfy`).
2. Subscribe to a topic - pick a random, hard-to-guess name (anyone who
   knows the topic name can see/send to it).

### 2. Get a free AirLabs API key (for the departure/early-warning script)
1. Sign up at airlabs.co (may involve a waitlist - approval isn't always
   instant).
2. Copy your API key from the dashboard.
3. Free plan = 1,000 requests/month, which is why departures check
   hourly rather than continuously.

### 3. Add GitHub secrets
Repo → **Settings → Secrets and variables → Actions → New repository
secret**:
- `NTFY_TOPIC` - your ntfy topic name
- `AIRLABS_KEY` - your AirLabs API key

### 4. Turn it on
Both workflows run automatically on their schedules once the files are
in your repo. Trigger a one-off test any time: repo → **Actions** tab →
pick the workflow → **Run workflow**.

## Customizing

- **`common_types.txt`** - add/remove ICAO type codes as you decide
  what's routine. One code per line, `#` for comments.
- **`always_notify.json`** - add registrations you always want alerted
  on. Format: `"REG": "description"`.
- **`known_specials.json`** - add registrations to fully silence.
- **`seen_registrations.json`** - auto-generated, grows forever, tracks
  every registration ever seen (powers the "first time" check). Don't
  edit by hand.
- **`seen.json`** / **`seen_departures.json`** / **`possible_seen.json`**
  - auto-generated, reset daily, prevent same-day repeat notifications
  for confirmed/departure/possible detections respectively.

## Known limitations

- **Confirmed arrival** (`nzaa_watcher.py`): relies on airplanes.live's
  crowdsourced ADS-B coverage near Auckland, which depends on volunteer
  ground receivers.
- **Early-warning departures** (`check_departures.py`): AirLabs'
  live-tracking endpoint doesn't always include a scheduled arrival time
  or operator name for every flight - when missing, the script estimates
  ETA itself from the aircraft's live position and speed (labeled
  "estimated," not exact) rather than showing nothing.
- **Possible/unconfirmed inbound**: inherently a heuristic based on
  heading + speed, not a real destination field - expect occasional
  false positives from transiting traffic or aircraft headed to nearby
  airports instead of NZAA.
- Military aircraft with no broadcast registration will notify again on
  a future visit rather than going quiet after the first time - there's
  nothing stable to track them by across days without a tail number.

## Files
- `nzaa_watcher.py` - confirmed arrival + possible/unconfirmed inbound detection
- `check_departures.py` - early-warning departure detection
- `nzaa_common.py` - shared notification decision logic
- `common_types.txt` / `always_notify.json` / `known_specials.json` - your 3 config lists
- `seen_registrations.json` - auto-generated, permanent, powers "first time seeing this" checks
- `seen.json` / `seen_departures.json` / `possible_seen.json` - auto-generated, reset daily
- `.github/workflows/watch.yml` - arrival watcher schedule (every 5 min)
- `.github/workflows/watch-departures.yml` - departure watcher schedule (hourly)
- `.gitignore` - keeps Python's `__pycache__` clutter out of the repo
