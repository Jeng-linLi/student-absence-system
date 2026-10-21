# SAAS: A Student Absence and Academic Accommodation System

## 1. Problem / Opportunity

In most universities, requesting a short absence is still an email exercise. A student who must miss two days emails every instructor separately, attaches the same medical certificate several times, and then negotiates, one message at a time, what will happen to the quiz, laboratory or workshop they will miss. Each instructor improvises a reply; the programme office sees nothing; when a dispute arises, the Registry has no record of what was agreed.

Three groups are affected at once. Students face unpredictable outcomes and must repeatedly disclose private reasons to people they barely know, while instructors decide without context — unable to see that the same student has already missed four sessions this term. Programme offices and the Registry have no visibility, no audit trail, and no way to notice a struggling student before the damage reaches their grades.

It matters because an absence decision is an academic decision: an unmanaged conflict becomes a missed assessment, a disputed grade, or a student who quietly disengages. Existing tools do not help — generic form builders know nothing about timetables, learning-management systems record attendance but cannot distribute an accommodation decision, and email creates no state anyone can audit.

## 2. Proposed Innovation

SAAS is a single web and mobile platform in which one submission replaces the email ritual.

A student selects a category (medical, official activity, conference, family emergency, visa, bereavement, personal…), enters dates and a reason, and uploads evidence where required. The system resolves their enrolments against the assessment calendar and creates one conflict record per affected quiz, midterm, final assessment, workshop or laboratory. It then routes the request by policy: medical leave goes to the affected instructors; a student on an endorsed activity roster is verified automatically and instructors are merely informed; any absence longer than seven days passes through the programme office and the Registry first. Each instructor then selects one of five accommodations — excused absence, make-up assessment, alternative assignment, weight redistribution, or rejection with reasons.

The technologies are mature — cloud hosting, campus single sign-on, a rule-based workflow engine, document storage, and integrations with the student information system, LMS, email and Teams. What is new is the information design. The unit of work is not the request but its consequence: every affected assessment is resolved explicitly. Policy parameters live in Registry-owned configuration, so rules change without a release. And privacy is structural: instructors receive the accommodation to apply, never the medical reason or certificate behind it.

## 3. Application of Course Concepts

**Complements.** Teece (1986) argues that an innovator captures little value when the complementary assets it needs belong to others. SAAS is an extreme case: its value is almost entirely co-produced. Without an accurate assessment calendar from the student information system, conflict detection is guesswork; without campus single sign-on, adoption collapses; without email and Teams delivery, decisions stall in a queue nobody opens. The hard work is therefore not writing the application but securing the complements.

**S-curve.** Rule-based workflow automation sits on the flat, mature part of its performance curve (Utterback & Abernathy, 1975; Foster, 1986), so SAAS cannot win by being a better form. It matters only by starting a new curve: joining academic-calendar data to a privacy-aware approval workflow. The same logic explains a deliberate omission — automatic triage of free-text reasons and certificate-authenticity checks sit on the steep part of the language-model curve, where reliability is still improving, and a wrong automated decision in a high-stakes academic process is unacceptable. They wait.

**Externalities.** One student filing one request generates structured absence data nobody paid for — a positive externality, giving advisors and the Registry visibility they could not otherwise buy. The same data is also a potential surveillance instrument, the negative externality taken up below.

## 4. Business Value

**Business model.** The payer is the university, not the student — the Academic Registry and IT office buy the platform, while students and instructors pay only in time. The shape is a classic subscription SaaS with three explicit revenue lines: (1) a recurring annual licence sized by enrolled population; (2) a one-off implementation fee for connecting campus SSO, the student-information system and the LMS; and (3) ongoing configuration support as policy evolves. Pricing by headcount, not by feature, aligns vendor incentives with adoption and retention rather than with bespoke per-university builds.

**What value it creates.** The core promise is to make an invisible cost visible and then reduce it. Three cost lines disappear from the manual process: (1) instructor hours spent on absence email and ad-hoc negotiation; (2) administrative effort in the programme office and Registry on disputes, duplicate records and manual audits; (3) the compliance risk of holding no defensible record of what was agreed. No off-the-shelf alternative produces the last of these — an auditable, consistent decision history that survives staff turnover and external scrutiny.

**A quantified lens (indicative).** The argument is easiest to defend with an order-of-magnitude model rather than precise figures. A research-intensive university with, say, 10,000 students and 800 instructors, where each instructor handles a handful of absence negotiations per term, plausibly absorbs several thousand hours of fragmented coordination a year; even a 30–50% reduction is material against academic time otherwise spent on teaching and research. The compliance line is not a productivity gain but a liability avoided — a single disputed grade or visa-attendance finding can cost more than the annual licence. These numbers are illustrative, intended to show scale, not to be quoted as measured results.

