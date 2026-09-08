"""Учёт служебных авто механиков: пробег, топливо, адаптивный расход."""

from __future__ import annotations

import calendar
import os
from datetime import date, datetime
from io import BytesIO
from typing import Any, Callable

FLEET_PHOTO_PX = 1000
FLEET_PHOTO_DPI = 72
FLEET_PHOTO_SLOTS = 4

FLEET_FUEL_TYPE_CHOICES: tuple[tuple[str, str], ...] = (
    ("gasoline_92", "Бензин 92"),
    ("gasoline_95", "Бензин 95"),
    ("gasoline_100", "Бензин 100"),
    ("diesel", "Дизель"),
)
FLEET_FUEL_TYPE_CODES = frozenset(code for code, _ in FLEET_FUEL_TYPE_CHOICES)

FLEET_BRANDING_PRESETS: tuple[tuple[str, str], ...] = (
    ("branded", "Брендирован"),
    ("field_service", "Оборудован по выездной сервис"),
    ("recorder", "Работает регистратор"),
)

DEFAULT_FLEET_VAN_EQUIPMENT_CATALOG: tuple[str, ...] = (
    "Огнетушитель",
    "Аптечка",
    "Знак аварийной остановки",
    "Жилет светоотражающий",
    "Домкрат",
    "Баллонный ключ",
    "Компрессор",
    "Провода прикуривания",
    "Трос буксировочный",
    "Лопата",
    "Кабель питания регистратора",
    "Запасное колесо",
)

DEFAULT_FUEL_STATION_NAMES: tuple[str, ...] = (
    "Лукойл",
    "Газпромнефть",
    "Роснефть",
    "Татнефть",
    "Teboil",
    "Shell",
    "Другое",
)

VEHICLE_MODELS: dict[str, dict[str, Any]] = {
    "fleet_custom": {
        "name": "Служебный автомобиль",
        "image": "gazelle_nn.svg",
        "factory_l100": 10.0,
        "winter_factor": 1.1,
        "fuel_reference": {
            "official_range": "—",
            "cycles": [],
            "notes": ["Регламент задаётся для каждой машины в автопарке."],
        },
        "maintenance": {
            "official_interval_km": 10000,
            "recommended_oil_km": 10000,
            "warranty": "",
            "schedule": [],
            "notes": [],
            "service_milestones_km": [],
        },
    },
    "gazelle_nn": {
        "name": "ГАЗель NN",
        "image": "gazelle_nn.svg",
        "factory_l100": 10.5,
        "winter_factor": 1.15,
        "fuel_reference": {
            "official_range": "9,5–11,2 л/100 км",
            "cycles": [
                {"label": "Смешанный цикл", "value": "10–12 л/100 км"},
                {"label": "Город", "value": "11,5–13 л/100 км"},
                {"label": "Трасса", "value": "до 10 л/100 км (≈100 км/ч, 6-я передача)"},
            ],
            "notes": [
                "Реальный расход сильно зависит от манеры вождения, особенно от работы с педалью газа.",
                "Зимой из‑за прогрева и предпускового подогревателя расход ожидаемо выше.",
                "На трассе ГАЗель NN экономичнее предшественника примерно на 1 л/100 км.",
            ],
        },
        "maintenance": {
            "official_interval_km": 20000,
            "recommended_oil_km": 10000,
            "warranty": "3 года или 200 000 км",
            "schedule": [
                {
                    "mileage": "2 000 км",
                    "title": "Обкатка",
                    "works": "Замена моторного масла и масляного фильтра. Проверка и протяжка креплений двигателя, КПП, элементов подвески.",
                },
                {
                    "mileage": "Каждые 10 000 км",
                    "title": "Рекомендованное ТО",
                    "works": "Замена масла и масляного фильтра, проверка воздушного и топливного фильтров, уровня жидкостей в КПП и мосту, тормозных колодок, шин, рулевого управления.",
                },
                {
                    "mileage": "Каждые 20 000 км",
                    "title": "Официальное ТО",
                    "works": "Замена масла, масляного, воздушного и топливного фильтров. Диагностика ЭБУ. Замена тормозной жидкости (рекомендуется каждые 2 года).",
                },
                {
                    "mileage": "Каждые 60 000 км",
                    "title": "Жидкости",
                    "works": "Замена охлаждающей жидкости (антифриза) и масла в МКПП и заднем мосту.",
                },
                {
                    "mileage": "60 000–90 000 км",
                    "title": "ГРМ",
                    "works": "Замена ремня и роликов ГРМ (для дизеля). Завод заявляет до 120 000 км, специалисты рекомендуют раньше.",
                },
            ],
            "notes": [
                "Официальный межсервисный интервал — 20 000 км, но в РФ рекомендуют 7 500–10 000 км для замены масла.",
                "Двигатель G21A (Foton Aucan): гидрокомпенсаторы — регулировка клапанов не требуется.",
            ],
            "service_milestones_km": [2000, 10000, 20000, 60000, 90000],
        },
    },
    "peugeot_expert": {
        "name": "Peugeot Expert",
        "image": "peugeot_expert.svg",
        "factory_l100": 7.4,
        "winter_factor": 1.12,
        "fuel_reference": {
            "official_range": "6,3 л/100 км (смешанный цикл)",
            "real_mixed": "≈7,4 л/100 км",
            "cycles": [
                {"label": "Смешанный", "value": "6,3 л/100 км"},
                {"label": "Город", "value": "7,2 л/100 км"},
                {"label": "Трасса", "value": "5,8 л/100 км"},
            ],
            "notes": [
                "В тестах с разной загрузкой и стилем вождения реальный расход около 7,4 л/100 км — близко к заводским показателям.",
                "1,5 дизель (2019 г.): владельцы указывают ~45 mpg (≈6,3 л/100 км).",
                "Старые версии 2,0 HDi (конец 2000-х): 24–29 mpg (≈8–10 л/100 км) — не относится к современным моделям.",
                "Фактический расход зависит от загрузки фургона и стиля вождения.",
            ],
        },
        "maintenance": {
            "official_interval_km": 20000,
            "official_interval_label": "20 000 км / 1 год",
            "recommended_oil_km": 10000,
            "warranty": "по условиям дилера",
            "schedule": [
                {
                    "mileage": "Каждые 10 000 км",
                    "title": "Замена масла (реком.)",
                    "works": "Масло и масляный фильтр — чаще официального интервала, особенно при коротких поездках, буксировке прицепа и тяжёлых условиях.",
                },
                {
                    "mileage": "20 000 км / 1 год",
                    "title": "Официальное ТО",
                    "works": "Плановое обслуживание по регламенту производителя: масло, фильтры, проверка узлов. Срок — что наступит раньше: пробег или год.",
                },
            ],
            "notes": [
                "Даже при пробеге менее 20 000 км за год ТО необходимо проходить — регламент привязан к сроку.",
                "Дилерские центры, как правило, придерживаются интервала 20 000 км / 1 год.",
                "Бортовой компьютер может сигнализировать о ТО раньше в зависимости от стиля вождения.",
            ],
            "service_milestones_km": [10000, 20000],
        },
    },
    "lada_largus": {
        "name": "Lada Largus",
        "image": "lada_largus.svg",
        "factory_l100": 10.2,
        "winter_factor": 1.13,
        "fuel_reference": {
            "official_range": "8,5–10,5 л/100 км",
            "cycles": [
                {"label": "Смешанный цикл", "value": "9–11 л/100 км"},
                {"label": "Город", "value": "10–12 л/100 км"},
                {"label": "Трасса", "value": "8–9,5 л/100 км"},
            ],
            "notes": [
                "При полной загрузке и зимней эксплуатации расход выше паспортного.",
            ],
        },
        "maintenance": {
            "official_interval_km": 15000,
            "recommended_oil_km": 10000,
            "warranty": "3 года или 100 000 км",
            "schedule": [
                {
                    "mileage": "Каждые 10 000–15 000 км",
                    "title": "Плановое ТО",
                    "works": "Замена масла и фильтров, проверка тормозов, ремней, жидкостей.",
                },
            ],
            "notes": [],
            "service_milestones_km": [10000, 15000, 30000, 45000, 60000],
        },
    },
}

WINTER_MONTHS = {12, 1, 2}

# Временные тестовые данные в списке Автопарк (подгонка шрифтов). Убрать: False
FLEET_GRID_PREVIEW = False

_FLEET_PREVIEW = {
    "latest_mileage": 127_000,
    "snapshot": {
        "km": 1847,
        "amount_rub": 28_450.0,
        "fill_count": 8,
        "actual_l100": 11.4,
    },
    "last_oil_km": 117_000,
}

# Временные карточки авто (если в БД меньше 4 машин с привязкой). Убрать вместе с FLEET_GRID_PREVIEW.
_FLEET_PREVIEW_VEHICLES = [
    {
        "id": -9001,
        "last_name": "Соколов",
        "first_name": "Андрей",
        "middle_name": "Игоревич",
        "model_code": "gazelle_nn",
        "plate_number": "А123ВС 777",
        "latest_mileage": 127_000,
        "last_oil_km": 117_000,
        "mileage_display": "127 000",
        "snapshot": {
            "km": 1847,
            "amount_rub": 28_450.0,
            "fill_count": 8,
            "actual_l100": 11.4,
        },
    },
    {
        "id": -9002,
        "last_name": "Морозов",
        "first_name": "Павел",
        "middle_name": "Сергеевич",
        "model_code": "peugeot_expert",
        "plate_number": "В456КМ 199",
        "latest_mileage": 86_400,
        "last_oil_km": 80_000,
        "mileage_display": "86 400",
        "snapshot": {
            "km": 1320,
            "amount_rub": 14_800.0,
            "fill_count": 5,
            "actual_l100": 7.9,
        },
    },
    {
        "id": -9003,
        "last_name": "Кузнецова",
        "first_name": "Елена",
        "middle_name": "Викторовна",
        "model_code": "lada_largus",
        "plate_number": "Е789ОР 750",
        "latest_mileage": 54_200,
        "last_oil_km": 50_000,
        "mileage_display": "54 200",
        "snapshot": {
            "km": 980,
            "amount_rub": 11_200.0,
            "fill_count": 4,
            "actual_l100": 10.8,
        },
    },
    {
        "id": -9004,
        "last_name": "Никитин",
        "first_name": "Дмитрий",
        "middle_name": "Александрович",
        "model_code": "gazelle_nn",
        "plate_number": "К321ТА 177",
        "latest_mileage": 203_500,
        "last_oil_km": 195_000,
        "mileage_display": "203 500",
        "snapshot": {
            "km": 2410,
            "amount_rub": 36_900.0,
            "fill_count": 11,
            "actual_l100": 12.6,
        },
    },
]

