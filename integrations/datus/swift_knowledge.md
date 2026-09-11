# Database catalog: `birdbench` (MySQL / OceanBase dialect)

You are answering analytical questions over one database, `birdbench`, with the
complete schema below. The schema is authoritative and complete: never call
`list_tables` or `describe_table`, and never re-derive the schema by probing.

## Tables

```sql
CREATE TABLE `customers` (
  `CustomerID` bigint(20) NOT NULL,
  `Segment` varchar(32) DEFAULT NULL,
  `Currency` varchar(8) DEFAULT NULL,
  PRIMARY KEY (`CustomerID`)
);
CREATE TABLE `gasstations` (
  `GasStationID` bigint(20) NOT NULL,
  `ChainID` bigint(20) DEFAULT NULL,
  `Country` varchar(16) DEFAULT NULL,
  `Segment` varchar(64) DEFAULT NULL,
  PRIMARY KEY (`GasStationID`)
);
CREATE TABLE `products` (
  `ProductID` bigint(20) NOT NULL,
  `Description` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`ProductID`)
);
CREATE TABLE `transactions_1k` (
  `TransactionID` bigint(20) NOT NULL,
  `Date` date DEFAULT NULL,
  `Time` varchar(16) DEFAULT NULL,
  `CustomerID` bigint(20) DEFAULT NULL,
  `CardID` bigint(20) DEFAULT NULL,
  `GasStationID` bigint(20) DEFAULT NULL,
  `ProductID` bigint(20) DEFAULT NULL,
  `Amount` bigint(20) DEFAULT NULL,
  `Price` double DEFAULT NULL,
  PRIMARY KEY (`TransactionID`)
);
CREATE TABLE `yearmonth` (
  `CustomerID` bigint(20) NOT NULL,
  `Date` varchar(6) NOT NULL,  -- 'YYYYMM'
  `Consumption` double DEFAULT NULL,
  PRIMARY KEY (`Date`, `CustomerID`)
);
```

## Domain facts (verified against this instance)

- `gasstations.Country` values: `CZE`, `SVK`.
- `gasstations.Segment` values: `Discount`, `Noname`, `Other`, `Premium`, `Value for money`.
- `customers.Currency` values: `CZK`, `EUR`. `customers.Segment` values: `KAM`, `LAM`, `SME`.
- `products` holds fuel and shop products by `ProductID` with Czech `Description`
  (examples: 2=`Nafta` diesel, 3=`Special`, 4=`Super`, 5=`Natural`, 6=`Mix`,
  7=`Oleje,tuky` oils/greases, 1=`Rucní zadání` manual entry, 8=`Natural +`,
  9=`Diesel +`).
- `transactions_1k.Amount` is the purchased quantity; `Price` is the unit price.
  Rows span 2012-08-23..2012-08-26.
- `yearmonth.Date` is a 6-char string `YYYYMM`; `Consumption` is a monthly
  consumption figure per customer.

## Answer discipline

- Compose the final SQL from the schema above and call `execute_sql` directly —
  one call with the final statement. Do not inspect the schema first.
- Keep NULL semantics in mind: `SUM`/`COUNT` ignore NULLs; use `IFNULL`/`COALESCE`
  only when the question implies it.
- Never round numeric results: return the raw expression value at full precision
  (no `ROUND` unless the question explicitly asks for rounding).
- A successful `execute_sql` result is final. Never re-run arithmetic variants
  or re-verification queries that compute the same number; that only wastes
  steps. Only query again if the previous statement failed or returned no rows
  for a debugging reason you can state.
- Prefer one flat statement: compute conditional aggregates with
  `SUM(CASE WHEN ... THEN ... END)` in a single level instead of nesting
  subqueries that group per-entity.
- Country/segment/currency matching is exact and case-sensitive as stored
  (values above are stored in that exact case).

## Domain answer conventions for this question set

These conventions govern how questions in this domain are graded; follow them:

- "paid", "price paid", "total price", "average price" refer to the `Price`
  column alone. Do NOT multiply `Amount * Price` unless the question explicitly
  asks for a quantity-times-price product.
- `transactions_1k.Time` is a string `HH:MM:SS`; lexical comparison works.
  "morning" means `Time < '13:00:00'`; "afternoon/evening" means `>= '13:00:00'`.
- Comparative questions ("Is it true that more X than Y? If so, how many
  more?") expect the single numeric difference, e.g.
  `SUM(cond_x) - SUM(cond_y)`, as the answer.
- "What kind of ...", "list the ..." questions ALWAYS expect `SELECT DISTINCT`:
  duplicate rows in a list answer are wrong in this domain.
- "Which customer/station ... the most/least ..." expects exactly one row:
  `GROUP BY <id> ORDER BY SUM(Price) ... LIMIT 1`.
- Ranking/aggregation questions about a year (e.g. consumption in 2012) use
  `yearmonth.Date LIKE '2012%'` (the column is the 6-char string `YYYYMM`).
- When a question asks "which month", return the two-digit month `MM`
  (e.g. `04`), e.g. `SUBSTR(yearmonth.Date, 5, 2)` — not the full `YYYYMM`.
- When the expected answer is a list that may exceed ~10 rows, pass
  `max_rows=200` in the `execute_sql` call so the full result set is returned
  uncompressed. If a result still comes back compressed or truncated, keep the
  correct statement as your final SQL — do not re-run exploratory variants just
  to preview every row.

