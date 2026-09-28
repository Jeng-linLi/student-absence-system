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

The payer is the university, not the student — the Academic Registry and IT office buying an annual licence sized by student population. Students and instructors are users who pay in time.

The value is that it makes an unmeasured cost measurable. Instructor hours on absence email, administrative effort on disputes, and the compliance risk of holding no record are invisible today; SAAS exposes and reduces them. It also produces what no alternative does: an auditable, consistent decision history.

A generic form tool cannot see the timetable; a learning-management attendance module records absence but cannot distribute an accommodation decision; building in-house hard-codes one university's policy. SAAS's differentiator is the policy-configuration layer plus portability — one product serving HKUST(GZ), HKUST, CUHK(SZ) and other institutions whose thresholds differ.

Needed complements: the campus identity provider, student-information and LMS vendors for calendar feeds, Microsoft for delivery, and — hardest of all — cross-campus policy owners willing to harmonise enough for configuration to be reusable.

## 5. Social Impact and Risks

The main positive impact is equity. Today the outcome depends on which instructor a student happens to ask and how persuasive their email is; a policy-driven process replaces discretion with consistent rules, benefiting those least able to advocate for themselves. Students with recurring needs — chronic illness, caregiving, visa appointments, athletes — gain most, because the system carries context instead of forcing them to retell a private story. Early-warning rules can also route a struggling student to support before failure appears in grades.

The gains are uneven: well-connected, confident students adopt earliest, while occasional users may find a formal process heavier than an email. Without an assisted, non-digital route, the system could widen the gap it intends to close.

Negative externalities centre on data. A complete absence record is also a surveillance instrument: risk scoring may stigmatise a student with a chronic condition, and medical documents are among the most sensitive records a university holds. Automation bias may lead instructors to ratify whatever the queue presents; administrative staff shift from email triage to case management. Mitigations are design decisions: instructors never see medical reasons or certificates, every decision stays human, retention periods are fixed in advance, and AI — when it arrives — is advisory, logged and refusable in favour of human-only handling.

## Final Reflection

Yes, but narrowed. The problem is real and bounded, the complements already exist on campus, and its hardest questions are organisational rather than technical — a genuine test of collaborative innovation. The full system is too large for one term, so I would scope the team project to the assessment-conflict workflow, where both the value and the privacy tension are sharpest.

## References

- European Union. (2016). *General Data Protection Regulation* (Regulation (EU) 2016/679).
- Foster, R. (1986). *Innovation: The Attacker's Advantage*. New York: Summit Books.
- Microsoft. (2025). *Microsoft Entra ID documentation*. https://learn.microsoft.com/entra/identity/
- Teece, D. J. (1986). Profiting from technological innovation. *Research Policy*, 15(6), 285–305.
- U.S. Department of Education. *Family Educational Rights and Privacy Act (FERPA)*, 20 U.S.C. §1232g; 34 CFR Part 99.
- Utterback, J. M., & Abernathy, W. J. (1975). A dynamic model of process and product innovation. *Omega*, 3(6), 639–656.
