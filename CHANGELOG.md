# Changelog ｜更新日誌

本專案所有變更記錄於此。格式參考 [Keep a Changelog](https://keepachangelog.com/)，
版本號依 [Semantic Versioning](https://semver.org/)（MAJOR.MINOR.PATCH）。

All notable changes to this project are documented in this file.

---

## [0.7.0] — 2026-10-21

主軸是把 v0.4.0 聲稱的模組拆分**真正落地**，外加一輪安全與正確性加固。資料庫結構未變動，
既有資料可直接沿用；舊庫補欄位改走新的通用 migrate 框架，升級更穩。

### Added ｜新增

- **健康檢查 `GET /healthz`**：無需登入，回傳 JSON 健康狀態（含資料庫探測），
  供負載均衡／容器探測／CI 使用；資料庫異常時回 503。
- **通用 migrate 框架**：`db.py` 以 `MIGRATIONS` 清單驅動「舊庫缺欄位自動補上」，
  `affected_courses` 遷移改走此框架；日後新增欄位只需在清單加一行。
- **單元測試 +10 項（16 → 26）**：`tests/test_password_policy.py`（強度策略、不可與帳號相同、
  臨時密碼產生器）、`tests/test_csv_safe.py`（公式注入防護）。

### Changed ｜變更

- **`app.py` 真正模組化**：常數與純函式一律 `from utils import ...`，SQLite 連線／查詢
  `from db import ...`，刪除 `app.py` 內所有重複定義——v0.4.0 的文件聲稱已拆分，但實際上
  `app.py` 仍留著第二份實作（改一邊不會同步到另一邊，是潛在的漂移來源）。順帶刪除從未使用的
  `paginate()`。
- 冒煙測試由 73 項擴充至 **85 項**（密碼策略補齊、CSV 公式注入、統計範圍隔離、`/healthz`）。
- 「修改密碼」頁與「新增帳號」的密碼提示同步更新為新策略。

### Fixed ｜修正

- **統計頁假別彙總越權**：`/stats` 的「依假別統計」查詢原本沒有套用導師／院系範圍，
  導師會看到全系統的假別天數彙總；現在與逐學生統計共用同一範圍條件（`role_scope`）。
- **學生儀表板白查全系統統計**：學生身份也會執行 4 個全系統 COUNT 查詢——頁面雖不顯示，
  但既浪費也擴大資訊暴露面；現在只有審批者／管理員才查。
- 提交假單時 `overlapping_requests` 被呼叫兩次（一次判斷、一次取衝突單號）→ 只查一次。
- **企業版 DDL 補齊課表模型**：`saas/schema.sql` 新增 `timetable` 表與
  `absence_requests.affected_courses JSONB`——v0.6.0 只在原型加了課表功能，企業版
  PostgreSQL DDL 一直沒同步，正式環境據此落地會缺欄位。

### Security ｜安全性

- **CSV 公式注入防護**：CSV 匯出對 `= + - @` 開頭的自由文字（事由等）加 `'` 前綴，
  避免匯出檔在 Excel／Google Sheets 開啟時被當公式執行。
- **密碼強度策略補齊**（兌現 v0.5.0 的 Notes）：`validate_password(pw, username)` 新增
  「不得與帳號相同」「須同時含大寫、小寫字母與數字」。管理員重設密碼的自動產生器改用
  `temp_password()`——舊的 `secrets.token_urlsafe()` 有機率產生不符合新政式的密碼，
  導致重設被自己的校驗擋下。

### Notes ｜備註

- 密碼策略暫不要求特殊符號（可用性優先）；上生產前可再收緊（常見弱密碼擋鎖、強度評分）。
- `/healthz` 只探測程序與資料庫可達性，不含業務層語義。

---

## [0.6.1] — 2026-10-21

v0.6.0 的小幅修正：受影響課程清單不再顯示教室欄位。資料庫結構未變動，既有資料可直接沿用。

### Changed ｜變更
- **受影響課程隱藏教室**：提交頁即時預覽與假單詳情頁的「受影響課程（依課表推算）」清單，
  改為只列課程代碼、類型與起訖時間，不再顯示教室；`timetable.room` 資料欄位保留不變，
  正式環境仍可完整留存教室資訊。
- 同步更新根目錄 `README.md` 與 `leave-system/README.md` 的版本號與功能描述。

---

## [0.6.0] — 2026-10-15

在 v0.4.0／v0.5.0 之上，於請假流程加入「課表 × 請假」連動：學生填寫起訖日期與上午／下午時段時，
即時推算並顯示會錯過的課程；假單送出時也把受影響課程一併存檔，讓導師審批時一眼看到課程衝突。

### Added ｜新增
- **課表（timetable）**：新資料表 `timetable`（學生、星期 0–4、起訖時間、課程代碼、類型、教室、備註）。
  正式環境應由 SIS / LMS 同步；此版本內建示範課表（demo 學生共用，週一至週五）。
- **提交頁即時預覽**：選定起訖日期與時段後，前端呼叫 `GET /api/affected_courses`（需登入），
  即時列出該區間受影響的課程（日期／星期／時段／課程＋教室）。
- **假單存檔受影響課程**：`leave_requests.affected_courses`（JSON）寫入推算結果；
  假單詳情頁新增「受影響課程（依課表推算）」區塊。
- 按半天粒度推算：同日 AM→PM = 全天；單一時段只列對應上午或下午的課；中間日一律全天。

### Database ｜資料庫結構
- `leave_requests` 新增 `affected_courses TEXT NOT NULL DEFAULT ''`。
  既有資料庫於啟動時自動 migrate 補欄位（`ALTER TABLE`，不影響既有資料）。
- 新增 `timetable` 資料表與 `idx_tt_student` 索引。
- 冒煙測試由 66 項擴至 **73 項**（課表寫入、整天/半天受影響課程、API、提交後存檔）。
---

## [0.5.0] — 2026-10-13

在 v0.4.0（模組拆分、待辦分頁、單元測試）之上，補齊兩項安全強化。資料庫結構未變動，既有資料可直接沿用。

### Added ｜新增

- **防點擊劫持**：所有回應加上 `X-Frame-Options: DENY`，並於 `Content-Security-Policy` 加入
  `frame-ancestors 'none'`，防止系統被嵌入惡意 iframe 進行點擊劫持。
- **密碼強度策略（落地）**：新增常數 `MIN_PASSWORD_LEN = 8` 與 `validate_password()`，於「修改密碼」
  （`/password`）、「管理員重置密碼」（`/admin/users/<uid>/reset-password`）、「新增用戶」
  （`/admin/users/add`）三處強制最小長度。此前 v0.4.0 文件已聲稱「長度至少 8 碼」，本版正式在後端實作。

### Changed ｜變更

- 冒煙測試由 60 項擴充至 **66 項**（新增防點擊劫持標頭、密碼強度策略——含管理員新增短密碼被拒、
  學生改短密碼不生效等檢查）。

### Notes ｜備註

- 密碼策略目前僅檢查最小長度；上生產前建議再補「不得與帳號相同、須含大小寫與數字」等規則。

---

## [0.4.0] — 2026-10-07

原型系統模組化重構與測試補強。資料庫結構未變動，既有資料可直接沿用。

### Added ｜新增

- **`utils.py`**：抽出純函式（`calc_days`、`required_level_for`、`academic_year_start`、`allowed_file`）與常數，不依賴 Flask。
- **`db.py`**：抽出 SQLite 連線與查詢輔助（`get_db` / `q` / `execute` / `init_db`）。
- **單元測試**：`tests/` 共 16 項（天數計算、審批層級、學年起算、副檔名白名單）；CI 新增 `unit-test` job。
- **待審分頁**：`/approvals?scope=todo` 改為 SQL `LIMIT/OFFSET`，與「全部假單」一致。

### Changed ｜變更

- `app.py` 精簡為路由與業務流程；常數與純函式改由 `utils` / `db` 匯入。
- 刪除未使用的記憶體分頁函式 `paginate()`。
- `requirements.txt` 加入 `pytest>=8.0`。

### Security ｜安全性

- 無變更（沿用 v0.3.0 的 CSRF、附件權限、安全標頭、登入限流）。

---

## [0.3.0] — 2026-09-29

原型系統（`leave-system/`）的第二輪安全性與健壯性強化。資料庫結構未變動，既有資料可直接沿用。

### Added ｜新增

- **安全回應標頭**：所有回應加上 `X-Content-Type-Options: nosniff`、`Referrer-Policy` 與基本
  `Content-Security-Policy`（允許本專案所需的 inline style / script）。
- **可選 Email 通知通道**：設定 `LEAVE_SMTP_*` 環境變數後，`notify()` 在寫入站內通知的同時額外寄信；
  發送失敗靜默處理，絕不影響請假主流程（預設關閉）。
- **通知「全部標為已讀」**（`/notifications/read`，POST + CSRF）：可一次清空未讀，未讀項在清單中以左側色條標示。
- 環境變數 `LEAVE_SMTP_HOST` / `LEAVE_SMTP_USER` / `LEAVE_SMTP_FROM` / `LEAVE_SMTP_PASS` /
  `LEAVE_SMTP_PORT` / `LEAVE_SMTP_TLS`：啟用可選 Email 通道。

### Changed ｜變更

- 冒煙測試由 49 項擴充至 **60 項**（新增安全標頭、文字長度上限、通知標為已讀、Email 通道預設關閉等檢查）。
- 開啟通知頁不再自動清除未讀，改由明確的「全部標為已讀」按鈕處理。
- 預設 `secret_key` 未以 `LEAVE_SECRET` 覆寫時，啟動即印出安全警告。

### Fixed ｜修正

- **自由文字缺少長度上限**：事由、審批意見、聯絡電話過長可能撐爆版面／儲存。現在分別設上限
  500 / 300 / 30 字（前端 `maxlength` 先擋一層，後端再校驗）。

### Security ｜安全性

- 回應層補齊 `X-Content-Type-Options`、`Referrer-Policy` 與 `Content-Security-Policy`，
  降低 MIME 嗅探、referrer 洩漏與 XSS 風險。

---

## [0.2.0] — 2026-09-29

原型系統（`leave-system/`）的安全性與完整性強化。資料庫結構未變動，既有資料可直接沿用。

### Added ｜新增

- **CSRF 防護**：所有 POST 表單（`登入`/`提交假單`/`審批`/`撤回`/`後台`/`修改密碼`）皆需帶 session 內的
  CSRF token，缺少或不符者請求直接拒絕且不寫入資料庫。
- **修改密碼**（`/password`）：所有角色可自行修改密碼；密碼至少 8 碼、需二次確認，
  更新後自動登出。
- **管理員重設密碼**（`/admin/users/<id>/reset-password`）：未填寫時自動產生隨機臨時密碼。
- **管理員編輯帳號**（`/admin/users/<id>/edit`）：可改姓名、院系、班級、Email 與導師歸屬
  （導師異動時最常需要）。
- **分頁**：`我的假單` 與 `全部假單` 每頁 20 筆，附上下頁導覽與總筆數。
- **表單試算**：提交頁即時預覽請假天數，並提示是否會送院系複核。
- **學年統計**：學生儀表板改顯示「本學年已核准天數」（學年自 9/1 起算）。
- **GitHub Actions CI**：每次 push / PR 自動執行冒煙測試。
- `LEAVE_DB`、`LEAVE_UPLOAD_DIR` 環境變數：可指向其他資料庫／上傳目錄，便於測試隔離。

### Changed ｜變更

- 冒煙測試改為**每次使用全新暫存資料庫**，不再污染 `leave_system.db`；
  檢查項目由 10 項擴充至 **49 項**。
- Session cookie 加上 `HttpOnly` 與 `SameSite=Lax`，登入成功時重設 session（防 session fixation）。
- 附件檔名加入隨機字串，避免 URL 被猜測枚舉。
- 提交頁的日期輸入加上 `min` / `max` 限制，瀏覽器端先擋一層。

### Fixed ｜修正

- **附件權限漏洞**：`/uploads/<file>` 原本只驗證「已登入」，任何學生都能下載他人的病假證明
  （屬個人敏感資料）。現在僅限本人、該生導師、同院系審批人與管理員。
- **可重複請假**：原本同一學生可在重疊區間提交多張假單。現在會阻擋並提示衝突的假單編號
  （已駁回／已撤回的假單不佔用區間）。
- **缺少日期合理性校驗**：新增單張假單 90 天上限，以及最多回溯補請 30 天。
- 通知已讀標記處重複 commit。

### Security ｜安全性

- 登入限流：同一 IP + 帳號 5 分鐘內連續失敗 8 次即暫時鎖定（進程內計數；
  多進程部署請改用 Redis）。

---

## [0.1.0] — 2026-09-28

- 初始版本：`leave-system/` Flask + SQLite 可執行原型（學生請假、兩級審批、通知、統計、CSV 匯出）。
- `saas/` 企業級設計提案包（PRD、架構與 API、資料與安全、UI 設計、路線圖與成本、PostgreSQL DDL）。
- `assignment/` 個人創意作業（約 1,030 字正文、10 頁簡報、3 張圖）。
- 加入 MIT License，並於 README 聲明 `assignment/` 僅供參考引用、不得作為他人作業提交。
