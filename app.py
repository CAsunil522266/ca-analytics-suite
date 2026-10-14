"""CA Analytics Suite - Streamlit app.      Run:  streamlit run app.py

Tools: (1) Bank reconciliation  (2) GSTR-2B vs purchase register  (3) MIS dashboard.
Each tool offers PDF + Excel download after it has been run.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

import charts
import mis_engine as me
import recon_engine as rc
import reports as rp
import sample_data as sd
from utils import fdate, inr

st.set_page_config(page_title="CA Analytics Suite", page_icon="📊", layout="wide")

NONE = "- none -"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ------------------------------------------------------------------ helpers
def read_any(f) -> pd.DataFrame:
    name = f.name.lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(f)
    try:
        return pd.read_csv(f)
    except UnicodeDecodeError:
        f.seek(0)
        return pd.read_csv(f, encoding="latin-1")


def gi(options, *keys, optional=False) -> int:
    """Index of the first option whose name contains one of the keywords (for default column guesses)."""
    for k in keys:
        for i, o in enumerate(options):
            if k in str(o).lower():
                return i + (1 if optional else 0)
    return 0


def show(fig):
    st.pyplot(fig)
    plt.close(fig)


def money(df, cols=(), dates=()):
    out = df.copy()
    for c in cols:
        out[c] = out[c].map(inr)
    for c in dates:
        out[c] = out[c].map(fdate)
    return out


def downloads(prefix, pdf, xlsx, stem):
    c = st.columns(2)
    c[0].download_button("Download PDF report", pdf, f"{stem}.pdf", "application/pdf", key=f"{prefix}_pdf")
    c[1].download_button("Download Excel workbook", xlsx, f"{stem}.xlsx", XLSX, key=f"{prefix}_xlsx")


def txn_mapper(df, label, key, inflow, hint):
    cols = list(df.columns)
    st.markdown(f"**{label}** - map the columns")
    c = st.columns(4)
    date = c[0].selectbox("Date", cols, index=gi(cols, "date"), key=f"{key}_date")
    ref = c[1].selectbox("Reference / cheque no.", [NONE] + cols, index=gi(cols, "ref", "chq", "cheque", "utr", optional=True), key=f"{key}_ref")
    desc = c[2].selectbox("Description", [NONE] + cols, index=gi(cols, "desc", "narr", "particular", optional=True), key=f"{key}_desc")
    layout = c[3].radio("Amount columns", ["Debit / Credit", "Single signed amount"], key=f"{key}_lay")
    kw = dict(ref=None if ref == NONE else ref, desc=None if desc == NONE else desc)
    if layout == "Single signed amount":
        amt = st.selectbox("Amount (+ money in, - money out)", cols, index=gi(cols, "amount"), key=f"{key}_amt")
        return rc.standardize_txn(df, date, amount=amt, **kw)
    c2 = st.columns(2)
    deb = c2[0].selectbox("Debit column", cols, index=gi(cols, "debit", "withdraw"), key=f"{key}_dr")
    cre = c2[1].selectbox("Credit column", cols, index=gi(cols, "credit", "deposit"), key=f"{key}_cr")
    st.caption(hint)
    return rc.standardize_txn(df, date, debit=deb, credit=cre, inflow=inflow, **kw)


GST_FIELDS = [("gstin", "Supplier GSTIN", ("gstin", "gst no"), False), ("supplier", "Supplier name", ("trade", "supplier", "party", "name"), True),
              ("invoice_no", "Invoice number", ("invoice no", "inv no", "invoice num", "bill", "document"), False),
              ("invoice_date", "Invoice date", ("date",), False), ("taxable", "Taxable value", ("taxable",), False),
              ("igst", "IGST", ("igst", "integrated"), True), ("cgst", "CGST", ("cgst", "central"), True),
              ("sgst", "SGST / UTGST", ("sgst", "state", "utgst"), True), ("cess", "Cess", ("cess",), True)]


def gst_mapper(df, key):
    cols, m = list(df.columns), {}
    grid = st.columns(3)
    for n, (f, label, keys, opt) in enumerate(GST_FIELDS):
        sel = grid[n % 3].selectbox(label, ([NONE] if opt else []) + cols, index=gi(cols, *keys, optional=opt), key=f"{key}_{f}")
        if sel != NONE:
            m[f] = sel
    return rc.standardize_gst(df, m)


# ------------------------------------------------------------------ 1. bank reconciliation
def page_bank():
    st.subheader("Bank reconciliation - bank statement vs books")
    st.caption("Five matching passes (reference + amount, amount + date window, same reference with a difference, grouped entries), "
               "then a bank reconciliation statement (BRS) that must tie to nil.")
    c = st.columns(3)
    meta = {"company": c[0].text_input("Entity", "Demo Industries Pvt Ltd", key="b_co"),
            "account": c[1].text_input("Bank account", "SBI Current A/c xxxx1234", key="b_ac"),
            "period": c[2].text_input("Period", "April 2025", key="b_pe")}
    demo = st.toggle("Use demo data", value=True, key="b_demo")
    if demo:
        d = sd.bank_vs_books()
        books, bank, ob, ok_ = d["books"], d["bank"], d["opening_books"], d["opening_bank"]
        with st.expander("Preview demo data"):
            st.write("Books (bank ledger)")
            st.dataframe(money(books.head(8), ["amount"], ["date"]), hide_index=True)
            st.write("Bank statement")
            st.dataframe(money(bank.head(8), ["amount"], ["date"]), hide_index=True)
    else:
        c = st.columns(2)
        fb = c[0].file_uploader("Books - bank ledger (CSV / Excel)", type=["csv", "xlsx", "xls"], key="b_fb")
        fk = c[1].file_uploader("Bank statement (CSV / Excel)", type=["csv", "xlsx", "xls"], key="b_fk")
        if not (fb and fk):
            st.info("Upload both files, or switch on demo data.")
            return
        books = txn_mapper(read_any(fb), "Books", "bk", "debit", "In the books' bank ledger, a debit is a receipt.")
        bank = txn_mapper(read_any(fk), "Bank statement", "bs", "credit", "In a bank statement, a credit is a deposit.")
        c = st.columns(2)
        ob = c[0].number_input("Opening balance - books", value=0.0, key="b_ob")
        ok_ = c[1].number_input("Opening balance - bank", value=0.0, key="b_ok")
    window = st.slider("Date window for matching (days)", 0, 10, 5, key="b_win")

    if st.button("Run reconciliation", type="primary", key="b_run"):
        with st.spinner("Reconciling..."):
            res = rc.reconcile_bank(books, bank, ob, ok_, window=window)
            st.session_state["bank"] = {"res": res, "pdf": rp.bank_recon_pdf(res, meta), "xlsx": rp.bank_recon_excel(res, meta)}
    out = st.session_state.get("bank")
    if not out:
        return
    res = out["res"]
    s = res.summary
    (st.success if s["tie"] else st.error)(
        f"{'Reconciled' if s['tie'] else 'Not reconciled'}: adjusted books {inr(s['adj_books'])} vs adjusted bank {inr(s['adj_bank'])} "
        f"(difference {inr(s['difference'])})")
    downloads("b", out["pdf"], out["xlsx"], "bank_reconciliation")
    m = st.columns(5)
    m[0].metric("Books entries", s["n_books"])
    m[1].metric("Bank entries", s["n_bank"])
    m[2].metric("Entries matched", f"{s['pct_books_matched']:.1f}%")
    m[3].metric("Value matched", f"{s['pct_value_matched']:.1f}%")
    m[4].metric("Open exceptions", s["n_exceptions"])
    st.markdown("#### Bank reconciliation statement")
    st.dataframe(money(res.brs, ["amount"])[["section", "particulars", "amount"]], hide_index=True)
    show(charts.bank_exceptions_chart(res))
    t = st.tabs(["Books, not in bank", "Bank, not in books", "Amount differences", "Matched"])
    cols = ["date", "ref", "desc", "category", "amount"]
    t[0].dataframe(money(res.books_only[cols], ["amount"], ["date"]), hide_index=True)
    t[1].dataframe(money(res.bank_only[cols], ["amount"], ["date"]), hide_index=True)
    mm = res.matched[res.matched["match_type"].str.startswith("3")]
    t[2].dataframe(money(mm, ["books_amount", "bank_amount", "diff"], ["books_date", "bank_date"]), hide_index=True)
    t[3].dataframe(money(res.matched, ["books_amount", "bank_amount", "diff"], ["books_date", "bank_date"]), hide_index=True)


# ------------------------------------------------------------------ 2. GSTR-2B vs purchase register
def page_gst():
    st.subheader("GSTR-2B vs purchase register - ITC reconciliation")
    st.caption("Matches on GSTIN + normalised invoice number, then probable matches (same supplier and value, similar number/date). "
               "Quantifies ITC at risk and builds a supplier follow-up list.")
    c = st.columns(3)
    meta = {"company": c[0].text_input("Entity", "Demo Industries Pvt Ltd", key="g_co"),
            "account": c[1].text_input("GSTIN", "20XXXXX0000X1ZX", key="g_ac"),
            "period": c[2].text_input("Tax period", "April 2025", key="g_pe")}
    demo = st.toggle("Use demo data", value=True, key="g_demo")
    tol = st.number_input("Value tolerance (₹ per tax head)", 0.0, 100.0, 1.0, key="g_tol")
    if demo:
        d = sd.gst_data()
        pr, g2b = d["pr"], d["gstr2b"]
        with st.expander("Preview demo data"):
            st.dataframe(money(pr.head(8), ["taxable", "igst", "cgst", "sgst"], ["invoice_date"]), hide_index=True)
    else:
        c = st.columns(2)
        fp = c[0].file_uploader("Purchase register (CSV / Excel)", type=["csv", "xlsx", "xls"], key="g_fp")
        fg = c[1].file_uploader("GSTR-2B (B2B sheet as CSV / Excel)", type=["csv", "xlsx", "xls"], key="g_fg")
        if not (fp and fg):
            st.info("Upload both files, or switch on demo data.")
            return
        st.markdown("**Purchase register** - map the columns")
        pr = gst_mapper(read_any(fp), "gp")
        st.markdown("**GSTR-2B** - map the columns")
        g2b = gst_mapper(read_any(fg), "gg")

    if st.button("Run reconciliation", type="primary", key="g_run"):
        with st.spinner("Reconciling..."):
            res = rc.reconcile_gst(pr, g2b, tol=tol)
            st.session_state["gst"] = {"res": res, "pdf": rp.gst_recon_pdf(res, meta), "xlsx": rp.gst_recon_excel(res, meta)}
    out = st.session_state.get("gst")
    if not out:
        return
    res = out["res"]
    s = res.summary
    st.success(f"{s['pct_matched']:.0f}% of invoices matched. ITC at risk: {inr(s['at_risk'])}. ITC in GSTR-2B not booked: {inr(s['g_only'])}.")
    downloads("g", out["pdf"], out["xlsx"], "gstr2b_reconciliation")
    m = st.columns(5)
    m[0].metric("ITC per books", inr(s["itc_books"], 0))
    m[1].metric("ITC per GSTR-2B", inr(s["itc_2b"], 0))
    m[2].metric("Eligible (matched)", inr(s["eligible"], 0))
    m[3].metric("ITC at risk", inr(s["at_risk"], 0))
    m[4].metric("In 2B, not booked", inr(s["g_only"], 0))
    show(charts.gst_itc_chart(res))
    show(charts.gst_status_chart(res))
    t = st.tabs(["Supplier follow-up", "All invoices", "Not in 2B", "Not in books", "Mismatches"])
    mon = ["pr_taxable", "pr_tax", "g_taxable", "g_tax", "diff_taxable", "diff_tax", "itc_at_risk"]
    d = res.detail
    t[0].dataframe(money(res.suppliers, ["itc_at_risk", "in_2b_not_in_books"]), hide_index=True)
    t[1].dataframe(money(d, mon, ["pr_date", "g_date"]), hide_index=True)
    t[2].dataframe(money(d[d["status"] == rc.S_PRONLY], mon, ["pr_date", "g_date"]), hide_index=True)
    t[3].dataframe(money(d[d["status"] == rc.S_GONLY], mon, ["pr_date", "g_date"]), hide_index=True)
    t[4].dataframe(money(d[d["status"].isin([rc.S_VALUE, rc.S_FUZZY])], mon, ["pr_date", "g_date"]), hide_index=True)
    st.caption("ITC availability depends on Section 16 conditions - confirm before reversing or claiming. GSTIN check is format-only.")


# ------------------------------------------------------------------ 3. MIS dashboard
LEDGER_COLS = ["date", "group", "head", "party", "amount"]
BUDGET_COLS = ["month", "group", "budget"]
INV_COLS = ["invoice_no", "customer", "invoice_date", "due_date", "amount", "received"]


def page_mis():
    st.subheader("MIS dashboard for SMEs")
    st.caption("Monthly P&L, KPIs, budget variance, cost-spike detection, customer concentration, receivables ageing and a revenue outlook. "
               "Exports to PDF, Excel (with a Power BI-ready fact table).")
    company = st.text_input("Entity", "Demo Industries Pvt Ltd", key="m_co")
    demo = st.toggle("Use demo data", value=True, key="m_demo")
    if demo:
        d = sd.mis_data()
        ledger, budget, invoices, asof = d["ledger"], d["budget"], d["invoices"], d["asof"]
    else:
        d = sd.mis_data()
        with st.expander("Templates - file formats expected"):
            st.write("Ledger groups allowed: " + ", ".join(me.GROUPS))
            c = st.columns(3)
            c[0].download_button("Ledger template", d["ledger"].head(40).to_csv(index=False), "ledger_template.csv", "text/csv", key="m_t1")
            c[1].download_button("Budget template", d["budget"].head(12).to_csv(index=False), "budget_template.csv", "text/csv", key="m_t2")
            c[2].download_button("Invoices template", d["invoices"].head(20).to_csv(index=False), "invoices_template.csv", "text/csv", key="m_t3")
        c = st.columns(3)
        fl = c[0].file_uploader("Ledger (required)", type=["csv", "xlsx", "xls"], key="m_fl")
        fb = c[1].file_uploader("Budget (optional)", type=["csv", "xlsx", "xls"], key="m_fb")
        fi = c[2].file_uploader("Open invoices (optional)", type=["csv", "xlsx", "xls"], key="m_fi")
        if not fl:
            st.info("Upload the ledger, or switch on demo data.")
            return
        ledger, budget, invoices = read_any(fl), (read_any(fb) if fb else None), (read_any(fi) if fi else None)
        for name, df, need in [("Ledger", ledger, LEDGER_COLS), ("Budget", budget, BUDGET_COLS), ("Invoices", invoices, INV_COLS)]:
            if df is not None and [c_ for c_ in need if c_ not in df.columns]:
                st.error(f"{name} is missing columns: {', '.join(c_ for c_ in need if c_ not in df.columns)}")
                return
        asof = st.date_input("As-of date for receivables ageing", value=pd.to_datetime(ledger["date"], dayfirst=True, errors="coerce").max(), key="m_asof")

    led, unknown = me.prepare_ledger(ledger)
    if unknown:
        st.warning("Ignored unknown groups: " + ", ".join(unknown))
    months = list(pd.period_range(led["month"].min(), led["month"].max(), freq="M"))
    labels = [m.strftime("%b-%y") for m in months]
    a, b = st.select_slider("Reporting period", options=labels, value=(labels[0], labels[-1]), key="m_rng")
    r = me.run_mis(ledger, budget, invoices, asof, start=months[labels.index(a)], end=months[labels.index(b)], company=company)

    meta = {"company": company, "period": ""}
    sig = (company, a, b, bool(demo), str(asof), len(ledger))
    if st.button("Prepare PDF + Excel reports", type="primary", key="m_prep"):
        with st.spinner("Building reports..."):
            st.session_state["mis_out"] = {"sig": sig, "pdf": rp.mis_pdf(r, meta), "xlsx": rp.mis_excel(r, meta)}
    out = st.session_state.get("mis_out")
    if out and out["sig"] == sig:
        downloads("m", out["pdf"], out["xlsx"], "mis_report")
    elif out:
        st.caption("Settings changed - press 'Prepare PDF + Excel reports' again to refresh the downloads.")

    k = r.kpis.set_index("metric")
    cols = st.columns(5)
    for col, (name, kind) in zip(cols, [("Revenue", "amt"), ("GP %", "pct"), ("EBITDA", "amt"), ("EBITDA %", "pct"), ("PBT", "amt")]):
        v, ch = k.loc[name, "current"], k.loc[name, "change"]
        col.metric(name, inr(v, 0) if kind == "amt" else f"{v:.1f}%",
                   None if ch != ch else (f"{ch:+.1f}%" if kind == "amt" else f"{ch:+.1f} pp"))
    t = st.tabs(["Overview", "P&L", "Budget", "Costs", "Customers & receivables", "Outlook"])
    with t[0]:
        for line in r.insights:
            st.markdown(f"- {line}")
        c = st.columns(2)
        with c[0]:
            show(charts.revenue_trend_chart(r))
        with c[1]:
            show(charts.cost_mix_chart(r))
    with t[1]:
        p = r.pnl.loc[r.months[0]:r.months[-1]].copy()
        p.index = [x.strftime("%b-%y") for x in p.index]
        st.dataframe((p[me.AMT] / 1e5).round(2).T, )
        st.caption("₹ lakh. EBITDA = gross profit - operating expenses; PBT = EBITDA + other income - depreciation - finance cost.")
    with t[2]:
        if r.variance is None:
            st.info("Upload a budget file to see budget vs actual.")
        else:
            st.dataframe(money(r.variance, ["actual", "budget", "variance"]), hide_index=True)
    with t[3]:
        o = r.opex.copy()
        o.columns = [x.strftime("%b-%y") for x in o.columns]
        st.dataframe((o / 1e5).round(2))
        if len(r.anomalies):
            a_ = r.anomalies.copy()
            a_["month"] = a_["month"].map(lambda x: x.strftime("%b-%y"))
            st.warning("Cost spikes to review")
            st.dataframe(money(a_, ["amount", "typical"]), hide_index=True)
        else:
            st.success("No unusual cost spikes in the selected period.")
    with t[4]:
        c = st.columns(2)
        with c[0]:
            show(charts.customer_chart(r))
        with c[1]:
            show(charts.ageing_chart(r))
        if r.ageing:
            st.dataframe((r.ageing["by_customer"] / 1e5).round(2))
    with t[5]:
        if r.forecast is None:
            st.info("Need at least 6 months of data.")
        else:
            f = r.forecast.copy()
            f["month"] = f["month"].map(lambda x: x.strftime("%b-%y"))
            st.dataframe(money(f, ["forecast", "low", "high"]), hide_index=True)
            st.caption("Linear trend on the last 12 months; ignores seasonality. Indicative only.")


# ------------------------------------------------------------------ main
def main():
    st.title("CA Analytics Suite")
    st.caption("Reconciliation and MIS automation in Python. Demo data is synthetic.")
    page = st.sidebar.radio("Tool", ["Bank reconciliation", "GSTR-2B vs purchase register", "MIS dashboard"])
    st.sidebar.info("Run this app locally for client data. Do not upload confidential data to a shared or public deployment.")
    {"Bank reconciliation": page_bank, "GSTR-2B vs purchase register": page_gst, "MIS dashboard": page_mis}[page]()


main()
