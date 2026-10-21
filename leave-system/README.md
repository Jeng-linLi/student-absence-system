# 線上請假系統（原型 v0.8.0）

學生線上提交假單 → 導師審批 → （必要時）院系複核 → 核准生效。Flask + SQLite，開箱即跑。

## 啟動

```bash
# 或雙擊 start.bat
"C:\\Users\\Johnny\\.workbuddy-ai\\binaries\\python\\envs\\default\\Scripts\\python.exe" app.py
```

打開 <http://127.0.0.1:5055>。首次啟動自動建庫並寫入示範資料。

## 演示帳號（統一密碼 `Pass@123`）

| 角色 | 帳號 | 姓名 | 能做的事 |
|---|---|---|---|
| 學生 | `2024001` | 李政霖 | 提交/查看/撤回自己的假單 |
| 學生 | `2024002` `2024003` `2024004` `2024005` | — | 同上 |
| 導師 | `T001` | 陳導師 | 審批 AI-1 班（第 1 級） |
| 導師 | `T002` | 林導師 | 審批 AI-2 班 |
| 院系 | `F001` | 王院辦 | 複核人工智能學域（第 2 級） |
| 管理員 | `admin` | 系統管理員 | 帳號管理、假別管理 |

## 審批規則

- 請假 **≤ 3 天**：導師核准即生效。
- 請假 **> 3 天**，或假別標記「需院系審批」（公假、喪假）：導師核准後自動轉院系複核。
- 病假**必須**上傳證明文件。
- 駁回必須填寫理由，理由會通知學生。
- 天數按半天粒度計：同日 AM→PM = 1 天，同日 AM→AM = 0.5 天。

## 表單校驗

- 單張假單最多 **90 天**。
- 最多可回溯補請 **30 天**內的假。
- **同一區間不可重複請假**；被阻擋時會提示衝突的假單編號。已駁回／已撤回的假單不佔用區間。
- 自由文字長度上限：事由 **500 字**、審批意見 **300 字**、聯絡電話 **30 字**（前端 `maxlength` 先擋，後端再校驗）。

## 課表 × 請假（受影響課程）

- 學生在「提交假單」頁選定**起訖日期**與**上午／下午時段**後，系統即時算出並顯示請假期間會錯過的課程（日期、星期、時段、課程代碼）。
- 按半天粒度：同日 AM→PM 為全天；單一時段只列該上午／下午的課；多日假的中間日為全天。
- 假單送出時，受影響課程一併寫入 `leave_requests.affected_courses`（JSON），假單詳情頁可見，方便導師判斷課程衝突。
- 資料來源為 `timetable` 表；`seed` 會寫入一份**示範課表**（demo 學生共用，週一至週五）。正式環境請改由 SIS / LMS 同步，勿依賴硬編碼。

## 安全設計

| 項目 | 做法 |
|---|---|
| CSRF | 所有 POST 表單需帶 session 內的 CSRF token，不符者直接拒絕 |
| 安全標頭 | 回應加 `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy` 與基本 `Content-Security-Policy`（含 `frame-ancestors 'none'`） |
| Session | 登入成功時重設 session（防 fixation）；cookie 設 `HttpOnly` + `SameSite=Lax`；閒置超過 `LEAVE_SESSION_IDLE_MINUTES`（預設 480 分鐘）需重新登入 |
| Session 作廢 | 管理員重設密碼或使用者自改密碼後，`users.pwd_changed_at` 更新，登入早於此時間的舊 session 一律登出（簽名 cookie 無法逐出，以時間戳記檢驗） |
| 登入限流 | 同一 IP + 帳號 5 分鐘內失敗 8 次即鎖定；計數寫入 `login_attempts` 表——重啟不清零、多進程一致（正式環境量大可換 Redis，介面已隔離） |
| 附件存取 | 僅本人、該生導師、同院系審批人、管理員可下載；檔名含隨機字串防枚舉 |
| 密碼 | 長度至少 8 碼、須含大寫／小寫字母與數字、不可與帳號相同、不可與舊密碼相同（後端 `validate_password` 於修改／管理員重設／新增用戶強制）；使用者可自行修改，管理員可重設（未填則產生符合策略的隨機臨時密碼） |
| SQL | 全部參數化查詢 |
| CSV 匯出 | 自由文字若以 `= + - @` 開頭會加 `'` 前綴，防止 Excel／Sheets 公式注入 |
| 帳號枚舉 | 帳號不存在時也跑一次固定 dummy hash 比對，拉平與「密碼錯誤」的回應時間差 |
| 後台防呆 | 管理員不可停用自己、不可停用最後一位在職管理員；新增帳號強制角色白名單與帳號格式校驗 |

