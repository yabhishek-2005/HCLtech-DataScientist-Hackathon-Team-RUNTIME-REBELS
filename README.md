# ShopSphere: Governed E-Commerce Analytics Engine
**Senior Data Engineer Technical Assessment & MVP Readout**  
**Architecture:** Medallion Lakehouse Engine (Bronze → Silver/Quarantine) + Power BI Semantic Gold Layer

---

## 1. Executive Summary & Architecture Overview

ShopSphere ingests high-velocity transactional and operational data across four siloed upstream source systems: **CRM (Customer Master)**, **E-Commerce Platform (Orders & Items)**, **Payment Gateway**, and **Procurement (Suppliers)**. The raw landing data arrived in inconsistent, dirty, and non-analytics-ready states featuring severe data defects: key collisions, broken referential integrity, mathematical anomalies in calculated fields, and mixed schema formats.

Our solution implements an end-to-end governed pipeline:
1. **Bronze Layer (Raw Landing):** Ingests raw CSV/JSON extracts verbatim preserving immutable auditability.
2. **Silver Layer (Python / Google Colab Cleansing & Quarantine):** Deterministic data cleansing, casing/syntax normalization, mathematical re-computation, and strict quarantine routing (zero silent drops).
3. **Gold Layer (Power BI Semantic Star Schema):** Analytical modeling, relationship mapping, DAX-driven KPI formulation, and executive dashboarding.

```
+---------------------------------------------------------------------------------------------------+
|                                 END-TO-END PIPELINE ARCHITECTURE                                  |
+---------------------------------------------------------------------------------------------------+
|  [UPSTREAM SOURCES]          [SILVER PIPELINE: PYTHON]              [GOLD & BI: POWER BI]         |
|  * CRM (customers.csv)   --> * Deduplication Engine             --> * Star Schema Modeling        |
|  * E-Comm (orders.csv)   --> * Schema Standardization (DD/MM/Y) --> * 1:M Active Relationships    |
|  * Items (order_items)   --> * Mathematical Recomputation       --> * DAX Formulation Engine      |
|  * Gateway (payments)    --> * Scale Normalization              --> * 8 Core KPIs + Dashboards    |
|  * Returns (returns)     -->                                                                      |
|                              |--> PASS: [Cleaned CSVs] ---------> (Import to Power BI)            |
|                              |--> FAIL: [Quarantine Files] -----> (Audit & Risk Review)           |
+---------------------------------------------------------------------------------------------------+
```

---

## 2. Data Cleaning & Quarantine Strategy (Silver Layer)

All pre-processing and data validation were executed using Python in Google Colab. The pipeline adheres to the senior engineering principle of **Zero Silent Drops**: records violating business integrity are isolated into reference quarantine datasets tagged with machine-readable failure reason codes.

### Detailed Entity-by-Entity Rules

| Dataset | Cleaning & Standardization Applied | Quarantine Isolation Criteria (`quarantine_*.csv`) |
| :--- | :--- | :--- |
| **`customers.csv`** | • Casing normalization (`PLATINUM` → `Platinum`, `glod` → `Gold`).<br>• Standardized booleans (`Y`, `1`, `yes` → `True`).<br>• Normalized city naming (`Bengaluru` → `Bangalore`).<br>• Formatted `signup_date` to uniform `DD/MM/YYYY`.<br>• Cleaned phone strings (stripped symbols, negative signs, leading country codes). | • `DUPLICATE_CUSTOMER_ID`<br>• `INVALID_OR_FUTURE_SIGNUP_DATE`<br>• `MALFORMED_OR_MISSING_EMAIL`<br>• `MISSING_ALL_CONTACT_INFO` |
| **`orders.csv`** | • Deduplicated re-sent order IDs (`O00012`, `O00108`, `O00170`).<br>• Harmonized status typos (`Complted`, `Cancled` → `Completed`, `Cancelled`).<br>• Unified channel tags (`app`, `APP`, `In-store` → `App`, `Store`, `Web`).<br>• Multi-format dates parsed to `DD/MM/YYYY`. | • Dropped duplicate keys; invalid dates flagged. |
| **`order_items.csv`** | • Deduplicated duplicate line keys.<br>• Recomputed missing unit prices from line total, discount, and quantity.<br>• Mathematical integrity enforcement: Recomputed derived $\text{line\_total} = \text{quantity} \times \text{unit\_price} \times (1 - \text{discount})$. | • `INVALID_QUANTITY_LEQ_ZERO`<br>• `INVALID_DISCOUNT_OUT_OF_RANGE` ($<0\%$ or $>100\%$)<br>• `MISSING_UNIT_PRICE` |
| **`returns.csv`** | • Standardized return status (`Approved`, `Rejected`, `Pending`).<br>• Corrected reason typos (`Damged` → `Damaged`).<br>• Normalized dates to `DD/MM/YYYY`.<br>• Missing refunds mapped to `N/A`. | • `RETURN_DATE_BEFORE_ORDER_DATE`<br>• `ORPHAN_ORDER_ITEM_ID`<br>• `MISSING_REFUND_AMOUNT`<br>• `DUPLICATE_RETURN_ID`<br>• `REFUND_EXCEEDS_LINE_TOTAL` |
| **`payments.csv`** | • Deduplicated payment transaction IDs.<br>• Standardized payment methods and statuses (`Success`, `Failed`, `Pending`). | • Duplicate payments or negative transaction amounts. |
| **`suppliers.json`** | • Inconsistent ratings across scales (1–5 vs 1–100) normalized to a standardized 0–100 index.<br>• Standardized preferred supplier booleans. | • Malformed records or non-positive contracts. |
| **`products.csv`** | • Standardized category and sub-category title casing.<br>• Standardized boolean flags for `is_active`. | • Non-positive prices (`unit_price <= 0` or `cost_price <= 0`). |

