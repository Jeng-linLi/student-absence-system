# SaaS 痛點全景與就緒度登錄 — SAAS Pain Points & Readiness Register

> **狀態｜Status：v1.19.1 盤點文件（2026-11-09）**
>
> **v1.19.1 增量**：修復安全郵件的 Host header poisoning——重設密碼與 Email 驗證連結
> 不再由請求 `Host` 生成，只接受固定、經格式校驗的 `LEAVE_BASE_URL` HTTP(S) origin。
> 新增惡意 Host 與無效組態回歸測試；功能缺口與生產就緒判斷不變。
>
> **v1.19.0 增量**：① **D-4 通知偏好與每日摘要已在原型關閉**，支援即時、每日或關閉一般
> Email，站內通知與安全郵件邊界不變；正式環境仍須 SMTP、每日排程與失敗告警，摘要採至少一次
> 投遞。② 修復巢狀交易提前寄信及寫庫失敗後的附件孤兒。③ **T-8 已校準**：EW-5 改為可枚舉的
> 固定條件與支持性人工跟進，不再使用個人綜合風險分、隱性權重或 GPA 趨勢；EW 全組仍屬提案，
> 原型尚未交付。D-9／T-10 代理人範圍政策繼續刻意保留。
>
> **v1.18.0 增量**：① **D-3 細粒度 PII 稽核已關閉**——`/request/<rid>` 對非當事人
> 逐筆寫 `view_pii`（`GAP-ANALYSIS.md` D3）；② 補掉四處「假防護」（課表預覽區間上限、
> 匯入交易化、驗證 token 守衛、額度 NaN），詳見 `CHANGELOG.md`；③ 新增
> **D-9／T-10**：代理人範圍在審批與統計／匯出之間口徑不一致，**刻意未修**，
> 因為放寬等於讓代理人能匯出委託人全系個資，需校方先訂政策。
> 本文其餘「細粒度稽核仍缺」的歷史描述已就地加註，較早段落不代表現況。
>
> 本文是**痛點盤點與對照表，不是功能實作、不是報價依據、不是已上線能力證明**。
> 盤點範圍：`saas/` 六頁中英對照提案包與 `schema.sql`、根目錄治理文件
> （`README.md`、`GAP-ANALYSIS.md`、`COMMERCIAL.md`、`AI-ROADMAP.md`、`AI-ETHICS.md`）、
> `leave-system/` 可執行原型、`assignment/` 作業正文。
>
> **引用規則**：每條都標註來源檔案與章節，讀者可逐項回查。本文不新增估算數字、
> 不把規劃估算改寫成實測、不把提案中的 Phase 2／3 功能寫成已交付功能。

---

## 0. 怎麼讀這份文件

整個專案同時存在三種「真實」，混淆它們就是大部分爭議的來源：

| 層次 | 代表檔案 | 它說的是 | 常見誤讀 |
| --- | --- | --- | --- |
| **提案主張（claim）** | `saas/*.html`、`COMMERCIAL.md` | 想賣給院校的目標藍圖、估算與時程 | 被當成「現在就有」 |
| **原型現況（prototype）** | `leave-system/`、`CHANGELOG.md` | 本機可執行、CI 可驗證的程式碼與測試 | 被當成「可簽生產合約」 |
| **生產就緒（production）** | `GAP-ANALYSIS.md`、`COMMERCIAL.md` §4 | 尚缺的工程、合規、維運與商業門檻 | 被當成「已全部關閉」 |

**現況標籤**：`已解`＝原型內已有可執行程式碼或可查核文件；`部分`＝通道／制度已備、外部輸入或驗證仍缺；
`未解`＝尚未落地；`漂移`＝不同檔案對同一件事說法不一致，本身就是痛點。

> **重要**：`已解` 只代表「原型內關閉」，**不等於**可在大學生產環境部署。
> 例如 D1／D2／D3 個資治理已在原型關閉，但 `COMMERCIAL.md` §4 的 Readiness gate
> 仍需外部 IdP、PostgreSQL、真實課表資料源與合約文件才能關閉。

---

