-- =====================================================================
-- 02_client_mart.sql — очистка транзакций и витрина «1 строка = 1 клиент»
-- Запуск: python scripts/run_sql.py sql/02_client_mart.sql
-- Нужны таблицы raw_* из 01_data_quality.sql
-- =====================================================================

-- ---------------------------------------------------------------------
-- Шаг 1. Очищенные транзакции (tx_clean)
-- Правила очистки (по итогам 01_data_quality.sql):
--   * полные дубли   → оставляем одну строку на transaction_id (ROW_NUMBER)
--   * нулевые суммы  → удаляем (технические операции)
--   * неизвестные клиенты → удаляем (нет в справочнике)
--   * даты вне 01.03–30.06.2026 → удаляем
-- Возвраты оставляем: сумма отрицательная, поэтому SUM(amount) даёт чистые расходы.
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE tx_clean AS
WITH numbered AS (
    SELECT
        t.*,
        ROW_NUMBER() OVER (PARTITION BY transaction_id ORDER BY transaction_id) AS rn
    FROM raw_transactions AS t
)
SELECT
    n.transaction_id,
    n.client_id,
    n.transaction_date,
    n.amount,
    n.category,
    n.transaction_type,
    CASE WHEN n.transaction_date < DATE '2026-05-01' THEN 'pre' ELSE 'campaign' END AS period,
    n.category IN ('Кафе и рестораны', 'Онлайн-покупки')                       AS is_promo_category
FROM numbered AS n
WHERE n.rn = 1
  AND n.amount <> 0
  AND n.transaction_date BETWEEN DATE '2026-03-01' AND DATE '2026-06-30'
  AND EXISTS (SELECT 1 FROM raw_clients AS c WHERE c.client_id = n.client_id);

-- ---------------------------------------------------------------------
-- Шаг 2. Витрина клиентов (client_mart)
-- Главное правило против задвоения сумм: КАЖДУЮ таблицу фактов сначала
-- агрегируем до уровня клиента (свой CTE), и только потом делаем JOIN.
-- Базой служит таблица назначения: в витрину попадают ВСЕ назначенные клиенты
-- (intention-to-treat), у клиентов без покупок расходы = 0, а не NULL.
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE client_mart AS
WITH tx_by_client AS (                        -- транзакции → 1 строка на клиента
    SELECT
        client_id,
        SUM(CASE WHEN period = 'pre' THEN amount ELSE 0 END)                                AS pre_spend,
        SUM(CASE WHEN period = 'pre' AND is_promo_category THEN amount ELSE 0 END)          AS pre_spend_promo,
        SUM(CASE WHEN period = 'pre' AND transaction_type = 'purchase' THEN 1 ELSE 0 END)   AS pre_tx,
        MAX(CASE WHEN period = 'pre' AND transaction_type = 'purchase' THEN transaction_date END) AS pre_last_purchase,
        SUM(CASE WHEN period = 'campaign' THEN amount ELSE 0 END)                           AS camp_spend,
        SUM(CASE WHEN period = 'campaign' AND is_promo_category THEN amount ELSE 0 END)     AS camp_spend_promo,
        SUM(CASE WHEN period = 'campaign' AND NOT is_promo_category THEN amount ELSE 0 END) AS camp_spend_other,
        SUM(CASE WHEN period = 'campaign' AND transaction_type = 'purchase' THEN 1 ELSE 0 END) AS camp_tx
    FROM tx_clean
    GROUP BY client_id
),
cashback_by_client AS (                       -- начисления → 1 строка на клиента
    SELECT
        client_id,
        SUM(CASE WHEN period_month >= DATE '2026-05-01' AND cashback_type = 'base'
                 THEN cashback_amount ELSE 0 END)                                           AS camp_cashback_base,
        SUM(CASE WHEN cashback_type = 'campaign_extra' THEN cashback_amount ELSE 0 END)     AS camp_cashback_extra
    FROM raw_accruals
    GROUP BY client_id
),
base AS (
    SELECT
        a.client_id,
        a.group_name,
        c.card_tier,
        c.city_type,
        CASE WHEN c.age BETWEEN 18 AND 100 THEN c.age END                AS age,      -- 999 и пропуски → NULL
        DATE_DIFF('month', c.card_open_date, DATE '2026-05-01')          AS tenure_months,
        COALESCE(t.pre_spend, 0)                                         AS pre_spend,
        COALESCE(t.pre_spend_promo, 0)                                   AS pre_spend_promo,
        COALESCE(t.pre_tx, 0)                                            AS pre_tx,
        CASE WHEN COALESCE(t.pre_tx, 0) > 0 THEN 1 ELSE 0 END            AS pre_active,
        DATE_DIFF('day', t.pre_last_purchase, DATE '2026-04-30')         AS pre_recency_days,
        COALESCE(t.camp_spend, 0)                                        AS camp_spend,
        COALESCE(t.camp_spend_promo, 0)                                  AS camp_spend_promo,
        COALESCE(t.camp_spend_other, 0)                                  AS camp_spend_other,
        COALESCE(t.camp_tx, 0)                                           AS camp_tx,
        CASE WHEN COALESCE(t.camp_tx, 0) > 0 THEN 1 ELSE 0 END           AS camp_active,
        COALESCE(b.camp_cashback_base, 0)                                AS camp_cashback_base,
        COALESCE(b.camp_cashback_extra, 0)                               AS camp_cashback_extra
    FROM raw_assignment AS a
    LEFT JOIN raw_clients        AS c ON c.client_id = a.client_id
    LEFT JOIN tx_by_client       AS t ON t.client_id = a.client_id
    LEFT JOIN cashback_by_client AS b ON b.client_id = a.client_id
),
-- Шаг 3. Сегменты — ТОЛЬКО по поведению до кампании:
--   «Спящие» — ни одной покупки в марте–апреле,
--   остальные делятся на три равные по численности группы по расходам до кампании (NTILE).
spend_tertiles AS (
    SELECT client_id, NTILE(3) OVER (ORDER BY pre_spend, client_id) AS spend_tertile
    FROM base
    WHERE pre_active = 1
)
SELECT
    b.*,
    CASE
        WHEN b.pre_active = 0    THEN '1. Спящие'
        WHEN s.spend_tertile = 1 THEN '2. Низкая активность'
        WHEN s.spend_tertile = 2 THEN '3. Средняя активность'
        ELSE                          '4. Высокая активность'
    END AS segment
