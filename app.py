"""
E-commerce Analytics Dashboard (8 KPIs + 1 stretch KPI)
Run:  python -m streamlit run app.py
Needs: streamlit, pandas, numpy  (altair ships with streamlit)

Put the CSVs next to this file:
customers.csv, orders.csv, order_items.csv, payments.csv, products.csv,
returns.csv and suppliers.csv (or the original suppliers_json.txt).
"""
import difflib
import json
import re
from datetime import datetime
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

# ----------------------------------------------------------------------------
# 1. CLEANING HELPERS
# ----------------------------------------------------------------------------
TRUE_SET = {"y", "yes", "true", "1", "t"}
FALSE_SET = {"n", "no", "false", "0", "f"}


def to_bool(s: pd.Series) -> pd.Series:
    v = s.astype(str).str.strip().str.lower()
    return v.map(lambda x: True if x in TRUE_SET else (False if x in FALSE_SET else np.nan))


def to_num(s: pd.Series) -> pd.Series:
    """'₹210,054.96' / '$1,234.5' / '73078.38 INR' / '-70.38' -> float."""
    cleaned = s.astype(str).str.replace(r"[^0-9.\-]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


def canon(val, canon_list, cutoff=0.6):
    """Map typos / case variants to the closest canonical label."""
    if pd.isna(val):
        return np.nan
    v = re.sub(r"\s+", " ", str(val).strip().lower()).replace(" and ", " & ")
    lookup = {c.lower(): c for c in canon_list}
    if v in lookup:
        return lookup[v]
    close = difflib.get_close_matches(v, list(lookup), n=1, cutoff=cutoff)
    return lookup[close[0]] if close else str(val).strip().title()


def clean_channel(v):
    if pd.isna(v):
        return np.nan
    v = str(v).lower()
    if "store" in v:
        return "Store"
    if "app" in v:
        return "App"
    if "web" in v:
        return "Web"
    return str(v).title()


def clean_city(v):
    if pd.isna(v):
        return np.nan
    v = str(v).strip().lower()
    return {"bangalore": "Bengaluru", "bengaluru": "Bengaluru", "new delhi": "Delhi"}.get(v, v.title())


def parse_dates(s: pd.Series) -> pd.Series:
    """Parses YYYY-MM-DD, DD-Mon-YYYY, DD-MM-YYYY and DD/MM/YYYY.
    Every numeric day/month date is treated as DD-MM-YYYY (day first)."""

    def one(x):
        if pd.isna(x):
            return pd.NaT
        x = str(x).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x):
            fmt = "%Y-%m-%d"
        elif re.fullmatch(r"\d{1,2}-[A-Za-z]{3}-\d{4}", x):
            fmt = "%d-%b-%Y"
        elif re.fullmatch(r"\d{1,2}-\d{1,2}-\d{4}", x):
            fmt = "%d-%m-%Y"
        elif re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", x):
            fmt = "%d/%m/%Y"
        else:
            return pd.NaT
        try:
            return pd.Timestamp(datetime.strptime(x, fmt))
        except ValueError:
            return pd.NaT

    return pd.to_datetime(s.map(one))


