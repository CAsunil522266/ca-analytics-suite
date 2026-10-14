"""Reconciliation engines (pandas): bank vs books, and GSTR-2B vs purchase register."""
from __future__ import annotations

import difflib
import itertools
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

TAX = ["igst", "cgst", "sgst", "cess"]
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def _num(s: pd.Series) -> pd.Series:
    s = (s.astype(str).str.replace(",", "", regex=False).str.replace("₹", "", regex=False)
         .str.replace(r"^\((.*)\)$", r"-\1", regex=True).str.strip())
    return pd.to_numeric(s, errors="coerce")


# =========================================================================== BANK vs BOOKS
def standardize_txn(df, date, amount=None, debit=None, credit=None, ref=None, desc=None,
                    inflow="credit", dayfirst=True) -> pd.DataFrame:
    """Normalise a bank statement / bank ledger to: date, ref, desc, amount (+ve = money IN to the bank).

    inflow: which column increases the bank balance. 'credit' for a bank statement (deposits are credits),
            'debit' for the bank ledger in the books (receipts are debits).
    """
    out = pd.DataFrame({"date": pd.to_datetime(df[date], dayfirst=dayfirst, errors="coerce")})
    out["ref"] = df[ref].astype(str).str.strip().replace({"nan": "", "None": ""}) if ref else ""
    out["desc"] = df[desc].astype(str).str.strip() if desc else ""
    if amount:
        out["amount"] = _num(df[amount])
    else:
        d = _num(df[debit]).fillna(0.0) if debit else 0.0
        c = _num(df[credit]).fillna(0.0) if credit else 0.0
        out["amount"] = (c - d) if inflow == "credit" else (d - c)
    out = out.dropna(subset=["date", "amount"])
    return out[out["amount"] != 0].reset_index(drop=True)


def _nref(x) -> str:
    s = re.sub(r"[^A-Z0-9]", "", str(x).upper())
    return "" if s in ("NAN", "NONE") else re.sub(r"(?<!\d)0+(?=\d)", "", s)


def _subset(target, pool, amt, maxn):
    for r in range(2, maxn + 1):
        for combo in itertools.combinations(pool, r):
            if sum(amt[j] for j in combo) == target:
                return combo
    return None


_BANK_RULES = [
    (r"CHARG|CHG|FEE|SMS|GST ON|PENAL|AMC", "Bank charges not in books"),
    (r"INTEREST|INT\.? ?(PD|CR)", "Interest credited by bank, not in books"),
    (r"NACH|ECS|EMI|AUTO ?DEBIT|SI DEBIT|MANDATE", "Direct debit (ECS/NACH/EMI) not in books"),
]

_MATCH_COLS = ["match_type", "books_date", "books_ref", "books_desc", "books_amount",
               "bank_date", "bank_ref", "bank_desc", "bank_amount"]


@dataclass
class BankRecon:
    matched: pd.DataFrame
    books_only: pd.DataFrame
    bank_only: pd.DataFrame
    brs: pd.DataFrame        # section, particulars, amount, strong
    summary: dict