FROM base AS b
LEFT JOIN spend_tertiles AS s ON s.client_id = b.client_id;

-- ---------------------------------------------------------------------
-- Проверки корректности
-- ---------------------------------------------------------------------

-- >> C1. Что удалила очистка
SELECT
    (SELECT COUNT(*) FROM raw_transactions) AS raw_rows,
    (SELECT COUNT(*) FROM tx_clean)         AS clean_rows,
    (SELECT COUNT(*) FROM raw_transactions) - (SELECT COUNT(*) FROM tx_clean) AS removed_rows;

-- >> C2. Гранулярность витрины: строк = уникальных клиентов = назначенных клиентов
SELECT
    COUNT(*)                                      AS mart_rows,
    COUNT(DISTINCT client_id)                     AS unique_clients,
    (SELECT COUNT(*) FROM raw_assignment)         AS assigned_clients
FROM client_mart;

-- >> C3. Нет задвоения сумм после JOIN: расходы в витрине = расходам в tx_clean
SELECT
    ROUND((SELECT SUM(pre_spend + camp_spend) FROM client_mart), 2) AS spend_in_mart,
    ROUND((SELECT SUM(amount) FROM tx_clean), 2)                     AS spend_in_tx_clean;

-- >> C4. Сверка с учётной системой: пересчитанный базовый кешбэк (1% от чистых расходов за месяц) = начислениям
WITH recalc AS (
    SELECT client_id,
           CAST(DATE_TRUNC('month', transaction_date) AS DATE) AS period_month,
           ROUND(GREATEST(SUM(amount), 0) * 0.01, 2)          AS base_recalc
    FROM tx_clean
    GROUP BY client_id, CAST(DATE_TRUNC('month', transaction_date) AS DATE)
),
accrued AS (
    SELECT client_id, period_month, cashback_amount
    FROM raw_accruals
    WHERE cashback_type = 'base'
)
SELECT
    COUNT(*)                                                                         AS client_months,
    SUM(CASE WHEN ABS(COALESCE(r.base_recalc, 0) - COALESCE(a.cashback_amount, 0)) > 0.011
             THEN 1 ELSE 0 END)                                                      AS mismatches,
    ROUND(SUM(r.base_recalc), 0)                                                     AS recalculated_total,
    ROUND(SUM(a.cashback_amount), 0)                                                 AS accrued_total
FROM recalc AS r
FULL OUTER JOIN accrued AS a
    ON a.client_id = r.client_id AND a.period_month = r.period_month;

-- >> C5. Сегменты: размер по группам (должны быть близки) и профиль до кампании
SELECT
    segment,
    SUM(CASE WHEN group_name = 'control' THEN 1 ELSE 0 END) AS n_control,
    SUM(CASE WHEN group_name = 'test'    THEN 1 ELSE 0 END) AS n_test,
    ROUND(AVG(pre_spend), 0)                                 AS avg_pre_spend,
    ROUND(AVG(pre_tx), 1)                                    AS avg_pre_tx,
    ROUND(AVG(pre_recency_days), 1)                          AS avg_recency_days
FROM client_mart
GROUP BY segment
ORDER BY segment;
