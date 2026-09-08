import math
import sqlite3

import pandas as pd

from app import DB_PATH, app, init_db


def is_nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def to_text(v):
    if is_nan(v):
        return None
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return str(v).strip()
    s = str(v).strip()
    return s if s else None


def to_int_or_text(v):
    # В Excel поля часто могут быть числами — превращаем в строку без лишней .0
    return to_text(v)


def main():
    # 1) Создаём/инициализируем схему
    with app.app_context():
        init_db()

    # 2) Открываем БД
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 3) Админ (будет user_id для восстановленных отчётов)
    row = cur.execute("SELECT id FROM users WHERE login = ?", ("admin",)).fetchone()
    if not row:
        raise RuntimeError("admin user not found after init_db")
    admin_user_id = row["id"]

    # 4) Чистим reports
    cur.execute("DELETE FROM reports")
    con.commit()

    # 5) Читаем Excel
    xls_path = "data/reports.xlsx"
    df = pd.read_excel(xls_path)

    cols = [r[1] for r in cur.execute("PRAGMA table_info(reports)").fetchall()]
    wanted = {
        "report_id",
        "seq_num",
        "company_code",
        "user_id",
        "created_at",
        "printed_at",
        "opened_at",
        "date",
        "organization",
        "equipment_type",
        "serial_number",
        "internal_number",
        "location",
        "engine_hours",
        "year",
        "work_types",
        "work_description",
        "recommendations",
        "materials_json",
    }
    insert_cols = [c for c in cols if c in wanted]
    placeholders = ",".join(["?"] * len(insert_cols))
    col_list = ",".join(insert_cols)
    sql = f"INSERT INTO reports ({col_list}) VALUES ({placeholders})"

    seq = 1
    inserted = 0
    for _, r in df.iterrows():
        report_id = to_int_or_text(r.get("report_id"))
        if not report_id:
            continue

        row_values = {
            "report_id": report_id,
            "seq_num": seq,
            "company_code": to_text(r.get("company_code")),
            "user_id": admin_user_id,
            "created_at": to_text(r.get("created_at")),
            "printed_at": None,
            "opened_at": None,
            "date": to_text(r.get("date")),
            "organization": to_text(r.get("organization")),
            "equipment_type": to_text(r.get("equipment_type")),
            "serial_number": to_text(r.get("serial_number")),
            "internal_number": to_text(r.get("internal_number")),
            "location": to_text(r.get("location")),
            "engine_hours": to_text(r.get("engine_hours")),
            "year": to_text(r.get("year")),
            "work_types": to_text(r.get("work_types")),
            "work_description": to_text(r.get("work_description")),
            "recommendations": to_text(r.get("recommendations")),
            "materials_json": to_text(r.get("materials_json")),
        }

        values = [row_values.get(c) for c in insert_cols]
        cur.execute(sql, values)
        inserted += 1
        seq += 1

    con.commit()

    cnt = cur.execute("SELECT count(*) FROM reports").fetchone()[0]
    print(f"reports restored: inserted={inserted}, count={cnt}")

    sched_cnt = cur.execute("SELECT count(*) FROM mindmap_equipment_schedule").fetchone()[0]
    print(f"mindmap schedule rows: {sched_cnt}")

    con.close()


if __name__ == "__main__":
    main()

