"""MIS engine: monthly P&L, KPIs, budget variance, cost anomalies, receivables ageing, forecast, commentary."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from utils import inr_short

GROUPS = ["Revenue", "COGS", "Opex", "Other Income", "Finance Cost", "Depreciation"]
AMT = ["Revenue", "COGS", "Gross Profit", "Opex", "EBITDA", "Other Income", "Depreciation", "Finance Cost", "PBT"]
BUCKETS = ["Not due", "1-30 days", "31-60 days", "61-90 days", "90+ days"]


def _per(x) -> pd.Period:
    return x if isinstance(x, pd.Period) else pd.Period(str(x)[:7], freq="M")


# ------------------------------------------------------------------ data prep
def prepare_ledger(df: pd.DataFrame):
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"], dayfirst=True, errors="coerce")
    d["amount"] = pd.to_numeric(d["amount"], errors="coerce")
    d["group"] = d["group"].astype(str).str.strip()
    for c in ("head", "party"):
        d[c] = d[c].fillna("").astype(str) if c in d else ""
    d = d.dropna(subset=["date", "amount"])
    unknown = sorted(set(d["group"]) - set(GROUPS))
    d = d[d["group"].isin(GROUPS)].copy()
    d["month"] = d["date"].dt.to_period("M")
    return d.reset_index(drop=True), unknown


def monthly_pnl(ledger: pd.DataFrame) -> pd.DataFrame:
    p = ledger.pivot_table(index="month", columns="group", values="amount", aggfunc="sum", fill_value=0.0)
    p = p.reindex(pd.period_range(p.index.min(), p.index.max(), freq="M"), fill_value=0.0)
    for g in GROUPS:
        if g not in p:
            p[g] = 0.0
    o = pd.DataFrame(index=p.index)
    o["Revenue"], o["COGS"] = p["Revenue"], p["COGS"]
    o["Gross Profit"] = o["Revenue"] - o["COGS"]
    o["Opex"] = p["Opex"]
    o["EBITDA"] = o["Gross Profit"] - o["Opex"]
    o["Other Income"], o["Depreciation"], o["Finance Cost"] = p["Other Income"], p["Depreciation"], p["Finance Cost"]
    o["PBT"] = o["EBITDA"] + o["Other Income"] - o["Depreciation"] - o["Finance Cost"]
    rev = o["Revenue"].replace(0, np.nan)
    o["GP %"], o["EBITDA %"], o["PBT %"] = (o["Gross Profit"] / rev * 100, o["EBITDA"] / rev * 100, o["PBT"] / rev * 100)
    return o


def _sum(pnl, months) -> pd.Series:
    s = pnl.loc[list(months), AMT].sum()
    rev = s["Revenue"] or np.nan
    s["GP %"], s["EBITDA %"], s["PBT %"] = (s["Gross Profit"] / rev * 100, s["EBITDA"] / rev * 100, s["PBT"] / rev * 100)
    return s


def kpi_table(pnl, months) -> pd.DataFrame:
    months = list(months)
    cur = _sum(pnl, months)
    prior_m = list(pd.period_range(end=months[0] - 1, periods=len(months), freq="M"))
    prior = _sum(pnl, prior_m) if all(m in pnl.index for m in prior_m) else None
    rows = []
    for m, kind in [("Revenue", "amt"), ("Gross Profit", "amt"), ("GP %", "pct"), ("Opex", "amt"), ("EBITDA", "amt"),
                    ("EBITDA %", "pct"), ("PBT", "amt"), ("PBT %", "pct")]:
        pv = float(prior[m]) if prior is not None else np.nan
        cv = float(cur[m])
        chg = (cv - pv) if kind == "pct" else ((cv / pv - 1) * 100 if pv else np.nan)
        rows.append({"metric": m, "current": cv, "prior": pv, "change": chg, "kind": kind})
    return pd.DataFrame(rows)


def variance(pnl, budget, months):
    if budget is None or len(budget) == 0:
        return None
    b = budget.copy()
    b["month"] = pd.to_datetime(b["month"].astype(str)).dt.to_period("M")
    b = b[b["month"].isin(list(months))]
    if b.empty:
        return None
    bs = b.groupby("group")["budget"].sum()
    act = pnl.loc[list(months), AMT].sum()
    bud = {"Revenue": float(bs.get("Revenue", 0.0)), "COGS": float(bs.get("COGS", 0.0)), "Opex": float(bs.get("Opex", 0.0))}
    bud["Gross Profit"] = bud["Revenue"] - bud["COGS"]
    bud["EBITDA"] = bud["Gross Profit"] - bud["Opex"]
    rows = []
    for m, hi in [("Revenue", True), ("COGS", False), ("Gross Profit", True), ("Opex", False), ("EBITDA", True)]:
        a, bb = float(act[m]), bud[m]
        v = a - bb
        rows.append({"line": m, "actual": a, "budget": bb, "variance": v, "var_pct": v / bb * 100 if bb else np.nan,
                     "status": "Favourable" if (v >= 0) == hi else "Adverse"})
    return pd.DataFrame(rows)


def find_anomalies(ledger, groups=("Opex", "COGS"), thr: float = 3.5, min_ratio: float = 1.3) -> pd.DataFrame:
    cols = ["head", "month", "amount", "typical", "times_typical", "score"]
    d = ledger[ledger["group"].isin(groups)]
    if d.empty:
        return pd.DataFrame(columns=cols)
    pv = d.pivot_table(index="month", columns="head", values="amount", aggfunc="sum", fill_value=0.0)
    out = []
    for head in pv.columns:
        s = pv[head]
        if len(s) < 6:
            continue
        med = float(s.median())
        mad = float((s - med).abs().median())
        if mad == 0:
            continue
        z = 0.6745 * (s - med) / mad
        for m, zz in z.items():
            if zz > thr and med and s[m] / med >= min_ratio:
                out.append({"head": head, "month": m, "amount": float(s[m]), "typical": med,
                            "times_typical": float(s[m] / med) if med else np.nan, "score": float(zz)})
    return pd.DataFrame(out, columns=cols).sort_values("score", ascending=False).reset_index(drop=True)


def opex_trend(ledger, months) -> pd.DataFrame:
    d = ledger[(ledger["group"].isin(["Opex"])) & (ledger["month"].isin(list(months)))]
    return d.pivot_table(index="head", columns="month", values="amount", aggfunc="sum", fill_value=0.0)


def customer_table(ledger, months):
    d = ledger[(ledger["group"] == "Revenue") & (ledger["month"].isin(list(months)))]
    rev = d.groupby("party")["amount"].sum().sort_values(ascending=False)
    rev.index = [i or "(unspecified)" for i in rev.index]
    t = pd.DataFrame({"revenue": rev})
    tot = t["revenue"].sum()
    t["share"] = t["revenue"] / tot * 100 if tot else 0.0
    t["cum_share"] = t["share"].cumsum()
    return t, {"top1": float(t["share"].iloc[0]) if len(t) else 0.0, "top3": float(t["share"].head(3).sum()),
               "hhi": float(((t["share"] / 100) ** 2).sum() * 10000)}


def ageing(invoices, asof, revenue_3m=None):
    if invoices is None or len(invoices) == 0:
        return None
    inv = invoices.copy()
    for c in ("invoice_date", "due_date"):
        inv[c] = pd.to_datetime(inv[c], dayfirst=True, errors="coerce")
    inv["amount"] = pd.to_numeric(inv["amount"], errors="coerce").fillna(0.0)
    inv["received"] = pd.to_numeric(inv["received"], errors="coerce").fillna(0.0) if "received" in inv else 0.0
    inv["outstanding"] = inv["amount"] - inv["received"]
    inv = inv[inv["outstanding"] > 0.5].copy()
    if inv.empty:
        return None
    inv["days_overdue"] = (pd.Timestamp(asof) - inv["due_date"]).dt.days

    def bucket(d):
        return BUCKETS[0] if d <= 0 else BUCKETS[1] if d <= 30 else BUCKETS[2] if d <= 60 else BUCKETS[3] if d <= 90 else BUCKETS[4]

    inv["bucket"] = pd.Categorical(inv["days_overdue"].map(bucket), categories=BUCKETS, ordered=True)
    by = inv.pivot_table(index="customer", columns="bucket", values="outstanding", aggfunc="sum", fill_value=0.0, observed=False)
    by = by.reindex(columns=BUCKETS, fill_value=0.0)
    by["Total"] = by.sum(axis=1)
    by = by.sort_values("Total", ascending=False)
    totals = by[BUCKETS].sum()
    total = float(totals.sum())
    return {"detail": inv.sort_values("days_overdue", ascending=False), "by_customer": by, "totals": totals, "total": total,
            "overdue": float(totals[BUCKETS[1:]].sum()), "over60": float(totals[BUCKETS[3:]].sum()),
            "dso": total / revenue_3m * 90 if revenue_3m else np.nan}


def forecast(pnl, horizon: int = 3, lookback: int = 12):
    s = pnl["Revenue"].iloc[-lookback:]
    if len(s) < 6:
        return None
    x = np.arange(len(s))
    coef = np.polyfit(x, s.values, 1)
    sd = float(np.std(s.values - np.polyval(coef, x), ddof=2))
    f = np.polyval(coef, np.arange(len(s), len(s) + horizon))
    return pd.DataFrame({"month": pd.period_range(s.index[-1] + 1, periods=horizon, freq="M"), "forecast": f,
                         "low": f - 1.28 * sd, "high": f + 1.28 * sd})


# ------------------------------------------------------------------ orchestration
@dataclass
class MISResult:
    company: str
    months: list
    pnl: pd.DataFrame
    kpis: pd.DataFrame
    variance: pd.DataFrame | None
    budget_rev: pd.Series | None
    opex: pd.DataFrame
    anomalies: pd.DataFrame
    customers: pd.DataFrame
    conc: dict
    ageing: dict | None
    forecast: pd.DataFrame | None
    insights: list
    ledger: pd.DataFrame
    asof: pd.Timestamp
    unknown_groups: list


def run_mis(ledger, budget=None, invoices=None, asof=None, start=None, end=None, company="") -> MISResult:
    led, unknown = prepare_ledger(ledger)
    pnl = monthly_pnl(led)
    s = _per(start) if start is not None else pnl.index.min()
    e = _per(end) if end is not None else pnl.index.max()
    months = list(pd.period_range(s, e, freq="M"))
    months = [m for m in months if m in pnl.index]
    asof = pd.Timestamp(asof) if asof is not None else led["date"].max()
    kp = kpi_table(pnl, months)
    var = variance(pnl, budget, months)
    brev = None
    if budget is not None and len(budget):
        bb = budget[budget["group"] == "Revenue"].copy()
        bb["month"] = pd.to_datetime(bb["month"].astype(str)).dt.to_period("M")
        brev = bb.set_index("month")["budget"]
    an = find_anomalies(led[led["month"].isin(months)])
    cust, conc = customer_table(led, months)
    rev3 = float(pnl.loc[:months[-1], "Revenue"].iloc[-3:].sum())
    age = ageing(invoices, asof, rev3) if invoices is not None else None
    fc = forecast(pnl.loc[:months[-1]])
    res = MISResult(company, months, pnl, kp, var, brev, opex_trend(led, months), an, cust, conc, age, fc, [], led, asof, unknown)
    res.insights = insights(res)
    return res


def insights(r: MISResult) -> list:
    out = []
    k = r.kpis.set_index("metric")
    label = f"{r.months[0].strftime('%b-%y')} to {r.months[-1].strftime('%b-%y')}"
    rev = k.loc["Revenue"]
    line = f"Revenue for {label} was {inr_short(rev['current'])}"
    if rev["prior"] == rev["prior"]:
        line += f", {rev['change']:+.1f}% against the preceding {len(r.months)}-month period"
    out.append(line + ".")
    gp, eb = k.loc["GP %"], k.loc["EBITDA %"]
    t = f"Gross margin is {gp['current']:.1f}% and EBITDA margin {eb['current']:.1f}%"
    if gp["prior"] == gp["prior"]:
        t += f" ({gp['change']:+.1f} pp and {eb['change']:+.1f} pp vs prior period)"
    out.append(t + ".")
    if r.variance is not None:
        v = r.variance.set_index("line")
        rv, ob = v.loc["Revenue"], v.loc["Opex"]
        out.append(f"Against budget, revenue is {rv['var_pct']:+.1f}% ({rv['status'].lower()}) and operating expenses "
                   f"{ob['var_pct']:+.1f}% ({ob['status'].lower()}); EBITDA is {inr_short(abs(v.loc['EBITDA', 'variance']))} "
                   f"{'above' if v.loc['EBITDA', 'variance'] >= 0 else 'below'} budget.")
    for _, a in r.anomalies.head(3).iterrows():
        out.append(f"Cost spike: {a['head']} in {a['month'].strftime('%b-%y')} was {inr_short(a['amount'])}, "
                   f"{a['times_typical']:.1f}x its usual {inr_short(a['typical'])} - review the vouchers.")
    if r.conc["top1"] >= 25 or r.conc["top3"] >= 60:
        out.append(f"Customer concentration: top customer is {r.conc['top1']:.0f}% of revenue and top 3 are {r.conc['top3']:.0f}% - "
                   "dependence risk.")
    if r.ageing:
        a = r.ageing
        out.append(f"Receivables stand at {inr_short(a['total'])} (DSO about {a['dso']:.0f} days); {inr_short(a['overdue'])} is overdue, "
                   f"of which {inr_short(a['over60'])} is more than 60 days past due.")
    if r.forecast is not None:
        out.append(f"A simple trend projection puts revenue near {inr_short(r.forecast['forecast'].mean())} a month for the next "
                   f"{len(r.forecast)} months (indicative only - ignores seasonality).")
    return out
