"""Synthetic DEMO data for the CA Analytics Suite (no real entity or taxpayer data).

Each generator deliberately plants known errors so the tools have something to find.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_ALNUM = list("123456789ABCDEFGHJKLMNPQRSTUVWXYZ")
_UP = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _gstin(rng, state="20"):
    pan = "".join(rng.choice(_UP, 5)) + "".join(str(x) for x in rng.integers(0, 10, 4)) + str(rng.choice(_UP))
    return f"{state}{pan}{rng.integers(1, 9)}Z{rng.choice(_ALNUM)}"


# --------------------------------------------------------------------------- bank vs books
def bank_vs_books(seed: int = 7, n: int = 110) -> dict:
    """Books (bank ledger) vs bank statement for April 2025.

    Planted: 7 deposits/cheques in transit, 3 amount differences, 2 duplicate book entries,
    2 receipts banked together as one credit, 5 bank-only items (charges, interest, EMI),
    ~25% of bank rows without a reference number.
    """
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2025-04-01", "2025-04-25")
    parties = ["Mahalaxmi Traders", "Jharia Coal Suppliers", "Dhanbad Steel Works", "Om Sai Transport",
               "Bharat Electricals", "Rajesh & Sons", "Kumar Logistics", "Annapurna Stores",
               "Shree Engineering", "National Hardware"]
    rows = []
    for _ in range(n):
        receipt = rng.random() < 0.45
        amt = round(float(np.exp(rng.normal(9.6, 1.0))), 2)
        rows.append({
            "date": pd.Timestamp(days[rng.integers(len(days))]),
            "ref": str(rng.integers(100000, 999999)),
            "desc": ("Receipt from " if receipt else "Payment to ") + parties[rng.integers(len(parties))],
            "amount": amt if receipt else -amt,
        })
    books = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)

    idx = [int(i) for i in rng.permutation(len(books))]
    transit, mism, dups = idx[:7], idx[7:10], idx[10:12]
    receipts = [i for i in idx[12:] if books.at[i, "amount"] > 0][:2]
    books.loc[transit, "date"] = pd.Timestamp("2025-04-29")

    bank = books.drop(transit + receipts).copy()
    for i in mism:  # bank shows a different amount (keying / transposition error in books)
        a = books.at[i, "amount"]
        bank.at[i, "amount"] = round(float(np.sign(a)) * (abs(a) + float(rng.choice([90, 180, 270, 900]))), 2)
    lag = np.where(bank["amount"] > 0, rng.integers(0, 3, len(bank)), rng.integers(1, 5, len(bank)))
    bank["date"] = bank["date"] + pd.to_timedelta(lag, unit="D")
    bank["desc"] = [("CHQ DEP " if a > 0 else "CHQ PAID ") + r for a, r in zip(bank["amount"], bank["ref"])]
    blank = rng.random(len(bank)) < 0.25
    bank.loc[blank, "ref"] = ""
    bank.loc[mism, "ref"] = books.loc[mism, "ref"]  # keep ref on difference rows so they can be traced

    g = books.loc[receipts]
    extra = pd.DataFrame([
        {"date": g["date"].max() + pd.Timedelta(days=1), "ref": "", "desc": "CHQ DEP BULK CLEARING", "amount": round(g["amount"].sum(), 2)},
        {"date": pd.Timestamp("2025-04-30"), "ref": "", "desc": "BANK CHARGES - QTR", "amount": -1180.00},
        {"date": pd.Timestamp("2025-04-30"), "ref": "", "desc": "GST ON CHARGES", "amount": -212.40},
        {"date": pd.Timestamp("2025-04-30"), "ref": "", "desc": "SMS CHARGES", "amount": -23.60},
        {"date": pd.Timestamp("2025-04-30"), "ref": "", "desc": "INTEREST CREDIT", "amount": 2345.50},
        {"date": pd.Timestamp("2025-04-30"), "ref": "", "desc": "NACH DEBIT LOAN EMI", "amount": -18500.00},
    ])
    bank = pd.concat([bank, extra], ignore_index=True)
    bank["date"] = bank["date"].clip(upper=pd.Timestamp("2025-04-30"))
    bank = bank.sort_values("date").reset_index(drop=True)

    books = pd.concat([books, books.loc[dups]], ignore_index=True).sort_values("date").reset_index(drop=True)
    return {"books": books, "bank": bank, "opening_books": 500000.0, "opening_bank": 500000.0}


# --------------------------------------------------------------------------- GSTR-2B vs purchase register
_SUPPLIERS = ["Jharia Coal Suppliers", "Dhanbad Steel Works", "Bharat Electricals", "Om Sai Transport",
              "Kumar Logistics", "Shree Engineering", "National Hardware", "Annapurna Stores",
              "Mahalaxmi Traders", "Eastern Lubricants", "Tata Tubes Dealer", "Sanjay Safety Gear"]
_STATES = ["20", "20", "20", "19", "10", "27", "20", "20", "07", "20", "19", "20"]


def _gst_row(rng, sup, serial):
    gstin, name, pre = sup[rng.integers(len(sup))]
    d = pd.Timestamp("2025-04-01") + pd.Timedelta(days=int(rng.integers(0, 30)))
    taxable = round(float(np.exp(rng.normal(10.2, 0.9))), 2)
    rate = float(rng.choice([0.05, 0.12, 0.18, 0.28], p=[0.1, 0.2, 0.6, 0.1]))
    tax = round(taxable * rate, 2)
    intra = gstin[:2] == "20"
    return {"gstin": gstin, "supplier": name, "invoice_no": f"{pre}/{serial}", "invoice_date": d, "taxable": taxable,
            "igst": 0.0 if intra else tax, "cgst": round(tax / 2, 2) if intra else 0.0,
            "sgst": round(tax / 2, 2) if intra else 0.0, "cess": 0.0}


def gst_data(seed: int = 11, n: int = 70) -> dict:
    """Purchase register vs GSTR-2B for April 2025.

    Planted: 6 invoices missing in 2B, 4 value mismatches, 4 invoice-number variants, 1 period mismatch,
    1 invalid GSTIN, 5 invoices only in 2B, 1 duplicate in the purchase register.
    """
    rng = np.random.default_rng(seed)
    sup = [(_gstin(rng, s), nm, "".join(w[0] for w in nm.split()[:3]).upper()) for nm, s in zip(_SUPPLIERS, _STATES)]
    pr = pd.DataFrame([_gst_row(rng, sup, 1000 + i) for i in range(n)])
    g2b = pr.copy()

    idx = [int(i) for i in rng.permutation(n)]
    missing, vmm, invvar, period, bad, dup = idx[:6], idx[6:10], idx[10:14], idx[14], idx[15], idx[16]
    g2b = g2b.drop(missing)
    for i in vmm[:2]:  # supplier reported lower tax -> ITC at risk
        for t in ("igst", "cgst", "sgst"):
            g2b.at[i, t] = round(pr.at[i, t] * 0.9, 2)
    for i in vmm[2:]:  # only taxable value differs
        g2b.at[i, "taxable"] = round(pr.at[i, "taxable"] + 1000.0, 2)
    for k, i in enumerate(invvar):
        pre, num = pr.at[i, "invoice_no"].split("/")
        g2b.at[i, "invoice_no"] = [f"{pre}-{num}", f"{pre}/0{num}", f"{pre[:-1]}/{num}A", f"{pre}{int(num) + 10}"][k]
    g2b.at[period, "invoice_date"] = pr.at[period, "invoice_date"] + pd.Timedelta(days=40)
    bad_g = pr.at[bad, "gstin"][:-1]
    pr.at[bad, "gstin"] = bad_g
    g2b.at[bad, "gstin"] = bad_g
    only_2b = pd.DataFrame([_gst_row(rng, sup, 2000 + j) for j in range(5)])
    g2b = pd.concat([g2b, only_2b], ignore_index=True)
    pr = pd.concat([pr, pr.iloc[[dup]]], ignore_index=True)
    return {"pr": pr, "gstr2b": g2b}


# --------------------------------------------------------------------------- MIS
def mis_data(seed: int = 21) -> dict:
    """12 months (FY 2025-26) ledger, monthly budget and open invoices for a small manufacturer.

    Planted: Marketing spike (Oct-25), Repairs spike (Jan-26), one slow-paying customer, customer concentration.
    """
    rng = np.random.default_rng(seed)
    months = pd.period_range("2025-04", "2026-03", freq="M")
    customers = {"Eastern Power Ltd": 0.28, "Bharat Steel Co": 0.22, "Rajesh Infra": 0.16,
                 "Maa Tara Industries": 0.12, "Kumar Cement": 0.09, "Retail & Others": 0.13}
    cogs = {"Raw Material": 0.50, "Freight Inward": 0.10, "Direct Labour": 0.10}
    opex = {"Salaries": 620000, "Rent": 180000, "Power & Fuel": 140000, "Marketing": 90000,
            "Repairs & Maintenance": 70000, "Professional Fees": 60000, "Admin & Misc": 85000}
    rows, bud = [], []
    for i, m in enumerate(months):
        d = m.to_timestamp(how="end").normalize()
        rev_budget = 9_000_000 * (1 + 0.02 * i)
        rev = rev_budget * (1 + 0.08 * np.sin(i / 12 * 2 * np.pi)) * rng.normal(1.0, 0.04)
        for c, w in customers.items():
            rows.append((d, "Revenue", "Sales", c, rev * w * rng.normal(1, 0.05)))
        for h, p in cogs.items():
            rows.append((d, "COGS", h, "", rev * p * rng.normal(1, 0.03)))
        for h, b in opex.items():
            v = b * (1 + 0.01 * i) * rng.normal(1, 0.04)
            if h == "Marketing" and m.month == 10:
                v *= 3.2
            if h == "Repairs & Maintenance" and m.month == 1:
                v *= 4.0
            rows.append((d, "Opex", h, "", v))
        rows.append((d, "Finance Cost", "Interest on Term Loan", "", 210000 - 4000 * i))
        rows.append((d, "Depreciation", "Depreciation", "", 150000.0))
        rows.append((d, "Other Income", "Interest & Misc Income", "", 25000 * rng.normal(1, 0.2)))
        opex_b = sum(opex.values()) * (1 + 0.01 * i)
        bud += [(str(m), "Revenue", rev_budget), (str(m), "COGS", rev_budget * 0.70), (str(m), "Opex", opex_b)]
    ledger = pd.DataFrame(rows, columns=["date", "group", "head", "party", "amount"])
    ledger["amount"] = ledger["amount"].round(2)
    budget = pd.DataFrame(bud, columns=["month", "group", "budget"]).round(2)

    asof = pd.Timestamp("2026-03-31")
    sales = ledger[ledger["group"] == "Revenue"]
    inv = []
    n = 1
    for _, r in sales[sales["date"] >= pd.Timestamp("2025-10-01")].iterrows():
        age = (asof - r["date"]).days
        slow = r["party"] == "Rajesh Infra"
        p_paid = 0.0 if age < 15 else min(0.95, 0.35 + age / 300) * (0.4 if slow else 1.0)
        amt = round(r["amount"], 2)
        recd = amt if rng.random() < p_paid else (round(amt * 0.4, 2) if (age > 30 and rng.random() < 0.3) else 0.0)
        inv.append({"invoice_no": f"INV-{1000 + n}", "customer": r["party"], "invoice_date": r["date"],
                    "due_date": r["date"] + pd.Timedelta(days=45), "amount": amt, "received": recd})
        n += 1
    return {"ledger": ledger, "budget": budget, "invoices": pd.DataFrame(inv), "asof": asof}
