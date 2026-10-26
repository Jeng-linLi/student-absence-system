# Student Absence & Academic Accommodation System (SAAS)

![CI](https://github.com/Jeng-linLi/student-absence-system/actions/workflows/ci.yml/badge.svg)

One problem, three artefacts: a **runnable prototype**, an **enterprise design package**, and the
**Individual Ideation assignment** that motivated them.

一個問題，三份產出：可執行的原型、企業級設計提案包，以及促成這一切的個人創意作業。

**Prototype version ｜原型版本：`v1.2.0`**（2026-10-26）· MIT · CI passing

v0.6.0 links the leave flow to the student timetable: picking start/end dates and AM/PM
periods instantly previews the classes a leave would make a student miss (`GET
/api/affected_courses`), and the affected courses are saved onto each request for approvers
to see. v0.6.1 trims that preview and detail list to course code, type and time slot —
the room is no longer shown. v0.7.0 consolidates the module split (single source of truth
for constants and pure functions), fixes a stats-scope leak, hardens the CSV export against
formula injection, completes the password-strength policy, and adds a `/healthz` probe.
v0.8.0 hardens authentication and account administration: login throttling now persists in
SQLite (survives restarts and multi-worker deployments), password resets invalidate every
existing session, idle sessions expire, and admins can no longer lock themselves out of the
back office. v0.9.0 completes the password and notification experience: a common-weak-
password blocklist (the old demo default `Pass@123` is now blocked — demo password moved to
`Demo@2026`), admin-set passwords shown once instead of being written into the session
cookie, paginated notifications with an unread filter, magic-byte sniffing for uploads, a
per-username login-throttle key, and `Cache-Control: no-store` on dynamic pages. v0.9.1
finishes the scope-isolation line the v0.7.0 audit started — advisors and faculty can no
longer browse the whole system's leave reasons (`/approvals?scope=all`) or open other
people's request detail pages — and cleans up leave-type administration (duplicate codes
no longer 500, deactivation is a real toggle, AM/PM periods are whitelisted). **v1.0.0**
closes the last two scope-isolation gaps found in re-audit (students could open
`/approvals` and read every pending request system-wide; advisor/faculty dashboards showed
system-wide aggregates), adds an admin audit-log page with action filters, notifies the
advisor when a student withdraws a request, and cleans up edge cases (friendly 413 for
oversized uploads, long attachment filenames, same-day PM→AM period conflicts, a password
length cap). **v1.1.0** turns per-request defences into real ones: atomic multi-table
writes via a transaction context, optimistic concurrency guards so a double-click cannot
overwrite another approver's decision, paginated todo list, single-notification
mark-as-read, a `Retry-After` header on login lockout, SMTP connection timeouts, referrer
validation on error redirects (open-redirect fix), and a department-required rule for
student/faculty accounts (empty-department students used to be invisible to every faculty
approver). **v1.2.0** moves the last synchronous I/O out of the transaction: notification
emails used to be sent inside the write transaction, so a slow SMTP server held the
SQLite write lock for the whole connection — emails are now queued and sent only after
the outermost transaction commits (rolled back with it otherwise). It also closes the
remaining scale gaps: a status filter on `/approvals?scope=all`, pagination for the
account admin page, and streamed CSV export (memory use no longer scales with data
volume), plus a pre-save check that keeps rejected attachment uploads from leaving
orphaned files on disk. It builds on v0.5.0's clickjacking protection, v0.4.0's
pagination / unit tests, and a 183-check smoke suite in CI.

| Folder | What it is | 說明 |
|---|---|---|
| `leave-system/` | Flask + SQLite prototype (**v1.2.0**) — student leave, two-level approval (advisor → faculty), CSRF protection, attachment access control + content sniffing, timetable-based affected-course preview, DB-persisted login throttle (IP+account and account-only keys), session invalidation on password change, common-weak-password blocklist, paginated notifications with per-item read, atomic approval transactions with concurrency guards, scope isolation on approvals/detail/dashboard, admin audit-log page, leave-type administration, transaction-safe deferred email delivery, status-filtered all-requests view, paginated account admin, streamed CSV export, 183-check smoke suite. Runs at `http://127.0.0.1:5055` | 可執行原型：學生請假、兩級審批、CSRF 防護、附件權限＋內容嗅探、課表受影響課程、限流持久化、改密碼登出所有 session、弱密碼擋鎖、通知分頁＋單則已讀、審批交易原子化與併發守衛、範圍隔離（審批／詳情／儀表板）、後台稽核日誌、交易提交後才寄通知郵件、全部假單狀態篩選、帳號管理分頁、CSV 串流匯出 |
| `saas/` | Enterprise design package for HKUST(GZ): PRD, architecture & API, data & security, UI design, roadmap & cost, plus full PostgreSQL DDL | 企業級設計提案包（6 頁中英對照）＋完整資料庫 DDL |
| `assignment/` | Individual Ideation assignment (≈1,030 words) + 10-slide deck + 3 figures | 個人創意作業：Word 正文、簡報、流程圖 |

