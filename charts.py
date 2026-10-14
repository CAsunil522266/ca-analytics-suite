"""Matplotlib charts shared by the PDF reports and the Streamlit app."""
from __future__ import annotations

import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter

from mis_engine import BUCKETS
from utils import inr_short

NAVY, TEAL, ORANGE, GOLD, GREY, BLUE, RED = "#1f3a5f", "#2a9d8f", "#e76f51", "#e9c46a", "#6b7280", "#457b9d", "#c62828"


def _clean(ax, grid="y"):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(labelsize=8)
    ax.grid(axis=grid, alpha=0.25)
    ax.set_axisbelow(True)


def _money_axis(ax, axis="x"):
    f = FuncFormatter(lambda v, _: inr_short(v))
    (ax.xaxis if axis == "x" else ax.yaxis).set_major_formatter(f)


def _empty(msg, size=(10, 2.6)):
    fig, ax = plt.subplots(figsize=size)
    ax.axis("off")
    ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=11, color=GREY)
    return fig


# ------------------------------------------------------------------ reconciliation
def bank_exceptions_chart(res):
    cats = []
    for df in (res.books_only, res.bank_only):
        for cat, g in df.groupby("category"):
            cats.append((cat, len(g), float(g["amount"].abs().sum())))
    mm = res.matched[res.matched["match_type"].str.startswith("3")]
    if len(mm):
        cats.append(("Amount differences (same ref)", len(mm), float(mm["diff"].abs().sum())))
    if not cats:
        return _empty("No exceptions - books and bank agree")
    labels = [textwrap.fill(c[0], 30) for c in cats]
    fig, ax = plt.subplots(1, 2, figsize=(10, 0.55 * len(cats) + 1.4))
    ax[0].barh(labels, [c[1] for c in cats], color=ORANGE)
    ax[0].set_title("Open exceptions - number", fontsize=9, loc="left")
    ax[1].barh(labels, [c[2] for c in cats], color=NAVY)
    ax[1].set_title("Open exceptions - value", fontsize=9, loc="left")
    ax[1].set_yticklabels([])
    _money_axis(ax[1])
    for a in ax:
        a.invert_yaxis()
        _clean(a, "x")
    fig.tight_layout()
    return fig


def gst_itc_chart(res):
    s = res.summary
    fig, ax = plt.subplots(figsize=(10, 2.7))
    left = 0
    for name, v, c in [("Eligible (matched in 2B)", s["eligible"], TEAL), ("Excess over 2B (value mismatch)", s["excess"], GOLD),
                       ("Not in GSTR-2B", s["pr_only"], ORANGE), ("Duplicate in books", s["dup"], GREY)]:
        ax.barh(1, v, left=left, color=c, label=name)
        left += v
    left = 0
    for name, v, c, lab in [("", s["eligible"], TEAL, None), ("In 2B, not in books", s["g_only"], BLUE, "In GSTR-2B, not in books")]:
        ax.barh(0, v, left=left, color=c, label=lab)
        left += v
    ax.set_yticks([0, 1], ["As per GSTR-2B", "As per books"])
    _money_axis(ax)
    _clean(ax, "x")
    ax.legend(fontsize=7.5, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.18), frameon=False)
    ax.set_title("Input tax credit: books vs GSTR-2B", fontsize=9, loc="left")
    fig.tight_layout()
    return fig


def gst_status_chart(res):
    sc = res.summary["status_counts"]
    colors = {"Matched": TEAL, "Value mismatch": GOLD, "Probable match (invoice no. differs)": BLUE,
              "In books, not in GSTR-2B": ORANGE, "In GSTR-2B, not in books": NAVY, "Duplicate in purchase register": GREY}
    labels = [textwrap.fill(k, 28) for k in sc]
    fig, ax = plt.subplots(figsize=(10, 0.5 * len(sc) + 1.2))
    bars = ax.barh(labels, list(sc.values()), color=[colors.get(k, GREY) for k in sc])
    for b in bars:
        ax.text(b.get_width(), b.get_y() + b.get_height() / 2, f" {int(b.get_width())}", va="center", fontsize=8)
    ax.invert_yaxis()
    ax.set_title("Invoices by reconciliation status", fontsize=9, loc="left")
    _clean(ax, "x")
    fig.tight_layout()
    return fig