---

## 3. Power BI Relational Data Modeling (Gold Layer)

The cleaned CSV exports were ingested into Power BI Desktop to build a high-performance **Star Schema**. 

```
                                +--------------------+
                                |   dim_customers    |
                                +--------------------+
                                | PK  customer_id    |
                                |     customer_name  |
                                |     segment        |
                                |     city, state    |
                                +--------------------+
                                          | 1
                                          | 
                                          | M
+-------------------+           +--------------------+           +--------------------+
|   dim_products    | 1       M |  fact_order_items  | M       1 |     dim_orders     |
+-------------------+-----------+--------------------+-----------+--------------------+
| PK  product_id    |           | PK  order_item_id  |           | PK  order_id       |
|     product_name  |           | FK  order_id       |           | FK  customer_id    |
|     category      |           | FK  product_id     |           |     order_date     |
|     cost_price    |           |     quantity       |           |     order_status   |
| FK  supplier_id   |           |     unit_price     |           |     channel        |
+-------------------+           |     discount_pct   |           +--------------------+
          | M                   |     line_total     |                     | 1
          |                     +--------------------+                     |
          | 1                             | 1                              | M
+-------------------+                     |                                +--------------------+
|   dim_suppliers   |                     | M                              |   fact_payments    |
+-------------------+           +--------------------+                     +--------------------+
| PK  supplier_id   |           |    fact_returns    |                     | PK  payment_id     |
|     supplier_name |           +--------------------+                     | FK  order_id       |
|     norm_rating   |           | PK  return_id      |                     |     payment_date   |
+-------------------+           | FK  order_item_id  |                     |     amount         |
                                |     refund_amount  |                     |     payment_status |
                                |     return_status  |                     +--------------------+
                                +--------------------+
```

### Relationship Topology:
1. `dim_customers` `[customer_id]` $(1) \longrightarrow (M)$ `dim_orders` `[customer_id]`
2. `dim_orders` `[order_id]` $(1) \longrightarrow (M)$ `fact_order_items` `[order_id]`
3. `dim_products` `[product_id]` $(1) \longrightarrow (M)$ `fact_order_items` `[product_id]`
4. `dim_suppliers` `[supplier_id]` $(1) \longrightarrow (M)$ `dim_products` `[supplier_id]`
5. `fact_order_items` `[order_item_id]` $(1) \longrightarrow (M)$ `fact_returns` `[order_item_id]`
6. `dim_orders` `[order_id]` $(1) \longrightarrow (M)$ `fact_payments` `[order_id]`

---

## 4. Power BI DAX Measures & KPI Formulations

All 8 assessment KPIs were authored in Power BI using optimized DAX:

### KPI 1: Net Revenue by Product Category, per Month
$$\text{Net Revenue} = \text{Gross Sales Revenue} - \text{Total Approved Refunds}$$
```dax
Gross_Revenue = 
CALCULATE(
    SUM(fact_order_items[line_total]),
    dim_orders[order_status] <> "Cancelled"
)

Total_Refunds = 
CALCULATE(
    SUM(fact_returns[refund_amount]),
    fact_returns[return_status] = "Approved"
)

Net_Revenue = [Gross_Revenue] - COALESCE([Total_Refunds], 0)
```

### KPI 2: Top 10 Customers by Net Lifetime Value (LTV)
```dax
Customer_Net_LTV = 
CALCULATE(
    [Net_Revenue],
    dim_orders[order_status] = "Completed"
)
```
*Visual Configuration: TopN filter applied on `dim_customers[customer_id]` ranked by `[Customer_Net_LTV]`.*

### KPI 3: Standardized Customer Segment Profile
```dax
Customer_Count = DISTINCTCOUNT(dim_customers[customer_id])

Average_Order_Value = 
DIVIDE(
    [Net_Revenue], 
    DISTINCTCOUNT(dim_orders[order_id]), 
    0
)
```

### KPI 4: Month-over-Month (MoM) Growth by Channel
```dax
Prior_Month_Net_Revenue = 
CALCULATE(
    [Net_Revenue],
    DATEADD(dim_date[Date], -1, MONTH)
)

MoM_Revenue_Growth_Pct = 
DIVIDE(
    [Net_Revenue] - [Prior_Month_Net_Revenue],
    [Prior_Month_Net_Revenue],
    0
)
```

