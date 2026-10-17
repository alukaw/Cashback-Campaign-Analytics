"""
04_ab_test.py — статистический анализ эксперимента и выгрузка таблиц для Power BI и Excel.

Запуск из корня проекта (после sql/01–03):
    python scripts/04_ab_test.py | tee reports/04_ab_test.txt

Что делает скрипт:
  0. Дизайн: какой минимальный эффект (MDE) можно было обнаружить при таком размере групп.
  1. SRM-проверка: совпадает ли фактическое деление клиентов с плановым 50/50.
  2. Баланс групп до кампании: сопоставимы ли группы (A/A-проверка).
  3. Основная метрика — расходы на клиента за период кампании:
       * основной анализ: t-тест Уэлча на исходной метрике;
       * уточняющий анализ: CUPED — та же метрика, очищенная от разброса,
         который объясняется расходами клиента ДО кампании.
  4. Дополнительные и защитные метрики (доля активных — z-тест для долей).
  5. Сегменты: эффект в каждом сегменте + поправка Холма на множественные сравнения.
  6. Выгрузка CSV для Power BI / Excel и два графика для README.

Данные синтетические: результат проверяет методику, а не реальную кампанию.
"""

from pathlib import Path

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "cashback.duckdb"
OUT_DATA = ROOT / "data" / "processed"
OUT_IMG = ROOT / "images"

ALPHA = 0.05
POWER = 0.80
CAMPAIGN_START = "2026-05-01"
COLOR_CONTROL, COLOR_TEST = "#2a78d6", "#eb6834"

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)
pd.set_option("display.float_format", lambda x: f"{x:,.4f}")


def header(text):
    print("\n" + "=" * 100 + f"\n{text}\n" + "-" * 100)


# ---------------------------------------------------------------------------
# Статистические функции
# ---------------------------------------------------------------------------
def welch_test(x_test, x_control):
    """t-тест Уэлча для разницы средних (дисперсии групп могут различаться).
    Возвращает средние, абсолютную разницу с 95% ДИ, относительный uplift с ДИ и p-value."""
    x_test, x_control = np.asarray(x_test, float), np.asarray(x_control, float)
    m_t, m_c = x_test.mean(), x_control.mean()
    v_t, v_c = x_test.var(ddof=1) / len(x_test), x_control.var(ddof=1) / len(x_control)
    diff, se = m_t - m_c, np.sqrt(v_t + v_c)
    df = (v_t + v_c) ** 2 / (v_t ** 2 / (len(x_test) - 1) + v_c ** 2 / (len(x_control) - 1))
    t_crit = stats.t.ppf(1 - ALPHA / 2, df)
    p_value = stats.ttest_ind(x_test, x_control, equal_var=False).pvalue
    # ДИ относительного uplift — дельта-методом (учитывает неопределённость и в контрольном среднем)
    rel = m_t / m_c - 1
    se_rel = np.sqrt(v_t / m_c ** 2 + m_t ** 2 * v_c / m_c ** 4)
    z_crit = stats.norm.ppf(1 - ALPHA / 2)
    return dict(control_mean=m_c, test_mean=m_t, diff_abs=diff,
                ci_low_abs=diff - t_crit * se, ci_high_abs=diff + t_crit * se,
                uplift_rel=rel, ci_low_rel=rel - z_crit * se_rel, ci_high_rel=rel + z_crit * se_rel,
                p_value=p_value)


def proportions_ztest(success_t, n_t, success_c, n_c):
    """z-тест для разницы двух долей (бинарная метрика, большие выборки)."""
    p_t, p_c = success_t / n_t, success_c / n_c
    p_pool = (success_t + success_c) / (n_t + n_c)
    z = (p_t - p_c) / np.sqrt(p_pool * (1 - p_pool) * (1 / n_t + 1 / n_c))
    p_value = 2 * (1 - stats.norm.cdf(abs(z)))
    se_unpooled = np.sqrt(p_t * (1 - p_t) / n_t + p_c * (1 - p_c) / n_c)
    z_crit = stats.norm.ppf(1 - ALPHA / 2)
    diff = p_t - p_c
    return dict(control_mean=p_c, test_mean=p_t, diff_abs=diff,
                ci_low_abs=diff - z_crit * se_unpooled, ci_high_abs=diff + z_crit * se_unpooled,
                uplift_rel=p_t / p_c - 1, ci_low_rel=np.nan, ci_high_rel=np.nan, p_value=p_value)


def cuped(y, x):
    """CUPED: y_adj = y − θ·(x − mean(x)), θ = cov(y, x) / var(x).
    Среднее не меняется, а разброс уменьшается на долю corr(y, x)²."""
    theta = np.cov(y, x, ddof=1)[0, 1] / np.var(x, ddof=1)
    return y - theta * (x - x.mean()), theta