**Defensible moat (configuration, not code).** A generic form tool cannot see the timetable; an LMS attendance module records absence but cannot distribute an accommodation decision; an in-house build hard-codes one university's policy. SAAS's moat is the policy-configuration layer plus portability: the same product serves HKUST(GZ), HKUST, CUHK(SZ) and other institutions whose thresholds, routing and retention rules differ — and the configuration, not the code, carries the value (Teece, 1986). The encoded policy also becomes the switching cost: once a university commits its absence and accommodation rules to the platform, replicating them elsewhere is expensive, which protects recurring revenue and makes the configuration layer the durable competitive asset.

**Compliance as a commercial driver, not a footnote.** For internationally-facing institutions the system is less a convenience than a control. UKVI student-sponsor duties (UK Visas and Immigration, 2024), Australia's ESOS/ELICOS 80% attendance obligation (Australian Government, 2018) and US FERPA expectations (U.S. Department of Education, 20 U.S.C. §1232g) all require institutions to evidence how absence and academic accommodation are handled; SAAS turns that obligation into a query instead of a fire drill. This reframes the buyer from "nice-to-have efficiency" to "regulatory defensibility," which shortens sales cycles and widens the addressable market beyond elite research universities to any institution with sponsored or international students.

**Needed complements.** Campus identity provider, SIS/LMS vendors for calendar feeds, Microsoft for delivery, and — hardest of all — cross-campus policy owners willing to harmonise enough for configuration to be reusable. Securing these, not writing features, is the real work.

## 5. Social Impact and Risks

**Positive impact — equity by structure.** The main gain is that outcomes stop depending on which instructor a student happens to ask and how persuasive their email is. A policy-driven process replaces discretion with consistent, documented rules, which benefits those least able to advocate for themselves. Students with recurring or sensitive needs — chronic illness, caregiving responsibilities, visa appointments, elite athletes — gain most, because the system carries context across requests instead of forcing a private retelling each time. Early-warning rules can also route a struggling student to support before failure appears in grades, turning an administrative record into a welfare signal.

**Positive impact — protecting the internationally mobile.** A second, often-overlooked social benefit follows directly from the compliance driver above: international students on visas face attendance and engagement duties — UKVI sponsor monitoring in the UK, Australia's ESOS 80% attendance rule — where an unmanaged absence can escalate to a visa risk. By recording accommodations consistently and surfacing date-unavoidable absences (visa appointments, official travel), SAAS reduces the chance that a legitimate absence becomes a compliance breach: a concrete protective effect for a vulnerable, often linguistically and procedurally disadvantaged population.

**Risks.** The gains are uneven. Well-connected, confident students adopt earliest and navigate any residual friction; occasional or less-digital users may find a formal process heavier than an email, so without an assisted, non-digital route the system could widen the gap it intends to close. A complete absence record is also a potential surveillance instrument: risk scoring could stigmatise a student with a chronic condition, and medical documents are among the most sensitive records a university holds. Automation bias may lead instructors to ratify whatever the queue presents; and administrative staff shift from email triage to case management — a change in required skills, not simply less work.

**Mitigations are design decisions, not add-ons.** Instructors never see medical reasons or certificates; every accommodation decision stays human-in-the-loop; retention periods are fixed in advance and audited; and any future AI is advisory, logged and explicitly refusable in favour of human-only handling. Equity is protected structurally too: the assisted route, a multilingual interface, and the rule that date-unavoidable absences cannot be rejected on grounds of date all stop the system from penalising its least empowered user.

## Final Reflection

Yes, but narrowed. The problem is real and bounded, the complements already exist on campus, and its hardest questions are organisational rather than technical — a genuine test of collaborative innovation. The full system is too large for one term, so I would scope the team project to the assessment-conflict workflow, where both the value and the privacy tension are sharpest.

## References

- European Union. (2016). *General Data Protection Regulation* (Regulation (EU) 2016/679).
- Foster, R. (1986). *Innovation: The Attacker's Advantage*. New York: Summit Books.
- Microsoft. (2025). *Microsoft Entra ID documentation*. https://learn.microsoft.com/entra/identity/
- Teece, D. J. (1986). Profiting from technological innovation. *Research Policy*, 15(6), 285–305.
- U.S. Department of Education. *Family Educational Rights and Privacy Act (FERPA)*, 20 U.S.C. §1232g; 34 CFR Part 99.
- Utterback, J. M., & Abernathy, W. J. (1975). A dynamic model of process and product innovation. *Omega*, 3(6), 639–656.
- Australian Government, Department of Education. (2018). *National Code of Practice for Providers of Education and Training to Overseas Students 2018* (including the National ELICOS Standards). https://www.education.gov.au/
- UK Visas and Immigration. (2024). *Sponsor guidance: Student sponsors — compliance and reporting duties*. Home Office. https://www.gov.uk/
