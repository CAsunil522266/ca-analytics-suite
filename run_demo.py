"""Generate every report from the built-in demo data:  python run_demo.py"""
import os

import mis_engine as me
import recon_engine as rc
import reports as rp
import sample_data as sd

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_output")
os.makedirs(OUT, exist_ok=True)


def save(name, data):
    with open(os.path.join(OUT, name), "wb") as f:
        f.write(data)
    print("wrote", name, f"({len(data) / 1024:.0f} KB)")


def main():
    d = sd.bank_vs_books()
    meta = {"company": "Demo Industries Pvt Ltd", "account": "SBI Current A/c xxxx1234", "period": "April 2025 (demo data)"}
    r = rc.reconcile_bank(d["books"], d["bank"], d["opening_books"], d["opening_bank"])
    save("bank_reconciliation.pdf", rp.bank_recon_pdf(r, meta))
    save("bank_reconciliation.xlsx", rp.bank_recon_excel(r, meta))

    g = sd.gst_data()
    x = rc.reconcile_gst(g["pr"], g["gstr2b"])
    meta = {"company": "Demo Industries Pvt Ltd", "account": "GSTIN 20XXXXX0000X1ZX (demo)", "period": "Tax period April 2025"}
    save("gstr2b_reconciliation.pdf", rp.gst_recon_pdf(x, meta))
    save("gstr2b_reconciliation.xlsx", rp.gst_recon_excel(x, meta))

    m = sd.mis_data()
    res = me.run_mis(m["ledger"], m["budget"], m["invoices"], m["asof"], company="Demo Industries Pvt Ltd")
    meta = {"company": "Demo Industries Pvt Ltd", "period": ""}
    save("mis_report.pdf", rp.mis_pdf(res, meta))
    save("mis_report.xlsx", rp.mis_excel(res, meta))


if __name__ == "__main__":
    main()