## Quick start ｜快速開始

```bash
# 原型系統
cd leave-system
python app.py            # http://127.0.0.1:5055
python smoke_test.py     # 183 項全鏈路冒煙測試

# 設計提案包（靜態站）
cd saas
python -m http.server 5066 --bind 127.0.0.1
```

Demo accounts (password `Demo@2026`): student `2024001`, advisor `T001`, faculty `F001`, admin `admin`.

> Before deploying: set `LEAVE_SECRET` (see [`leave-system/README.md`](leave-system/README.md#環境變數)).

## Changelog ｜更新日誌

See [`CHANGELOG.md`](CHANGELOG.md) — **v1.2.0** moves the last synchronous I/O out of
the write transaction: notification emails used to be sent inside `transaction()`, so a
slow or unreachable SMTP server held the SQLite write lock for the entire connection and
blocked every concurrent approval/submission. Emails are now queued and sent only after
the outermost transaction commits (and discarded with it on rollback). It also closes the
remaining scale gaps — a status filter on `/approvals?scope=all`, pagination for the
account admin page, and a streamed CSV export whose memory use no longer scales with
data volume — and keeps rejected attachment uploads from leaving orphaned files on
disk; v1.1.0 makes the per-request defences real:
atomic multi-table writes (submit/approve/withdraw), optimistic concurrency guards
(a double-click can no longer overwrite another approver's decision or double-notify the
student), a paginated todo list, single-notification mark-as-read, `Retry-After` on login
lockout, SMTP connection timeouts, referrer validation on error redirects (open-redirect
fix), a phone-format check, an env-gated debug mode, and a department-required rule for
student/faculty accounts (empty-department students used to fall into an approval
dead-end); v1.0.0 closes the last two scope-isolation gaps
found in re-audit (students could open `/approvals` and read every pending request
system-wide; advisor/faculty dashboards showed system-wide aggregates), adds an admin
audit-log page with action filters, notifies the advisor when a student withdraws a
request, and cleans up edge cases (friendly 413 for oversized uploads, long attachment
filenames, same-day PM→AM period conflicts, a password length cap); v0.9.1 completes scope
isolation (advisors and faculty can no longer browse every leave reason via
`/approvals?scope=all` or open other people's request detail pages) and cleans up
leave-type administration (duplicate codes no longer 500, deactivation is a real toggle,
AM/PM periods whitelisted); v0.9.0 completes the password and notification experience
(common-weak-password blocklist, one-time password display outside the session cookie,
paginated notifications, upload content sniffing, per-username login throttle, no-store
cache headers); v0.8.0 hardens authentication and account administration (persistent login
throttling, session invalidation on password change, idle-session expiry, admin lockout
guards); v0.7.0 consolidates the module split, fixes a stats-scope leak, hardens CSV export
against formula injection, completes the password policy, and adds a `/healthz` probe
(smoke suite now 165 checks).

完整變更記錄見 [`CHANGELOG.md`](CHANGELOG.md)。

## Absence workflow ｜請假流程

- ≤ 3 days → instructor approval only
- \> 3 days, or an official-activity absence → advisor → faculty review
- Official activity roster → auto-verified, instructors notified only
- Absence > 7 days → programme office → registry → instructors

## Author

Johnny, Jeng-lin Li · HKUST(GZ)

## License

Code and documentation are released under the **MIT License** — see [`LICENSE`](LICENSE).
You may use, modify and redistribute freely, including commercially, provided the copyright
notice is retained.

程式碼與文件皆採 **MIT License**，可自由使用、修改、再散布（含商業用途），只需保留著作權聲明。

> **On `assignment/`:** the Individual Ideation essay is published as a worked example for
> reference and citation. **It must not be submitted as another student's own assessment.**
> If you cite it, please attribute: Li, J. (2026). *SAAS: A Student Absence and Academic
> Accommodation System* (Individual Ideation assignment), HKUST(GZ).
>
> `assignment/` 內的作業全文僅供參考與引用，**不得作為他人作業提交**。引用請註明出處。
