---
name: gas-station-counts-ratios
description: Count gas stations by Country and Segment, compare counts between countries (difference), or compute the percentage share of a segment (e.g. Premium) within a country's stations. Single-table gasstations queries.
tags: [birdbench, gasstations, count, percentage]
---

# Gas-station count / ratio / percentage patterns

## Execution policy (follow strictly)

1. You have loaded the right skill. Do NOT load any other skill, do NOT load
   this skill again, and do NOT call `describe_table` or `list_tables` - this
   skill already contains the exact schema and verified SQL.
2. Instantiate the matching template with the question's parameters
   (country, segment, currency, year, month, customer id, threshold, ...).
3. Validate that one final SQL once with `execute_sql`. If it succeeds, return
   the final JSON envelope immediately. No exploratory queries, no re-checks.


Use when the question counts gas stations, compares station counts between
countries, or asks what percentage of a country's stations fall in a segment.
Do NOT call describe_table: the full schema slice you need is below.

## Schema (exact)

- gasstations(GasStationID BIGINT PK, ChainID BIGINT, Country VARCHAR(16), Segment VARCHAR(64))
- Country values: 'CZE', 'SVK'. Segment values: 'Premium', 'Discount' (and others).
- Comparisons are binary-collation, case-sensitive: use exact literals above.

## Templates

1. Count stations of one segment in one country:
   `SELECT COUNT(GasStationID) FROM gasstations WHERE Country = {country} AND Segment = {segment}`
2. How many MORE stations country A has than country B for a segment:
   `SELECT SUM(IF(Country = {a}, 1, 0)) - SUM(IF(Country = {b}, 1, 0)) FROM gasstations WHERE Segment = {segment}`
3. Percentage of a segment within a country (full precision, NO ROUND):
   `SELECT CAST(SUM(IF(Segment = {segment}, 1, 0)) AS FLOAT) * 100 / COUNT(GasStationID) FROM gasstations WHERE Country = {country}`
4. Percentage of a segment within a country computed over the whole table:
   `SELECT CAST(SUM(IF(Country = {c} AND Segment = {s}, 1, 0)) AS FLOAT) * 100 / SUM(IF(Country = {c}, 1, 0)) FROM gasstations`

## Worked examples (verified on this database)

- "How many gas stations in CZE has Premium gas?"
  `SELECT COUNT(GasStationID) FROM gasstations WHERE Country = 'CZE' AND Segment = 'Premium'` -> 1114
- "How many more 'discount' gas stations does the Czech Republic have compared to Slovakia?"
  `SELECT SUM(IF(Country = 'CZE', 1, 0)) - SUM(IF(Country = 'SVK', 1, 0)) FROM gasstations WHERE Segment = 'Discount'` -> 176
- "What percentage of Slovakian gas stations are premium?"
  `SELECT CAST(SUM(IF(Segment = 'Premium', 1, 0)) AS FLOAT) * 100 / COUNT(GasStationID) FROM gasstations WHERE Country = 'SVK'` -> 35.68181818181818

## Output rules

- Return ONLY the asked-for value column; no extra columns (no labels, no row counts).
- Never wrap in ROUND(...) unless the question explicitly asks for rounding.
- "How many more X than Y" = count(X) - count(Y), a single scalar.
- Percentage = share * 100 / total, single scalar, full float precision.
