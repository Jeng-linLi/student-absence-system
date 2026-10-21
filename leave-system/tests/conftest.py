# -*- coding: utf-8 -*-
"""pytest 共用設定：為整個測試階段準備獨立的臨時資料庫，再匯入 app。

app.py 在 import 時就會從環境變數讀取資料庫路徑，因此必須在測試模組
import app 之前設定好 LEAVE_DB —— conftest.py 永遠比測試模組更早被匯入。
同時在此完成 init_db + seed，讓各測試共用一份乾淨的示範資料。
"""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix='leave-pytest-')
os.environ['LEAVE_DB'] = os.path.join(_TMP, 'test.db')
os.environ['LEAVE_UPLOAD_DIR'] = os.path.join(_TMP, 'uploads')
os.environ['LEAVE_SECRET'] = 'pytest-secret'

# 讓 `from app import ...` 找得到 leave-system/ 目錄
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from app import app, init_db, seed  # noqa: E402


@pytest.fixture(scope='session', autouse=True)
def _seeded_db():
    """建庫 + 寫入示範資料；關閉 CSRF（CSRF 由 smoke_test 專門覆蓋）。"""
    with app.app_context():
        init_db()
        seed()
    app.config['CSRF_ENABLED'] = False
    app.config['TESTING'] = True
    return app
