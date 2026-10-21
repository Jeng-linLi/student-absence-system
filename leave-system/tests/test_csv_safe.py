# -*- coding: utf-8 -*-
"""csv_safe CSV 公式注入防護單元測試（v0.7.0）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import csv_safe


def test_formula_prefixes_escaped():
    assert csv_safe('=1+1') == "'=1+1"
    assert csv_safe('+cmd') == "'+cmd"
    assert csv_safe('-5') == "'-5"
    assert csv_safe('@SUM(A1)') == "'@SUM(A1)"


def test_safe_values_untouched():
    assert csv_safe('正常事由') == '正常事由'
    assert csv_safe('2026-10-21') == '2026-10-21'
    assert csv_safe('事假 0.5 天') == '事假 0.5 天'
    assert csv_safe('') == ''


def test_non_string_passthrough():
    assert csv_safe(3.5) == 3.5
    assert csv_safe(42) == 42
    assert csv_safe(None) is None
    assert csv_safe(True) is True
