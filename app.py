# app.py — Streamlit "Vendor Finance vs Lump Sum" Dashboard
# --------------------------------------------------------
# README — How to run
# 1) Install deps (ideally in a venv):
#    pip install streamlit pandas numpy matplotlib xlsxwriter
# 2) Run:
#    streamlit run app.py
# 3) The app opens in your browser. Adjust inputs on the left; export PNG/CSV/XLSX/JSON.
#
# Notes:
# - All amounts formatted as AUD (A$) with thousand separators and no decimals for big figures.
# - "Tax view" is a simplified model: CGT is assumed payable at settlement (t=0) on total capital gain
#   with a 50% CGT discount for individuals; interest is fully taxable in the year received.
#   Always seek professional tax advice for a specific deal—this is for illustration only.
# - PNG export renders a pitch-deck clean 1920×1080 image of the key cards and charts.
# - Unit tests: click "Run unit tests" in the sidebar to verify formulas quickly.
#
# --------------------------------------------------------

import io
import json
import math
from dataclasses import dataclass
from typing import List, Literal, Dict

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import streamlit as st

Structure = Literal["Amortizing", "Interest-Only + Balloon", "Equal Principal"]

# ---------- Helpers ----------

def aud(x: float) -> str:
    """Format currency in AUD with thousands separators, 0 decimals for big figures."""
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    return f"A${x:,.0f}"

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

# ---------- Schedules ----------

@dataclass
class CashFlow:
    year: int
    payment: float
    interest: float
    principal: float
    ending_balance: float

def amortizing_schedule(principal: float, rate: float, years: int) -> List[CashFlow]:
    n = int(years)
    r = rate
    if n <= 0:
        return []
    if r == 0:
        pmt = principal / n
    else:
        pmt = principal * r / (1 - (1 + r) ** (-n))

    bal = principal
    rows = []
    for t in range(1, n + 1):
        interest = bal * r
        principal_part = pmt - interest
        bal = max(0.0, bal - principal_part)
        rows.append(CashFlow(t, pmt, interest, principal_part, bal))
    return rows

def interest_only_balloon_schedule(principal: float, rate: float, years: int) -> List[CashFlow]:
    n = int(years)
    r = rate
    if n <= 0:
        return []
    bal = principal
    rows = []
    # Years 1..n-1 interest only
    for t in range(1, n):
        interest = bal * r
        rows.append(CashFlow(t, interest, interest, 0.0, bal))
    # Final year: interest + full principal
    interest = bal * r
    payment = interest + bal
    rows.append(CashFlow(n, payment, interest, bal, 0.0))
    return rows

def equal_principal_schedule(principal: float, rate: float, years: int) -> List[CashFlow]:
    n = int(years)
    r = rate
    if n <= 0:
        return []
    bal = principal
    principal_part = principal / n
    rows = []
    for t in range(1, n + 1):
        interest = bal * r
        payment = principal_part + interest
        bal = max(0.0, bal - principal_part)
        rows.append(CashFlow(t, payment, interest, principal_part, bal))
    return rows

def build_schedule(structure: Structure, principal: float, rate: float, years: int) -> List[CashFlow]:
    if structure == "Amortizing":
        return amortizing_schedule(principal, rate, years)
    elif structure == "Interest-Only + Balloon":
        return interest_only_balloon_schedule(principal, rate, years)
    elif structure == "Equal Principal":
        return equal_principal_schedule(principal, rate, years)
    else:
        raise ValueError("Unknown structure")

# ---------- Tax & NPV (simplified) ----------

def npv(cashflows: List[float], discount_rate: float) -> float:
    return sum(cf / ((1 + discount_rate) ** t) for t, cf in enumerate(cashflows))

