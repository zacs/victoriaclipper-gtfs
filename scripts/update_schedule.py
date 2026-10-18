#!/usr/bin/env python3
"""Regenerate the schedule files in src/ from the Clipper's published sailings.

clippervacations.com lists every sailing in its sailings calendar (The Events
Calendar WordPress plugin), which is also served as JSON. This script reads
that calendar and rewrites calendar.txt, calendar_dates.txt, trips.txt,
stop_times.txt and feed_info.txt. agency.txt, routes.txt, stops.txt and
shapes.txt are maintained by hand.

    python3 scripts/update_schedule.py [--start YYYY-MM-DD] [--events FILE]

Requires Python 3.9+ with up-to-date tz data (B.C. stays on UTC-7 all year
starting November 2026).
"""

import argparse
import csv
import datetime as dt
import html
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from zoneinfo import ZoneInfo

API = 'https://www.clippervacations.com/wp-json/tribe/events/v1/events'
# The site rejects requests without a browser user agent.
USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/130.0 Safari/537.36')

AGENCY_TZ = ZoneInfo('America/Los_Angeles')
SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src')

# Sailing titles in the calendar. Each event's start time is local to the
# origin terminal and its end time is local to the destination terminal.
DIRECTIONS = {
    'Seattle – Victoria': {
        'code': 'sea', 'origin': 'sea', 'dest': 'vic', 'headsign': 'Victoria',
        'short_name': 'Vic', 'direction_id': '0', 'shape_id': '4',
        'origin_tz': ZoneInfo('America/Los_Angeles'), 'dest_tz': ZoneInfo('America/Vancouver'),
    },
    'Victoria – Seattle': {
        'code': 'vic', 'origin': 'vic', 'dest': 'sea', 'headsign': 'Seattle',
        'short_name': 'Sea', 'direction_id': '1', 'shape_id': '5',
        'origin_tz': ZoneInfo('America/Vancouver'), 'dest_tz': ZoneInfo('America/Los_Angeles'),
    },
}
ROUTE_ID = '1'

# A crossing this far from the usual time is treated as a typo in the
# calendar's arrival time and replaced with the usual crossing time.
MAX_CROSSING_DEVIATION = dt.timedelta(minutes=30)

# Weighting for compressing service dates: each calendar.txt row also costs
# a trips.txt row and two stop_times.txt rows, while an exception costs one
# calendar_dates.txt row.
SERVICE_COST = 4


def fetch_events():
    params = {'per_page': 50, 'start_date': '2000-01-01 00:00:00', 'end_date': '2100-01-01 00:00:00'}
    url = API + '?' + urllib.parse.urlencode(params)
    events = []
    while url:
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            page = json.load(resp)
        events.extend(page['events'])
        url = page.get('next_rest_url')
    return events


def localize(value, tz):
    return dt.datetime.fromisoformat(value).replace(tzinfo=tz)


def gtfs_time(moment, service_date):
    """Format a moment as a GTFS time relative to noon minus 12h on service_date."""
    noon = dt.datetime.combine(service_date, dt.time(12), tzinfo=AGENCY_TZ)
    seconds = int((moment - noon).total_seconds()) + 12 * 3600
    return '%02d:%02d:%02d' % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def read_sailings(events, start):
    """Return a list of (direction title, service date, departure, arrival)."""
    sailings = []
    for event in events:
        title = html.unescape(event['title']).strip()
        if title not in DIRECTIONS or event.get('status', 'publish') != 'publish':
            continue
        info = DIRECTIONS[title]
        depart = localize(event['start_date'], info['origin_tz'])
        arrive = localize(event['end_date'], info['dest_tz'])
        service_date = depart.astimezone(AGENCY_TZ).date()
        if service_date >= start:
            sailings.append((title, service_date, depart, arrive, event.get('url', '')))

    # Fix arrival times that don't match the usual crossing time.
    usual = {}
    for title in DIRECTIONS:
        crossings = [a - d for t, _, d, a, _ in sailings if t == title]
        if crossings:
            usual[title] = statistics.median(crossings)
    fixed = []
    for title, service_date, depart, arrive, url in sailings:
        if abs((arrive - depart) - usual[title]) > MAX_CROSSING_DEVIATION:
            corrected = depart + usual[title]
            print('warning: %s %s listed as a %s crossing; using %s arrival (%s)' % (
                title, depart.strftime('%Y-%m-%d %H:%M'), arrive - depart,
                corrected.astimezone(DIRECTIONS[title]['dest_tz']).strftime('%H:%M'), url),
                file=sys.stderr)
            arrive = corrected
        fixed.append((title, service_date, depart, arrive))
    return fixed


