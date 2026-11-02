# Commercial ｜商業化

> 本文件说明 SAAS 的**商业化路径**：卖给谁、卖什么价值、怎么授权、什么时候能卖。
> 原型代码与提案包见 [`README.md`](README.md)；授权条款见 [`LICENSE`](LICENSE)；
> 部署前的功能缺口见 [`GAP-ANALYSIS.md`](GAP-ANALYSIS.md)。
>
> This note sets out how SAAS is commercialised: who buys it, what they are buying,
> how it is licensed, and what has to be true before it can be sold.

---

## 1. 定位 ｜Positioning

一句话：**把「请假审批」从行政杂事升级为「合规证据链」**。

One line: turn absence handling from an administrative chore into **audit-grade evidence**.

学校买的不是表单流程，而是三件事：

1. **Defensibility ｜可举证** — 监管方问「这个学生缺了多少课、谁批的、依据什么」时，能在几秒内回答。
2. **Consistency ｜一致性** — 跨院系、跨审批人用同一套规则，不再靠人记得。
3. **Continuity ｜学业不中断** — 缺课自动生成学业便利方案（accommodation），而不是事后补救。

## 2. 目标客户 ｜Who buys

| 层级 | 客群 | 采购动机 |
| --- | --- | --- |
| **Beachhead ｜滩头** | 中外合作大学、有国际生／赞助生（sponsored students）的院校 | 签证与合规义务刚性最强，预算也最明确 |
| **Expansion ｜扩张** | 任何需向监管方证明出勤管理的院校 | 效率与风险并重 |
| **Long tail ｜长尾** | 国际学校、海外校区、继续教育机构 | 流程标准化 |

**为什么从合规切入**：面向国际的院校面对的是义务而非便利——英国 UKVI 学生担保人责任
（UK Visas and Immigration, 2024）、澳洲 ESOS/ELICOS 80% 出勤义务（Australian Government,
2018）、美国 FERPA 对学业记录处理的期望（U.S. Department of Education, 20 U.S.C. §1232g），
都要求院校能举证请假与学业便利如何处置。SAAS 把这项义务变成一次查询，而不是一次救火。
这把买家从「锦上添花的效率工具」重新定义为「监管可辩护性」——销售周期更短，可寻址市场
也从顶尖研究型大学扩大到任何有国际生的院校。

来源：上述论证见 [`assignment/content.md`](assignment/content.md)；成本与效益估算见
提案 [`saas/index.html`](saas/index.html) §6.1 与 §8（提案自估，非成交数据）。

## 3. 授权分层 ｜Licensing tiers

| 层级 | 范围 | 状态 |
| --- | --- | --- |
| **Evaluation ／ Personal** | 个人、教学、内部评估；不得部署生产环境、不得对第三方提供服务 | **免费**（`LICENSE` §2(a)） |
| **Institutional Site License** | 单一院校／校区生产部署（自托管，校方 IT 运维） | 商业授权，价格 **TBD** |
| **Managed Service** | 作者方托管与运维（含升级、备份、SLA） | 商业授权，价格 **TBD** |

定价原则（尚未定价，需作者确认）：

- 锚定**可避免成本**与学生规模，而非开发成本；
- 分层随规模与合规强度递增（是否需要 UKVI／ESOS 报表、是否需 SSO、是否需托管）；
- 试点期（pilot）单列条款：提案中「未达采用率门槛可退回现状」的回退设计应反映在合同里。

> ⚠️ 本文件**不设具体价格**。定价、折扣、付款条件与合约范本需由作者确认后另立
> `PRICING.md`（私有）。对外报价前请先补上。

## 4. 商业化就绪度 ｜Readiness gate

原型已达 `v1.5.0`，功能面足以演示与试点；但**可对外销售**前须关闭下列
`GAP-ANALYSIS.md` 中的缺口（缺一项就不应签生产合同）：

| 缺口 | 说明 | 状态 |
| --- | --- | --- |
| **A2 SSO** | 校内单点登录（Entra ID / OIDC）；企业采购的硬门槛 | P0 · 未做 |
| **A1 PostgreSQL** | SQLite 不适合多用户生产部署 | P0 · 未做 |
| **C1 课表真实化** | 课表须由 SIS／LMS 同步，而非手工维护 | P0 · 未做 |
| **D 组个资治理** | 数据分级、保存期、删除权、稽核导出（对 FERPA／GDPR／个保法） | P1 · 未做 |
| **SLA 与运维** | 若卖 Managed Service，需监控、备份、事件响应流程 | 未做 |

对照：`saas/index.html` 提案的 12 周 MVP、8.5 FTE、¥1.0M 开发预算即以上线的工程范围估算，
与原型当前状态是两件事——**原型是资产，不是产品**。

## 5. 上市路径 ｜Go-to-market

1. **Pilot（1 个学年，2 个项目，约 600 名学生）** — 与现行邮件流程并行，设明确回退条件；
   门槛建议沿用提案：第 14 周采用率与满意度未达标即退回现状。
2. **Proof pack** — 把试点数据做成合规证据包（审批 SLA、缺课检出率、举证查询耗时），
   这是后续销售的主要素材。
3. **Institutional rollout** — 教務处主导，扩建至全校；此时补 SSO 与 PostgreSQL。
4. **Replication** — 以「同类院校合规样板」复制到兄弟院校／合作校区。

## 6. 商业化指标 ｜Metrics that matter

| 指标 | 用途 |
| --- | --- |
| 试点采用率（学生／教师） | 定价与续约的底线 |
| 审批 SLA 达成率 | 服务承诺（Managed Service 级别） |
| 缺课检出／预警数 | 价值证明：从「事后发现」变「事前预警」 |
| 监管查询响应时间 | 合规卖点的量化证据（从数天 → 数秒） |

## 7. 风险与待决事项 ｜Risks & open decisions

| # | 事项 | 说明 |
| --- | --- | --- |
| 1 | **定价** | 尚未设定；须由作者决定后再对外报价 |
| 2 | **智慧财产归属** | 作者任职于 HKUST(GZ)；若原型在校内资源／职务范围内产出，学校可能主张权利——商用前须釐清 |
| 3 | **数据驻留** | 中国境内院校涉及数据出境与个保法；托管方案须明确区域与责任划分 |
| 4 | **责任上限与 SLA** | 生产合同需赔偿上限、可用性承诺、事件通报时限 |
| 5 | **开源边界** | 是否开源部分模组（如校历汇入）以换采用度，须与商业授权策略一并决定 |
| 6 | **提案数字的可引用性** | `saas/index.html` 的成本／效益为提案估算，对外引用须标注为估算 |

## 8. 联络 ｜Contact

商业授权与合作洽谈：Johnny, Jeng-lin Li (HKUST(GZ)) — jli642@connect.hkust-gz.edu.cn

---

_Drafted 2026-11-02 · 随原型 v1.5.1 纳入版本库。定价与合约条款待作者确认后另立私有文件。_
