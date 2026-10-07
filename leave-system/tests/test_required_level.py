# -*- coding: utf-8 -*-
"""required_level_for 單元測試。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import required_level_for, FACULTY_THRESHOLD_DAYS


def test_short_leave():
    assert required_level_for(3.0, needs_faculty=False) == 1
    assert required_level_for(2.5, needs_faculty=False) == 1
    assert required_level_for(0.5, needs_faculty=False) == 1


def test_long_leave():
    assert required_level_for(FACULTY_THRESHOLD_DAYS + 0.5, needs_faculty=False) == 2
    assert required_level_for(6.0, needs_faculty=False) == 2


def test_official_always_faculty():
    assert required_level_for(0.5, needs_faculty=True) == 2
    assert required_level_for(1.0, needs_faculty=True) == 2


def test_leave_type_dict():
    assert required_level_for(1.0, leave_type={'needs_faculty': 0}) == 1
    assert required_level_for(1.0, leave_type={'needs_faculty': 1}) == 2
    assert required_level_for(5.0, leave_type={'needs_faculty': 0}) == 2