## 1. 現況痛點（問題陳述層）

來源集中於 `saas/index.html` §1 Problem Statement、`README.md` Target users、
`assignment/content.md`。

| ID | 痛點 | 來源 | 現況 | 原型對應／殘留 |
| --- | --- | --- | --- | --- |
| P1 | 學生逐封電郵通知每位任課教師，單次請假 8–15 封、無法確認送達 | `saas/index.html` §1 第 1 列 | 部分 | 原型以一次填表＋`notify()` 佇列寄信取代人工轉寄；但**受影響教師通知**與「官方活動免附證明」屬提案範圍，原型尚未逐科寄發 |
| P2 | 缺乏集中紀錄，個資散落私人信箱，跨境／留存／刪除不可控 | `saas/index.html` §1 第 2 列 | 部分（原型主體） | `leave_requests`／`approvals`／`audit_logs` 結構化帳本；v1.9.0 起大範圍 PII 讀取寫入 `view_pii`／`export` 稽核，**v1.18.0 起單張詳情頁對非當事人逐筆稽核**（`GAP-ANALYSIS.md` D3）。**仍缺**：跨境／刪除權自助流程、DPIA 與資料分級文件 |
| P3 | 考評衝突靠人工協調，各班補考口徑不一 | `saas/index.html` §1 第 3 列 | 部分 | 原型有 `timetable` 受影響課程預覽與 `makeup_days`；**自動偵測衝突＋5 種標準學業便利方案**仍是 PRD 目標（`saas/prd.html` §5），未落地 |
| P4 | 官方活動證明每學期重複上傳 | `saas/index.html` §1 第 4 列 | 未解 | `saas/prd.html` §6 規劃活動名冊自動核實；原型無 official activity roster |
| P5 | 項目辦公室看不見缺席趨勢，介入過晚 | `saas/index.html` §1 第 5 列 | 部分 | 原型有 `/stats` 與 v1.13.0 搜尋篩選；**早期預警 EW-1～EW-5** 與院系看板屬 PRD（`saas/prd.html` §9–§10），未落地 |
| P6 | 教師耗時處理重複郵件 | `saas/index.html` §1 第 6 列 | 部分 | 原型有待辦佇列、v1.6.0 批次審批、v1.4.0 委派與 SLA 催辦；決策脈絡完整度仍低於提案描述 |
| P7 | 表面是表單問題，本質是**可舉證**問題 | `README.md` Commercial、`assignment/content.md` | 已解（設計立場） | 稽核導向是原型與提案的一致核心；但「可舉證」同時意味著**完整的缺席紀錄也可能是監控工具**，此張力由 `AI-ETHICS.md` 承接 |

> **P7 是整份痛點清單的樞紐**：同一份帳本既能保護學生（申訴有據），也能傷害學生
> （慢性病學生被標記）。`assignment/content.md` 已點出 surveillance 與標籤風險，
> `AI-ETHICS.md` §2／§4 把它制度化為四種傷害與代理變數盤點。

---

## 2. 角色痛點

來源：`saas/prd.html` §2 Users & Personas、§3 Feature Requirements by Role；`README.md` Target users。