def reconcile_bank(books: pd.DataFrame, bank: pd.DataFrame, opening_books: float = 0.0,
                   opening_bank: float = 0.0, window: int = 5, max_group: int = 3) -> BankRecon:
    b = books.reset_index(drop=True).copy()
    k = bank.reset_index(drop=True).copy()
    for d in (b, k):
        d["_ref"] = d["ref"].map(_nref)
        d["_c"] = (d["amount"] * 100).round().astype("int64")
    ub, uk = set(b.index), set(k.index)
    pairs: list = []

    def gap(i, j):
        return abs((b.at[i, "date"] - k.at[j, "date"]).days)

    def take(kind, bi, ki):
        pairs.append((kind, list(bi), list(ki)))
        ub.difference_update(bi)
        uk.difference_update(ki)

    korder = list(k.sort_values("date").index)
    # 1. same reference + same amount
    for j in korder:
        if j in uk and k.at[j, "_ref"]:
            c = [i for i in ub if b.at[i, "_ref"] == k.at[j, "_ref"] and b.at[i, "_c"] == k.at[j, "_c"]]
            if c:
                take("1. Exact (ref + amount)", [min(c, key=lambda i: gap(i, j))], [j])
    # 2. same amount, dates within window
    for j in korder:
        if j in uk:
            c = [i for i in ub if b.at[i, "_c"] == k.at[j, "_c"] and gap(i, j) <= window]
            if c:
                take("2. Amount + date window", [min(c, key=lambda i: gap(i, j))], [j])
    # 3. same reference, different amount -> difference to be explained
    for j in korder:
        if j in uk and k.at[j, "_ref"]:
            c = [i for i in ub if b.at[i, "_ref"] == k.at[j, "_ref"] and np.sign(b.at[i, "_c"]) == np.sign(k.at[j, "_c"])]
            if c:
                take("3. Amount difference (same ref)", [c[0]], [j])
    # 4. several book entries = one bank entry (e.g. cheques banked together)
    bamt, kamt = b["_c"].to_dict(), k["_c"].to_dict()
    for j in sorted(uk, key=lambda x: k.at[x, "date"]):
        pool = sorted([i for i in ub if np.sign(bamt[i]) == np.sign(kamt[j])
                       and -window <= (k.at[j, "date"] - b.at[i, "date"]).days <= window + 3], key=lambda i: gap(i, j))[:15]
        combo = _subset(kamt[j], pool, bamt, max_group) if len(pool) >= 2 else None
        if combo:
            take("4. Grouped (many books : 1 bank)", combo, [j])
    # 5. one book entry = several bank entries
    for i in sorted(ub, key=lambda x: b.at[x, "date"]):
        pool = sorted([j for j in uk if np.sign(kamt[j]) == np.sign(bamt[i])
                       and -window - 3 <= (k.at[j, "date"] - b.at[i, "date"]).days <= window + 3], key=lambda j: gap(i, j))[:15]
        combo = _subset(bamt[i], pool, kamt, max_group) if len(pool) >= 2 else None
        if combo:
            take("5. Grouped (1 books : many bank)", [i], combo)

    recs = []
    for typ, bi, ki in pairs:
        bs, ks = b.loc[bi], k.loc[ki]
        recs.append({
            "match_type": typ,
            "books_date": bs["date"].min(), "books_ref": ", ".join(x for x in bs["ref"] if x),
            "books_desc": "; ".join(bs["desc"])[:70], "books_amount": round(bs["amount"].sum(), 2),
            "bank_date": ks["date"].min(), "bank_ref": ", ".join(x for x in ks["ref"] if x),
            "bank_desc": "; ".join(ks["desc"])[:70], "bank_amount": round(ks["amount"].sum(), 2),
        })
    matched = pd.DataFrame(recs, columns=_MATCH_COLS)
    for c in ("books_date", "bank_date"):
        matched[c] = pd.to_datetime(matched[c])
    for c in ("books_amount", "bank_amount"):
        matched[c] = matched[c].astype(float)
    matched["diff"] = (matched["bank_amount"] - matched["books_amount"]).round(2)
    matched["days_gap"] = (matched["bank_date"] - matched["books_date"]).dt.days

    # ---- exceptions
    mk = {(b.at[i, "_ref"], b.at[i, "_c"]) for _, bi, _ in pairs for i in bi if b.at[i, "_ref"]}
    bo = b.loc[sorted(ub), ["date", "ref", "desc", "amount"]].copy()
    cats = []
    for i in sorted(ub):
        if b.at[i, "_ref"] and (b.at[i, "_ref"], b.at[i, "_c"]) in mk:
            cats.append("Possible duplicate entry in books")
        elif b.at[i, "amount"] > 0:
            cats.append("Deposit in transit (not yet credited by bank)")
        else:
            cats.append("Cheque issued, not yet presented")
    bo["category"] = pd.Series(cats, index=bo.index, dtype=str)
    bo = bo.reset_index(drop=True)

    ko = k.loc[sorted(uk), ["date", "ref", "desc", "amount"]].copy()
    kcats = []
    for _, r in ko.iterrows():
        label = None
        for pat, name in _BANK_RULES:
            if re.search(pat, str(r["desc"]).upper()):
                label = name
                break
        kcats.append(label or ("Receipt in bank, not in books" if r["amount"] > 0 else "Payment in bank, not in books"))
    ko["category"] = pd.Series(kcats, index=ko.index, dtype=str)
    ko = ko.reset_index(drop=True)

    # ---- bank reconciliation statement
    bal_books = round(opening_books + b["amount"].sum(), 2)
    bal_bank = round(opening_bank + k["amount"].sum(), 2)
    mism = float(matched.loc[matched["match_type"].str.startswith("3"), "diff"].sum())
    dupmask = bo["category"].str.startswith("Possible duplicate")
    dup_signed = float(bo.loc[dupmask, "amount"].sum())
    lines = [("A. Adjusting the books balance", "Balance as per books (bank ledger)", bal_books, True)]
    adj_b = bal_books
    for cat, v in ko.groupby("category")["amount"].sum().items():
        lines.append(("A. Adjusting the books balance", f"Add/(Less): {cat}", round(v, 2), False))
        adj_b += v
    if abs(mism) > 0.004:
        lines.append(("A. Adjusting the books balance", "Add/(Less): Amount differences, bank vs books (correct books)", round(mism, 2), False))
        adj_b += mism
    if abs(dup_signed) > 0.004:
        lines.append(("A. Adjusting the books balance", "Add/(Less): Reverse duplicate entries in books", round(-dup_signed, 2), False))
        adj_b -= dup_signed
    adj_b = round(adj_b, 2)
    lines.append(("A. Adjusting the books balance", "Adjusted books balance", adj_b, True))

    lines.append(("B. Adjusting the bank balance", "Balance as per bank statement", bal_bank, True))
    adj_k = bal_bank
    for cat, v in bo[~dupmask].groupby("category")["amount"].sum().items():
        lines.append(("B. Adjusting the bank balance", f"Add/(Less): {cat}", round(v, 2), False))
        adj_k += v
    adj_k = round(adj_k, 2)
    lines.append(("B. Adjusting the bank balance", "Adjusted bank balance", adj_k, True))
    diff = round(adj_b - adj_k, 2)
    lines.append(("C. Check", "Difference between adjusted balances (should be nil)", diff, True))
    brs = pd.DataFrame(lines, columns=["section", "particulars", "amount", "strong"])

    tb, tk = float(b["amount"].abs().sum()), float(k["amount"].abs().sum())
    nb_m = sum(len(bi) for _, bi, _ in pairs)
    nk_m = sum(len(ki) for _, _, ki in pairs)
    summary = {
        "n_books": len(b), "n_bank": len(k), "n_pairs": len(pairs),
        "books_matched": nb_m, "bank_matched": nk_m,
        "pct_books_matched": 100 * nb_m / max(len(b), 1), "pct_bank_matched": 100 * nk_m / max(len(k), 1),
        "pct_value_matched": 100 * float(matched["books_amount"].abs().sum()) / tb if tb else 0.0,
        "n_books_only": len(bo), "n_bank_only": len(ko), "n_mismatch": int(matched["match_type"].str.startswith("3").sum()),
        "n_exceptions": len(bo) + len(ko) + int(matched["match_type"].str.startswith("3").sum()),
        "bal_books": bal_books, "bal_bank": bal_bank, "adj_books": adj_b, "adj_bank": adj_k,
        "difference": diff, "tie": abs(diff) < 0.01,
        "by_type": matched["match_type"].value_counts().sort_index().to_dict(),
    }
    return BankRecon(matched, bo, ko, brs, summary)


