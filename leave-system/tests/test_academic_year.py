# -*- coding: utf-8 -*-
"""academic_year_start / allowed_file 單元測試。"""
from datetime import date
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import academic_year_start, allowed_file


def test_after_september():
    assert academic_year_start(date(2026, 10, 7)) == date(2026, 9, 1)


def test_before_september():
    assert academic_year_start(date(2026, 3, 15)) == date(2025, 9, 1)


def test_on_september_first():
    assert academic_year_start(date(2026, 9, 1)) == date(2026, 9, 1)


def test_allowed_file():
    assert allowed_file('proof.pdf') is True
    assert allowed_file('a.PNG') is True
    assert allowed_file('note.docx') is True
    assert allowed_file('evil.exe') is False
    assert allowed_file('noext') is False
