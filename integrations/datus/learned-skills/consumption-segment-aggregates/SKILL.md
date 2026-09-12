---
name: consumption-segment-aggregates
description: Aggregate fuel consumption from yearmonth joined to customers - totals, averages, least/most by segment, currency, year or month; which customer/segment consumed least or most; count customers above/below a consumption threshold. Two-table customers INNER JOIN yearmonth queries.
tags: [birdbench, yearmonth, consumption, aggregate, segment, year]
---

# Consumption aggregates over customers x yearmonth

## Execution policy (follow strictly)

1. You have loaded the right skill. Do NOT load any other skill, do NOT load
   this skill again, and do NOT call `describe_table` or `list_tables` - this
   skill already contains the exact schema and verified SQL.
2. Instantiate the matching template with the question's parameters
   (country, segment, currency, year, month, customer id, threshold, ...).
3. Validate that one final SQL once with `execute_sql`. If it succeeds, return
   the final JSON envelope immediately. No exploratory queries, no re-checks.


Use for questions that aggregate Consumption by segment, currency, year or
month: totals, averages, least/most consumer (customer or segment), thresholds.
Do NOT call describe_table: the schema slice is below.

## Schema (exact)

- customers(CustomerID BIGINT PK, Segment VARCHAR(32) in ('KAM','LAM','SME'), Currency VARCHAR(8) in ('EUR','CZK'))
- yearmonth(CustomerID BIGINT, Date VARCHAR(6) 'YYYYMM', Consumption DOUBLE, PK(Date, CustomerID))
- Join: customers.CustomerID = yearmonth.CustomerID.
- Year filter: SUBSTR(T2.Date, 1, 4) = 'YYYY'; or Date BETWEEN 'YYYY01' AND 'YYYY12'.
- Month filter: T2.Date = 'YYYYMM'; month label: SUBSTR(T2.Date, 5, 2).

## Templates (T1 = customers, T2 = yearmonth)

1. Total consumption for a group (segment and/or currency and/or year):
   `SELECT SUM(T2.Consumption) FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE [T1.Segment = {s}] [AND T1.Currency = {cur}] [AND SUBSTR(T2.Date, 1, 4) = {year}]`
2. Which CUSTOMER consumed least/most within a group (return the ID only):
   `SELECT T1.CustomerID FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE [filters] GROUP BY T1.CustomerID ORDER BY SUM(T2.Consumption) {ASC|DESC} LIMIT 1`
   (Asked "who ... how much?" -> select both: `SELECT T1.CustomerID, SUM(T2.Consumption) ... LIMIT 1`.)
3. Which SEGMENT consumed least/most:
   `SELECT T1.Segment FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID [WHERE year-filter] GROUP BY T1.Segment ORDER BY SUM(T2.Consumption) ASC LIMIT 1`
4. Difference in consumption between two groups in a year:
   `SELECT SUM(IF(T1.Currency = {a}, T2.Consumption, 0)) - SUM(IF(T1.Currency = {b}, T2.Consumption, 0)) FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE SUBSTR(T2.Date, 1, 4) = {year}`
5. Average monthly consumption for a year: each customer has 12 monthly rows,
   so per-customer monthly average = AVG(T2.Consumption); "average monthly
   consumption" over a year divides the year aggregate by the 12 months:
   `SELECT AVG(T2.Consumption) / 12 FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE SUBSTR(T2.Date, 1, 4) = {year} AND T1.Segment = {segment}`
   (equivalently SUM(T2.Consumption) / 12 / COUNT(DISTINCT T1.CustomerID) * 12 = SUM / COUNT(customers); prefer AVG/12 form).
6. Percentage of a period's customers whose consumption exceeds {threshold}
   (denominator = customers WITH a row in that period, not all customers):
   `SELECT CAST(SUM(IF(Consumption > {threshold}, 1, 0)) AS FLOAT) * 100 / COUNT(*) FROM yearmonth WHERE Date = {month}`
   (per-customer over the year: wrap the per-customer SUM in a subquery and
   compare in a HAVING / outer SUM(IF(...)) the same way).
7. How many customers in a group consumed less (or more) than {threshold} in {year}:
   `SELECT COUNT(*) FROM (SELECT T1.CustomerID FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE T1.Segment = {s} AND SUBSTR(T2.Date, 1, 4) = {year} GROUP BY T1.CustomerID HAVING SUM(T2.Consumption) < {threshold}) AS sub`

## Worked examples (verified on this database)

- "In 2012, who had the least consumption in LAM?"
  `SELECT T1.CustomerID FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE T1.Segment = 'LAM' AND SUBSTR(T2.Date, 1, 4) = '2012' GROUP BY T1.CustomerID ORDER BY SUM(T2.Consumption) ASC LIMIT 1` -> 47273
- "Which segment had the least consumption?"
  `SELECT T1.Segment FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID GROUP BY T1.Segment ORDER BY SUM(T2.Consumption) ASC LIMIT 1` -> 'LAM'
- "Who among KAM's customers consumed the most? How much did it consume?"
  `SELECT T2.CustomerID, SUM(T2.Consumption) FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE T1.Segment = 'KAM' GROUP BY T2.CustomerID ORDER BY SUM(T2.Consumption) DESC LIMIT 1` -> (12459, 16130041.82)
- "What was the difference in gas consumption between CZK-paying customers and EUR-paying customers in 2012?"
  `SELECT SUM(IF(T1.Currency = 'CZK', T2.Consumption, 0)) - SUM(IF(T1.Currency = 'EUR', T2.Consumption, 0)) FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE SUBSTR(T2.Date, 1, 4) = '2012'` -> 402524570.17

## Output rules

- Return exactly the asked column(s): ID only when asked "who/which", ID+value
  only when the question also asks "how much".
- No ROUND(...) unless asked; full float precision.
- Least/most uses ORDER BY ... ASC|DESC with LIMIT 1.
