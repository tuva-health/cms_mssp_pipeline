"""
A synthetic final settlement (STLMT, financial reconciliation) workbook in the
real CMS layout (TUVA-167).

Sheet names, sheet order, titles, header rows, lettered codes, row labels,
group headings and blanks follow the PY2025 CMS template: 16 sheets, rows 1-3
a title, a 'Table of Contents' link and a blank row, a blank cell written as
an en dash. Only template text is used. Every number is made up.

The numbers follow the CMS calculation, so a reader can check one table
against another (TUVA-165, extended to Table 3 [A]-[UU] and Table A1's nine
columns):

- Table A1: [D] = [C] / [B], [G] = [F] / [E], [H] = weighted-average [G] +
  0.03, and [I] = [D] because the weighted-average [D] stays under the cap.
- Table 2: [B] = A1 [B] (BY3) and A1 [C] (PY), [C] = A1 [I], [D] = [A] x [C],
  [E] = A3 [G] PY, [F] = A3 [I] PY, [G] = [D] x [F], [H] = Table 3 [D], and
  [I] = sum of [G] x [H]. [A] = Table 1 [L], and its total row = Table 1 [M].
- Table 3: [E] = sum of [C] x [D], [F] = Table 2 [I], [G] = [E] x [B],
  [H] = [F] x [B], [I] = [H] - [G], [J] / [K] split [I], [N] = [L] x [H],
  [CC] = [X] (the quality standard is met), [EE] = [J] x [CC], [GG] = 2% of
  [EE], [HH] = min([EE] - [GG], [FF]), [LL] = [JJ] - [KK], [MM] = [HH] - [KK].
  [L] is Table A4's sliding scale at [A], and [X] is Table A5's track value.

The A1 BY3 CMS-HCC scores are the e2e BNMRK final delivery's Table 1 [C] BY3
(tests/e2e/fixtures/workbooks/bnmrk_table_1.csv), as in a real settlement.
"""

from typing import Dict, List

DASH = "–"  # CMS writes a blank value as an en dash, not a hyphen.
TYPES = ("ESRD", "Disabled", "Aged/dual", "Aged/non-dual")
TOC = "Table of Contents"


def fmt(value: float) -> str:
    """A number as CMS-like text: up to ten significant digits, no '.0'."""
    return f"{value:.10g}"


def _by_type(func) -> Dict[str, str]:
    return {t: fmt(func(t)) for t in TYPES}


# ---------------------------------------------------------------------------
# Inputs. Everything else is derived from these.
# ---------------------------------------------------------------------------

ASSIGNED_BENEFICIARIES = 10500
PERSON_YEARS = 10000
# Table 2 [H] / Table 3 [D]: each type's share of performance-year person years.
PERSON_YEAR_SHARES = {"ESRD": 0.02, "Disabled": 0.08, "Aged/dual": 0.1, "Aged/non-dual": 0.8}
# Table 2 [G]: the updated benchmark by type; [I] = 13920 (TUVA-165).
TYPE_BENCHMARK = {"ESRD": 95000, "Disabled": 14000, "Aged/dual": 21000, "Aged/non-dual": 11000}
UPDATED_BENCHMARK = sum(PERSON_YEAR_SHARES[t] * TYPE_BENCHMARK[t] for t in TYPES)
# Table 2 [E] / A3 [G] PY, and [F] / A3 [I] PY.
BLENDED_UPDATE = {"ESRD": 1.035, "Disabled": 1.025, "Aged/dual": 1.025, "Aged/non-dual": 1.015}
THREE_WAY_UPDATE = {"ESRD": 1.04, "Disabled": 1.03, "Aged/dual": 1.03, "Aged/non-dual": 1.02}
# Table A1 [B]: BY3 CMS-HCC scores (the BNMRK fixture's), and [D] the ratio.
BY3_RISK_SCORE = {
    "ESRD": 0.8540764, "Disabled": 0.506856, "Aged/dual": 0.5337067, "Aged/non-dual": 0.8598672,
}
RISK_RATIO = {"ESRD": 1.01, "Disabled": 0.98, "Aged/dual": 1.02, "Aged/non-dual": 1.015}
# Table A1 [E]/[F]: demographic scores; the demographic ratio [G] is 1.
BY3_DEMOGRAPHIC = {"ESRD": 1.06, "Disabled": 1.1, "Aged/dual": 0.95, "Aged/non-dual": 0.98}
# Table 3 [C]: per capita expenditures by type.
PER_CAPITA_EXPENDITURES = {"ESRD": 90000, "Disabled": 13000, "Aged/dual": 20000, "Aged/non-dual": 10000}