| 角色 | 痛點 | 現況 | 說明 |
| --- | --- | --- | --- |
| 學生 | 不知道進度、要重複補件、缺課影響不透明 | 部分 | 原型有狀態追蹤、通知、v1.13.0 核准意見回饋、逐生課表預覽；但**無免理由拒絕 AI 的介面、無查閱／更正／刪除自助流程、無非正式數位協助通道**（`AI-ETHICS.md` §3、`assignment/content.md`） |
| 學生（弱勢） | 慢病、身心狀況、非母語、無穩定網路者被流程本身排除 | 未解 | `AI-ETHICS.md` §4 列為分配／尊嚴／程序傷害；需 assisted non-digital route，目前僅為承諾 |
| 導師／院系 | 待辦積壓、決策缺乏上下文、信件轟炸 | 部分 | v1.6.0 批次、v1.4.0 委派／SLA、v1.13.0 搜尋已緩解；**v1.19.0 已交付即時／每日摘要／關閉一般 Email 偏好**，正式環境仍須 SMTP、每日排程與失敗告警（`GAP-ANALYSIS.md` D4） |
| 審批人（AI 情境） | 建議可能變成實質決定（自動化偏見） | 漂移 | `AI-ETHICS.md` §6 禁止以採納率考核、禁止建議長得像決定；但尚未有介面可驗證，且 H2 尚未啟動 |
| 管理員 | 課表／校曆／帳號／稽核散在多處 | 已解（原型） | 後台頁與 `flask import-timetable`／`import-calendar`／`backup`／`av-scan`／`purge-attachments` CLI 齊備 |
| 教務處／DPO | 無法快速回答「誰看過、依據什麼、保存多久」 | 部分 | `audit_logs`＋v1.9.0 稽核＋v1.10.0 留存銷毀＋v1.14.0 可驗證備份＋**v1.18.0 單張詳情頁逐筆稽核**；**DPIA、資料分級文件、跨境評估仍缺**（`saas/data.html` §5–§6 為設計，非已交付） |
| 校方 IT | 需要 SSO、監控、備份還原演練、事件響應 | 部分 | v1.8.0 OIDC 通道、v1.14.0 備份還原；**真實 IdP 參數與角色對映、監控告警、事件分級 runbook 仍缺**（`COMMERCIAL.md` §4） |

---

## 3. 資料與合規痛點

來源：`saas/data.html` §5–§7、`saas/prd.html` §10 Privacy safeguard、`GAP-ANALYSIS.md` D 組、
`AI-ETHICS.md` §7。

| ID | 痛點 | 現況 | 依據／下一步 |
| --- | --- | --- | --- |
| D-1 | 附件含病假證明等健康個資，保存期與刪除權 | 已解 | v1.10.0 `purge_expired_attachments()`、`LEAVE_ATTACHMENT_RETENTION_DAYS`、備份區與 `purge_attachment` 稽核 |
| D-2 | 惡意附件落地風險 | 已解 | v1.11.0 `LEAVE_AV_CMD` 掃描、fail-closed、`av_block` 稽核、`flask av-scan` 存量重掃 |
| D-3 | 大範圍 PII 讀取／匯出未留痕 | 已解（原型） | v1.9.0 已覆蓋 `/stats`／`/export`／`/admin/users`；**v1.18.0 補上單張詳情頁逐筆 `view_pii`**，且明細不抄個資（`GAP-ANALYSIS.md` D3）。仍缺 DPIA／資料分級文件 |
| D-4 | 通知偏好與頻率控制 | 已解（原型） | v1.19.0 已交付 `user_prefs`、本人偏好表單、即時／每日／關閉一般 Email，以及可重試的 `flask send-digests`；站內通知與安全郵件邊界不變。**部署仍須 SMTP、每日排程與失敗告警；摘要採至少一次投遞**（`GAP-ANALYSIS.md` D4） |
| D-5 | 備份包本身是高密度個資集合 | 部分 | v1.14.0 有 SHA-256 manifest、完整性驗證、pre-restore 副本、`.gitignore` 排除；**異地存放加密、保存期限、還原演練紀錄與存取稽核**未制度化 |
| D-6 | FERPA／GDPR／PIPL 對照表是設計文件，不是合規證明 | 未解 | `saas/data.html` §6 為 mapping；DPIA／PIA、資料駐留決策、跨境評估見 `COMMERCIAL.md` §8.3、§9 第 3 項，皆未完成 |
| D-7 | 未來 AI 輸入／輸出與備份體系的勾稽 | 未解 | `AI-ETHICS.md` §7 要求新增 AI 資料表須同步納入 `backup.py` 的備份／驗證／還原演練；目前無 AI 資料表，屬前置約束 |
| D-8 | 「同意」可能被當成萬用授權 | 已解（文件立場） | `AI-ETHICS.md` 開頭與 §3 明定個資外送、自動決定、個人風險分、拿學生資料訓練模型不因同意而解禁 |
| D-9 | 代理人範圍口徑不一致（審批 vs 統計／匯出） | 部分（刻意保留） | `role_scope()` 只算本人，`todo_scope()`／`can_view_request()` 含代理人（v1.4.0 遺留）。**v1.18.0 盤點發現但刻意未修**：放寬會讓代理人能匯出委託人全系學生的個資，屬個資範圍擴張，需教務處／DPO 先訂政策。見 `GAP-ANALYSIS.md` D5、T-10 |

