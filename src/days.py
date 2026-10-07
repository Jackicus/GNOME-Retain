# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Days, as the scheduler counts them.

A day number is the proleptic Gregorian ordinal (`date.toordinal()`) of the local date a
moment falls in, the day starting at `day_start_hour` (4 in the morning by default, as Anki:
a session that runs past midnight stays in the day it began). Review cards are due on a day
number; `today()` is the current one.

    day_number(timestamp, day_start_hour=4) -> int
    today(day_start_hour=4, now=None) -> int
    day_start(day_number, day_start_hour=4) -> float   the Unix time the day begins
    date_of(day_number) -> datetime.date
"""

import datetime
import time

DEFAULT_DAY_START_HOUR = 4
SECONDS_PER_DAY = 86400


def day_number(timestamp, day_start_hour=DEFAULT_DAY_START_HOUR):
    """The day number of the local moment `timestamp` (Unix seconds)."""
    moment = datetime.datetime.fromtimestamp(timestamp)
    return (moment - datetime.timedelta(hours=day_start_hour)).toordinal()


def today(day_start_hour=DEFAULT_DAY_START_HOUR, now=None):
    """Today's day number (`now` is Unix seconds, the clock's by default)."""
    return day_number(time.time() if now is None else now, day_start_hour)


def day_start(number, day_start_hour=DEFAULT_DAY_START_HOUR):
    """The Unix time at which day `number` begins (local time)."""
    date = datetime.date.fromordinal(number)
    moment = datetime.datetime.combine(date, datetime.time(hour=day_start_hour))
    return moment.timestamp()


def date_of(number):
    """The calendar date day `number` is counted as."""
    return datetime.date.fromordinal(number)