# ---------------------------------------------------------------------------
# Derived values
# ---------------------------------------------------------------------------

PY_RISK_SCORE = {t: BY3_RISK_SCORE[t] * RISK_RATIO[t] for t in TYPES}
# Table 2 [D] = [G] / [F], and [A] = [D] / [C].
RISK_ADJUSTED_BENCHMARK = {t: TYPE_BENCHMARK[t] / THREE_WAY_UPDATE[t] for t in TYPES}
HISTORICAL_BENCHMARK = {t: RISK_ADJUSTED_BENCHMARK[t] / RISK_RATIO[t] for t in TYPES}
HISTORICAL_BENCHMARK_TOTAL = sum(
    HISTORICAL_BENCHMARK[t] * PERSON_YEAR_SHARES[t] for t in TYPES
)

# Table A1 [A]: benchmark expenditures x PY person years, normalized.
_weight = {t: HISTORICAL_BENCHMARK[t] * PERSON_YEAR_SHARES[t] for t in TYPES}
RISK_WEIGHTS = {t: _weight[t] / sum(_weight.values()) for t in TYPES}
WEIGHTED_RISK_RATIO = sum(RISK_WEIGHTS[t] * RISK_RATIO[t] for t in TYPES)
WEIGHTED_DEMOGRAPHIC_RATIO = 1.0
AGGREGATE_CAP = WEIGHTED_DEMOGRAPHIC_RATIO + 0.03
assert WEIGHTED_RISK_RATIO < AGGREGATE_CAP  # the cap binds for no type
CAPPED_RISK_RATIO = dict(RISK_RATIO)

# Table 3.
PER_CAPITA_TOTAL = sum(PER_CAPITA_EXPENDITURES[t] * PERSON_YEAR_SHARES[t] for t in TYPES)
TOTAL_EXPENDITURES = PER_CAPITA_TOTAL * PERSON_YEARS
TOTAL_BENCHMARK = UPDATED_BENCHMARK * PERSON_YEARS
SAVINGS = TOTAL_BENCHMARK - TOTAL_EXPENDITURES
assert SAVINGS > 0
# The regulation's sliding scale: 10,000-14,999 beneficiaries runs 3.0% to 2.7%.
MSR = 0.03 - (ASSIGNED_BENEFICIARIES - 10000) / 5000 * 0.003
MAX_SHARING_RATE = 0.4  # BASIC Track Level A (Table A5)
FINAL_SHARING_RATE = MAX_SHARING_RATE
SHARED_SAVINGS = SAVINGS * FINAL_SHARING_RATE
SHARED_SAVINGS_CAP = 0.1 * TOTAL_BENCHMARK
SEQUESTRATION = 0.02 * SHARED_SAVINGS
EARNED_PAYMENT = min(SHARED_SAVINGS - SEQUESTRATION, SHARED_SAVINGS_CAP)
assert SAVINGS > MSR * TOTAL_BENCHMARK  # savings are realized


# ---------------------------------------------------------------------------
# Sheet builders
# ---------------------------------------------------------------------------


def _top(title: str, width: int) -> list:
    blanks = [None] * (width - 1)
    return [[title, *blanks], [TOC, *blanks], [None, *blanks]]


def _blank_row(width: int) -> list:
    return [None] * width


COVER = [[None]] * 10 + [
    ["Shared Savings Program"],
    ["Financial Reconciliation Report"],
    ["Performance Year 2025"],
    ["Synthetic ACO"],
]
# A cover whose content starts well below row 1, as the real one does. It is
# what made the combined read_sheets() lose the short sheets (TUVA-167).