# =========================================================================== GSTR-2B vs PURCHASE REGISTER
def norm_inv(x) -> str:
    s = re.sub(r"[^A-Z0-9]", "", str(x).upper())
    return re.sub(r"(?<!\d)0+(?=\d)", "", s)


def standardize_gst(df: pd.DataFrame, m: dict) -> pd.DataFrame:
    """m maps target column -> source column. Required: gstin, invoice_no, invoice_date, taxable."""
    out = pd.DataFrame({"gstin": df[m["gstin"]].astype(str).str.strip().str.upper()})
    out["supplier"] = df[m["supplier"]].astype(str).str.strip() if m.get("supplier") else ""
    out["invoice_no"] = df[m["invoice_no"]].astype(str).str.strip()
    out["invoice_date"] = pd.to_datetime(df[m["invoice_date"]], dayfirst=True, errors="coerce")
    out["taxable"] = _num(df[m["taxable"]]).fillna(0.0)
    for t in TAX:
        out[t] = _num(df[m[t]]).fillna(0.0) if m.get(t) else 0.0
    return out.reset_index(drop=True)


@dataclass
class GstRecon:
    detail: pd.DataFrame
    suppliers: pd.DataFrame
    summary: dict


_DETAIL_COLS = ["status", "gstin", "supplier", "pr_invoice", "pr_date", "pr_taxable", "pr_tax",
                "g_invoice", "g_date", "g_taxable", "g_tax", "diff_taxable", "diff_tax", "itc_at_risk", "remark"]

S_MATCH, S_VALUE = "Matched", "Value mismatch"
S_FUZZY, S_PRONLY = "Probable match (invoice no. differs)", "In books, not in GSTR-2B"
S_GONLY, S_DUP = "In GSTR-2B, not in books", "Duplicate in purchase register"