---

## 4. 技術與架構痛點

來源：`GAP-ANALYSIS.md` A／C／E 組、`saas/architecture.html`、`saas/schema.sql`、`COMMERCIAL.md` §4。

| ID | 痛點 | 現況 | 影響與既有對照 |
| --- | --- | --- | --- |
| T-1 | **A1：SQLite 單一寫入鎖**，撐不住多用戶生產量級 | 未解 | `GAP-ANALYSIS.md` 建議順序列為 P0 但「暫緩」；`saas/schema.sql` 已有完整 PostgreSQL DDL、`saas/architecture.html` §2 有微服務劃分，**兩者與原型 schema 並未對映驗證** |
| T-2 | **A2：SSO 只有接線通道** | 部分 | v1.8.0 OIDC 授權碼流程、本地帳密災難備援；`README.md` SSO 段明言尚缺 ID-token 簽章驗證、nonce 重放防護、真實 IdP 參數與角色對映 |
| T-3 | **C1：逐生課表匯入通道已備，真實資料源未接** | 部分 | v1.7.0 CSV／CLI／後台匯入與 demo 覆蓋率指標；**SIS／LMS 同步或教務處定期匯出**仍待校方，這也是「課表感知」賣點能否成立的前提 |
| T-4 | **E1：無對外 API／Webhook** | 未解 | `GAP-ANALYSIS.md` E1、`saas/architecture.html` §5 REST API Design 皆為設計；原型僅有人手 `/export`。同時是 `AI-ROADMAP.md` H0 的事件契約前置 |
| T-5 | **E2：i18n** | 未解 | 提案為中英對照、原型介面以中文為主；`GAP-ANALYSIS.md` E2、`AI-ROADMAP.md` H0 前置 |
| T-6 | E3 備份還原的維運邊界 | 已解（原型） | v1.14.0 SQLite 在線快照、manifest、路徑穿越／符號連結拒絕、`--yes`、pre-restore、失敗補償、DB-only 語意、遞迴備份防護；**跨主機還原與定期演練**屬維運流程，非程式缺口 |
| T-7 | 搜尋仍為 `LIKE '%…%'` 全掃 | 部分 | v1.13.0 已參數化並以 `ESCAPE '!'` 防萬用字元濫用；`GAP-ANALYSIS.md` E4 註明資料量再上一級應改 FTS5／`pg_trgm` |
| T-8 | **EW-5「Composite risk score」語意與 AI 紅線衝突** | 已解（文件校準） | v1.19.0 已把 `saas/prd.html` §10 的 EW-5 改為 **Repeated support follow-up**：僅由 EW-1／EW-2／EW-4 任兩項各自達標觸發支持性人工跟進，不加權、不產生個人綜合風險分，也不再使用 GPA 趨勢或遲交資料。`AI-ROADMAP.md` 與提案首頁同步此邊界；EW-1～EW-5 仍屬提案設計，原型尚未交付 |
| T-9 | 企業級 DDL 與原型 schema 無遷移對映 | 未解 | `saas/schema.sql` 是目標態；缺欄位對映、資料遷移腳本與等價性測試，A1 升級時會成為主要工作量 |
| T-10 | 代理人範圍：`role_scope()` 與 `todo_scope()` 口徑不一致 | 部分（刻意保留） | v1.18.0 盤點：代理人待辦看得到、能審、能看詳情，`/stats`／`/export`／全部假單卻看不到同一批單（`GAP-ANALYSIS.md` D5）。**不直接修**：放寬 `role_scope()` 等於授權代理人匯出委託人全系學生個資，須先有校方政策與獨立稽核設計 |

---

## 5. 商業化與交付痛點

