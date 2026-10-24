# 📊 E-commerce Analytics Dashboard

An interactive Streamlit dashboard that cleans a messy e-commerce dataset and reports 8 business KPIs plus one stretch KPI (margin risk). All cleaning is done in pandas at load time, and every assumption is logged in the app's **Data quality** tab.

## Features

| Tab | KPI | What it shows |
|---|---|---|
| 1 | Net revenue | Net revenue by product category, per month (stacked or line) |
| 2 | Top customers | Top 10 customers by net lifetime value (completed orders, net of returns) |
| 3 | Segments | Standardized customer segment profile: customers and average order value |
| 4 | MoM by channel | Month-over-month order volume and revenue growth per channel |
| 5 | Product returns | Top 10 and bottom 10 products by return rate, with a minimum-units threshold |
| 6 | Suppliers | Weighted composite scorecard (revenue, low returns, rating) with adjustable weights |
| 7 | Repeat purchase | Share of customers with a 2nd completed order within N days (default 90) |
| 8 | Payment recon | Order totals vs successful payments, with anomaly flags (underpaid, overpaid, none, multiple) |
| ★ | Margin risk | Products sold below cost, with estimated loss |
| - | Data quality | Log of every issue found and fixed, plus the rules applied |

Sidebar filters: order date range, channel, which order statuses count as revenue, and which return statuses count. Every table has a **Download CSV** button.

## Requirements

- Python 3.9+
- `streamlit`, `pandas`, `numpy` (Altair ships with Streamlit)

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/<username>/<repo>.git
cd <repo>

# 2. (Optional) create a virtual environment
python -m venv .venv
# Windows:   .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

`requirements.txt`:

```
streamlit
pandas
numpy
```

## Data files

Place these files in the same folder as `app.py` (or in a subfolder named `Ecommerce_Analyics_DE_Source_Data`):

```
customers.csv
orders.csv
order_items.csv
payments.csv
products.csv
returns.csv
suppliers.csv          (or the original suppliers_json.txt)
```

The data folder can also be changed from the sidebar.

## Run

```bash
python -m streamlit run app.py
```

The app opens at http://localhost:8501.

## Project structure

```
.
├── app.py              # cleaning, KPI functions and Streamlit UI
├── requirements.txt
├── README.md
└── *.csv               # source data (not committed if private)
```

Inside `app.py`:

1. **Cleaning helpers**: booleans, numbers, typo-tolerant labels, dates
2. **`load_data`** (cached): loads, cleans and joins all tables into one fact table (one row per order line)
3. **KPI functions**: pure pandas, easy to test and reuse
4. **UI helpers**: charts, tables, CSV download
5. **`main`**: sidebar filters and tabs

## Data cleaning rules

- **Duplicates:** exact duplicate rows dropped; duplicate IDs keep the first record.
- **Dates:** all numeric dates are read as **DD-MM-YYYY** (day first), whether written with `-` or `/`. `YYYY-MM-DD` and `DD-Mon-YYYY` are also recognised. Unparseable dates become blank.
- **Labels:** typos and case variants are mapped to canonical values (e.g. `Glod` → `Gold`); channels and cities are standardized (`Bangalore` → `Bengaluru`).
- **Products:** conflicting records are resolved in favour of the valid price that matches real transactions (the "(Revised)" row). Non-positive prices fall back to the most common transaction price, and a missing category is filled from sub-category.
- **Order lines:**
  - Orders not in `orders.csv` are excluded.
  - Negative quantities become absolute values.
  - Quantity 0 and invalid discounts (below 0 or above 100) are inferred from `line_total`.
  - Missing prices come from the product record.
  - `line_total` is recomputed as qty × price × (1 − discount).
- **Returns:** refund is capped at the line value, a missing refund equals the full line value, and a return covers the whole line.
- **Payments:** currency symbols and "INR" are stripped with no FX conversion, and only `Success` counts as paid.
- **Suppliers:** ratings above 5 are treated as a 10-point scale and halved.
- **Orphans:** order lines for unknown products stay in revenue as "Unmapped" but are left out of category, product and supplier KPIs. Orders from unknown customers are excluded from customer KPIs (toggle in KPI 2).

## KPI definitions

- **Net revenue** = qty × unit price × (1 − discount) − refunds
- **Return rate** = returned units ÷ ordered units
- **Supplier composite score** = 100 × weighted blend of min-max revenue, (1 − min-max average return rate) and rating ÷ 5
- **Repeat rate** = customers with a 2nd completed order within N days of their first ÷ customers with a completed order
- **Payment anomaly** = difference between successful payments and the order total beyond the tolerance (default ₹1)

## Deploying (optional)

Push the repo to GitHub, then deploy on [Streamlit Community Cloud](https://streamlit.io/cloud) by pointing it at `app.py`. If the data is private, do not commit the CSVs; supply them through a private repo or another storage option.

## License

Add a license of your choice (e.g. MIT) here.