def reconcile_gst(pr: pd.DataFrame, g2b: pd.DataFrame, tol: float = 1.0, fuzzy: float = 0.70) -> GstRecon:
    p = pr.reset_index(drop=True).copy()
    g = g2b.reset_index(drop=True).copy()
    for d in (p, g):
        d["_inv"] = d["invoice_no"].map(norm_inv)
        d["tax"] = d[TAX].sum(axis=1).round(2)
        d["valid"] = d["gstin"].map(lambda x: bool(GSTIN_RE.match(str(x))))
    p["dup"] = p.duplicated(["gstin", "_inv"], keep="first")
    up, ug = set(p.index[~p["dup"]]), set(g.index)
    pairs = []

    gmap: dict = {}
    for j in g.index:
        gmap.setdefault((g.at[j, "gstin"], g.at[j, "_inv"]), []).append(j)
    for i in sorted(up):  # pass 1: GSTIN + normalised invoice number
        cand = [j for j in gmap.get((p.at[i, "gstin"], p.at[i, "_inv"]), []) if j in ug]
        if cand:
            pairs.append((i, cand[0], "exact"))
            up.discard(i)
            ug.discard(cand[0])
    for i in sorted(up):  # pass 2: same supplier, same taxable value, similar number or date
        best = None
        for j in ug:
            if g.at[j, "gstin"] != p.at[i, "gstin"]:
                continue
            if abs(g.at[j, "taxable"] - p.at[i, "taxable"]) > max(tol, 0.005 * abs(p.at[i, "taxable"])):
                continue
            r = difflib.SequenceMatcher(None, p.at[i, "_inv"], g.at[j, "_inv"]).ratio()
            di, dj = p.at[i, "invoice_date"], g.at[j, "invoice_date"]
            dd = abs((dj - di).days) if not (pd.isna(di) or pd.isna(dj)) else 999
            if r >= fuzzy or dd <= 3:
                score = r - dd / 1000
                if best is None or score > best[0]:
                    best = (score, j)
        if best:
            pairs.append((i, best[1], "fuzzy"))
            up.discard(i)
            ug.discard(best[1])

    rows = []
    n_period = 0
    for i, j, how in pairs:
        dtx = round(p.at[i, "taxable"] - g.at[j, "taxable"], 2)
        dtax = round(p.at[i, "tax"] - g.at[j, "tax"], 2)
        ok = abs(dtx) <= tol and all(abs(p.at[i, t] - g.at[j, t]) <= tol for t in TAX)
        status = (S_MATCH if ok else S_VALUE) if how == "exact" else S_FUZZY
        rem = []
        if how == "fuzzy" and not ok:
            rem.append("value also differs")
        if not ok:
            rem.append(f"taxable diff {dtx:,.2f}; tax diff {dtax:,.2f}")
        di, dj = p.at[i, "invoice_date"], g.at[j, "invoice_date"]
        if not (pd.isna(di) or pd.isna(dj)) and (di.year, di.month) != (dj.year, dj.month):
            rem.append(f"period mismatch (books {di:%b-%y} vs 2B {dj:%b-%y})")
            n_period += 1
        if not p.at[i, "valid"]:
            rem.append("invalid GSTIN format")
        rows.append([status, p.at[i, "gstin"], p.at[i, "supplier"] or g.at[j, "supplier"], p.at[i, "invoice_no"], di,
                     p.at[i, "taxable"], p.at[i, "tax"], g.at[j, "invoice_no"], dj, g.at[j, "taxable"], g.at[j, "tax"],
                     dtx, dtax, max(dtax, 0.0), "; ".join(rem)])
    for i in sorted(up):
        rows.append([S_PRONLY, p.at[i, "gstin"], p.at[i, "supplier"], p.at[i, "invoice_no"], p.at[i, "invoice_date"],
                     p.at[i, "taxable"], p.at[i, "tax"], "", pd.NaT, 0.0, 0.0, p.at[i, "taxable"], p.at[i, "tax"],
                     p.at[i, "tax"], "Not in 2B - supplier may not have filed GSTR-1" +
                     ("; invalid GSTIN format" if not p.at[i, "valid"] else "")])
    for i in sorted(p.index[p["dup"]]):
        rows.append([S_DUP, p.at[i, "gstin"], p.at[i, "supplier"], p.at[i, "invoice_no"], p.at[i, "invoice_date"],
                     p.at[i, "taxable"], p.at[i, "tax"], "", pd.NaT, 0.0, 0.0, p.at[i, "taxable"], p.at[i, "tax"],
                     p.at[i, "tax"], "Booked twice - reverse the extra entry"])
    for j in sorted(ug):
        rows.append([S_GONLY, g.at[j, "gstin"], g.at[j, "supplier"], "", pd.NaT, 0.0, 0.0, g.at[j, "invoice_no"],
                     g.at[j, "invoice_date"], g.at[j, "taxable"], g.at[j, "tax"], -g.at[j, "taxable"], -g.at[j, "tax"],
                     0.0, "In 2B, not booked - verify and record" +
                     ("; invalid GSTIN format" if not g.at[j, "valid"] else "")])
    detail = pd.DataFrame(rows, columns=_DETAIL_COLS)

    pair = detail[detail["status"].isin([S_MATCH, S_VALUE, S_FUZZY])]
    eligible = float(np.minimum(pair["pr_tax"], pair["g_tax"]).sum())
    excess = float(np.maximum(pair["pr_tax"] - pair["g_tax"], 0).sum())
    pr_only = float(detail.loc[detail["status"] == S_PRONLY, "pr_tax"].sum())
    dup = float(detail.loc[detail["status"] == S_DUP, "pr_tax"].sum())
    g_only = float(detail.loc[detail["status"] == S_GONLY, "g_tax"].sum())
    unmatched_pr_rows = int((detail["status"] == S_PRONLY).sum())
    summary = {
        "n_pr": len(p), "n_2b": len(g), "status_counts": detail["status"].value_counts().to_dict(),
        "itc_books": round(float(p["tax"].sum()), 2), "itc_2b": round(float(g["tax"].sum()), 2),
        "eligible": round(eligible, 2), "excess": round(excess, 2), "pr_only": round(pr_only, 2),
        "dup": round(dup, 2), "g_only": round(g_only, 2),
        "g_excess": round(float(np.maximum(pair["g_tax"] - pair["pr_tax"], 0).sum()), 2),
        "check_2b": round(float(g["tax"].sum()) - (eligible + g_only + float(np.maximum(pair["g_tax"] - pair["pr_tax"], 0).sum())), 2),
        "at_risk": round(excess + pr_only + dup, 2),
        "check_diff": round(float(p["tax"].sum()) - (eligible + excess + pr_only + dup), 2),
        "pct_matched": 100 * len(pair) / max(len(p) - int(p["dup"].sum()), 1),
        "n_invalid_gstin": int((~p["valid"]).sum() + (~g["valid"]).sum()),
        "n_period_mismatch": n_period, "n_dup": int(p["dup"].sum()), "n_pr_only": unmatched_pr_rows,
        "n_g_only": int((detail["status"] == S_GONLY).sum()), "n_value_mismatch": int((detail["status"] == S_VALUE).sum()),
        "n_fuzzy": int((detail["status"] == S_FUZZY).sum()),
    }

    sup = detail.copy()
    sup["supplier"] = sup["supplier"].replace("", np.nan)
    sup["supplier"] = sup.groupby("gstin")["supplier"].transform(lambda s: s.dropna().iloc[0] if s.notna().any() else "")
    grp = []
    for gst, d in sup.groupby("gstin"):
        issues = d[d["status"] != S_MATCH]
        risk = float(d["itc_at_risk"].sum())
        unbooked = float(d.loc[d["status"] == S_GONLY, "g_tax"].sum())
        if issues.empty:
            continue
        parts = []
        for s_, label in [(S_PRONLY, "not in 2B"), (S_VALUE, "value mismatch"), (S_FUZZY, "invoice no. differs"),
                          (S_GONLY, "not in books"), (S_DUP, "duplicate")]:
            c = int((d["status"] == s_).sum())
            if c:
                parts.append(f"{c} {label}")
        action = []
        if risk > 0:
            action.append("Follow up: file/amend GSTR-1")
        if unbooked > 0:
            action.append("Book missing invoices")
        grp.append({"gstin": gst, "supplier": d["supplier"].iloc[0], "issues": ", ".join(parts),
                    "itc_at_risk": round(risk, 2), "in_2b_not_in_books": round(unbooked, 2),
                    "action": "; ".join(action) or "Review"})
    suppliers = pd.DataFrame(grp, columns=["gstin", "supplier", "issues", "itc_at_risk", "in_2b_not_in_books", "action"])
    suppliers = suppliers.sort_values(["itc_at_risk", "in_2b_not_in_books"], ascending=False).reset_index(drop=True)
    return GstRecon(detail, suppliers, summary)