來源：`COMMERCIAL.md`、`saas/index.html` §8、`saas/plan.html` §3–§4、`README.md` Commercial。

| ID | 痛點 | 現況 | 說明 |
| --- | --- | --- | --- |
| B-1 | **Readiness gate 未關閉**：A1、A2、C1、SLA／維運 | 未解 | `COMMERCIAL.md` §4 與 `README.md` Commercial 段；缺一項即不應簽生產合約 |
| B-2 | **定價仍為 TBD** | 未解 | 三層授權（Evaluation／Site License／Managed Service）已定義，價格刻意不公開，私有 `PRICING.md` 尚未建立（`COMMERCIAL.md` §3、§9 第 2 項） |
| B-3 | **所有成本與收益數字皆為規劃估算** | 部分 | `saas/index.html` §8 Basis of estimate 已加註（6,000 學生、8.5 FTE、校園 Azure 授權、非實測、不得用於對外報價／合約）；`saas/plan.html` §3 有 Assumptions 與 ±20%。**仍缺**：敏感性區間、試點實測 Proof pack（`COMMERCIAL.md` §8.6 第 2／4 點、§9 第 5 項之後） |
| B-4 | 12 週 MVP／8.5 FTE／¥1.0M 是**上線工程範圍**，不是原型現況 | 部分 | `COMMERCIAL.md` §4 已明示「原型是資產，不是產品」；但 `saas/index.html` §6.4 把原型與提案並列，讀者仍易混淆（見 §7） |
| B-5 | 合約與法務文件未備 | 未解 | IP 書面確認（`COMMERCIAL.md` §8.2、§9 第 1 項，阻塞項）、責任上限／SLA／DPA 範本（§8.4）、開源邊界（§8.5） |
| B-6 | SLA／監控／事件響應流程 | 未解 | `COMMERCIAL.md` §4 列為 Managed Service 的必要條件；`AI-ETHICS.md` §10 的事故分級與復權目前是倫理承諾，尚無對外 SLA 或 runbook |
| B-7 | 試點回退設計尚未入約 | 未解 | `COMMERCIAL.md` §5 建議第 14 週採用率／滿意度未達標即退回現狀；需反映在合約條款 |
| B-8 | 成本口徑不一致 | 漂移 | `saas/index.html` §8 寫 `¥0.45M/yr`，`saas/plan.html` §3.2 寫 `≈ ¥0.5M per year`；後者由月費 `≈ 42,000` 推得（42,000 × 12 = 504,000），兩者為**同一估算的不同精度**，但並列會被審計方抓為矛盾 |

---

## 6. AI 治理與倫理痛點

來源：`AI-ROADMAP.md`、`AI-ETHICS.md`、`saas/plan.html` §5、`saas/monthly-report.html` §5、
`assignment/content.md`。

| ID | 痛點 | 現況 | 說明 |
| --- | --- | --- | --- |
| G-1 | governance 已成形但**工程前置尚未關閉** | 未解 | H0 需要政策版本、去識別化唯讀視圖、golden set、唯讀副本、事件契約、特徵開關；D4 已於 v1.19.0 在原型完成，A1／E1／E2 與其餘 H0 前置仍未完成（`AI-ROADMAP.md` §6） |
| G-2 | 原型**沒有 AI**，H0–H3 全為規劃 | 已解（ boundary 明確） | v1.15.0／v1.16.0 皆為純文件；無模型呼叫、無新依賴、無個資外送。**風險**：外部讀者可能把兩份治理文件誤讀為功能已上線 |
| G-3 | 月報 Phase 3 仍列個人風險分與跨校對標 | 漂移 | `saas/monthly-report.html` §5 列 `Composite risk score (GPA + absence)`、`Predictive dropout risk`、`Cross-institution benchmarking`；與 `AI-ROADMAP.md` 紅線（不做隱性個人風險分、不拿學生資料訓練）及 `AI-ETHICS.md` §7（禁止跨校聯合訓練）衝突 |
| G-4 | 倫理審查的否決權尚無對應組織 | 未解 | `AI-ETHICS.md` §9 要求含學生代表與獨立成員、供應商迴避；目前僅為驗收標準，無具名簽核 |
| G-5 | 復權與事故分級無演練紀錄 | 未解 | `AI-ETHICS.md` §10 定義復權（放回「若無此事件應有」狀態、舉證責任在系統方）；§11 檢核表 18 條目前全部未勾選 |
| G-6 | 採納率的雙重語意 | 已解（文件已校準） | `AI-ROADMAP.md` §5.2 已加註「只作系統層級品質訊號，不得作為個別審批人績效指標」，與 `AI-ETHICS.md` §3／§6 一致 |
| G-7 | 去識別化不等於公平 | 已解（文件立場） | `AI-ETHICS.md` §4 已列代理變數與分群審查；但需 golden set 與偏差評估報告才能驗收（`AI-ROADMAP.md` §5.1） |

