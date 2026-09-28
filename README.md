# Student Absence & Academic Accommodation System (SAAS)

One problem, three artefacts: a **runnable prototype**, an **enterprise design package**, and the
**Individual Ideation assignment** that motivated them.

一個問題，三份產出：可執行的原型、企業級設計提案包，以及促成這一切的個人創意作業。

| Folder | What it is | 說明 |
|---|---|---|
| `leave-system/` | Flask + SQLite prototype — student leave, two-level approval (advisor → faculty), local accounts. Runs at `http://127.0.0.1:5055` | 可執行原型：學生請假、兩級審批、本地帳號 |
| `saas/` | Enterprise design package for HKUST(GZ): PRD, architecture & API, data & security, UI design, roadmap & cost, plus full PostgreSQL DDL | 企業級設計提案包（6 頁中英對照）＋完整資料庫 DDL |
| `assignment/` | Individual Ideation assignment (≈1,030 words) + 10-slide deck + 3 figures | 個人創意作業：Word 正文、簡報、流程圖 |

## Quick start ｜快速開始

```bash
# 原型系統
cd leave-system
python app.py            # http://127.0.0.1:5055
python smoke_test.py     # 10 項全鏈路冒煙測試

# 設計提案包（靜態站）
cd saas
python -m http.server 5066 --bind 127.0.0.1
```

Demo accounts (password `Pass@123`): student `2024001`, advisor `T001`, faculty `F001`, admin `admin`.

## Absence workflow ｜請假流程

- ≤ 3 days → instructor approval only
- \> 3 days, or an official-activity absence → advisor → faculty review
- Official activity roster → auto-verified, instructors notified only
- Absence > 7 days → programme office → registry → instructors

## Author

Johnny, Jeng-lin Li · HKUST(GZ)