# ------------------------------------------------------------------ MIS
def _lbl(periods):
    return [p.strftime("%b-%y") for p in periods]


def revenue_trend_chart(r):
    pnl = r.pnl.loc[r.months[0]:r.months[-1]]
    x = _lbl(pnl.index)
    fig, ax = plt.subplots(figsize=(6.6, 3.1))
    ax.bar(x, pnl["Revenue"], color=NAVY, label="Revenue", width=0.65)
    if r.forecast is not None:
        ax.bar(_lbl(r.forecast["month"]), r.forecast["forecast"], color=NAVY, alpha=0.35, hatch="//", label="Forecast", width=0.65)
        x = x + _lbl(r.forecast["month"])
    if r.budget_rev is not None:
        b = r.budget_rev.reindex(pnl.index)
        if b.notna().any():
            ax.plot(_lbl(pnl.index), b.values, color=ORANGE, marker="o", ms=3, lw=1.6, label="Budget")
    _money_axis(ax, "y")
    _clean(ax)
    ax.tick_params(axis="x", rotation=45)
    ax2 = ax.twinx()
    ax2.plot(_lbl(pnl.index), pnl["EBITDA %"], color=TEAL, marker="s", ms=3, lw=1.6, label="EBITDA %")
    ax2.set_ylim(0, max(30, float(np.nanmax(pnl["EBITDA %"])) * 1.4))
    ax2.tick_params(labelsize=8)
    ax2.spines["top"].set_visible(False)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=7, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.16), frameon=False)
    fig.tight_layout()
    return fig


def cost_mix_chart(r):
    d = r.ledger[(r.ledger["group"].isin(["COGS", "Opex", "Finance Cost"])) & (r.ledger["month"].isin(r.months))]
    t = d.groupby(["group", "head"])["amount"].sum().reset_index().sort_values("amount", ascending=False).head(10)
    col = {"COGS": NAVY, "Opex": TEAL, "Finance Cost": GOLD}
    fig, ax = plt.subplots(figsize=(6.6, 3.1))
    ax.barh([textwrap.shorten(h, 26) for h in t["head"]], t["amount"], color=[col[g] for g in t["group"]])
    ax.invert_yaxis()
    _money_axis(ax)
    _clean(ax, "x")
    ax.set_title("Top cost heads (navy = COGS, green = Opex, gold = finance)", fontsize=8.5, loc="left")
    fig.tight_layout()
    return fig


def customer_chart(r):
    t = r.customers.head(6)
    fig, ax = plt.subplots(figsize=(6.6, 3.1))
    bars = ax.barh([textwrap.shorten(i, 24) for i in t.index], t["share"], color=BLUE)
    for b, v in zip(bars, t["share"]):
        ax.text(b.get_width(), b.get_y() + b.get_height() / 2, f" {v:.1f}%", va="center", fontsize=8)
    ax.invert_yaxis()
    _clean(ax, "x")
    ax.set_xlim(0, max(40, float(t["share"].max()) * 1.2))
    ax.set_title(f"Revenue share by customer (top 3 = {r.conc['top3']:.0f}%)", fontsize=8.5, loc="left")
    fig.tight_layout()
    return fig


def ageing_chart(r):
    if not r.ageing:
        return _empty("No receivables data provided", (6.6, 3.1))
    t = r.ageing["totals"]
    fig, ax = plt.subplots(figsize=(6.6, 3.1))
    bars = ax.bar(BUCKETS, t.values, color=[TEAL, GOLD, "#f4a261", ORANGE, RED])
    for b, v in zip(bars, t.values):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height(), inr_short(v), ha="center", va="bottom", fontsize=7.5)
    _money_axis(ax, "y")
    _clean(ax)
    ax.set_title(f"Receivables ageing (total {inr_short(r.ageing['total'])}, DSO {r.ageing['dso']:.0f} days)", fontsize=8.5, loc="left")
    fig.tight_layout()
    return fig
