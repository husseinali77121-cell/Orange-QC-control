# Orange Lab — BK-280 QC / Westgard Decision Support Program

A standalone Streamlit app for running **internal QC** on the BIOBASE BK-280
chemistry analyzer: enter control results, get an instant Westgard multirule
verdict (status / rule / interpretation / recommended action), track a
Levey-Jennings chart per test/level, keep a full audit history with a
structured investigation workflow, and print a signed-off PDF summary.

Built to slot into the same deployment workflow as the other Orange Lab
apps (HVMS, Send-Out System): Streamlit + GitHub Contents API persistence,
deployed on Streamlit Community Cloud.

## خلاصة سريعة (TL;DR بالعربي)

1. تعمل `Test Setup` مرة واحدة (محتاج admin password منفصل): تدخل اسم
   التحليل، الوحدة، وكل مستوى كنترول (Level 1/2/3) بالـ Mean/SD/Lot بتاعته.
2. كل يوم تدخل نتيجة الكنترول في `QC Entry` — البرنامج يحسب Z-score فورًا
   ويطلعلك: **Status** (Accept / Warning / Reject) + **اسم القاعدة**
   (Westgard rule) + **تفسير** + **الإجراء المطلوب**. نفس القرار ده هو
   اللي هيتحفظ ويتعرض في كل مكان تاني (مفيش إعادة حساب تختلف النتيجة).
3. `Levey-Jennings` بيرسم النتائج، وفيه وضع Z-score موحّد آمن حتى لو
   اتغير الـ lot في نص المدة.
4. `QC History` فيه سجل كل الـ runs + investigation form كامل (root
   cause / corrective / preventive action) لو حصل Reject، وبيسجل كل
   تعديل بدل ما يمسح القديم.
5. `Reports` يطلعلك PDF جاهز للطباعة والتوقيع (فني + مدير المعمل).

---

## 0. Changelog — v1.1 (post-review fixes)

A full code review found several real bugs in v1.0. All of the following
are fixed and covered by tests where the sandbox allowed it (no network
access here to install streamlit/fpdf2/plotly, so those three stayed at
compile-check level — please run `streamlit run app.py` once on your end
before trusting it with real QC data):

