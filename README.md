# CA Analytics Suite

Two audit/finance automation tools in one Streamlit app, with **PDF and Excel download after every run**.

| Tool | What it does |
|---|---|
| **Reconciliation** (a) Bank vs books (b) GSTR-2B vs purchase register | (a) 5-pass matching, exceptions, and a Bank Reconciliation Statement that must tie to nil. (b) GSTIN + invoice-number matching, probable matches, value mismatches, ITC at risk, supplier follow-up list |
| **MIS dashboard** | Monthly P&L, KPIs vs prior period, budget variance, cost-spike detection, customer concentration, receivables ageing + DSO, revenue outlook, auto-written commentary. Excel export includes a Power BI-ready fact table |

## Run it (on your own computer)

```bash
pip install -r requirements.txt
streamlit run app.py          # the app (demo data is built in; toggle off to upload your own files)
python run_demo.py            # no UI: writes all 3 PDFs + 3 Excel files into demo_output/
python test_app_stub.py       # smoke test of the app logic
```

## Your own data

* **Bank reconciliation** - upload the bank statement and the bank ledger from your books (CSV/Excel). Map columns on screen
  (Debit/Credit or single signed amount). In the books a *debit* is a receipt; in the bank statement a *credit* is a deposit.
* **GSTR-2B** - upload the purchase register and the GSTR-2B B2B sheet; map GSTIN, invoice no./date, taxable value, IGST/CGST/SGST/cess.
* **MIS** - download the three templates inside the app: ledger (`date, group, head, party, amount`; groups: Revenue, COGS, Opex,
  Other Income, Finance Cost, Depreciation), budget (`month, group, budget`), open invoices (`invoice_no, customer, invoice_date, due_date, amount, received`).

## How the matching works

**Bank vs books:** (1) same reference + amount, (2) same amount within a date window, (3) same reference with a different amount
(flagged as a difference), (4) several book entries = one bank entry, (5) one book entry = several bank entries. Leftovers are
classified (deposit in transit, cheque not presented, possible duplicate, bank charges, interest, direct debit).
BRS: adjusted books balance = adjusted bank balance, otherwise the app says **NOT RECONCILED** with the difference.

**GSTR-2B vs purchase register:** (1) GSTIN + normalised invoice number (case, symbols and leading zeros ignored), (2) same supplier
with same taxable value and a similar invoice number or date, then status per invoice: Matched / Value mismatch / Probable match /
In books not in 2B / In 2B not in books / Duplicate. ITC bridge ties both ways (books and 2B) to nil.

## Power BI

Open `mis_report.xlsx` in Power BI Desktop -> *Get data -> Excel* -> load `PowerBI_Fact` and `Dim_Month`, relate `PowerBI_Fact[month]`
to `Dim_Month[month]`. `signed_amount` is +income / -cost, so a simple SUM gives profit.

## Limits - please read

* Working-paper tool: **review the output before relying on it.** Matching rules are heuristic; the tolerance and date window are adjustable.
* GSTIN validation is a **format check only**. ITC eligibility depends on Section 16 conditions - confirm before reversing or claiming.
* The revenue outlook is a linear trend (no seasonality) and the cost-spike test is statistical - both are prompts to investigate, not conclusions.
* Demo data is synthetic. Run locally for real client data; do not upload confidential data to a public deployment.
* Use it only on data you are authorised to process (employer data and client data carry confidentiality obligations).