def compress_dates(dates):
    """Split a set of dates into calendar.txt-style periods plus exceptions.

    Returns a list of (start, end, weekday flags, added dates, removed dates),
    choosing the split (at week boundaries) with the fewest total rows.
    """
    dates = sorted(dates)
    date_set = set(dates)
    first_monday = dates[0] - dt.timedelta(days=dates[0].weekday())
    weeks = (dates[-1] - first_monday).days // 7 + 1
    week_dates = [[] for _ in range(weeks)]
    for d in dates:
        week_dates[(d - first_monday).days // 7].append(d)

    def period(lo, hi):
        """Build one period spanning weeks lo..hi-1, or None if it has no dates."""
        inside = [d for w in range(lo, hi) for d in week_dates[w]]
        if not inside:
            return None
        start, end = inside[0], inside[-1]
        present, total = [0] * 7, [0] * 7
        day = start
        while day <= end:
            total[day.weekday()] += 1
            present[day.weekday()] += day in date_set
            day += dt.timedelta(days=1)
        flags = [1 if present[i] * 2 > total[i] else 0 for i in range(7)]
        if not any(flags):
            return None
        added, removed = [], []
        day = start
        while day <= end:
            if flags[day.weekday()] and day not in date_set:
                removed.append(day)
            elif not flags[day.weekday()] and day in date_set:
                added.append(day)
            day += dt.timedelta(days=1)
        return (start, end, flags, added, removed)

    best = [(0, [])] + [None] * weeks
    for hi in range(1, weeks + 1):
        if not week_dates[hi - 1]:
            best[hi] = best[hi - 1]
        for lo in range(hi):
            p = period(lo, hi)
            if p is None:
                continue
            cost = best[lo][0] + SERVICE_COST + len(p[3]) + len(p[4])
            if best[hi] is None or cost < best[hi][0]:
                best[hi] = (cost, best[lo][1] + [p])
    return best[weeks][1]


def write_csv(name, header, rows):
    with open(os.path.join(SRC_DIR, name), 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--start', type=dt.date.fromisoformat,
                        default=dt.datetime.now(AGENCY_TZ).date(),
                        help='first service date to include (default: today)')
    parser.add_argument('--events', help='read sailings JSON from this file instead of the website')
    args = parser.parse_args()

    winter = dt.datetime(2026, 12, 1, 12, tzinfo=ZoneInfo('America/Vancouver'))
    if winter.utcoffset() != dt.timedelta(hours=-7):
        sys.exit('tz data is out of date (B.C. is on UTC-7 all year from November 2026); '
                 'update it, e.g. pip install -U tzdata')

    if args.events:
        with open(args.events, encoding='utf-8') as f:
            events = json.load(f)
    else:
        events = fetch_events()
    sailings = read_sailings(events, args.start)
    if not sailings:
        sys.exit('no sailings found on or after %s' % args.start)

    # One trip pattern per direction and pair of GTFS times.
    patterns = defaultdict(set)
    for title, service_date, depart, arrive in sailings:
        key = (title, gtfs_time(depart, service_date), gtfs_time(arrive, service_date))
        patterns[key].add(service_date)

    calendar, calendar_dates, trips, stop_times = [], [], [], []
    for (title, depart, arrive), dates in sorted(patterns.items(), key=lambda p: (DIRECTIONS[p[0][0]]['direction_id'], p[0][1])):
        info = DIRECTIONS[title]
        for n, (start, end, flags, added, removed) in enumerate(compress_dates(dates), 1):
            service_id = '%s%s_%d' % (info['code'], depart[:5].replace(':', ''), n)
            calendar.append([service_id] + flags + [start.strftime('%Y%m%d'), end.strftime('%Y%m%d')])
            calendar_dates.extend([service_id, d.strftime('%Y%m%d'), 1] for d in added)
            calendar_dates.extend([service_id, d.strftime('%Y%m%d'), 2] for d in removed)
            trips.append([ROUTE_ID, service_id, service_id, info['headsign'], info['short_name'],
                          info['direction_id'], '', info['shape_id'], 1, 1])
            stop_times.append([service_id, depart, depart, info['origin'], 1])
            stop_times.append([service_id, arrive, arrive, info['dest'], 2])

    calendar_dates.sort(key=lambda r: (r[1], r[0]))
    all_dates = [d for _, d, _, _ in sailings]

    write_csv('calendar.txt', ['service_id', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday',
                               'saturday', 'sunday', 'start_date', 'end_date'], calendar)
    write_csv('calendar_dates.txt', ['service_id', 'date', 'exception_type'], calendar_dates)
    write_csv('trips.txt', ['route_id', 'service_id', 'trip_id', 'trip_headsign', 'trip_short_name',
                            'direction_id', 'block_id', 'shape_id', 'wheelchair_accessible',
                            'bikes_allowed'], trips)
    write_csv('stop_times.txt', ['trip_id', 'arrival_time', 'departure_time', 'stop_id', 'stop_sequence'],
              stop_times)
    write_csv('feed_info.txt', ['feed_publisher_name', 'feed_publisher_url', 'feed_lang', 'feed_start_date',
                                'feed_end_date', 'feed_version'],
              [['zacs@github', 'https://github.com/zacs/victoriaclipper-gtfs', 'en',
                min(all_dates).strftime('%Y%m%d'), max(all_dates).strftime('%Y%m%d'),
                args.start.strftime('%Y%m%d')]])

    print('%d sailings from %s to %s: %d services, %d calendar exceptions' % (
        len(sailings), min(all_dates), max(all_dates), len(calendar), len(calendar_dates)))


if __name__ == '__main__':
    main()