TOC_SHEET = [
    ["Table of Contents"],
    ["Parameters"],
    ["Table 1: Historical Benchmark Determination"],
    ["Table 2: Updated Benchmark Determination"],
    ["Table 3: Shared Savings/Losses Calculation"],
    ["Table A1: Risk Ratios for Annual Adjustment to the Historical Benchmark"],
]

GLOSSARY = [
    ["Glossary", None],
    ["ACO", "Accountable Care Organization"],
    ["ACPT", "Accountable Care Prospective Trend"],
    ["MSR", "Minimum Savings Rate"],
    ["MLR", "Minimum Loss Rate"],
]

_BY_PY = [
    ["Benchmark Year 1 (BY1)", "01/01/2022 - 12/31/2022"],
    ["Benchmark Year 2 (BY2)", "01/01/2023 - 12/31/2023"],
    ["Benchmark Year 3 (BY3)", "01/01/2024 - 12/31/2024"],
    ["Performance Year (PY)", "01/01/2025 - 12/31/2025"],
]

PARAMETERS = [
    ["Parameters of the Financial Reconciliation", None],
    [TOC, None],
    [None, None],
    ["This report is based on the PY 2025 ACO participant list.", None],
    ["Time Periods and ACO Characteristics", None],
    ["Performance Year for which ACO is Being Reconciled", "2025"],
    ["Claims-Based Beneficiary Assignment Window", None],
    *_BY_PY,
    ["Expenditure & Risk Score Period", None],
    *_BY_PY,
    ["Benchmark Year Weights", None],
    ["Benchmark Year 1 (BY1)", "0.1"],
    ["Benchmark Year 2 (BY2)", "0.3"],
    ["Benchmark Year 3 (BY3)", "0.6"],
    ["Claims Run-Out Period", "3 months"],
    ["Date Produced", "01/01/2026"],
    ["ACO Track", "BASIC Level A"],
    ["ACO Agreement Period", "1"],
    ["ACO Assignment Methodology", "Prospective"],
    ["ACO High or Low Revenue", "Low Revenue"],
]

# Table 1. Values sit in BY1-BY3 and/or Benchmark, by row; column E is the
# Excel table's blank 'Column1' spacer.
_BY = {"BY1": 0.9, "BY2": 0.95, "BY3": 1.0}


def _t1_types(by_values: Dict[str, Dict[str, str]], benchmark: Dict[str, str]) -> list:
    return [
        [t, *(by_values[y][t] for y in ("BY1", "BY2", "BY3")), None, benchmark[t]]
        for t in TYPES
    ]


def _t1_by(func) -> Dict[str, Dict[str, str]]:
    return {y: _by_type(lambda t: func(t, f)) for y, f in _BY.items()}


_DASHES = {t: DASH for t in TYPES}
_DASH_BY = {y: _DASHES for y in _BY}

