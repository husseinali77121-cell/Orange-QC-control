# Orange Lab — BK-280 QC / Westgard Decision Support Program

A standalone Streamlit app for running **internal QC** on the BIOBASE BK-280
chemistry analyzer: enter control results, get an instant Westgard multirule
verdict (status / rule / interpretation / recommended action), track a
Levey-Jennings chart per test/level, keep a full audit history, and print a
signed-off PDF summary.

Built to slot into the same deployment workflow as the other Orange Lab
apps (HVMS, Send-Out System): Streamlit + GitHub Contents API persistence,
deployed on Streamlit Community Cloud.

## خلاصة سريعة (TL;DR بالعربي)

1. تعمل `Test Setup` مرة واحدة: تدخل اسم التحليل، الوحدة، وكل مستوى كنترول
   (Level 1/2/3) بالـ Mean/SD/Lot بتاعته.
2. كل يوم تدخل نتيجة الكنترول في `QC Entry` — البرنامج يحسب Z-score فورًا
   ويطلعلك: **Status** (Accept / Warning / Reject) + **اسم القاعدة**
   (Westgard rule) + **تفسير** + **الإجراء المطلوب**.
3. `Levey-Jennings` بيرسم النتائج على مخطط مع حدود ±1/2/3 SD.
4. `QC History` فيه سجل كل الـ runs + مكان تكتب فيه سبب الرفض والإجراء
   التصحيحي (CAPA) لو حصل Reject.
5. `Reports` يطلعلك PDF جاهز للطباعة والتوقيع (فني + مدير المعمل).

---

## 1. Architecture

```
orange_qc_control/
├── app.py                          # login + home dashboard
├── pages/
│   ├── 1_⚙️_Test_Setup.py           # define tests & control levels (mean/SD/lot, versioned)
│   ├── 2_📝_QC_Entry.py             # enter results -> instant Westgard verdict
│   ├── 3_📊_Levey_Jennings.py       # interactive chart with rule annotations
│   ├── 4_📋_QC_History.py           # audit log + CAPA notes
│   └── 5_🖨️_Reports.py              # PDF summary report
├── core/
│   ├── models.py                   # TestDefinition / ControlLevel / LevelVersion / QCPoint / QCRecord
│   ├── westgard_engine.py          # pure-logic rule engine (no Streamlit/IO — unit testable)
│   ├── data_manager.py             # GitHub Contents API persistence + local-mode fallback
│   ├── auth.py                     # operator name + branch + shared QC password
│   ├── charts.py                   # Levey-Jennings: Plotly (interactive) + Matplotlib (for PDF)
│   └── pdf_report.py               # fpdf2-based printable summary
├── tests/
│   └── test_westgard_engine.py     # 12 unit tests, all passing — run with `python -m pytest tests/`
├── assets/                         # drop Orange_Logo_transparent.png here for the PDF header
├── data/                           # local-mode JSON storage (gitignored)
├── .streamlit/secrets.toml.example
├── requirements.txt
└── .gitignore
```

## 2. The Westgard rule engine — how it actually decides