---

## 7. 文件矛盾與過時資訊（v1.17.0 校準登錄）

這一節本身就是痛點：同一份專案裡，不同檔案對**版本、測試數、缺口狀態、治理邊界、成本**
給出不同答案。以下每條皆經逐檔抽查確認。

| ID | 矛盾 | 衝突兩端 | v1.17.0 處置 |
| --- | --- | --- | --- |
| M-1 | 原型版本過時 | `COMMERCIAL.md` §4 寫「原型已達 `v1.7.0`」；根 `README.md` 與 `APP_VERSION` 已是 v1.16.0 | 移除寫死的版本號，改為「一律以 `CHANGELOG.md` 最新版與 git tag 為準」，避免每次發布都要回來改又再次脫節 |
| M-2 | D 組狀態互相矛盾 | `COMMERCIAL.md` §4 與根 `README.md` Readiness gate 仍列「D 組個資治理 P1 · 未做」；`leave-system/README.md` 與 `GAP-ANALYSIS.md` v1.11.0 註記寫「D 組三項全部關閉」；`GAP-ANALYSIS.md` D4 仍開放 | 區分 **D1／D2／D3 已關閉** 與 **D4 通知偏好、細粒度 PII 稽核仍開放**；Readiness gate 改列「D 組主體已關閉，殘留 D4 與細粒度稽核」，並在本文 §3 登錄 |
| M-3 | 提案首頁版本與測試數過時 | `saas/index.html` §6.4 寫 `v1.4.0, 115 unit tests + 183 smoke checks`；CI 現況為 241 collected（240 passed、1 skipped）＋247 smoke | 更新 `saas/index.html` §6.4 為當前可查核數字，並加「數字以 `CHANGELOG.md` 與 CI 為準」 |
| M-4 | 月報數字與現況落差 | `saas/monthly-report.html` 頁首與 §2.2 寫 `v1.3.0, 105 unit tests + 183 smoke checks` | **不修改歷史數字**（月報是 2026-09-28→10-28 的歷史快照，改動等於竄改紀錄）；改加「Historical snapshot」註記，標明數字僅代表該報告區間，現況見根 `README.md` |
| M-5 | 月報 Phase 3 與 AI 治理紅線衝突 | `saas/monthly-report.html` §5 的 `Composite risk score`／`Predictive dropout risk`／`Cross-institution benchmarking` vs. `AI-ROADMAP.md` 紅線、`AI-ETHICS.md` §7 | **保留歷史原文**並加「Superseded」邊界註記，指向 `AI-ROADMAP.md` §5.1 與 `AI-ETHICS.md` §4／§7，明確這些條目不得作為後續實作依據 |
| M-6 | 成本精度不一致 | `saas/index.html` §8 `¥0.45M/yr` vs. `saas/plan.html` §3.2 `≈ ¥0.5M per year`（同為 ¥5.0M 三年 TCO、±20%） | 在 `saas/plan.html` §3.2 加註「首頁以 ¥0.45M/yr 呈現，為同一估算的不同精度」，並維持 Basis of estimate 免責 |
| M-7 | `GAP-ANALYSIS.md` 頂部按版本堆疊的歷史註記 | 頂部有 v1.7.0→v1.16.0 多段狀態註記，最新與較舊段落的「E 組剩餘項目」不同 | 保留歷史脈絡，於頂部加 v1.17.0 導讀指向本文 §4；並在 D3／D4 段落補上「與 Readiness gate 用語對照」 |
| M-8 | 「D 組全數關閉」與 D4 並存 | `GAP-ANALYSIS.md` 建議實施順序表寫「D 組全數關閉」，同文件 D4 與 P3 列仍列 D4 待辦 | 改為「D1／D2／D3 已關閉；D4 待辦」，用語與本文一致 |
| M-9 | 根 `README.md` 開頭為超長版本編年史 | 單段從 v0.6.0 寫到 v1.16.0，新讀者難以分辨「現況」與「歷史」 | 不改寫歷史（避免遺失變更脈絡），改在版本行下加導讀，區分歷史編年史／現況／提案／原型／缺口，並指向 `CHANGELOG.md` 與本文 |
| M-10 | 原型說明書自身前後說法不一致 | `leave-system/README.md`「上生產前還要補」第 2 點寫「內容層的惡意程式仍需掃描引擎」，第 5 點卻寫 D2 已落地且「D 組三項全部關閉」；同文件第 6 點又把 D4 列為 H0 前置 | 第 2 點改為「v1.11.0 掃毒通道已落地，引擎本體仍須部署環境安裝配置」；第 5 點改為「D1／D2／D3 已關閉，D4 與細粒度 PII 稽核仍開放」，用語與 M-2 一致（**該第 5 點已於 v1.18.0 再更新為「僅剩 D4」，見 M-11**） |
| M-11 | v1.17.0 的 D 組用語在 v1.18.0 已過時 | M-2／M-8／M-10 與 `GAP-ANALYSIS.md` D4「狀態對照（v1.17.0）」仍寫「殘留 D4 與細粒度 PII 稽核」 | **保留上表歷史處置原文**，於本文檔頭加 v1.18.0 增量註記、D-3 改標 `已解（原型）`、D-4 補「D 組目前僅剩此項」、`GAP-ANALYSIS.md` D4 狀態對照與根 `README.md` Readiness gate 改標 v1.18.0。這正是 §9「每次發布更新登錄表」規則的實作 |