> `SECRET_KEY` 預設為開發用值，部署前請設環境變數 `LEAVE_SECRET`；
> 走 HTTPS 時再加 `LEAVE_COOKIE_SECURE=1`。

## 功能清單

| 角色 | 功能 |
|---|---|
| 學生 | 提交假單（含附件、**依課表預覽受影響課程**）、我的假單（按狀態篩選＋分頁）、撤回、查看審批進度與操作日誌、修改密碼 |
| 導師 | 待我審批、全部假單（分頁）、核准/駁回、班級請假統計、CSV 匯出、修改密碼 |
| 院系 | 同上（第 2 級複核，範圍為所屬院系） |
| 管理員 | 帳號增刪/停用/編輯、重設密碼、假別管理（是否需證明、是否需院系） |
共通：站內通知（可選 Email 通道，需設定 `LEAVE_SMTP_*`）、操作日誌（誰在何時做了什麼）、淺色/深色主題切換。
維運：`GET /healthz` 健康檢查（無需登入，回傳 JSON，資料庫異常時回 503）。

## 目錄結構

```
leave-system/
  app.py              路由與業務流程（常數與純函式、DB 輔助分別見 utils.py / db.py）
  utils.py            純函式與常數（calc_days、validate_password、csv_safe 等，可單元測試）
  db.py               SQLite 連線 / 查詢輔助 / 通用 migrate
  schema.sql          資料庫結構
  smoke_test.py       全鏈路冒煙測試（93 項）
  tests/              單元測試（pytest，42 項）
  start.bat           一鍵啟動
  templates/          Jinja2 頁面模板
  static/css/         樣式
  uploads/            上傳的證明文件
  leave_system.db     SQLite 資料庫（自動生成）
```

## 測試

```bash
python -m pytest tests/ -v   # 42 項單元測試（calc_days / 審批層級 / 學年 / 副檔名 / 密碼策略 / CSV 安全 / 認證加固）
python smoke_test.py         # 93 項全鏈路冒煙：兩級審批、撤回、CSRF、附件權限、分頁、受影響課程、密碼策略、CSV 注入、統計範圍、healthz、session 失效、限流持久化、後台防呆
```

測試會使用**全新的暫存資料庫**，不會動到 `leave_system.db`。

## 環境變數

| 變數 | 預設 | 說明 |
|---|---|
| `LEAVE_SECRET` | `dev-only-change-me-in-production` | Flask `SECRET_KEY`，**部署前務必覆寫** |
| `LEAVE_COOKIE_SECURE` | `0` | 設 `1` 時 session cookie 加上 `Secure`（僅 HTTPS） |
| `LEAVE_SESSION_IDLE_MINUTES` | `480` | 閒置多久（分鐘）需重新登入 |
| `LEAVE_DB` | `leave_system.db` | 資料庫路徑（測試隔離用） |
| `LEAVE_UPLOAD_DIR` | `uploads/` | 附件上傳目錄 |
| `LEAVE_SMTP_HOST` / `LEAVE_SMTP_USER` / `LEAVE_SMTP_FROM` / `LEAVE_SMTP_PASS` / `LEAVE_SMTP_PORT` / `LEAVE_SMTP_TLS` | （未設則不啟用） | 選用 Email 通知通道；`HOST`/`USER`/`FROM` 三者齊全才啟用，發送失敗不影響主流程 |

## 上生產前還要補

1. **認證**：接學校 SSO / CAS，替換本地帳號；密碼改由 IAM 託管。
2. **安全**：上傳檔案做病毒掃描、`SECRET_KEY` 走密鑰管理服務；登入限流已持久化到 SQLite
   （多進程一致），量大後可再換 Redis 橫向擴展。
   （CSRF token、參數化 SQL、`SECRET_KEY` 環境變數、附件權限已於 v0.2.0 補上。）
3. **部署**：換 Gunicorn/Waitress + Nginx，SQLite 換 PostgreSQL（DDL 見 `../saas/schema.sql`）。
4. **業務**：假期額度上限、學期/校曆（排除假日扣天數）、銷假流程、與教務考勤系統對接、企業微信通知（站內通知已就緒，Email 為可選通道，需自備 SMTP 伺服器）。
5. **合規**：個資（請假事由、病假證明）保存期限與存取權限需符合學校與《個人信息保護法》要求。