TABLE_1 = [
    *_top("Table 1: Historical Benchmark Determination ", 6),
    ["Calculate Historical Benchmark", "BY1", "BY2", "BY3", "Column1", "Benchmark"],
    ["Assigned Beneficiaries", None, None, None, None, None],
    ["[A1] Assigned Beneficiaries", "9800", "10100", "10300", None, DASH],
    ["[A2] Person Years", "9300", "9600", "9800", None, DASH],
    *_t1_types(
        {"BY1": _DASHES, "BY2": _DASHES,
         "BY3": _by_type(lambda t: PERSON_YEAR_SHARES[t] * 9800)},
        _DASHES,
    ),
    ["Trended Historical Benchmark Expenditures", None, None, None, None, None],
    ["[B] Per Capita Expenditures ($)", None, None, None, None, None],
    *_t1_types(_t1_by(lambda t, f: HISTORICAL_BENCHMARK[t] * f), _DASHES),
    ["[C] CMS-HCC Risk Score", None, None, None, None, None],
    *_t1_types(_t1_by(lambda t, f: BY3_RISK_SCORE[t] * f), _DASHES),
    ["[H] Assigned Beneficiary Proportions ", None, None, None, None, None],
    *_t1_types({"BY1": _DASHES, "BY2": _DASHES, "BY3": _by_type(PERSON_YEAR_SHARES.get)}, _DASHES),
    ["[I] Historical Benchmark Before Regional Adjustment, Prior Savings Adjustment, "
     "or Population Adjustment ($)", DASH, DASH, DASH, None, fmt(HISTORICAL_BENCHMARK_TOTAL)],
    ["Apply Regional Adjustment, Prior Savings Adjustment, or Population Adjustment, "
     "as Applicable", None, None, None, None, None],
    ["[J] Adjustment Applied to Benchmark ($)", DASH, DASH, DASH, None, "None"],
    ["[L] Historical Benchmark Expenditures After Applying Adjustment ($) ",
     None, None, None, None, None],
    *_t1_types(_DASH_BY, _by_type(HISTORICAL_BENCHMARK.get)),
    ["[M] Historical Benchmark After Applying Applicable Adjustment ($) ",
     DASH, DASH, DASH, None, fmt(HISTORICAL_BENCHMARK_TOTAL)],
]

TABLE_1A = [
    *_top("Table 1A:  Regional Adjustment Determination", 4),
    ["Regional Adjustment Calculation", "BY3", "Column1", "Regional Adjustment"],
    ["Determine Regional Adjustment Before Applying Cap or Offset Factor", None, None, None],
    ["[A] ACO Risk-Adjusted Regional Average Expenditure Amount ($) ", None, None, None],
    *[[t, DASH, None, v] for t, v in _by_type(lambda t: HISTORICAL_BENCHMARK[t] * 1.01).items()],
    ["[C] Weighted Average Difference Between ACO Risk-Adjusted Regional Average "
     "Expenditure Amount and Historical Benchmark ($)", DASH, None, "130"],
    ["[D] Regional Adjustment Weight", DASH, None, "0.15"],
    ["Determine Regional Adjustment", None, None, None],
    ["[K] Dual Eligible Person Years Across Enrollment Types", "1200", None, DASH],
    ["[N] Regional Adjustment Expressed as a Single Value ($)", DASH, None, "0"],
]

TABLE_1B = [
    *_top("Table 1B: Prior Savings Adjustment Determination", 5),
    ["Prior Savings Adjustment Calculation", "PY Corresponding to BY1",
     "PY Corresponding to BY2", "PY Corresponding to BY3", "Prior Savings Adjustment"],
    ["Determine Eligibility for Prior Savings Adjustment", None, None, None, None],
    ["[A] Total Savings or Losses ($)  ", DASH, DASH, DASH, DASH],
    ["[D] ACO Eligible for Prior Savings Adjustment?", DASH, DASH, DASH, "No"],
    ["Apply Proration Factor to Account for Changes in Assigned Beneficiary "
     "Population Size", None, None, None, None],
    # [E] and [F] are entirely blank in a real delivery.
    ["[E] Numerator of Proration Factor Based on Financial Reconciliation Person Years",
     None, None, None, None],
    ["[F] Denominator of Proration Factor Based on BYs of Current Agreement Period",
     None, None, None, None],
    ["[G] Proration Factor", DASH, DASH, DASH, DASH],
    ["Determine Prior Savings Adjustment", None, None, None, None],
    ["[K] Prior Savings Adjustment ($)", DASH, DASH, DASH, "Not applicable"],
]

TABLE_1C = [
    *_top("Table 1C: Population Adjustment Determination", 3),
    ["Population Adjustment Calculation", "PY", "Population Adjustment"],
    ["Determine Eligibility for the Population Adjustment", None, None],
    ["[A] ACO Proportion of Assigned Beneficiaries enrolled in Medicare Part D LIS "
     "or Dually Eligible", "0.2", DASH],
    ["[B] ACO Eligible for Population Adjustment?", DASH, "No"],
    ["Determine Population Adjustment ", None, None],
    ["[C] Population Adjustment Scaler ($) ", DASH, "0"],
    ["[D] Population Adjustment ($)", DASH, "0"],
]


