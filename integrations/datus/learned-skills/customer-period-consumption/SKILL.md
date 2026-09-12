---
name: customer-period-consumption
description: Consumption for a specific customer ID or month range in yearmonth - totals between months, comparing two customers or two months, peak/least month of a segment, or reverse lookup (which customer/attribute matches a known consumption value).
tags: [birdbench, yearmonth, customer, month, period, compare]
---

# Per-customer / per-period consumption patterns

## Execution policy (follow strictly)

1. You have loaded the right skill. Do NOT load any other skill, do NOT load
   this skill again, and do NOT call `describe_table` or `list_tables` - this
   skill already contains the exact schema and verified SQL.
2. Instantiate the matching template with the question's parameters
   (country, segment, currency, year, month, customer id, threshold, ...).
3. Validate that one final SQL once with `execute_sql`. If it succeeds, return
   the final JSON envelope immediately. No exploratory queries, no re-checks.


Use when the question names a specific customer (by ID or by a known
Consumption value) or a month/time range in yearmonth. Do NOT call
describe_table: the schema slice is below.

## Schema (exact)

- yearmonth(CustomerID BIGINT, Date VARCHAR(6) 'YYYYMM', Consumption DOUBLE, PK(Date, CustomerID))
- customers(CustomerID BIGINT PK, Segment in ('KAM','LAM','SME'), Currency in ('EUR','CZK'))
- A month range is Date BETWEEN 'YYYYMM' AND 'YYYYMM'; a single month is Date = 'YYYYMM'.

## Templates (T1 = customers, T2 = yearmonth)

1. Total consumption of one customer over a month range:
   `SELECT SUM(Consumption) FROM yearmonth WHERE CustomerID = {cid} AND Date BETWEEN {start_month} AND {end_month}`
2. Compare consumption of two customers (or one customer vs another month):
   difference of two sums, e.g.
   `SELECT SUM(IF(CustomerID = {cid1}, Consumption, 0)) - SUM(IF(CustomerID = {cid2}, Consumption, 0)) FROM yearmonth WHERE Date = {month}`
   or two SUM(...) subquery difference; return a single scalar.
3. Peak / least month (returns the month label 'MM'):
   `SELECT SUBSTR(T2.Date, 5, 2) FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE SUBSTR(T2.Date, 1, 4) = {year} AND T1.Segment = {segment} GROUP BY SUBSTR(T2.Date, 5, 2) ORDER BY SUM(T2.Consumption) {DESC|ASC} LIMIT 1`
4. Highest monthly consumption in a year (= max over MONTHLY TOTALS, one
   SUM per month, not a single row max):
   `SELECT MAX(monthly_total) FROM (SELECT SUM(T2.Consumption) AS monthly_total FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE SUBSTR(T2.Date, 1, 4) = {year} GROUP BY SUBSTR(T2.Date, 5, 2)) AS sub`
5. Reverse lookup by known consumption value (find customer/attribute):
   `SELECT T2.{attribute} FROM yearmonth AS T1 INNER JOIN customers AS T2 ON T1.CustomerID = T2.CustomerID WHERE T1.Date = {month} AND T1.Consumption = {value}`
6. Customer with least/most consumption in one month within a segment:
   `SELECT T1.CustomerID FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE T2.Date = {month} AND T1.Segment = {segment} GROUP BY T1.CustomerID ORDER BY SUM(T2.Consumption) ASC LIMIT 1`

## Worked examples (verified on this database)

- "What was the gas consumption peak month for SME customers in 2013?"
  `SELECT SUBSTR(T2.Date, 5, 2) FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE SUBSTR(T2.Date, 1, 4) = '2013' AND T1.Segment = 'SME' GROUP BY SUBSTR(T2.Date, 5, 2) ORDER BY SUM(T2.Consumption) DESC LIMIT 1` -> '04'
- "Which SME customer consumed the least in June 2012?"
  `SELECT T1.CustomerID FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE T2.Date = '201206' AND T1.Segment = 'SME' GROUP BY T1.CustomerID ORDER BY SUM(T2.Consumption) ASC LIMIT 1` -> 27338
- "There's one customer spent 214582.17 in the June of 2013, which currency did he/she use?"
  `SELECT T2.Currency FROM yearmonth AS T1 INNER JOIN customers AS T2 ON T1.CustomerID = T2.CustomerID WHERE T1.Date = '201306' AND T1.Consumption = 214582.17` -> 'CZK'

## Output rules

- Return exactly the asked column; single row unless a list is asked.
- No ROUND(...) unless asked; keep full precision.
- "How much more did A consume than B" = one scalar difference, no extra columns.
