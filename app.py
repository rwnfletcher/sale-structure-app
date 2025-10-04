# app.py — Streamlit "Vendor Finance vs Lump Sum" Dashboard (with Equity Roll + Handover Timebar)
# ---------------------------------------------------------------------------------------------
# README — How to run
# 1) pip install streamlit pandas numpy matplotlib xlsxwriter
# 2) streamlit run app.py
#
# What’s new:
# - Equity roll input (% of gross) to model sellers keeping equity (reduces cash proceeds; shows rolled value).
# - Replaces cumulative timeline with a comparative handover timebar (weeks): Seller finance vs Lump sum.
# - PNG export reflects the new timebar. JSON export includes new inputs.
# - Tax view unchanged and IGNORE equity roll for CGT by default (equity roll has specific tax rules).
#   Add a caption note to avoid misinterpretation—consult your tax advisor for deal‑specific treatment.
# ---------------------------------------------------------------------------------------------

import io
import json
import math
from dataclasses import dataclass
from typing import List, Literal, Dict, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import streamlit as st

Structure = Literal["Amortizing", "Interest-Only + Balloon", "Equal Principal"]

# ---------- Helpers ----------

def aud(x: float) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    return f"A${x:,.0f}"

@dataclass
class CashFlow:
    year: int
    payment: float
    interest: float
    principal: float
    ending_balance: float

# ---------- Schedules ----------

def amortizing_schedule(principal: float, rate: float, years: int) -> List[CashFlow]:
    n = int(years); r = rate
    if n <= 0: return []
    if r == 0: pmt = principal / n
    else: pmt = principal * r / (1 - (1 + r) ** (-n))
    bal = principal; rows = []
    for t in range(1, n + 1):
        interest = bal * r
        principal_part = pmt - interest
        bal = max(0.0, bal - principal_part)
        rows.append(CashFlow(t, pmt, interest, principal_part, bal))
    return rows

def interest_only_balloon_schedule(principal: float, rate: float, years: int) -> List[CashFlow]:
    n = int(years); r = rate
    if n <= 0: return []
    bal = principal; rows = []
    for t in range(1, n):
        interest = bal * r
        rows.append(CashFlow(t, interest, interest, 0.0, bal))
    interest = bal * r
    payment = interest + bal
    rows.append(CashFlow(n, payment, interest, bal, 0.0))
    return rows

def equal_principal_schedule(principal: float, rate: float, years: int) -> List[CashFlow]:
    n = int(years); r = rate
    if n <= 0: return []
    bal = principal; rows = []; principal_part = principal / n
    for t in range(1, n + 1):
        interest = bal * r
        payment = principal_part + interest
        bal = max(0.0, bal - principal_part)
        rows.append(CashFlow(t, payment, interest, principal_part, bal))
    return rows

def build_schedule(structure: Structure, principal: float, rate: float, years: int) -> List[CashFlow]:
    if structure == "Amortizing": return amortizing_schedule(principal, rate, years)
    if structure == "Interest-Only + Balloon": return interest_only_balloon_schedule(principal, rate, years)
    if structure == "Equal Principal": return equal_principal_schedule(principal, rate, years)
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
    if not is_on: return {}
    # Note: Equity roll is ignored here by default. Deal-specific tax (e.g., scrip rollover) varies.
    gain_lump = max(0.0, lump_gross - cost_base)
    cgt_tax_lump = marginal_rate * (0.5 * gain_lump)
    lump_after_tax_cf = [lump_gross - cgt_tax_lump]
    lump_after_tax_npv = npv(lump_after_tax_cf, discount_rate)

    gain_vf = max(0.0, vf_principal - cost_base)
    cgt_tax_vf = marginal_rate * (0.5 * gain_vf)
    vf_after_tax_cf = [deposit - cgt_tax_vf]
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

# ---------- Export image (PNG) ----------

def draw_timebar(ax, seller_weeks: float, lump_min: float, lump_max: float):
    """Horizontal comparative timebar in weeks with a range for Lump Sum."""
    ax.set_title("Handover Timing (weeks)")
    labels = ["Seller Finance", "Lump Sum"]
    y_pos = [1, 0]

    # Seller finance: single week bar
    ax.barh([y_pos[0]], [seller_weeks], left=[0], height=0.35)
    # Lump sum: show min..max as a bar from min to max
    ax.barh([y_pos[1]], [lump_max - lump_min], left=[lump_min], height=0.35)

    ax.set_yticks(y_pos, labels)
    ax.set_xlabel("Weeks")
    ax.grid(True, axis="x", alpha=0.25)
    # Annotate
    ax.text(seller_weeks + 0.2, y_pos[0], f"{seller_weeks:.0f} wk", va="center")
    ax.text(lump_max + 0.2, y_pos[1], f"{lump_min:.0f}–{lump_max:.0f} wks", va="center")

