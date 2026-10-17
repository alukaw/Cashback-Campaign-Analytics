"""
01_generate_data.py — генерация СИНТЕТИЧЕСКОГО датасета для учебного кейса
«Cashback Campaign Analytics».

ВАЖНО
-----
* Все данные выдуманы. Это не данные реального банка и не реальные клиенты.
* Эффект кампании ЗАЛОЖЕН в генерацию (блок «ПАРАМЕТРЫ ЭФФЕКТА» ниже).
  Поэтому A/B-тест на этих данных проверяет МЕТОДИКУ анализа,
  а не доказывает, что такие кампании работают в реальном банке.
* Фиксированный SEED: при повторном запуске получаются те же самые данные.

Что создаётся (папка data/raw/):
    clients.csv              — клиенты (1 строка = 1 клиент)
    campaign_assignment.csv  — назначение в группу test/control (1 строка = 1 клиент)
    transactions.csv         — операции по карте (1 строка = 1 операция)
    cashback_accruals.csv    — начисления кешбэка (1 строка = клиент × месяц × тип кешбэка)

В transactions.csv специально оставлены типичные проблемы выгрузки
(дубли, нулевые суммы, операции неизвестных клиентов, возвраты),
а в clients.csv — пропуски и некорректный возраст. Их нужно найти на этапе проверки качества.

Запуск (из корня проекта):  python scripts/01_generate_data.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# ОБЩИЕ ПАРАМЕТРЫ
# ---------------------------------------------------------------------------
SEED = 42
N_CLIENTS = 20_000
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"

PRE_MONTHS = [3, 4]        # март–апрель 2026: период ДО кампании
CAMPAIGN_MONTHS = [5, 6]   # май–июнь 2026: период кампании
YEAR = 2026
CAMPAIGN_ID = "CB_2026_05"
ASSIGNMENT_DATE = "2026-04-24"

# Категории покупок: доля операций в «среднем» портфеле и медианный чек (₸, карта Classic)
CATEGORIES = ["Супермаркеты", "Кафе и рестораны", "Онлайн-покупки",
              "АЗС и транспорт", "Одежда и товары", "Прочее"]
CAT_TX_MIX = np.array([0.38, 0.17, 0.12, 0.13, 0.06, 0.14])
CAT_TICKET = np.array([5_500, 6_500, 11_000, 8_000, 18_000, 7_000])
CAMPAIGN_CATEGORIES = ["Кафе и рестораны", "Онлайн-покупки"]

# Условия кешбэка (используются для расчёта начислений)
BASE_CASHBACK_RATE = 0.01      # базовый кешбэк 1% на все покупки — у всех клиентов
CAMPAIGN_EXTRA_RATE = 0.04     # тест: +4% в акционных категориях (итого 5%)
CAMPAIGN_EXTRA_CAP = 5_000     # лимит доп. кешбэка, ₸ в месяц на клиента

# Сезонность — общая для обеих групп (поэтому сравнение «до/после» без контроля вводит в заблуждение)
SEASONALITY = {3: 1.00, 4: 1.03, 5: 1.06, 6: 1.09}

# ---------------------------------------------------------------------------
# ПАРАМЕТРЫ ЭФФЕКТА (заложены в данные; в README указаны как ограничение)
# ---------------------------------------------------------------------------
# 1) Рост частоты покупок в акционных категориях у тестовой группы.
#    Средний эффект зависит от исходной вовлечённости клиента z:
#    менее активные клиенты реагируют сильнее, очень активные — слабее.
EFFECT_CAT_INTERCEPT = 0.08    # средний прирост частоты при z = 0 (+8%)
EFFECT_CAT_SLOPE = -0.04       # чем выше z, тем меньше эффект
EFFECT_CAT_NOISE_SD = 0.04     # индивидуальный разброс эффекта
# 2) Небольшой перенос активности на остальные категории («карта становится основной»)
EFFECT_OTHER_CAT = 0.01
# 3) Реактивация «спящих» клиентов: рост вероятности быть активным в месяц
EFFECT_REACTIVATION_PP = 0.07
# 4) Эффект новизны: во втором месяце кампании эффект слабее
EFFECT_DECAY = {5: 1.0, 6: 0.8}

# Доли «грязных» записей
DUPLICATE_SHARE = 0.002        # дубли строк из-за повторной загрузки
REFUND_SHARE = 0.012           # доля покупок с возвратом
ZERO_AMOUNT_SHARE = 0.0005     # технические операции с нулевой суммой
N_ORPHAN_TX = 40               # операции клиентов, которых нет в clients
MISSING_AGE_SHARE = 0.03
N_INVALID_AGE = 15             # возраст 999 — «заглушка» из учётной системы


def month_bounds(month: int):
    start = pd.Timestamp(year=YEAR, month=month, day=1)
    return start, start + pd.offsets.MonthEnd(0)


def main():
    rng = np.random.default_rng(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # 1. КЛИЕНТЫ
    # -----------------------------------------------------------------------
    client_id = np.arange(100_001, 100_001 + N_CLIENTS)
    card_tier = rng.choice(["Classic", "Gold", "Premium"], size=N_CLIENTS, p=[0.65, 0.27, 0.08])
    tier_ticket_mult = pd.Series(card_tier).map({"Classic": 1.0, "Gold": 1.4, "Premium": 2.2}).to_numpy()
    city_type = rng.choice(["Мегаполис", "Крупный город", "Малый город"], size=N_CLIENTS, p=[0.50, 0.32, 0.18])
    age = np.clip(np.round(rng.normal(36, 11, N_CLIENTS)), 18, 75)

    open_start, open_end = pd.Timestamp("2019-01-01"), pd.Timestamp("2026-02-15")
    open_days = rng.integers(0, (open_end - open_start).days + 1, N_CLIENTS)
    card_open_date = open_start + pd.to_timedelta(open_days, unit="D")

    clients = pd.DataFrame({
        "client_id": client_id,
        "card_open_date": card_open_date.strftime("%Y-%m-%d"),
        "age": age,
        "city_type": city_type,
        "card_tier": card_tier,
    })
    # «Грязь»: пропуски и некорректный возраст
    missing_idx = rng.choice(N_CLIENTS, int(MISSING_AGE_SHARE * N_CLIENTS), replace=False)
    clients.loc[missing_idx, "age"] = np.nan
    invalid_idx = rng.choice(np.setdiff1d(np.arange(N_CLIENTS), missing_idx), N_INVALID_AGE, replace=False)
    clients.loc[invalid_idx, "age"] = 999
    clients["age"] = clients["age"].astype("Int64")

    # Скрытые (не выгружаются) характеристики поведения клиента
    z = rng.normal(0, 1, N_CLIENTS)                              # вовлечённость
    is_dormant_type = rng.random(N_CLIENTS) < 0.18               # склонные к «спячке»
    lam = np.where(is_dormant_type,
                   1.5 * np.exp(0.3 * z),                        # редкие покупки
                   np.exp(1.85 + 0.55 * z))                      # обычные клиенты, ~7 операций/мес
    p_active = np.where(is_dormant_type, 0.12, np.clip(0.93 + 0.03 * z, 0.75, 0.99))
    ticket_mult = tier_ticket_mult * rng.lognormal(0, 0.35, N_CLIENTS)
    cat_pref = rng.dirichlet(8 * CAT_TX_MIX, N_CLIENTS)          # индивидуальный набор категорий

    # -----------------------------------------------------------------------
    # 2. НАЗНАЧЕНИЕ В ГРУППЫ (рандомизация на уровне клиента, 50/50)
    # -----------------------------------------------------------------------
    is_test = rng.random(N_CLIENTS) < 0.5
    assignment = pd.DataFrame({
        "client_id": client_id,
        "campaign_id": CAMPAIGN_ID,
        "group_name": np.where(is_test, "test", "control"),
        "assignment_date": ASSIGNMENT_DATE,
    })

    # Индивидуальный эффект на акционные категории (используется только для теста в мае–июне)
    effect_cat = np.clip(EFFECT_CAT_INTERCEPT + EFFECT_CAT_SLOPE * z
                         + rng.normal(0, EFFECT_CAT_NOISE_SD, N_CLIENTS), 0, None)
    campaign_cat_mask = np.isin(CATEGORIES, CAMPAIGN_CATEGORIES)

    # -----------------------------------------------------------------------
    # 3. ТРАНЗАКЦИИ: число операций по клиенту × месяцу × категории
    # -----------------------------------------------------------------------
    tx_parts = []
    for month in PRE_MONTHS + CAMPAIGN_MONTHS:
        in_campaign = month in CAMPAIGN_MONTHS
        decay = EFFECT_DECAY.get(month, 0.0)
        treated = is_test & in_campaign

        p = p_active + np.where(treated & is_dormant_type, EFFECT_REACTIVATION_PP * decay, 0)
        active = rng.random(N_CLIENTS) < np.clip(p, 0, 1)

        mult = np.ones((N_CLIENTS, len(CATEGORIES)))
        mult[:, campaign_cat_mask] += np.where(treated, effect_cat * decay, 0)[:, None]
        mult[:, ~campaign_cat_mask] += np.where(treated, EFFECT_OTHER_CAT * decay, 0)[:, None]

        expected = lam[:, None] * cat_pref * SEASONALITY[month] * mult
        counts = rng.poisson(expected) * active[:, None]

        ci, cj = np.nonzero(counts)
        n = counts[ci, cj]
        rows_client = np.repeat(ci, n)
        rows_cat = np.repeat(cj, n)

        start, end = month_bounds(month)
        days = rng.integers(0, end.day, rows_client.size)
        amount = (CAT_TICKET[rows_cat] * ticket_mult[rows_client]
                  * rng.lognormal(0, 0.7, rows_client.size))

        tx_parts.append(pd.DataFrame({
            "client_id": client_id[rows_client],
            "transaction_date": start + pd.to_timedelta(days, unit="D"),
            "amount": np.round(amount, 2),
            "category": np.array(CATEGORIES)[rows_cat],
            "transaction_type": "purchase",
        }))

    purchases = pd.concat(tx_parts, ignore_index=True)

    # Возвраты: часть покупок возвращается через 1–14 дней (полностью или наполовину)
    refund_idx = rng.choice(len(purchases), int(REFUND_SHARE * len(purchases)), replace=False)
    refunds = purchases.iloc[refund_idx].copy()
    refunds["transaction_date"] += pd.to_timedelta(rng.integers(1, 15, len(refunds)), unit="D")
    refunds["amount"] = -np.round(refunds["amount"] * rng.choice([1.0, 0.5], len(refunds), p=[0.7, 0.3]), 2)
    refunds["transaction_type"] = "refund"
    refunds = refunds[refunds["transaction_date"] <= pd.Timestamp(f"{YEAR}-06-30")]

    clean_tx = pd.concat([purchases, refunds], ignore_index=True)
    clean_tx = clean_tx.sort_values(["transaction_date", "client_id"], kind="stable").reset_index(drop=True)
    clean_tx.insert(0, "transaction_id", np.arange(1, len(clean_tx) + 1))

    # -----------------------------------------------------------------------
    # 4. НАЧИСЛЕНИЯ КЕШБЭКА — считаются по «чистым» операциям, как в учётной системе банка
    # -----------------------------------------------------------------------
    tx_m = clean_tx.assign(period_month=clean_tx["transaction_date"].dt.to_period("M").dt.to_timestamp())
    net_all = tx_m.groupby(["client_id", "period_month"], as_index=False)["amount"].sum()
    net_all["cashback_type"] = "base"
    net_all["cashback_amount"] = np.round(net_all["amount"].clip(lower=0) * BASE_CASHBACK_RATE, 2)

    test_ids = set(client_id[is_test])
    camp = tx_m[tx_m["category"].isin(CAMPAIGN_CATEGORIES)
                & tx_m["period_month"].dt.month.isin(CAMPAIGN_MONTHS)
                & tx_m["client_id"].isin(test_ids)]
    net_camp = camp.groupby(["client_id", "period_month"], as_index=False)["amount"].sum()
    net_camp["cashback_type"] = "campaign_extra"
    net_camp["cashback_amount"] = np.round(
        np.minimum(net_camp["amount"].clip(lower=0) * CAMPAIGN_EXTRA_RATE, CAMPAIGN_EXTRA_CAP), 2)

    accruals = pd.concat([net_all, net_camp], ignore_index=True)
    accruals = accruals[accruals["cashback_amount"] > 0].drop(columns="amount")
    accruals["accrual_date"] = accruals["period_month"] + pd.offsets.MonthBegin(1) + pd.Timedelta(days=4)
    accruals = accruals.sort_values(["period_month", "client_id", "cashback_type"]).reset_index(drop=True)
    accruals.insert(0, "accrual_id", np.arange(1, len(accruals) + 1))
    accruals = accruals[["accrual_id", "client_id", "period_month", "cashback_type",
                         "cashback_amount", "accrual_date"]]

    # -----------------------------------------------------------------------
    # 5. «ГРЯЗЬ» В ВЫГРУЗКЕ ТРАНЗАКЦИЙ (после расчёта начислений)
    # -----------------------------------------------------------------------
    tx_out = clean_tx.copy()
    # нулевые технические операции
    zero_idx = rng.choice(len(tx_out), int(ZERO_AMOUNT_SHARE * len(tx_out)), replace=False)
    zero_rows = tx_out.iloc[zero_idx].copy()
    zero_rows["amount"] = 0.0
    zero_rows["transaction_id"] = np.arange(len(tx_out) + 1, len(tx_out) + 1 + len(zero_rows))
    # операции клиентов, которых нет в справочнике
    orphan = tx_out.sample(N_ORPHAN_TX, random_state=SEED).copy()
    orphan["client_id"] = rng.integers(900_001, 900_100, N_ORPHAN_TX)
    orphan["transaction_id"] = np.arange(len(tx_out) + len(zero_rows) + 1,
                                         len(tx_out) + len(zero_rows) + 1 + N_ORPHAN_TX)
    # полные дубли строк (тот же transaction_id)
    dup_rows = tx_out.sample(frac=DUPLICATE_SHARE, random_state=SEED + 1)

    tx_out = pd.concat([tx_out, zero_rows, orphan, dup_rows], ignore_index=True)
    tx_out = tx_out.sort_values(["transaction_date", "transaction_id"], kind="stable").reset_index(drop=True)

    # -----------------------------------------------------------------------
    # 6. СОХРАНЕНИЕ (UTF-8 с BOM, чтобы кириллица корректно открывалась в Excel и Power BI)
    # -----------------------------------------------------------------------
    def save(df, name):
        path = OUT_DIR / name
        df.to_csv(path, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
        print(f"  {name:<26} {len(df):>8,} строк   {path.stat().st_size / 1e6:6.1f} МБ")

    print(f"Синтетические данные (SEED={SEED}) → {OUT_DIR}")
    save(clients, "clients.csv")
    save(assignment, "campaign_assignment.csv")
    save(tx_out, "transactions.csv")
    save(accruals, "cashback_accruals.csv")

    print("\nКонтроль:")
    print(f"  клиентов в группах: {assignment['group_name'].value_counts().to_dict()}")
    print(f"  период операций:    {tx_out['transaction_date'].min():%Y-%m-%d} … "
          f"{tx_out['transaction_date'].max():%Y-%m-%d}")
    print("Готово. Данные синтетические — не используйте их как результаты реального банка.")


if __name__ == "__main__":
    main()