def holm(p_values):
    """Поправка Холма — Бонферрони на множественные сравнения."""
    p = np.asarray(p_values, float)
    order = np.argsort(p)
    adjusted = np.empty_like(p)
    running_max = 0.0
    for rank, idx in enumerate(order):
        running_max = max(running_max, (len(p) - rank) * p[idx])
        adjusted[idx] = min(1.0, running_max)
    return adjusted


def smd(a, b):
    """Стандартизированная разница средних: |SMD| < 0.1 — группы сбалансированы."""
    a, b = pd.Series(a).dropna().astype(float), pd.Series(b).dropna().astype(float)
    return (a.mean() - b.mean()) / np.sqrt((a.var() + b.var()) / 2)


def main():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    df = con.sql("SELECT * FROM client_mart").df()
    # Недельная динамика: недели отсчитываются от старта кампании (01.05.2026, пятница),
    # поэтому ни одна неделя не смешивает период «до» и период кампании.
    weekly = con.sql(f"""
        SELECT client_id,
               DATE '{CAMPAIGN_START}' + INTERVAL (7 * FLOOR(DATE_DIFF('day', DATE '{CAMPAIGN_START}', transaction_date) / 7)) DAY AS week_start,
               CAST(FLOOR(DATE_DIFF('day', DATE '{CAMPAIGN_START}', transaction_date) / 7) AS INTEGER) AS week_num,
               period,
               SUM(amount) AS spend,
               SUM(CASE WHEN transaction_type = 'purchase' THEN 1 ELSE 0 END) AS purchases
        FROM tx_clean
        GROUP BY ALL
    """).df()
    n_capped = con.sql("""
        SELECT COUNT(DISTINCT client_id) FROM raw_accruals
        WHERE cashback_type = 'campaign_extra' AND cashback_amount >= 5000
    """).fetchone()[0]
    con.close()
    # Оставляем только полные недели (первая и последняя неделя данных неполные — по 5 дней)
    weekly = weekly[weekly["week_num"].between(-8, 7)].copy()
    weekly["week_start"] = pd.to_datetime(weekly["week_start"]).dt.date

    t, c = df[df.group_name == "test"], df[df.group_name == "control"]
    n_t, n_c = len(t), len(c)
    z_a, z_b = stats.norm.ppf(1 - ALPHA / 2), stats.norm.ppf(POWER)

    # -----------------------------------------------------------------------
    header("0. ДИЗАЙН: минимальный обнаруживаемый эффект (MDE) по данным ДО кампании")
    sd_pre = df["pre_spend"].std()
    mde_abs = (z_a + z_b) * sd_pre * np.sqrt(1 / n_t + 1 / n_c)
    print(f"Группы: test = {n_t:,}, control = {n_c:,};  α = {ALPHA}, мощность = {POWER:.0%}")
    print(f"Стандартное отклонение расходов за 2 месяца (до кампании): {sd_pre:,.0f} ₸")
    print(f"MDE ≈ {mde_abs:,.0f} ₸ на клиента ≈ {mde_abs / df['pre_spend'].mean():.1%} от среднего")
    print("Смысл: эффекты меньше этого значения тест без снижения дисперсии часто не заметит.")

    # -----------------------------------------------------------------------
    header("1. SRM: соответствует ли фактическое деление плановому 50/50 (хи-квадрат)")
    chi2, p_srm = stats.chisquare([n_t, n_c], [(n_t + n_c) / 2] * 2)
    print(f"test = {n_t:,} ({n_t / (n_t + n_c):.2%}), control = {n_c:,};  хи-квадрат = {chi2:.3f}, p-value = {p_srm:.4f}")
    print("Вывод:", "расхождение в пределах случайности (p ≥ 0.01), рандомизация в порядке."
          if p_srm >= 0.01 else "ВНИМАНИЕ: признаки SRM — результатам теста доверять нельзя.")

    # -----------------------------------------------------------------------
    header("2. БАЛАНС ГРУПП ДО КАМПАНИИ (|SMD| < 0.1 — сопоставимы)")
    balance = pd.DataFrame([
        ("Расходы до кампании, ₸", c.pre_spend.mean(), t.pre_spend.mean(), smd(t.pre_spend, c.pre_spend),
         stats.ttest_ind(t.pre_spend, c.pre_spend, equal_var=False).pvalue),
        ("Покупок до кампании", c.pre_tx.mean(), t.pre_tx.mean(), smd(t.pre_tx, c.pre_tx),
         stats.ttest_ind(t.pre_tx, c.pre_tx, equal_var=False).pvalue),
        ("Доля активных до кампании", c.pre_active.mean(), t.pre_active.mean(), smd(t.pre_active, c.pre_active),
         stats.ttest_ind(t.pre_active, c.pre_active, equal_var=False).pvalue),
        ("Возраст (без пропусков)", c.age.mean(), t.age.mean(), smd(t.age, c.age),
         stats.ttest_ind(t.age.dropna(), c.age.dropna(), equal_var=False).pvalue),
        ("Срок жизни карты, мес.", c.tenure_months.mean(), t.tenure_months.mean(),
         smd(t.tenure_months, c.tenure_months),
         stats.ttest_ind(t.tenure_months, c.tenure_months, equal_var=False).pvalue),
    ], columns=["Показатель", "control", "test", "SMD", "p_value"])
    print(balance.to_string(index=False))
    for col in ["card_tier", "city_type", "segment"]:
        p_cat = stats.chi2_contingency(pd.crosstab(df[col], df.group_name))[1]
        print(f"Распределение по «{col}»: хи-квадрат p-value = {p_cat:.3f}")

    # -----------------------------------------------------------------------
    header("3. ОСНОВНАЯ МЕТРИКА: расходы на клиента за период кампании (₸ за 2 месяца, ITT)")
    rows = []
    raw = welch_test(t.camp_spend, c.camp_spend)
    rows.append({"metric": "Расходы на клиента (основная)", "method": "t-тест Уэлча", **raw})

    y_adj, theta = cuped(df.camp_spend.to_numpy(float), df.pre_spend.to_numpy(float))
    df["camp_spend_cuped"] = y_adj
    adj = welch_test(df.loc[df.group_name == "test", "camp_spend_cuped"],
                     df.loc[df.group_name == "control", "camp_spend_cuped"])
    rows.append({"metric": "Расходы на клиента (основная)", "method": "CUPED + t-тест Уэлча", **adj})
    var_reduction = 1 - df.camp_spend_cuped.var() / df.camp_spend.var()
    print(f"θ (CUPED) = {theta:.3f};  дисперсия метрики снизилась на {var_reduction:.1%}")
    print("Ниже: первая строка — основной анализ, вторая — уточнение с CUPED.")

    # -----------------------------------------------------------------------
    # 4. Дополнительные и защитные метрики
    rows.append({"metric": "Доля активных клиентов", "method": "z-тест для долей",
                 **proportions_ztest(t.camp_active.sum(), n_t, c.camp_active.sum(), n_c)})
    rows.append({"metric": "Покупок на клиента", "method": "t-тест Уэлча",
                 **welch_test(t.camp_tx, c.camp_tx)})
    rows.append({"metric": "Расходы в акционных категориях", "method": "t-тест Уэлча",
                 **welch_test(t.camp_spend_promo, c.camp_spend_promo)})
    rows.append({"metric": "Расходы в остальных категориях (guardrail)", "method": "t-тест Уэлча",
                 **welch_test(t.camp_spend_other, c.camp_spend_other)})
    ab = pd.DataFrame(rows)
    ab["significant"] = np.where(ab.p_value < ALPHA, "да", "нет")
    print(ab.to_string(index=False))

    header("4. СТОИМОСТЬ КАМПАНИИ (факт из начислений)")
    extra_total = t.camp_cashback_extra.sum()
    print(f"Доп. кешбэк тестовой группе: {extra_total:,.0f} ₸  ({extra_total / n_t:,.0f} ₸ на клиента теста)")
    print(f"Клиентов теста, получивших доп. кешбэк: {(t.camp_cashback_extra > 0).mean():.1%}")
    print(f"Клиентов теста, упёршихся в лимит 5 000 ₸ хотя бы в одном месяце: {n_capped / n_t:.1%}")

    # -----------------------------------------------------------------------
    header("5. СЕГМЕНТЫ: эффект на расходы + поправка Холма (4 сравнения)")
    seg_rows = []
    for seg, g in df.groupby("segment"):
        gt, gc = g[g.group_name == "test"], g[g.group_name == "control"]
        r = welch_test(gt.camp_spend, gc.camp_spend)
        act = proportions_ztest(gt.camp_active.sum(), len(gt), gc.camp_active.sum(), len(gc))
        seg_rows.append({
            "segment": seg, "n_control": len(gc), "n_test": len(gt), **r,
            "active_control": act["control_mean"], "active_test": act["test_mean"],
            "active_p_value": act["p_value"],
            "extra_cashback_per_test_client": gt.camp_cashback_extra.mean(),
        })
    seg = pd.DataFrame(seg_rows)
    seg["p_value_holm"] = holm(seg.p_value)
    seg["significant_holm"] = np.where(seg.p_value_holm < ALPHA, "да", "нет")
    # Сколько доп. кешбэка банк платит на 1 ₸ прироста расходов (если прирост > 0)
    seg["cashback_per_1_tenge_uplift"] = np.where(
        seg.diff_abs > 0, seg.extra_cashback_per_test_client / seg.diff_abs, np.nan)
    print(seg[["segment", "n_control", "n_test", "control_mean", "test_mean", "diff_abs",
               "ci_low_abs", "ci_high_abs", "uplift_rel", "p_value", "p_value_holm",
               "significant_holm"]].to_string(index=False))
    print()
    print(seg[["segment", "active_control", "active_test", "active_p_value",
               "extra_cashback_per_test_client", "cashback_per_1_tenge_uplift"]].to_string(index=False))

    # -----------------------------------------------------------------------
    header("6. ВЫГРУЗКА ДЛЯ POWER BI / EXCEL И ГРАФИКИ")
    OUT_IMG.mkdir(exist_ok=True)
    mart_cols = ["client_id", "group_name", "segment", "card_tier", "city_type", "age", "tenure_months",
                 "pre_spend", "pre_tx", "pre_active", "camp_spend", "camp_spend_promo", "camp_spend_other",
                 "camp_tx", "camp_active", "camp_cashback_base", "camp_cashback_extra"]
    outputs = {
        "client_mart.csv": df[mart_cols],
        "client_weekly.csv": weekly[["client_id", "week_start", "week_num", "period", "spend", "purchases"]],
        "ab_results.csv": ab,
        "segment_results.csv": seg,
    }
    # Целые числа — как целые, суммы — до 2 знаков, строки — в стабильном порядке (удобно для Git)
    outputs["client_weekly.csv"] = (outputs["client_weekly.csv"].astype({"purchases": int}).round({"spend": 2})
                                    .sort_values(["client_id", "week_num", "period"]))
    outputs["client_mart.csv"] = (outputs["client_mart.csv"]
                                  .astype({"pre_tx": int, "camp_tx": int, "pre_active": int,
                                           "camp_active": int, "tenure_months": int, "age": "Int64"})
                                  .round(2).sort_values("client_id"))
    for name, frame in outputs.items():
        # Разделитель дробной части — точка; при импорте в Power BI укажем локаль English (United States)
        frame.round(6).to_csv(OUT_DATA / name, index=False, encoding="utf-8-sig")
        print(f"  data/processed/{name:<22} {len(frame):>8,} строк")

    # График 1: средние недельные расходы на клиента по группам (знаменатель — все назначенные клиенты)
    wk = (weekly.merge(df[["client_id", "group_name"]], on="client_id")
                .groupby(["week_start", "group_name"])["spend"].sum().unstack())
    wk["control"] /= n_c
    wk["test"] /= n_t
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.plot(wk.index, wk["control"], color=COLOR_CONTROL, lw=2, marker="o", ms=4, label="control")
    ax.plot(wk.index, wk["test"], color=COLOR_TEST, lw=2, marker="o", ms=4, label="test")
    ax.axvline(pd.Timestamp(CAMPAIGN_START).date(), color="#888888", ls="--", lw=1)
    ax.text(pd.Timestamp(CAMPAIGN_START).date(), ax.get_ylim()[1], " старт кампании", va="top",
            fontsize=9, color="#555555")
    ax.set_title("Средние расходы на клиента в неделю, ₸ (синтетические данные)", fontsize=11, loc="left")
    ax.grid(axis="y", color="#e5e5e5")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(OUT_IMG / "weekly_spend_by_group.png", dpi=150)
    plt.close(fig)

    # График 2: относительный uplift по сегментам с 95% ДИ
    fig, ax = plt.subplots(figsize=(8, 3.8))
    ypos = np.arange(len(seg))[::-1]
    ax.errorbar(seg.uplift_rel * 100, ypos,
                xerr=[(seg.uplift_rel - seg.ci_low_rel) * 100, (seg.ci_high_rel - seg.uplift_rel) * 100],
                fmt="o", color=COLOR_TEST, ecolor="#999999", capsize=4, ms=7)
    ax.axvline(0, color="#555555", lw=1)
    ax.set_yticks(ypos, seg.segment)
    ax.set_xlabel("Uplift расходов test vs control, % (95% ДИ, без поправки)")
    ax.set_title("Эффект кампании по сегментам (синтетические данные)", fontsize=11, loc="left")
    ax.grid(axis="x", color="#e5e5e5")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT_IMG / "uplift_by_segment.png", dpi=150)
    plt.close(fig)
    print("  images/weekly_spend_by_group.png, images/uplift_by_segment.png")
    print("\nГотово. Данные синтетические — результаты демонстрируют методику, а не эффект реальной кампании.")


if __name__ == "__main__":
    main()