def build_slide_image(
    headline: float,
    lump_cash: float,
    vf_cash: float,
    vf_principal: float,
    total_interest: float,
    equity_roll_pct: float,
    seller_weeks: float,
    lump_min: float,
    lump_max: float,
    width_px: int = 1920,
    height_px: int = 1080,
) -> bytes:
    delta_amt = vf_cash - lump_cash
    delta_pct = (delta_amt / lump_cash * 100) if lump_cash > 0 else 0
    vf_gross = vf_principal + total_interest

    fig_w = width_px / 100; fig_h = height_px / 100
    fig = plt.figure(figsize=(fig_w, fig_h), dpi=100); fig.patch.set_facecolor("white")

    # Title
    ax_title = plt.axes([0, 0.89, 1, 0.1]); ax_title.axis("off")
    ax_title.text(0.02, 0.65, "Vendor Finance vs Lump Sum (Export)", fontsize=28, fontweight="bold", va="center")
    ax_title.text(0.02, 0.15, f"Headline {aud(headline)} • Equity roll {equity_roll_pct:.1f}% of gross", fontsize=16, va="center")

    # Cards row
    ax_cards = plt.axes([0.03, 0.64, 0.94, 0.22]); ax_cards.axis("off")
    def card(ax, x, y, w, h, title, big, foot, extra=None):
        ax.add_patch(plt.Rectangle((x, y), w, h, fill=False, linewidth=1.5))
        ax.text(x + 0.02*w, y + 0.72*h, title, fontsize=14, fontweight="bold", va="top")
        ax.text(x + 0.02*w, y + 0.38*h, big, fontsize=36, fontweight="bold", va="top")
        ax.text(x + 0.02*w, y + 0.12*h, foot, fontsize=12, va="top")
        if extra:
            ax.text(x + 0.02*w, y + 0.03*h, extra, fontsize=11, va="top")

    # Lump card
    card(ax_cards, 0.00, 0.05, 0.30, 0.9,
         "Lump Sum (Cash after equity roll)",
         aud(lump_cash),
         "Paid at settlement",
         f"Equity rolled: {equity_roll_pct:.1f}% of gross")

    # VF card
    card(ax_cards, 0.35, 0.05, 0.30, 0.9,
         "Vendor Finance (Cash after equity roll)",
         aud(vf_cash),
         f"Principal {aud(vf_principal)} + Interest {aud(total_interest)}",
         f"Equity rolled: {equity_roll_pct:.1f}% of gross")

    # Delta
    ax_cards.add_patch(plt.Rectangle((0.70, 0.05), 0.30, 0.9, fill=False, linewidth=1.5))
    ax_cards.text(0.72, 0.72, "Delta (Cash)", fontsize=14, fontweight="bold", va="top")
    ax_cards.text(0.72, 0.38, f"+{aud(delta_amt)}\n≈ +{delta_pct:.1f}%", fontsize=32, fontweight="bold", va="top")
    ax_cards.text(0.72, 0.12, "Vendor Finance vs Lump Sum", fontsize=12, va="top")

    # Bars (cash): Lump vs VF (with VF shown as stacked principal & interest proportions scaled down by equity roll)
    ax_bar = plt.axes([0.06, 0.35, 0.38, 0.22])
    ax_bar.bar([0], [lump_cash], label="Lump Cash")
    # Scale principal+interest by (1 - equity_roll_pct)
    roll_multiplier = (1 - equity_roll_pct/100.0)
    ax_bar.bar([1], [vf_principal * roll_multiplier], label="VF Principal (cash)")
    ax_bar.bar([1], [total_interest * roll_multiplier], bottom=[vf_principal * roll_multiplier], label="VF Interest (cash)")
    ax_bar.set_xticks([0,1], ["Lump", "Vendor Finance"])
    ax_bar.set_ylabel("A$"); ax_bar.set_title("Cash Proceeds — Breakdown")
    ax_bar.grid(True, axis="y", alpha=0.2)

    # Timebar (handover weeks)
    ax_time = plt.axes([0.55, 0.35, 0.38, 0.22])
    draw_timebar(ax_time, seller_weeks, lump_min, lump_max)

    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=100, bbox_inches="tight"); plt.close(fig); buf.seek(0)
    return buf.getvalue()