def tax_model(
    is_on: bool,
    lump_gross: float,
    vf_principal: float,
    schedule: List[CashFlow],
    cost_base: float,
    marginal_rate: float,
    discount_rate: float,
    deposit: float
) -> Dict[str, float]:
    """
    Simplified tax model:
    - CGT payable at t=0 on (proceeds - cost base), 50% discount for individuals.
    - Interest taxed annually at marginal_rate.
    - Returns nominal after-tax totals and NPV of after-tax cash flows for both paths.
    """
    if not is_on:
        return {}

    # Lump Sum cashflows (t=0 only)
    gain_lump = max(0.0, lump_gross - cost_base)
    discounted_gain_lump = 0.5 * gain_lump
    cgt_tax_lump = marginal_rate * discounted_gain_lump
    lump_after_tax_cf = [lump_gross - cgt_tax_lump]
    lump_after_tax_npv = npv(lump_after_tax_cf, discount_rate)

    # Vendor Finance cashflows: deposit at t=0 then schedule
    gain_vf = max(0.0, vf_principal - cost_base)
    discounted_gain_vf = 0.5 * gain_vf
    cgt_tax_vf = marginal_rate * discounted_gain_vf

    # Year 0
    vf_after_tax_cf = [deposit - cgt_tax_vf]
    # Years 1..N
    for row in schedule:
        interest_tax = marginal_rate * row.interest
        vf_after_tax_cf.append(row.payment - interest_tax)
    vf_after_tax_npv = npv(vf_after_tax_cf, discount_rate)

    return {
        "cgt_tax_lump": cgt_tax_lump,
        "cgt_tax_vf": cgt_tax_vf,
        "lump_after_tax_total": sum(lump_after_tax_cf),
        "vf_after_tax_total": sum(vf_after_tax_cf),
        "lump_after_tax_npv": lump_after_tax_npv,
        "vf_after_tax_npv": vf_after_tax_npv,
    }

# ---------- Slide image (PNG) ----------

def build_slide_image(
    headline: float,
    lump: float,
    vf_principal: float,
    schedule: List[CashFlow],
    width_px: int = 1920,
    height_px: int = 1080,
) -> bytes:
    total_interest = sum(r.interest for r in schedule)
    vf_gross = vf_principal + total_interest
    delta_amt = vf_gross - lump
    delta_pct = (delta_amt / lump * 100) if lump > 0 else 0

    # Build cumulative VF timeline (assumes no deposit for chart simplicity; deposit shown in text cards)
    cum = 0.0
    xs = [0]
    ys_vf = [0.0]
    for row in schedule:
        cum += row.payment
        xs.append(row.year)
        ys_vf.append(cum)
    ys_lump = [lump] + [lump] * (len(schedule))

    # Matplotlib figure
    fig_w = width_px / 100
    fig_h = height_px / 100
    fig = plt.figure(figsize=(fig_w, fig_h), dpi=100)
    fig.patch.set_facecolor("white")

    # Title area
    ax_title = plt.axes([0, 0.89, 1, 0.1]); ax_title.axis("off")
    ax_title.text(0.02, 0.65, "Vendor Finance vs Lump Sum (Export)", fontsize=28, fontweight="bold", va="center")
    ax_title.text(0.02, 0.15, f"Headline {aud(headline)} • Pitch‑deck clean export", fontsize=16, va="center")

    # Cards
    ax_cards = plt.axes([0.03, 0.64, 0.94, 0.22]); ax_cards.axis("off")

    def card(ax, x, y, w, h, title, big, foot):
        ax.add_patch(plt.Rectangle((x, y), w, h, fill=False, linewidth=1.5))
        ax.text(x + 0.02*w, y + 0.72*h, title, fontsize=14, fontweight="bold", va="top")
        ax.text(x + 0.02*w, y + 0.38*h, big, fontsize=36, fontweight="bold", va="top")
        ax.text(x + 0.02*w, y + 0.12*h, foot, fontsize=12, va="top")

    card(ax_cards, 0.00, 0.05, 0.30, 0.9, "Lump Sum (Gross)", aud(lump), "Paid at settlement")
    card(ax_cards, 0.35, 0.05, 0.30, 0.9, "Vendor Finance (Gross)", aud(vf_gross),
         f"Principal {aud(vf_principal)} + Interest {aud(total_interest)}")
    ax_cards.add_patch(plt.Rectangle((0.70, 0.05), 0.30, 0.9, fill=False, linewidth=1.5))
    ax_cards.text(0.72, 0.72, "Delta", fontsize=14, fontweight="bold", va="top")
    ax_cards.text(0.72, 0.38, f"+{aud(delta_amt)}\\n≈ +{delta_pct:.1f}%", fontsize=32, fontweight="bold", va="top")
    ax_cards.text(0.72, 0.12, "Vendor Finance vs Lump Sum", fontsize=12, va="top")

    # Bars
    ax_bar = plt.axes([0.06, 0.35, 0.38, 0.22])
    ax_bar.bar([0], [lump], label="Lump Gross")
    ax_bar.bar([1], [vf_principal], label="VF Principal")
    ax_bar.bar([1], [total_interest], bottom=[vf_principal], label="VF Interest")
    ax_bar.set_xticks([0,1], ["Lump", "Vendor Finance"])
    ax_bar.set_ylabel("A$")
    ax_bar.set_title("Gross Proceeds — Breakdown")
    ax_bar.tick_params(axis="x", labelrotation=0)

    # Timeline
    ax_time = plt.axes([0.55, 0.35, 0.38, 0.22])
    ax_time.plot(xs, ys_lump, linewidth=2)
    ax_time.plot(xs, ys_vf, linewidth=2)
    ax_time.set_title("Cumulative Receipts Timeline (years)")
    ax_time.set_xlabel("Year")
    ax_time.set_ylabel("A$")
    ax_time.legend(["Lump Sum (t=0)", "Vendor Finance"], loc="lower right")
    ax_time.grid(True, alpha=0.25)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()