> **表中數字為 v1.17.0 當下的快照**；v1.18.0 當時為 264 collected（263 passed、1 skipped）＋256 smoke；
> v1.19.0 現況為 278 collected（277 passed、1 skipped）＋263 smoke，見 `CHANGELOG.md`。
> **校準原則**：歷史報告只加註、不改數；現況文件（README、COMMERCIAL、提案首頁）才更新數字。
> 這樣既消除誤讀，也不犧牲可追溯性。

---

## 8. 接下來最該處理的事項

依「不確定性高、阻塞後續、可在本機或單一文件內驗證」排序，與既有路線對齊：

1. **C1 真實課表資料源**（T-3）：SIS／LMS 對接或教務處定期匯出的格式與責任窗口。
   這是「課表感知受影響課程」這個核心賣點從裝飾變事實的前提。
2. **A1 PostgreSQL**（T-1／T-9）：先做 `saas/schema.sql` ↔ 原型 schema 的欄位對映與等價性測試，
   再談遷移；目前連對映都缺，直接升級風險最高。
3. **A2 SSO 真實 IdP**（T-2）：校方提供參數與角色對映，補 ID-token 簽章驗證與 nonce 重放防護。
4. **E1 API／Webhook、E2 i18n**（T-4／T-5）：兩者仍是 `AI-ROADMAP.md` H0 之前的工程前置；
   D4 通知偏好已於 v1.19.0 在原型完成。其餘前置未關閉前，任何 AI 升級都應維持 No-Go。
5. **商業化總閘**（B-1～B-6）：Readiness gate、定價、合約範本、敏感性區間與試點 Proof pack；
   在試點數據出現前，`saas/index.html` §8 的估算不得引用於對外報價或第三方商業論證。