# ---------- Unit Tests (unchanged core formulas) ----------

def run_unit_tests():
    s1 = amortizing_schedule(1000.0, 0.0, 5)
    assert len(s1) == 5 and abs(sum(r.principal for r in s1) - 1000.0) < 1e-6 and all(abs(r.interest) < 1e-9 for r in s1)
    s2 = interest_only_balloon_schedule(2000.0, 0.1, 3)
    assert len(s2) == 3 and abs(s2[-1].ending_balance) < 1e-9 and all(abs(r.principal) < 1e-9 for r in s2[:-1]) and abs(s2[-1].principal-2000.0) < 1e-9
    s3 = equal_principal_schedule(3000.0, 0.1, 3)
    assert abs(sum(r.principal for r in s3) - 3000.0) < 1e-6
    for s in ("Amortizing", "Interest-Only + Balloon", "Equal Principal"):
        assert isinstance(build_schedule(s, 1000, 0.05, 5), list)
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

    # New equity roll input
    st.markdown("---")
    equity_roll_pct = st.number_input("Equity roll (% of gross kept as equity)", min_value=0.0, max_value=90.0, value=0.0, step=1.0)
    st.caption("Reduces cash proceeds by this percentage of gross; shows a separate 'Equity value retained'.")

    # New handover timing inputs
    st.markdown("---")
    st.markdown("**Handover Timing (weeks)**")
    seller_weeks = st.number_input("Seller finance (weeks)", min_value=0.1, max_value=12.0, value=1.0, step=0.1)
    lump_min = st.number_input("Lump sum — min weeks", min_value=1.0, max_value=52.0, value=12.0, step=1.0)
    lump_max = st.number_input("Lump sum — max weeks", min_value=1.0, max_value=52.0, value=16.0, step=1.0)

    st.markdown("---")
    tax_on = st.checkbox("Tax view (simplified)", value=False)
    if tax_on:
        cost_base = st.number_input("Cost base (A$)", min_value=0.0, value=0.0, step=50_000.0, format="%.0f")
        seller_type = st.selectbox("Seller type", ["Individual (50% CGT discount)"])
        mtr = st.number_input("Marginal tax rate %", min_value=0.0, max_value=60.0, value=47.0, step=1.0) / 100.0
        disc_rate = st.number_input("NPV discount rate %", min_value=0.0, max_value=50.0, value=8.0, step=0.5) / 100.0
    else:
        cost_base = 0.0; seller_type = "Individual (50% CGT discount)"; mtr = 0.47; disc_rate = 0.08

    if st.button("Run unit tests"):
        try: st.success(run_unit_tests())
        except AssertionError as e: st.error(f"Unit tests failed: {e}")

# Base proceeds
lump_gross = headline * (1 - lump_disc)
vf_principal = headline * (1 + vf_prem)

if deposit > vf_principal:
    st.error("Deposit cannot exceed the Vendor Finance principal (headline × (1 + premium)).")
    st.stop()

# Schedule on financed balance after deposit
financed = vf_principal - deposit
schedule = build_schedule(structure, financed, rate, years)
total_interest = sum(r.interest for r in schedule)
vf_gross = vf_principal + total_interest

# Equity roll application (reducing *cash* proceeds)
roll_mult = (1 - equity_roll_pct/100.0)
lump_cash = lump_gross * roll_mult
vf_cash = vf_gross * roll_mult
equity_retained_lump = lump_gross - lump_cash
equity_retained_vf = vf_gross - vf_cash

# Annual payment (Yr 1) for display
annual_payment = schedule[0].payment if schedule else 0.0

# ---------- Top cards ----------
col1, col2, col3 = st.columns([1,1,1])
with col1:
    st.markdown("### Lump Sum (Cash)")
    st.markdown(f"## {aud(lump_cash)}")
    st.caption(f"Gross {aud(lump_gross)} • Equity rolled {equity_roll_pct:.0f}% = {aud(equity_retained_lump)}")
with col2:
    st.markdown("### Vendor Finance (Cash)")
    st.markdown(f"## {aud(vf_cash)}")
    st.caption(f"Gross {aud(vf_gross)} • Equity rolled {equity_roll_pct:.0f}% = {aud(equity_retained_vf)}")