| # | Issue found | Fix |
|---|---|---|
| P0 | PDF generation crashed on the em-dash and on subscript rule names (`Helvetica` core font is Latin-1 only) | Embedded DejaVu Sans (bundled in `assets/fonts/`, pulled from matplotlib's own files — no network needed) as a real Unicode font, plus a defensive sanitizer (`core/text_sanitize.py`) as a second safety net. 8 new tests lock this in. |
| P0 | QC Entry could say REJECT while Levey-Jennings/Reports showed WARNING for the same run, because those pages re-ran the engine one level at a time | Those pages no longer call the engine at all — they read the `overall_status` / `violated_rules` that were persisted at entry time. One computation, one source of truth, everywhere. |
| P0 | QC history fed to the engine wasn't filtered by branch — La Cité and Diamond results could be treated as one continuous series | `data_manager.load_qc_history_for_level()` now filters by branch (and level) explicitly. |
| P1 | Fixed "last 3 calendar months" history window was too short for 10x/12x/7T on a low-frequency test | Replaced with a backward search that keeps pulling months until it has enough points (or hits a sane cap) — see `load_qc_history_for_level`. |
| P1 | `extended_rules` on/off wasn't stored per record, so a historical verdict couldn't be audited later | `QCRecord` now stores `extended_rules_enabled` and `engine_version` at entry time. |
| P1 | `active_version()` could silently return a *future* lot's mean/SD if nothing was effective yet | Now returns `None` (no valid configuration) — QC Entry surfaces this as a clear warning instead of scoring against the wrong reference. |
| P1 | Duplicate QC submissions (e.g. double-click Save) weren't blocked | `data_manager.find_existing_record()` checks branch+test+level+date+run before saving; duplicates are rejected with a clear message. |
| — | CAPA was a single free-text box, overwritten with no trace | Replaced with a structured investigation record (status, responsible person, immediate/root cause/corrective/preventive action, recheck QC, opened/closed by+when) — see `pages/4`. |
| — | CAPA edits silently overwrote the previous value | `update_qc_record()` now appends an `audit_events` entry (who/when/field/old→new) for every changed field, shown in QC History. |
| — | Dashboard counted per-level *records*, not *runs* — a 2-level rejected run showed as "2 rejected" | `app.py` now groups records back into runs by (test, date, run_number) before counting anything, and adds a "QC Health by Test" overview. |
| — | Levey-Jennings/Reports plotted raw results across a lot change against one (wrong) set of bands | Added a **standardized Z-score chart mode** (fixed ±1/2/3 bands, always lot-correct) — default, with raw-units still available for single-lot ranges. |
| — | Any technologist could change Mean/SD/lot from the same login used for daily entry | Added `core.auth.require_admin()` — a separate password gate on `Test Setup` (optional; skipped if `qc_admin_password` isn't set). |
| — | Lot setup accepted overlapping/duplicate effective dates and expiry-before-effective dates | Both are now validated and rejected on the "new lot" form. |

**Not done in this pass** (flagged, not silently dropped — see §5): full
username/role-based auth, Sigma-metrics rule selection, the QC-gate
integration with report release, multi-branch comparison dashboard, EQA/PT
logging. These are genuinely separate features, not bug fixes, and are
scoped out on purpose rather than rushed.

---

## 1. Architecture

```
orange_qc_control/
├── app.py                          # login + home dashboard (run-level metrics, QC health by test)
├── pages/
│   ├── 1_⚙️_Test_Setup.py           # define tests & control levels (admin-gated, versioned lots)
│   ├── 2_📝_QC_Entry.py             # enter results -> instant Westgard verdict (source of truth)
│   ├── 3_📊_Levey_Jennings.py       # chart from SAVED verdicts, standardized Z-score option
│   ├── 4_📋_QC_History.py           # audit log + structured CAPA + change history
│   └── 5_🖨️_Reports.py              # PDF summary report, from SAVED verdicts
├── core/
│   ├── models.py                   # TestDefinition / ControlLevel / LevelVersion / QCPoint / QCRecord
│   ├── westgard_engine.py          # pure-logic rule engine (no Streamlit/IO — unit testable)
│   ├── data_manager.py             # GitHub Contents API persistence + local-mode fallback
│   ├── date_utils.py               # pure month-range helpers (unit tested)
│   ├── text_sanitize.py            # pure PDF/CSV text-safety helpers (unit tested)
│   ├── auth.py                     # operator/branch login + separate admin gate for Test Setup
│   ├── charts.py                   # Levey-Jennings: raw-unit + standardized Z-score, Plotly + Matplotlib
│   └── pdf_report.py                # fpdf2 report, embeds a Unicode font (DejaVu Sans)
├── tests/
│   ├── test_westgard_engine.py     # 14 tests — rule logic
│   ├── test_text_sanitize.py       # 8 tests — PDF/Unicode safety (the P0 bug's regression tests)
│   └── test_date_utils.py          # 7 tests — month-range math
├── assets/
│   ├── fonts/DejaVuSans.ttf, DejaVuSans-Bold.ttf   # bundled, no network needed
│   └── (drop Orange_Logo_transparent.png here for the PDF header)
├── data/                           # local-mode JSON storage (gitignored)
├── .streamlit/secrets.toml.example
├── requirements.txt
└── .gitignore
```

**Data-flow rule that fixes the P0 consistency bug:** `QC Entry` is the
*only* place that calls `evaluate_run()` and decides a verdict. Every
other page (`Levey-Jennings`, `QC History`, `Reports`, `app.py`) reads
`overall_status` / `violated_rules` back from the saved record. If you
add a new display of QC data later, follow the same rule — never
re-derive a verdict for display.

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
"2-2s" means at 7am. Each rule also has a `name_ascii` fallback used by
the PDF/CSV path, and `ENGINE_VERSION` is stamped on every saved record.

**Lot changes are handled correctly.** Each control level keeps a
*history* of mean/SD "versions" tied to an `effective_from` date. A QC
result entered under the old lot is always scored against the old
mean/SD — changing to a new lot on Test Setup does not silently rewrite
the Z-scores (and therefore the rule verdicts) of past runs.
`active_version()` returns `None` (not a wrong future lot) if nothing was
effective yet on the requested date.

Validated with **29 unit tests** across three files (`tests/`), all
passing in this sandbox (pure-logic tests only — see §6 on what still
needs a live run).

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
   `qc_access_password`, `qc_admin_password`, `branches`. **Do not commit
   this file** (already gitignored).
3. Push the code repo to GitHub (private, same as your other code repos):
   ```bash
   cd orange_qc_control
   git init
   git add .
   git commit -m "Initial commit: BK-280 QC / Westgard program (v1.1)"
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
- **QC-gate on report release.** The same way Culture Analyzer gates PDF
  release on dose-band approval, HVMS/reporting tools could check
  "is today's QC for this analyte currently REJECTed?" before allowing a
  patient report to print — a real patient-safety interlock. This would be
  a full workflow: Enter QC → Evaluate → (In control → release allowed) or
  (Reject → results locked → investigation → corrective action → repeat QC
  → release).
- **Full username/role-based auth** (Technologist / Supervisor / Lab
  Director) instead of the current operator-name + shared-password model —
  the current admin-password gate on Test Setup is a proportionate interim
  step, not the end state.
- **Due/missed QC reminders**, e.g. wiring into the same alarm mechanism
  you use elsewhere, so a branch that hasn't logged today's Level 1/2 by a
  cut-off time gets flagged.
- **Multi-branch analytics dashboard** — same test/level, La Cité vs
  Diamond, comparing mean/CV/drift/rejection rate side by side to catch a
  branch-specific instrument problem the single-branch view would miss.
- **Peer-group / EQA import** — a place to log external quality assessment
  (EQA/PT) results alongside internal QC, since both feed into the same
  Sigma-metric story above.
- **Arabic branding on the PDF.** DejaVu Sans (now embedded) doesn't cover
  Arabic script. You've already solved Arabic shaping/bidi rendering for
  the semen-analysis worksheet and the price-list invoices — that same
  font/reshaping approach can be ported into `pdf_report.py` if you want
  the tagline "للتحاليل الطبية" or Arabic operator names on the QC report.
- **Richer home dashboard** (large status cards, mini sparklines per test)
  — the current "QC Health by Test" list is a first pass in that
  direction, not the final UI.

## 6. What still needs a live run before you trust it with real data

This sandbox has no network access, so `streamlit`, `plotly`, and
`fpdf2` couldn't be installed here — every file passed `py_compile` and
the pure-logic modules (engine, sanitizer, date math) are unit tested,
but the Streamlit pages and the actual PDF byte output have only been
reviewed, not executed. Before relying on this for real QC decisions:
run `streamlit run app.py` locally, walk through Test Setup → QC Entry →
Levey-Jennings → Reports once with a couple of sample control lots, and
generate one real PDF to eyeball.

## 7. Governance note

Like your other clinical software, the *rule catalogue* in
`core/westgard_engine.py::RULE_INFO` (which rule → which action) is a
clinical/QA decision, not just a coding one — recommend Dr. Tarek reviews
and signs off on the exact rule set and reject/warning thresholds before
this goes live for patient-affecting QC decisions, the same way he
countersigns guideline citations and dose bands elsewhere. The
"Clinical/QA verification matrix" idea from the review (a signed table of
scenario → expected → actual, including the now-fixed branch-isolation
and duplicate-run cases) is worth doing as a real sign-off document once
you've run it live.
