# -*- coding: utf-8 -*-
"""calc_days 單元測試。"""
from datetime import date
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import calc_days


def test_same_day_full():
    d = date(2026, 10, 7)
    assert calc_days(d, d, 'AM', 'PM') == 1.0


def test_same_day_half_am():
    d = date(2026, 10, 7)
    assert calc_days(d, d, 'AM', 'AM') == 0.5


def test_same_day_half_pm():
    d = date(2026, 10, 7)
    assert calc_days(d, d, 'PM', 'PM') == 0.5


def test_two_days_full():
    assert calc_days(date(2026, 10, 7), date(2026, 10, 8), 'AM', 'PM') == 2.0


def test_two_days_half():
    assert calc_days(date(2026, 10, 7), date(2026, 10, 8), 'PM', 'AM') == 1.0


def test_three_days():
    assert calc_days(date(2026, 10, 7), date(2026, 10, 9), 'AM', 'PM') == 3.0


def test_span_with_half_ends():
    # 10/7 PM ~ 10/9 AM = 0.5 + 1 + 0.5 = 2.0
    assert calc_days(date(2026, 10, 7), date(2026, 10, 9), 'PM', 'AM') == 2.0


def test_end_before_start():
    assert calc_days(date(2026, 10, 8), date(2026, 10, 7), 'AM', 'PM') == 0