delta_amt = vf_cash - lump_cash
delta_pct = (delta_amt / lump_cash * 100) if lump_cash > 0 else 0.0
with col3:
    st.markdown("### Delta (Cash)")
    st.markdown(f"## +{aud(delta_amt)}  \n≈ +{delta_pct:.1f}%")
    st.caption("Vendor Finance vs Lump Sum (after equity roll)")

# ---------- Charts ----------
left, right = st.columns([1,1])
with left:
    st.markdown("#### Cash Proceeds — Breakdown")
    fig_bar, ax = plt.subplots(figsize=(6,4), dpi=150)
    ax.bar([0], [lump_cash], label="Lump Cash")
    # Stack principal+interest as cash (scaled by roll_mult)
    ax.bar([1], [vf_principal * roll_mult], label="VF Principal (cash)")
    ax.bar([1], [total_interest * roll_mult], bottom=[vf_principal * roll_mult], label="VF Interest (cash)")
    ax.set_xticks([0,1], ["Lump", "Vendor Finance"]); ax.set_ylabel("A$"); ax.grid(True, axis="y", alpha=0.2)
    st.pyplot(fig_bar, use_container_width=True); st.caption("Bars show *cash* after equity roll; VF is stacked principal + interest.")
    plt.close(fig_bar)

with right:
    st.markdown("#### Handover Timing (weeks)")
    fig_t, ax2 = plt.subplots(figsize=(6,4), dpi=150)
    draw_timebar(ax2, seller_weeks, lump_min, lump_max)
    st.pyplot(fig_t, use_container_width=True)
    st.caption("Indicative closing/settlement handover: Seller finance often faster (e.g., 1 week) vs Lump sum (e.g., 12–16 weeks).")
    plt.close(fig_t)

# ---------- Totals & Tax ----------
st.markdown("### Totals")
tot_cols = st.columns([1,1,1,1])
with tot_cols[0]: st.metric("Lump Sum — Cash Proceeds", aud(lump_cash))
with tot_cols[1]: st.metric("Vendor Finance — Cash Proceeds", aud(vf_cash))
with tot_cols[2]: st.metric("Vendor Finance — Total Interest (gross)", aud(total_interest))
with tot_cols[3]: st.metric("Annual payment (Year 1)", aud(annual_payment))

if tax_on:
    results = tax_model(True, lump_gross, vf_principal, schedule, cost_base, mtr, disc_rate, deposit)
    st.markdown("### Tax View (simplified)")
    c = st.columns([1,1,1,1,1,1])
    c[0].metric("CGT at settlement — Lump", aud(results["cgt_tax_lump"]))
    c[1].metric("CGT at settlement — VF", aud(results["cgt_tax_vf"]))
    c[2].metric("Nominal After‑Tax — Lump", aud(results["lump_after_tax_total"]))
    c[3].metric("Nominal After‑Tax — VF", aud(results["vf_after_tax_total"]))
    c[4].metric("NPV After‑Tax — Lump", aud(results["lump_after_tax_npv"]))
    c[5].metric("NPV After‑Tax — VF", aud(results["vf_after_tax_npv"]))
    st.caption("Tax view currently ignores equity roll for CGT. Real deals may use rollover relief/scrip rules—get tax advice.")

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
    "equity_roll_pct": round(equity_roll_pct, 2),
    "seller_weeks": seller_weeks,
    "lump_weeks_min": lump_min,
    "lump_weeks_max": lump_max,
    "tax_on": tax_on,
    "cost_base": cost_base if tax_on else None,
    "marginal_tax_rate_pct": round(mtr*100, 2) if tax_on else None,
    "npv_discount_rate_pct": round(disc_rate*100, 2) if tax_on else None,
}, indent=2).encode("utf-8")

st.download_button("Export CSV (Schedule)", data=csv_data, file_name="payment_schedule.csv", mime="text/csv")
st.download_button("Export XLSX (Schedule)", data=xlsx_buf, file_name="payment_schedule.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# PNG export with new elements
png_bytes = build_slide_image(
    headline=headline,
    lump_cash=lump_cash,
    vf_cash=vf_cash,
    vf_principal=vf_principal,
    total_interest=total_interest,
    equity_roll_pct=equity_roll_pct,
    seller_weeks=seller_weeks,
    lump_min=lump_min,
    lump_max=lump_max
)
st.download_button("Download PNG (Dashboard 1920×1080)", data=png_bytes, file_name="dashboard_export.png", mime="image/png")

st.caption("Charts include descriptive captions. Colors not hard‑coded to improve contrast compatibility.")