def minmax(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    rng = s.max() - s.min()
    return (s - s.min()) / rng if rng and rng > 0 else pd.Series(0.5, index=s.index)


def find_file(folder: Path, names):
    for base in (folder, folder / "Ecommerce_Analyics_DE_Source_Data"):
        for n in names:
            if (base / n).exists():
                return base / n
    return None


# ----------------------------------------------------------------------------
# 2. LOAD + CLEAN EVERYTHING  (cached)
# ----------------------------------------------------------------------------
DISCOUNT_STEPS = np.array([0, 5, 10, 15, 20])


def _fix_item(r):
    """Repair one order line. Components (qty, price, discount) are trusted over line_total."""
    qty, price, disc, lt = r["quantity"], r["unit_price"], r["discount_pct"], r["line_total"]
    if pd.isna(price) or price <= 0:
        price = r["prod_price"]
    disc_ok = pd.notna(disc) and 0 <= disc <= 100
    if pd.notna(qty) and qty < 0:
        qty = abs(qty)  # sign error: line_total shows the positive amount
    qty_ok = pd.notna(qty) and qty > 0
    d0 = disc if disc_ok else 0
    if pd.isna(price) and qty_ok and pd.notna(lt):
        price = lt / (qty * (1 - d0 / 100))  # price missing -> back out from line_total
    if not qty_ok and pd.notna(price) and price > 0 and pd.notna(lt):
        qty = max(1, round(lt / (price * (1 - d0 / 100))))  # qty 0 -> infer from line_total
    if not disc_ok and pd.notna(price) and price > 0 and pd.notna(qty) and qty > 0 and pd.notna(lt):
        implied = (1 - lt / (qty * price)) * 100  # bad discount (-10/150) -> infer valid step
        near = DISCOUNT_STEPS[np.abs(DISCOUNT_STEPS - implied).argmin()]
        disc = near if abs(near - implied) <= 0.6 else 0
    elif not disc_ok:
        disc = 0
    return pd.Series({"qty": qty, "unit_price": price, "discount_pct": disc})


@st.cache_data(show_spinner="Loading and cleaning data...")
def load_data(folder: str):
    base = Path(folder)
    dq = []  # data-quality log: (table, issue, count)

    def rd(name):
        p = find_file(base, [name])
        if p is None:
            raise FileNotFoundError(f"{name} not found in {base} (or its Ecommerce_Analyics_DE_Source_Data subfolder)")
        return pd.read_csv(p, dtype=str)

    # ---- customers -------------------------------------------------------
    cu = rd("customers.csv")
    n = len(cu)
    cu = cu.drop_duplicates().drop_duplicates("customer_id")
    dq.append(("customers", "duplicate rows removed", n - len(cu)))
    cu["customer_segment"] = cu["customer_segment"].map(
        lambda v: canon(v, ["Bronze", "Silver", "Gold", "Platinum"])).fillna("Unknown")
    cu["name"] = (cu["first_name"].fillna("") + " " + cu["last_name"].fillna("")).str.strip()
    cu["city"] = cu["city"].map(clean_city)
    cu["signup_date"] = parse_dates(cu["signup_date"])
    cu["is_active"] = to_bool(cu["is_active"])

    # ---- orders ----------------------------------------------------------
    od = rd("orders.csv")
    n = len(od)
    od = od.drop_duplicates().drop_duplicates("order_id")
    dq.append(("orders", "duplicate rows removed", n - len(od)))
    od["order_date"] = parse_dates(od["order_date"])
    od["order_status"] = od["order_status"].map(lambda v: canon(v, ["Completed", "Cancelled", "Returned", "Pending"]))
    od["channel"] = od["channel"].map(clean_channel)
    od["payment_method"] = od["payment_method"].map(
        lambda v: canon(v, ["COD", "Credit Card", "UPI", "Debit Card", "NetBanking"]))
    dq.append(("orders", "customer_id not in customers (orphans)", int((~od.customer_id.isin(cu.customer_id)).sum())))

    # ---- products --------------------------------------------------------
    pr = rd("products.csv")
    it_raw = rd("order_items.csv")
    it_raw["unit_price_n"] = to_num(it_raw["unit_price"])
    modal = it_raw[it_raw["unit_price_n"] > 0].groupby("product_id")["unit_price_n"].agg(lambda x: x.mode().iloc[0])
    pr["up"] = to_num(pr["unit_price"])
    pr["cost_price"] = to_num(pr["cost_price"])
    pr["modal"] = pr["product_id"].map(modal)
    pr["match"] = (pr["up"] - pr["modal"]).abs() < 0.01
    pr["valid"] = pr["up"] > 0
    pr["revised"] = pr["product_name"].str.contains("Revised", na=False)
    n = len(pr)
    # which record wins: valid (>0) price > price matching real transactions > "(Revised)" record
    pr = (pr.sort_values(["product_id", "valid", "match", "revised"], ascending=[True, False, False, False])
            .drop_duplicates("product_id"))
    dq.append(("products", "duplicate/conflicting product records resolved", n - len(pr)))
    pr["unit_price"] = np.where(pr["up"] > 0, pr["up"], pr["modal"])
    pr.loc[pr["cost_price"] <= 0, "cost_price"] = np.nan
    pr["product_name"] = (pr["product_name"].str.replace(r"\s*\(Revised\)", "", regex=True)
                          .fillna("Product " + pr["product_id"]))
    pr["category"] = pr["category"].map(
        lambda v: canon(v, ["Electronics", "Fashion", "Grocery", "Home & Kitchen", "Sports"]))
    sub_map = pr.dropna(subset=["category"]).groupby("sub_category")["category"].agg(lambda x: x.mode().iloc[0])
    miss = pr["category"].isna()
    pr.loc[miss, "category"] = pr.loc[miss, "sub_category"].map(sub_map)
    dq.append(("products", "missing category filled from sub_category", int(miss.sum())))
    pr["category"] = pr["category"].fillna("Unmapped")
    pr["is_active"] = to_bool(pr["is_active"])
    pr = pr[["product_id", "product_name", "category", "sub_category", "unit_price", "cost_price",
             "supplier_id", "is_active"]].rename(columns={"unit_price": "prod_price"})

    # ---- suppliers -------------------------------------------------------
    sp = find_file(base, ["suppliers.csv"])
    if sp is not None:
        su = pd.read_csv(sp, dtype=str)
    else:
        jp = find_file(base, ["suppliers_json.txt", "suppliers.json"])
        if jp is None:
            raise FileNotFoundError("suppliers.csv / suppliers_json.txt not found")
        su = pd.DataFrame(json.load(open(jp, encoding="utf-8"))).astype(object)
    n = len(su)
    su = su.drop_duplicates("supplier_id")
    dq.append(("suppliers", "duplicate supplier_id removed (first kept)", n - len(su)))
    su["rating"] = to_num(su["rating"])
    scaled = su["rating"] > 5
    su.loc[scaled, "rating"] = su.loc[scaled, "rating"] / 2
    dq.append(("suppliers", "ratings on 10-pt scale rescaled to 5-pt", int(scaled.sum())))
    su["country"] = su["country"].fillna("Unknown")
    su["is_preferred"] = to_bool(su["is_preferred"])
    su = su[["supplier_id", "supplier_name", "country", "rating", "is_preferred"]]
    dq.append(("products", "supplier_id not in suppliers", int((~pr.supplier_id.isin(su.supplier_id)).sum())))

    # ---- order items -----------------------------------------------------
    it = it_raw.drop(columns=["unit_price_n"]).copy()
    n = len(it)
    it = it.drop_duplicates().drop_duplicates("order_item_id")
    dq.append(("order_items", "duplicate rows removed", n - len(it)))
    for c in ["quantity", "unit_price", "discount_pct", "line_total"]:
        it[c] = to_num(it[c])
    dq.append(("order_items", "invalid qty (<=0)", int((it.quantity <= 0).sum())))
    dq.append(("order_items", "invalid discount (<0 or >100)", int(((it.discount_pct < 0) | (it.discount_pct > 100)).sum())))
    dq.append(("order_items", "missing unit_price (filled from product)", int(it.unit_price.isna().sum())))
    dq.append(("order_items", "non-positive unit_price replaced with valid product price", int((it.unit_price <= 0).sum())))
    orphan_o = ~it.order_id.isin(od.order_id)
    dq.append(("order_items", "order_id not in orders (excluded)", int(orphan_o.sum())))
    it = it[~orphan_o].copy()
    it = it.merge(pr[["product_id", "prod_price"]], on="product_id", how="left")
    dq.append(("order_items", "product_id not in products (kept as 'Unmapped')", int((~it.product_id.isin(pr.product_id)).sum())))
    fixed = it.apply(_fix_item, axis=1)
    it["recorded_total"] = it["line_total"]
    it[["qty", "unit_price", "discount_pct"]] = fixed[["qty", "unit_price", "discount_pct"]].values
    it["gross"] = it["qty"] * it["unit_price"]
    it["net_line"] = (it["gross"] * (1 - it["discount_pct"] / 100)).fillna(it["recorded_total"])
    dq.append(("order_items", "line_total mismatched components -> recomputed",
               int(((it["net_line"] - it["recorded_total"]).abs() > 1).sum())))

    # ---- returns ---------------------------------------------------------
    rt = rd("returns.csv")
    n = len(rt)
    rt = rt.drop_duplicates().drop_duplicates("return_id")
    dq.append(("returns", "duplicate rows removed", n - len(rt)))
    rt["return_date"] = parse_dates(rt["return_date"])
    rt["refund_amount"] = to_num(rt["refund_amount"])
    rt["return_status"] = rt["return_status"].map(lambda v: canon(v, ["Approved", "Rejected", "Pending"]))
    rt["return_reason"] = rt["return_reason"].map(
        lambda v: canon(v, ["Defective", "Damaged", "Not as Described", "Size Issue", "Wrong Item"]))
    orphan_r = ~rt.order_item_id.isin(it.order_item_id)
    dq.append(("returns", "order_item_id not in order_items (excluded)", int(orphan_r.sum())))
    dq.append(("returns", "missing refund_amount (filled with line total)", int(rt.refund_amount.isna().sum())))
    rt = rt[~orphan_r]

    # ---- payments --------------------------------------------------------
    pm = rd("payments.csv")
    n = len(pm)
    pm = pm.drop_duplicates().drop_duplicates("payment_id")
    dq.append(("payments", "duplicate rows removed", n - len(pm)))
    pm["amount"] = to_num(pm["amount"])
    pm["payment_date"] = parse_dates(pm["payment_date"])
    pm["payment_status"] = pm["payment_status"].astype(str).map(
        lambda v: "Success" if v.lower().startswith("succ") else canon(v, ["Failed", "Pending", "Refunded"]))
    pm["payment_method"] = pm["payment_method"].map(
        lambda v: canon(v, ["COD", "Credit Card", "UPI", "Debit Card", "NetBanking"]))
    dq.append(("payments", "order_id not in orders", int((~pm.order_id.isin(od.order_id)).sum())))

    # ---- fact table (one row per order line) ----------------------------
    fact = it.merge(od[["order_id", "customer_id", "order_date", "order_status", "channel"]], on="order_id", how="inner")
    fact = fact.merge(pr.drop(columns=["prod_price"]), on="product_id", how="left")
    fact["category"] = fact["category"].fillna("Unmapped")
    fact["product_name"] = fact["product_name"].fillna(fact["product_id"])
    fact["month"] = fact["order_date"].dt.to_period("M").dt.to_timestamp()
    fact["month_label"] = fact["order_date"].dt.strftime("%Y-%m")

    data = dict(customers=cu, orders=od, products=pr, suppliers=su, items=it, returns=rt, payments=pm, fact=fact)
    return data, pd.DataFrame(dq, columns=["table", "issue", "rows"])


# ----------------------------------------------------------------------------
# 3. KPI FUNCTIONS (pure pandas -> easy to test / reuse)
# ----------------------------------------------------------------------------
def apply_returns(fact, returns, counted_status):
    r = returns[returns["return_status"].isin(counted_status)].drop_duplicates("order_item_id")
    r = r[["order_item_id", "refund_amount"]]
    f = fact.merge(r, on="order_item_id", how="left", indicator=True)
    f["returned"] = f["_merge"] == "both"
    f = f.drop(columns="_merge")
    refund = f["refund_amount"].fillna(f["net_line"])
    f["refund"] = np.where(f["returned"], np.minimum(refund, f["net_line"]), 0.0)  # refund capped at line value
    f["net_rev"] = f["net_line"] - f["refund"]
    f["returned_qty"] = np.where(f["returned"], f["qty"], 0)
    return f.drop(columns="refund_amount")


def kpi1_net_revenue(f):
    g = f[f.category != "Unmapped"].groupby(["month", "month_label", "category"], as_index=False)["net_rev"].sum()
    return g.sort_values(["month", "category"])


def kpi2_top_customers(fr, customers, include_orphans=False, n=10):
    c = fr[fr.order_status == "Completed"]
    g = c.groupby("customer_id", as_index=False).agg(
        net_ltv=("net_rev", "sum"), gross_spend=("net_line", "sum"),
        refunds=("refund", "sum"), orders=("order_id", "nunique"))
    g = g.merge(customers[["customer_id", "name", "customer_segment", "city"]], on="customer_id", how="left")
    known = g["name"].notna()
    if include_orphans:
        g["name"] = g["name"].fillna("Unknown customer")
        g["customer_segment"] = g["customer_segment"].fillna("Unknown")
    else:
        g = g[known]
    g["label"] = g["name"] + " (" + g["customer_id"] + ")"
    return g.sort_values("net_ltv", ascending=False).head(n).reset_index(drop=True)


def kpi3_segments(f, customers):
    ov = f.groupby(["order_id", "customer_id"], as_index=False)["net_line"].sum()
    ov = ov.merge(customers[["customer_id", "customer_segment"]], on="customer_id", how="left")
    ov["customer_segment"] = ov["customer_segment"].fillna("Unknown (orphan)")
    seg = ov.groupby("customer_segment").agg(
        orders=("order_id", "nunique"), ordering_customers=("customer_id", "nunique"),
        avg_order_value=("net_line", "mean"), revenue=("net_line", "sum"))
    cc = customers.groupby("customer_segment")["customer_id"].nunique().rename("customers")
    out = pd.concat([cc, seg], axis=1).fillna(0).reset_index().rename(columns={"index": "customer_segment"})
    out[["customers", "orders", "ordering_customers"]] = out[["customers", "orders", "ordering_customers"]].astype(int)
    return out.sort_values("customers", ascending=False)


def kpi4_mom_by_channel(f):
    g = f.groupby(["channel", "month"]).agg(orders=("order_id", "nunique"), revenue=("net_rev", "sum")).reset_index()
    months = pd.date_range(f["month"].min(), f["month"].max(), freq="MS")
    parts = []
    for ch, d in g.groupby("channel"):
        d = d.set_index("month").reindex(months).fillna(0)
        d.index.name = "month"
        for col in ["orders", "revenue"]:
            prev = d[col].shift(1)
            d[f"{col}_mom_pct"] = np.where(prev > 0, (d[col] / prev - 1) * 100, np.nan)
        d["channel"] = ch
        parts.append(d.reset_index())
    out = pd.concat(parts, ignore_index=True)
    out["month_label"] = out["month"].dt.strftime("%Y-%m")
    return out


def product_return_table(f):
    g = f[f.category != "Unmapped"].groupby("product_id", as_index=False).agg(
        ordered_qty=("qty", "sum"), returned_qty=("returned_qty", "sum"), revenue=("net_rev", "sum"))
    g["return_rate"] = g["returned_qty"] / g["ordered_qty"]
    return g


def kpi5_return_rate(f, products, min_units):
    g = product_return_table(f).merge(products[["product_id", "product_name", "category", "supplier_id"]], on="product_id")
    elig = g[g.ordered_qty >= min_units].copy()
    elig["label"] = elig["product_id"] + " " + elig["product_name"]
    top = elig.sort_values(["return_rate", "ordered_qty"], ascending=[False, False]).head(10)
    bottom = elig.sort_values(["return_rate", "ordered_qty"], ascending=[True, False]).head(10)
    return top, bottom, len(g), len(elig)


def kpi6_supplier_scorecard(f, products, suppliers, w_rev, w_ret, w_rating):
    pr = product_return_table(f).merge(products[["product_id", "supplier_id"]], on="product_id")
    sup = pr.groupby("supplier_id").agg(
        revenue=("revenue", "sum"), avg_return_rate=("return_rate", "mean"),
        units_sold=("ordered_qty", "sum"), products_sold=("product_id", "nunique"))
    s = suppliers.merge(sup, on="supplier_id", how="left")
    s[["revenue", "units_sold", "products_sold"]] = s[["revenue", "units_sold", "products_sold"]].fillna(0)
    s["avg_return_rate"] = s["avg_return_rate"].fillna(s["avg_return_rate"].median())
    s["revenue_score"] = minmax(s["revenue"])
    s["return_score"] = 1 - minmax(s["avg_return_rate"])
    s["rating_norm"] = (s["rating"] / 5).clip(0, 1).fillna(s["rating"].median() / 5)
    tot_w = (w_rev + w_ret + w_rating) or 1
    s["composite_score"] = 100 * (w_rev * s["revenue_score"] + w_ret * s["return_score"]
                                  + w_rating * s["rating_norm"]) / tot_w
    s = s.sort_values("composite_score", ascending=False).reset_index(drop=True)
    s["rank"] = s.index + 1
    return s


def kpi7_repeat_rate(orders, customers, window=90, mature_only=False):
    o = orders[(orders.order_status == "Completed") & orders.customer_id.isin(customers.customer_id)]
    o = o.sort_values(["customer_id", "order_date", "order_id"]).copy()
    o["seq"] = o.groupby("customer_id").cumcount()
    first = o[o.seq == 0].set_index("customer_id")["order_date"].rename("first_date")
    o = o.join(first, on="customer_id")
    o["within"] = (o.seq > 0) & ((o.order_date - o.first_date).dt.days <= window)
    cust = o.groupby("customer_id").agg(first_date=("first_date", "first"), repeated=("within", "any")).reset_index()
    if mature_only:
        cust = cust[cust.first_date <= orders.order_date.max() - pd.Timedelta(days=window)]
    cust["cohort"] = cust["first_date"].dt.strftime("%Y-%m")
    return cust


def kpi8_payment_recon(fact, orders, payments, tol):
    tot = fact.groupby("order_id")["net_line"].sum().rename("order_total")
    ok = payments[payments.payment_status == "Success"]
    paid = ok.groupby("order_id")["amount"].sum().rename("paid_success")
    n_ok = ok.groupby("order_id").size().rename("success_payments")
    n_all = payments.groupby("order_id").size().rename("payment_rows")
    stat = payments.groupby("order_id")["payment_status"].agg(lambda s: ", ".join(sorted(set(s)))).rename("payment_statuses")
    r = orders[["order_id", "customer_id", "order_date", "order_status", "channel"]].join(
        pd.concat([tot, paid, n_ok, n_all, stat], axis=1), on="order_id")
    r[["paid_success", "success_payments", "payment_rows"]] = r[["paid_success", "success_payments", "payment_rows"]].fillna(0)
    r["difference"] = r["paid_success"] - r["order_total"].fillna(0)
    conds = [
        r["order_total"].isna(),
        r["success_payments"] == 0,
        r["success_payments"] > 1,
        r["difference"] < -tol,
        r["difference"] > tol,
    ]
    labels = ["No line items", "No successful payment", "Multiple success payments", "Underpaid", "Overpaid"]
    r["anomaly"] = np.select(conds, labels, default="Matched")
    r["conflicting_rows"] = r["payment_rows"] > 1
    orphans = payments[~payments.order_id.isin(orders.order_id)]
    return r, orphans


def kpi_margin_risk(f):
    d = f.dropna(subset=["cost_price"]).copy()
    d["eff_price"] = d["unit_price"] * (1 - d["discount_pct"] / 100)
    d = d[d["eff_price"] < d["cost_price"]]
    d["loss"] = (d["cost_price"] - d["eff_price"]) * d["qty"]
    g = d.groupby(["product_id", "product_name", "category"], as_index=False).agg(
        loss_lines=("order_item_id", "count"), qty_exposed=("qty", "sum"),
        revenue_exposed=("net_line", "sum"), est_loss=("loss", "sum"))
    return g.sort_values("est_loss", ascending=False)


# ----------------------------------------------------------------------------
# 4. UI HELPERS
# ----------------------------------------------------------------------------
def inr(x):
    return f"₹{x:,.0f}"


def show(df, name, fmt=None):
    d = df.copy()
    for c in d.select_dtypes("float").columns:
        d[c] = d[c].round(2)
    st.dataframe(d, hide_index=True)
    st.download_button("Download CSV", d.to_csv(index=False).encode(), f"{name}.csv", "text/csv", key=f"dl_{name}")


def hbar(df, x, y, title, fmt=",.0f", color=None):
    enc = dict(
        x=alt.X(f"{x}:Q", title=title, axis=alt.Axis(format=fmt)),
        y=alt.Y(f"{y}:N", sort="-x", title=None),
        tooltip=[y, alt.Tooltip(f"{x}:Q", format=fmt)])
    if color:
        enc["color"] = alt.Color(f"{color}:N")
    return alt.Chart(df).mark_bar().encode(**enc).properties(height=max(220, 28 * len(df)))


# ----------------------------------------------------------------------------
# 5. APP
# ----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="E-commerce KPI Dashboard", page_icon="📊", layout="wide")
    st.title("📊 E-commerce Analytics Dashboard")

    # ---------------- sidebar: data + global assumptions -------------------
    sb = st.sidebar
    sb.header("Data")
    folder = sb.text_input("Data folder", str(Path(__file__).parent))
    try:
        data, dq = load_data(folder)
    except Exception as e:
        st.error(f"Could not load data: {e}")
        st.stop()

    fact, customers, orders, products = data["fact"], data["customers"], data["orders"], data["products"]
    suppliers, returns, payments = data["suppliers"], data["returns"], data["payments"]

    sb.header("Filters")
    dmin, dmax = fact["order_date"].min().date(), fact["order_date"].max().date()
    rng = sb.date_input("Order date range", (dmin, dmax), min_value=dmin, max_value=dmax, format="DD/MM/YYYY")
    d1, d2 = (rng[0], rng[1]) if isinstance(rng, (tuple, list)) and len(rng) == 2 else (dmin, dmax)
    chans = sb.multiselect("Channel", sorted(fact.channel.dropna().unique()), default=sorted(fact.channel.dropna().unique()))
    all_status = ["Completed", "Returned", "Pending", "Cancelled"]
    rev_status = sb.multiselect("Order statuses counted as revenue", all_status, default=["Completed", "Returned"],
                                help="Used by KPIs 1, 3, 4, 5, 6. KPIs 2 and 7 always use Completed only; KPI 8 covers every order.")
    ret_status = sb.multiselect("Return statuses counted", ["Approved", "Pending", "Rejected"], default=["Approved"],
                                help="Returns with these statuses reduce revenue and count in return rates.")

    fr = apply_returns(fact, returns, ret_status)  # full history
    mask = ((fr.order_date.dt.date >= d1) & (fr.order_date.dt.date <= d2)
            & fr.channel.isin(chans) & fr.order_status.isin(rev_status))
    f = fr[mask]
    if f.empty:
        st.warning("No data for the selected filters.")
        st.stop()

    # ---------------- headline metrics ------------------------------------
    n_orders = f.order_id.nunique()
    m = st.columns(5)
    m[0].metric("Net revenue", inr(f.net_rev.sum()))
    m[1].metric("Orders", f"{n_orders:,}")
    m[2].metric("Avg order value", inr(f.net_line.sum() / n_orders))
    m[3].metric("Refunds", inr(f.refund.sum()))
    m[4].metric("Return rate (units)", f"{f.returned_qty.sum() / f.qty.sum():.1%}")

    tabs = st.tabs(["1 Net revenue", "2 Top customers", "3 Segments", "4 MoM by channel", "5 Product returns",
                    "6 Suppliers", "7 Repeat purchase", "8 Payment recon", "★ Margin risk", "Data quality"])

    # ---- KPI 1 -----------------------------------------------------------
    with tabs[0]:
        st.subheader("Net revenue by product category, per month")
        st.caption("Net revenue = qty × price × (1 − discount) − refunds. Unmapped products (no product record) excluded.")
        g = kpi1_net_revenue(f)
        cats = sorted(g.category.unique())
        sel = st.multiselect("Categories", cats, default=cats, key="k1c")
        g = g[g.category.isin(sel)]
        style = st.radio("Chart", ["Stacked", "Lines"], horizontal=True, key="k1s")
        base = alt.Chart(g).encode(
            x=alt.X("month_label:O", title="Month"),
            y=alt.Y("net_rev:Q", title="Net revenue (₹)", axis=alt.Axis(format=",.0f")),
            color="category:N", tooltip=["month_label", "category", alt.Tooltip("net_rev:Q", format=",.0f")])
        st.altair_chart(base.mark_bar() if style == "Stacked" else base.mark_line(point=True))
        pv = g.pivot_table(index="month_label", columns="category", values="net_rev", aggfunc="sum", fill_value=0)
        pv["Total"] = pv.sum(axis=1)
        show(pv.reset_index().rename(columns={"month_label": "month"}), "kpi1_net_revenue_by_category_month")

    # ---- KPI 2 -----------------------------------------------------------
    with tabs[1]:
        st.subheader("Top 10 customers by net lifetime value")
        st.caption("Completed orders only, net of returns, full history (ignores the date filter).")
        inc = st.checkbox("Include orphan customer IDs (orders whose customer isn't in customers.csv)", value=False)
        t = kpi2_top_customers(fr, customers, inc)
        st.altair_chart(hbar(t, "net_ltv", "label", "Net lifetime value (₹)", color="customer_segment"))
        show(t.drop(columns="label"), "kpi2_top_customers")

    # ---- KPI 3 -----------------------------------------------------------
    with tabs[2]:
        st.subheader("Standardized customer segment profile")
        st.caption("Segments cleaned (Glod→Gold, Broze→Bronze, Silverr→Silver, case fixed). AOV = net line value per order.")
        s = kpi3_segments(f, customers)
        c1, c2 = st.columns(2)
        c1.altair_chart(alt.Chart(s).mark_bar().encode(
            x=alt.X("customer_segment:N", sort="-y", title=None), y=alt.Y("customers:Q", title="Customers"),
            color="customer_segment:N", tooltip=["customer_segment", "customers"]).properties(title="Customers per segment"))
        c2.altair_chart(alt.Chart(s).mark_bar().encode(
            x=alt.X("customer_segment:N", sort="-y", title=None),
            y=alt.Y("avg_order_value:Q", title="Avg order value (₹)", axis=alt.Axis(format=",.0f")),
            color="customer_segment:N", tooltip=["customer_segment", alt.Tooltip("avg_order_value:Q", format=",.0f")]
        ).properties(title="Average order value per segment"))
        show(s, "kpi3_segment_profile")

    # ---- KPI 4 -----------------------------------------------------------
    with tabs[3]:
        st.subheader("Month-over-month order volume & revenue growth, by channel")
        mm = kpi4_mom_by_channel(f)
        metric = st.radio("Metric", ["Revenue", "Orders"], horizontal=True, key="k4m")
        col, pct = ("revenue", "revenue_mom_pct") if metric == "Revenue" else ("orders", "orders_mom_pct")
        c1, c2 = st.columns(2)
        c1.altair_chart(alt.Chart(mm).mark_line(point=True).encode(
            x=alt.X("month_label:O", title="Month"), y=alt.Y(f"{col}:Q", title=metric), color="channel:N",
            tooltip=["channel", "month_label", alt.Tooltip(f"{col}:Q", format=",.0f")]).properties(title=f"{metric} by channel"))
        c2.altair_chart(alt.Chart(mm.dropna(subset=[pct])).mark_bar().encode(
            x=alt.X("month_label:O", title="Month"), xOffset="channel:N", y=alt.Y(f"{pct}:Q", title="MoM change (%)"),
            color="channel:N", tooltip=["channel", "month_label", alt.Tooltip(f"{pct}:Q", format=".1f")]
        ).properties(title=f"{metric} MoM % change"))
        st.caption("MoM % is blank where the previous month was zero.")
        show(mm.drop(columns=["month"]), "kpi4_mom_by_channel")

    # ---- KPI 5 -----------------------------------------------------------
    with tabs[4]:
        st.subheader("Product return rate — Top 10 and Bottom 10")
        st.caption("Return rate = returned units ÷ ordered units (a return is assumed to cover the whole order line).")
        mx = int(product_return_table(f).ordered_qty.max())
        thr = st.slider("Minimum units ordered", 1, max(mx, 2), min(20, mx), key="k5t")
        top, bot, n_all, n_el = kpi5_return_rate(f, products, thr)
        st.write(f"{n_el} of {n_all} products meet the threshold.")
        c1, c2 = st.columns(2)
        c1.altair_chart(hbar(top, "return_rate", "label", "Return rate", ".0%").properties(title="Highest return rate"))
        c2.altair_chart(hbar(bot, "return_rate", "label", "Return rate", ".0%").properties(title="Lowest return rate"))
        cols = ["product_id", "product_name", "category", "ordered_qty", "returned_qty", "return_rate"]
        show(pd.concat([top.assign(group="Top 10")[["group"] + cols], bot.assign(group="Bottom 10")[["group"] + cols]]),
             "kpi5_product_return_rate")

    # ---- KPI 6 -----------------------------------------------------------
    with tabs[5]:
        st.subheader("Supplier performance scorecard")
        st.caption("Composite = weighted blend of revenue (min-max), 1 − avg product return rate (min-max) and rating ÷ 5. "
                   "Ratings >5 were on a 10-pt scale and are halved.")
        c = st.columns(3)
        wr = c[0].slider("Weight: revenue", 0.0, 1.0, 0.4, 0.05)
        wt = c[1].slider("Weight: low returns", 0.0, 1.0, 0.3, 0.05)
        wq = c[2].slider("Weight: quality rating", 0.0, 1.0, 0.3, 0.05)
        sc = kpi6_supplier_scorecard(f, products, suppliers, wr, wt, wq)
        sc["label"] = sc["supplier_id"] + " " + sc["supplier_name"]
        st.altair_chart(hbar(sc, "composite_score", "label", "Composite score (0-100)", ".1f"))
        show(sc[["rank", "supplier_id", "supplier_name", "country", "is_preferred", "revenue", "avg_return_rate",
                 "rating", "composite_score"]], "kpi6_supplier_scorecard")

    # ---- KPI 7 -----------------------------------------------------------
    with tabs[6]:
        st.subheader("Customer repeat-purchase rate (90 days)")
        st.caption("Share of customers with a 2nd completed order within N days of their first completed order. "
                   "Known customers only; full history, first order must fall in the selected date range.")
        c1, c2 = st.columns(2)
        win = c1.slider("Window (days)", 30, 180, 90, 15)
        mature = c2.checkbox("Only customers whose first order is ≥ window days before the last order date", value=False)
        cu = kpi7_repeat_rate(orders, customers, win, mature)
        cu = cu[(cu.first_date.dt.date >= d1) & (cu.first_date.dt.date <= d2)]
        if cu.empty:
            st.info("No customers in range.")
        else:
            a = st.columns(3)
            a[0].metric("Repeat rate", f"{cu.repeated.mean():.1%}")
            a[1].metric("Customers with a completed order", f"{len(cu):,}")
            a[2].metric("Repeat customers", f"{int(cu.repeated.sum()):,}")
            co = cu.groupby("cohort").agg(customers=("customer_id", "count"), repeat_rate=("repeated", "mean")).reset_index()
            st.altair_chart(alt.Chart(co).mark_bar().encode(
                x=alt.X("cohort:O", title="First-order month"), y=alt.Y("repeat_rate:Q", title="Repeat rate", axis=alt.Axis(format=".0%")),
                tooltip=["cohort", "customers", alt.Tooltip("repeat_rate:Q", format=".0%")]).properties(title="Repeat rate by first-order cohort"))
            show(co, "kpi7_repeat_by_cohort")

    # ---- KPI 8 -----------------------------------------------------------
    with tabs[7]:
        st.subheader("Payment reconciliation & anomaly report")
        st.caption("Order total = sum of cleaned line values (before refunds). Paid = sum of 'Success' payments. "
                   "Applies the date/channel filters but covers every order status.")
        tol = st.number_input("Match tolerance (₹)", 0.0, 1000.0, 1.0, 0.5)
        o_f = orders[(orders.order_date.dt.date >= d1) & (orders.order_date.dt.date <= d2) & orders.channel.isin(chans)]
        rec, orph = kpi8_payment_recon(fact, o_f, payments, tol)
        stat_sel = st.multiselect("Order status", all_status, default=all_status, key="k8s")
        rec = rec[rec.order_status.isin(stat_sel)]
        cnt = rec.anomaly.value_counts()
        a = st.columns(6)
        a[0].metric("Orders checked", f"{len(rec):,}")
        a[1].metric("Matched", f"{cnt.get('Matched', 0):,}")
        a[2].metric("Underpaid", f"{cnt.get('Underpaid', 0):,}")
        a[3].metric("Overpaid", f"{cnt.get('Overpaid', 0):,}")
        a[4].metric("No success payment", f"{cnt.get('No successful payment', 0):,}")
        a[5].metric("Orders with >1 payment row", f"{int(rec.conflicting_rows.sum()):,}")
        cd = cnt.rename_axis("anomaly").reset_index(name="orders")
        st.altair_chart(alt.Chart(cd).mark_bar().encode(
            x=alt.X("orders:Q"), y=alt.Y("anomaly:N", sort="-x", title=None), color="anomaly:N",
            tooltip=["anomaly", "orders"]).properties(height=240))
        pick = st.multiselect("Show anomaly types", sorted(rec.anomaly.unique()),
                              default=[a_ for a_ in rec.anomaly.unique() if a_ != "Matched"], key="k8a")
        show(rec[rec.anomaly.isin(pick)].sort_values("difference"), "kpi8_payment_reconciliation")
        if len(orph):
            with st.expander(f"Payments referencing unknown orders ({len(orph)})"):
                show(orph, "kpi8_orphan_payments")

    # ---- Stretch ---------------------------------------------------------
    with tabs[8]:
        st.subheader("★ Margin risk — products sold below cost")
        mr = kpi_margin_risk(f)
        if mr.empty:
            st.success("No loss-making lines in the current selection.")
        else:
            a = st.columns(4)
            a[0].metric("Products at risk", f"{mr.product_id.nunique()}")
            a[1].metric("Loss-making lines", f"{int(mr.loss_lines.sum())}")
            a[2].metric("Revenue exposed", inr(mr.revenue_exposed.sum()))
            a[3].metric("Estimated loss", inr(mr.est_loss.sum()))
            mr["label"] = mr["product_id"] + " " + mr["product_name"]
            st.altair_chart(hbar(mr.head(10), "est_loss", "label", "Estimated loss (₹)", color="category"))
            show(mr.drop(columns="label"), "stretch_margin_risk")

    # ---- Data quality ----------------------------------------------------
    with tabs[9]:
        st.subheader("Data-quality log & assumptions")
        st.dataframe(dq[dq.rows > 0], hide_index=True)
        st.markdown(
            """
**Rules applied**
- Exact duplicate rows dropped; duplicate IDs keep the first record.
- **Products:** conflicting records resolved by choosing the one whose price matches actual order-line prices (the "(Revised)" row); non-positive prices fall back to the modal transaction price; missing category filled from sub_category.
- **Order lines:** orphan orders excluded; negative qty → absolute value; qty 0 and invalid discounts (<0, >100) inferred from `line_total`; missing price from product price; `line_total` is **recomputed** from qty × price × (1 − discount).
- **Returns:** refund capped at line value; missing refund = full line value; returned qty = whole line.
- **Payments:** currency symbols/"INR" stripped, no FX conversion (magnitudes are consistent); only `Success` counts as paid.
- **Orphans:** order lines pointing to unknown products stay in revenue as "Unmapped" but are left out of category/product/supplier KPIs; orders from unknown customers are excluded from customer KPIs (toggle in KPI 2).
- **Dates:** all numeric dates are read as **DD-MM-YYYY** (day first), whether written with `-` or `/`. `YYYY-MM-DD` and `DD-Mon-YYYY` are also recognised.
"""
        )


if __name__ == "__main__":
    main()