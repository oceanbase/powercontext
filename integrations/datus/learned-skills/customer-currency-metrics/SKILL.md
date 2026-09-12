---
name: customer-currency-metrics
description: Count or compare customers by Currency (EUR/CZK) and Segment (KAM/LAM/SME) - how many pay in a currency, how many more pay in X than Y, ratio of one currency group to another, or percentage paying in a currency. Single-table customers queries.
tags: [birdbench, customers, currency, count, ratio, percentage]
---

# Customer currency / segment count metrics

## Execution policy (follow strictly)

1. You have loaded the right skill. Do NOT load any other skill, do NOT load
   this skill again, and do NOT call `describe_table` or `list_tables` - this
   skill already contains the exact schema and verified SQL.
2. Instantiate the matching template with the question's parameters
   (country, segment, currency, year, month, customer id, threshold, ...).
3. Validate that one final SQL once with `execute_sql`. If it succeeds, return
   the final JSON envelope immediately. No exploratory queries, no re-checks.


Use when the question is about how many customers pay in EUR vs CZK, differences
or ratios between those groups, or what percentage of a segment pays in a
currency. Do NOT call describe_table: the schema slice is below.

## Schema (exact)

- customers(CustomerID BIGINT PK, Segment VARCHAR(32), Currency VARCHAR(8))
- Segment values: 'KAM', 'LAM', 'SME'. Currency values: 'EUR', 'CZK'.

## Templates

1. How many more customers pay in {x} than in {y} (optionally within a segment):
   `SELECT SUM(Currency = {x}) - SUM(Currency = {y}) FROM customers [WHERE Segment = {segment}]`
   (MySQL boolean sums; equivalent to SUM(IF(Currency={x},1,0)) - SUM(IF(Currency={y},1,0)).)
2. Ratio of customers paying in {x} to customers paying in {y} (full precision):
   `SELECT CAST(SUM(IF(Currency = {x}, 1, 0)) AS FLOAT) / SUM(IF(Currency = {y}, 1, 0)) FROM customers`
3. Percentage of customers (optionally in a segment) paying in {x}:
   `SELECT CAST(SUM(Currency = {x}) AS FLOAT) * 100 / COUNT(CustomerID) FROM customers [WHERE Segment = {segment}]`

## Worked examples (verified on this database)

- "Is it true that more SMEs pay in Czech koruna than in euros? If so, how many more?"
  `SELECT SUM(Currency = 'CZK') - SUM(Currency = 'EUR') FROM customers WHERE Segment = 'SME'` -> 23505
- "What percentage of KAM customers pay in euros?"
  `SELECT CAST(SUM(Currency = 'EUR') AS FLOAT) * 100 / COUNT(CustomerID) FROM customers WHERE Segment = 'KAM'` -> 3.480392156862745

## Output rules

- Single scalar column only; no extra columns.
- No ROUND(...) unless explicitly requested; keep full float precision.
- Difference = count_a - count_b; ratio = count_a / count_b; percentage = count_a * 100 / total.