# ---------- Unit Tests ----------

def run_unit_tests():
    # 1) Amortizing zero-rate equals straight-line
    sched = amortizing_schedule(1000.0, 0.0, 5)
    assert len(sched) == 5
    assert abs(sum(r.principal for r in sched) - 1000.0) < 1e-6
    assert all(abs(r.interest) < 1e-9 for r in sched)

    # 2) IO+Balloon: last balance zero, first n-1 principal zero
    sched2 = interest_only_balloon_schedule(2000.0, 0.1, 3)
    assert len(sched2) == 3
    assert abs(sched2[-1].ending_balance - 0.0) < 1e-9
    assert all(abs(r.principal) < 1e-9 for r in sched2[:-1])
    assert abs(sched2[-1].principal - 2000.0) < 1e-9

    # 3) Equal Principal: sum principal equals principal input
    sched3 = equal_principal_schedule(3000.0, 0.1, 3)
    assert abs(sum(r.principal for r in sched3) - 3000.0) < 1e-6

    # 4) Build schedule selector mapping
    for s in ("Amortizing", "Interest-Only + Balloon", "Equal Principal"):
        sc = build_schedule(s, 1000, 0.05, 5)
        assert isinstance(sc, list)

    return "All unit tests passed ✅"

# ---------- UI ----------

st.set_page_config(page_title="Sale Structure Comparator", layout="wide")

