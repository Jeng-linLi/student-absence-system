# Changelog ｜更新日誌

本專案所有變更記錄於此。格式參考 [Keep a Changelog](https://keepachangelog.com/)，
版本號依 [Semantic Versioning](https://semver.org/)（MAJOR.MINOR.PATCH）。

All notable changes to this project are documented in this file.

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
