"""
run_sql.py — выполняет SQL-файл в базе DuckDB проекта и печатает результаты запросов.

Запуск из корня проекта:
    python scripts/run_sql.py sql/01_data_quality.sql

Как это устроено:
* База — файл data/processed/cashback.duckdb (создаётся автоматически).
  Таблицы, созданные одним SQL-файлом, доступны в следующих.
* Файл делится на запросы по символу «;», комментарии «--» пропускаются.
* Строка-комментарий, начинающаяся с «-- >>», печатается как заголовок результата.
"""

import os
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "cashback.duckdb"

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 60)
pd.set_option("display.float_format", lambda x: f"{x:,.2f}")


def parse_statements(text):
    """Делит SQL-текст на запросы по «;». Комментарии «--» отбрасываются,
    строка «-- >> Заголовок» становится заголовком следующего запроса.
    (Внутри строковых литералов в наших SQL-файлах нет «--».)"""
    statements, buf, title = [], [], None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("-- >>"):
            title = stripped[5:].strip()
            continue
        code = line.split("--", 1)[0]
        while ";" in code:
            before, code = code.split(";", 1)
            buf.append(before)
            sql = "\n".join(buf).strip()
            if sql:
                statements.append((title, sql))
            buf, title = [], None
        buf.append(code)
    tail = "\n".join(buf).strip()
    if tail:
        statements.append((title, tail))
    return statements


def main():
    if len(sys.argv) != 2:
        print("Использование: python scripts/run_sql.py sql/<файл>.sql")
        sys.exit(1)

    sql_path = (ROOT / sys.argv[1]).resolve() if not Path(sys.argv[1]).is_absolute() else Path(sys.argv[1])
    os.chdir(ROOT)  # пути к CSV в SQL указаны относительно корня проекта
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))

    statements = parse_statements(sql_path.read_text(encoding="utf-8"))
    print(f"Файл: {sql_path.relative_to(ROOT)}   База: {DB_PATH.relative_to(ROOT)}\n")

    for title, stmt in statements:
        result = con.sql(stmt)
        if result is not None:  # SELECT возвращает таблицу; CREATE — нет
            print("=" * 100)
            print(title or "Результат запроса")
            print("-" * 100)
            df = result.df()
            for col in df.select_dtypes("float").columns:  # целые числа печатаем без «.00»
                values = df[col].dropna()
                if len(values) and (values % 1 == 0).all():
                    df[col] = df[col].astype("Int64")
            print(df.to_string(index=False))
            print()

    con.close()
    print("Готово.")


if __name__ == "__main__":
    main()