def _t2_types(values: Dict[str, str], benchmark_period: Dict[str, str] = None) -> list:
    """Table 2 per-type rows: B (benchmark period), C spacer, D (performance year)."""
    return [
        [t, (benchmark_period or _DASHES)[t], None, values[t]] for t in TYPES
    ]


TABLE_2 = [
    *_top("Table 2: Updated Benchmark Determination ", 4),
    [None, "Benchmark Period", None, "Performance Year"],
    ["Updated Benchmark Determination", None, None, None],
    # The bracket rows of [A] and [B] carry sub-header text, not values.
    ["[A] Historical Benchmark Expenditures After Applying Adjustment",
     "3-Year Average Benchmark", None, None],
    *_t2_types(_DASHES, _by_type(HISTORICAL_BENCHMARK.get)),
    # The total row reuses the [A] label.
    ["Historical Benchmark Expenditures After Applying Adjustment",
     fmt(HISTORICAL_BENCHMARK_TOTAL), None, DASH],
    ["[B] ACO CMS-HCC Risk Score ", "BY3", None, None],
    *_t2_types(_by_type(PY_RISK_SCORE.get), _by_type(BY3_RISK_SCORE.get)),
    ["[C] ACO CMS-HCC Risk Ratio (Capped)", None, None, None],
    *_t2_types(_by_type(CAPPED_RISK_RATIO.get)),
    ["[D] Risk-Adjusted Historical Benchmark ($) ", None, None, None],
    *_t2_types(_by_type(RISK_ADJUSTED_BENCHMARK.get)),
    ["[E] National-Regional Blended Update Factor ", None, None, None],
    *_t2_types(_by_type(BLENDED_UPDATE.get)),
    ["[F] Three-Way Blended Update Factor ", None, None, None],
    *_t2_types(_by_type(THREE_WAY_UPDATE.get)),
    ["[G] Updated Benchmark Expenditures ($)  ", None, None, None],
    *_t2_types(_by_type(TYPE_BENCHMARK.get)),
    ["[H] Assigned Beneficiary Proportions", None, None, None],
    *_t2_types(_by_type(PERSON_YEAR_SHARES.get)),
    ["[I] Updated Benchmark ($) ", DASH, None, fmt(UPDATED_BENCHMARK)],
]

