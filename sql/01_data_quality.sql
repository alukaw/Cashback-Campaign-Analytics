-- =====================================================================
-- 01_data_quality.sql — загрузка сырых CSV и проверка качества данных
-- Запуск: python scripts/run_sql.py sql/01_data_quality.sql
-- Диалект: DuckDB (почти полностью совместим с PostgreSQL)
-- =====================================================================

-- Загружаем сырые CSV в базу как есть (таблицы raw_*). Ничего не исправляем.
CREATE OR REPLACE TABLE raw_clients      AS SELECT * FROM read_csv('data/raw/clients.csv', header = true);
CREATE OR REPLACE TABLE raw_assignment   AS SELECT * FROM read_csv('data/raw/campaign_assignment.csv', header = true);
CREATE OR REPLACE TABLE raw_transactions AS SELECT * FROM read_csv('data/raw/transactions.csv', header = true);
CREATE OR REPLACE TABLE raw_accruals     AS SELECT * FROM read_csv('data/raw/cashback_accruals.csv', header = true);

-- >> Q1. Сколько строк в каждой таблице
SELECT 'clients' AS table_name, COUNT(*) AS n_rows FROM raw_clients
UNION ALL SELECT 'campaign_assignment', COUNT(*) FROM raw_assignment
UNION ALL SELECT 'transactions',        COUNT(*) FROM raw_transactions
UNION ALL SELECT 'cashback_accruals',   COUNT(*) FROM raw_accruals;

-- >> Q2. Уникальность ключей: число строк должно совпадать с числом уникальных ключей
SELECT 'clients.client_id' AS key_column, COUNT(*) AS n_rows, COUNT(DISTINCT client_id) AS n_unique FROM raw_clients
UNION ALL SELECT 'campaign_assignment.client_id', COUNT(*), COUNT(DISTINCT client_id) FROM raw_assignment
UNION ALL SELECT 'transactions.transaction_id',   COUNT(*), COUNT(DISTINCT transaction_id) FROM raw_transactions
UNION ALL SELECT 'cashback_accruals.accrual_id',  COUNT(*), COUNT(DISTINCT accrual_id) FROM raw_accruals;

-- >> Q3. Дубли транзакций: сколько transaction_id встречаются больше одного раза
SELECT n_copies, COUNT(*) AS n_transaction_ids
FROM (
    SELECT transaction_id, COUNT(*) AS n_copies
    FROM raw_transactions
    GROUP BY transaction_id
    HAVING COUNT(*) > 1
) AS dup
GROUP BY n_copies;

-- >> Q4. Это полные дубли строк? Если distinct_rows = unique_transaction_ids, то да — можно оставить одну копию
SELECT
    (SELECT COUNT(*) FROM (SELECT DISTINCT * FROM raw_transactions) AS d) AS distinct_rows,
    (SELECT COUNT(DISTINCT transaction_id) FROM raw_transactions)        AS unique_transaction_ids;

-- >> Q5. Пропуски и некорректные значения в справочнике клиентов
SELECT
    COUNT(*)                                                           AS n_clients,
    SUM(CASE WHEN age IS NULL THEN 1 ELSE 0 END)                       AS age_missing,
    SUM(CASE WHEN age NOT BETWEEN 18 AND 100 THEN 1 ELSE 0 END)        AS age_out_of_range,
    SUM(CASE WHEN city_type IS NULL THEN 1 ELSE 0 END)                 AS city_missing,
    SUM(CASE WHEN card_tier IS NULL THEN 1 ELSE 0 END)                 AS tier_missing,
    SUM(CASE WHEN card_open_date >= DATE '2026-03-01' THEN 1 ELSE 0 END) AS opened_after_study_start
FROM raw_clients;

-- >> Q6. Суммы по типам операций: покупки должны быть > 0, возвраты < 0, нулевые суммы — подозрительны
SELECT
    transaction_type,
    COUNT(*)                                           AS n_rows,
    SUM(CASE WHEN amount = 0 THEN 1 ELSE 0 END)        AS zero_amount,
    SUM(CASE WHEN amount < 0 THEN 1 ELSE 0 END)        AS negative_amount,
    SUM(CASE WHEN amount > 0 THEN 1 ELSE 0 END)        AS positive_amount,
    MIN(amount)                                        AS min_amount,
    MAX(amount)                                        AS max_amount
FROM raw_transactions
GROUP BY transaction_type
ORDER BY transaction_type;

-- >> Q7. Операции клиентов, которых нет в справочнике (anti-join через LEFT JOIN)
SELECT COUNT(*) AS orphan_rows, COUNT(DISTINCT t.client_id) AS orphan_clients
FROM raw_transactions AS t
LEFT JOIN raw_clients AS c ON c.client_id = t.client_id
WHERE c.client_id IS NULL;

-- >> Q8. Все ли клиенты назначены в группу и все ли назначенные есть в справочнике
SELECT
    (SELECT COUNT(*) FROM raw_clients c
      WHERE NOT EXISTS (SELECT 1 FROM raw_assignment a WHERE a.client_id = c.client_id)) AS clients_without_group,
    (SELECT COUNT(*) FROM raw_assignment a
      WHERE NOT EXISTS (SELECT 1 FROM raw_clients c WHERE c.client_id = a.client_id))   AS assigned_not_in_clients;

-- >> Q9. Диапазон дат: все операции должны попадать в 01.03.2026–30.06.2026
SELECT MIN(transaction_date) AS min_date, MAX(transaction_date) AS max_date,
       SUM(CASE WHEN transaction_date NOT BETWEEN DATE '2026-03-01' AND DATE '2026-06-30' THEN 1 ELSE 0 END) AS out_of_window
FROM raw_transactions;

-- >> Q10. Размер групп (формальную проверку SRM сделаем в Python)
SELECT group_name, COUNT(*) AS n_clients, ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM raw_assignment
GROUP BY group_name
ORDER BY group_name;

-- >> Q11. Справочник категорий: нет ли опечаток и лишних значений
SELECT category, COUNT(*) AS n_rows
FROM raw_transactions
GROUP BY category
ORDER BY n_rows DESC;
