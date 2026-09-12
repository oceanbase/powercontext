---
name: transaction-lookups-joins
description: Questions about transactions_1k - look up a transaction by exact timestamp/date/time window, count or average transactions with filters, list DISTINCT chains/products/descriptions through joins to gasstations, customers, products; who paid most on a date; revenue SUM(Amount*Price); most expensive product.
tags: [birdbench, transactions_1k, timestamp, join, count, avg, distinct]
---

# transactions_1k lookups and joins

## Execution policy (follow strictly)

1. You have loaded the right skill. Do NOT load any other skill, do NOT load
   this skill again, and do NOT call `describe_table` or `list_tables` - this
   skill already contains the exact schema and verified SQL.
2. Instantiate the matching template with the question's parameters
   (country, segment, currency, year, month, customer id, threshold, ...).
3. Validate that one final SQL once with `execute_sql`. If it succeeds, return
   the final JSON envelope immediately. No exploratory queries, no re-checks.


Use for any question about individual transactions: by date, exact time, time
window, price, card, or asking for attributes (chain, product description,
country, currency, segment) of matching transactions. Do NOT call
describe_table: the schema slice is below.

## Schema (exact)

- transactions_1k(TransactionID BIGINT PK, Date DATE 'YYYY-MM-DD', Time VARCHAR(16) 'HH:MM:SS',
  CustomerID BIGINT, CardID BIGINT, GasStationID BIGINT, ProductID BIGINT, Amount BIGINT, Price DOUBLE)
- Joins: transactions_1k.CustomerID = customers.CustomerID;
  transactions_1k.GasStationID = gasstations.GasStationID;
  transactions_1k.ProductID = products.ProductID (products has Description).
- gasstations also has ChainID, Country ('CZE','SVK'), Segment.
- customers has Segment ('KAM','LAM','SME'), Currency ('EUR','CZK').
- Total paid per transaction = Price; quantity = Amount; revenue = SUM(Amount * Price).

## Templates (T1 = transactions_1k)

1. Exact timestamp lookup:
   `... WHERE T1.Date = {date} AND T1.Time = {time}` (e.g. Date='2012-08-23', Time='21:20:00')
2. Time window within a date (morning / hour range):
   `... WHERE T1.Date = {date} AND T1.Time < '13:00:00'` or `T1.Time BETWEEN '08:00:00' AND '09:00:00'`
3. Attribute of a matching transaction. DISTINCT only when the question asks
   to "list" values; for singular lookups ("which country was it", "the
   product id of the transaction") return the matching rows WITHOUT DISTINCT:
   `SELECT DISTINCT T2.{attr} FROM transactions_1k AS T1 INNER JOIN {table} AS T2 ON T1.{fk} = T2.{pk} WHERE T1.Date = ... AND T1.Time = ...`
   Examples: product id (T1.ProductID, no join needed), country (join gasstations),
   currency/segment (join customers), description (join products).
4. Count transactions with filters:
   `SELECT COUNT(T1.TransactionID) FROM transactions_1k AS T1 [INNER JOIN gasstations AS T2 ON T1.GasStationID = T2.GasStationID] WHERE [T2.Country = {country}] [AND T1.Price > {p}] [AND T2.Currency... via customers join]`
5. Average price of transactions (optionally filtered by country / payer currency):
   `SELECT AVG(T1.Price) FROM transactions_1k AS T1 INNER JOIN gasstations AS T2 ON T1.GasStationID = T2.GasStationID [INNER JOIN customers AS T3 ON T1.CustomerID = T3.CustomerID] WHERE ...`
6. Who paid the most on a date (aggregate per customer, top 1):
   `SELECT CustomerID FROM transactions_1k WHERE Date = {date} GROUP BY CustomerID ORDER BY SUM(Price) DESC LIMIT 1`
7. DISTINCT lists through joins (chains / descriptions):
   `SELECT DISTINCT T3.ChainID FROM transactions_1k AS T1 INNER JOIN customers AS T2 ON T1.CustomerID = T2.CustomerID INNER JOIN gasstations AS T3 ON T1.GasStationID = T3.GasStationID WHERE T2.Currency = {cur}`
8. Revenue by gas station (top 1) / most expensive product:
   `SELECT GasStationID FROM transactions_1k GROUP BY GasStationID ORDER BY SUM(Amount * Price) DESC LIMIT 1`
   `SELECT GasStationID FROM transactions_1k WHERE ProductID = {pid} ORDER BY Price DESC LIMIT 1`

## Worked examples (verified on this database)

- "What was the product id of the transaction happened at 2012/8/23 21:20:00?"
  `SELECT T1.ProductID FROM transactions_1k AS T1 INNER JOIN gasstations AS T2 ON T1.GasStationID = T2.GasStationID WHERE T1.Date = '2012-08-23' AND T1.Time = '21:20:00'` -> 2
- "What kind of currency did the customer paid at 16:25:00 in 2012/8/24?"
  `SELECT DISTINCT T3.Currency FROM transactions_1k AS T1 INNER JOIN gasstations AS T2 ON T1.GasStationID = T2.GasStationID INNER JOIN customers AS T3 ON T1.CustomerID = T3.CustomerID WHERE T1.Date = '2012-08-24' AND T1.Time = '16:25:00'` -> 'CZK'
- "How many transactions taken place in the gas station in the Czech Republic are with a price higher than 1000?"
  `SELECT COUNT(T1.TransactionID) FROM transactions_1k AS T1 INNER JOIN gasstations AS T2 ON T1.GasStationID = T2.GasStationID WHERE T2.Country = 'CZE' AND T1.Price > 1000` -> 56
- "What is the average total price of the transactions taken place in gas stations in the Czech Republic?"
  `SELECT AVG(T1.Price) FROM transactions_1k AS T1 INNER JOIN gasstations AS T2 ON T1.GasStationID = T2.GasStationID WHERE T2.Country = 'CZE'` -> 453.15031082529475
- "Which customer paid the most in 2012/8/25?"
  `SELECT CustomerID FROM transactions_1k WHERE Date = '2012-08-25' GROUP BY CustomerID ORDER BY SUM(Price) DESC LIMIT 1` -> 19182
- "How many transactions were paid in CZK in the morning of 2012/8/26?"
  `SELECT COUNT(T1.TransactionID) FROM transactions_1k AS T1 INNER JOIN customers AS T2 ON T1.CustomerID = T2.CustomerID WHERE T1.Date = '2012-08-26' AND T1.Time < '13:00:00' AND T2.Currency = 'CZK'` -> 68

## Composition rule

For two-step questions ("the customer who paid X on date D ... which Y?"),
resolve the inner lookup first, then join — but prefer writing ONE SQL with the
join directly (templates 3/5/6 do this); do not run exploratory queries first.

## Output rules

- Return exactly the asked column(s); use DISTINCT only for "list ..." style
  questions; for singular lookups return matching rows without DISTINCT;
  LIMIT 1 for "which/who" questions.
- "list the products bought/involved in ..." returns DISTINCT ProductID and
  Description together (the product = its ID + description).
- No ROUND(...) unless asked; full float precision for AVG and ratios.
- Count answers are a single number: COUNT(TransactionID) or COUNT(*).