# Table 3: one value column. (code, label, value)
TABLE_3_CODES = [
    ("Total Savings/Losses Calculation", None, None),
    ("A", "Assigned Beneficiaries", fmt(ASSIGNED_BENEFICIARIES)),
    ("B", "Person Years", fmt(PERSON_YEARS)),
    ("C", "Per Capita Expenditures by Enrollment Type ($)", _by_type(PER_CAPITA_EXPENDITURES.get)),
    ("D", "Assigned Beneficiary Proportions", _by_type(PERSON_YEAR_SHARES.get)),
    ("E", "Per Capita Expenditures ($)", fmt(PER_CAPITA_TOTAL)),
    ("F", "Per Capita Benchmark Expenditures ($)", fmt(UPDATED_BENCHMARK)),
    ("G", "Total Expenditures ($)", fmt(TOTAL_EXPENDITURES)),
    ("H", "Total Benchmark Expenditures ($)", fmt(TOTAL_BENCHMARK)),
    ("I", "Total Benchmark Expenditures Minus Total Expenditures ($)", fmt(SAVINGS)),
    ("J", "Total Savings ($)", fmt(SAVINGS)),
    ("K", "Total Losses ($)", "0"),
    ("L", "Minimum Savings Rate (%)", fmt(MSR)),
    ("M", "Minimum Loss Rate (%)", DASH),
    ("N", "Minimum Savings Rate ($)", fmt(MSR * TOTAL_BENCHMARK)),
    ("O", "Minimum Loss Rate ($)", DASH),
    ("P", "Savings or Losses Realized", "Savings"),
    ("Guardrail Determination for ACOs with Realized Losses ", None, None),
    ("Q", "Per Capita Benchmark Expenditures (Two-Way Blend) ($) ", DASH),
    ("R", "Total Benchmark Expenditures (Two-Way Blend) ($) ", DASH),
    ("S", "Total Benchmark Expenditures (Two-Way Blend) Minus Total Expenditures ($) ", DASH),
    ("T", "Guardrail Applies (Y/N) ", DASH),
    ("U", "Total Losses with Guardrail", DASH),
    ("V", "Minimum Loss Rate with Guardrail ($)", DASH),
    ("W", "Losses Realized with Guardrail (Y/N)", DASH),
    ("Sharing and Losses Rate Calculations", None, None),
    ("X", "Maximum Sharing Rate (%)", fmt(MAX_SHARING_RATE)),
    ("Y", "ACO Quality Score (%)", "0.85"),
    ("Z", "Met Quality Performance Standard (Y/N)", "Y"),
    ("AA", "Met Alternative Quality Performance Standard (Y/N/NA)", "NA"),
    ("BB", "ACO Met Criteria for Opportunities for Low Revenue ACOs to Share in Savings (Y/N)", "N"),
    ("CC", "Final Sharing Rate (%)", fmt(FINAL_SHARING_RATE)),
    ("DD", "Shared Loss Rate (%)", DASH),
    ("Shared Savings Calculation", None, None),
    ("EE", "Shared Savings ($)", fmt(SHARED_SAVINGS)),
    ("FF", "Shared Savings Cap ($)", fmt(SHARED_SAVINGS_CAP)),
    ("GG", "Sequestration Adjustment ($)", fmt(SEQUESTRATION)),
    ("HH", "Earned Performance Payment ($) ", fmt(EARNED_PAYMENT)),
    ("Adjustment for Advance Investment Payment Recoupment ", None, None),
    ("II", "Applicable Payment", "Earned Performance Payment"),
    ("JJ", "Maximum Recoupment at PY ($)", "0"),
    ("KK", "Recoupment at PY, Actual ($)", "0"),
    ("LL", "Payment Still Owed to CMS After PY ($)", "0"),
    ("MM", "Net Earned Performance Payment ($)", fmt(EARNED_PAYMENT)),
    ("Shared Losses Calculation", None, None),
    ("NN", "Shared Losses ($)", DASH),
    # rusty_sheet can return a large number in E-notation.
    ("OO", "Total Medicare Parts A and B FFS Revenue for ACO Participants ($)", "5.0E7"),
    ("PP", "Shared Losses Cap ($)", DASH),
    ("QQ", "Shared Losses After Applying Cap ($)", DASH),
    ("RR", "Share of Beneficiaries in Counties Affected by an Extreme and Uncontrollable "
           "Circumstance (%)", DASH),
    ("SS", "Share of Year Affected by an Extreme and Uncontrollable Circumstance (%)", DASH),
    ("TT", "Extreme and Uncontrollable Circumstance Adjustment ($)", DASH),
    ("UU", "Payment Due to CMS ($) ", DASH),
]


def _table_3() -> list:
    rows = [*_top("Table 3: Shared Savings/Losses Calculation  ", 2), [None, "Performance Year"]]
    for code, label, value in TABLE_3_CODES:
        if label is None:
            rows.append([code, None])  # a group heading
        elif isinstance(value, dict):
            rows.append([f"[{code}] {label}", None])
            rows.extend([t, value[t]] for t in TYPES)
        else:
            rows.append([f"[{code}] {label}", value])
    return rows


TABLE_3 = _table_3()

# Table A1: the letters label the columns. (code, header label)
A1_COLUMNS = [
    ("A", "Risk Score Weights ​\n[A]"),
    ("B", "BY3 CMS-HCC Risk Score\n[B]"),
    ("C", "PY CMS-HCC Risk Score\n[C]"),
    ("D", "CMS-HCC Risk Ratio (Uncapped)\n[D]"),
    ("E", "BY3 Demographic Risk Score\n[E]"),
    ("F", "PY Demographic Risk Score\n[F]"),
    ("G", "Demographic Risk Ratio ​\n[G]"),
    ("H", "Aggregate Cap ​\n[H]"),
    ("I", "Final Risk Ratio (Capped)\n[I]"),
]
A1_VALUES = {
    "A": RISK_WEIGHTS,
    "B": BY3_RISK_SCORE,
    "C": PY_RISK_SCORE,
    "D": RISK_RATIO,
    "E": BY3_DEMOGRAPHIC,
    "F": BY3_DEMOGRAPHIC,
    "G": {t: 1.0 for t in TYPES},
    "H": {t: AGGREGATE_CAP for t in TYPES},
    "I": CAPPED_RISK_RATIO,
}