with st.sidebar:
    st.header("Inputs")
    headline = st.number_input("Headline value (A$)", min_value=0.0, value=5_000_000.0, step=100_000.0, format="%.0f")
    lump_disc = st.number_input("Lump Sum discount %", min_value=0.0, max_value=95.0, value=25.0, step=1.0) / 100.0
    vf_prem = st.number_input("Vendor Finance premium %", min_value=0.0, max_value=200.0, value=15.0, step=1.0) / 100.0
    rate = st.number_input("Interest rate %", min_value=0.0, max_value=100.0, value=5.0, step=0.5) / 100.0
    years = int(st.number_input("Term (years)", min_value=1, max_value=50, value=10, step=1))

    structure = st.selectbox("Structure", ["Amortizing", "Interest-Only + Balloon", "Equal Principal"])

    deposit = st.number_input("Optional: Deposit at settlement (A$)", min_value=0.0, value=0.0, step=50_000.0, format="%.0f")
    start_date = st.date_input("Optional: Start date (ignored for math)")

    tax_on = st.checkbox("Tax view (simplified)", value=False)
    if tax_on:
        cost_base = st.number_input("Cost base (A$)", min_value=0.0, value=0.0, step=50_000.0, format="%.0f")
        seller_type = st.selectbox("Seller type", ["Individual (50% CGT discount)"])
        mtr = st.number_input("Marginal tax rate %", min_value=0.0, max_value=60.0, value=47.0, step=1.0) / 100.0
        disc_rate = st.number_input("NPV discount rate %", min_value=0.0, max_value=50.0, value=8.0, step=0.5) / 100.0
    else:
        cost_base = 0.0
        seller_type = "Individual (50% CGT discount)"
        mtr = 0.47
        disc_rate = 0.08

    if st.button("Run unit tests"):
        try:
            msg = run_unit_tests()
            st.success(msg)
        except AssertionError as e:
            st.error(f"Unit tests failed: {e}")

# Validate deposit <= VF principal (pre-check; principal is headline * (1 + premium))
lump_gross = headline * (1 - lump_disc)
vf_principal = headline * (1 + vf_prem)
if deposit > vf_principal:
    st.error("Deposit cannot exceed the Vendor Finance principal (headline × (1 + premium)).")
    st.stop()

# Build schedule on financed balance after deposit
financed = vf_principal - deposit
schedule = build_schedule(structure, financed, rate, years)

total_interest = sum(r.interest for r in schedule)
vf_gross = vf_principal + total_interest
annual_payment_label = "Annual payment (Year 1)"

if structure == "Amortizing":
    annual_payment = schedule[0].payment if schedule else 0.0
elif structure == "Interest-Only + Balloon":
    annual_payment = schedule[0].payment if schedule else 0.0
else:  # Equal Principal
    annual_payment = schedule[0].payment if schedule else 0.0

# ---------- Top cards ----------
col1, col2, col3 = st.columns([1,1,1])

with col1:
    st.markdown("### Lump Sum (Gross)")
    st.markdown(f"## {aud(lump_gross)}")
    st.caption("Paid at settlement")

with col2:
    st.markdown("### Vendor Finance (Gross)")
    st.markdown(f"## {aud(vf_gross)}")
    st.caption(f"Principal {aud(vf_principal)} + Interest {aud(total_interest)}")

delta_amt = vf_gross - lump_gross
delta_pct = (delta_amt / lump_gross * 100) if lump_gross > 0 else 0.0
with col3:
    st.markdown("### Delta")
    st.markdown(f"## +{aud(delta_amt)}  \n≈ +{delta_pct:.1f}%")
    st.caption("Vendor Finance vs Lump Sum")

# ---------- Charts ----------
left, right = st.columns([1,1])
with left:
    st.markdown("#### Gross Proceeds — Breakdown")
    fig_bar, ax = plt.subplots(figsize=(6,4), dpi=150)
    ax.bar([0], [lump_gross], label="Lump Gross")
    ax.bar([1], [vf_principal], label="VF Principal")
    ax.bar([1], [total_interest], bottom=[vf_principal], label="VF Interest")
    ax.set_xticks([0,1], ["Lump", "Vendor Finance"])
    ax.set_ylabel("A$")
    ax.grid(True, axis="y", alpha=0.2)
    st.pyplot(fig_bar, caption="Stacked principal + interest for Vendor Finance vs single bar for Lump.", use_container_width=True)

with right:
    st.markdown("#### Cumulative Receipts Timeline (years)")
    xs = [0] + [r.year for r in schedule]
    # Lump received at t=0 (year 0); flat thereafter
    ys_lump = [lump_gross] + [lump_gross] * len(schedule)
    cum = 0.0
    ys_vf = [deposit]  # deposit at t=0
    for r in schedule:
        cum += r.payment
        ys_vf.append(deposit + cum)
    fig_t, ax2 = plt.subplots(figsize=(6,4), dpi=150)
    ax2.plot(xs, ys_lump, linewidth=2, label="Lump Sum (t=0)")
    ax2.plot(xs, ys_vf, linewidth=2, label="Vendor Finance")
    ax2.set_xlabel("Year"); ax2.set_ylabel("A$")
    ax2.grid(True, alpha=0.25); ax2.legend(loc="lower right")
    st.pyplot(fig_t, caption="Mini timeline strip (0–term) showing cumulative receipts.", use_container_width=True)