6. **D-9／T-10 代理人範圍政策**（v1.18.0 新增）：先由教務處／DPO 決定代理人是否具備
   統計與匯出權、是否僅限委託有效期、是否需獨立稽核動作，再動 `role_scope()`。
   這是**政策決策**，不是可以順手修掉的技術債。

> **不建議現在做的**：任何模型呼叫、個人風險分、跨校對標、以採納率考核審批人，
> 以及**未經政策確認就放寬代理人匯出範圍**。
> 前四項在 `AI-ROADMAP.md` 紅線與 `AI-ETHICS.md` §3／§7 中已明令禁止或須先通過倫理審查。

---

## 9. 防止再次漂移的維護規則

| 規則 | 做法 |
| --- | --- |
| 版本唯一來源 | `leave-system/utils.py` 的 `APP_VERSION`、`CHANGELOG.md`、根 `README.md` 版本行、git tag 四者同步；其他檔案不寫死版本，改指向 `CHANGELOG.md` |
| 測試數唯一來源 | 根 `README.md` 與 `saas/index.html` §6.4 引用 CI 結果；歷史月報的數字視為快照，加註不改動 |
| 缺口狀態唯一來源 | `GAP-ANALYSIS.md` 為工程缺口權威；`COMMERCIAL.md` §4 與根 `README.md` Readiness gate 只做摘要並連結回 `GAP-ANALYSIS.md` 與本文 |
| 治理邊界唯一來源 | `AI-ROADMAP.md`（能不能上線）＋ `AI-ETHICS.md`（該不該做）；提案與月報中的未來功能若與之衝突，一律以「Superseded」註記標明，不逕行刪除歷史 |
| 每次發布 | 更新本文 §7 登錄表，確認沒有新增未校準矛盾；執行繁簡、相對連結、HTML 標籤平衡與版本一致性檢查 |

---

## 10. 依據索引

| 檔案 | 本文引用的部分 |
| --- | --- |
| `saas/index.html` | §1 Problem Statement、§6.4 De-risked、§8 Investment Summary（含 Basis of estimate） |
| `saas/prd.html` | §2 Personas、§3 功能需求、§5 Academic Conflict、§6 Official Activity、§9 Dashboards、§10 Early Warning System（EW-1～EW-5 與 Privacy safeguard）、§15 NFR |
| `saas/architecture.html` | §2 Microservice Responsibilities、§3 Authentication Flow、§4 RBAC、§5 REST API Design、§6 Deployment |
| `saas/data.html` | §5 Security Controls、§6 FERPA／GDPR Compliance Mapping、§7 Document Verification |
| `saas/plan.html` | §3 Team & Cost Model、§4 Risk Register、§5 Future AI Features（§5.1 H0–H3、§5.2 倫理） |
| `saas/monthly-report.html` | 頁首、§2.2 Test coverage、§5 Roadmap Ahead |
| `saas/schema.sql` | PostgreSQL DDL（目標態） |
| `GAP-ANALYSIS.md` | A1／A2、C1、D1–D4、E1–E4、建議實施順序表 |
| `COMMERCIAL.md` | §3 定價、§4 Readiness gate、§5 GTM、§8 風險、§9 執行順序 |
| `AI-ROADMAP.md` | 紅線、§5.1 H0–H3 閘門、§5.2 監控、§5.4 倫理審查、§6 銜接順序 |
| `AI-ETHICS.md` | §2 三特殊性、§3 權利、§4 傷害與代理變數、§6 自動化偏見、§7 資料生命週期、§9 問責、§10 復權、§11 檢核表 |
| `leave-system/README.md`、`README.md`、`CHANGELOG.md` | 原型現況、維運邊界與版本歷程 |
| `assignment/content.md` | surveillance、automation bias、assisted non-digital route 的原始倫理立場 |

---

> **一句話總結**：這個專案的工程底子（`leave-system/`）比它的文件一致性更成熟；
> 真正的痛點已經從「缺功能」轉為「**原型、提案、治理三層說法不一致，且商業化與 AI 上線的
> 前置條件尚未關閉**」。本文把這件事變成可逐項查核、可逐項關閉的登錄表。
