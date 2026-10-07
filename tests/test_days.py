# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

import datetime
import unittest

from tests import ROOT  # noqa: F401
from retain import days


class DayNumberTest(unittest.TestCase):

    def test_a_late_night_belongs_to_the_day_before(self):
        before = datetime.datetime(2026, 3, 10, 3, 59).timestamp()
        after = datetime.datetime(2026, 3, 10, 4, 0).timestamp()
        self.assertEqual(days.day_number(before), days.day_number(after) - 1)
        self.assertEqual(days.date_of(days.day_number(after)), datetime.date(2026, 3, 10))

    def test_day_start_round_trips(self):
        number = days.day_number(datetime.datetime(2026, 7, 1, 12).timestamp())
        self.assertEqual(days.day_number(days.day_start(number)), number)
        self.assertEqual(days.day_number(days.day_start(number) - 1), number - 1)

    def test_the_hour_is_a_setting(self):
        moment = datetime.datetime(2026, 3, 10, 1, 0).timestamp()
        self.assertEqual(days.day_number(moment, 0), days.day_number(moment, 4) + 1)


if __name__ == '__main__':
    unittest.main()
