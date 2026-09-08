import os
import re
from io import BytesIO

from app import app, get_db, init_db


def _extract_report_id_from_pdf_bytes(data: bytes) -> str | None:
    # Ищем report_id по имени файла в PDF метаданных или по включению текста.
    # Это best-effort, т.к. PDF может хранить текст в сжатом виде.
    m = re.search(rb"service_report_(\d+)\.pdf", data)
    if m:
        return m.group(1).decode("ascii", errors="ignore")
    return None


def run():
    # Чистим БД для теста
    db_path = os.path.join(os.path.dirname(__file__), "data", "app.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    with app.app_context():
        init_db()

    client = app.test_client()

    # 1) Регистрация сотрудника
    r = client.post(
        "/register",
        data={
            "last_name": "Иванов",
            "first_name": "Иван",
            "middle_name": "Иванович",
            "position": "Механик",
            "login": "ivanov",
            "password": "pass123",
            "password2": "pass123",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200

    # 2) Попытка входа сотрудника до одобрения должна дать ошибку
    r = client.post(
        "/login",
        data={"login": "ivanov", "password": "pass123"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "ожидает подтверждения".encode("utf-8") in r.data

    # 3) Вход админа
    r = client.post(
        "/login",
        data={"login": "admin", "password": "admin"},
        follow_redirects=True,
    )
    assert r.status_code == 200

    # 4) Найти пользователя и одобрить
    with app.app_context():
        db = get_db()
        cur = db.execute("SELECT id FROM users WHERE login = ?", ("ivanov",))
        row = cur.fetchone()
        assert row is not None
        user_id = row["id"]

    r = client.post(f"/admin/user/{user_id}/approve", follow_redirects=True)
    assert r.status_code == 200

    # 5) Логаут админа и логин сотрудника
    client.get("/logout", follow_redirects=True)
    r = client.post(
        "/login",
        data={"login": "ivanov", "password": "pass123"},
        follow_redirects=True,
    )
    assert r.status_code == 200

    # 6) Генерация PDF через /submit с кириллицей
    r = client.post(
        "/submit",
        data={
            "date": "06.03.2026",
            "organization": "ООО Ромашка",
            "equipment_type": "Экскаватор",
            "serial_number": "SN123",
            "internal_number": "IN123",
            "location": "Москва",
            "engine_hours": "100",
            "year": "2020",
            "work_type": ["ТО"],
            "work_description": "Проверка. Замена масла.",
            "recommendations": "Проверить через 500 моточасов.",
            "part_code[]": ["A-1"],
            "part_name[]": ["Масло моторное"],
            "part_qty[]": ["10 л"],
        },
    )
    assert r.status_code == 200
    assert r.headers.get("Content-Type", "").startswith("application/pdf")
    assert len(r.data) > 1000
    # Проверяем, что шрифт DejaVuSans встраивается в PDF
    assert b"DejaVuSans" in r.data

    # 7) Достаём id последнего отчёта из БД и проверяем /report/<id>/pdf
    with app.app_context():
        db = get_db()
        cur = db.execute("SELECT id FROM reports ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        assert row is not None
        report_row_id = row["id"]

    r2 = client.get(f"/report/{report_row_id}/pdf")
    assert r2.status_code == 200
    assert r2.headers.get("Content-Type", "").startswith("application/pdf")
    assert len(r2.data) > 1000
    assert b"DejaVuSans" in r2.data

    print("OK: smoke_test passed")


if __name__ == "__main__":
    run()

