# -*- coding: utf-8 -*-
"""validate_password 密碼強度策略單元測試（v0.7.0 建立，v0.9.0 補弱密碼擋鎖）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import validate_password, temp_password, MIN_PASSWORD_LEN


def test_too_short():
    assert validate_password('Ab1') is not None
    assert validate_password('Ab1cdef') is not None          # 7 碼，差一位
    assert validate_password('a' * MIN_PASSWORD_LEN) is not None  # 剛好 8 碼但無大寫數字


def test_meets_policy():
    assert validate_password('Demo@2026') is None
    assert validate_password('NewPass@2026') is None


def test_empty():
    assert validate_password('') is not None
    assert validate_password(None) is not None


def test_missing_character_classes():
    assert validate_password('onlyletters') is not None    # 無數字、無大寫
    assert validate_password('NOLOWER123') is not None     # 無小寫
    assert validate_password('nouppercase1') is not None   # 無大寫
    assert validate_password('NoDigitsHere') is not None   # 無數字


def test_not_equal_to_username():
    assert validate_password('2024001', '2024001') is not None
    assert validate_password('admin', 'admin') is not None
    # 大小寫視為相同
    assert validate_password('ADMIN', 'admin') is not None
    # 不等於帳號即可通過
    assert validate_password('Demo@2026', '2024001') is None


def test_username_optional():
    # 不提供 username 時跳過「與帳號相同」檢查
    assert validate_password('StrongPass1') is None


def test_common_passwords_blocked():
    """v0.9.0：通過組成策略（長度＋大小寫＋數字）的常見弱密碼仍須擋下。"""
    for pw in ('Pass@123', 'Password1', 'Qwerty123', 'Admin1234',
               'Welcome123', 'Letmein123', 'abc12345'):
        err = validate_password(pw)
        assert err is not None, pw
        assert '常見' in err, pw          # 擋鎖訊息，而非組成策略訊息
    # 明顯弱密碼同樣拒絕（先過擋鎖這一關）
    for pw in ('password', '12345678', 'qwertyuiop'):
        assert validate_password(pw) is not None


def test_common_password_case_insensitive():
    assert validate_password('PASS@123') is not None
    assert validate_password('password123') is not None


def test_temp_password_always_valid():
    for _ in range(50):
        assert validate_password(temp_password()) is None
        assert validate_password(temp_password(), 'anyone') is None