### KPI 5: Product Return Rate (Outliers with Minimum Volume Threshold)
$$\text{Return Rate} = \frac{\sum \text{Returned Units}}{\sum \text{Ordered Units}} \quad (\text{Threshold: Ordered Units} \ge 20)$$
```dax
Units_Ordered = SUM(fact_order_items[quantity])

Units_Returned = 
CALCULATE(
    COUNT(fact_returns[return_id]),
    fact_returns[return_status] = "Approved"
)

Product_Return_Rate = 
IF(
    [Units_Ordered] >= 20,
    DIVIDE([Units_Returned], [Units_Ordered], 0),
    BLANK()
)
```

### KPI 6: Supplier Performance Scorecard
Composite metric incorporating normalized procurement rating, net revenue contribution, and product quality:
```dax
Supplier_Return_Rate = 
DIVIDE(
    CALCULATE(COUNT(fact_returns[return_id]), fact_returns[return_status] = "Approved"),
    SUM(fact_order_items[quantity]),
    0
)

Supplier_Composite_Score = 
AVERAGE(dim_suppliers[normalized_rating]) * 0.40 + 
(1 - [Supplier_Return_Rate]) * 100 * 0.40 + 
(DIVIDE([Net_Revenue], 10000, 0)) * 0.20
```

### KPI 7: Customer Repeat-Purchase Rate (90-Day Retention Cohort)
Evaluates percentage of customers who successfully placed an additional completed order within 90 days of their initial order:
```dax
Has_Repeat_Order_90D = 
VAR CurrentCust = SELECTEDVALUE(dim_customers[customer_id])
VAR FirstOrderDate = 
    CALCULATE(
        MIN(dim_orders[order_date]),
        FILTER(ALL(dim_orders), dim_orders[customer_id] = CurrentCust && dim_orders[order_status] = "Completed")
    )
VAR RepeatOrderCount = 
    CALCULATE(
        COUNTROWS(dim_orders),
        FILTER(
            ALL(dim_orders),
            dim_orders[customer_id] = CurrentCust &&
            dim_orders[order_status] = "Completed" &&
            dim_orders[order_date] > FirstOrderDate &&
            dim_orders[order_date] <= FirstOrderDate + 90
        )
    )
RETURN IF(RepeatOrderCount > 0, 1, 0)
```

### KPI 8: Payment Reconciliation & Gateway Anomaly Engine
Calculates payment mismatches per order to catch financial leakages:
```dax
Total_Order_Amount = SUM(fact_order_items[line_total])

Successful_Paid_Amount = 
CALCULATE(
    SUM(fact_payments[amount]),
    fact_payments[payment_status] = "Success"
)

Payment_Audit_Status = 
SWITCH(
    TRUE(),
    ISBLANK([Successful_Paid_Amount]), "NO_PAYMENT_CAPTURED",
    ABS([Total_Order_Amount] - [Successful_Paid_Amount]) <= 0.05, "RECONCILED",
    [Successful_Paid_Amount] < [Total_Order_Amount], "UNDERPAID",
    [Successful_Paid_Amount] > [Total_Order_Amount], "OVERPAID",
    "INVESTIGATE"
)
```

---

## 5. GenAI Usage Log (Senior Engineering Audit)

As required by the assessment guidelines, GenAI was utilized responsibly to accelerate boilerplate coding and stress-test assumptions:

| AI Tool | Prompt Given | Output & Flaw Caught | Human Senior Engineer Correction |
| :--- | :--- | :--- | :--- |
| **Claude / ChatGPT** | *"Write a DAX measure for Net Customer LTV across orders, items, and returns."* | The AI model performed a simple `SUM(orders[total]) - SUM(returns[refund])` without filtering out `Cancelled` order statuses. | Manually re-engineered the measure to explicitly filter for `order_status = "Completed"` and `return_status = "Approved"` to prevent severe LTV inflation. |
| **Claude / ChatGPT** | *"Generate Python script to clean returns.csv and flag date issues."* | The code blindly converted strings to dates using `errors='coerce'` without comparing against the parent order date. | Added multi-table validation to identify anachronistic returns (`return_date < order_date`) and routed 26 rows to `quarantine_returns.csv`. |
| **Claude / ChatGPT** | *"Write DAX for Supplier Scorecard combining ratings and returns."* | Suggested multiplying un-normalized ratings directly with raw revenue numbers, distorting scale. | Created min-max index normalization across heterogeneous scales (1–5 vs 1–100) before weighting. |

---

## 6. How to Reproduce & Execute

1. **Clean Raw Data:** Run the Python cleaning scripts in Google Colab on all 7 raw source files.
2. **Review Quarantines:** Inspect output files (`quarantine_returns.csv`, `quarantine_customers.csv`, `quarantine_order_items.csv`) for governance auditing.
3. **Open Power BI Desktop:**
   * Import the generated `cleaned_*.csv` files.
   * Verify table relationships as documented in Section 3.
   * Open the DAX view to review/modify measures.
4. **Interact with Dashboards:** View the 8 interactive reports configured with slicers for channel, customer segment, and calendar periods.
