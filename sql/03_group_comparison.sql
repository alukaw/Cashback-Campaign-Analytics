-- =====================================================================
-- 03_group_comparison.sql — описательное сравнение групп и сегментов
-- Запуск: python scripts/run_sql.py sql/03_group_comparison.sql
-- Нужна таблица client_mart из 02_client_mart.sql
-- Здесь только средние и разницы. Значимость (p-value, доверительные интервалы)
-- считаем в Python: SQL не подходит для статистических тестов.
-- =====================================================================

-- >> A1. Основные показатели по группам (на одного назначенного клиента, ₸ за 2 месяца)
SELECT
    group_name,
    COUNT(*)                                   AS n_clients,
    ROUND(AVG(pre_spend), 0)                   AS avg_pre_spend,
    ROUND(AVG(camp_spend), 0)                  AS avg_camp_spend,
    ROUND(AVG(camp_spend_promo), 0)            AS avg_camp_spend_promo,
    ROUND(AVG(camp_spend_other), 0)            AS avg_camp_spend_other,
    ROUND(AVG(camp_tx), 2)                     AS avg_camp_tx,
    ROUND(100.0 * AVG(pre_active), 2)          AS pct_active_pre,
    ROUND(100.0 * AVG(camp_active), 2)         AS pct_active_camp,
    ROUND(AVG(camp_cashback_extra), 0)         AS avg_extra_cashback
FROM client_mart
GROUP BY group_name
ORDER BY group_name;

-- >> A2. Разница test − control (условная агрегация). До кампании разница должна быть близка к 0
WITH g AS (
    SELECT
        AVG(CASE WHEN group_name = 'test'    THEN pre_spend END)        AS pre_t,
        AVG(CASE WHEN group_name = 'control' THEN pre_spend END)        AS pre_c,
        AVG(CASE WHEN group_name = 'test'    THEN camp_spend END)       AS camp_t,
        AVG(CASE WHEN group_name = 'control' THEN camp_spend END)       AS camp_c,
        AVG(CASE WHEN group_name = 'test'    THEN camp_spend_promo END) AS promo_t,
        AVG(CASE WHEN group_name = 'control' THEN camp_spend_promo END) AS promo_c,
        AVG(CASE WHEN group_name = 'test'    THEN camp_spend_other END) AS other_t,
        AVG(CASE WHEN group_name = 'control' THEN camp_spend_other END) AS other_c
    FROM client_mart
)
SELECT 'Расходы до кампании (A/A-проверка)' AS metric, ROUND(pre_c, 0) AS control, ROUND(pre_t, 0) AS test,
       ROUND(pre_t - pre_c, 0) AS diff_abs, ROUND(100 * (pre_t / pre_c - 1), 2) AS diff_pct FROM g
UNION ALL
SELECT 'Расходы в период кампании', ROUND(camp_c, 0), ROUND(camp_t, 0),
       ROUND(camp_t - camp_c, 0), ROUND(100 * (camp_t / camp_c - 1), 2) FROM g
UNION ALL
SELECT '  в т.ч. акционные категории', ROUND(promo_c, 0), ROUND(promo_t, 0),
       ROUND(promo_t - promo_c, 0), ROUND(100 * (promo_t / promo_c - 1), 2) FROM g
UNION ALL
SELECT '  в т.ч. остальные категории', ROUND(other_c, 0), ROUND(other_t, 0),
       ROUND(other_t - other_c, 0), ROUND(100 * (other_t / other_c - 1), 2) FROM g;

-- >> A3. Почему нельзя сравнивать «до и после» без контроля: рост есть и в контрольной группе
SELECT
    group_name,
    ROUND(AVG(pre_spend), 0)                          AS avg_pre_spend,
    ROUND(AVG(camp_spend), 0)                         AS avg_camp_spend,
    ROUND(100 * (AVG(camp_spend) / AVG(pre_spend) - 1), 2) AS growth_pct
FROM client_mart
GROUP BY group_name
ORDER BY group_name;

-- >> A4. Сегменты: расходы в период кампании и разница test − control
WITH seg AS (
    SELECT
        segment,
        group_name,
        COUNT(*)              AS n_clients,
        AVG(camp_spend)       AS avg_camp_spend,
        AVG(camp_active)      AS share_active,
        AVG(camp_cashback_extra) AS avg_extra_cashback
    FROM client_mart
    GROUP BY segment, group_name
)
SELECT
    segment,
    MAX(CASE WHEN group_name = 'control' THEN n_clients END)                    AS n_control,
    MAX(CASE WHEN group_name = 'test'    THEN n_clients END)                    AS n_test,
    ROUND(MAX(CASE WHEN group_name = 'control' THEN avg_camp_spend END), 0)     AS spend_control,
    ROUND(MAX(CASE WHEN group_name = 'test'    THEN avg_camp_spend END), 0)     AS spend_test,
    ROUND(MAX(CASE WHEN group_name = 'test'    THEN avg_camp_spend END)
        - MAX(CASE WHEN group_name = 'control' THEN avg_camp_spend END), 0)     AS diff_abs,
    ROUND(100 * (MAX(CASE WHEN group_name = 'test' THEN avg_camp_spend END)
               / MAX(CASE WHEN group_name = 'control' THEN avg_camp_spend END) - 1), 2) AS diff_pct,
    ROUND(100 * MAX(CASE WHEN group_name = 'control' THEN share_active END), 1) AS pct_active_control,
    ROUND(100 * MAX(CASE WHEN group_name = 'test'    THEN share_active END), 1) AS pct_active_test,
    ROUND(MAX(CASE WHEN group_name = 'test' THEN avg_extra_cashback END), 0)    AS extra_cashback_per_test_client
FROM seg
GROUP BY segment
ORDER BY segment;