TABLE_A1 = [
    *_top("Table A1: Risk Ratios for Annual Adjustment to the Historical Benchmark", 10),
    ["Medicare Enrollment Type", *(label for _, label in A1_COLUMNS)],
    *[[t, *(fmt(A1_VALUES[code][t]) for code, _ in A1_COLUMNS)] for t in TYPES],
    # Filled only under [D] and [G].
    ["Weighted Average ", DASH, DASH, DASH, fmt(WEIGHTED_RISK_RATIO), DASH, DASH,
     fmt(WEIGHTED_DEMOGRAPHIC_RATIO), DASH, DASH],
]

A2_BLOCKS = (
    "National Assignable Fee for Service Mean CMS-HCC Risk Scores Used to Renormalize "
    "CMS-HCC Risk Scores",
    "National Assignable Fee for Service Mean Demographic Risk Scores Used to Renormalize "
    "Demographic Risk Scores",
)
_A2_HEADER = ["Medicare Enrollment Type", "BY1", "BY2", "BY3", "PY"]

TABLE_A2 = [
    *_top("Table A2: Risk Score Renormalization", 5),
    [A2_BLOCKS[0], None, None, None, None],
    _A2_HEADER,
    *[[t, "1.05", "1.06", "1.07", "1.08"] for t in TYPES],
    _blank_row(5),
    [A2_BLOCKS[1], None, None, None, None],
    _A2_HEADER,
    *[[t, "1.01", "1.02", "1.03", "1.04"] for t in TYPES],
]


def _a3_types(func, py_only: bool = False) -> list:
    return [
        [t, *((DASH,) * 3 if py_only else (fmt(func(t) * 0.98), fmt(func(t) * 0.99), fmt(func(t)))),
         None, fmt(func(t))]
        for t in TYPES
    ]


TABLE_A3 = [
    *_top("Table A3: Trend and Update Factor Determination", 6),
    [None, "BY1", "BY2", "BY3", None, "PY"],
    ["[A] OACT National Assignable FFS Per Capita Expenditures ($)", None, None, None, None, None],
    *_a3_types(lambda t: HISTORICAL_BENCHMARK[t]),
    ["[G] National-Regional Blended Trend Factors and Update Factors", None, None, None, None, None],
    *_a3_types(BLENDED_UPDATE.get),
    ["[H] ACO-Specific ACPT Update Factors", None, None, None, None, None],
    *_a3_types(lambda t: THREE_WAY_UPDATE[t] + 0.01, py_only=True),
    ["[I] Three-Way Blended Update Factors", None, None, None, None, None],
    *_a3_types(THREE_WAY_UPDATE.get, py_only=True),
]

# Table A4: the regulation's MSR sliding scale, a lookup grid.
MSR_GRID = [
    ("5000", "5999", "0.039", "0.036"),
    ("10000", "14999", "0.03", "0.027"),
    ("60000", "and above", "0.02", "0.02"),
]
TABLE_A4 = [
    ["Table A4: Minimum Savings Rate (MSR) and Minimum Loss Rate (MLR) by Number of "
     "Assigned Beneficiaries", None, None, None],
    [TOC, None, None, None],
    ["Overview", None, None, None],
    ["For all one-sided ACOs, and for two-sided ACOs selecting this option, the ACO’s "
     "MSR varies based on the number of assigned beneficiaries.", None, None, None],
    _blank_row(4),
    ["Number of Assigned Beneficiaries Range: \nLow", "Number of Assigned Beneficiaries Range: \nHigh",
     "MSR Range: \nLow", "MSR Range:\nHigh"],
    *[list(row) for row in MSR_GRID],
]