_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def init_vehicle_fleet_schema(db) -> None:
    cur = db.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mechanic_vehicles (
            user_id INTEGER PRIMARY KEY,
            model_code TEXT NOT NULL,
            plate_number TEXT NOT NULL,
            assigned_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_mileage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            mileage_km INTEGER NOT NULL,
            logged_at TEXT NOT NULL,
            note TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_fuel_monthly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            year_month TEXT NOT NULL,
            amount_rub REAL NOT NULL DEFAULT 0,
            fill_count INTEGER NOT NULL DEFAULT 0,
            note TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, year_month),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_fuel_fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            fill_date TEXT NOT NULL,
            liters REAL NOT NULL DEFAULT 0,
            price_per_liter REAL NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vehicle_fuel_fills_user_date
        ON vehicle_fuel_fills (user_id, fill_date)
        """
    )
    cur.execute("PRAGMA table_info(vehicle_fuel_fills)")
    vff_cols = {row[1] for row in cur.fetchall()}
    if "station_name" not in vff_cols:
        cur.execute("ALTER TABLE vehicle_fuel_fills ADD COLUMN station_name TEXT")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_oil_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            mileage_km INTEGER NOT NULL,
            logged_at TEXT NOT NULL,
            note TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS fleet_vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            plate_number TEXT NOT NULL UNIQUE,
            color TEXT,
            is_branded INTEGER NOT NULL DEFAULT 0,
            has_field_service INTEGER NOT NULL DEFAULT 0,
            initial_mileage INTEGER NOT NULL DEFAULT 0,
            assigned_user_id INTEGER,
            assigned_at TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY (assigned_user_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS fleet_vehicle_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fleet_vehicle_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            mileage_mark TEXT NOT NULL,
            mileage_km INTEGER,
            comment TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (fleet_vehicle_id) REFERENCES fleet_vehicles(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS fleet_vehicle_important_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fleet_vehicle_id INTEGER NOT NULL,
            event_date TEXT,
            mileage_km INTEGER,
            title TEXT NOT NULL,
            comment TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (fleet_vehicle_id) REFERENCES fleet_vehicles(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS fleet_vehicle_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fleet_vehicle_id INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(fleet_vehicle_id, slot),
            FOREIGN KEY (fleet_vehicle_id) REFERENCES fleet_vehicles(id) ON DELETE CASCADE
        )
        """
    )
    # Связь старой привязки механика с машиной автопарка
    cur.execute("PRAGMA table_info(mechanic_vehicles)")
    mv_cols = {row[1] for row in cur.fetchall()}
    if "fleet_vehicle_id" not in mv_cols:
        cur.execute("ALTER TABLE mechanic_vehicles ADD COLUMN fleet_vehicle_id INTEGER")
    cur.execute("PRAGMA table_info(fleet_vehicles)")
    fv_cols = {row[1] for row in cur.fetchall()}
    if "fuel_type" not in fv_cols:
        cur.execute("ALTER TABLE fleet_vehicles ADD COLUMN fuel_type TEXT")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS fleet_vehicle_branding_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fleet_vehicle_id INTEGER NOT NULL,
            item_key TEXT NOT NULL DEFAULT 'custom',
            label TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (fleet_vehicle_id) REFERENCES fleet_vehicles(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS fleet_vehicle_van_equipment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fleet_vehicle_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            note TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (fleet_vehicle_id) REFERENCES fleet_vehicles(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS fleet_vehicle_mechanic_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fleet_vehicle_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            title TEXT,
            event_date TEXT,
            status TEXT NOT NULL DEFAULT 'green',
            max_duplicated INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (fleet_vehicle_id) REFERENCES fleet_vehicles(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute("PRAGMA table_info(fleet_vehicle_mechanic_messages)")
    mm_cols = {row[1] for row in cur.fetchall()}
    if "event_date" not in mm_cols:
        cur.execute("ALTER TABLE fleet_vehicle_mechanic_messages ADD COLUMN event_date TEXT")
    if "title" not in mm_cols:
        cur.execute("ALTER TABLE fleet_vehicle_mechanic_messages ADD COLUMN title TEXT")
    if "status" not in mm_cols:
        cur.execute("ALTER TABLE fleet_vehicle_mechanic_messages ADD COLUMN status TEXT NOT NULL DEFAULT 'green'")
    if "max_duplicated" not in mm_cols:
        cur.execute(
            "ALTER TABLE fleet_vehicle_mechanic_messages ADD COLUMN max_duplicated INTEGER NOT NULL DEFAULT 0"
        )
    if "admin_state" not in mm_cols:
        cur.execute(
            "ALTER TABLE fleet_vehicle_mechanic_messages ADD COLUMN admin_state TEXT NOT NULL DEFAULT 'open'"
        )
    cur.execute("PRAGMA table_info(fleet_vehicle_schedule)")
    fvs_cols = {row[1] for row in cur.fetchall()}
    if "status" not in fvs_cols:
        cur.execute("ALTER TABLE fleet_vehicle_schedule ADD COLUMN status TEXT NOT NULL DEFAULT 'green'")
    cur.execute("PRAGMA table_info(fleet_vehicle_important_info)")
    fvi_cols = {row[1] for row in cur.fetchall()}
    if "status" not in fvi_cols:
        cur.execute("ALTER TABLE fleet_vehicle_important_info ADD COLUMN status TEXT NOT NULL DEFAULT 'green'")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS fleet_vehicle_schedule_baseline (
            fleet_vehicle_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL,
            baseline_mileage_km INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (fleet_vehicle_id, sort_order),
            FOREIGN KEY (fleet_vehicle_id) REFERENCES fleet_vehicles(id) ON DELETE CASCADE
        )
        """
    )
    from vehicle_fuel_report import init_fuel_report_schema

    init_fuel_report_schema(db)
    db.commit()


def _normalize_regulation_status(value) -> str:
    code = (value or "green").strip().lower()
    if code not in ("red", "yellow", "green"):
        return "green"
    return code


def get_fleet_van_equipment_catalog(get_setting_fn: Callable[[str, str], str] | None = None) -> list[str]:
    import json

    if get_setting_fn:
        raw = (get_setting_fn("fleet_van_equipment_catalog", "") or "").strip()
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    items = [str(x).strip() for x in data if str(x).strip()]
                    if items:
                        return items
            except json.JSONDecodeError:
                items = [ln.strip() for ln in raw.splitlines() if ln.strip()]
                if items:
                    return items
    return list(DEFAULT_FLEET_VAN_EQUIPMENT_CATALOG)


def get_fuel_station_catalog(get_setting_fn: Callable[[str, str], str] | None = None) -> list[str]:
    import json

    if get_setting_fn:
        raw = (get_setting_fn("fleet_fuel_station_catalog", "") or "").strip()
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    items = [str(x).strip() for x in data if str(x).strip()]
                    if items:
                        return items
            except json.JSONDecodeError:
                items = [ln.strip() for ln in raw.splitlines() if ln.strip()]
                if items:
                    return items
    return list(DEFAULT_FUEL_STATION_NAMES)


def build_branding_presets(branding_items: list[dict]) -> list[dict]:
    saved = {(item.get("item_key") or "").strip().lower() for item in branding_items}
    return [
        {
            "item_key": key,
            "label": label,
            "enabled": key in saved,
        }
        for key, label in FLEET_BRANDING_PRESETS
    ]


def build_van_equipment_catalog_state(
    catalog: list[str],
    van_equipment: list[dict],
) -> list[dict]:
    selected = {(row.get("title") or "").strip() for row in van_equipment}
    items: list[dict] = []
    seen: set[str] = set()
    for title in catalog:
        title = (title or "").strip()
        if not title or title in seen:
            continue
        items.append({"title": title, "selected": title in selected})
        seen.add(title)
    for row in van_equipment:
        title = (row.get("title") or "").strip()
        if title and title not in seen:
            items.append({"title": title, "selected": True})
            seen.add(title)
    return items


def list_fleet_vehicle_branding_items(db, vehicle_id: int) -> list[dict]:
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, item_key, label, sort_order
        FROM fleet_vehicle_branding_items
        WHERE fleet_vehicle_id = ?
        ORDER BY sort_order ASC, id ASC
        """,
        (int(vehicle_id),),
    )
    return [dict(r) for r in cur.fetchall()]


def update_fleet_vehicle_branding_items(db, vehicle_id: int, items: list[dict]) -> None:
    if not get_fleet_vehicle(db, vehicle_id):
        raise ValueError("Машина не найдена в автопарке")
    cur = db.cursor()
    cur.execute("DELETE FROM fleet_vehicle_branding_items WHERE fleet_vehicle_id = ?", (int(vehicle_id),))
    for idx, raw in enumerate(items or []):
        item_key = (raw.get("item_key") or "custom").strip().lower()
        if item_key not in ("branded", "field_service", "recorder", "custom"):
            item_key = "custom"
        label = (raw.get("label") or "").strip()
        if not label:
            continue
        cur.execute(
            """
            INSERT INTO fleet_vehicle_branding_items (fleet_vehicle_id, item_key, label, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (int(vehicle_id), item_key, label, int(raw.get("sort_order", idx))),
        )
    db.commit()


def list_fleet_vehicle_van_equipment(db, vehicle_id: int) -> list[dict]:
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, title, note, sort_order
        FROM fleet_vehicle_van_equipment
        WHERE fleet_vehicle_id = ?
        ORDER BY sort_order ASC, id ASC
        """,
        (int(vehicle_id),),
    )
    return [dict(r) for r in cur.fetchall()]


def update_fleet_vehicle_van_equipment(db, vehicle_id: int, items: list[dict]) -> None:
    if not get_fleet_vehicle(db, vehicle_id):
        raise ValueError("Машина не найдена в автопарке")
    cur = db.cursor()
    cur.execute("DELETE FROM fleet_vehicle_van_equipment WHERE fleet_vehicle_id = ?", (int(vehicle_id),))
    for idx, raw in enumerate(items or []):
        title = (raw.get("title") or "").strip()
        note = (raw.get("note") or "").strip()
        if not title and not note:
            continue
        if not title:
            title = f"Позиция {idx + 1}"
        cur.execute(
            """
            INSERT INTO fleet_vehicle_van_equipment (fleet_vehicle_id, title, note, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (int(vehicle_id), title, note or None, int(raw.get("sort_order", idx))),
        )
    db.commit()


def list_fleet_vehicle_mechanic_messages(
    db,
    vehicle_id: int,
    *,
    limit: int = 50,
    open_only: bool = False,
) -> list[dict]:
    cur = db.cursor()
    sql = """
        SELECT id, fleet_vehicle_id, user_id, message, title, event_date, status,
               max_duplicated, admin_state, created_at
        FROM fleet_vehicle_mechanic_messages
        WHERE fleet_vehicle_id = ?
    """
    params: list = [int(vehicle_id)]
    if open_only:
        sql += " AND COALESCE(admin_state, 'open') = 'open'"
    sql += " ORDER BY datetime(COALESCE(event_date, created_at)) DESC, id DESC LIMIT ?"
    params.append(int(limit))
    cur.execute(sql, params)
    rows = []
    for row in cur.fetchall():
        item = dict(row)
        item["status"] = _normalize_regulation_status(item.get("status"))
        item["admin_state"] = (item.get("admin_state") or "open").strip().lower()
        rows.append(item)
    return rows


def count_open_mechanic_messages(db, vehicle_id: int) -> int:
    cur = db.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FROM fleet_vehicle_mechanic_messages
        WHERE fleet_vehicle_id = ? AND COALESCE(admin_state, 'open') = 'open'
        """,
        (int(vehicle_id),),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def count_open_mechanic_messages_important(db, vehicle_id: int) -> int:
    cur = db.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FROM fleet_vehicle_mechanic_messages
        WHERE fleet_vehicle_id = ?
          AND COALESCE(admin_state, 'open') = 'open'
          AND LOWER(COALESCE(status, 'green')) = 'red'
        """,
        (int(vehicle_id),),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def set_mechanic_message_admin_state(
    db,
    message_id: int,
    fleet_vehicle_id: int,
    admin_state: str,
) -> dict:
    state = (admin_state or "").strip().lower()
    if state not in ("accepted", "resolved"):
        raise ValueError("Некорректное действие по обращению")
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, user_id, message, title, event_date, status, admin_state
        FROM fleet_vehicle_mechanic_messages
        WHERE id = ? AND fleet_vehicle_id = ?
        """,
        (int(message_id), int(fleet_vehicle_id)),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError("Обращение не найдено")
    item = dict(row)
    if (item.get("admin_state") or "open") != "open":
        raise ValueError("Обращение уже обработано")
    cur.execute(
        """
        UPDATE fleet_vehicle_mechanic_messages
        SET admin_state = ?
        WHERE id = ? AND fleet_vehicle_id = ?
        """,
        (state, int(message_id), int(fleet_vehicle_id)),
    )
    db.commit()
    item["admin_state"] = state
    return item


def add_fleet_vehicle_mechanic_message(
    db,
    vehicle_id: int,
    user_id: int,
    message: str,
    *,
    title: str | None = None,
    event_date: str | None = None,
    status: str = "green",
    max_duplicated: bool = False,
) -> int:
    if not get_fleet_vehicle(db, vehicle_id):
        raise ValueError("Машина не найдена в автопарке")
    text = (message or "").strip()
    if not text:
        raise ValueError("Введите текст сообщения.")
    if len(text) > 4000:
        raise ValueError("Сообщение слишком длинное (максимум 4000 символов).")
    title_text = (title or "").strip()[:200] or None
    status_code = _normalize_regulation_status(status)
    event_day = (event_date or "").strip()[:10]
    if not event_day:
        event_day = datetime.now().strftime("%Y-%m-%d")
    try:
        date.fromisoformat(event_day)
    except ValueError as exc:
        raise ValueError("Укажите корректную дату.") from exc
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO fleet_vehicle_mechanic_messages (
            fleet_vehicle_id, user_id, message, title, event_date, status,
            max_duplicated, admin_state, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (
            int(vehicle_id),
            int(user_id),
            text,
            title_text,
            event_day,
            status_code,
            1 if max_duplicated else 0,
            now,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def normalize_fleet_fuel_type(value: str | None) -> str:
    code = (value or "").strip()
    if code not in FLEET_FUEL_TYPE_CODES:
        raise ValueError("Выберите тип топлива")
    return code


def _interval_remaining_km(baseline_km: int, interval_km: int, latest_km: int) -> int:
    """Остаток км до следующего срабатывания регламента «каждые N км»."""
    elapsed = int(latest_km) - int(baseline_km)
    if elapsed < 0:
        return int(interval_km)
    remainder = elapsed % int(interval_km)
    if remainder == 0 and elapsed > 0:
        return 0
    return int(interval_km) - remainder


def get_schedule_baselines(db, vehicle_id: int) -> dict[int, int]:
    cur = db.cursor()
    cur.execute(
        """
        SELECT sort_order, baseline_mileage_km
        FROM fleet_vehicle_schedule_baseline
        WHERE fleet_vehicle_id = ?
        """,
        (int(vehicle_id),),
    )
    return {int(row["sort_order"]): int(row["baseline_mileage_km"]) for row in cur.fetchall()}


def _default_baseline_mileage(db, vehicle_id: int) -> int | None:
    vehicle = get_fleet_vehicle(db, vehicle_id)
    if not vehicle:
        return None
    assigned = vehicle.get("assigned_user_id")
    if assigned:
        latest = get_latest_mileage(db, int(assigned))
        if latest is not None:
            return int(latest)
    initial = int(vehicle.get("initial_mileage") or 0)
    return initial if initial > 0 else None


def sync_schedule_baselines(db, vehicle_id: int, sort_orders: list[int], *, commit: bool = True) -> None:
    existing = get_schedule_baselines(db, vehicle_id)
    default_km = _default_baseline_mileage(db, vehicle_id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.cursor()
    keep = {int(s) for s in sort_orders}
    if keep:
        placeholders = ",".join("?" for _ in keep)
        cur.execute(
            f"""
            DELETE FROM fleet_vehicle_schedule_baseline
            WHERE fleet_vehicle_id = ? AND sort_order NOT IN ({placeholders})
            """,
            (int(vehicle_id), *sorted(keep)),
        )
    else:
        cur.execute(
            "DELETE FROM fleet_vehicle_schedule_baseline WHERE fleet_vehicle_id = ?",
            (int(vehicle_id),),
        )
    for sort_order in sort_orders:
        so = int(sort_order)
        baseline = existing.get(so, default_km)
        if baseline is None:
            continue
        cur.execute(
            """
            INSERT INTO fleet_vehicle_schedule_baseline (
                fleet_vehicle_id, sort_order, baseline_mileage_km, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(fleet_vehicle_id, sort_order) DO NOTHING
            """,
            (int(vehicle_id), so, int(baseline), now),
        )
    if commit:
        db.commit()


def mark_regulation_done(db, vehicle_id: int, sort_order: int, mileage_km: int) -> None:
    if not get_fleet_vehicle(db, vehicle_id):
        raise ValueError("Машина не найдена в автопарке")
    schedule = list_fleet_vehicle_schedule(db, vehicle_id)
    if not any(int(item.get("sort_order", 0)) == int(sort_order) for item in schedule):
        raise ValueError("Пункт регламента не найден")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO fleet_vehicle_schedule_baseline (
            fleet_vehicle_id, sort_order, baseline_mileage_km, updated_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(fleet_vehicle_id, sort_order) DO UPDATE SET
            baseline_mileage_km = excluded.baseline_mileage_km,
            updated_at = excluded.updated_at
        """,
        (int(vehicle_id), int(sort_order), int(mileage_km), now),
    )
    db.commit()


def unmark_regulation_done(db, vehicle_id: int, sort_order: int, mileage_km: int) -> None:
    """Отменить «Выполнено»: вернуть пункт в статус срока (остаток 0 км)."""
    if not get_fleet_vehicle(db, vehicle_id):
        raise ValueError("Машина не найдена в автопарке")
    schedule = list_fleet_vehicle_schedule(db, vehicle_id)
    item = next((row for row in schedule if int(row.get("sort_order", 0)) == int(sort_order)), None)
    if not item:
        raise ValueError("Пункт регламента не найден")
    try:
        interval = int(item.get("mileage_km") or 0)
    except (TypeError, ValueError):
        interval = 0
    if interval <= 0:
        raise ValueError("Не задан интервал пробега")
    restored = max(0, int(mileage_km) - interval)
    mark_regulation_done(db, vehicle_id, sort_order, restored)


def compute_interval_auto_status(remaining_km: int | None, interval_km: int | None) -> str:
    """Зелёный — далеко от срока, жёлтый — приближается, красный — срок или просрочка."""
    if remaining_km is None or interval_km is None or int(interval_km) <= 0:
        return "green"
    if int(remaining_km) <= 0:
        return "red"
    yellow_at = max(3000, int(int(interval_km) * 0.25))
    if int(remaining_km) <= yellow_at:
        return "yellow"
    return "green"


def important_days_until(event_date: str | None, ref_date=None) -> int | None:
    from datetime import date

    raw = (event_date or "").strip()
    if not raw:
        return None
    ref = ref_date or date.today()
    try:
        ed = date.fromisoformat(raw[:10])
    except ValueError:
        return None
    return (ed - ref).days


IMPORTANT_MECHANIC_MAX_DAYS = 10


def important_status_from_date(event_date: str | None, ref_date=None) -> str | None:
    """Каждый день заново: красный сегодня и 1–3 дня, жёлтый 4–7, зелёный больше 7. Просрочка — красный."""
    days = important_days_until(event_date, ref_date)
    if days is None:
        return None
    if days <= 3:
        return "red"
    if days <= 7:
        return "yellow"
    return "green"


def compute_important_auto_status(row: dict, latest_mileage: int | None = None, ref_date=None) -> str:
    auto = important_status_from_date(row.get("event_date"), ref_date)
    if auto:
        return auto
    return _normalize_regulation_status(row.get("status"))


def annotate_important_proximity(item: dict, ref_date=None) -> dict:
    days = important_days_until(item.get("event_date"), ref_date)
    item["days_until"] = days
    item["is_overdue"] = days is not None and days < 0
    item["is_far"] = days is not None and days > IMPORTANT_MECHANIC_MAX_DAYS
    item["mechanic_visible"] = (not item["is_overdue"]) and (not item["is_far"])
    return item


def _important_status_code(item: dict) -> str:
    return _normalize_regulation_status(item.get("auto_status") or item.get("status"))


def _important_days_sort_value(item: dict) -> int:
    days = item.get("days_until")
    if days is None:
        return 10**9
    return int(days)


def sort_important_widget_items(items: list[dict]) -> list[dict]:
    """Красные — в начало прокрутки; между собой — ближе к дате завершения раньше."""
    reds = [item for item in items if _important_status_code(item) == "red"]
    others = [item for item in items if _important_status_code(item) != "red"]
    reds.sort(key=lambda item: (_important_days_sort_value(item), item.get("sort_order") or 0))
    others.sort(
        key=lambda item: (
            0 if _important_status_code(item) == "yellow" else 1,
            _important_days_sort_value(item),
            item.get("sort_order") or 0,
        )
    )
    return reds + others


def _apply_official_auto_status(item: dict) -> dict:
    item = dict(item)
    item["auto_status"] = compute_interval_auto_status(item.get("remaining_km"), item.get("interval_km"))
    item["status"] = item["auto_status"]
    return item


def build_official_regulation_live(
    db,
    fleet_vehicle_id: int | None,
    rows: list[dict],
    latest_mileage: int | None,
    *,
    min_rows: int = 2,
) -> list[dict]:
    baselines = get_schedule_baselines(db, int(fleet_vehicle_id)) if fleet_vehicle_id else {}
    live: list[dict] = []
    for row in rows:
        base = _build_official_widget_item(row, baselines, latest_mileage)
        base["title"] = row.get("title") or base.get("title") or ""
        base["comment"] = row.get("comment") or base.get("comment") or ""
        base["mileage_km"] = row.get("mileage_km")
        live.append(_apply_official_auto_status(base))
    while len(live) < min_rows:
        live.append(
            {
                "sort_order": len(live),
                "title": "",
                "comment": "",
                "mileage_km": None,
                "interval_km": None,
                "remaining_km": None,
                "baseline_km": None,
                "auto_status": "green",
                "status": "green",
                "is_new": True,
            }
        )
    return live


def build_important_info_live(
    rows: list[dict],
    latest_mileage: int | None,
    *,
    min_rows: int = 1,
) -> list[dict]:
    live: list[dict] = []
    for idx, row in enumerate(rows):
        item = {
            "sort_order": int(row.get("sort_order") if row.get("sort_order") is not None else idx),
            "title": row.get("title") or "",
            "comment": row.get("comment") or "",
            "event_date": row.get("event_date"),
        }
        item["auto_status"] = compute_important_auto_status(row, latest_mileage)
        item["status"] = item["auto_status"]
        annotate_important_proximity(item)
        live.append(item)
    while len(live) < min_rows:
        live.append(
            annotate_important_proximity(
                {
                    "sort_order": len(live),
                    "title": "",
                    "comment": "",
                    "event_date": None,
                    "auto_status": "green",
                    "status": "green",
                    "is_new": True,
                }
            )
        )
    return live


def _build_official_widget_item(
    item: dict,
    baselines: dict[int, int],
    latest_mileage: int | None,
) -> dict:
    sort_order = int(item.get("sort_order") or 0)
    interval = item.get("mileage_km")
    interval_int = int(interval) if interval not in (None, "") else None
    baseline = baselines.get(sort_order)
    if baseline is None and latest_mileage is not None:
        baseline = int(latest_mileage)
    remaining = None
    if interval_int and baseline is not None and latest_mileage is not None:
        remaining = _interval_remaining_km(baseline, interval_int, int(latest_mileage))
    result = {
        "sort_order": sort_order,
        "title": item.get("title") or "Регламент",
        "comment": item.get("comment") or "",
        "interval_km": interval_int,
        "remaining_km": remaining,
        "baseline_km": baseline,
    }
    return _apply_official_auto_status(result)


def build_regulation_widget(
    db,
    fleet_vehicle_id: int | None,
    latest_mileage: int | None,
) -> dict:
    if not fleet_vehicle_id:
        return {"official_items": [], "important_items": []}
    schedule = list_fleet_vehicle_schedule(db, int(fleet_vehicle_id))
    important_rows = list_fleet_vehicle_important_info(db, int(fleet_vehicle_id))
    baselines = get_schedule_baselines(db, int(fleet_vehicle_id))

    official_items = [_build_official_widget_item(item, baselines, latest_mileage) for item in schedule]
    if official_items and latest_mileage is not None:
        official_items.sort(
            key=lambda o: (
                o["remaining_km"] if o["remaining_km"] is not None else 10**9,
                o["sort_order"],
            )
        )

    important_items = []
    for idx, row in enumerate(important_rows):
        item = {
            "title": row.get("title") or "",
            "comment": row.get("comment") or "",
            "event_date": row.get("event_date"),
            "sort_order": int(row.get("sort_order") if row.get("sort_order") is not None else idx),
        }
        item["auto_status"] = compute_important_auto_status(row, latest_mileage)
        item["status"] = item["auto_status"]
        annotate_important_proximity(item)
        important_items.append(item)

    important_items = sort_important_widget_items(important_items)

    return {
        "official_items": official_items,
        "important_items": important_items,
    }


def build_regulation_widget_preview(maintenance_brief: dict | None) -> dict:
    brief = maintenance_brief or {}
    recs = brief.get("recommendations") or []
    official_items = []
    if recs:
        rec = recs[0]
        official_items.append(
            {
                "sort_order": 0,
                "title": rec.get("title") or "Регламент",
                "comment": rec.get("works") or "",
                "auto_status": "yellow",
                "status": "yellow",
                "interval_km": 10000,
                "remaining_km": rec.get("remaining_km"),
                "baseline_km": None,
            }
        )
    return {
        "official_items": official_items,
        "important_items": [
            {
                "title": "Важная информация",
                "comment": "Демо-запись для вёрстки",
                "auto_status": "yellow",
                "status": "yellow",
                "event_date": None,
                "mileage_km": None,
                "sort_order": 0,
                "is_overdue": False,
                "is_far": False,
                "mechanic_visible": True,
                "days_until": None,
            }
        ]
        if official_items
        else [],
    }


def fleet_fuel_type_label(code: str | None) -> str:
    for item_code, label in FLEET_FUEL_TYPE_CHOICES:
        if item_code == code:
            return label
    return (code or "").strip() or "—"


def _parse_mileage_km(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        text = str(value)
        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            return None
        try:
            return int(digits)
        except ValueError:
            return None


def list_fleet_vehicles_for_assign(db, user_id: int | None = None) -> list[dict]:
    """Свободные машины автопарка + машина, уже закреплённая за user_id."""
    uid = int(user_id) if user_id else None
    out: list[dict] = []
    seen: set[int] = set()
    current_vehicle = get_fleet_vehicle_for_user(db, uid) if uid is not None else None
    current_id = int(current_vehicle["id"]) if current_vehicle else None

    for item in list_fleet_vehicles(db, only_unassigned=False):
        vid = int(item["id"])
        assigned = item.get("assigned_user_id")
        if assigned is None:
            out.append(item)
            seen.add(vid)
        elif uid is not None and int(assigned) == uid:
            out.append(item)
            seen.add(vid)

    if current_id is not None and current_id not in seen:
        extra = get_fleet_vehicle(db, current_id)
        if extra:
            extra = dict(extra)
            extra["display_label"] = f"{extra.get('name', '')} — {extra.get('plate_number', '')}"
            out.insert(0, extra)
            seen.add(current_id)

    return out


def list_fleet_vehicles(db, *, only_unassigned: bool = False, include_inactive: bool = False) -> list[dict]:
    cur = db.cursor()
    sql = """
        SELECT fv.*,
               u.last_name AS assignee_last_name,
               u.first_name AS assignee_first_name,
               u.middle_name AS assignee_middle_name
        FROM fleet_vehicles fv
        LEFT JOIN users u ON u.id = fv.assigned_user_id
        WHERE 1=1
    """
    params: list = []
    if not include_inactive:
        sql += " AND fv.is_active = 1"
    if only_unassigned:
        sql += " AND fv.assigned_user_id IS NULL"
    sql += " ORDER BY fv.name COLLATE NOCASE, fv.plate_number"
    cur.execute(sql, params)
    out = []
    for row in cur.fetchall():
        item = dict(row)
        item["display_label"] = f"{item['name']} — {item['plate_number']}"
        item["is_branded"] = bool(item.get("is_branded"))
        item["has_field_service"] = bool(item.get("has_field_service"))
        item["fuel_type_label"] = fleet_fuel_type_label(item.get("fuel_type"))
        out.append(item)
    return out


def get_fleet_vehicle(db, vehicle_id: int) -> dict | None:
    cur = db.cursor()
    cur.execute("SELECT * FROM fleet_vehicles WHERE id = ?", (int(vehicle_id),))
    row = cur.fetchone()
    if not row:
        return None
    item = dict(row)
    item["is_branded"] = bool(item.get("is_branded"))
    item["has_field_service"] = bool(item.get("has_field_service"))
    item["fuel_type_label"] = fleet_fuel_type_label(item.get("fuel_type"))
    item["schedule"] = list_fleet_vehicle_schedule(db, vehicle_id)
    item["important_info"] = list_fleet_vehicle_important_info(db, vehicle_id)
    item["photos"] = list_fleet_vehicle_photos(db, vehicle_id)
    item["branding_items"] = list_fleet_vehicle_branding_items(db, vehicle_id)
    item["van_equipment"] = list_fleet_vehicle_van_equipment(db, vehicle_id)
    return item


def list_fleet_vehicle_photos(db, vehicle_id: int) -> list[dict]:
    cur = db.cursor()
    cur.execute(
        """
        SELECT slot, file_name, created_at
        FROM fleet_vehicle_photos
        WHERE fleet_vehicle_id = ?
        ORDER BY slot
        """,
        (int(vehicle_id),),
    )
    return [dict(row) for row in cur.fetchall()]


def fleet_photos_by_slot_map(photos: list[dict] | None) -> dict[int, str]:
    """Слот → имя файла. Один источник для карточки сотрудника, админки и механика."""
    out: dict[int, str] = {}
    for photo in photos or []:
        try:
            slot = int(photo["slot"])
        except (KeyError, TypeError, ValueError):
            continue
        name = (photo.get("file_name") or "").strip()
        if name:
            out[slot] = name
    return out


def fleet_photos_ordered_files(photos_by_slot: dict[int, str] | None) -> list[str]:
    """Имена файлов слотов 1→4 — порядок «по часовой» для прокрутки 360°."""
    mapping = photos_by_slot or {}
    return [mapping[slot] for slot in range(1, FLEET_PHOTO_SLOTS + 1) if mapping.get(slot)]


def normalize_fleet_vehicle_jpeg(file_storage) -> bytes:
    """Проверка и сохранение JPG 1000×1000 @ 72 dpi."""
    if not file_storage or not getattr(file_storage, "filename", None):
        raise ValueError("Файл не выбран")
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in (".jpg", ".jpeg"):
        raise ValueError("Допустим только формат JPG")
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError("Обработка фото недоступна (Pillow не установлен)") from exc

    file_storage.stream.seek(0)
    img = Image.open(file_storage.stream)
    if (img.format or "").upper() not in ("JPEG", "JPG"):
        raise ValueError("Допустим только формат JPG")
    width, height = img.size
    if width != FLEET_PHOTO_PX or height != FLEET_PHOTO_PX:
        raise ValueError(f"Фото должно быть {FLEET_PHOTO_PX}×{FLEET_PHOTO_PX} px")
    img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90, dpi=(FLEET_PHOTO_DPI, FLEET_PHOTO_DPI))
    return buf.getvalue()


def save_fleet_vehicle_photos(db, vehicle_id: int, slot_files: dict[int, Any], *, upload_dir: str) -> None:
    if not get_fleet_vehicle(db, vehicle_id):
        raise ValueError("Машина не найдена в автопарке")
    os.makedirs(upload_dir, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.cursor()
    for slot in sorted(slot_files.keys()):
        slot_num = int(slot)
        if slot_num < 1 or slot_num > FLEET_PHOTO_SLOTS:
            raise ValueError("Недопустимый номер фото")
        file_storage = slot_files[slot_num]
        if not file_storage or not getattr(file_storage, "filename", None):
            continue
        jpeg = normalize_fleet_vehicle_jpeg(file_storage)
        file_name = f"fleet_{int(vehicle_id)}_{slot_num}.jpg"
        path = os.path.join(upload_dir, file_name)
        with open(path, "wb") as out:
            out.write(jpeg)
        cur.execute(
            "DELETE FROM fleet_vehicle_photos WHERE fleet_vehicle_id = ? AND slot = ?",
            (int(vehicle_id), slot_num),
        )
        cur.execute(
            """
            INSERT INTO fleet_vehicle_photos (fleet_vehicle_id, slot, file_name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (int(vehicle_id), slot_num, file_name, now),
        )
    db.commit()


def delete_fleet_vehicle_photos(db, vehicle_id: int, slots: list[int], *, upload_dir: str) -> None:
    """Удалить фото указанных слотов из БД и с диска."""
    if not slots:
        return
    if not get_fleet_vehicle(db, vehicle_id):
        raise ValueError("Машина не найдена в автопарке")
    cur = db.cursor()
    for slot in slots:
        slot_num = int(slot)
        if slot_num < 1 or slot_num > FLEET_PHOTO_SLOTS:
            continue
        cur.execute(
            "SELECT file_name FROM fleet_vehicle_photos WHERE fleet_vehicle_id = ? AND slot = ?",
            (int(vehicle_id), slot_num),
        )
        row = cur.fetchone()
        cur.execute(
            "DELETE FROM fleet_vehicle_photos WHERE fleet_vehicle_id = ? AND slot = ?",
            (int(vehicle_id), slot_num),
        )
        file_name = ""
        if row is not None:
            try:
                file_name = str(row["file_name"] or "")
            except (KeyError, IndexError, TypeError):
                file_name = str(row[0] or "")
        if file_name and upload_dir:
            path = os.path.join(upload_dir, file_name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
    db.commit()


def list_fleet_vehicle_schedule(db, vehicle_id: int) -> list[dict]:
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, title, mileage_mark, mileage_km, comment, sort_order, status
        FROM fleet_vehicle_schedule
        WHERE fleet_vehicle_id = ?
        ORDER BY sort_order ASC, id ASC
        """,
        (int(vehicle_id),),
    )
    return [dict(r) for r in cur.fetchall()]


def list_fleet_vehicle_important_info(db, vehicle_id: int) -> list[dict]:
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, event_date, mileage_km, title, comment, sort_order, status
        FROM fleet_vehicle_important_info
        WHERE fleet_vehicle_id = ?
        ORDER BY sort_order ASC, id ASC
        """,
        (int(vehicle_id),),
    )
    return [dict(r) for r in cur.fetchall()]


def _replace_fleet_important_info(db, vehicle_id: int, items: list[dict], *, commit: bool = True) -> None:
    cur = db.cursor()
    cur.execute("DELETE FROM fleet_vehicle_important_info WHERE fleet_vehicle_id = ?", (int(vehicle_id),))
    for idx, raw in enumerate(items):
        title = (raw.get("title") or "").strip()
        comment = (raw.get("comment") or "").strip()
        event_date = (raw.get("event_date") or "").strip()
        mileage_km = _parse_mileage_km(raw.get("mileage_km"))
        status = _normalize_regulation_status(raw.get("status"))
        if not title and not comment and not event_date and mileage_km is None:
            continue
        if not title:
            title = f"Запись {idx + 1}"
        cur.execute(
            """
            INSERT INTO fleet_vehicle_important_info (
                fleet_vehicle_id, event_date, mileage_km, title, comment, sort_order, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(vehicle_id),
                event_date or None,
                mileage_km,
                title,
                comment or None,
                int(raw.get("sort_order", idx)),
                status,
            ),
        )
    if commit:
        db.commit()


def update_fleet_vehicle_important_info(db, vehicle_id: int, items: list[dict]) -> None:
    if not get_fleet_vehicle(db, vehicle_id):
        raise ValueError("Машина не найдена в автопарке")
    _replace_fleet_important_info(db, vehicle_id, items or [])


def create_fleet_vehicle(
    db,
    *,
    name: str,
    plate_number: str,
    color: str = "",
    is_branded: bool = False,
    has_field_service: bool = False,
    initial_mileage: int = 0,
    fuel_type: str = "",
    schedule_items: list[dict] | None = None,
) -> int:
    name = (name or "").strip()
    plate_number = normalize_plate(plate_number)
    color = (color or "").strip()
    initial_mileage = max(0, int(initial_mileage or 0))
    fuel_type = normalize_fleet_fuel_type(fuel_type)
    if not name:
        raise ValueError("Укажите название машины")
    if not plate_number:
        raise ValueError("Укажите госномер")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.cursor()
    try:
        cur.execute(
            """
            INSERT INTO fleet_vehicles (
                name, plate_number, color, is_branded, has_field_service,
                initial_mileage, fuel_type, assigned_user_id, assigned_at, is_active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 1, ?)
            """,
            (
                name,
                plate_number,
                color or None,
                1 if is_branded else 0,
                1 if has_field_service else 0,
                initial_mileage,
                fuel_type,
                now,
            ),
        )
    except Exception as exc:
        raise ValueError("Машина с таким госномером уже есть в автопарке") from exc
    vehicle_id = int(cur.lastrowid)
    _replace_fleet_schedule(db, vehicle_id, schedule_items or [], commit=False)
    db.commit()
    return vehicle_id


def update_fleet_vehicle(
    db,
    vehicle_id: int,
    *,
    name: str,
    plate_number: str,
    color: str = "",
    is_branded: bool = False,
    has_field_service: bool = False,
    initial_mileage: int = 0,
    fuel_type: str = "",
) -> None:
    vehicle = get_fleet_vehicle(db, vehicle_id)
    if not vehicle:
        raise ValueError("Машина не найдена в автопарке")
    name = (name or "").strip()
    plate_number = normalize_plate(plate_number)
    color = (color or "").strip()
    initial_mileage = max(0, int(initial_mileage or 0))
    fuel_type = normalize_fleet_fuel_type(fuel_type)
    if not name:
        raise ValueError("Укажите название машины")
    if not plate_number:
        raise ValueError("Укажите госномер")
    cur = db.cursor()
    try:
        cur.execute(
            """
            UPDATE fleet_vehicles
            SET name = ?, plate_number = ?, color = ?, is_branded = ?,
                has_field_service = ?, initial_mileage = ?, fuel_type = ?
            WHERE id = ?
            """,
            (
                name,
                plate_number,
                color or None,
                1 if is_branded else 0,
                1 if has_field_service else 0,
                initial_mileage,
                fuel_type,
                int(vehicle_id),
            ),
        )
    except Exception as exc:
        raise ValueError("Машина с таким госномером уже есть в автопарке") from exc
    assigned_user_id = vehicle.get("assigned_user_id")
    if assigned_user_id:
        cur.execute(
            """
            UPDATE mechanic_vehicles
            SET plate_number = ?
            WHERE user_id = ? AND fleet_vehicle_id = ?
            """,
            (plate_number, int(assigned_user_id), int(vehicle_id)),
        )
    db.commit()


def delete_fleet_vehicle(db, vehicle_id: int, *, upload_dir: str | None = None) -> None:
    vehicle = get_fleet_vehicle(db, vehicle_id)
    if not vehicle:
        raise ValueError("Машина не найдена в автопарке")
    if vehicle.get("assigned_user_id"):
        raise ValueError("Нельзя удалить машину: она закреплена за сотрудником")
    photos = list_fleet_vehicle_photos(db, vehicle_id)
    cur = db.cursor()
    cur.execute("DELETE FROM fleet_vehicles WHERE id = ?", (int(vehicle_id),))
    db.commit()
    if upload_dir:
        for photo in photos:
            path = os.path.join(upload_dir, photo["file_name"])
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


def clear_fleet_vehicle_for_user(db, user_id: int) -> None:
    cur = db.cursor()
    cur.execute(
        """
        UPDATE fleet_vehicles
        SET assigned_user_id = NULL, assigned_at = NULL
        WHERE assigned_user_id = ?
        """,
        (int(user_id),),
    )
    cur.execute("DELETE FROM mechanic_vehicles WHERE user_id = ?", (int(user_id),))
    db.commit()


def _replace_fleet_schedule(db, vehicle_id: int, items: list[dict], *, commit: bool = True) -> None:
    cur = db.cursor()
    cur.execute("DELETE FROM fleet_vehicle_schedule WHERE fleet_vehicle_id = ?", (int(vehicle_id),))
    sort_orders: list[int] = []
    for idx, raw in enumerate(items):
        title = (raw.get("title") or "").strip()
        mileage_mark = (raw.get("mileage_mark") or "").strip()
        comment = (raw.get("comment") or "").strip()
        mileage_km = _parse_mileage_km(raw.get("mileage_km"))
        if mileage_km is None:
            mileage_km = _parse_mileage_km(mileage_mark)
        if not title and not mileage_mark and not comment and mileage_km is None:
            continue
        if not title:
            title = f"Регламент {idx + 1}"
        if mileage_km is not None:
            mileage_mark = f"каждые {int(mileage_km):,} км".replace(",", " ")
        elif not mileage_mark:
            mileage_mark = "—"
        status = _normalize_regulation_status(raw.get("status"))
        sort_order = int(raw.get("sort_order", idx))
        sort_orders.append(sort_order)
        cur.execute(
            """
            INSERT INTO fleet_vehicle_schedule (
                fleet_vehicle_id, title, mileage_mark, mileage_km, comment, sort_order, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(vehicle_id),
                title,
                mileage_mark,
                mileage_km,
                comment or None,
                sort_order,
                status,
            ),
        )
    sync_schedule_baselines(db, vehicle_id, sort_orders, commit=False)
    if commit:
        db.commit()


def update_fleet_vehicle_schedule(db, vehicle_id: int, schedule_items: list[dict]) -> None:
    if not get_fleet_vehicle(db, vehicle_id):
        raise ValueError("Машина не найдена в автопарке")
    _replace_fleet_schedule(db, vehicle_id, schedule_items or [])


def assign_fleet_vehicle_to_user(db, user_id: int, fleet_vehicle_id: int) -> dict:
    """Привязать машину автопарка к механику (1 механик — 1 машина)."""
    vehicle = get_fleet_vehicle(db, fleet_vehicle_id)
    if not vehicle:
        raise ValueError("Машина не найдена в автопарке")
    if vehicle.get("assigned_user_id") and int(vehicle["assigned_user_id"]) != int(user_id):
        raise ValueError("Машина уже закреплена за другим сотрудником")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.cursor()
    # Снять прошлую машину с этого механика
    cur.execute(
        """
        UPDATE fleet_vehicles
        SET assigned_user_id = NULL, assigned_at = NULL
        WHERE assigned_user_id = ? AND id != ?
        """,
        (int(user_id), int(fleet_vehicle_id)),
    )
    cur.execute(
        """
        UPDATE fleet_vehicles
        SET assigned_user_id = ?, assigned_at = ?
        WHERE id = ?
        """,
        (int(user_id), now, int(fleet_vehicle_id)),
    )
    # Совместимость со старой схемой mechanic_vehicles
    cur.execute(
        """
        INSERT INTO mechanic_vehicles (user_id, model_code, plate_number, assigned_at, fleet_vehicle_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            model_code = excluded.model_code,
            plate_number = excluded.plate_number,
            assigned_at = excluded.assigned_at,
            fleet_vehicle_id = excluded.fleet_vehicle_id
        """,
        (int(user_id), "fleet_custom", vehicle["plate_number"], now, int(fleet_vehicle_id)),
    )
    # Стартовый пробег, если ещё нет записей
    latest = get_latest_mileage(db, user_id)
    if latest is None and int(vehicle.get("initial_mileage") or 0) > 0:
        cur.execute(
            """
            INSERT INTO vehicle_mileage_logs (user_id, mileage_km, logged_at, note)
            VALUES (?, ?, ?, ?)
            """,
            (
                int(user_id),
                int(vehicle["initial_mileage"]),
                now,
                "Стартовый пробег при постановке в автопарк",
            ),
        )
    schedule = list_fleet_vehicle_schedule(db, int(fleet_vehicle_id))
    if schedule:
        baseline_km = get_latest_mileage(db, user_id)
        if baseline_km is None:
            baseline_km = int(vehicle.get("initial_mileage") or 0) or None
        if baseline_km is not None:
            sort_orders = [int(s.get("sort_order", i)) for i, s in enumerate(schedule)]
            existing = get_schedule_baselines(db, int(fleet_vehicle_id))
            for so in sort_orders:
                if so not in existing:
                    existing[so] = int(baseline_km)
            cur = db.cursor()
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for so, km in existing.items():
                if so in sort_orders:
                    cur.execute(
                        """
                        INSERT INTO fleet_vehicle_schedule_baseline (
                            fleet_vehicle_id, sort_order, baseline_mileage_km, updated_at
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(fleet_vehicle_id, sort_order) DO NOTHING
                        """,
                        (int(fleet_vehicle_id), int(so), int(km), ts),
                    )
    db.commit()
    return get_fleet_vehicle(db, fleet_vehicle_id) or vehicle


def get_fleet_vehicle_for_user(db, user_id: int) -> dict | None:
    cur = db.cursor()
    cur.execute(
        """
        SELECT id FROM fleet_vehicles
        WHERE assigned_user_id = ? AND is_active = 1
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(user_id),),
    )
    row = cur.fetchone()
    if not row:
        cur.execute(
            "SELECT fleet_vehicle_id FROM mechanic_vehicles WHERE user_id = ?",
            (int(user_id),),
        )
        link = cur.fetchone()
        if not link or not link[0]:
            return None
        return get_fleet_vehicle(db, int(link[0]))
    return get_fleet_vehicle(db, int(row[0]))


def build_regulation_slides(maintenance_brief: dict | None) -> list[list[dict]]:
    """Две карточки регламента на слайд для виджета автопарка."""
    brief = maintenance_brief or {}
    items: list[dict] = []
    for rec in brief.get("recommendations") or []:
        items.append(
            {
                "kind": rec.get("kind") or "soon",
                "title": rec.get("title") or "Регламент",
                "works": rec.get("works") or "",
                "remaining_km": rec.get("remaining_km"),
                "target_km": rec.get("target_km"),
            }
        )
    oil_next = brief.get("oil_next_km")
    oil_rem = brief.get("oil_remaining_km")
    if oil_next is not None:
        has_oil = any("масл" in (item.get("title") or "").lower() for item in items)
        if not has_oil:
            if oil_rem is not None and oil_rem <= 0:
                oil_kind = "due"
            elif oil_rem is not None and oil_rem <= 3000:
                oil_kind = "soon"
            else:
                oil_kind = "milestone"
            works = "Плановая замена масла и фильтра"
            if oil_rem is not None:
                works = f"Осталось {oil_rem:,} км".replace(",", " ")
            items.append(
                {
                    "kind": oil_kind,
                    "title": f"Замена масла: {oil_next:,} км".replace(",", " "),
                    "works": works,
                    "remaining_km": oil_rem,
                    "target_km": oil_next,
                }
            )
    if not items:
        return [[]]
    slides: list[list[dict]] = []
    for idx in range(0, len(items), 2):
        slides.append(items[idx : idx + 2])
    return slides


def build_fleet_schedule_brief(latest_mileage: int | None, schedule: list[dict]) -> dict:
    """Рекомендации по пользовательскому регламенту автопарка."""
    if latest_mileage is None:
        return {
            "mileage": None,
            "recommendations": [],
            "oil_next_km": None,
            "oil_remaining_km": None,
            "last_oil_km": None,
            "schedule": schedule or [],
        }
    recommendations = []
    next_item = None
    next_km = None
    for item in schedule or []:
        km = item.get("mileage_km")
        if km is None:
            continue
        km = int(km)
        title = item.get("title") or "Регламент"
        mark = item.get("mileage_mark") or f"{km} км"
        comment = item.get("comment") or ""
        remaining = km - int(latest_mileage)
        if remaining <= 3000:
            kind = "due" if remaining <= 0 else "soon"
            recommendations.append(
                {
                    "kind": kind,
                    "title": f"{title}: {mark}",
                    "works": comment or f"Плановое обслуживание при пробеге {mark}",
                    "target_km": km,
                    "remaining_km": remaining,
                }
            )
        if remaining > 0 and (next_km is None or km < next_km):
            next_km = km
            next_item = item
    recommendations.sort(key=lambda r: (0 if r["kind"] == "due" else 1, r.get("remaining_km", 0)))
    oil_next = next_km
    oil_remaining = (next_km - int(latest_mileage)) if next_km is not None else None
    return {
        "mileage": latest_mileage,
        "recommendations": recommendations[:4],
        "oil_next_km": oil_next,
        "oil_remaining_km": oil_remaining,
        "last_oil_km": None,
        "schedule": schedule or [],
        "next_title": (next_item or {}).get("title"),
    }


def get_fleet_period_config(get_setting_fn: Callable[[str, str], str] | None = None) -> dict:
    day_from, day_to = 1, 0
    if get_setting_fn:
        try:
            day_from = int(get_setting_fn("fleet_period_day_from", "1") or 1)
        except (TypeError, ValueError):
            day_from = 1
        try:
            day_to = int(get_setting_fn("fleet_period_day_to", "0") or 0)
        except (TypeError, ValueError):
            day_to = 0
    return {
        "day_from": max(1, min(31, day_from)),
        "day_to": max(0, min(31, day_to)),
    }


def _period_last_day(year: int, month: int, day_to: int) -> int:
    month_last = calendar.monthrange(year, month)[1]
    if day_to <= 0:
        return month_last
    return min(day_to, month_last)


def format_period_label(year: int, month: int, day_from: int, day_to: int) -> str:
    last = _period_last_day(year, month, day_to)
    month_name = _MONTHS_GENITIVE[month - 1]
    if day_from == 1 and day_to <= 0:
        return f"1–{last} {month_name}"
    if day_from == 1:
        return f"1–{last} {month_name}"
    return f"{day_from}–{last} {month_name}"


def format_mileage_km(km: int | None) -> str | None:
    if km is None:
        return None
    return f"{int(km):,}".replace(",", " ")


def _format_russian_ordinal_day(day: int) -> str:
    return f"{int(day)}-е"


def get_previous_mileage(db, user_id: int) -> int | None:
    cur = db.cursor()
    cur.execute(
        """
        SELECT mileage_km FROM vehicle_mileage_logs
        WHERE user_id = ?
        ORDER BY mileage_km DESC, datetime(logged_at) DESC, id DESC
        LIMIT 1 OFFSET 1
        """,
        (int(user_id),),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def get_mileage_log_on_date(db, user_id: int, iso_date: str) -> dict | None:
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, mileage_km, logged_at, note FROM vehicle_mileage_logs
        WHERE user_id = ? AND substr(logged_at, 1, 10) = ?
        ORDER BY datetime(logged_at) DESC, id DESC
        LIMIT 1
        """,
        (int(user_id), (iso_date or "").strip()[:10]),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _get_current_mileage_excluding_date(db, user_id: int, iso_date: str) -> int | None:
    cur = db.cursor()
    cur.execute(
        """
        SELECT MAX(mileage_km) FROM vehicle_mileage_logs
        WHERE user_id = ? AND substr(logged_at, 1, 10) != ?
        """,
        (int(user_id), (iso_date or "").strip()[:10]),
    )
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _upsert_mileage_log_on_date(db, user_id: int, iso_date: str, mileage_km: int) -> None:
    iso_date = (iso_date or "").strip()[:10]
    today_iso = date.today().isoformat()
    if iso_date == today_iso:
        logged_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        logged_at = f"{iso_date} 12:00:00"
    existing = get_mileage_log_on_date(db, user_id, iso_date)
    cur = db.cursor()
    if existing:
        cur.execute(
            """
            UPDATE vehicle_mileage_logs
            SET mileage_km = ?, logged_at = ?
            WHERE id = ?
            """,
            (int(mileage_km), logged_at, int(existing["id"])),
        )
    else:
        cur.execute(
            """
            INSERT INTO vehicle_mileage_logs (user_id, mileage_km, logged_at, note)
            VALUES (?, ?, ?, NULL)
            """,
            (int(user_id), int(mileage_km), logged_at),
        )


def save_period_boundary_mileage(
    db,
    user_id: int,
    boundary: str,
    mileage_km: int,
    get_setting_fn: Callable[[str, str], str] | None = None,
) -> dict:
    boundary = (boundary or "").strip().lower()
    if boundary not in ("period_start", "period_end"):
        raise ValueError("Некорректная отметка периода")

    mileage_km = int(mileage_km)
    if mileage_km < 0:
        raise ValueError("Пробег не может быть отрицательным")

    informer = build_mileage_informer(db, user_id, get_setting_fn)
    if boundary == "period_start":
        target = informer["period_start"]
    else:
        target = informer["period_end"]

    if not target.get("active"):
        raise ValueError("Эта отметка периода пока недоступна")

    target_date = target["date"]
    day_label = target["day_label"]
    start_info = informer["period_start"]
    end_info = informer["period_end"]

    if boundary == "period_end" and not start_info.get("fixed"):
        raise ValueError("Сначала зафиксируйте пробег на начало периода")

    if boundary == "period_end" and start_info.get("fixed"):
        start_km = int(start_info["km"])
        if mileage_km < start_km:
            raise ValueError(
                "Пробег на конец периода не может быть меньше пробега на начало "
                f"({format_mileage_km(start_km)} км)"
            )

    if boundary == "period_start" and end_info.get("fixed"):
        end_km = int(end_info["km"])
        if mileage_km > end_km:
            raise ValueError(
                "Пробег на начало периода не может быть больше уже зафиксированного "
                f"пробега на конец ({format_mileage_km(end_km)} км)"
            )

    baseline = _get_current_mileage_excluding_date(db, user_id, target_date)
    if baseline is not None and mileage_km < baseline:
        raise ValueError(
            f"Пробег не может быть меньше текущего ({format_mileage_km(baseline)} км)"
        )

    _upsert_mileage_log_on_date(db, user_id, target_date, mileage_km)
    db.commit()
    return {"boundary": boundary, "day_label": day_label}


def reset_period_boundary_mileage(
    db,
    user_id: int,
    get_setting_fn: Callable[[str, str], str] | None = None,
) -> dict:
    """Удаляет отметки пробега на начало и конец текущего отчётного периода."""
    informer = build_mileage_informer(db, user_id, get_setting_fn)
    if not informer.get("period_closed"):
        raise ValueError("Период ещё не закрыт — сбрасывать нечего")

    start_date = informer["period_start"]["date"]
    end_date = informer["period_end"]["date"]
    cur = db.cursor()
    for iso_date in (start_date, end_date):
        cur.execute(
            """
            DELETE FROM vehicle_mileage_logs
            WHERE user_id = ? AND substr(logged_at, 1, 10) = ?
            """,
            (int(user_id), iso_date),
        )
    db.commit()
    return {"start_date": start_date, "end_date": end_date}


def build_mileage_informer(
    db,
    user_id: int,
    get_setting_fn: Callable[[str, str], str] | None = None,
    ref_date: datetime | None = None,
) -> dict:
    """Сводка зафиксированного пробега для админского информера."""
    ref = ref_date or datetime.now()
    today_iso = ref.strftime("%Y-%m-%d")
    period_config = get_fleet_period_config(get_setting_fn)
    day_from = int(period_config.get("day_from") or 1)
    day_to = int(period_config.get("day_to") or 0)
    year, month = ref.year, ref.month
    end_day = _period_last_day(year, month, day_to)
    start_day = min(day_from, end_day)
    start_date = f"{year:04d}-{month:02d}-{start_day:02d}"
    end_date = f"{year:04d}-{month:02d}-{end_day:02d}"

    current = get_latest_mileage(db, user_id)
    previous = get_previous_mileage(db, user_id)
    start_log = get_mileage_log_on_date(db, user_id, start_date)
    end_log = get_mileage_log_on_date(db, user_id, end_date)

    def _boundary(
        log: dict | None,
        day_num: int,
        date_iso: str,
        *,
        active: bool,
        always_editable: bool = False,
    ) -> dict:
        fixed = log is not None
        km = int(log["mileage_km"]) if log else None
        return {
            "day": day_num,
            "day_label": _format_russian_ordinal_day(day_num),
            "date": date_iso,
            "date_display": f"{date_iso[8:10]}.{date_iso[5:7]}.{date_iso[0:4]}",
            "fixed": fixed,
            "km": km,
            "km_display": format_mileage_km(km),
            "active": active or always_editable,
            "pending": (active or always_editable) and not fixed,
        }

    period_start = _boundary(
        start_log, start_day, start_date, active=today_iso >= start_date
    )
    period_end = _boundary(
        end_log, end_day, end_date, active=today_iso >= end_date, always_editable=True
    )

    return {
        "current_km": current,
        "current_km_display": format_mileage_km(current),
        "previous_km": previous,
        "previous_km_display": format_mileage_km(previous),
        "period_label": format_period_label(year, month, day_from, day_to),
        "period_start": period_start,
        "period_end": period_end,
        "period_closed": start_log is not None and end_log is not None,
        "period_closed_message": (
            f"Период с {period_start['day_label']} по {period_end['day_label']} "
            f"({format_period_label(year, month, day_from, day_to)}) закрыт."
        ),
    }


def add_oil_change_log(db, user_id: int, mileage_km: int, note: str = "") -> None:
    mileage_km = int(mileage_km)
    if mileage_km < 0:
        raise ValueError("Пробег не может быть отрицательным")
    latest = get_latest_mileage(db, user_id)
    if latest is not None and mileage_km > latest:
        raise ValueError(f"Пробег замены масла не может превышать текущий ({latest} км)")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO vehicle_oil_logs (user_id, mileage_km, logged_at, note)
        VALUES (?, ?, ?, ?)
        """,
        (int(user_id), mileage_km, now, (note or "").strip() or None),
    )
    db.commit()


def get_last_oil_change_mileage(db, user_id: int) -> int | None:
    cur = db.cursor()
    cur.execute(
        """
        SELECT mileage_km FROM vehicle_oil_logs
        WHERE user_id = ?
        ORDER BY mileage_km DESC, datetime(logged_at) DESC, id DESC
        LIMIT 1
        """,
        (int(user_id),),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def list_oil_logs(db, user_id: int, limit: int = 12) -> list:
    cur = db.cursor()
    cur.execute(
        """
        SELECT * FROM vehicle_oil_logs
        WHERE user_id = ?
        ORDER BY datetime(logged_at) DESC, id DESC
        LIMIT ?
        """,
        (int(user_id), int(limit)),
    )
    return cur.fetchall()


def _find_schedule_row(schedule: list[dict], *, title_part: str = "", mileage_part: str = "") -> dict | None:
    for row in schedule:
        title = row.get("title") or ""
        mileage = row.get("mileage") or ""
        if title_part and title_part in title:
            return row
        if mileage_part and mileage_part in mileage:
            return row
    return None


def build_maintenance_brief(
    latest_mileage: int | None,
    model_code: str,
    last_oil_km: int | None = None,
) -> dict:
    if latest_mileage is None:
        return {
            "mileage": None,
            "recommendations": [],
            "oil_next_km": None,
            "oil_remaining_km": None,
            "last_oil_km": last_oil_km,
        }

    model = VEHICLE_MODELS.get(model_code or "", {})
    maint = model.get("maintenance") or {}
    schedule = maint.get("schedule") or []
    oil_int = int(maint.get("recommended_oil_km") or 10000)
    official_int = int(maint.get("official_interval_km") or 20000)

    if last_oil_km is not None:
        next_oil = last_oil_km + oil_int
    elif latest_mileage % oil_int == 0:
        next_oil = latest_mileage + oil_int
    else:
        next_oil = ((latest_mileage // oil_int) + 1) * oil_int
    oil_remaining = max(0, next_oil - latest_mileage)

    recommendations: list[dict] = []
    seen_titles: set[str] = set()

    def add_rec(row: dict | None, *, at_km: int | None = None, kind: str = "info") -> None:
        if not row:
            return
        title = row.get("title") or "ТО"
        if title in seen_titles:
            return
        seen_titles.add(title)
        recommendations.append(
            {
                "title": title,
                "works": row.get("works") or "",
                "at_km": at_km,
                "kind": kind,
            }
        )

    row_oil = _find_schedule_row(schedule, title_part="Рекомендованное") or _find_schedule_row(
        schedule, mileage_part="10 000"
    ) or _find_schedule_row(schedule, title_part="масла")
    if latest_mileage >= oil_int:
        add_rec(row_oil, at_km=(latest_mileage // oil_int) * oil_int or oil_int, kind="due")

    if latest_mileage >= official_int:
        row_official = _find_schedule_row(schedule, title_part="Официальное")
        add_rec(
            row_official,
            at_km=(latest_mileage // official_int) * official_int,
            kind="official",
        )

    for milestone in maint.get("service_milestones_km") or []:
        m = int(milestone)
        if m in (oil_int, official_int):
            continue
        if latest_mileage < m - 3000:
            continue
        token = str(m // 1000)
        row_m = None
        for row in schedule:
            mileage_label = (row.get("mileage") or "").replace(" ", "")
            if str(m) in mileage_label or f"{token}000" in mileage_label:
                row_m = row
                break
        if row_m:
            add_rec(row_m, at_km=m, kind="milestone")

    return {
        "mileage": latest_mileage,
        "recommendations": recommendations,
        "oil_next_km": next_oil,
        "oil_remaining_km": oil_remaining,
        "last_oil_km": last_oil_km,
    }


def build_fuel_brief(
    db,
    user_id: int,
    model_code: str,
    fuel_price_rub_l: float,
    snapshot: dict,
) -> dict:
    model = VEHICLE_MODELS.get(model_code or "", {})
    fuel_ref = model.get("fuel_reference") or {}
    adaptive = get_adaptive_consumption(db, user_id, fuel_price_rub_l)
    factory = model.get("factory_l100", 10.0)
    month = datetime.now().month
    is_winter = month in WINTER_MONTHS
    winter_factory = round(factory * model.get("winter_factor", 1.1), 1) if is_winter else round(factory, 1)
    actual = snapshot.get("actual_l100")
    verdict = None
    over_limit = False
    if actual and winter_factory:
        ratio = actual / winter_factory
        if ratio <= 1.05:
            verdict = "в пределах нормы"
        elif ratio <= 1.2:
            verdict = "чуть выше нормы"
        else:
            verdict = "выше нормы — проверьте стиль вождения"
            over_limit = True
    return {
        "official_range": fuel_ref.get("official_range"),
        "cycles": fuel_ref.get("cycles") or [],
        "notes": fuel_ref.get("notes") or [],
        "factory_l100": winter_factory,
        "actual_l100": actual,
        "blended_l100": adaptive.get("blended_l100"),
        "confidence": adaptive.get("confidence"),
        "is_winter": is_winter,
        "verdict": verdict,
        "over_limit": over_limit,
    }


def normalize_plate(plate: str) -> str:
    return " ".join((plate or "").upper().split())


def is_valid_model(code: str) -> bool:
    return (code or "").strip() in VEHICLE_MODELS


def assign_vehicle(db, user_id: int, model_code: str, plate_number: str) -> None:
    model_code = (model_code or "").strip()
    plate_number = normalize_plate(plate_number)
    if not is_valid_model(model_code):
        raise ValueError("Неизвестная модель авто")
    if not plate_number:
        raise ValueError("Укажите госномер")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO mechanic_vehicles (user_id, model_code, plate_number, assigned_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            model_code = excluded.model_code,
            plate_number = excluded.plate_number,
            assigned_at = excluded.assigned_at
        """,
        (int(user_id), model_code, plate_number, now),
    )
    db.commit()


def get_vehicle_for_user(db, user_id: int) -> dict | None:
    cur = db.cursor()
    cur.execute(
        "SELECT * FROM mechanic_vehicles WHERE user_id = ?",
        (int(user_id),),
    )
    row = cur.fetchone()
    if not row:
        return None
    model = VEHICLE_MODELS.get(row["model_code"], {})
    fleet = None
    try:
        fv_id = row["fleet_vehicle_id"]
    except (KeyError, IndexError, TypeError):
        fv_id = None
    if fv_id:
        fleet = get_fleet_vehicle(db, int(fv_id))
    elif row["plate_number"]:
        fleet = get_fleet_vehicle_for_user(db, user_id)
    result = {
        "user_id": row["user_id"],
        "model_code": row["model_code"],
        "model_name": model.get("name", row["model_code"]),
        "image": model.get("image", ""),
        "factory_l100": model.get("factory_l100", 10.0),
        "winter_factor": model.get("winter_factor", 1.1),
        "plate_number": row["plate_number"],
        "assigned_at": row["assigned_at"],
        "fleet_vehicle_id": int(fv_id) if fv_id else None,
        "fleet": fleet,
    }
    if fleet:
        result["model_name"] = fleet.get("name") or result["model_name"]
        result["plate_number"] = fleet.get("plate_number") or result["plate_number"]
        result["color"] = fleet.get("color") or ""
        result["is_branded"] = bool(fleet.get("is_branded"))
        result["has_field_service"] = bool(fleet.get("has_field_service"))
        result["initial_mileage"] = int(fleet.get("initial_mileage") or 0)
        result["fleet_schedule"] = fleet.get("schedule") or []
        result["fuel_type"] = fleet.get("fuel_type") or ""
        result["fuel_type_label"] = fleet_fuel_type_label(fleet.get("fuel_type"))
    else:
        result["fuel_type"] = ""
        result["fuel_type_label"] = "—"
    return result


def get_latest_mileage(db, user_id: int) -> int | None:
    """Текущий пробег одометра — максимальное зафиксированное значение."""
    cur = db.cursor()
    cur.execute(
        """
        SELECT MAX(mileage_km) FROM vehicle_mileage_logs
        WHERE user_id = ?
        """,
        (int(user_id),),
    )
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def add_mileage_log(db, user_id: int, mileage_km: int, note: str = "") -> None:
    mileage_km = int(mileage_km)
    if mileage_km < 0:
        raise ValueError("Пробег не может быть отрицательным")
    latest = get_latest_mileage(db, user_id)
    if latest is not None and mileage_km < latest:
        raise ValueError(f"Новый пробег не может быть меньше предыдущего ({latest} км)")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO vehicle_mileage_logs (user_id, mileage_km, logged_at, note)
        VALUES (?, ?, ?, ?)
        """,
        (int(user_id), mileage_km, now, (note or "").strip() or None),
    )
    db.commit()


def list_mileage_logs(db, user_id: int, limit: int = 24) -> list:
    cur = db.cursor()
    cur.execute(
        """
        SELECT * FROM vehicle_mileage_logs
        WHERE user_id = ?
        ORDER BY datetime(logged_at) DESC, id DESC
        LIMIT ?
        """,
        (int(user_id), int(limit)),
    )
    return cur.fetchall()


def upsert_fuel_monthly(
    db,
    user_id: int,
    year_month: str,
    amount_rub: float,
    fill_count: int,
    note: str = "",
) -> None:
    ym = (year_month or "").strip()
    if len(ym) != 7 or ym[4] != "-":
        raise ValueError("Некорректный месяц")
    amount_rub = float(amount_rub or 0)
    fill_count = int(fill_count or 0)
    if amount_rub < 0 or fill_count < 0:
        raise ValueError("Сумма и количество заправок не могут быть отрицательными")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO vehicle_fuel_monthly (user_id, year_month, amount_rub, fill_count, note, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, year_month) DO UPDATE SET
            amount_rub = excluded.amount_rub,
            fill_count = excluded.fill_count,
            note = excluded.note,
            updated_at = excluded.updated_at
        """,
        (int(user_id), ym, amount_rub, fill_count, (note or "").strip() or None, now),
    )
    db.commit()


def list_fuel_monthly(db, user_id: int, limit: int = 18) -> list:
    cur = db.cursor()
    cur.execute(
        """
        SELECT * FROM vehicle_fuel_monthly
        WHERE user_id = ?
        ORDER BY year_month DESC
        LIMIT ?
        """,
        (int(user_id), int(limit)),
    )
    return cur.fetchall()


def list_fuel_fills_in_period(
    db,
    user_id: int,
    start_date: str,
    end_date: str,
) -> list[dict]:
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, fill_date, liters, price_per_liter, station_name, sort_order
        FROM vehicle_fuel_fills
        WHERE user_id = ? AND fill_date >= ? AND fill_date <= ?
        ORDER BY fill_date ASC, sort_order ASC, id ASC
        """,
        (int(user_id), start_date, end_date),
    )
    return [dict(row) for row in cur.fetchall()]


def aggregate_fuel_fills(
    db,
    user_id: int,
    start_date: str,
    end_date: str,
) -> dict:
    fills = list_fuel_fills_in_period(db, user_id, start_date, end_date)
    if not fills:
        return {
            "amount_rub": 0.0,
            "fill_count": 0,
            "est_liters": None,
            "has_fills": False,
        }
    liters_total = sum(float(row.get("liters") or 0) for row in fills)
    amount = sum(
        float(row.get("liters") or 0) * float(row.get("price_per_liter") or 0) for row in fills
    )
    return {
        "amount_rub": round(amount, 2),
        "fill_count": len(fills),
        "est_liters": round(liters_total, 1) if liters_total > 0 else None,
        "has_fills": True,
    }


def _month_fuel_totals(db, user_id: int, year: int, month: int) -> tuple[float, int, float | None]:
    last_day = calendar.monthrange(year, month)[1]
    start_date = f"{year:04d}-{month:02d}-01"
    end_date = f"{year:04d}-{month:02d}-{last_day:02d}"
    agg = aggregate_fuel_fills(db, user_id, start_date, end_date)
    if agg["has_fills"]:
        return agg["amount_rub"], agg["fill_count"], agg["est_liters"]
    cur = db.cursor()
    ym = f"{year:04d}-{month:02d}"
    cur.execute(
        """
        SELECT amount_rub, fill_count FROM vehicle_fuel_monthly
        WHERE user_id = ? AND year_month = ?
        """,
        (int(user_id), ym),
    )
    row = cur.fetchone()
    if not row:
        return 0.0, 0, None
    amount = float(row["amount_rub"] or 0)
    fills = int(row["fill_count"] or 0)
    return amount, fills, None


def _sync_fuel_monthly_for_range(db, user_id: int, start_date: str, end_date: str) -> None:
    start_year, start_month = int(start_date[:4]), int(start_date[5:7])
    end_year, end_month = int(end_date[:4]), int(end_date[5:7])
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        last_day = calendar.monthrange(year, month)[1]
        month_start = f"{year:04d}-{month:02d}-01"
        month_end = f"{year:04d}-{month:02d}-{last_day:02d}"
        agg = aggregate_fuel_fills(db, user_id, month_start, month_end)
        upsert_fuel_monthly(
            db,
            user_id,
            f"{year:04d}-{month:02d}",
            agg["amount_rub"],
            agg["fill_count"],
        )
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def replace_fuel_fills_in_period(
    db,
    user_id: int,
    start_date: str,
    end_date: str,
    items: list[dict],
) -> None:
    start_date = (start_date or "").strip()[:10]
    end_date = (end_date or "").strip()[:10]
    if not start_date or not end_date:
        raise ValueError("Некорректный период")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.cursor()
    cur.execute(
        """
        DELETE FROM vehicle_fuel_fills
        WHERE user_id = ? AND fill_date >= ? AND fill_date <= ?
        """,
        (int(user_id), start_date, end_date),
    )
    for idx, raw in enumerate(items):
        fill_date = (raw.get("fill_date") or "").strip()[:10]
        if not fill_date:
            continue
        if fill_date < start_date or fill_date > end_date:
            raise ValueError(f"Дата заправки {fill_date} вне отчётного периода")
        try:
            liters = float(raw.get("liters") or 0)
            price_per_liter = float(raw.get("price_per_liter") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректные значения литров или цены") from exc
        if liters <= 0:
            raise ValueError("Укажите количество литров")
        if price_per_liter < 0:
            raise ValueError("Цена за литр не может быть отрицательной")
        station_name = (raw.get("station_name") or "").strip() or None
        cur.execute(
            """
            INSERT INTO vehicle_fuel_fills (
                user_id, fill_date, liters, price_per_liter, station_name, sort_order, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (int(user_id), fill_date, liters, price_per_liter, station_name, idx, now),
        )
    _sync_fuel_monthly_for_range(db, user_id, start_date, end_date)
    db.commit()


def add_fuel_fills_in_period(
    db,
    user_id: int,
    start_date: str,
    end_date: str,
    items: list[dict],
) -> int:
    """Добавляет новые заправки в период, не удаляя существующие."""
    start_date = (start_date or "").strip()[:10]
    end_date = (end_date or "").strip()[:10]
    if not start_date or not end_date:
        raise ValueError("Некорректный период")
    if not items:
        raise ValueError("Заполните данные хотя бы по одной заправке")

    existing = list_fuel_fills_in_period(db, user_id, start_date, end_date)
    next_sort = max((int(row.get("sort_order") or 0) for row in existing), default=-1) + 1
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.cursor()
    added = 0

    for raw in items:
        fill_date = (raw.get("fill_date") or "").strip()[:10]
        if not fill_date:
            continue
        if fill_date < start_date or fill_date > end_date:
            raise ValueError(f"Дата заправки {fill_date} вне отчётного периода")
        try:
            liters = float(raw.get("liters") or 0)
            price_per_liter = float(raw.get("price_per_liter") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректные значения литров или цены") from exc
        if liters <= 0:
            raise ValueError("Укажите количество литров")
        if price_per_liter < 0:
            raise ValueError("Цена за литр не может быть отрицательной")
        station_name = (raw.get("station_name") or "").strip() or None
        cur.execute(
            """
            INSERT INTO vehicle_fuel_fills (
                user_id, fill_date, liters, price_per_liter, station_name, sort_order, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                fill_date,
                liters,
                price_per_liter,
                station_name,
                next_sort + added,
                now,
            ),
        )
        added += 1

    if added == 0:
        raise ValueError("Заполните данные хотя бы по одной заправке")

    _sync_fuel_monthly_for_range(db, user_id, start_date, end_date)
    db.commit()
    return added


def delete_fuel_fill(db, user_id: int, fill_id: int) -> None:
    cur = db.cursor()
    cur.execute(
        """
        SELECT fill_date FROM vehicle_fuel_fills
        WHERE id = ? AND user_id = ?
        """,
        (int(fill_id), int(user_id)),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError("Заправка не найдена")
    fill_date = row["fill_date"]
    cur.execute(
        """
        DELETE FROM vehicle_fuel_fills
        WHERE id = ? AND user_id = ?
        """,
        (int(fill_id), int(user_id)),
    )
    _sync_fuel_monthly_for_range(db, user_id, fill_date, fill_date)
    db.commit()


def _mileage_near_date(db, user_id: int, iso_date: str, prefer_last: bool = True) -> int | None:
    """Пробег на дату или ближайшую запись до/после границы месяца."""
    cur = db.cursor()
    if prefer_last:
        cur.execute(
            """
            SELECT mileage_km FROM vehicle_mileage_logs
            WHERE user_id = ? AND substr(logged_at, 1, 10) <= ?
            ORDER BY datetime(logged_at) DESC, id DESC
            LIMIT 1
            """,
            (int(user_id), iso_date),
        )
    else:
        cur.execute(
            """
            SELECT mileage_km FROM vehicle_mileage_logs
            WHERE user_id = ? AND substr(logged_at, 1, 10) >= ?
            ORDER BY datetime(logged_at) ASC, id ASC
            LIMIT 1
            """,
            (int(user_id), iso_date),
        )
    row = cur.fetchone()
    return int(row[0]) if row else None


def _month_km(db, user_id: int, year: int, month: int) -> int | None:
    if month == 12:
        next_y, next_m = year + 1, 1
    else:
        next_y, next_m = year, month + 1
    start = f"{year:04d}-{month:02d}-01"
    end = f"{next_y:04d}-{next_m:02d}-01"
    start_km = _mileage_near_date(db, user_id, start, prefer_last=True)
    end_km = _mileage_near_date(db, user_id, end, prefer_last=False)
    if start_km is None or end_km is None:
        return None
    delta = end_km - start_km
    return delta if delta > 0 else None


def compute_monthly_stats(
    db,
    user_id: int,
    fuel_price_rub_l: float = 55.0,
) -> list[dict]:
    vehicle = get_vehicle_for_user(db, user_id)
    if not vehicle:
        return []
    fuel_rows = list_fuel_monthly(db, user_id, limit=36)
    stats: list[dict] = []
    for fr in fuel_rows:
        ym = fr["year_month"]
        year, month = int(ym[:4]), int(ym[5:7])
        km = _month_km(db, user_id, year, month)
        amount, fills, est_liters_from_fills = _month_fuel_totals(db, user_id, year, month)
        if est_liters_from_fills is not None:
            est_liters = est_liters_from_fills
        else:
            est_liters = amount / fuel_price_rub_l if fuel_price_rub_l > 0 and amount > 0 else 0
        l100 = (est_liters / km * 100) if km and km > 0 and est_liters > 0 else None
        is_winter = month in WINTER_MONTHS
        factory = vehicle["factory_l100"]
        if is_winter:
            factory *= vehicle["winter_factor"]
        stats.append(
            {
                "year_month": ym,
                "year": year,
                "month": month,
                "km": km,
                "amount_rub": amount,
                "fill_count": fills,
                "est_liters": round(est_liters, 1) if est_liters else None,
                "actual_l100": round(l100, 2) if l100 else None,
                "factory_l100": round(factory, 2),
                "is_winter": is_winter,
            }
        )
    return stats


def get_adaptive_consumption(db, user_id: int, fuel_price_rub_l: float = 55.0) -> dict:
    vehicle = get_vehicle_for_user(db, user_id)
    if not vehicle:
        return {}
    monthly = compute_monthly_stats(db, user_id, fuel_price_rub_l)
    measured = [m for m in monthly if m.get("actual_l100") and m.get("km", 0) and m["km"] >= 30]
    factory = vehicle["factory_l100"]
    if not measured:
        return {
            "factory_l100": factory,
            "learned_l100": None,
            "blended_l100": factory,
            "confidence": "заводской норматив",
            "months_used": 0,
        }
    # Вес свежее — больше; чем больше месяцев, тем выше доверие к факту.
    total_w = 0.0
    weighted = 0.0
    for i, m in enumerate(measured):
        w = 1.0 + i * 0.35
        weighted += m["actual_l100"] * w
        total_w += w
    learned = weighted / total_w if total_w else measured[0]["actual_l100"]
    n = len(measured)
    learn_share = min(0.85, 0.25 + n * 0.12)
    blended = factory * (1 - learn_share) + learned * learn_share
    confidence = "уточнённый" if n >= 3 else "предварительный"
    return {
        "factory_l100": round(factory, 2),
        "learned_l100": round(learned, 2),
        "blended_l100": round(blended, 2),
        "confidence": confidence,
        "months_used": n,
    }


def build_fuel_analysis(db, user_id: int, fuel_price_rub_l: float = 55.0) -> dict:
    """Данные для окна «почему?» — полугодовая статистика и порог перерасхода."""
    adaptive = get_adaptive_consumption(db, user_id, fuel_price_rub_l)
    monthly = compute_monthly_stats(db, user_id, fuel_price_rub_l)
    vehicle = get_vehicle_for_user(db, user_id) or {}
    factory = float(adaptive.get("factory_l100") or vehicle.get("factory_l100") or 10)
    learned = adaptive.get("learned_l100")
    blended = float(adaptive.get("blended_l100") or factory)
    threshold_ratio = 1.2

    recent = list(reversed(monthly[:6]))
    series: list[dict] = []
    for m in recent:
        actual = m.get("actual_l100")
        series.append(
            {
                "label": f"{int(m['month']):02d}.{str(m['year'])[-2:]}",
                "year_month": m.get("year_month"),
                "actual_l100": actual,
                "km": m.get("km"),
                "fill_count": m.get("fill_count"),
                "amount_rub": m.get("amount_rub"),
                "factory_l100": m.get("factory_l100"),
                "over_limit": bool(actual and actual > blended * threshold_ratio),
            }
        )

    current = monthly[0] if monthly else {}
    current_actual = current.get("actual_l100")
    ratio = (float(current_actual) / blended) if current_actual and blended else None
    over_pct = round((ratio - 1) * 100, 1) if ratio is not None else None

    return {
        "vehicle_name": vehicle.get("model_name") or "",
        "factory_l100": factory,
        "learned_l100": learned,
        "blended_l100": blended,
        "confidence": adaptive.get("confidence") or "",
        "months_used": adaptive.get("months_used") or 0,
        "series": series,
        "current_actual_l100": current_actual,
        "over_pct": over_pct,
        "threshold_pct": int((threshold_ratio - 1) * 100),
        "fuel_price_rub_l": fuel_price_rub_l,
    }


def get_model_reference(model_code: str) -> dict | None:
    model = VEHICLE_MODELS.get(model_code or "")
    if not model:
        return None
    return {
        "fuel_reference": model.get("fuel_reference"),
        "maintenance": model.get("maintenance"),
    }


def get_service_hints(latest_mileage: int | None, model_code: str) -> list[dict]:
    """Ближайшие регламентные точки ТО от текущего пробега."""
    if latest_mileage is None:
        return []
    model = VEHICLE_MODELS.get(model_code or "", {})
    maintenance = model.get("maintenance") or {}
    hints: list[dict] = []

    oil_km = maintenance.get("recommended_oil_km")
    if oil_km:
        oil_km = int(oil_km)
        next_oil = ((latest_mileage // oil_km) + 1) * oil_km
        hints.append(
            {
                "target_km": next_oil,
                "remaining_km": next_oil - latest_mileage,
                "label": "Замена масла",
            }
        )

    official = maintenance.get("official_interval_km")
    if official:
        official = int(official)
        next_official = ((latest_mileage // official) + 1) * official
        hints.append(
            {
                "target_km": next_official,
                "remaining_km": next_official - latest_mileage,
                "label": "Официальное ТО",
            }
        )

    recurring = {oil_km, official}
    for milestone in maintenance.get("service_milestones_km") or []:
        m = int(milestone)
        if m in recurring or latest_mileage >= m:
            continue
        hints.append(
            {
                "target_km": m,
                "remaining_km": m - latest_mileage,
                "label": f"Регламент на {m:,} км".replace(",", " "),
            }
        )

    hints.sort(key=lambda h: h["remaining_km"])
    seen: set[int] = set()
    unique: list[dict] = []
    for hint in hints:
        if hint["target_km"] in seen:
            continue
        seen.add(hint["target_km"])
        unique.append(hint)
    return unique[:3]


def list_fuel_reports_for_panel(db, user_id: int) -> list[dict]:
    from vehicle_fuel_report import list_fuel_reports

    return list_fuel_reports(db, user_id)


def build_vehicle_panel_context(
    db,
    user_id: int,
    fuel_price_rub_l: float,
    show_admin_stats: bool,
    get_setting_fn: Callable[[str, str], str] | None = None,
) -> dict:
    vehicle = get_vehicle_for_user(db, user_id)
    if not vehicle:
        return {"has_vehicle": False}
    latest = get_latest_mileage(db, user_id)
    last_oil = get_last_oil_change_mileage(db, user_id)
    now = datetime.now()
    current_ym = now.strftime("%Y-%m")
    fuel_rows = list_fuel_monthly(db, user_id)
    current_fuel = next((r for r in fuel_rows if r["year_month"] == current_ym), None)
    period_config = get_fleet_period_config(get_setting_fn)
    period_snapshot = get_reporting_period_snapshot(
        db, user_id, fuel_price_rub_l, now, period_config=period_config
    )
    period_start_date = period_snapshot.get("start_date") or now.strftime("%Y-%m-01")
    period_end_date = period_snapshot.get("end_date") or now.strftime("%Y-%m-%d")
    fuel_fills = list_fuel_fills_in_period(db, user_id, period_start_date, period_end_date)
    fuel_type_label = vehicle.get("fuel_type_label") or "—"
    fuel_stations = get_fuel_station_catalog(get_setting_fn)
    fleet_schedule = vehicle.get("fleet_schedule") or []
    if fleet_schedule:
        maintenance_brief = build_fleet_schedule_brief(latest, fleet_schedule)
        model_reference = {
            "fuel_reference": (VEHICLE_MODELS.get(vehicle["model_code"]) or {}).get("fuel_reference") or {},
            "maintenance": {
                "schedule": [
                    {
                        "title": s.get("title") or "",
                        "mileage": s.get("mileage_mark") or "",
                        "works": s.get("comment") or "",
                    }
                    for s in fleet_schedule
                ],
                "notes": [],
            },
        }
    else:
        maintenance_brief = build_maintenance_brief(latest, vehicle["model_code"], last_oil)
        model_reference = get_model_reference(vehicle["model_code"])
    fleet_vehicle = get_fleet_vehicle_for_user(db, user_id)
    fleet_vehicle_id = int(fleet_vehicle["id"]) if fleet_vehicle else None
    official_regulation_rows = []
    important_info_rows = []
    branding_items = []
    van_equipment = []
    mechanic_messages = []
    mechanic_messages_open_count = 0
    mechanic_messages_important_count = 0
    fleet_is_branded = False
    fleet_has_field_service = False
    fleet_photos_by_slot: dict[int, str] = {}
    if fleet_vehicle_id:
        official_regulation_rows = list_fleet_vehicle_schedule(db, fleet_vehicle_id)
        important_info_rows = list_fleet_vehicle_important_info(db, fleet_vehicle_id)
        branding_items = list_fleet_vehicle_branding_items(db, fleet_vehicle_id)
        van_equipment = list_fleet_vehicle_van_equipment(db, fleet_vehicle_id)
        mechanic_messages = list_fleet_vehicle_mechanic_messages(db, fleet_vehicle_id, open_only=True)
        mechanic_messages_open_count = count_open_mechanic_messages(db, fleet_vehicle_id)
        mechanic_messages_important_count = count_open_mechanic_messages_important(db, fleet_vehicle_id)
        fleet_is_branded = bool(fleet_vehicle.get("is_branded"))
        fleet_has_field_service = bool(fleet_vehicle.get("has_field_service"))
        fleet_photos_by_slot = fleet_photos_by_slot_map(
            fleet_vehicle.get("photos") or list_fleet_vehicle_photos(db, fleet_vehicle_id)
        )
    branding_presets = build_branding_presets(branding_items) if fleet_vehicle_id else []
    van_catalog = get_fleet_van_equipment_catalog(get_setting_fn)
    van_equipment_catalog = (
        build_van_equipment_catalog_state(van_catalog, van_equipment) if fleet_vehicle_id else []
    )
    branding_has_visible = any(p.get("enabled") for p in branding_presets)
    branding_block_visible_for_mechanic = branding_has_visible or bool(van_equipment)
    mileage_informer = build_mileage_informer(db, user_id, get_setting_fn, now)
    cur = db.cursor()
    cur.execute("SELECT max_user_id FROM users WHERE id = ?", (int(user_id),))
    user_max_row = cur.fetchone()
    mechanic_max_user_id = ""
    if user_max_row:
        mechanic_max_user_id = (dict(user_max_row).get("max_user_id") or "").strip()
    ctx = {
        "has_vehicle": True,
        "fleet_vehicle_id": fleet_vehicle_id,
        "official_regulation_rows": official_regulation_rows,
        "official_regulation_live": build_official_regulation_live(
            db, fleet_vehicle_id, official_regulation_rows, latest, min_rows=2
        ),
        "official_regulation_live_display": build_official_regulation_live(
            db, fleet_vehicle_id, official_regulation_rows, latest, min_rows=0
        ),
        "important_info_rows": important_info_rows,
        "important_info_live": build_important_info_live(important_info_rows, latest, min_rows=1),
        "important_info_live_display": build_important_info_live(important_info_rows, latest, min_rows=0),
        "branding_items": branding_items,
        "branding_presets": branding_presets,
        "branding_has_visible": branding_has_visible,
        "branding_block_visible_for_mechanic": branding_block_visible_for_mechanic,
        "van_equipment": van_equipment,
        "van_equipment_catalog": van_equipment_catalog,
        "mechanic_messages": mechanic_messages,
        "mechanic_messages_open_count": mechanic_messages_open_count if fleet_vehicle_id else 0,
        "mechanic_messages_important_count": mechanic_messages_important_count if fleet_vehicle_id else 0,
        "mechanic_info_today": date.today().isoformat(),
        "mechanic_max_user_id": mechanic_max_user_id,
        "mechanic_max_linked": bool(mechanic_max_user_id),
        "fleet_is_branded": fleet_is_branded,
        "fleet_has_field_service": fleet_has_field_service,
        "fleet_photos_by_slot": fleet_photos_by_slot,
        "fleet_photos_ordered": fleet_photos_ordered_files(fleet_photos_by_slot),
        "vehicle": vehicle,
        "latest_mileage": latest,
        "mileage_display": format_mileage_km(latest),
        "mileage_logs": list_mileage_logs(db, user_id),
        "oil_logs": list_oil_logs(db, user_id),
        "last_oil_km": last_oil,
        "fuel_rows": fuel_rows,
        "current_ym": current_ym,
        "current_fuel": current_fuel,
        "vehicle_models": VEHICLE_MODELS,
        "fuel_price_rub_l": fuel_price_rub_l,
        "period_snapshot": period_snapshot,
        "mileage_informer": mileage_informer,
        "fuel_fills": fuel_fills,
        "fuel_type_label": fuel_type_label,
        "fuel_stations": fuel_stations,
        "fuel_period_start": period_start_date,
        "fuel_period_end": period_end_date,
        "fuel_reports": list_fuel_reports_for_panel(db, user_id),
        "model_reference": model_reference,
        "service_hints": get_service_hints(latest, vehicle["model_code"]),
        "maintenance_brief": maintenance_brief,
        "regulation_widget": build_regulation_widget(db, fleet_vehicle_id, latest),
        "fuel_brief": build_fuel_brief(db, user_id, vehicle["model_code"], fuel_price_rub_l, period_snapshot),
        "fleet_schedule": fleet_schedule,
    }
    if show_admin_stats:
        ctx["monthly_stats"] = compute_monthly_stats(db, user_id, fuel_price_rub_l)
        ctx["adaptive"] = get_adaptive_consumption(db, user_id, fuel_price_rub_l)
    return ctx


def get_fleet_preview_demo(demo_id: int) -> dict | None:
    for demo in _FLEET_PREVIEW_VEHICLES:
        if int(demo["id"]) == int(demo_id):
            return demo
    return None


def _build_fleet_preview_panel_from_demo(
    db,
    demo: dict,
    fuel_price_rub_l: float,
    get_setting_fn: Callable[[str, str], str] | None = None,
) -> dict:
    model_code = demo["model_code"]
    model = VEHICLE_MODELS.get(model_code, {})
    period_config = get_fleet_period_config(get_setting_fn)
    now = datetime.now()
    day_from = int(period_config.get("day_from", 1))
    day_to = int(period_config.get("day_to", 0))
    end_day = _period_last_day(now.year, now.month, day_to)
    start_day = min(day_from, end_day)
    period_start_date = f"{now.year:04d}-{now.month:02d}-{start_day:02d}"
    period_end_date = f"{now.year:04d}-{now.month:02d}-{end_day:02d}"
    snap = {
        "period_label": format_period_label(
            now.year,
            now.month,
            day_from,
            day_to,
        ),
        "year_month": now.strftime("%Y-%m"),
        "start_date": period_start_date,
        "end_date": period_end_date,
        "km": demo["snapshot"]["km"],
        "amount_rub": demo["snapshot"]["amount_rub"],
        "fill_count": demo["snapshot"]["fill_count"],
        "est_liters": None,
        "actual_l100": demo["snapshot"]["actual_l100"],
        "factory_l100": model.get("factory_l100"),
        "norm_pct": None,
        "km_bar_pct": 0,
        "fuel_bar_pct": 0,
    }
    if snap.get("actual_l100") and snap.get("factory_l100"):
        snap["norm_pct"] = min(
            150, int(round(float(snap["actual_l100"]) / float(snap["factory_l100"]) * 100))
        )
    snap["km_bar_pct"] = min(100, int((snap.get("km") or 0) / 800 * 100)) if snap.get("km") else 0
    snap["fuel_bar_pct"] = (
        min(100, int((snap.get("amount_rub") or 0) / 25000 * 100)) if snap.get("amount_rub") else 0
    )
    mileage = demo["latest_mileage"]
    last_oil = demo["last_oil_km"]
    maintenance_brief = build_maintenance_brief(mileage, model_code, last_oil)
    preview_previous = max(0, int(mileage) - 500) if mileage is not None else None
    preview_start = max(0, int(mileage) - 820) if mileage is not None else None
    return {
        "has_vehicle": True,
        "is_preview": True,
        "vehicle": {
            "user_id": demo["id"],
            "model_code": model_code,
            "model_name": model.get("name", model_code),
            "image": model.get("image", ""),
            "factory_l100": model.get("factory_l100", 10.0),
            "winter_factor": model.get("winter_factor", 1.1),
            "plate_number": demo["plate_number"],
            "assigned_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "latest_mileage": mileage,
        "mileage_logs": [],
        "oil_logs": [],
        "last_oil_km": last_oil,
        "fuel_rows": [],
        "current_ym": now.strftime("%Y-%m"),
        "current_fuel": {
            "year_month": now.strftime("%Y-%m"),
            "amount_rub": demo["snapshot"]["amount_rub"],
            "fill_count": demo["snapshot"]["fill_count"],
            "note": "тестовые данные",
        },
        "vehicle_models": VEHICLE_MODELS,
        "fuel_price_rub_l": fuel_price_rub_l,
        "period_snapshot": snap,
        "mileage_informer": {
            "current_km": mileage,
            "current_km_display": format_mileage_km(mileage),
            "previous_km": preview_previous,
            "previous_km_display": format_mileage_km(preview_previous),
            "period_label": snap["period_label"],
            "period_start": {
                "day": int(period_config.get("day_from", 1)),
                "day_label": _format_russian_ordinal_day(int(period_config.get("day_from", 1))),
                "fixed": True,
                "km": preview_start,
                "km_display": format_mileage_km(preview_start),
                "active": True,
                "pending": False,
            },
            "period_end": {
                "day": _period_last_day(now.year, now.month, int(period_config.get("day_to", 0))),
                "day_label": _format_russian_ordinal_day(
                    _period_last_day(now.year, now.month, int(period_config.get("day_to", 0)))
                ),
                "fixed": False,
                "km": None,
                "km_display": None,
                "active": True,
                "pending": True,
            },
            "period_closed": False,
            "period_closed_message": "",
        },
        "model_reference": get_model_reference(model_code),
        "service_hints": get_service_hints(mileage, model_code),
        "maintenance_brief": maintenance_brief,
        "regulation_widget": build_regulation_widget_preview(maintenance_brief),
        "fuel_brief": build_fuel_brief(db, demo["id"], model_code, fuel_price_rub_l, snap),
        "fuel_fills": [],
        "fuel_type_label": fleet_fuel_type_label("gasoline_95"),
        "fuel_stations": get_fuel_station_catalog(get_setting_fn),
        "fuel_period_start": period_start_date,
        "fuel_period_end": period_end_date,
        "fuel_reports": [],
        "fleet_schedule": [],
    }


def build_fleet_preview_detail_context(
    db,
    demo_id: int,
    fuel_price_rub_l: float,
    show_admin_stats: bool,
    get_setting_fn: Callable[[str, str], str] | None = None,
) -> tuple[dict | None, dict]:
    demo = get_fleet_preview_demo(demo_id)
    if not demo:
        return None, {"has_vehicle": False}
    selected_mechanic = {
        "id": demo["id"],
        "last_name": demo["last_name"],
        "first_name": demo["first_name"],
        "middle_name": demo["middle_name"],
        "role": "MECHANIC",
    }
    ctx = _build_fleet_preview_panel_from_demo(db, demo, fuel_price_rub_l, get_setting_fn)
    if show_admin_stats:
        ctx["monthly_stats"] = []
        ctx["adaptive"] = {}
    return selected_mechanic, ctx


def build_fleet_preview_mechanic_panel(
    db,
    fuel_price_rub_l: float,
    get_setting_fn: Callable[[str, str], str] | None = None,
) -> dict:
    """Временная карточка «как у механика» для переключателя вида в Автопарке."""
    return _build_fleet_preview_panel_from_demo(db, _FLEET_PREVIEW_VEHICLES[0], fuel_price_rub_l, get_setting_fn)


def list_mechanics_with_vehicles(db) -> list[dict]:
    cur = db.cursor()
    cur.execute(
        """
        SELECT u.id, u.last_name, u.first_name, u.middle_name,
               mv.model_code, mv.plate_number, mv.fleet_vehicle_id,
               fv.name AS fleet_name, fv.plate_number AS fleet_plate
        FROM users u
        LEFT JOIN mechanic_vehicles mv ON mv.user_id = u.id
        LEFT JOIN fleet_vehicles fv ON fv.id = mv.fleet_vehicle_id
        WHERE u.role IN ('MECHANIC', 'EMPLOYEE') AND u.is_approved = 1
        ORDER BY u.last_name, u.first_name
        """
    )
    rows = []
    for r in cur.fetchall():
        model = VEHICLE_MODELS.get(r["model_code"] or "", {})
        model_name = r["fleet_name"] or model.get("name")
        plate = r["fleet_plate"] or r["plate_number"]
        rows.append(
            {
                "id": r["id"],
                "last_name": r["last_name"],
                "first_name": r["first_name"],
                "middle_name": r["middle_name"],
                "model_code": r["model_code"],
                "model_name": model_name,
                "plate_number": plate,
                "fleet_vehicle_id": r["fleet_vehicle_id"],
                "has_vehicle": bool(r["model_code"] or r["fleet_vehicle_id"]),
            }
        )
    return rows


def get_reporting_period_snapshot(
    db,
    user_id: int,
    fuel_price_rub_l: float = 55.0,
    ref_date: datetime | None = None,
    period_config: dict | None = None,
) -> dict:
    """Сводка за настраиваемый отчётный период текущего месяца."""
    ref = ref_date or datetime.now()
    year, month = ref.year, ref.month
    cfg = period_config or {"day_from": 1, "day_to": 0}
    day_from = int(cfg.get("day_from") or 1)
    day_to = int(cfg.get("day_to") or 0)
    end_day = _period_last_day(year, month, day_to)
    day_from = min(day_from, end_day)
    start_date = f"{year:04d}-{month:02d}-{day_from:02d}"
    end_date = f"{year:04d}-{month:02d}-{end_day:02d}"
    start_km = _mileage_near_date(db, user_id, start_date, prefer_last=True)
    end_km = _mileage_near_date(db, user_id, end_date, prefer_last=True)
    km = None
    if start_km is not None and end_km is not None and end_km >= start_km:
        km = end_km - start_km

    ym = f"{year:04d}-{month:02d}"
    fuel_agg = aggregate_fuel_fills(db, user_id, start_date, end_date)
    if fuel_agg["has_fills"]:
        amount = fuel_agg["amount_rub"]
        fills = fuel_agg["fill_count"]
        est_liters = fuel_agg["est_liters"]
    else:
        cur = db.cursor()
        cur.execute(
            """
            SELECT amount_rub, fill_count FROM vehicle_fuel_monthly
            WHERE user_id = ? AND year_month = ?
            """,
            (int(user_id), ym),
        )
        fuel = cur.fetchone()
        amount = float(fuel["amount_rub"]) if fuel else 0.0
        fills = int(fuel["fill_count"]) if fuel else 0
        est_liters = amount / fuel_price_rub_l if amount > 0 and fuel_price_rub_l > 0 else None
    l100 = (est_liters / km * 100) if km and km > 0 and est_liters else None

    vehicle = get_vehicle_for_user(db, user_id)
    factory = vehicle["factory_l100"] if vehicle else 10.0
    if month in WINTER_MONTHS and vehicle:
        factory *= vehicle["winter_factor"]

    norm_pct = None
    if l100 and factory:
        norm_pct = min(150, int(round(l100 / factory * 100)))

    return {
        "period_label": format_period_label(year, month, day_from, day_to),
        "year_month": ym,
        "start_date": start_date,
        "end_date": end_date,
        "km": km,
        "amount_rub": amount,
        "fill_count": fills,
        "est_liters": round(est_liters, 1) if est_liters else None,
        "actual_l100": round(l100, 1) if l100 else None,
        "factory_l100": round(factory, 1),
        "norm_pct": norm_pct,
        "km_bar_pct": min(100, int((km or 0) / 800 * 100)) if km else 0,
        "fuel_bar_pct": min(100, int(amount / 25000 * 100)) if amount else 0,
    }


def build_autos_grid_cards(
    db,
    mechanics_autos: list[dict],
    fuel_price_rub_l: float,
    get_setting_fn: Callable[[str, str], str] | None = None,
) -> list[dict]:
    period_config = get_fleet_period_config(get_setting_fn)
    cards = []
    for m in mechanics_autos:
        fleet = get_fleet_vehicle_for_user(db, m["id"])
        if not fleet:
            continue
        snap = get_reporting_period_snapshot(
            db, m["id"], fuel_price_rub_l, period_config=period_config
        )
        latest = get_latest_mileage(db, m["id"])
        fleet_schedule = fleet.get("schedule") or []
        maint = build_fleet_schedule_brief(latest, fleet_schedule) if fleet_schedule else build_maintenance_brief(
            latest, m.get("model_code") or "fleet_custom", get_last_oil_change_mileage(db, m["id"])
        )
        fv_id = fleet.get("id")
        regulation_widget = (
            build_regulation_widget(db, int(fv_id), latest)
            if fv_id
            else build_regulation_widget_preview(maint)
        )
        model = VEHICLE_MODELS.get(m.get("model_code") or "", {})
        fuel_brief = build_fuel_brief(db, m["id"], m.get("model_code") or "", fuel_price_rub_l, snap)
        photos_by_slot = fleet_photos_by_slot_map(fleet.get("photos"))
        card = {
            **m,
            "model_name": fleet.get("name") or m.get("model_name"),
            "plate_number": fleet.get("plate_number") or m.get("plate_number"),
            "image": model.get("image", "gazelle_nn.svg"),
            "snapshot": snap,
            "latest_mileage": latest,
            "maintenance_brief": maint,
            "fuel_brief": fuel_brief,
            "regulation_widget": regulation_widget,
            "fleet_schedule": fleet_schedule,
            "fleet_vehicle_id": fv_id,
            "fleet_photos_by_slot": photos_by_slot,
            "fleet_photos_ordered": fleet_photos_ordered_files(photos_by_slot),
        }
        cards.append(card)
    return cards


def build_fleet_preview_filler_cards(
    db,
    fuel_price_rub_l: float,
    *,
    period_config: dict,
    need: int,
    used_ids: set[int],
) -> list[dict]:
    """Временные карточки для вёрстки Автопарка, пока в БД мало машин."""
    period_label = format_period_label(
        datetime.now().year,
        datetime.now().month,
        int(period_config.get("day_from", 1)),
        int(period_config.get("day_to", 0)),
    )
    out: list[dict] = []
    for demo in _FLEET_PREVIEW_VEHICLES:
        if len(out) >= need:
            break
        if demo["id"] in used_ids:
            continue
        model_code = demo["model_code"]
        model = VEHICLE_MODELS.get(model_code, {})
        snap = {
            "period_label": period_label,
            "year_month": datetime.now().strftime("%Y-%m"),
            "km": demo["snapshot"]["km"],
            "amount_rub": demo["snapshot"]["amount_rub"],
            "fill_count": demo["snapshot"]["fill_count"],
            "est_liters": None,
            "actual_l100": demo["snapshot"]["actual_l100"],
            "factory_l100": model.get("factory_l100"),
            "norm_pct": None,
            "km_bar_pct": 0,
            "fuel_bar_pct": 0,
        }
        mileage = demo["latest_mileage"]
        last_oil = demo["last_oil_km"]
        maint = build_maintenance_brief(mileage, model_code, last_oil)
        out.append(
            {
                "id": demo["id"],
                "last_name": demo["last_name"],
                "first_name": demo["first_name"],
                "middle_name": demo["middle_name"],
                "model_code": model_code,
                "model_name": model.get("name"),
                "plate_number": demo["plate_number"],
                "has_vehicle": True,
                "is_preview": True,
                "image": model.get("image", ""),
                "snapshot": snap,
                "latest_mileage": mileage,
                "mileage_display": demo["mileage_display"],
                "maintenance_brief": maint,
                "fuel_brief": build_fuel_brief(db, demo["id"], model_code, fuel_price_rub_l, snap),
                "regulation_widget": build_regulation_widget_preview(maint),
            }
        )
    return out


def apply_fleet_grid_preview(card: dict, db, fuel_price_rub_l: float) -> dict:
    """Подставляет реалистичные тестовые значения для предпросмотра вёрстки."""
    model_code = card.get("model_code") or ""
    snap = {**card["snapshot"], **_FLEET_PREVIEW["snapshot"]}
    snap["period_label"] = card["snapshot"].get("period_label", "")
    if snap.get("actual_l100") and snap.get("factory_l100"):
        snap["norm_pct"] = min(150, int(round(float(snap["actual_l100"]) / float(snap["factory_l100"]) * 100)))
    snap["km_bar_pct"] = min(100, int((snap.get("km") or 0) / 800 * 100)) if snap.get("km") else 0
    snap["fuel_bar_pct"] = min(100, int((snap.get("amount_rub") or 0) / 25000 * 100)) if snap.get("amount_rub") else 0
    mileage = _FLEET_PREVIEW["latest_mileage"]
    last_oil = _FLEET_PREVIEW["last_oil_km"]
    maint = build_maintenance_brief(mileage, model_code, last_oil)
    return {
        **card,
        "snapshot": snap,
        "latest_mileage": mileage,
        "mileage_display": "127 000",
        "maintenance_brief": maint,
        "fuel_brief": build_fuel_brief(db, card["id"], model_code, fuel_price_rub_l, snap),
        "regulation_widget": build_regulation_widget_preview(maint),
    }