Unlike a simple "one point → one rule" lookup, `core/westgard_engine.py`
distinguishes **within-run** rules (comparing two control levels run
together) from **across-run** rules (comparing a level's history over time):

| Rule | Scope | Status | Meaning |
|---|---|---|---|
| 1-2s | across-run | Warning (trigger only) | One control beyond ±2SD — check the other rules before deciding |
| 1-3s | across-run | Reject | One control beyond ±3SD — random error |
| 2-2s (within-run) | within-run | Reject | Both levels in the same run beyond ±2SD, same side — systematic error |
| 2-2s (across-run) | across-run | Reject | Same level beyond ±2SD, same side, two runs in a row — systematic error |
| R-4s | within-run | Reject | Range between two levels in the same run ≥4SD — random error |
| 4-1s | across-run | Reject | Same level beyond ±1SD, same side, 4 runs in a row — systematic error |
| 10x̄ | across-run | Reject | Same level on the same side of the mean for 10 runs in a row — systematic error |
| 8x̄ / 9x̄ / 12x̄ / 7T | across-run | Reject (optional) | Extended multirule — off by default, toggle per your QC SOP |

Every violation returned by the engine carries `rule_name`, `status`,
`error_type` (random / systematic / trend), `interpretation`, and
`action` — that's what QC Entry prints, so nobody has to memorize what
"2-2s" means at 7am.

**Lot changes are handled correctly.** Each control level keeps a
*history* of mean/SD "versions" tied to an `effective_from` date. A QC
result entered under the old lot is always scored against the old
mean/SD — changing to a new lot on Test Setup does not silently rewrite
the Z-scores (and therefore the rule verdicts) of past runs.

Validated with 12 unit tests in `tests/test_westgard_engine.py`
(in-control, 1-2s, 1-3s, 2-2s within/across-run, R-4s, 4-1s, 10x, extended
rules on/off, 7T trend, and level-isolation) — all passing.

## 3. Running it

```bash
pip install -r requirements.txt
streamlit run app.py
```

If `.streamlit/secrets.toml` doesn't exist yet, the app runs in
**local mode** and writes JSON under `./data/` — good for testing on
Termux before you connect it to a real data repo.

## 4. Deploying like your other apps (GitHub + Streamlit Cloud)

1. Create a **private** data repo (or reuse a private repo, same pattern as
   the HVMS data repo) and generate a fine-grained PAT scoped to
   **Contents: Read and write** on that repo only.
2. Copy `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml`,
   fill in `github_token`, `github_repo`, `github_branch`,
   `qc_access_password`, `branches`. **Do not commit this file** (already
   gitignored).
3. Push the code repo to GitHub (private, same as your other code repos):
   ```bash
   cd orange_qc_control
   git init
   git add .
   git commit -m "Initial commit: BK-280 QC / Westgard program"
   git branch -M main
   git remote add origin https://github.com/<you>/orange-lab-qc.git
   git push -u origin main
   ```
4. On Streamlit Community Cloud: New app → point at the repo/branch/`app.py`
   → paste the same secrets into the app's Secrets manager (not the repo).

## 5. Ideas for future development (not built yet — flagging for you & Dr. Tarek)

- **Sigma-metrics based rule selection (Westgard "Sigma Rules").** Right
  now every test uses the same fixed multirule set. The more modern,
  evidence-based approach calculates a Sigma-metric per test (from bias +
  imprecision + TEa) and *selects* which rules/N apply per analyte — tight
  rules for low-Sigma tests, relaxed rules (fewer false rejects) for
  high-Sigma tests. Worth adding once you have enough bias/TEa data per
  analyte.
- **Non-conformance / CAPA integration with a real workflow** (status:
  open → investigating → closed, assigned owner, due date) instead of a
  free-text note — mirrors what you already do with controlled documents.
- **QC-gate on report release.** The same way Culture Analyzer gates PDF
  release on dose-band approval, HVMS/reporting tools could check
  "is today's QC for this analyte currently REJECTed?" before allowing a
  patient report to print — a real patient-safety interlock.
- **Due/missed QC reminders**, e.g. wiring into the same alarm mechanism
  you use elsewhere, so a branch that hasn't logged today's Level 1/2 by a
  cut-off time gets flagged.
- **Multi-branch comparison view** — same test/level, La Cité vs Diamond,
  to catch a branch-specific instrument drift the single-branch view
  would miss.
- **Peer-group / EQA import** — a place to log external quality assessment
  (EQA/PT) results alongside internal QC, since both feed into the same
  Sigma-metric story above.
- **Arabic branding on the PDF.** You've already solved Arabic shaping/bidi
  rendering for the semen-analysis worksheet and the price-list invoices —
  that same font/reshaping approach can be ported into `pdf_report.py` if
  you want the tagline "للتحاليل الطبية" on the QC report header too.

## 6. Governance note

Like your other clinical software, the *rule catalogue* in
`core/westgard_engine.py::RULE_INFO` (which rule → which action) is a
clinical/QA decision, not just a coding one — recommend Dr. Tarek reviews
and signs off on the exact rule set and reject/warning thresholds before
this goes live for patient-affecting QC decisions, the same way he
countersigns guideline citations and dose bands elsewhere.