A5_TRACKS = ["BASIC Track Level A", "BASIC Track Level B", "BASIC Track Level C",
             "BASIC Track Level D", "BASIC Track Level E", "ENHANCED Track"]
TABLE_A5 = [
    *_top("Table A5: Track-Dependent Calculation Inputs", 7),
    ["Input", *A5_TRACKS],
    ["Maximum Sharing Rate \n(Row [X] in Table 3)", "0.4", "0.4", "0.5", "0.5", "0.5", "0.75"],
    ["Shared Loss Rate \n(Row [DD] in Table 3)", "Not applicable", "Not applicable",
     "0.3", "0.3", "0.3", "1 minus the final sharing rate"],
    ["Shared Savings Cap \n(Row [FF] in Table 3)", *["10% of updated benchmark"] * 5,
     "20% of updated benchmark"],
    ["Shared Losses Cap \n(Row [PP] in Table 3)", "Not applicable", "Not applicable",
     "2% of revenue", "4% of revenue", "8% of revenue", "15% of updated benchmark"],
    _blank_row(7),
    ["Note: If \"Guardrail Applies\" (Row [T] in Table 3 is equal to \"Y\"), the "
     "two-way blend is used.", None, None, None, None, None, None],
]

_PY_COLS = ["BY3", "PY1", "PY2", "PY3", "PY4", "PY5"]


def _a6_rows(types) -> list:
    return [[f"      {t}", DASH, "1.03", "1.06", "1.09", "1.12", "1.15"] for t in types]


TABLE_A6 = [
    *_top("Table A6: Accountable Care Prospective Trend (ACPT) Component of the Three-Way "
          "Blended Benchmark Update Factor", 7),
    [None, None, "Performance Year", None, None, None, None],
    ["ACPT Calculation ", *_PY_COLS],
    ["[A] Modified USPCC Annualized Growth Rates", None, None, None, None, None, None],
    *_a6_rows(("ESRD", "Aged/Disabled")),
    ["[B] Cumulative Modified USPCC Growth Rates", None, None, None, None, None, None],
    *_a6_rows(("ESRD", "Aged/Disabled")),
    ["[K] ACPT", None, None, None, None, None, None],
    *_a6_rows(TYPES),
]

# table -> (real sheet name, rows)
STLMT_SHEET_BY_TABLE = {
    "stlmt_table_1": ("Table 1 - Historical Benchmark", TABLE_1),
    "stlmt_table_1a": ("Table 1A - Regional Adjustment", TABLE_1A),
    "stlmt_table_1b": ("Table 1B - Prior Savings Adj", TABLE_1B),
    "stlmt_table_1c": ("Table 1C - Population Adj", TABLE_1C),
    "stlmt_table_2": ("Table 2 - Updated Benchmark", TABLE_2),
    "stlmt_table_3": ("Table 3 - Shared Savings Losses", TABLE_3),
    "stlmt_table_a1": ("Table A1 - Risk", TABLE_A1),
    "stlmt_table_a2": ("Table A2 - Renormalization", TABLE_A2),
    "stlmt_table_a3": ("Table A3 - Trend & Update", TABLE_A3),
    "stlmt_table_a4": ("Table A4 - MSR", TABLE_A4),
    "stlmt_table_a5": ("Table A5 - Inputs by Track", TABLE_A5),
    "stlmt_table_a6": ("Table A6 - ACPT", TABLE_A6),
}


def stlmt_sheets(with_table_a6: bool = True) -> Dict[str, List[list]]:
    """{sheet name: rows} in the real workbook's sheet order."""
    sheets = {"Cover": COVER, "TOC": TOC_SHEET, "Glossary": GLOSSARY, "Parameters": PARAMETERS}
    for table, (sheet_name, rows) in STLMT_SHEET_BY_TABLE.items():
        if table == "stlmt_table_a6" and not with_table_a6:
            continue
        sheets[sheet_name] = rows
    return sheets
