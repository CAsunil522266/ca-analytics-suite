"""Small formatting helpers (Indian number system)."""
from __future__ import annotations

import pandas as pd


def inr(x, dec: int = 2, sym: bool = False) -> str:
    """Format a number the Indian way: 12,34,567.50 (optionally with the rupee sign)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return ""
    if x != x:  # NaN
        return ""
    x = round(x, dec)
    if x == 0:
        x = 0.0
    neg = x < 0
    whole, _, frac = f"{abs(x):.{dec}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    out = whole + ("." + frac if dec else "")
    return ("-" if neg else "") + ("₹" if sym else "") + out


def inr_short(x) -> str:
    """₹1.25 Cr / ₹3.40 L / ₹8,500."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return ""
    if x != x:
        return ""
    a, sign = abs(x), "-" if x < 0 else ""
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.2f} L"
    return f"{sign}₹{a:,.0f}"


def fdate(x) -> str:
    return "" if pd.isna(x) else pd.Timestamp(x).strftime("%d-%m-%Y")


def pct(x, d: int = 1) -> str:
    return "" if x is None or x != x else f"{x:.{d}f}%"
