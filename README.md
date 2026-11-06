# Student Absence & Academic Accommodation System (SAAS)

![CI](https://github.com/Jeng-linLi/student-absence-system/actions/workflows/ci.yml/badge.svg)

One problem, three artefacts: a **runnable prototype**, an **enterprise design package**, and the
**Individual Ideation assignment** that motivated them.

一個問題，三份產出：可執行的原型、企業級設計提案包，以及促成這一切的個人創意作業。

**Prototype version ｜原型版本：`v1.18.0`**（2026-11-06）· Commercial License · CI passing
· 商業化方案見 [`COMMERCIAL.md`](COMMERCIAL.md)
· 完整痛點與就緒度登錄見 [`SAAS-PAIN-POINTS.md`](SAAS-PAIN-POINTS.md)

> **導讀｜How to read this project**：下一段是完整版本編年史（v0.6.0 → v1.18.0），屬**歷史紀錄**；
> **當前現況**以 [`CHANGELOG.md`](CHANGELOG.md) 最新版、CI 結果與
> [`SAAS-PAIN-POINTS.md`](SAAS-PAIN-POINTS.md) 為準。提案（`saas/`）描述的是**目標藍圖**，
> 原型（`leave-system/`）描述的是**已實作能力**，`GAP-ANALYSIS.md` 描述的是**尚未關閉的缺口**，
> 三者不可互換。歷史月報的數字為報告區間快照，不代表現況。

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
`av_block` audit trail is written. D-group (D1/D2/D3) is now fully closed. **v1.12.0** closes
**B1/B2**: users reset forgotten passwords themselves via a one-time, expiring email link
(SHA-256-hashed tokens, generic responses to prevent account enumeration, per-account rate
limiting), and new accounts with an email receive a verification link (`email_verified` flag) —
a successful reset also marks the email verified. **v1.13.0** closes **E4 + C4**: every list
page (`/my`, `/approvals` in both todo and all scopes, `/stats`, `/export`) now takes the same
`q` / `date_from` / `date_to` filters — keywords are bound as parameters with `ESCAPE '!'`
so `%` and `_` are literals rather than a free full-table export, the date filter means
"the leave interval intersects the queried range", and search only ever narrows `role_scope()`.
Approval comments now reach the student in the notification for both intermediate and final
approval. Three correctness gaps were fixed along the way: attachment authorisation looked at
only the first request sharing a filename, an unknown `?term=` silently fell back to "all
history", and a failed purge transaction left a dangling attachment pointer.
**225 unit tests + 247 smoke checks.** **v1.14.0** closes **E3** with verifiable,
rollback-safe operations: SQLite online snapshots, optional attachments, a SHA-256 manifest,
SQLite integrity and core-table checks, safe-path and symlink rejection, and a CLI-only restore
guarded by `--yes`. Every restore preserves pre-restore database, WAL/SHM sidecars and attachments,
and attempts compensation if installation or final validation fails. Database-only backup/restore
leaves existing attachments untouched. **241 unit tests collected (240 passed, 1 skipped locally)
+ 247 smoke checks.** **v1.15.0** turns the previously loose "future AI features" list into a governed
intelligence roadmap: data and policy prerequisites, H0–H3 capability gates, an advisory-only AI adapter,
evaluation / fairness / privacy evidence, and a kill switch back to deterministic rules. No model call or
student-data export is introduced in this version; see [`AI-ROADMAP.md`](AI-ROADMAP.md).
**v1.16.0** adds the missing **AI ethics layer**: seven positive duties, student and approver rights,
four harm types and proxy variables, layered explainability, automation-bias interface constraints,
an accountability matrix with an independent **ethics veto**, restoration after incidents, and a checkbox
pre-launch acceptance list — see [`AI-ETHICS.md`](AI-ETHICS.md). Still documentation only.
**v1.17.0** adds [`SAAS-PAIN-POINTS.md`](SAAS-PAIN-POINTS.md), a traceable pain-point and readiness
register covering the proposal package, prototype, data/compliance, architecture, commercialisation and
AI governance — every item cites its source file and section, and is tagged resolved / partial / open /
drift. It also reconciles long-standing document drift: stale prototype versions and test counts in
`saas/index.html` and `COMMERCIAL.md`, contradictory D-group status across `COMMERCIAL.md`,
`GAP-ANALYSIS.md` and `leave-system/README.md`, Phase-3 risk-scoring items in the historical monthly
report that conflict with the AI red lines, and the ¥0.45M/yr vs ≈ ¥0.5M/yr cost-precision mismatch.
Historical reports are annotated, never rewritten. Still documentation only.
**v1.18.0** is the first code-level release after that documentation pass: it closes four
"protections that were written but never actually took effect" — an unbounded
`/api/affected_courses` date range (single GET could loop 36,000 days), calendar/holiday imports
that reported "database unchanged" after partially committing, an Email-verification one-time
token guard whose affected-row count was discarded, and a quota setter that accepted `NaN` and
silently disabled the limit forever — and closes **D3's fine-grained PII audit**: the single-request
detail page now writes a per-view `view_pii` record for non-owners, without copying student IDs,
reasons or phone numbers into the audit detail. 264 unit tests collected (263 passed, 1 skipped
locally) + 256 smoke checks. The agent-scope inconsistency between approval and statistics/export
is deliberately **not** fixed: widening it would let a stand-in export an entire cohort's PII, so
it is logged as a policy decision in [`SAAS-PAIN-POINTS.md`](SAAS-PAIN-POINTS.md) D-9／T-10.

| Path | What it is | 說明 |
|---|---|---|
| `leave-system/` | Flask + SQLite prototype (**v1.18.0**) — student leave, two-level approval (advisor → faculty), **verifiable backup and rollback-safe restore** (SQLite online snapshots, optional attachments, SHA-256 manifest, integrity checks, `flask backup` / `verify-backup` / `restore-backup`, pre-restore copies), **per-student timetable import** (CSV with EN/中文 column aliases, idempotent replace/merge, `flask import-timetable`, coverage warning for demo-only timetables, student/advisor timetable views), **batch approve/reject** (tick rows in the todo list, one atomic transaction, per-request re-authorisation, 50-request cap), approval delegation (advisor/faculty can hand their level to a stand-in for a date range), admin advisor-reassignment, academic terms with per-term leave quotas (pending + approved both count), holiday calendar (non-teaching days skipped in affected courses), **academic-calendar file import** (terms / holiday ranges / makeup days, idempotent UPSERT), **makeup days** (weekend classes taught on another weekday's timetable still count as missed classes), built-in HKUST(GZ) 2026–27 calendar preset + `flask import-calendar` CLI, SLA escalation cron with 24h cooldown, CSRF protection, attachment access control + content sniffing, **attachment malware scanning** (config-gated via `LEAVE_AV_CMD`, ClamAV-compatible exit-code contract; uploads are scanned in a temp file before landing in `uploads/`, infected files are rejected with an `av_block` audit; `flask av-scan` re-scans existing files and quarantines hits), timetable-based affected-course preview, DB-persisted login throttle (IP+account and account-only keys), session invalidation on password change, common-weak-password blocklist, paginated notifications with per-item read, atomic approval transactions with concurrency guards, scope isolation on approvals/detail/dashboard, admin audit-log page, leave-type administration, transaction-safe deferred email delivery, status-filtered all-requests view, paginated account admin, **self-service password reset** (one-time expiring email link, hashed tokens, account-enumeration-safe, rate-limited) + **email verification on account creation** (`email_verified`, config-gated via SMTP), **config-gated OIDC SSO channel** (local password login kept as a disaster-recovery fallback), **PII read audit** (the stats page, the CSV export and the account list each write a `view_pii` / `export` audit record on every access), **attachment retention & purge** (proof files on terminal requests past the retention window are backed up then purged, the column is cleared and a `purge_attachment` audit is written), **list search & date-range filters** on every list page (`q`/`date_from`/`date_to`, parameterised `LIKE ... ESCAPE '!'`, scope-narrowing only), approval comments carried into student notifications, streamed CSV export, **v1.18.0 hardening** (bounded affected-course preview, transactional calendar/holiday imports, one-time verification-token guard, finite-only quotas, per-request detail `view_pii` audit), 264 unit tests collected (263 passed, 1 skipped) + 256-check smoke suite. Runs at `http://127.0.0.1:5055` | 可執行原型：學生請假、兩級審批、**課表真實化（逐生課表 CSV 匯入，中英簡繁欄名別名、replace／merge 冪等、`flask import-timetable` CLI、示範資料覆蓋率警示、學生／導師／院系課表檢視）**、**批次核准／駁回（待審清單勾選、單一交易原子化、逐張重新鑑權、單次上限 50 張）**、委託審批（導師／院系可把審批權暫交代理人）、管理員改派導師、學期制請假額度（待審＋已核准都計入）、校曆假日（受影響課程跳過非上課日）、**校曆檔匯入（學期／假日區間／補課日，冪等 UPSERT）**、**補課／調課日（週末補課按被補星期幾的課表計缺課）**、內建港科大（廣州）2026–27 校曆預設＋`flask import-calendar` CLI、催辦升級 cron（24 小時冷卻）、CSRF 防護、附件權限＋內容嗅探、**附件掃毒（組態門控 `LEAVE_AV_CMD`，相容 ClamAV 退出碼；上傳檔先掃後落盤、感染拒收並留 av_block 稽核、`flask av-scan` 存量重掃＋隔離）**、課表受影響課程、限流持久化、改密碼登出所有 session、弱密碼擋鎖、通知分頁＋單則已讀、審批交易原子化與併發守衛、範圍隔離（審批／詳情／儀表板）、後台稽核日誌、交易提交後才寄通知郵件、全部假單狀態篩選、帳號管理分頁、**自助密碼重設（Email 一次性時效連結、token 只存雜湊、泛用回應防帳號枚舉、頻率限制）＋建號 Email 驗證（email_verified，SMTP 組態門控）**、**組態門控 OIDC 單一登入通道（本地帳密為災難備援）**、**PII 讀取稽核（統計頁／CSV 匯出／帳號清單每次存取皆留 view_pii／export 軌跡）**、**附件留存期與銷毀（終態假單逾保存期即先備份再銷毀、清空 attachment 欄並留 purge_attachment 稽核）**、**列表搜尋與日期區間（`/my`、`/approvals`、`/stats`、`/export` 共用 `q`／`date_from`／`date_to`，`LIKE` 參數化並跳脫 `%`／`_`，搜尋只收窄範圍）**、核准意見帶進學生通知、CSV 串流匯出、**v1.18.0 把假防護補成真的（課表預覽區間上限、校曆／假日匯入交易化、Email 驗證一次性守衛、額度拒收 NaN）＋單張詳情頁逐筆 `view_pii` 稽核** |
| `saas/` | Enterprise design package for HKUST(GZ): PRD, architecture & API, data & security, UI design, roadmap & cost, plus full PostgreSQL DDL | 企業級設計提案包（6 頁中英對照）＋完整資料庫 DDL |
| `assignment/` | Individual Ideation assignment (≈1,030 words) + 10-slide deck + 3 figures | 個人創意作業：Word 正文、簡報、流程圖 |
| `AI-ROADMAP.md` | Governed intelligence roadmap: data foundation, H0–H3 gates, advisory-only architecture, evaluation and kill switch | 智能化治理路線：資料地基、H0–H3 閘門、僅建議架構、評估與熔斷 |
| `AI-ETHICS.md` | AI ethics framework: rights, fairness and harm types, explainability, automation bias, accountability matrix, ethics veto, restoration and pre-launch checklist | AI 倫理框架：權利、公平與傷害、可解釋、自動化偏見、問責矩陣、倫理否決、復權與上線檢核表 |
| `SAAS-PAIN-POINTS.md` | Traceable pain-point and readiness register: current problems, roles, data/compliance, architecture, commercialisation, AI governance and a document-drift reconciliation table | 痛點與就緒度登錄：現況、角色、資料合規、架構、商業化、AI 治理，以及文件矛盾校準表（每條附來源章節） |

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
budget approval; the vendor/engineering team gets a hardened, test-covered codebase (264 unit tests
collected: 263 passed, 1 skipped locally; plus 256 smoke checks) to grow into production.

<span class="zh">**賣給誰（決策者）：** 教務處與學生事務部——流程所有者，今天在裁決轉發郵件、追討證明文件、扛著個人資料保護法風險，SAAS 給他們結構化帳本、稽核軌跡與留存控制；資訊系統部門——要校內 IdP 的 OIDC 單一登入、小運維 footprint（試點 Flask＋SQLite，生產環境 DDL 已備）、cron 友善的維護端點，且不外流任何第三方資料。</span>

<span class="zh">**誰每天在用（終端用戶）：** 學生——上傳證明提交假單、按真實課表看到受影響課程、追蹤進度、收站內通知；導師——第一級審批（可批次、可委託），只看得到自己的學生；院系——長假與高風險假單的升級審批層，有額度可見性與催辦升級 cron；系統管理員——帳號、改派、假單類型、校曆與課表匯入、稽核日誌覆核。</span>

<span class="zh">**一份原型，兩類受眾：** 指導委員會在核准預算前就能點按試用完整流程；工程團隊拿到的是有 264 項單元測試（本機 263 通過、1 略過）＋256 項冒煙測試覆蓋的硬化代碼庫，可直接長成生產系統。</span>

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

## Future intelligence ｜智能化路線

**v1.15.0–v1.16.0 document the route; neither ships an AI feature.** The system already produces a structured,
auditable leave ledger, but a model should not enter a high-stakes approval path until policy versions,
de-identified data, evaluation evidence and a working rollback exist. The roadmap therefore starts with
governance, not a chatbot:

- **H0 · Trust foundation** — versioned policy, de-identified read-only views, golden set, DPIA, feature flag.
- **H1 · Deterministic assistance** — missing-document, calendar, quota and workload checks without an LLM.
- **H2 · Bounded advisory** — shadow mode → advisor pilot → opt-in, with schema-constrained output, abstention,
  PII minimisation and full model / prompt / policy version logging.
- **H3 · Aggregate insight** — policy simulation and supportive group-level trends on a read replica; no
  individual adverse action.

AI may assist, never decide. Any automatic decision, unauthorised PII flow or failed fallback triggers the
kill switch and returns the whole flow to deterministic rules. Full blueprint: [`AI-ROADMAP.md`](AI-ROADMAP.md);
buyer-facing gates: [`saas/plan.html`](saas/plan.html) §5.1.

**v1.16.0 adds the ethics layer — compliance is not ethics.** A signed DPIA says the system may lawfully
proceed, not that a student is treated fairly. [`AI-ETHICS.md`](AI-ETHICS.md) turns the red lines into
positive, checkable duties: a student can refuse AI silently and without giving a reason; suggestions can be
inspected, corrected and deleted without touching the official leave record; approvers are never scored on
suggestion-adoption rate; an independent ethics review with student representation holds a **veto** over every
H0–H3 transition; and after an incident the institution must **restore** the student to where they would have
been, with the burden of proof on the institution. Consent is not a blanket authorisation for prohibited uses.

<span class="zh">**v1.15.0 只補路線文件，沒有上線 AI 功能。** 原型已有可稽核的結構化請假帳本，但在政策版本、去識別化資料、評估證據與回退演練到位前，不應讓模型進入高影響審批路徑。路線依序為：H0 可信地基、H1 規則式智慧、H2 受限 AI 建議、H3 匯總洞察；AI 只能輔助、不得決定，異常即熔斷回到確定性規則。</span>

<span class="zh">**v1.16.0 補上倫理層：合規不等於倫理。** DPIA 簽核只代表可以合法地做，不代表學生被公平對待。[`AI-ETHICS.md`](AI-ETHICS.md) 把紅線寫成可查證的正面義務：學生可無痕、免理由拒絕 AI；建議可查閱、更正、刪除且不影響正式假單；不得以建議採納率考核審批人；含學生代表的獨立倫理審查對 H0–H3 每次升級都有否決權；事故後學校必須把學生**復權**回原狀，舉證責任在學校。同意不是禁止用途的萬用授權。</span>

## Quick start ｜快速開始

```bash
# 原型系統
cd leave-system
python app.py            # http://127.0.0.1:5055
python smoke_test.py     # 256 項全鏈路冒煙測試
flask import-calendar    # 匯入涵蓋今天的內建校曆（也可帶校曆檔路徑）
flask import-timetable data/timetable-sample.csv   # 匯入課表（--merge 為增量）
flask backup           # 建立含附件的可驗證備份（--no-uploads 僅資料庫）
flask verify-backup backups/leave-backup-...       # 還原前驗證雜湊與 SQLite 完整性

# 設計提案包（靜態站）
cd saas
python -m http.server 5066 --bind 127.0.0.1
```

Demo accounts (password `Demo@2026`): student `2024001`, advisor `T001`, faculty `F001`, admin `admin`.

> Before deploying: set `LEAVE_SECRET` (see [`leave-system/README.md`](leave-system/README.md#環境變數)).

## Changelog ｜更新日誌

See [`CHANGELOG.md`](CHANGELOG.md) — **v1.18.0** is a code-level release that turns four
written-but-inert protections into real ones (bounded affected-course preview, transactional
calendar/holiday imports, a working one-time Email-verification token guard, finite-only leave
quotas) and closes D3's fine-grained PII audit: the request detail page now writes a per-view
`view_pii` record for non-owners. Adds 23 regression tests and 9 smoke checks. The agent-scope
inconsistency between approval and export is logged as a policy decision, not silently widened —
see D-9／T-10 in [`SAAS-PAIN-POINTS.md`](SAAS-PAIN-POINTS.md).
**v1.17.0** adds [`SAAS-PAIN-POINTS.md`](SAAS-PAIN-POINTS.md),
a traceable pain-point and readiness register covering the proposal package, prototype, data/compliance,
architecture, commercialisation and AI governance, and reconciles long-standing document drift (stale
version and test counts, contradictory D-group status, superseded Phase-3 risk-scoring items and a
cost-precision mismatch). Dated historical reports are annotated, never rewritten. Documentation only.
**v1.16.0** adds the AI ethics layer: seven positive duties, student
and approver rights, four harm types and proxy variables, layered explainability, automation-bias interface
rules, an accountability matrix with an independent ethics veto, incident restoration, and a checkbox
pre-launch acceptance list ([`AI-ETHICS.md`](AI-ETHICS.md)). Documentation only. **v1.15.0** replaces a
vague AI wish list with a governed intelligence roadmap: H0–H3 capability gates, advisory-only architecture,
data / policy prerequisites, evaluation and fairness evidence, and a kill switch back to deterministic rules.
No model call, no new dependency, no student-data export. **v1.14.0** closes the backup/restore gap:
SQLite online snapshots and optional attachments are packaged with a SHA-256 manifest,
verified for file-list, path, database-integrity and core-table correctness, and restored
only through an explicit `--yes` CLI flow. Pre-restore copies preserve the database, WAL/SHM
sidecars and replaced attachments; failures attempt compensation, while database-only
operations leave current attachments untouched. Adds 16 regression tests (241 collected:
240 passed, 1 skipped locally). **v1.13.0** makes the lists searchable: `/my`,
`/approvals`, `/stats` and `/export` all accept the same `q` / `date_from` / `date_to`
filters, keywords are bound as parameters with `ESCAPE '!'` (so `%` is a literal, not a
free full-table export), the date filter uses interval-intersection semantics, and search
can only narrow `role_scope()` — never widen it. Approval comments now reach the student
in the notification, and three correctness gaps were fixed (multi-request attachment
authorisation, invalid `?term=` fallback, dangling pointer after a failed purge
transaction); plus 12 new regression tests, 225 total. **v1.7.0** makes the timetable real: instead of one
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
> 匯入通道已於 v1.7.0 就緒，尚待校方提供真實課表資料源），以及 SLA／監控／事件響應流程。
> **個資治理 D 組主體已於 v1.9.0–v1.11.0 關閉**（D1 留存銷毀、D2 附件掃毒、D3 大範圍 PII 稽核）；
> **v1.18.0 再關閉單張詳情頁的細粒度 PII 檢視稽核**，因此 **D 組僅剩 D4 通知偏好**。
> 另有一項**刻意的政策待決**：代理人範圍在審批與統計／匯出口徑不一致（D-9／T-10），
> 放寬等於讓代理人匯出委託人全系學生個資，須由教務處／DPO 先訂政策，本版不擅自變更。
> 完整對照見 [`SAAS-PAIN-POINTS.md`](SAAS-PAIN-POINTS.md) §3、§7。詳見 `COMMERCIAL.md` §4。
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
