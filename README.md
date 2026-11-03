# Student Absence & Academic Accommodation System (SAAS)

![CI](https://github.com/Jeng-linLi/student-absence-system/actions/workflows/ci.yml/badge.svg)

One problem, three artefacts: a **runnable prototype**, an **enterprise design package**, and the
**Individual Ideation assignment** that motivated them.

一個問題，三份產出：可執行的原型、企業級設計提案包，以及促成這一切的個人創意作業。

**Prototype version ｜原型版本：`v1.11.0`**（2026-11-03）· Commercial License · CI passing
· 商業化方案見 [`COMMERCIAL.md`](COMMERCIAL.md)

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
pagination / unit tests, and a 183-check smoke suite in CI. **v1.4.0** closes five
gaps from the deployment gap analysis: approval **delegation** (an advisor or faculty
member can hand their level to a stand-in for a date range — the delegate's todo list,
notifications and attachment access all follow), an admin **advisor-reassign** action,
**academic terms** with per-term, per-leave-type **quotas** (pending + approved both
count, so a stack of pending requests cannot bypass the cap), a **holiday calendar**
(non-teaching days no longer appear as affected courses), and an **SLA escalation
cron** (`/admin/cron/escalate`, token-authenticated) that reminds the current
approver, escalates to the next level, and writes an audit record with a 24-hour
cooldown. **v1.5.0** imports the official HKUST(GZ) 2026–27 academic calendar:
a plain-text calendar-file format (terms / holiday ranges / makeup days), a
`makeup_days` table so weekend make-up classes taught on a Friday schedule
still count as missed classes, built-in calendar presets auto-matched to
today, an admin import page and a `flask import-calendar` CLI for re-running
after government holiday announcements. 128 unit tests + 183 smoke checks.
**v1.5.1** turns the commercial side into a document: a licensing-tier model
(evaluation / institutional site license / managed service), the compliance-driven
buyer case (UKVI sponsor duties, ESOS 80% attendance, FERPA expectations), a
readiness gate listing the four gaps that must close before any production contract
(SSO, PostgreSQL, SIS timetable sync, personal-data governance), and a
pilot → proof → rollout GTM path. See [`COMMERCIAL.md`](COMMERCIAL.md); prices are
deliberately left TBD pending the author's decision. **v1.6.0** closes the batch-approval
gap (`GAP-ANALYSIS.md` B6): approvers can tick several pending requests in the todo list
and approve or reject them in one action, wrapped in a single atomic transaction so a
batch can never land half-applied. Every id is re-checked server-side — unauthorised,
already-closed or concurrently-taken requests are skipped rather than decided — and the
write path is now a single shared `_apply_decision()` used by both the single-request and
batch routes, so approval routing, notifications and audit wording cannot drift apart.
**v1.7.0** closes the biggest P0 in `GAP-ANALYSIS.md` (**C1: the timetable was one shared
demo fixture for every student**, which made the headline "timetable-aware affected courses"
feature hollow). Timetables are now per-student and importable: a CSV parser that accepts
English/Chinese (simplified or traditional) column names and an optional header, zero-padded
`HH:MM` storage, idempotent `replace`/`merge` modes, per-row error reporting with line
numbers, a coverage metric that puts "how many students still have fake timetables" on the
admin page, per-student timetable views for students/advisors/faculty, and
`flask import-timetable`. Until two students' affected courses actually differ, the feature
was decoration — the test suite now asserts exactly that. **v1.8.0** adds a config-gated OIDC
SSO channel (local password login stays as a disaster-recovery fallback — see the SSO section
below). **v1.9.0** closes the PIPL gap **D3**: the stats page, the CSV export and the account
list now each write a `view_pii` / `export` audit record on every access, so who viewed or
exported all-scope personal data is traceable. **v1.10.0** closes **D1**: proof attachments on
terminal leave requests past the retention period are now backed up then purged (the column is
cleared and a `purge_attachment` audit trail is written). **v1.11.0** closes **D2**: uploads are
scanned by a malware engine (ClamAV-compatible, config-gated via `LEAVE_AV_CMD`) before landing in
`uploads/` — infected files never touch the attachment area, never reach the database, and an
`av_block` audit trail is written. D-group (D1/D2/D3) is now fully closed. 203 unit tests + 236
smoke checks.

| Folder | What it is | 說明 |
|---|---|---|
| `leave-system/` | Flask + SQLite prototype (**v1.11.0**) — student leave, two-level approval (advisor → faculty), **per-student timetable import** (CSV with EN/中文 column aliases, idempotent replace/merge, `flask import-timetable`, coverage warning for demo-only timetables, student/advisor timetable views), **batch approve/reject** (tick rows in the todo list, one atomic transaction, per-request re-authorisation, 50-request cap), approval delegation (advisor/faculty can hand their level to a stand-in for a date range), admin advisor-reassignment, academic terms with per-term leave quotas (pending + approved both count), holiday calendar (non-teaching days skipped in affected courses), **academic-calendar file import** (terms / holiday ranges / makeup days, idempotent UPSERT), **makeup days** (weekend classes taught on another weekday's timetable still count as missed classes), built-in HKUST(GZ) 2026–27 calendar preset + `flask import-calendar` CLI, SLA escalation cron with 24h cooldown, CSRF protection, attachment access control + content sniffing, **attachment malware scanning** (config-gated via `LEAVE_AV_CMD`, ClamAV-compatible exit-code contract; uploads are scanned in a temp file before landing in `uploads/`, infected files are rejected with an `av_block` audit; `flask av-scan` re-scans existing files and quarantines hits), timetable-based affected-course preview, DB-persisted login throttle (IP+account and account-only keys), session invalidation on password change, common-weak-password blocklist, paginated notifications with per-item read, atomic approval transactions with concurrency guards, scope isolation on approvals/detail/dashboard, admin audit-log page, leave-type administration, transaction-safe deferred email delivery, status-filtered all-requests view, paginated account admin, **config-gated OIDC SSO channel** (local password login kept as a disaster-recovery fallback), **PII read audit** (the stats page, the CSV export and the account list each write a `view_pii` / `export` audit record on every access), **attachment retention & purge** (proof files on terminal requests past the retention window are backed up then purged, the column is cleared and a `purge_attachment` audit is written), streamed CSV export, 203 unit tests + 236-check smoke suite. Runs at `http://127.0.0.1:5055` | 可執行原型：學生請假、兩級審批、**課表真實化（逐生課表 CSV 匯入，中英簡繁欄名別名、replace／merge 冪等、`flask import-timetable` CLI、示範資料覆蓋率警示、學生／導師／院系課表檢視）**、**批次核准／駁回（待審清單勾選、單一交易原子化、逐張重新鑑權、單次上限 50 張）**、委託審批（導師／院系可把審批權暫交代理人）、管理員改派導師、學期制請假額度（待審＋已核准都計入）、校曆假日（受影響課程跳過非上課日）、**校曆檔匯入（學期／假日區間／補課日，冪等 UPSERT）**、**補課／調課日（週末補課按被補星期幾的課表計缺課）**、內建港科大（廣州）2026–27 校曆預設＋`flask import-calendar` CLI、催辦升級 cron（24 小時冷卻）、CSRF 防護、附件權限＋內容嗅探、**附件掃毒（組態門控 `LEAVE_AV_CMD`，相容 ClamAV 退出碼；上傳檔先掃後落盤、感染拒收並留 av_block 稽核、`flask av-scan` 存量重掃＋隔離）**、課表受影響課程、限流持久化、改密碼登出所有 session、弱密碼擋鎖、通知分頁＋單則已讀、審批交易原子化與併發守衛、範圍隔離（審批／詳情／儀表板）、後台稽核日誌、交易提交後才寄通知郵件、全部假單狀態篩選、帳號管理分頁、**組態門控 OIDC 單一登入通道（本地帳密為災難備援）**、**PII 讀取稽核（統計頁／CSV 匯出／帳號清單每次存取皆留 view_pii／export 軌跡）**、**附件留存期與銷毀（終態假單逾保存期即先備份再銷毀、清空 attachment 欄並留 purge_attachment 稽核）**、CSV 串流匯出 |
| `saas/` | Enterprise design package for HKUST(GZ): PRD, architecture & API, data & security, UI design, roadmap & cost, plus full PostgreSQL DDL | 企業級設計提案包（6 頁中英對照）＋完整資料庫 DDL |
| `assignment/` | Individual Ideation assignment (≈1,030 words) + 10-slide deck + 3 figures | 個人創意作業：Word 正文、簡報、流程圖 |

## Target users ｜目標用戶

**Who this is for — and who touches it every day.** SAAS is built for universities where absence
handling still lives in private email — especially Chinese-foreign joint-venture and
internationalised campuses with English-medium instruction, two-tier academic advising, and
PIPL obligations.

**Who buys it (decision makers)：**

- **Registry / 教務處 & Student Affairs / 學生事務部** — the process owner: today they adjudicate
  forwarded emails, chase missing proof documents, and hold the PIPL risk. SAAS gives them the
  structured ledger, the audit trail and the retention controls.
- **IT / 資訊系統部門** — wants OIDC SSO against the campus IdP, a small ops footprint (Flask + SQLite
  for pilot, PostgreSQL DDL provided for production), cron-friendly maintenance endpoints, and
  no third-party data sharing.

**Who uses it daily (end users)：**

- **Students / 學生** — submit a leave request with an uploaded proof document, see their affected
  courses per their real timetable, track approval status, and get notifications — no email threads.
- **Academic advisors / 導師（第一級審批）** — approve/reject level 1 (batch or one by one), delegate
  during leave, with full scope isolation: they only ever see their own advisees.
- **Faculty & programme heads / 院系（第二級審批）** — the escalation tier for long or high-risk absences,
  with quota visibility and an SLA escalation cron to keep queues moving.
- **System administrators / 系統管理員** — accounts, advisor reassignment, leave types, academic
  calendar & timetable imports, audit-log review.

**One prototype, two audiences:** the steering committee gets a clickable, testable flow before any
budget approval; the vendor/engineering team gets a hardened, test-covered codebase (203 unit tests +
236 smoke checks) to grow into production.

<span class="zh">**賣給誰（決策者）：** 教務處與學生事務部——流程所有者，今天在裁決轉發郵件、追討證明文件、扛著個人資料保護法風險，SAAS 給他們結構化帳本、稽核軌跡與留存控制；資訊系統部門——要校內 IdP 的 OIDC 單一登入、小運維 footprint（試點 Flask＋SQLite，生產環境 DDL 已備）、cron 友善的維護端點，且不外流任何第三方資料。</span>

<span class="zh">**誰每天在用（終端用戶）：** 學生——上傳證明提交假單、按真實課表看到受影響課程、追蹤進度、收站內通知；導師——第一級審批（可批次、可委託），只看得到自己的學生；院系——長假與高風險假單的升級審批層，有額度可見性與催辦升級 cron；系統管理員——帳號、改派、假單類型、校曆與課表匯入、稽核日誌覆核。</span>

<span class="zh">**一份原型，兩類受眾：** 指導委員會在核准預算前就能點按試用完整流程；工程團隊拿到的是有 203 項單元測試＋236 項冒煙測試覆蓋的硬化代碼庫，可直接長成生產系統。</span>

## Why adopt ｜為什麼採用（for universities）

This isn't a slide-deck proposal — it ships a **runnable prototype** alongside the enterprise design package, so a
steering committee can try the full leave-and-approval flow before approving any budget. Four reasons it makes
sense to act this academic cycle:

- **Payback under one year.** ≈ ¥2.0M / year in avoided cost (faculty email overhead, registry adjudication,
  duplicate-document handling) against ¥0.45M / year run cost. The ¥1.0M MVP build pays back inside Year 1.
- **Closes a live PIPL / 個人資料保護法 gap.** Student absence data (health reasons, family events, accommodation
  needs) today lives in private email inboxes — uncontrolled retention, no deletion, no cross-border audit trail.
  SAAS moves it into a single, retention-scheduled, access-controlled, audit-logged system.
- **Peer institutions are moving.** "Email your instructors" reads as 2010-era friction to applicants comparing
  HKUST(GZ) with CUHK(SZ), NYU Shanghai, Duke Kunshan. A modern, humane absence flow is a visible signal of digital
  maturity — and it produces the structured ledger that future analytics / risk-scoring / AI initiatives need.
- **De-risked pilot.** Phase-1 runs two programmes in parallel with the existing email process, with an explicit
  rollback plan at week 14. No sunk infrastructure cost if it doesn't meet adoption thresholds.

See the Executive Summary's "Why Now" section for the full case.

<span class="zh">這不是簡報式提案——附帶**可執行原型**，指導委員可在核准預算前先試用完整的請假與審批流程。本學年即須行動的四個理由：</span>

- <span class="zh">**不到一學年回本。** 穩態後每年可避免約 200 萬成本（教師郵件、教務裁決、重複文件），對應每年 45 萬運維；100 萬 MVP 開發費用在第一年內回收。</span>
- <span class="zh">**收斂個人資料保護法風險。** 學生請假資料（健康事由、家庭事件、便利需求）目前散落私人信箱——留存無控、刪除無機制、跨境無軌跡。SAAS 收進單一、有留存期、有權限、有審計的系統。</span>
- <span class="zh">**同業已在推進。** 「自行發電郵給教師」在申請者眼中是 2010 年代的摩擦。現代、友善的請假流程是數位成熟的顯性訊號——也為未來分析、風險評分、AI 功能鋪好結構化資料。</span>
- <span class="zh">**試點可回退。** 第一期兩個項目與現行電郵並行，第 14 週設回退閘門；未達採用門檻則退回現狀，無沉沒基礎設施成本。</span>

<span class="zh">完整論述見執行摘要的「為什麼是現在」區段。</span>

## Quick start ｜快速開始

```bash
# 原型系統
cd leave-system
python app.py            # http://127.0.0.1:5055
python smoke_test.py     # 236 項全鏈路冒煙測試
flask import-calendar    # 匯入涵蓋今天的內建校曆（也可帶校曆檔路徑）
flask import-timetable data/timetable-sample.csv   # 匯入課表（--merge 為增量）

# 設計提案包（靜態站）
cd saas
python -m http.server 5066 --bind 127.0.0.1
```

Demo accounts (password `Demo@2026`): student `2024001`, advisor `T001`, faculty `F001`, admin `admin`.

> Before deploying: set `LEAVE_SECRET` (see [`leave-system/README.md`](leave-system/README.md#環境變數)).

## Changelog ｜更新日誌

See [`CHANGELOG.md`](CHANGELOG.md) — **v1.7.0** makes the timetable real: instead of one
demo fixture shared by every student, timetables are per-student and importable from CSV
(English/中文 column aliases, header optional, zero-padded `HH:MM`, idempotent
`replace`/`merge`, per-row errors with line numbers), with a coverage metric on the admin
page, per-student views for students/advisors/faculty, and `flask import-timetable` (plus 28
new unit tests, 168 total, and 31 new smoke checks, 231 total). **v1.6.0** adds batch approval: tick rows in the
todo list and approve or reject them in one atomic transaction, with every id
re-authorised server-side and a 50-request per-batch cap; the approval write path is now
a single shared helper used by both the single and batch routes (plus 12 new regression
tests, 140 total, and 17 new smoke checks, 200 total). **v1.5.1** adds the
commercialisation brief ([`COMMERCIAL.md`](COMMERCIAL.md)): licensing tiers, the
compliance-driven buyer case, the readiness gate before any production contract, and the
GTM path (prices TBD); **v1.5.0** imports the official HKUST(GZ)
2026–27 academic calendar (plain-text calendar-file format, holiday ranges,
makeup days, built-in presets, admin import page and `flask import-calendar` CLI;
plus 13 new regression tests, 128 total);
**v1.4.0** closes five gaps from the
deployment gap analysis (`GAP-ANALYSIS.md`): approval delegation, admin advisor
reassignment, academic terms with per-term leave quotas, a holiday calendar, and an
SLA escalation cron (plus 10 new regression tests, 115 total);
**v1.3.0** adds competitive comparison against
DingTalk and Feishu (13-dimension table showing why general-purpose IM tools cannot
handle the multi-step academic absence-and-assessment flow), plus a monthly report
page with MVP outcomes, standout features, and roadmap; v1.2.1 strengthens the
buyer-incentive case with a "Why Now" section (ROI, PIPL, peer benchmarking,
de-risked pilot); v1.2.0 moves the last synchronous I/O out of the
write transaction: notification emails used to be sent inside `transaction()`, so a
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
(smoke suite now 236 checks).

完整變更記錄見 [`CHANGELOG.md`](CHANGELOG.md)。

## Absence workflow ｜請假流程

- ≤ 3 days → instructor approval only
- \> 3 days, or an official-activity absence → advisor → faculty review
- Official activity roster → auto-verified, instructors notified only
- Absence > 7 days → programme office → registry → instructors

## Single sign-on (SSO) ｜單一登入（SSO）

SSO is wired in as a **config-gated OIDC authorization-code channel** (since `v1.8.0`,
see `GAP-ANALYSIS.md` A2). Local username/password login is kept as a **disaster-recovery
fallback** — admins can still get in if the IdP is down.

- The login page shows a **"使用學校帳號登入"** button only when SSO is enabled.
- Identity mapping priority in `resolve_user()`: ① `external_id` → ② username claim →
  ③ email auto-link (toggleable) → ④ auto-provision (only when `SSO_PROVISION=1`, and the
  new account gets a random hash so it can **only** sign in via SSO).

> ⚠️ What this release does **not** do: it is a wiring channel, not a finished SSO
> integration. Each school must supply its real IdP parameters (issuer, `client_id`,
> redirect URI, scope) and the role-claim → local-role mapping at deploy time. Production
> should also add ID-token signature verification and nonce replay protection.

Full configuration: `SSO_CLIENT_ID` / `SSO_CLIENT_SECRET` / `SSO_AUTHORIZE_URL` /
`SSO_TOKEN_URL` / `SSO_USERINFO_URL` / `SSO_REDIRECT_URI` / `SSO_SCOPES` /
`SSO_USERNAME_CLAIM` / `SSO_ROLE_CLAIM` / `SSO_DEFAULT_ROLE` / `SSO_PROVISION` /
`SSO_AUTO_LINK_EMAIL`. Details in `leave-system/README.md` "單一登入（SSO）".

## Commercial ｜商業化

Positioning, buyers, licensing and go-to-market are documented in
[`COMMERCIAL.md`](COMMERCIAL.md). Summary ｜摘要：

| Tier ｜層級 | Scope ｜範圍 | Status ｜狀態 |
|---|---|---|
| Evaluation ／ Personal | 個人、教學、內部評估（不得部署生產環境、不得對第三方提供服務） | **免費**（`LICENSE` §2(a)） |
| Institutional Site License | 單一院校／校區生產部署（自託管，校方 IT 維運） | 商業授權 · 價格 **TBD** |
| Managed Service | 作者方託管與維運（含升級、備份、SLA） | 商業授權 · 價格 **TBD** |

The buyer case is **compliance, not convenience**: UKVI student-sponsor duties,
Australia's ESOS/ELICOS 80% attendance obligation and US FERPA expectations all require
institutions to evidence how absence and academic accommodation are handled — SAAS turns
that obligation into a query instead of a fire drill.

> ⚠️ **Readiness gate ｜就緒門檻**：原型可演示與試點，但簽署生產合約前須關閉
> `GAP-ANALYSIS.md` 中的 SSO（A2 · v1.8.0 已備妥 OIDC 接線通道，尚待校方提供真實 IdP 參數與角色對映）、
> PostgreSQL（A1）、課表接入學校 SIS／LMS（C1 的格式與
> 匯入通道已於 v1.7.0 就緒，尚待校方提供真實課表資料源）
> 與個資治理（D 組）四項缺口。詳見 `COMMERCIAL.md` §4。
>
> 本專案**尚未定價**；定價、折扣與合約範本由作者確認後另立私有文件。

<span class="zh">買的不是表單流程，而是**可舉證**：監管方問「這個學生缺了多少課、誰批的、依據什麼」時，幾秒內答得出來。</span>

## Author

Johnny, Jeng-lin Li · HKUST(GZ)

## License

This project is released under a **Commercial License** — all rights reserved. See
[`LICENSE`](LICENSE) for the full terms.

- **Personal / evaluation use** is permitted free of charge (no production deployment, no
  services to third parties).
- **Commercial use** (production deployment, offering as a service, or use by a for-profit
  organisation) requires a separate paid license. Contact the author to obtain one.

本專案採 **商業授權**，保留所有權利。完整條款見 [`LICENSE`](LICENSE)。

- **個人／評估用途**可免費使用（不得用於正式環境部署，亦不得對第三方提供服務）。
- **商業用途**（正式環境部署、作為服務提供、或由營利組織使用）須另購商業授權，請聯絡作者取得。

> **On `assignment/`:** the Individual Ideation essay is published as a worked example for
> reference and citation. **It must not be submitted as another student's own assessment.**
> If you cite it, please attribute: Li, J. (2026). *SAAS: A Student Absence and Academic
> Accommodation System* (Individual Ideation assignment), HKUST(GZ).
>
> `assignment/` 內的作業全文僅供參考與引用，**不得作為他人作業提交**。引用請註明出處。