# ---------- Totals & Tax ----------
st.markdown("### Totals")
tot_cols = st.columns([1,1,1,1])
with tot_cols[0]:
    st.metric("Lump Sum — Gross Proceeds", aud(lump_gross))
with tot_cols[1]:
    st.metric("Vendor Finance — Principal", aud(vf_principal))
with tot_cols[2]:
    st.metric("Vendor Finance — Total Interest", aud(total_interest))
with tot_cols[3]:
    st.metric(annual_payment_label, aud(annual_payment))

if tax_on:
    results = tax_model(True, lump_gross, vf_principal, schedule, cost_base, mtr, disc_rate, deposit)
    st.markdown("### Tax View (simplified)")
    tax_cols = st.columns([1,1,1,1,1,1])
    tax_cols[0].metric("CGT at settlement — Lump", aud(results["cgt_tax_lump"]))
    tax_cols[1].metric("CGT at settlement — VF", aud(results["cgt_tax_vf"]))
    tax_cols[2].metric("Nominal After‑Tax — Lump", aud(results["lump_after_tax_total"]))
    tax_cols[3].metric("Nominal After‑Tax — VF", aud(results["vf_after_tax_total"]))
    tax_cols[4].metric("NPV After‑Tax — Lump", aud(results["lump_after_tax_npv"]))
    tax_cols[5].metric("NPV After‑Tax — VF", aud(results["vf_after_tax_npv"]))
    st.caption("Assumes CGT at settlement and interest taxed annually; for illustration only.")

# ---------- Schedule Table + Exports ----------
st.markdown("### Compact Payment Schedule")
df = pd.DataFrame([{
    "Year": r.year,
    "Payment": round(r.payment),
    "Interest": round(r.interest),
    "Principal": round(r.principal),
    "Ending Balance": round(r.ending_balance)
} for r in schedule])

st.dataframe(df, use_container_width=True)

# Export buttons
csv_data = df.to_csv(index=False).encode("utf-8")

xlsx_buf = io.BytesIO()
with pd.ExcelWriter(xlsx_buf, engine="xlsxwriter") as writer:
    df.to_excel(writer, index=False, sheet_name="Schedule")
xlsx_buf.seek(0)

inputs_json = json.dumps({
    "headline": headline,
    "lump_discount_pct": round(lump_disc*100, 2),
    "vendor_premium_pct": round(vf_prem*100, 2),
    "rate_pct": round(rate*100, 2),
    "years": years,
    "structure": structure,
    "deposit": deposit,
    "start_date": str(start_date),
    "tax_on": tax_on,
    "cost_base": cost_base if tax_on else None,
    "marginal_tax_rate_pct": round(mtr*100, 2) if tax_on else None,
    "npv_discount_rate_pct": round(disc_rate*100, 2) if tax_on else None,
}, indent=2).encode("utf-8")

st.download_button("Export CSV (Schedule)", data=csv_data, file_name="payment_schedule.csv", mime="text/csv")
st.download_button("Export XLSX (Schedule)", data=xlsx_buf, file_name="payment_schedule.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
st.download_button("Export JSON (Inputs)", data=inputs_json, file_name="inputs.json", mime="application/json")

# PNG export: build image from current state
png_bytes = build_slide_image(headline, lump_gross, vf_principal, schedule)
st.download_button("Download PNG (Dashboard 1920×1080)", data=png_bytes, file_name="dashboard_export.png", mime="image/png")

# Accessibility notes
st.caption("Charts include descriptive captions. Colors not hard‑coded to improve contrast compatibility.")
