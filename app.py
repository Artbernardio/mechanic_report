import os
import json
import re
import sqlite3
import hashlib
import tempfile
import smtplib
from io import BytesIO
from datetime import datetime, timedelta
from email.message import EmailMessage

from flask import (
    Flask, render_template, request, redirect,
    url_for, send_file, send_from_directory, jsonify, session, g, flash
)
from markupsafe import Markup, escape
from jinja2 import pass_eval_context
from xhtml2pdf import pisa
import pandas as pd
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

try:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        Image,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfgen import canvas
    from pypdf import PdfReader, PdfWriter
except Exception:
    pdfmetrics = None
    TTFont = None

# ----------------- базовая настройка -----------------

app = Flask(__name__)
app.secret_key = "change_this_secret_key"  # замените на свой

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SHARED_DIR = os.path.join(BASE_DIR, "shared")
DATA_DIR = os.path.join(BASE_DIR, "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_PHOTOS_DIR = os.path.join(DATA_DIR, "uploads", "photos")
UPLOAD_FLEET_PHOTOS_DIR = os.path.join(DATA_DIR, "uploads", "fleet_photos")
UPLOAD_MECHANIC_INFO_DIR = os.path.join(DATA_DIR, "uploads", "mechanic_info")
UPLOAD_FUEL_REPORTS_DIR = os.path.join(DATA_DIR, "uploads", "fuel_reports")
ORG_FILE = os.path.join(DATA_DIR, "organizations.json")
REPORTS_FILE = os.path.join(DATA_DIR, "reports.xlsx")
DB_PATH = os.path.join(DATA_DIR, "app.db")

# Полоса с адресом организации внизу шапки PDF.
# При необходимости замените текст на фактический адрес.
COMPANY_ADDRESS_LINE = (
    'ООО "АльфаСкладСервис" 105118, г. Москва, ш. Энтузиастов, дом 34, этаж под., '
    'пом.1, ком.34, офис №57, Телефон +7 (495) 960-97-16, kv@alfasklad.com'
)

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_MECHANIC_INFO_DIR, exist_ok=True)
os.makedirs(UPLOAD_FUEL_REPORTS_DIR, exist_ok=True)

import sys

if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)

from report_config import (  # noqa: E402
    can_edit_report,
    can_sign_report,
    edit_timer_remaining,
    filter_work_types,
    format_edit_timer,
    form_field_limits,
    get_company_meta,
    get_pdf_layout,
    load_report_config,
    normalize_company_code,
    pdf_filename_suffix,
    sanitize_materials,
    sanitize_step1_fields,
    template_filename,
    is_valid_date_not_future,
    is_valid_year_not_future,
    work_types_list,
)

# ----------------- работа с БД (SQLite) -----------------


def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    cur = db.cursor()

    # Пользователи: сотрудники и администраторы
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            last_name TEXT,
            first_name TEXT,
            middle_name TEXT,
            position TEXT,
            role TEXT NOT NULL DEFAULT 'MECHANIC',
            is_approved INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )

    # Отчёты (генерации PDF), привязанные к пользователю
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id TEXT NOT NULL,
            seq_num INTEGER,
            company_code TEXT,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            printed_at TEXT,
            opened_at TEXT,
            date TEXT,
            organization TEXT,
            equipment_type TEXT,
            serial_number TEXT,
            internal_number TEXT,
            location TEXT,
            engine_hours TEXT,
            year TEXT,
            work_types TEXT,
            work_description TEXT,
            recommendations TEXT,
            materials_json TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    # Миграция: добавляем колонку date, если таблица уже существовала
    cur.execute("PRAGMA table_info(reports)")
    existing_cols = {row[1] for row in cur.fetchall()}
    if "date" not in existing_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN date TEXT")
    if "seq_num" not in existing_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN seq_num INTEGER")
    if "company_code" not in existing_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN company_code TEXT")
    if "printed_at" not in existing_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN printed_at TEXT")
    if "opened_at" not in existing_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN opened_at TEXT")
    if "is_signed" not in existing_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN is_signed INTEGER NOT NULL DEFAULT 0")
    if "signed_at" not in existing_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN signed_at TEXT")
    if "signed_by" not in existing_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN signed_by INTEGER")
    if "client_local_id" not in existing_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN client_local_id TEXT")
    if "signature_verified" not in existing_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN signature_verified INTEGER NOT NULL DEFAULT 0")
    if "signature_verified_at" not in existing_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN signature_verified_at TEXT")
    if "signature_verify_detail" not in existing_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN signature_verify_detail TEXT")
    if "signed_scan_path" not in existing_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN signed_scan_path TEXT")

    # ----------------- Mind map (визуальное ТО/ремонт) -----------------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mindmap_equipment_schedule (
            equipment_key TEXT PRIMARY KEY,
            organization TEXT NOT NULL,
            equipment_type TEXT,
            serial_number TEXT NOT NULL,
            last_auto_date_iso TEXT,
            interval_days INTEGER,
            updated_at TEXT NOT NULL
        )
        """
    )

    # Миграция: заполняем seq_num для старых отчётов, если он пуст
    cur.execute("SELECT DISTINCT user_id FROM reports WHERE seq_num IS NULL OR seq_num = 0")
    user_ids = [row[0] for row in cur.fetchall()]
    for uid in user_ids:
        # текущий максимум для пользователя
        cur.execute("SELECT COALESCE(MAX(seq_num), 0) FROM reports WHERE user_id = ?", (uid,))
        start = int(cur.fetchone()[0] or 0) + 1
        # старые отчёты без seq_num — в порядке создания
        cur.execute(
            """
            SELECT id FROM reports
            WHERE user_id = ? AND (seq_num IS NULL OR seq_num = 0)
            ORDER BY datetime(created_at) ASC, id ASC
            """,
            (uid,),
        )
        for row in cur.fetchall():
            cur.execute(
                "UPDATE reports SET seq_num = ? WHERE id = ?",
                (start, row[0]),
            )
            start += 1

    # Миграция: фото пользователя (путь к файлу)
    cur.execute("PRAGMA table_info(users)")
    user_cols = {row[1] for row in cur.fetchall()}
    if "photo_path" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN photo_path TEXT")
    if "max_user_id" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN max_user_id TEXT")

    cur.execute("PRAGMA table_info(reports)")
    report_cols = {row[1] for row in cur.fetchall()}
    if "yandex_scan_path" not in report_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN yandex_scan_path TEXT")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS equipment_unit_meta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization TEXT NOT NULL DEFAULT '',
            serial_number TEXT NOT NULL,
            battery_serial TEXT NOT NULL DEFAULT '',
            extra_notes TEXT NOT NULL DEFAULT '',
            previous_organizations TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL,
            UNIQUE(organization, serial_number)
        )
        """
    )
    cur.execute("PRAGMA table_info(equipment_unit_meta)")
    eq_meta_cols = {row[1] for row in cur.fetchall()}
    if "previous_organizations" not in eq_meta_cols:
        cur.execute(
            "ALTER TABLE equipment_unit_meta ADD COLUMN previous_organizations TEXT NOT NULL DEFAULT '[]'"
        )
    # Пароль режима «Переезд» (видит/меняет только SUPER_ADMIN)
    cur.execute("SELECT value FROM app_settings WHERE key = ?", ("equipment_move_password",))
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            ("equipment_move_password", "Переезд2026"),
        )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            invited_role TEXT NOT NULL DEFAULT 'ADMIN',
            status TEXT NOT NULL DEFAULT 'SENT',
            invited_by INTEGER,
            created_at TEXT NOT NULL,
            sent_at TEXT,
            error_text TEXT
        )
        """
    )
    cur.execute("PRAGMA table_info(admin_invites)")
    invite_cols = {row[1] for row in cur.fetchall()}
    if "sent_at" not in invite_cols:
        cur.execute("ALTER TABLE admin_invites ADD COLUMN sent_at TEXT")
    if "error_text" not in invite_cols:
        cur.execute("ALTER TABLE admin_invites ADD COLUMN error_text TEXT")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS device_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            used_at TEXT,
            device_label TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    # Таблица системных настроек (в т.ч. Яндекс.Диск)
    admin_login = "admin"
    cur.execute("SELECT id FROM users WHERE login = ?", (admin_login,))
    if cur.fetchone() is None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO users (
                login, password_hash, last_name, first_name, middle_name,
                position, role, is_approved, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                admin_login,
                generate_password_hash("admin"),
                "Администратор",
                "",
                "",
                "Администратор системы",
                "SUPER_ADMIN",
                1,
                now,
            ),
        )
    else:
        # Миграция существующего главного админа
        cur.execute(
            "UPDATE users SET role = 'SUPER_ADMIN' WHERE login = 'admin' AND role = 'ADMIN'"
        )
    # Миграция старого названия роли механика
    cur.execute("UPDATE users SET role = 'MECHANIC' WHERE role = 'EMPLOYEE'")

    db.commit()

    # Папка для загруженных фото пользователей
    os.makedirs(UPLOAD_PHOTOS_DIR, exist_ok=True)
    os.makedirs(UPLOAD_FLEET_PHOTOS_DIR, exist_ok=True)
    os.makedirs(UPLOAD_FUEL_REPORTS_DIR, exist_ok=True)

    from vehicle_fleet import init_vehicle_fleet_schema

    init_vehicle_fleet_schema(db)


# Инициализируем БД при запуске приложения (замена before_first_request)
with app.app_context():
    init_db()


def create_staff_user(
    login,
    password,
    last_name,
    first_name,
    middle_name,
    position,
    role="MECHANIC",
    is_approved=0,
    vehicle_model=None,
    vehicle_plate=None,
    fleet_vehicle_id=None,
):
    role_up = (role or "MECHANIC").strip().upper()
    if role_up not in STAFF_ROLE_DEFS:
        role_up = "MECHANIC"
    if not position:
        position = staff_role_position(role_up)
    db = get_db()
    cur = db.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        INSERT INTO users (
            login, password_hash, last_name, first_name, middle_name,
            position, role, is_approved, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            login,
            generate_password_hash(password),
            last_name,
            first_name,
            middle_name,
            position,
            role_up,
            1 if is_approved else 0,
            now,
        ),
    )
    user_id = cur.lastrowid
    db.commit()
    if role_up == "MECHANIC":
        from vehicle_fleet import assign_fleet_vehicle_to_user, assign_vehicle

        if fleet_vehicle_id:
            assign_fleet_vehicle_to_user(db, user_id, int(fleet_vehicle_id))
        elif vehicle_model and vehicle_plate:
            assign_vehicle(db, user_id, vehicle_model, vehicle_plate)
    return user_id


def create_user(
    login,
    password,
    last_name,
    first_name,
    middle_name,
    position,
    vehicle_model=None,
    vehicle_plate=None,
):
    return create_staff_user(
        login=login,
        password=password,
        last_name=last_name,
        first_name=first_name,
        middle_name=middle_name,
        position=position,
        role="MECHANIC",
        is_approved=0,
        vehicle_model=vehicle_model,
        vehicle_plate=vehicle_plate,
    )


def get_user_by_login(login):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE login = ?", (login,))
    return cur.fetchone()


def get_user_by_id(user_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return cur.fetchone()


def _user_as_dict(user) -> dict:
    if user is None:
        return {}
    if isinstance(user, dict):
        return user
    return dict(user)


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(user_id)


def format_short_name(last_name: str | None, first_name: str | None, middle_name: str | None) -> str:
    """
    Возвращает ФИО в формате: 'Фамилия И.О.'
    Если каких‑то частей нет, использует только доступные.
    """
    last = (last_name or "").strip()
    first = (first_name or "").strip()
    middle = (middle_name or "").strip()

    initials = ""
    if first:
        initials += f"{first[0].upper()}."
    if middle:
        initials += f"{middle[0].upper()}."

    full = (last + " " + initials).strip()
    return full or first or middle or last


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 100 in (11, 12, 13, 14):
        return many
    if n % 10 == 1:
        return one
    if n % 10 in (2, 3, 4):
        return few
    return many


def reports_word(n: int) -> str:
    return _plural_ru(n, "отчёт", "отчёта", "отчётов")


def is_admin_user(user) -> bool:
    return bool(user and user["role"] in ("SUPER_ADMIN", "ADMIN"))


def is_super_admin(user) -> bool:
    return bool(user and user["role"] == "SUPER_ADMIN")


def is_lead_manager_user(user) -> bool:
    return bool(user and user["role"] == "LEAD_MANAGER")


def is_manager_user(user) -> bool:
    """Обычный менеджер (без раздела «Сотрудники»)."""
    return bool(user and user["role"] == "MANAGER")


def is_any_manager_user(user) -> bool:
    return bool(user and user["role"] in ("MANAGER", "LEAD_MANAGER"))


def is_staff_user(user) -> bool:
    return bool(user and (is_admin_user(user) or is_any_manager_user(user)))


def can_access_admin_panel(user) -> bool:
    """Админка с боковым меню: админы и ведущий менеджер."""
    return bool(user and (is_admin_user(user) or is_lead_manager_user(user)))


def can_manage_users(user) -> bool:
    return bool(user and (is_admin_user(user) or is_lead_manager_user(user)))


def can_configure_integrations(user) -> bool:
    """Яндекс.Диск и бот MAX — только гл. администратор."""
    return is_super_admin(user)


def can_manage_backups(user) -> bool:
    return is_super_admin(user)


def is_mechanic_user(user) -> bool:
    # EMPLOYEE оставляем для обратной совместимости старых записей
    return bool(user and user["role"] in ("MECHANIC", "EMPLOYEE"))


STAFF_ROLE_DEFS = {
    "MECHANIC": {
        "title": "Механик",
        "position": "Механик",
        "description": (
            "Доступ только к своему аккаунту. К админке доступа нет."
        ),
    },
    "MANAGER": {
        "title": "Менеджер",
        "position": "Менеджер",
        "description": (
            "Разделы: отчёты механиков, блокноты, автопарк, каталог техники. "
            "Нет доступа к настройкам и сотрудникам. Можно переключаться в личный аккаунт."
        ),
    },
    "LEAD_MANAGER": {
        "title": "Ведущий менеджер",
        "position": "Ведущий менеджер",
        "description": (
            "То же, что у менеджера, плюс полный доступ к разделу «Сотрудники»: "
            "создание и редактирование сотрудников и машин."
        ),
    },
    "ADMIN": {
        "title": "Админ",
        "position": "Администратор",
        "description": (
            "Полный доступ, кроме создания бэкапов, подключения к Яндекс.Диску и боту MAX."
        ),
    },
    "SUPER_ADMIN": {
        "title": "Гл. администратор",
        "position": "Главный администратор",
        "description": "Абсолютно полный доступ.",
    },
}


def staff_role_title(user) -> str:
    if not user:
        return ""
    role = user["role"]
    meta = STAFF_ROLE_DEFS.get(role)
    if meta:
        return meta["title"]
    return "Механик"


def staff_role_position(role: str) -> str:
    meta = STAFF_ROLE_DEFS.get((role or "").strip().upper())
    return meta["position"] if meta else "Механик"


def creatable_staff_roles(actor) -> list[str]:
    """Какие роли может назначить текущий пользователь при создании."""
    if is_super_admin(actor):
        return ["MECHANIC", "MANAGER", "LEAD_MANAGER", "ADMIN", "SUPER_ADMIN"]
    if is_admin_user(actor):
        return ["MECHANIC", "MANAGER", "LEAD_MANAGER"]
    if is_lead_manager_user(actor):
        return ["MECHANIC", "MANAGER"]
    return []


def can_access_admin_section(user, section: str) -> bool:
    section = (section or "").strip().lower()
    if not can_access_admin_panel(user):
        return False
    if is_super_admin(user) or user["role"] == "ADMIN":
        return True
    if is_lead_manager_user(user):
        return section in ("", "reports", "notebooks", "autos", "equipment", "users")
    return False


def staff_access_label(user) -> str:
    """Подпись роли для шапки staff-панели."""
    if not user:
        return ""
    position = (user["position"] or "").strip().lower()
    if "ген" in position and "директор" in position:
        return "ген. директор"
    role = user["role"]
    if role == "SUPER_ADMIN":
        return "гл. администратор"
    if role == "ADMIN":
        return "админ"
    if role == "LEAD_MANAGER":
        return "ведущий менеджер"
    if role == "MANAGER":
        return "менеджер"
    return staff_role_title(user).lower()


STAFF_ADMIN_SECTION_TITLES = {
    "reports": "Отчёты механиков",
    "notebooks": "Блокноты механиков",
    "autos": "Автопарк",
    "equipment": "Каталог техники",
    "settings": "Настройки",
    "users": "Сотрудники",
}


def staff_home_endpoint(user) -> str:
    if can_access_admin_panel(user):
        return "admin_dashboard"
    if is_manager_user(user):
        return "manager_dashboard"
    return "account"


def list_pending_users():
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT * FROM users
        WHERE is_approved = 0 AND role IN ('MECHANIC', 'EMPLOYEE')
        ORDER BY created_at DESC
        """
    )
    return cur.fetchall()


def list_all_users():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM users ORDER BY created_at DESC")
    return cur.fetchall()


def list_mechanics_with_stats():
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT
            u.*,
            COUNT(r.id) AS total_reports,
            SUM(CASE WHEN r.id IS NOT NULL AND r.opened_at IS NOT NULL THEN 1 ELSE 0 END) AS opened_reports,
            SUM(CASE WHEN r.id IS NOT NULL AND COALESCE(r.is_signed, 0) = 0 THEN 1 ELSE 0 END) AS unsigned_reports,
            SUM(CASE WHEN r.id IS NOT NULL AND r.opened_at IS NULL THEN 1 ELSE 0 END) AS ungenerated_reports
        FROM users u
        LEFT JOIN reports r ON r.user_id = u.id
        WHERE u.role IN ('MECHANIC', 'EMPLOYEE')
        GROUP BY u.id
        ORDER BY u.last_name, u.first_name, u.middle_name
        """
    )
    return cur.fetchall()


def list_equipment_catalog(
    *,
    serial: str = "",
    organization: str = "",
    location: str = "",
    limit: int = 200,
):
    """Уникальная техника из отчётов + счётчики."""
    db = get_db()
    cur = db.cursor()
    clauses = [
        "r.serial_number IS NOT NULL",
        "TRIM(r.serial_number) != ''",
    ]
    params: list = []
    if serial.strip():
        clauses.append("UPPER(r.serial_number) LIKE UPPER(?)")
        params.append(f"%{serial.strip()}%")
    if organization.strip():
        clauses.append("r.organization LIKE ?")
        params.append(f"%{organization.strip()}%")
    if location.strip():
        clauses.append("r.location LIKE ?")
        params.append(f"%{location.strip()}%")
    where = " AND ".join(clauses)
    cur.execute(
        f"""
        SELECT
            r.organization,
            r.equipment_type,
            r.serial_number,
            r.location,
            COUNT(*) AS report_count,
            MAX(r.date) AS last_report_date,
            MAX(r.created_at) AS last_created_at,
            SUM(CASE WHEN COALESCE(r.signature_verified, 0) = 1 THEN 1 ELSE 0 END) AS verified_count
        FROM reports r
        WHERE {where}
        GROUP BY r.organization, r.equipment_type, r.serial_number, r.location
        ORDER BY datetime(last_created_at) DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    return cur.fetchall()


def get_latest_equipment_report(
    *,
    serial: str = "",
    organization: str = "",
    location: str = "",
    offset: int = 0,
):
    """Последний отчёт по найденной технике с данными механика."""
    db = get_db()
    cur = db.cursor()
    clauses = [
        "r.serial_number IS NOT NULL",
        "TRIM(r.serial_number) != ''",
    ]
    params: list = []
    if serial.strip():
        # Для блока "последний отчёт" используем строгое совпадение S/N.
        # Пользователь вводит S/N (в т.ч. может быть с префиксом '#').
        sn_exact = serial.strip()
        while sn_exact and sn_exact[0] in ["#", "№", " "]:
            sn_exact = sn_exact[1:]
        clauses.append("UPPER(r.serial_number) = UPPER(?)")
        params.append(sn_exact)
    if organization.strip():
        clauses.append("r.organization LIKE ?")
        params.append(f"%{organization.strip()}%")
    if location.strip():
        clauses.append("r.location LIKE ?")
        params.append(f"%{location.strip()}%")
    where = " AND ".join(clauses)
    cur.execute(
        f"""
        SELECT
            r.serial_number,
            r.equipment_type,
            r.location,
            r.date,
            r.created_at,
            r.work_types,
            r.recommendations,
            u.position,
            u.last_name,
            u.first_name,
            u.middle_name
        FROM reports r
        LEFT JOIN users u ON u.id = r.user_id
        WHERE {where}
        ORDER BY
            CASE WHEN COALESCE(r.date, '') != '' THEN r.date ELSE r.created_at END DESC,
            datetime(r.created_at) DESC,
            r.id DESC
        LIMIT 1
        OFFSET ?
        """,
        [*params, max(0, int(offset))],
    )
    return cur.fetchone()


def list_equipment_report_history(
    *,
    serial: str = "",
    organization: str = "",
    location: str = "",
    limit: int = 5,
    offset: int = 2,
):
    """Предпоследние отчёты по найденной технике (в виде краткой истории)."""
    db = get_db()
    cur = db.cursor()
    clauses = [
        "r.serial_number IS NOT NULL",
        "TRIM(r.serial_number) != ''",
    ]
    params: list = []

    if serial.strip():
        sn_exact = serial.strip()
        while sn_exact and sn_exact[0] in ["#", "№", " "]:
            sn_exact = sn_exact[1:]
        clauses.append("UPPER(r.serial_number) = UPPER(?)")
        params.append(sn_exact)

    if organization.strip():
        clauses.append("r.organization LIKE ?")
        params.append(f"%{organization.strip()}%")

    if location.strip():
        clauses.append("r.location LIKE ?")
        params.append(f"%{location.strip()}%")

    where = " AND ".join(clauses)
    cur.execute(
        f"""
        SELECT
            r.date,
            r.created_at,
            u.position,
            u.last_name,
            u.first_name,
            u.middle_name
        FROM reports r
        LEFT JOIN users u ON u.id = r.user_id
        WHERE {where}
        ORDER BY
            CASE WHEN COALESCE(r.date, '') != '' THEN r.date ELSE r.created_at END DESC,
            datetime(r.created_at) DESC,
            r.id DESC
        LIMIT ?
        OFFSET ?
        """,
        (*params, int(limit), int(offset)),
    )
    rows = cur.fetchall()
    out = []
    for row in rows:
        date_disp = _format_date_dd_mm_yy(row["date"] or row["created_at"])
        mech = format_short_name(row["last_name"], row["first_name"], row["middle_name"])
        out.append(
            {
                "date_display": date_disp
                or (row["date"] or row["created_at"] or "—")[:10],
                "mechanic_display": mech or "Механик",
            }
        )
    return out


def _unique_sorted_labels(values) -> list[str]:
    """Уникальные подписи без дубликатов (без учёта регистра), стабильный регистр."""
    by_key: dict[str, str] = {}
    for raw in values or []:
        text = str(raw or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key not in by_key:
            by_key[key] = text
    return sorted(by_key.values(), key=lambda s: s.casefold())


def list_equipment_catalog_organizations() -> list[str]:
    """Список компаний для выпадающего фильтра: база + отчёты, без дубликатов."""
    names: list[str] = []
    for c in load_counterparties():
        name = str(c.get("name") or "").strip()
        if name:
            names.append(name)
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT DISTINCT TRIM(organization) AS organization
        FROM reports
        WHERE organization IS NOT NULL AND TRIM(organization) != ''
        """
    )
    for row in cur.fetchall():
        name = (row["organization"] or "").strip()
        if name:
            names.append(name)
    return _unique_sorted_labels(names)


def list_equipment_catalog_locations(organization: str = "") -> list[str]:
    """Места эксплуатации: база контрагентов + отчёты, без дубликатов."""
    org = (organization or "").strip()
    locs: list[str] = []
    for row in load_counterparties():
        name = str(row.get("name") or "").strip()
        if org and name.casefold() != org.casefold():
            continue
        for loc in row.get("locations") or []:
            text = str(loc or "").strip()
            if text:
                locs.append(text)
    db = get_db()
    cur = db.cursor()
    if org:
        cur.execute(
            """
            SELECT DISTINCT TRIM(location) AS location
            FROM reports
            WHERE location IS NOT NULL AND TRIM(location) != ''
              AND TRIM(organization) = ?
            """,
            (org,),
        )
    else:
        cur.execute(
            """
            SELECT DISTINCT TRIM(location) AS location
            FROM reports
            WHERE location IS NOT NULL AND TRIM(location) != ''
            """
        )
    for row in cur.fetchall():
        text = (row["location"] or "").strip()
        if text:
            locs.append(text)
    return _unique_sorted_labels(locs)


def equipment_catalog_page_context(req) -> dict:
    """Контекст каталога техники: результаты только после нажатия «Найти»."""
    serial = req.args.get("sn", "").strip()[:55]
    organization = req.args.get("org", "").strip()[:55]
    location = req.args.get("loc", "").strip()[:55]
    searched = req.args.get("search") == "1"
    has_criteria = bool(serial or organization or location)
    search_empty_error = searched and not has_criteria
    if search_empty_error:
        searched = False
    rows = (
        list_equipment_catalog(
            serial=serial,
            organization=organization,
            location=location,
        )
        if searched and has_criteria
        else []
    )
    latest_report = (
        get_latest_equipment_report(
            serial=serial,
            organization=organization,
            location=location,
        )
        if searched and has_criteria and rows
        else None
    )
    previous_report = (
        get_latest_equipment_report(
            serial=serial,
            organization=organization,
            location=location,
            offset=1,
        )
        if searched and has_criteria and rows
        else None
    )
    previous_history = (
        list_equipment_report_history(
            serial=serial,
            organization=organization,
            location=location,
            limit=5,
            offset=2,
        )
        if searched and has_criteria and rows
        else []
    )
    latest_work_types = []
    latest_author = ""
    latest_position = ""
    latest_report_date = ""
    previous_work_types = []
    previous_author = ""
    previous_position = ""
    previous_report_date = ""
    if latest_report:
        latest_work_types = [
            item.strip()
            for item in str(latest_report["work_types"] or "").split(",")
            if item.strip()
        ]
        latest_author = format_short_name(
            latest_report["last_name"],
            latest_report["first_name"],
            latest_report["middle_name"],
        )
        latest_position = (latest_report["position"] or "").strip()
        latest_report_date = (
            (latest_report["date"] or "").strip()
            or ((latest_report["created_at"] or "")[:10])
        )
    if previous_report:
        previous_work_types = [
            item.strip()
            for item in str(previous_report["work_types"] or "").split(",")
            if item.strip()
        ]
        previous_author = format_short_name(
            previous_report["last_name"],
            previous_report["first_name"],
            previous_report["middle_name"],
        )
        previous_position = (previous_report["position"] or "").strip()
        previous_report_date = (
            (previous_report["date"] or "").strip()
            or ((previous_report["created_at"] or "")[:10])
        )
    sync_counterparties_from_reports()
    org_options = list_equipment_catalog_organizations()
    # Для выпадающего списка мест — полная база (отчёты + XML), без дубликатов
    loc_options = list_equipment_catalog_locations("")
    return {
        "equipment_rows": rows,
        "equipment_filters": {
            "sn": serial,
            "org": organization,
            "loc": location,
        },
        "equipment_org_options": org_options,
        "equipment_loc_options": loc_options,
        "equipment_searched": searched,
        "equipment_search_empty_error": search_empty_error,
        "equipment_latest_report": latest_report,
        "equipment_latest_work_types": latest_work_types,
        "equipment_latest_author": latest_author,
        "equipment_latest_position": latest_position,
        "equipment_latest_report_date": latest_report_date,
        "equipment_previous_report": previous_report,
        "equipment_previous_work_types": previous_work_types,
        "equipment_previous_author": previous_author,
        "equipment_previous_position": previous_position,
        "equipment_previous_report_date": previous_report_date,
        "equipment_previous_history": previous_history,
    }


def _normalize_serial_token(serial: str) -> str:
    sn = (serial or "").strip()
    while sn and sn[0] in ["#", "№", " "]:
        sn = sn[1:]
    return sn.strip()


def _work_types_include(work_types: str | None, needle: str) -> bool:
    """Проверка, что в CSV work_types есть нужный тип (например «Аренда»)."""
    target = (needle or "").strip().lower()
    if not target:
        return False
    for part in str(work_types or "").split(","):
        if part.strip().lower() == target:
            return True
    return False


def _sql_work_types_include_arenda(alias: str = "r") -> str:
    """SQL-условие: в work_types есть «Аренда» (как отдельный тип).

    Не используем SQLite LOWER() — для кириллицы он не работает.
    """
    col = f"{alias}.work_types"
    return f"""(
        TRIM(COALESCE({col}, '')) = 'Аренда'
        OR TRIM(COALESCE({col}, '')) LIKE 'Аренда, %'
        OR TRIM(COALESCE({col}, '')) LIKE '%, Аренда'
        OR TRIM(COALESCE({col}, '')) LIKE '%, Аренда, %'
    )"""


def list_equipment_dashboard_groups(*, rental_only: bool = False) -> list[dict]:
    """Организации → серийные номера техники из отчётов.

    rental_only=True — только отчёты с типом работ «Аренда» (карта арендной техники).
    """
    db = get_db()
    cur = db.cursor()
    rental_filter = f" AND {_sql_work_types_include_arenda('r')}" if rental_only else ""
    rental_filter_x = f" AND {_sql_work_types_include_arenda('x')}" if rental_only else ""
    cur.execute(
        f"""
        SELECT
            TRIM(r.organization) AS organization,
            TRIM(r.serial_number) AS serial_number,
            (
                SELECT x.equipment_type
                FROM reports x
                WHERE TRIM(x.organization) = TRIM(r.organization)
                  AND UPPER(TRIM(x.serial_number)) = UPPER(TRIM(r.serial_number))
                  {rental_filter_x}
                ORDER BY
                    CASE WHEN COALESCE(x.date, '') != '' THEN x.date ELSE x.created_at END DESC,
                    datetime(x.created_at) DESC,
                    x.id DESC
                LIMIT 1
            ) AS equipment_type,
            (
                SELECT x.location
                FROM reports x
                WHERE TRIM(x.organization) = TRIM(r.organization)
                  AND UPPER(TRIM(x.serial_number)) = UPPER(TRIM(r.serial_number))
                  {rental_filter_x}
                ORDER BY
                    CASE WHEN COALESCE(x.date, '') != '' THEN x.date ELSE x.created_at END DESC,
                    datetime(x.created_at) DESC,
                    x.id DESC
                LIMIT 1
            ) AS location,
            COUNT(*) AS report_count,
            MAX(r.created_at) AS last_created_at
        FROM reports r
        WHERE r.organization IS NOT NULL AND TRIM(r.organization) != ''
          AND r.serial_number IS NOT NULL AND TRIM(r.serial_number) != ''
          {rental_filter}
        GROUP BY TRIM(r.organization), TRIM(r.serial_number)
        ORDER BY TRIM(r.organization) COLLATE NOCASE ASC, datetime(last_created_at) DESC
        """
    )
    groups: dict[str, list] = {}
    for row in cur.fetchall():
        org = row["organization"] or "—"
        groups.setdefault(org, []).append(
            {
                "serial_number": row["serial_number"],
                "equipment_type": row["equipment_type"] or "",
                "location": row["location"] or "",
                "report_count": int(row["report_count"] or 0),
            }
        )
    return [{"organization": org, "units": units} for org, units in groups.items()]


def get_equipment_unit_meta(organization: str, serial_number: str) -> dict:
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT battery_serial, extra_notes, previous_organizations, updated_at
        FROM equipment_unit_meta
        WHERE organization = ? AND UPPER(serial_number) = UPPER(?)
        LIMIT 1
        """,
        ((organization or "").strip(), _normalize_serial_token(serial_number)),
    )
    row = cur.fetchone()
    if not row:
        return {
            "battery_serial": "",
            "extra_notes": "",
            "previous_organizations": [],
            "updated_at": "",
        }
    prev = []
    try:
        prev = json.loads(row["previous_organizations"] or "[]")
        if not isinstance(prev, list):
            prev = []
    except Exception:
        prev = []
    return {
        "battery_serial": row["battery_serial"] or "",
        "extra_notes": row["extra_notes"] or "",
        "previous_organizations": [str(x) for x in prev if str(x).strip()],
        "updated_at": row["updated_at"] or "",
    }


def upsert_equipment_unit_meta(
    organization: str,
    serial_number: str,
    *,
    battery_serial: str = "",
    extra_notes: str = "",
    previous_organizations: list | None = None,
) -> dict:
    db = get_db()
    cur = db.cursor()
    org = (organization or "").strip()
    sn = _normalize_serial_token(serial_number)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing = get_equipment_unit_meta(org, sn)
    prev = (
        previous_organizations
        if previous_organizations is not None
        else existing.get("previous_organizations") or []
    )
    prev_json = json.dumps(prev, ensure_ascii=False)
    cur.execute(
        """
        INSERT INTO equipment_unit_meta (
            organization, serial_number, battery_serial, extra_notes, previous_organizations, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(organization, serial_number) DO UPDATE SET
            battery_serial = excluded.battery_serial,
            extra_notes = excluded.extra_notes,
            previous_organizations = excluded.previous_organizations,
            updated_at = excluded.updated_at
        """,
        (
            org,
            sn,
            (battery_serial or "").strip(),
            (extra_notes or "").strip(),
            prev_json,
            now,
        ),
    )
    db.commit()
    return get_equipment_unit_meta(org, sn)


def relocate_equipment_unit(
    from_org: str,
    to_org: str,
    serial_number: str,
    *,
    rental_only: bool = False,
) -> dict:
    """Перенос единицы техники в другую организацию (отчёты + meta).

    rental_only=True — переносятся только отчёты с типом работ «Аренда».
    """
    src = (from_org or "").strip()
    dst = (to_org or "").strip()
    sn = _normalize_serial_token(serial_number)
    if not src or not dst or not sn:
        raise ValueError("organization/serial required")
    if src == dst:
        raise ValueError("same organization")

    db = get_db()
    cur = db.cursor()
    rental_where = ""
    if rental_only:
        rental_where = """
          AND (
            TRIM(COALESCE(work_types, '')) = 'Аренда'
            OR TRIM(COALESCE(work_types, '')) LIKE 'Аренда, %'
            OR TRIM(COALESCE(work_types, '')) LIKE '%, Аренда'
            OR TRIM(COALESCE(work_types, '')) LIKE '%, Аренда, %'
          )
        """
    cur.execute(
        f"""
        SELECT COUNT(*) FROM reports
        WHERE TRIM(organization) = ? AND UPPER(TRIM(serial_number)) = UPPER(?)
        {rental_where}
        """,
        (src, sn),
    )
    if int(cur.fetchone()[0] or 0) <= 0:
        raise ValueError("unit not found")

    old_meta = get_equipment_unit_meta(src, sn)
    prev = list(old_meta.get("previous_organizations") or [])
    if src not in prev:
        prev.append(src)

    cur.execute(
        f"""
        UPDATE reports
        SET organization = ?
        WHERE TRIM(organization) = ? AND UPPER(TRIM(serial_number)) = UPPER(?)
        {rental_where}
        """,
        (dst, src, sn),
    )

    # Meta: для арендной карты не трогаем, если у компании ещё есть «обычные» отчёты по этому S/N
    if rental_only:
        cur.execute(
            """
            SELECT COUNT(*) FROM reports
            WHERE TRIM(organization) = ? AND UPPER(TRIM(serial_number)) = UPPER(?)
            """,
            (src, sn),
        )
        still_has_reports = int(cur.fetchone()[0] or 0) > 0
        if not still_has_reports:
            cur.execute(
                """
                DELETE FROM equipment_unit_meta
                WHERE organization = ? AND UPPER(serial_number) = UPPER(?)
                """,
                (src, sn),
            )
        db.commit()
    else:
        cur.execute(
            """
            DELETE FROM equipment_unit_meta
            WHERE organization = ? AND UPPER(serial_number) = UPPER(?)
            """,
            (src, sn),
        )
        db.commit()

    dst_meta = get_equipment_unit_meta(dst, sn)
    merged_prev = list(dst_meta.get("previous_organizations") or [])
    for item in prev:
        if item not in merged_prev:
            merged_prev.append(item)
    battery = (old_meta.get("battery_serial") or dst_meta.get("battery_serial") or "").strip()
    notes = (old_meta.get("extra_notes") or dst_meta.get("extra_notes") or "").strip()

    upsert_equipment_unit_meta(
        dst,
        sn,
        battery_serial=battery,
        extra_notes=notes,
        previous_organizations=merged_prev,
    )

    card = get_equipment_unit_card(dst, sn)
    if not card:
        raise ValueError("relocate failed")
    return card


def _report_looks_like_to(work_types: str | None, work_description: str | None = None) -> bool:
    wt = (work_types or "").lower()
    desc = (work_description or "").lower()
    if "диагност" in wt or "гарант" in wt:
        return True
    if re.search(r"(^|[,;\s/])то([,;\s/]|$)", wt):
        return True
    if "т.о" in wt or "техническ" in desc or "обслуживан" in desc:
        return True
    return False


def get_equipment_unit_card(organization: str, serial_number: str) -> dict | None:
    """Карточка техники для дашборда: данные отчёта + meta + последнее ТО."""
    org = (organization or "").strip()
    sn = _normalize_serial_token(serial_number)
    if not sn:
        return None

    latest = get_latest_equipment_report(serial=sn, organization=org, location="")
    if not latest and org:
        latest = get_latest_equipment_report(serial=sn, organization="", location="")
    if not latest:
        return None

    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT
            r.date,
            r.created_at,
            r.work_types,
            r.work_description,
            r.recommendations,
            u.position,
            u.last_name,
            u.first_name,
            u.middle_name
        FROM reports r
        LEFT JOIN users u ON u.id = r.user_id
        WHERE UPPER(TRIM(r.serial_number)) = UPPER(?)
          AND (? = '' OR TRIM(r.organization) = ?)
        ORDER BY
            CASE WHEN COALESCE(r.date, '') != '' THEN r.date ELSE r.created_at END DESC,
            datetime(r.created_at) DESC,
            r.id DESC
        LIMIT 40
        """,
        (sn, org, org),
    )
    last_to = None
    for row in cur.fetchall():
        if _report_looks_like_to(row["work_types"], row["work_description"]):
            last_to = row
            break

    cur.execute(
        """
        SELECT organization, equipment_type, location, date, created_at, work_types, recommendations
        FROM reports
        WHERE UPPER(TRIM(serial_number)) = UPPER(?)
          AND (? = '' OR TRIM(organization) = ?)
        ORDER BY
            CASE WHEN COALESCE(date, '') != '' THEN date ELSE created_at END DESC,
            datetime(created_at) DESC,
            id DESC
        LIMIT 1
        """,
        (sn, org, org),
    )
    head = cur.fetchone()
    org_final = org or ((head["organization"] if head else "") or "")
    equipment_type = (head["equipment_type"] if head else "") or (latest["equipment_type"] or "")
    location = (head["location"] if head else "") or (latest["location"] or "")
    meta = get_equipment_unit_meta(org_final, sn)

    author = format_short_name(latest["last_name"], latest["first_name"], latest["middle_name"])
    visit_date = _format_date_dd_mm_yy(latest["date"] or latest["created_at"]) or (
        (latest["date"] or latest["created_at"] or "")[:10]
    )

    to_payload = None
    if last_to:
        to_payload = {
            "date": _format_date_dd_mm_yy(last_to["date"] or last_to["created_at"])
            or ((last_to["date"] or last_to["created_at"] or "")[:10]),
            "mechanic": format_short_name(
                last_to["last_name"], last_to["first_name"], last_to["middle_name"]
            )
            or "Механик",
            "position": (last_to["position"] or "").strip(),
            "work_types": last_to["work_types"] or "",
            "recommendations": last_to["recommendations"] or "",
        }

    return {
        "organization": org_final,
        "serial_number": sn,
        "equipment_type": equipment_type,
        "location": location,
        "battery_serial": meta["battery_serial"],
        "extra_notes": meta["extra_notes"],
        "previous_organizations": meta.get("previous_organizations") or [],
        "last_visit": {
            "date": visit_date,
            "mechanic": author or "Механик",
            "position": (latest["position"] or "").strip(),
            "work_types": latest["work_types"] or "",
            "recommendations": latest["recommendations"] or "",
        },
        "last_to": to_payload,
    }


def create_device_invite(user_id: int) -> str:
    import secrets

    db = get_db()
    cur = db.cursor()
    code = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10].upper()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        INSERT INTO device_invites (code, user_id, created_at)
        VALUES (?, ?, ?)
        """,
        (code, user_id, now),
    )
    db.commit()
    return code


def list_device_invites_for_user(user_id: int):
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT * FROM device_invites
        WHERE user_id = ?
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 20
        """,
        (user_id,),
    )
    return cur.fetchall()


def get_admin_overview_stats():
    """Устаревший алиас — используйте get_admin_dashboard_data."""
    dash = get_admin_dashboard_data()
    return {
        "mechanics_count": dash["mechanics_count"],
        "week_reports": dash["week_reports"],
        "week_signed": dash["week_signed"],
        "leader": dash["leaders_signed"][0] if dash["leaders_signed"] else None,
    }


_MONTHS_RU = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)

_MONTHS_RU_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


@app.context_processor
def inject_staff_ui():
    user = get_current_user()
    now = datetime.now()
    return {
        "staff_today_label": f"{now.day} {_MONTHS_RU_GENITIVE[now.month - 1]} {now.year} г.",
        "staff_access_label": staff_access_label(user),
        "staff_admin_section_titles": STAFF_ADMIN_SECTION_TITLES,
        "format_short_name": format_short_name,
        "can_manage_users": can_manage_users(user),
        "can_configure_integrations": can_configure_integrations(user),
        "can_manage_backups": can_manage_backups(user),
        "can_access_admin_panel": can_access_admin_panel(user),
        "is_super_admin_user": is_super_admin(user),
        "staff_role_defs": STAFF_ROLE_DEFS,
        "creatable_staff_roles": creatable_staff_roles(user),
    }


def _is_rental_organization(org: str | None) -> bool:
    o = (org or "").lower()
    return "аренд" in o or "rent" in o


def _serial_in_repair(last_signed: int, last_work_types: str | None) -> bool:
    if not int(last_signed or 0):
        return True
    wt = (last_work_types or "").lower()
    return "ремонт" in wt


def get_admin_dashboard_data(get_setting_fn=None) -> dict:
    """Стартовая панель админа: экосистема, техника, отчёты, лидеры."""
    db = get_db()
    cur = db.cursor()
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start_str = month_start.strftime("%Y-%m-%d %H:%M:%S")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    week_ago_str = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    cur.execute(
        "SELECT COUNT(*) FROM users WHERE role IN ('MECHANIC', 'EMPLOYEE') AND is_approved = 1"
    )
    mechanics_approved = int(cur.fetchone()[0] or 0)

    cur.execute(
        """
        SELECT COUNT(DISTINCT user_id) FROM device_invites
        WHERE used_at IS NOT NULL AND used_at != ''
        """
    )
    android_connected = int(cur.fetchone()[0] or 0)
    android_total = mechanics_approved

    gs = get_setting_fn or get_setting
    max_token = (gs("max_bot_token", "") or "").strip()
    bot_test = gs("bot_test_mode", "1") != "0"
    yandex_token = (gs("yandex_token", "") or "").strip()
    excel_on = gs("yandex_excel_enabled", "1") == "1"

    ecosystem = {
        "android_connected": android_connected,
        "android_total": android_total,
        "android_label": f"{android_connected} подключений из {android_total}",
        "max_bot": "тестовый режим" if max_token and bot_test else ("есть" if max_token else "нет"),
        "yandex_disk": "есть" if yandex_token else "нет",
        "recognizer": "работает, ошибок нет",
        "excel": "есть" if excel_on else "нет",
        "excel_hint": "поиск по хештегу (бот MAX) или серийному номеру (веб-кабинет)",
    }

    cur.execute(
        """
        SELECT
            r.serial_number,
            MAX(r.organization) AS organization,
            (SELECT COALESCE(x.is_signed, 0) FROM reports x
             WHERE x.serial_number = r.serial_number
             ORDER BY datetime(x.created_at) DESC, x.id DESC LIMIT 1) AS last_signed,
            (SELECT x.work_types FROM reports x
             WHERE x.serial_number = r.serial_number
             ORDER BY datetime(x.created_at) DESC, x.id DESC LIMIT 1) AS last_work
        FROM reports r
        WHERE r.serial_number IS NOT NULL AND TRIM(r.serial_number) != ''
        GROUP BY r.serial_number
        """
    )
    owned_total = owned_repair = rental_total = rental_repair = 0
    for row in cur.fetchall():
        rental = _is_rental_organization(row["organization"])
        repair = _serial_in_repair(int(row["last_signed"] or 0), row["last_work"])
        if rental:
            rental_total += 1
            if repair:
                rental_repair += 1
        else:
            owned_total += 1
            if repair:
                owned_repair += 1

    cur.execute(
        """
        SELECT COUNT(*) FROM reports r
        JOIN users u ON u.id = r.user_id
        WHERE u.role IN ('MECHANIC', 'EMPLOYEE') AND r.created_at >= ?
        """,
        (month_start_str,),
    )
    month_reports = int(cur.fetchone()[0] or 0)

    cur.execute(
        """
        SELECT COUNT(*) FROM reports r
        JOIN users u ON u.id = r.user_id
        WHERE u.role IN ('MECHANIC', 'EMPLOYEE') AND r.created_at >= ?
          AND COALESCE(r.is_signed, 0) = 0
        """,
        (month_start_str,),
    )
    month_unsigned = int(cur.fetchone()[0] or 0)

    cur.execute(
        """
        SELECT COUNT(*) FROM reports r
        JOIN users u ON u.id = r.user_id
        WHERE u.role IN ('MECHANIC', 'EMPLOYEE')
          AND substr(r.created_at, 1, 10) = ?
        """,
        (yesterday,),
    )
    yesterday_reports = int(cur.fetchone()[0] or 0)

    cur.execute(
        """
        SELECT COUNT(*) FROM reports r
        JOIN users u ON u.id = r.user_id
        WHERE u.role IN ('MECHANIC', 'EMPLOYEE')
          AND substr(r.created_at, 1, 10) = ?
          AND COALESCE(r.is_signed, 0) = 1
        """,
        (yesterday,),
    )
    yesterday_signed = int(cur.fetchone()[0] or 0)

    cur.execute(
        """
        SELECT COUNT(*) FROM reports r
        JOIN users u ON u.id = r.user_id
        WHERE u.role IN ('MECHANIC', 'EMPLOYEE') AND r.created_at >= ?
        """,
        (week_ago_str,),
    )
    week_reports = int(cur.fetchone()[0] or 0)

    cur.execute(
        """
        SELECT COUNT(*) FROM reports r
        JOIN users u ON u.id = r.user_id
        WHERE u.role IN ('MECHANIC', 'EMPLOYEE')
          AND r.created_at >= ?
          AND COALESCE(r.is_signed, 0) = 1
        """,
        (week_ago_str,),
    )
    week_signed = int(cur.fetchone()[0] or 0)

    def _leader_query(order_expr: str, limit: int = 5):
        cur.execute(
            f"""
            SELECT
                u.id, u.last_name, u.first_name, u.middle_name,
                COUNT(r.id) AS report_count,
                SUM(CASE WHEN COALESCE(r.is_signed, 0) = 1 THEN 1 ELSE 0 END) AS signed_count,
                SUM(CASE WHEN COALESCE(r.signature_verified, 0) = 1 THEN 1 ELSE 0 END) AS verified_count
            FROM users u
            LEFT JOIN reports r ON r.user_id = u.id AND r.created_at >= ?
            WHERE u.role IN ('MECHANIC', 'EMPLOYEE') AND u.is_approved = 1
            GROUP BY u.id
            HAVING report_count > 0
            ORDER BY {order_expr}
            LIMIT ?
            """,
            (month_start_str, limit),
        )
        return cur.fetchall()

    leaders_signed = _leader_query("signed_count DESC, report_count DESC")
    leaders_reports = _leader_query("report_count DESC, signed_count DESC")
    leaders_verified = _leader_query("verified_count DESC, signed_count DESC")

    return {
        "mechanics_count": mechanics_approved,
        "week_reports": week_reports,
        "week_signed": week_signed,
        "ecosystem": ecosystem,
        "equipment_owned_total": owned_total,
        "equipment_owned_repair": owned_repair,
        "equipment_rental_total": rental_total,
        "equipment_rental_repair": rental_repair,
        "month_name": _MONTHS_RU[now.month - 1],
        "month_reports": month_reports,
        "month_unsigned": month_unsigned,
        "yesterday_reports": yesterday_reports,
        "yesterday_signed": yesterday_signed,
        "leaders_signed": leaders_signed,
        "leaders_reports": leaders_reports,
        "leaders_verified": leaders_verified,
    }


def list_reports_for_admin_user(user_id):
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT * FROM reports WHERE user_id = ? ORDER BY COALESCE(seq_num, id) DESC",
        (user_id,),
    )
    return cur.fetchall()


def list_managers():
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT * FROM users
        WHERE role IN ('MANAGER', 'LEAD_MANAGER')
        ORDER BY
          CASE role WHEN 'LEAD_MANAGER' THEN 0 ELSE 1 END,
          last_name, first_name, middle_name
        """
    )
    return cur.fetchall()


def enrich_staff_report_row(row) -> dict:
    """Дополняет строку отчёта полями для кабинета админа/менеджера."""
    r = dict(row)
    seq = int(r.get("seq_num") or r.get("id") or 0)
    comp = normalize_company_code(r.get("company_code"))
    suffix = pdf_filename_suffix(comp)
    created_at = r.get("created_at") or ""
    dmy = ""
    try:
        dpart = (r.get("date") or created_at[:10] or "").strip()
        if "." in dpart:
            d, m, y = dpart.split(".")
            dmy = f"{d}{m}{y}"
        elif "-" in dpart:
            y, m, d = dpart.split("-")
            dmy = f"{d}{m}{y}"
    except Exception:
        dmy = ""
    r["display_seq"] = seq
    r["gen_name"] = f"{seq:04d}_{dmy}_{suffix}.pdf" if dmy else f"{seq:04d}_{suffix}.pdf"
    r["is_generated"] = True
    r["is_signed"] = int(r.get("is_signed") or 0)
    r["signature_verified"] = int(r.get("signature_verified") or 0)
    return r


def get_mechanic_staff_kpis(user_id) -> dict:
    reports = list_reports_for_admin_user(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    total = len(reports)
    signed = sum(1 for r in reports if int(r["is_signed"] or 0))
    verified = sum(1 for r in reports if int(r["signature_verified"] or 0))
    opened = sum(1 for r in reports if r["opened_at"])
    today_reports = sum(1 for r in reports if (r["created_at"] or "")[:10] == today)
    return {
        "total_reports": total,
        "signed_reports": signed,
        "verified_reports": verified,
        "opened_reports": opened,
        "today_reports": today_reports,
        "unsigned_reports": total - signed,
    }


def set_report_signed(report_id_int, signed: bool, admin_id: int):
    db = get_db()
    cur = db.cursor()
    if signed:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "UPDATE reports SET is_signed = 1, signed_at = ?, signed_by = ? WHERE id = ?",
            (now, admin_id, report_id_int),
        )
    else:
        cur.execute(
            "UPDATE reports SET is_signed = 0, signed_at = NULL, signed_by = NULL WHERE id = ?",
            (report_id_int,),
        )
    db.commit()


def set_report_verified(report_id_int: int, verified: bool, admin_id: int):
    db = get_db()
    cur = db.cursor()
    if verified:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            UPDATE reports
            SET signature_verified = 1,
                signature_verified_at = ?,
                signature_verify_detail = ?
            WHERE id = ?
            """,
            (
                now,
                json.dumps({"source": "admin", "admin_id": admin_id}, ensure_ascii=False),
                report_id_int,
            ),
        )
    else:
        cur.execute(
            """
            UPDATE reports
            SET signature_verified = 0,
                signature_verified_at = NULL,
                signature_verify_detail = NULL
            WHERE id = ?
            """,
            (report_id_int,),
        )
    db.commit()


def get_setting(key: str, default: str = "") -> str:
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
    row = cur.fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_setting(key: str, value: str):
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO app_settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    db.commit()


def list_admin_invites():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM admin_invites ORDER BY datetime(created_at) DESC, id DESC")
    return cur.fetchall()


def create_admin_invite(
    email: str,
    invited_by: int,
    invited_role: str = "ADMIN",
    status: str = "SENT",
    error_text: str = "",
):
    db = get_db()
    cur = db.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sent_at = now if status == "SENT" else None
    cur.execute(
        """
        INSERT INTO admin_invites (email, invited_role, status, invited_by, created_at, sent_at, error_text)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (email, invited_role, status, invited_by, now, sent_at, error_text),
    )
    db.commit()


def send_admin_invite_email(email_to: str) -> tuple[bool, str]:
    """Отправка приглашения второстепенному админу через SMTP."""
    host = get_setting("smtp_host", "").strip()
    port_raw = get_setting("smtp_port", "587").strip() or "587"
    username = get_setting("smtp_username", "").strip()
    password = get_setting("smtp_password", "").strip()
    sender = get_setting("smtp_from", "").strip() or username
    use_tls = get_setting("smtp_use_tls", "1").strip() == "1"

    if not host or not sender:
        return False, "SMTP не настроен: укажите host/from."

    try:
        port = int(port_raw)
    except Exception:
        return False, "Неверный SMTP-порт."

    msg = EmailMessage()
    msg["Subject"] = "Приглашение в админ-панель"
    msg["From"] = sender
    msg["To"] = email_to
    msg.set_content(
        "Вам выдан доступ второстепенного администратора в системе отчётов механиков.\n"
        "Пожалуйста, обратитесь к главному администратору для получения учётных данных."
    )

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            if use_tls:
                server.starttls()
            if username:
                server.login(username, password)
            server.send_message(msg)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def approve_user(user_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE users SET is_approved = 1 WHERE id = ?", (user_id,))
    db.commit()


def delete_user(user_id):
    db = get_db()
    cur = db.cursor()
    # сначала удаляем отчёты пользователя
    cur.execute("DELETE FROM reports WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()


def update_user(user_id, **kwargs):
    """Обновление полей пользователя (last_name, first_name, middle_name, position, role, is_approved, photo_path)."""
    allowed = {
        "last_name", "first_name", "middle_name", "position", "role",
        "is_approved", "photo_path", "password_hash", "max_user_id",
    }
    updates = []
    values = []
    for k, v in kwargs.items():
        if k in allowed and v is not None:
            updates.append(f"{k} = ?")
            values.append(v)
    if not updates:
        return
    values.append(user_id)
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "UPDATE users SET " + ", ".join(updates) + " WHERE id = ?",
        values,
    )
    db.commit()


def create_report(user_id, report_dict):
    db = get_db()
    cur = db.cursor()
    client_local_id = (report_dict.get("client_local_id") or "").strip()
    if client_local_id:
        cur.execute(
            "SELECT id FROM reports WHERE client_local_id = ? AND user_id = ?",
            (client_local_id, user_id),
        )
        if cur.fetchone():
            return
    cur.execute("SELECT COALESCE(MAX(seq_num), 0) FROM reports WHERE user_id = ?", (user_id,))
    next_seq = int(cur.fetchone()[0] or 0) + 1
    company_code = report_dict.get("company_code") or session.get("company_code") or "ALFASS"
    is_signed = 1 if report_dict.get("is_signed") else 0
    signed_at = report_dict.get("signed_at") if is_signed else None
    cur.execute(
        """
        INSERT INTO reports (
            report_id, seq_num, company_code, user_id, created_at, printed_at, date, organization, equipment_type,
            serial_number, internal_number, location, engine_hours, year,
            work_types, work_description, recommendations, materials_json,
            client_local_id, is_signed, signed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report_dict["report_id"],
            next_seq,
            company_code,
            user_id,
            report_dict["created_at"],
            None,
            report_dict.get("date", ""),
            report_dict.get("organization", ""),
            report_dict.get("equipment_type", ""),
            report_dict.get("serial_number", ""),
            report_dict.get("internal_number", ""),
            report_dict.get("location", ""),
            report_dict.get("engine_hours", ""),
            report_dict.get("year", ""),
            report_dict.get("work_types", ""),
            report_dict.get("work_description", ""),
            report_dict.get("recommendations", ""),
            report_dict.get("materials_json", ""),
            client_local_id or None,
            is_signed,
            signed_at,
        ),
    )
    db.commit()


def list_reports_for_user(user_id, year=None, month=None, day=None):
    db = get_db()
    cur = db.cursor()
    q = "SELECT * FROM reports WHERE user_id = ?"
    params = [user_id]
    if year:
        q += " AND substr(created_at, 1, 4) = ?"
        params.append(str(year))
    if month:
        q += " AND substr(created_at, 6, 2) = ?"
        params.append(f"{int(month):02d}")
    if day:
        q += " AND substr(created_at, 9, 2) = ?"
        params.append(f"{int(day):02d}")
    # сортируем по порядковому номеру отчёта (новые сверху)
    q += " ORDER BY COALESCE(seq_num, id) DESC"
    cur.execute(q, params)
    return cur.fetchall()


def mark_report_printed(report_id_int, user_id):
    db = get_db()
    cur = db.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        "UPDATE reports SET printed_at = ? WHERE id = ? AND user_id = ?",
        (now, report_id_int, user_id),
    )
    db.commit()


def mark_report_opened(report_id_int, user_id):
    db = get_db()
    cur = db.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        "UPDATE reports SET opened_at = COALESCE(opened_at, ?) WHERE id = ? AND user_id = ?",
        (now, report_id_int, user_id),
    )
    db.commit()


def get_report(report_id_int):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM reports WHERE id = ?", (report_id_int,))
    return cur.fetchone()


def delete_report(report_id_int, user_id=None):
    db = get_db()
    cur = db.cursor()
    if user_id is not None:
        cur.execute(
            "DELETE FROM reports WHERE id = ? AND user_id = ?", (report_id_int, user_id)
        )
    else:
        cur.execute("DELETE FROM reports WHERE id = ?", (report_id_int,))
    db.commit()

# ----------------- фильтр nl2br -----------------

_paragraph_re = re.compile(r'(?:\r\n|\r|\n){2,}')


@app.template_filter()
@pass_eval_context
def nl2br(eval_ctx, value):
    if not value:
        return ""
    result = u'\n\n'.join(
        u'<p>%s</p>' % p.replace('\n', '<br>\n')
        for p in _paragraph_re.split(escape(value))
    )
    if eval_ctx.autoescape:
        return Markup(result)
    return result


@app.template_filter("mechanic_message_html")
def mechanic_message_html_filter(value):
    from integrations.mechanic_info_message import format_mechanic_message_html

    return format_mechanic_message_html(value or "")


@app.template_filter("mechanic_message_parts")
def mechanic_message_parts_filter(value):
    from integrations.mechanic_info_message import parse_mechanic_message

    return parse_mechanic_message(value or "")

# ----------------- работа с организациями / контрагентами -----------------


def _normalize_location_list(raw) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    values: list = []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple)):
        values = list(raw)
    for item in values:
        loc = str(item or "").strip()
        if not loc:
            continue
        key = loc.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(loc)
    return out


def load_counterparties() -> list[dict]:
    """База компаний/контрагентов: [{name, locations: []}].

    Пополняется из отчётов и загрузкой XML в настройках.
    Поддерживает старый формат organizations.json (список строк).
    """
    if not os.path.exists(ORG_FILE):
        with open(ORG_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        return []
    with open(ORG_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            return []
    if not isinstance(data, list):
        return []
    result: list[dict] = []
    for item in data:
        if isinstance(item, str):
            name = item.strip()
            if name:
                result.append({"name": name, "locations": []})
            continue
        if not isinstance(item, dict):
            continue
        name = str(
            item.get("name")
            or item.get("organization")
            or item.get("company")
            or ""
        ).strip()
        if not name:
            continue
        locs = _normalize_location_list(item.get("locations"))
        single = str(item.get("location") or item.get("address") or "").strip()
        if single:
            locs = _normalize_location_list(list(locs) + [single])
        result.append({"name": name, "locations": locs})
    return result


def save_counterparties(items: list[dict]) -> None:
    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in items or []:
        name = str((item or {}).get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            # merge locations into already added
            for row in cleaned:
                if row["name"].casefold() == key:
                    row["locations"] = _normalize_location_list(
                        list(row.get("locations") or [])
                        + list((item or {}).get("locations") or [])
                    )
                    break
            continue
        seen.add(key)
        cleaned.append(
            {
                "name": name,
                "locations": _normalize_location_list((item or {}).get("locations")),
            }
        )
    with open(ORG_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)


def load_organizations():
    return [c["name"] for c in load_counterparties()]


def save_organizations(orgs):
    existing = {c["name"].casefold(): c for c in load_counterparties()}
    out: list[dict] = []
    for raw in orgs or []:
        name = str(raw or "").strip()
        if not name:
            continue
        prev = existing.get(name.casefold()) or {"name": name, "locations": []}
        out.append(
            {
                "name": name,
                "locations": _normalize_location_list(prev.get("locations")),
            }
        )
    save_counterparties(out)


def upsert_counterparty(name: str, location: str = "") -> None:
    org = (name or "").strip()
    loc = (location or "").strip()
    if not org and not loc:
        return
    items = load_counterparties()
    if not org:
        # Место без компании — кладём в служебную запись, чтобы не потерять адрес
        org = "—"
    key = org.casefold()
    for row in items:
        if row["name"].casefold() == key:
            if loc:
                before = list(row.get("locations") or [])
                merged = _normalize_location_list(before + [loc])
                if merged != before:
                    row["locations"] = merged
                    save_counterparties(items)
            return
    items.append({"name": org, "locations": _normalize_location_list([loc] if loc else [])})
    save_counterparties(items)


def sync_counterparties_from_reports() -> dict:
    """Дополняет базу компаний/мест из отчётов без дубликатов (пакетно)."""
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT DISTINCT TRIM(organization) AS organization, TRIM(location) AS location
        FROM reports
        WHERE (organization IS NOT NULL AND TRIM(organization) != '')
           OR (location IS NOT NULL AND TRIM(location) != '')
        """
    )
    items = load_counterparties()
    by_key = {c["name"].casefold(): c for c in items}
    added_orgs = 0
    added_locs = 0
    changed = False
    for row in cur.fetchall():
        org = (row["organization"] or "").strip()
        loc = (row["location"] or "").strip()
        if not org and not loc:
            continue
        if not org:
            org = "—"
        key = org.casefold()
        if key not in by_key:
            by_key[key] = {"name": org, "locations": []}
            items.append(by_key[key])
            added_orgs += 1
            changed = True
        if loc:
            before = list(by_key[key].get("locations") or [])
            merged = _normalize_location_list(before + [loc])
            if merged != before:
                by_key[key]["locations"] = merged
                added_locs += 1
                changed = True
    if changed:
        save_counterparties(items)
    return {"added_organizations": added_orgs, "added_locations": added_locs}


def _xml_local_tag(tag: str) -> str:
    if not tag:
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1].lower()
    return tag.lower()


def _xml_collect_text_map(el) -> dict[str, str]:
    out: dict[str, str] = {}
    for child in list(el):
        tag = _xml_local_tag(child.tag)
        text = "".join(child.itertext()).strip()
        if tag and text and tag not in out:
            out[tag] = text
    for attr, val in (el.attrib or {}).items():
        key = _xml_local_tag(attr)
        text = str(val or "").strip()
        if key and text and key not in out:
            out[key] = text
    return out


def _xml_pick_name_location(fields: dict[str, str]) -> tuple[str, str]:
    name_keys = (
        "name",
        "company",
        "organization",
        "org",
        "counterparty",
        "контрагент",
        "компания",
        "название",
        "организация",
        "заказчик",
    )
    loc_keys = (
        "location",
        "address",
        "place",
        "site",
        "адрес",
        "место",
        "местоэксплуатации",
        "место_эксплуатации",
        "эксплуатация",
    )
    name = ""
    loc = ""
    for key in name_keys:
        if fields.get(key):
            name = fields[key]
            break
    for key in loc_keys:
        if fields.get(key):
            loc = fields[key]
            break
    if not name:
        # fallback: first two values
        vals = [v for v in fields.values() if v]
        if vals:
            name = vals[0]
            if len(vals) > 1:
                loc = vals[1]
    return name.strip(), loc.strip()


def parse_counterparties_xml(raw: bytes | str) -> list[dict]:
    """Разбор XML: колонки компания/контрагент + место эксплуатации/адрес."""
    import xml.etree.ElementTree as ET

    if isinstance(raw, bytes):
        text = raw.decode("utf-8-sig", errors="replace")
    else:
        text = str(raw or "")
    text = text.strip()
    if not text:
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        # иногда приходит fragment без корня
        try:
            root = ET.fromstring(f"<root>{text}</root>")
        except ET.ParseError as exc:
            raise ValueError(f"Некорректный XML: {exc}") from exc

    row_tags = {
        "item",
        "row",
        "record",
        "entry",
        "company",
        "organization",
        "counterparty",
        "контрагент",
        "компания",
        "организация",
    }
    candidates = []
    for el in root.iter():
        tag = _xml_local_tag(el.tag)
        if tag in row_tags and el is not root:
            candidates.append(el)
    if not candidates:
        # любые элементы 2-го уровня с текстовыми детьми
        for el in list(root):
            if list(el):
                candidates.append(el)

    parsed: list[dict] = []
    for el in candidates:
        fields = _xml_collect_text_map(el)
        name, loc = _xml_pick_name_location(fields)
        if not name:
            continue
        parsed.append(
            {
                "name": name,
                "locations": _normalize_location_list([loc] if loc else []),
            }
        )
    return parsed


def merge_counterparties(imported: list[dict], *, replace: bool = False) -> dict:
    if replace:
        save_counterparties(imported)
        return {
            "total": len(load_counterparties()),
            "imported": len(imported),
            "mode": "replace",
        }
    items = load_counterparties()
    by_key = {c["name"].casefold(): c for c in items}
    added = 0
    updated = 0
    for row in imported or []:
        name = str((row or {}).get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        locs = _normalize_location_list((row or {}).get("locations"))
        if key in by_key:
            before = list(by_key[key].get("locations") or [])
            by_key[key]["locations"] = _normalize_location_list(before + locs)
            if by_key[key]["locations"] != before:
                updated += 1
        else:
            by_key[key] = {"name": name, "locations": locs}
            items.append(by_key[key])
            added += 1
    save_counterparties(items)
    return {
        "total": len(items),
        "imported": len(imported or []),
        "added": added,
        "updated": updated,
        "mode": "merge",
    }


def _mindmap_equipment_key(organization: str, equipment_type: str, serial_number: str) -> str:
    # Детерминированный ключ, чтобы обновлять расписание по одному узлу.
    raw = f"{organization or ''}|{equipment_type or ''}|{serial_number or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_date_any_to_iso(date_str: str) -> str | None:
    """
    Преобразует строки из БД (например, dd.mm.YYYY) или из инпута date (YYYY-MM-DD)
    в ISO YYYY-MM-DD. Возвращает None, если распарсить не удалось.
    """
    if not date_str:
        return None
    s = str(date_str).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(s, fmt).date()
            return dt.isoformat()
        except ValueError:
            continue
    return None


def _format_date_dd_mm_yy(raw: str | None) -> str | None:
    """Форматируем дату в виде ДД.ММ.ГГ (двузначный год).

    Поддерживаем типовые варианты: YYYY-MM-DD, DD.MM.YYYY, YYYYMMDD.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None

    # Быстрый парсинг ISO
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y%m%d"):
        try:
            dt = datetime.strptime(s, fmt).date()
            return dt.strftime("%d.%m.%y")
        except ValueError:
            continue

    # Если дата уже в ISO без корректного парсинга — пробуем как fallback через _parse_date_any_to_iso
    iso = _parse_date_any_to_iso(s)
    if iso:
        try:
            dt = datetime.strptime(iso, "%Y-%m-%d").date()
            return dt.strftime("%d.%m.%y")
        except ValueError:
            return None
    return None


def _calc_due_date_iso(last_auto_date_iso: str | None, interval_days: int | None) -> str | None:
    if not last_auto_date_iso:
        return None
    if interval_days is None:
        return None
    try:
        days = int(interval_days)
    except Exception:
        return None
    if days <= 0:
        return None
    try:
        last_dt = datetime.strptime(last_auto_date_iso, "%Y-%m-%d").date()
        due_dt = last_dt + timedelta(days=days)
        return due_dt.isoformat()
    except Exception:
        return None


def _get_latest_report_date_iso(organization: str, equipment_type: str, serial_number: str) -> str | None:
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT date
        FROM reports
        WHERE organization = ? AND equipment_type = ? AND serial_number = ?
          AND date IS NOT NULL AND date != ''
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 1
        """,
        (organization, equipment_type, serial_number),
    )
    row = cur.fetchone()
    if not row:
        return None
    return _parse_date_any_to_iso(row[0])


def _get_equipment_schedule_row(equipment_key: str) -> sqlite3.Row | None:
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT equipment_key, organization, equipment_type, serial_number,
               last_auto_date_iso, interval_days, updated_at
        FROM mindmap_equipment_schedule
        WHERE equipment_key = ?
        """,
        (equipment_key,),
    )
    return cur.fetchone()


def _upsert_equipment_schedule(
    equipment_key: str,
    organization: str,
    equipment_type: str,
    serial_number: str,
    last_auto_date_iso: str | None,
    interval_days: int | None,
) -> None:
    db = get_db()
    cur = db.cursor()
    now_iso = datetime.now().date().isoformat()
    existing = _get_equipment_schedule_row(equipment_key)
    if existing:
        cur.execute(
            """
            UPDATE mindmap_equipment_schedule
            SET organization = ?, equipment_type = ?, serial_number = ?,
                last_auto_date_iso = ?, interval_days = ?, updated_at = ?
            WHERE equipment_key = ?
            """,
            (organization, equipment_type, serial_number, last_auto_date_iso, interval_days, now_iso, equipment_key),
        )
    else:
        cur.execute(
            """
            INSERT INTO mindmap_equipment_schedule (
                equipment_key, organization, equipment_type, serial_number,
                last_auto_date_iso, interval_days, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (equipment_key, organization, equipment_type, serial_number, last_auto_date_iso, interval_days, now_iso),
        )
    db.commit()

# ----------------- сохранение отчёта в Excel -----------------


def append_report_to_excel(report_dict):
    df_new = pd.DataFrame([report_dict])
    if os.path.exists(REPORTS_FILE):
        df_old = pd.read_excel(REPORTS_FILE)
        df_all = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df_all = df_new
    df_all.to_excel(REPORTS_FILE, index=False)

def link_callback(uri, rel):
    """
    Преобразует URI ресурсов (шрифты, изображения, CSS) в файловые пути
    для xhtml2pdf. Поддерживает пути вида /static/... и static/....
    """
    if os.path.isabs(uri) and os.path.exists(uri):
        return uri

    if uri.startswith("/static/"):
        path = os.path.join(STATIC_DIR, uri.replace("/static/", "", 1))
    elif uri.startswith("static/"):
        path = os.path.join(STATIC_DIR, uri.replace("static/", "", 1))
    else:
        return uri

    if not os.path.isfile(path):
        return uri
    return path


def _register_pdf_font():
    """
    Регистрирует шрифт DejaVuSans в ReportLab, если доступны библиотеки
    и файл шрифта присутствует.
    """
    if pdfmetrics is None or TTFont is None:
        return
    fonts_dir = os.path.join(STATIC_DIR, "fonts")
    normal_path = os.path.join(fonts_dir, "DejaVuSans.ttf")
    bold_path = os.path.join(fonts_dir, "DejaVuSans-Bold.ttf")
    italic_path = os.path.join(fonts_dir, "DejaVuSans-Oblique.ttf")
    bold_italic_path = os.path.join(fonts_dir, "DejaVuSans-BoldOblique.ttf")

    if not os.path.isfile(normal_path):
        return

    try:
        pdfmetrics.registerFont(TTFont("DejaVuSans", normal_path))
        if os.path.isfile(bold_path):
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold_path))
        if os.path.isfile(italic_path):
            pdfmetrics.registerFont(TTFont("DejaVuSans-Oblique", italic_path))
        if os.path.isfile(bold_italic_path):
            pdfmetrics.registerFont(TTFont("DejaVuSans-BoldOblique", bold_italic_path))

        # Маппинг семейства для bold/italic в HTML/CSS
        try:
            pdfmetrics.registerFontFamily(
                "DejaVuSans",
                normal="DejaVuSans",
                bold="DejaVuSans-Bold" if os.path.isfile(bold_path) else "DejaVuSans",
                italic="DejaVuSans-Oblique"
                if os.path.isfile(italic_path)
                else "DejaVuSans",
                boldItalic="DejaVuSans-BoldOblique"
                if os.path.isfile(bold_italic_path)
                else "DejaVuSans",
            )
        except Exception:
            pass

    except Exception:
        return


_PDF_FONT_REGISTERED = False


def _wizard_form_context(user, company_code: str, **extra):
    limits = form_field_limits()
    mat_cfg = load_report_config().get("form", {}).get("materials", {})
    form_cfg = load_report_config().get("form", {})
    return {
        "user": user,
        "selected_company": normalize_company_code(company_code),
        "form_limits": limits,
        "material_limits": mat_cfg,
        "work_type_options": work_types_list(),
        "work_desc_max": int(form_cfg.get("work_description", {}).get("max_chars", 1000)),
        "rec_max": int(form_cfg.get("recommendations", {}).get("max_chars", 470)),
        **extra,
    }



def report_to_pdf(report: dict, materials: list[dict]) -> BytesIO:
    """
    Генерация PDF поверх утверждённого бланка blank_AlfaSS.pdf:
    сам бланк используется как фон, мы только вписываем текст по координатам.
    """
    global _PDF_FONT_REGISTERED
    if not _PDF_FONT_REGISTERED:
        _register_pdf_font()
        _PDF_FONT_REGISTERED = True

    if pdfmetrics is None or TTFont is None:
        raise RuntimeError("ReportLab недоступен для генерации PDF")

    # Выбираем подложку в зависимости от компании
    company_code = normalize_company_code(report.get("company_code"))
    template_name = template_filename(company_code)
    layout = get_pdf_layout(company_code)

    template_path = os.path.join(STATIC_DIR, template_name)
    if not os.path.isfile(template_path):
        raise RuntimeError(f"Не найден файл бланка {template_name}")

    reader = PdfReader(template_path)
    base_page = reader.pages[0]
    width = float(base_page.mediabox.width)
    height = float(base_page.mediabox.height)

    overlay_buf = BytesIO()
    c = canvas.Canvas(overlay_buf, pagesize=(width, height))
    c.setFont("DejaVuSans", 9)

    def draw_text(x, y, text, max_len=None):
        t = text or ""
        if max_len and len(t) > max_len:
            t = t[: max_len - 3] + "..."
        c.drawString(x, y, t)

    def y_from_top(offset: float) -> float:
        return height - offset

    c.setFont("DejaVuSans", 10)
    draw_text(layout["date"][0], y_from_top(layout["date"][1]), str(report.get("date", "")), max_len=10)
    draw_text(
        layout["organization"][0],
        y_from_top(layout["organization"][1]),
        str(report.get("organization", "")),
        max_len=35,
    )
    c.setFont("DejaVuSans", 9)

    draw_text(
        layout["equipment"][0],
        y_from_top(layout["equipment"][1]),
        str(report.get("equipment_type", "")),
        max_len=28,
    )
    draw_text(
        layout["serial"][0],
        y_from_top(layout["serial"][1]),
        str(report.get("serial_number", "")),
        max_len=28,
    )
    draw_text(
        layout["location"][0],
        y_from_top(layout["location"][1]),
        str(report.get("location", "")),
        max_len=38,
    )
    internal_num = str(report.get("internal_number", ""))[:7]
    draw_text(layout["internal"][0], y_from_top(layout["internal"][1]), internal_num)
    draw_text(
        layout["engine_hours"][0],
        y_from_top(layout["engine_hours"][1]),
        str(report.get("engine_hours", "")),
        max_len=28,
    )
    draw_text(
        layout["year"][0],
        y_from_top(layout["year"][1]),
        str(report.get("year", "")),
        max_len=10,
    )

    work_types_str = str(report.get("work_types", "") or "")
    selected_types = {w.strip() for w in work_types_str.split(",") if w.strip()}
    checkbox_defs = list(zip(work_types_list(), layout["checks_x"]))
    check_y = y_from_top(layout["check_y"])
    # Увеличиваем размер шрифта отметок типа работ до 12pt
    c.setFont("DejaVuSans", 12)
    for label, x in checkbox_defs:
        mark = "X" if label in selected_types else ""
        c.drawString(x, check_y, mark)
    # Возвращаем базовый размер шрифта
    c.setFont("DejaVuSans", 10)

    # Вспомогательная функция переноса по реальной ширине строки в пунктов
    def wrap_by_width(text: str, max_width: float, font_name: str = "DejaVuSans", font_size: int = 10) -> list[str]:
        """
        Переносит строки так, чтобы текст не выходил за правую границу блока.
        Учитывает ширину слов в пунктах, переносит только по словам (если слово длиннее max_width — режет его).
        """
        lines: list[str] = []
        words = text.split()
        current = ""

        for word in words:
            candidate = (current + " " + word).strip() if current else word
            w = pdfmetrics.stringWidth(candidate, font_name, font_size)

            if w <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                    # Проверяем, влезает ли слово целиком в новую строку
                    if pdfmetrics.stringWidth(word, font_name, font_size) <= max_width:
                        current = word
                    else:
                        # Слово слишком длинное — режем по символам
                        chunk = ""
                        for ch in word:
                            cw = pdfmetrics.stringWidth(chunk + ch, font_name, font_size)
                            if cw > max_width and chunk:
                                lines.append(chunk)
                                chunk = ch
                            else:
                                chunk += ch
                        current = chunk
                else:
                    # Нечего переносить, а слово уже не влезает — режем его
                    chunk = ""
                    for ch in word:
                        cw = pdfmetrics.stringWidth(chunk + ch, font_name, font_size)
                        if cw > max_width and chunk:
                            lines.append(chunk)
                            chunk = ch
                        else:
                            chunk += ch
                    current = chunk

        if current:
            lines.append(current)
        return lines

    # Мероприятия по оказанию услуг — многострочный текст в большом поле
    work_text_raw = report.get("work_description") or ""
    # Максимум символов для блока "Мероприятия по оказанию услуг"
    max_work_chars = 1000
    if len(work_text_raw) > max_work_chars:
        work_text_raw = work_text_raw[: max_work_chars - 3] + "..."
    work_box_x = layout["work_box_x"]
    work_box_y = y_from_top(layout["work_box_y_from_top"])
    work_box_w = width - 2 * work_box_x
    work_box_h = layout["work_box_h"]
    text_obj = c.beginText()
    text_obj.setTextOrigin(
        work_box_x + layout["work_text_pad_x"],
        work_box_y + work_box_h - layout["work_text_pad_top"],
    )
    text_obj.setFont("DejaVuSans", 10)
    max_line_width = layout["work_max_width"]
    wrapped_lines: list[str] = []
    for para in work_text_raw.splitlines():
        wrapped_lines.extend(wrap_by_width(para, max_width=max_line_width))

    # Ограничиваем отображение первыми 15 строками,
    # чтобы не выйти за нижнюю границу блока на бланке.
    for line in wrapped_lines[:15]:
        text_obj.textLine(line)
    c.drawText(text_obj)

    # Рекомендации
    rec_raw = report.get("recommendations") or ""
    # Примерный безопасный предел символов для блока рекомендаций
    max_rec_chars = 470
    if len(rec_raw) > max_rec_chars:
        rec_raw = rec_raw[: max_rec_chars - 3] + "..."
    rec_box_x = layout["rec_box_x"]
    rec_text = c.beginText()
    rec_text.setTextOrigin(
        rec_box_x + layout["rec_text_pad_x"],
        y_from_top(layout["rec_text_y_from_top"]),
    )
    rec_font_size = 10
    rec_text.setFont("DejaVuSans", rec_font_size)

    rec_max_line_width = layout["rec_max_width"]
    wrapped_rec_lines: list[str] = []
    for para in rec_raw.splitlines():
        wrapped_rec_lines.extend(
            wrap_by_width(para, max_width=rec_max_line_width, font_name="DejaVuSans", font_size=rec_font_size)
        )

    # Вертикально в блок рекомендаций влезает меньше строк, чем в блок мероприятий.
    # Ограничим первыми 8 строками, чтобы не выйти за нижнюю границу.
    for line in wrapped_rec_lines[:8]:
        rec_text.textLine(line)
    c.drawText(rec_text)

    mat_start_y = y_from_top(layout["mat_start_y_from_top"])
    row_h = layout["mat_row_h"]
    baseline_offset = layout.get("mat_baseline_offset", 0)
    max_rows = layout.get("mat_max_rows", 11)
    mat_font_size = 9
    c.setFont("DejaVuSans", mat_font_size)

    def fit_material_text(text: str, max_width: float) -> str:
        prepared = text or ""
        if max_width <= 0 or not prepared:
            return prepared
        while prepared and c.stringWidth(prepared, "DejaVuSans", mat_font_size) > max_width:
            prepared = prepared[:-1]
        return prepared

    for idx, m in enumerate(materials[:max_rows]):
        y = mat_start_y - idx * row_h - baseline_offset
        code = str(m.get("code", ""))[:10]
        name = str(m.get("name", ""))[:30]
        qty = str(m.get("qty", ""))[:4]
        code_max_x = layout.get("mat_code_max_x")
        code_center = layout.get("mat_code_center_x")
        code_col_w = layout.get("mat_code_col_w")
        if code_col_w is not None:
            code = fit_material_text(code, code_col_w)
        elif code_max_x is not None:
            code = fit_material_text(code, code_max_x - layout["mat_code_x"])
        name_max_x = layout.get("mat_name_max_x")
        if name_max_x is not None:
            name = fit_material_text(name, name_max_x - layout["mat_name_x"])
        if code_center is not None:
            code_w = c.stringWidth(code, "DejaVuSans", mat_font_size)
            draw_text(code_center - code_w / 2, y, code)
        else:
            draw_text(layout["mat_code_x"], y, code)
        draw_text(layout["mat_name_x"], y, name)
        qty_center = layout.get("mat_qty_center_x")
        qty_col_w = layout.get("mat_qty_col_w")
        if qty_col_w is not None:
            qty = fit_material_text(qty, qty_col_w)
        qty_right_x = layout.get("mat_qty_right_x")
        if qty_center is not None:
            qty_w = c.stringWidth(qty, "DejaVuSans", mat_font_size)
            draw_text(qty_center - qty_w / 2, y, qty)
        elif qty_right_x is not None:
            qty_width = c.stringWidth(qty, "DejaVuSans", mat_font_size)
            draw_text(qty_right_x - qty_width, y, qty)
        else:
            draw_text(layout["mat_qty_x"], y, qty)

    mech_name = str(report.get("mechanic_name", ""))[:40]
    name_center = layout.get("mechanic_name_center_x")
    name_col_w = layout.get("mechanic_name_col_w")
    if name_col_w:
        mech_name = fit_material_text(mech_name, name_col_w)
    if name_center is not None:
        name_w = c.stringWidth(mech_name, "DejaVuSans", 9)
        draw_text(name_center - name_w / 2, layout["mechanic_name"][1], mech_name)
    else:
        draw_text(layout["mechanic_name"][0], layout["mechanic_name"][1], mech_name, max_len=40)
    position = str(report.get("mechanic_position", ""))[:25]
    pos_center = layout.get("position_center_x")
    pos_col_w = layout.get("position_col_w")
    if pos_col_w:
        position = fit_material_text(position, pos_col_w)
    if pos_center is not None:
        pos_w = c.stringWidth(position, "DejaVuSans", 9)
        draw_text(pos_center - pos_w / 2, layout["position"][1], position)
    else:
        draw_text(
            layout["position"][0],
            layout["position"][1],
            position,
            max_len=25,
        )

    c.showPage()
    c.save()
    overlay_buf.seek(0)

    overlay_reader = PdfReader(overlay_buf)
    writer = PdfWriter()
    base = base_page
    base.merge_page(overlay_reader.pages[0])
    writer.add_page(base)

    out_buf = BytesIO()
    writer.write(out_buf)
    out_buf.seek(0)
    return out_buf

# ----------------- авторизация и главные экраны -----------------


@app.route("/", methods=["GET"])
def index():
    user = get_current_user()
    if user:
        return redirect(url_for(staff_home_endpoint(user)))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    portal = (request.args.get("portal") or request.form.get("portal") or "mechanic").strip()
    if portal not in ("mechanic", "staff"):
        portal = "mechanic"
    if request.method == "POST":
        login_val = request.form.get("login", "").strip()
        password = request.form.get("password", "")
        user = get_user_by_login(login_val)
        if not user or not check_password_hash(user["password_hash"], password):
            error = "Неверный логин или пароль"
        elif is_mechanic_user(user) and not user["is_approved"]:
            error = "Учётная запись ожидает подтверждения администратора"
        elif is_any_manager_user(user) and not user["is_approved"]:
            error = "Учётная запись менеджера ожидает подтверждения администратора"
        else:
            session.clear()
            session["user_id"] = user["id"]
            session["user_role"] = user["role"]
            # В интерфейсе (форма, админка) показываем полное ФИО
            fio_full = " ".join(
                p
                for p in [
                    user["last_name"],
                    user["first_name"],
                    user["middle_name"],
                ]
                if p
            ).strip()
            session["user_name"] = fio_full or user["login"]
            return redirect(url_for(staff_home_endpoint(user)))
    return render_template("login.html", error=error, portal=portal)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    success = None
    if request.method == "POST":
        last_name = request.form.get("last_name", "").strip()
        first_name = request.form.get("first_name", "").strip()
        middle_name = request.form.get("middle_name", "").strip()
        position = request.form.get("position", "").strip()
        login_val = request.form.get("login", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        vehicle_model = request.form.get("vehicle_model", "").strip()
        vehicle_plate = request.form.get("vehicle_plate", "").strip()

        if not last_name or not first_name or not login_val or not password:
            error = "Фамилия, имя, логин и пароль обязательны"
        elif not vehicle_model or not vehicle_plate:
            error = "Укажите служебный автомобиль и госномер"
        elif password != password2:
            error = "Пароли не совпадают"
        elif get_user_by_login(login_val):
            error = "Пользователь с таким логином уже существует"
        else:
            try:
                from vehicle_fleet import is_valid_model

                if not is_valid_model(vehicle_model):
                    raise ValueError("Выберите модель автомобиля из списка")
                create_user(
                    login=login_val,
                    password=password,
                    last_name=last_name,
                    first_name=first_name,
                    middle_name=middle_name,
                    position=position,
                    vehicle_model=vehicle_model,
                    vehicle_plate=vehicle_plate,
                )
                success = "Регистрация успешно отправлена. Дождитесь одобрения администратора."
            except ValueError as exc:
                error = str(exc)
    from vehicle_fleet import VEHICLE_MODELS

    return render_template("register.html", error=error, success=success, vehicle_models=VEHICLE_MODELS)


@app.route("/account")
def account():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    if is_admin_user(user):
        return redirect(url_for("admin_dashboard"))
    if is_manager_user(user):
        return redirect(url_for("manager_dashboard"))
    if not is_mechanic_user(user):
        return redirect(url_for("login"))
    company_code = session.get("company_code") or "ALFASS"
    now = datetime.now()
    filter_year = request.args.get("year", "").strip() or str(now.year)
    filter_month = request.args.get("month", "").strip() or str(now.month)
    filter_day = request.args.get("day", "").strip() or str(now.day)
    if not filter_year.isdigit():
        filter_year = str(now.year)
    if not filter_month.isdigit():
        filter_month = str(now.month)
    if not filter_day.isdigit():
        filter_day = str(now.day)
    raw_reports = list_reports_for_user(
        user["id"], year=filter_year, month=filter_month, day=filter_day
    )
    today = now
    today_str = today.strftime("%d.%m.%Y")
    months_ru = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")
    today_label = f"{today.day} {months_ru[today.month]} {today.year} года"
    total_count = len(raw_reports)
    # Годы из отчётов пользователя для фильтра
    all_reports = list_reports_for_user(user["id"])
    years_set = set()
    for r in all_reports:
        date_val = r["date"]
        if date_val and len(str(date_val)) >= 4:
            years_set.add(str(date_val)[:4])
    years_set.add(str(today.year))
    filter_years = sorted(years_set, reverse=True)
    # Готовим отчёты для шаблона (имя генерации + ограничение редактирования 48 часов)
    reports = []
    printed_count = 0
    unsigned_count = 0
    ungenerated_count = 0
    for idx, r in enumerate(raw_reports, start=1):
        seq = int(r["seq_num"] or 0) or idx
        created_at = r["created_at"] or ""
        dmy = ""
        try:
            dpart = created_at[:10]
            y, m, d = dpart.split("-")
            dmy = f"{d}{m}{y}"
        except Exception:
            dmy = ""

        comp = normalize_company_code(r["company_code"] or company_code)
        suffix = pdf_filename_suffix(comp)
        gen_name = f"{seq:04d}_{dmy}_{suffix}.pdf" if dmy else f"{seq:04d}_{suffix}.pdf"

        remaining = edit_timer_remaining(created_at, datetime.now())
        edit_allowed = can_edit_report(created_at, datetime.now())
        timer_str = format_edit_timer(remaining)

        reports.append(
            {
                **dict(r),
                "display_seq": seq,
                "gen_name": gen_name,
                "edit_allowed": edit_allowed,
                "edit_timer": timer_str,
            }
        )
        if r["opened_at"]:
            printed_count += 1
        else:
            ungenerated_count += 1
        if not int(r["is_signed"] or 0):
            unsigned_count += 1

    # Неподписанные за выбранный месяц
    month_reports = list_reports_for_user(user["id"], year=filter_year, month=filter_month, day=None)
    unsigned_month_count = sum(1 for r in month_reports if not int(r["is_signed"] or 0))

    is_today_selected = (
        str(filter_year) == str(today.year)
        and int(filter_month) == today.month
        and int(filter_day) == today.day
    )

    return render_template(
        "account.html",
        user=user,
        reports=reports,
        today_str=today_str,
        today_label=today_label,
        total_reports=total_count,
        filter_year=filter_year or "",
        filter_month=filter_month or "",
        filter_day=filter_day or "",
        filter_years=filter_years,
        months_ru=months_ru[1:],
        selected_company=company_code,
        reports_word=reports_word,
        printed_count=printed_count,
        unsigned_count=unsigned_count,
        ungenerated_count=ungenerated_count,
        unsigned_month_count=unsigned_month_count,
        is_today_selected=is_today_selected,
    )


@app.route("/account/vehicle", methods=["GET", "POST"])
def mechanic_vehicle():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    if is_admin_user(user):
        return redirect(url_for("admin_dashboard", section="autos"))
    if is_manager_user(user):
        return redirect(url_for("manager_dashboard", tab="autos"))
    if not is_mechanic_user(user):
        return redirect(url_for("login"))

    from vehicle_fleet import (
        add_mileage_log,
        add_oil_change_log,
        assign_vehicle,
        build_vehicle_panel_context,
        get_fleet_vehicle_for_user,
        get_latest_mileage,
        get_vehicle_for_user,
        mark_regulation_done,
        unmark_regulation_done,
        save_period_boundary_mileage,
        reset_period_boundary_mileage,
        replace_fuel_fills_in_period,
        add_fuel_fills_in_period,
        delete_fuel_fill,
        upsert_fuel_monthly,
        VEHICLE_MODELS,
    )

    db = get_db()
    fuel_price = get_fuel_price_setting()
    error = None
    success = None
    mechanic_info_success = session.pop("mechanic_info_success", None) == "1"
    mechanic_info_success_panel = session.pop("mechanic_info_success_panel", None)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        try:
            if action == "mileage_period":
                result = save_period_boundary_mileage(
                    db,
                    user["id"],
                    request.form.get("boundary", ""),
                    int(request.form.get("mileage_km", "0")),
                    get_setting_fn=get_setting,
                )
                success = f"Пробег на {result['day_label']} число сохранён."
            elif action == "mileage":
                add_mileage_log(
                    db,
                    user["id"],
                    int(request.form.get("mileage_km", "0")),
                    request.form.get("note", ""),
                )
                success = "Пробег сохранён."
            elif action == "fuel_fills":
                add_fuel_fills_in_period(
                    db,
                    user["id"],
                    request.form.get("fuel_period_start", ""),
                    request.form.get("fuel_period_end", ""),
                    _parse_fuel_fills_form(request.form),
                )
                success = "Данные по заправкам сохранены."
            elif action == "fuel_fill_delete":
                delete_fuel_fill(db, user["id"], int(request.form.get("fill_id", "0")))
                success = "Заправка удалена."
            elif action == "fuel_report":
                created = _create_fuel_report_from_request(db, user)
                success = (
                    f"Топливный отчёт за {created['start_date']} — {created['end_date']} сформирован."
                )
            elif action == "fuel":
                ym = (request.form.get("year_month") or "").strip()
                upsert_fuel_monthly(
                    db,
                    user["id"],
                    ym,
                    float(request.form.get("amount_rub", "0") or 0),
                    int(request.form.get("fill_count", "0") or 0),
                    request.form.get("note", ""),
                )
                success = "Данные по топливу сохранены."
            elif action == "oil":
                add_oil_change_log(
                    db,
                    user["id"],
                    int(request.form.get("oil_mileage_km", "0")),
                    request.form.get("note", ""),
                )
                success = "Замена масла зафиксирована."
            elif action == "assign":
                assign_vehicle(
                    db,
                    user["id"],
                    request.form.get("vehicle_model", ""),
                    request.form.get("vehicle_plate", ""),
                )
                success = "Автомобиль привязан."
            elif action == "regulation_done":
                fleet = get_fleet_vehicle_for_user(db, user["id"])
                if not fleet:
                    raise ValueError("Машина автопарка не найдена.")
                latest = get_latest_mileage(db, user["id"])
                if latest is None:
                    raise ValueError("Сначала зафиксируйте пробег.")
                sort_order = int(request.form.get("sort_order", "0") or 0)
                mark_regulation_done(db, int(fleet["id"]), sort_order, int(latest))
                success = "Отметка «сделано» сохранена — точка отсчёта обновлена."
            elif action == "regulation_undo":
                fleet = get_fleet_vehicle_for_user(db, user["id"])
                if not fleet:
                    raise ValueError("Машина автопарка не найдена.")
                latest = get_latest_mileage(db, user["id"])
                if latest is None:
                    raise ValueError("Сначала зафиксируйте пробег.")
                sort_order = int(request.form.get("sort_order", "0") or 0)
                unmark_regulation_done(db, int(fleet["id"]), sort_order, int(latest))
                success = "Отметка «выполнено» отменена."
            elif action == "mechanic_info":
                panel_index = (request.form.get("panel_index") or "1").strip()
                _submit_mechanic_info(db, user, get_setting_fn=get_setting)
                session["mechanic_info_success_panel"] = panel_index
        except (ValueError, TypeError) as exc:
            error = str(exc)

    vehicle_panel = build_vehicle_panel_context(
        db, user["id"], fuel_price, show_admin_stats=False, get_setting_fn=get_setting
    )
    return render_template(
        "mechanic_vehicle.html",
        user=user,
        vehicle_panel=vehicle_panel,
        vehicle_models=VEHICLE_MODELS,
        error=error,
        success=success,
        mechanic_info_success=mechanic_info_success,
        mechanic_info_success_panel=mechanic_info_success_panel,
    )


@app.route("/company/<code>")
def set_company(code):
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    code_up = (code or "").upper()
    if code_up not in ("ALFASS", "ALFADOM"):
        code_up = "ALFASS"
    session["company_code"] = code_up
    return redirect(request.referrer or url_for("account"))


def _staff_reports_context(mechanics, selected_user_id: str):
    selected_mechanic = None
    reports_for_selected = []
    mechanic_kpis = None
    if selected_user_id.isdigit():
        candidate = get_user_by_id(int(selected_user_id))
        if candidate and candidate["role"] in ("MECHANIC", "EMPLOYEE"):
            selected_mechanic = candidate
    if selected_mechanic:
        reports_for_selected = [
            enrich_staff_report_row(r) for r in list_reports_for_admin_user(selected_mechanic["id"])
        ]
        mechanic_kpis = get_mechanic_staff_kpis(selected_mechanic["id"])
    return selected_mechanic, reports_for_selected, mechanic_kpis


def get_fuel_price_setting() -> float:
    try:
        return float(get_setting("fuel_price_rub_l", "55") or 55)
    except (TypeError, ValueError):
        return 55.0


def get_fleet_period_settings() -> dict:
    import calendar
    from vehicle_fleet import get_fleet_period_config, format_period_label, _MONTHS_GENITIVE
    from datetime import datetime

    cfg = get_fleet_period_config(get_setting)
    now = datetime.now()
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    cfg["period_label"] = format_period_label(now.year, now.month, cfg["day_from"], cfg["day_to"])
    cfg["calendar_year"] = now.year
    cfg["calendar_month"] = now.month
    cfg["month_name"] = _MONTHS_GENITIVE[now.month - 1]
    cfg["days_in_month"] = days_in_month
    return cfg


def _parse_fleet_schedule_form(form) -> list[dict]:
    titles = form.getlist("schedule_title")
    marks = form.getlist("schedule_mileage_mark")
    comments = form.getlist("schedule_comment")
    mileages = form.getlist("schedule_mileage_km")
    schedule_items = []
    for i in range(max(len(titles), len(marks), len(comments), len(mileages))):
        mileage_km = mileages[i] if i < len(mileages) else ""
        mark = marks[i] if i < len(marks) else ""
        if mileage_km and not mark:
            try:
                mark = f"{int(mileage_km):,}".replace(",", " ") + " км"
            except (TypeError, ValueError):
                mark = str(mileage_km)
        schedule_items.append(
            {
                "title": titles[i] if i < len(titles) else "",
                "mileage_mark": mark,
                "mileage_km": mileage_km,
                "comment": comments[i] if i < len(comments) else "",
                "sort_order": i,
            }
        )
    return schedule_items


def _parse_official_regulation_form(form) -> list[dict]:
    titles = form.getlist("official_title")
    mileages = form.getlist("official_mileage_km")
    comments = form.getlist("official_comment")
    statuses = form.getlist("official_status")
    items = []
    for i in range(max(len(titles), len(mileages), len(comments))):
        mileage_km = mileages[i] if i < len(mileages) else ""
        mark = ""
        if mileage_km not in ("", None):
            try:
                mark = f"каждые {int(mileage_km):,} км".replace(",", " ")
            except (TypeError, ValueError):
                mark = str(mileage_km)
        items.append(
            {
                "title": titles[i] if i < len(titles) else "",
                "mileage_mark": mark,
                "mileage_km": mileage_km,
                "comment": comments[i] if i < len(comments) else "",
                "status": "green",
                "sort_order": i,
            }
        )
    return items


def _parse_important_info_form(form) -> list[dict]:
    from vehicle_fleet import compute_important_auto_status

    titles = form.getlist("important_title")
    comments = form.getlist("important_comment")
    dates = form.getlist("important_date")
    statuses = form.getlist("important_status")
    items = []
    count = min(10, max(len(titles), len(comments), len(dates), len(statuses)))
    for i in range(count):
        event_date = dates[i] if i < len(dates) else ""
        items.append(
            {
                "title": titles[i] if i < len(titles) else "",
                "comment": comments[i] if i < len(comments) else "",
                "event_date": event_date,
                "status": compute_important_auto_status({"event_date": event_date}),
                "sort_order": i,
            }
        )
    return items


def _log_mechanic_info_event(
    target_user: dict,
    action: str,
    *,
    message_id: int | None = None,
    event_date: str | None = None,
    status: str | None = None,
    title: str | None = None,
    message: str | None = None,
    plate_number: str | None = None,
) -> None:
    from integrations.mechanic_info_log import append_mechanic_info_log

    target_user = _user_as_dict(target_user)
    append_mechanic_info_log(
        DATA_DIR,
        last_name=target_user.get("last_name") or "",
        first_name=target_user.get("first_name") or "",
        middle_name=target_user.get("middle_name") or "",
        action=action,
        message_id=message_id,
        event_date=event_date,
        status=status,
        title=title,
        message=message,
        plate_number=plate_number,
    )


def _mechanic_info_plate_number(db, user_id: int, fleet: dict | None = None) -> str | None:
    from vehicle_fleet import get_fleet_vehicle_for_user, get_vehicle_for_user

    fleet_row = fleet or get_fleet_vehicle_for_user(db, user_id)
    vehicle_row = get_vehicle_for_user(db, user_id)
    plate_number = (fleet_row or {}).get("plate_number")
    if vehicle_row:
        plate_number = vehicle_row.get("plate_number") or plate_number
    return plate_number


def _submit_mechanic_info(db, mechanic_user: dict, *, get_setting_fn) -> int:
    from integrations.mechanic_info_image import save_mechanic_info_image
    from integrations.mechanic_info_message import append_image_link_to_message
    from max_bot.outbound import build_mechanic_info_max_text, send_mechanic_info_to_max
    from vehicle_fleet import (
        VEHICLE_MODELS,
        add_fleet_vehicle_mechanic_message,
        get_fleet_vehicle_for_user,
        get_vehicle_for_user,
    )

    mechanic_user = _user_as_dict(mechanic_user)
    user_id = int(mechanic_user["id"])
    fleet = get_fleet_vehicle_for_user(db, user_id)
    if not fleet:
        raise ValueError("Машина автопарка не найдена.")

    duplicate_max = request.form.get("duplicate_max") == "1"
    max_user_id = (mechanic_user.get("max_user_id") or "").strip()
    if duplicate_max and not max_user_id:
        raise ValueError("Для дубликата в MAX укажите ID пользователя в MAX в карточке сотрудника.")

    event_day = (request.form.get("event_date") or "").strip()[:10]
    if not event_day:
        event_day = datetime.now().strftime("%Y-%m-%d")

    msg_text = (request.form.get("message") or "").strip()
    upload = request.files.get("mechanic_info_image")
    has_upload = bool(upload and upload.filename)
    if not msg_text and not has_upload:
        raise ValueError("Введите текст сообщения или прикрепите изображение.")

    if has_upload:
        rel_path = save_mechanic_info_image(upload, UPLOAD_MECHANIC_INFO_DIR, user_id)
        image_url = url_for("mechanic_info_upload", rel_path=rel_path)
        msg_text = append_image_link_to_message(msg_text, image_url)

    title = request.form.get("mechanic_info_title", "")
    status = request.form.get("mechanic_info_status", "green")

    vehicle_row = get_vehicle_for_user(db, user_id)
    plate_number = fleet.get("plate_number")
    model_name = None
    if vehicle_row:
        plate_number = vehicle_row.get("plate_number") or plate_number
        model_meta = VEHICLE_MODELS.get(vehicle_row.get("model_code") or "")
        model_name = (model_meta or {}).get("name")

    message_id = add_fleet_vehicle_mechanic_message(
        db,
        int(fleet["id"]),
        user_id,
        msg_text,
        title=title,
        event_date=event_day,
        status=status,
        max_duplicated=duplicate_max,
    )
    _log_mechanic_info_event(
        mechanic_user,
        "SENT",
        message_id=message_id,
        event_date=event_day,
        status=status,
        title=title,
        message=msg_text,
        plate_number=plate_number,
    )

    if duplicate_max:
        mech_name = " ".join(
            p
            for p in (
                mechanic_user.get("last_name"),
                mechanic_user.get("first_name"),
                mechanic_user.get("middle_name"),
            )
            if p
        ).strip() or mechanic_user.get("username") or "Механик"
        max_text = build_mechanic_info_max_text(
            mechanic_name=mech_name,
            max_user_id=max_user_id,
            plate_number=plate_number,
            model_name=model_name,
            event_date=event_day,
            status=status,
            message=msg_text,
            title=title,
        )
        max_result = send_mechanic_info_to_max(
            get_setting_fn,
            text=max_text,
            max_user_id=max_user_id,
        )
        if not max_result.get("ok"):
            raise ValueError(max_result.get("error") or "Не удалось отправить в MAX.")
    return message_id


def _parse_fuel_fills_form(form) -> list[dict]:
    dates = form.getlist("fuel_fill_date")
    liters = form.getlist("fuel_fill_liters")
    prices = form.getlist("fuel_fill_price")
    stations = form.getlist("fuel_fill_station")
    items = []
    for i in range(max(len(dates), len(liters), len(prices), len(stations))):
        fill_date = dates[i] if i < len(dates) else ""
        raw_liters = liters[i] if i < len(liters) else ""
        raw_price = prices[i] if i < len(prices) else ""
        station_name = stations[i] if i < len(stations) else ""
        if (
            not (fill_date or "").strip()
            and not (raw_liters or "").strip()
            and not (raw_price or "").strip()
            and not (station_name or "").strip()
        ):
            continue
        items.append(
            {
                "fill_date": fill_date,
                "liters": raw_liters,
                "price_per_liter": raw_price,
                "station_name": station_name,
            }
        )
    return items


def _create_fuel_report_from_request(db, mechanic_user) -> dict:
    from vehicle_fuel_report import create_fuel_report, parse_fuel_report_period

    mechanic_user = _user_as_dict(mechanic_user)
    start, end = parse_fuel_report_period(
        request.form.get("fuel_report_start"),
        request.form.get("fuel_report_end"),
    )
    return create_fuel_report(
        db,
        int(mechanic_user["id"]),
        mechanic_user,
        start,
        end,
        request.files.getlist("fuel_report_receipts"),
        UPLOAD_FUEL_REPORTS_DIR,
        STATIC_DIR,
    )


def _parse_branding_items_form(form) -> list[dict]:
    from vehicle_fleet import FLEET_BRANDING_PRESETS

    items = []
    for idx, (key, label) in enumerate(FLEET_BRANDING_PRESETS):
        if form.get(f"branding_{key}") == "1":
            items.append(
                {
                    "item_key": key,
                    "label": label,
                    "sort_order": idx,
                }
            )
    return items


def _parse_van_equipment_form(form) -> list[dict]:
    titles = form.getlist("van_equipment_title")
    items = []
    seen: set[str] = set()
    for idx, raw_title in enumerate(titles):
        title = (raw_title or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        items.append(
            {
                "title": title,
                "note": "",
                "sort_order": idx,
            }
        )
    return items


def _staff_autos_context(mechanics_autos, selected_user_id: str, show_admin_stats: bool):
    from vehicle_fleet import (
        build_fleet_preview_detail_context,
        build_vehicle_panel_context,
        get_fleet_vehicle_for_user,
        FLEET_GRID_PREVIEW,
    )

    db = get_db()
    fuel_price = get_fuel_price_setting()
    selected_mechanic = None
    vehicle_panel = {"has_vehicle": False}
    if not (selected_user_id or "").strip():
        return None, vehicle_panel
    try:
        uid = int(selected_user_id)
    except (TypeError, ValueError):
        return None, vehicle_panel
    if uid < 0:
        if FLEET_GRID_PREVIEW:
            return build_fleet_preview_detail_context(
                db, uid, fuel_price, show_admin_stats, get_setting_fn=get_setting
            )
        return None, vehicle_panel
    candidate = get_user_by_id(uid)
    if candidate and candidate["role"] in ("MECHANIC", "EMPLOYEE"):
        selected_mechanic = _user_as_dict(candidate)
        if not get_fleet_vehicle_for_user(db, uid):
            return selected_mechanic, vehicle_panel
        vehicle_panel = build_vehicle_panel_context(
            db, selected_mechanic["id"], fuel_price, show_admin_stats, get_setting_fn=get_setting
        )
    return selected_mechanic, vehicle_panel


def _fallback_mechanic_autos_view(db, mechanics_autos, fuel_price):
    """Вид механика без user_id: первая машина автопарка с закреплённым механиком."""
    from vehicle_fleet import build_vehicle_panel_context, get_fleet_vehicle

    real = next((m for m in mechanics_autos if m.get("fleet_vehicle_id")), None)
    if not real:
        return None, {"has_vehicle": False}
    try:
        fleet_id = int(real["fleet_vehicle_id"])
        user_id = int(real["id"])
    except (TypeError, ValueError, KeyError):
        return None, {"has_vehicle": False}
    if not get_fleet_vehicle(db, fleet_id):
        return None, {"has_vehicle": False}
    mechanic = get_user_by_id(user_id)
    if mechanic:
        mechanic = _user_as_dict(mechanic)
    else:
        mechanic = {
            "id": user_id,
            "last_name": real.get("last_name") or "",
            "first_name": real.get("first_name") or "",
            "middle_name": real.get("middle_name") or "",
            "role": "MECHANIC",
        }
    panel = build_vehicle_panel_context(
        db, user_id, fuel_price, show_admin_stats=False, get_setting_fn=get_setting
    )
    return mechanic, panel


@app.route("/staff/vehicle/<int:user_id>", methods=["POST"])
def staff_vehicle_action(user_id: int):
    user = get_current_user()
    if not user:
        return redirect(url_for("login", portal="staff"))
    if not (is_admin_user(user) or is_any_manager_user(user)):
        return redirect(url_for("login", portal="staff"))

    target = get_user_by_id(user_id)
    if not target or target["role"] not in ("MECHANIC", "EMPLOYEE"):
        flash("Механик не найден.", "error")
        return redirect(request.referrer or url_for("admin_dashboard", section="autos"))

    from vehicle_fleet import (
        add_mileage_log,
        add_oil_change_log,
        get_fleet_vehicle_for_user,
        get_latest_mileage,
        mark_regulation_done,
        unmark_regulation_done,
        save_period_boundary_mileage,
        reset_period_boundary_mileage,
        replace_fuel_fills_in_period,
        add_fuel_fills_in_period,
        delete_fuel_fill,
        set_mechanic_message_admin_state,
        update_fleet_vehicle_branding_items,
        update_fleet_vehicle_important_info,
        update_fleet_vehicle_schedule,
        update_fleet_vehicle_van_equipment,
        upsert_fuel_monthly,
    )

    db = get_db()
    action = (request.form.get("action") or "").strip()
    back = request.referrer
    if not back:
        if can_access_admin_panel(user):
            back = url_for("admin_dashboard", section="autos", user_id=user_id)
        else:
            back = url_for("manager_dashboard", tab="autos", user_id=user_id)

    try:
        if action == "mileage":
            add_mileage_log(
                db,
                user_id,
                int(request.form.get("mileage_km", "0")),
                request.form.get("note", ""),
            )
            flash("Пробег сохранён.", "success")
        elif action == "mileage_period":
            result = save_period_boundary_mileage(
                db,
                user_id,
                request.form.get("boundary", ""),
                int(request.form.get("mileage_km", "0")),
                get_setting_fn=get_setting,
            )
            flash(f"Пробег на {result['day_label']} число сохранён.", "success")
        elif action == "mileage_period_reset":
            reset_period_boundary_mileage(db, user_id, get_setting_fn=get_setting)
            flash("Отметки пробега за период сброшены.", "success")
        elif action == "fuel_fills":
            add_fuel_fills_in_period(
                db,
                user_id,
                request.form.get("fuel_period_start", ""),
                request.form.get("fuel_period_end", ""),
                _parse_fuel_fills_form(request.form),
            )
            flash("Данные по заправкам сохранены.", "success")
        elif action == "fuel_fill_delete":
            delete_fuel_fill(db, user_id, int(request.form.get("fill_id", "0")))
            flash("Заправка удалена.", "success")
        elif action == "fuel_report":
            created = _create_fuel_report_from_request(db, target)
            flash(
                f"Топливный отчёт за {created['start_date']} — {created['end_date']} сформирован.",
                "success",
            )
        elif action == "fuel":
            upsert_fuel_monthly(
                db,
                user_id,
                (request.form.get("year_month") or "").strip(),
                float(request.form.get("amount_rub", "0") or 0),
                int(request.form.get("fill_count", "0") or 0),
                request.form.get("note", ""),
            )
            flash("Данные по топливу сохранены.", "success")
        elif action == "oil":
            add_oil_change_log(
                db,
                user_id,
                int(request.form.get("oil_mileage_km", "0")),
                request.form.get("note", ""),
            )
            flash("Замена масла зафиксирована.", "success")
        elif action == "official_regulation":
            fleet = get_fleet_vehicle_for_user(db, user_id)
            if not fleet:
                raise ValueError("Машина автопарка не найдена.")
            update_fleet_vehicle_schedule(db, int(fleet["id"]), _parse_official_regulation_form(request.form))
            flash("Официальный регламент сохранён.", "success")
        elif action == "important_info":
            fleet = get_fleet_vehicle_for_user(db, user_id)
            if not fleet:
                raise ValueError("Машина автопарка не найдена.")
            update_fleet_vehicle_important_info(db, int(fleet["id"]), _parse_important_info_form(request.form))
            flash("Важная информация сохранена.", "success")
        elif action == "branding_equipment":
            fleet = get_fleet_vehicle_for_user(db, user_id)
            if not fleet:
                raise ValueError("Машина автопарка не найдена.")
            update_fleet_vehicle_branding_items(db, int(fleet["id"]), _parse_branding_items_form(request.form))
            flash("Брендирование и комплектация сохранены.", "success")
        elif action == "van_equipment":
            fleet = get_fleet_vehicle_for_user(db, user_id)
            if not fleet:
                raise ValueError("Машина автопарка не найдена.")
            update_fleet_vehicle_van_equipment(db, int(fleet["id"]), _parse_van_equipment_form(request.form))
            flash("Список оборудования фургона сохранён.", "success")
        elif action == "regulation_done":
            fleet = get_fleet_vehicle_for_user(db, user_id)
            if not fleet:
                raise ValueError("Машина автопарка не найдена.")
            latest = get_latest_mileage(db, user_id)
            if latest is None:
                raise ValueError("Сначала зафиксируйте пробег.")
            sort_order = int(request.form.get("sort_order", "0") or 0)
            mark_regulation_done(db, int(fleet["id"]), sort_order, int(latest))
            flash("Отметка «сделано» сохранена — точка отсчёта обновлена.", "success")
        elif action == "regulation_undo":
            fleet = get_fleet_vehicle_for_user(db, user_id)
            if not fleet:
                raise ValueError("Машина автопарка не найдена.")
            latest = get_latest_mileage(db, user_id)
            if latest is None:
                raise ValueError("Сначала зафиксируйте пробег.")
            sort_order = int(request.form.get("sort_order", "0") or 0)
            unmark_regulation_done(db, int(fleet["id"]), sort_order, int(latest))
            flash("Отметка «выполнено» отменена.", "success")
        elif action == "mechanic_info":
            panel_index = (request.form.get("panel_index") or "1").strip()
            _submit_mechanic_info(db, target, get_setting_fn=get_setting)
            session["mechanic_info_success_panel"] = panel_index
            flash("Сообщение отправлено.", "success")
        elif action == "mechanic_info_accept":
            fleet = get_fleet_vehicle_for_user(db, user_id)
            if not fleet:
                raise ValueError("Машина автопарка не найдена.")
            item = set_mechanic_message_admin_state(
                db,
                int(request.form.get("message_id", "0")),
                int(fleet["id"]),
                "accepted",
            )
            _log_mechanic_info_event(
                target,
                "ACCEPTED",
                message_id=int(item["id"]),
                event_date=item.get("event_date"),
                status=item.get("status"),
                title=item.get("title"),
                message=item.get("message"),
                plate_number=_mechanic_info_plate_number(db, user_id, fleet),
            )
            flash("Обращение отмечено как принято.", "success")
        elif action == "mechanic_info_resolve":
            fleet = get_fleet_vehicle_for_user(db, user_id)
            if not fleet:
                raise ValueError("Машина автопарка не найдена.")
            item = set_mechanic_message_admin_state(
                db,
                int(request.form.get("message_id", "0")),
                int(fleet["id"]),
                "resolved",
            )
            _log_mechanic_info_event(
                target,
                "RESOLVED",
                message_id=int(item["id"]),
                event_date=item.get("event_date"),
                status=item.get("status"),
                title=item.get("title"),
                message=item.get("message"),
                plate_number=_mechanic_info_plate_number(db, user_id, fleet),
            )
            flash("Обращение отмечено как решено.", "success")
        else:
            flash("Неизвестное действие.", "error")
    except (ValueError, TypeError) as exc:
        flash(str(exc), "error")

    return redirect(back)


@app.route("/api/fleet/fuel-analysis/<int:user_id>")
def api_fleet_fuel_analysis(user_id: int):
    user = get_current_user()
    if not user:
        return jsonify({"ok": False, "error": "auth"}), 401
    if not (can_access_admin_panel(user) or is_any_manager_user(user)):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    target = get_user_by_id(user_id)
    if not target or target["role"] not in ("MECHANIC", "EMPLOYEE"):
        return jsonify({"ok": False, "error": "not_found"}), 404

    from vehicle_fleet import build_fuel_analysis

    db = get_db()
    payload = build_fuel_analysis(db, user_id, get_fuel_price_setting())
    return jsonify({"ok": True, **payload})


@app.route("/admin/fleet-settings", methods=["POST"])
def admin_fleet_settings():
    user = get_current_user()
    if not is_admin_user(user):
        return redirect(url_for("login", portal="staff"))

    try:
        day_from = int(request.form.get("fleet_period_day_from", "1") or 1)
        day_to = int(request.form.get("fleet_period_day_to", "0") or 0)
    except (TypeError, ValueError):
        flash("Некорректные дни расчётного периода.", "error")
        return redirect(url_for("admin_dashboard", section="settings"))

    day_from = max(1, min(31, day_from))
    day_to = max(0, min(31, day_to))
    if day_to > 0 and day_to < day_from:
        flash("Конец периода не может быть раньше начала.", "error")
        return redirect(url_for("admin_dashboard", section="settings"))

    set_setting("fleet_period_day_from", str(day_from))
    set_setting("fleet_period_day_to", str(day_to))
    flash("Расчётный период сохранён.", "success")
    return redirect(url_for("admin_dashboard", section="settings"))


@app.route("/admin")
def admin_dashboard():
    user = get_current_user()
    if not can_access_admin_panel(user):
        if is_manager_user(user):
            return redirect(url_for("manager_dashboard"))
        return redirect(url_for("login", portal="staff"))
    section = (request.args.get("section", "") or "").strip().lower()
    legacy_map = {
        "registrations": "users",
        "disk": "settings",
        "admins": "settings",
    }
    section = legacy_map.get(section, section)
    if section not in ("", "users", "reports", "notebooks", "autos", "settings", "equipment"):
        section = ""
    if section and not can_access_admin_section(user, section):
        section = ""
    if section == "users" and not can_manage_users(user):
        section = ""
    if section == "settings" and not is_admin_user(user):
        section = ""
    pending = list_pending_users()
    users = list_all_users()
    mechanics = list_mechanics_with_stats()
    managers = list_managers()
    overview = get_admin_overview_stats()
    dashboard = get_admin_dashboard_data(get_setting)
    selected_user_id = request.args.get("user_id", "").strip()
    selected_mechanic, reports_for_selected, mechanic_kpis = _staff_reports_context(
        mechanics, selected_user_id
    )

    yandex_settings = {
        "token": get_setting("yandex_token", ""),
        "login": get_setting("yandex_login", ""),
        "password": get_setting("yandex_password", ""),
        "folder": get_setting("yandex_folder", "Reports"),
        "excel_enabled": get_setting("yandex_excel_enabled", "1"),
    }
    max_bot_settings = {
        "token": get_setting("max_bot_token", ""),
        "webhook_url": get_setting("max_bot_webhook_url", ""),
        "chat_id": get_setting("max_bot_chat_id", ""),
        "test_mode": get_setting("bot_test_mode", "1"),
    }
    from max_bot.ecosystem import ensure_ecosystem_secret

    bot_ecosystem_secret = ensure_ecosystem_secret(get_setting, set_setting)

    equipment_ctx = equipment_catalog_page_context(request)

    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT user_id, COUNT(*) AS cnt FROM device_invites
        WHERE used_at IS NOT NULL AND used_at != ''
        GROUP BY user_id
        """
    )
    device_used_by_user = {int(row["user_id"]): int(row["cnt"]) for row in cur.fetchall()}

    from vehicle_fleet import (
        list_mechanics_with_vehicles,
        VEHICLE_MODELS,
        build_autos_grid_cards,
        build_vehicle_panel_context,
        list_fleet_vehicles,
        get_fleet_vehicle,
        FLEET_GRID_PREVIEW,
        FLEET_FUEL_TYPE_CHOICES,
    )

    mechanics_autos = list_mechanics_with_vehicles(db)
    fuel_price = get_fuel_price_setting()
    vehicle_panel = {"has_vehicle": False}
    autos_grid_cards = []
    autos_view = (request.args.get("view") or "").strip().lower()
    selected_fleet_vehicle = None
    fleet_schedule_base_url = url_for("admin_dashboard", section="autos")
    fleet_schedule_return_url = fleet_schedule_base_url
    if autos_view not in ("mechanic", ""):
        autos_view = ""
    if section == "autos":
        fleet_id_raw = (request.args.get("fleet_id") or "").strip()
        fleet_id = int(fleet_id_raw) if fleet_id_raw.isdigit() else None
        if selected_user_id:
            show_admin_stats = autos_view != "mechanic"
            selected_mechanic, vehicle_panel = _staff_autos_context(
                mechanics_autos, selected_user_id, show_admin_stats=show_admin_stats
            )
            fleet_schedule_return_url = url_for(
                "admin_dashboard",
                section="autos",
                user_id=selected_user_id,
                view=autos_view or None,
            )
            fleet_schedule_base_url = fleet_schedule_return_url
            if fleet_id is None and vehicle_panel.get("fleet_vehicle_id"):
                fleet_id = int(vehicle_panel["fleet_vehicle_id"])
        else:
            selected_mechanic = None
            autos_grid_cards = build_autos_grid_cards(db, mechanics_autos, fuel_price, get_setting_fn=get_setting)
            fleet_schedule_return_url = url_for(
                "admin_dashboard",
                section="autos",
                view=autos_view or None,
            )
            fleet_schedule_base_url = fleet_schedule_return_url
            if autos_view == "mechanic":
                selected_mechanic, vehicle_panel = _fallback_mechanic_autos_view(
                    db, mechanics_autos, fuel_price
                )
        if fleet_id:
            selected_fleet_vehicle = get_fleet_vehicle(db, fleet_id)
            if selected_fleet_vehicle:
                fleet_schedule_return_url = url_for(
                    "admin_dashboard",
                    section="autos",
                    user_id=selected_user_id or None,
                    view=autos_view or None,
                    fleet_id=fleet_id,
                )
                fleet_schedule_base_url = url_for(
                    "admin_dashboard",
                    section="autos",
                    user_id=selected_user_id or None,
                    view=autos_view or None,
                )

    mechanic_info_success_panel = (
        session.pop("mechanic_info_success_panel", None) if section == "autos" else None
    )

    return render_template(
        "admin.html",
        user=user,
        section=section,
        role_title=staff_role_title(user),
        overview=overview,
        dashboard=dashboard,
        pending_users=pending,
        all_users=users,
        mechanics=mechanics,
        managers=managers,
        selected_mechanic=selected_mechanic,
        reports_for_selected=reports_for_selected,
        mechanic_kpis=mechanic_kpis,
        yandex_settings=yandex_settings,
        max_bot_settings=max_bot_settings,
        bot_ecosystem_secret=bot_ecosystem_secret,
        admin_users=[u for u in users if u["role"] in ("ADMIN", "SUPER_ADMIN")],
        admin_invites=list_admin_invites(),
        smtp_settings={
            "host": get_setting("smtp_host", ""),
            "port": get_setting("smtp_port", "587"),
            "username": get_setting("smtp_username", ""),
            "password": get_setting("smtp_password", ""),
            "from": get_setting("smtp_from", ""),
            "use_tls": get_setting("smtp_use_tls", "1"),
            "api_key": get_setting("mail_api_key", ""),
            "api_mode": get_setting("mail_api_mode", "smtp"),
        },
        equipment_move_password=get_setting("equipment_move_password", "Переезд2026"),
        counterparties_count=len(load_counterparties()),
        staff_mode="admin",
        show_admin_actions=True,
        **equipment_ctx,
        device_used_by_user=device_used_by_user,
        mechanics_autos=mechanics_autos,
        vehicle_panel=vehicle_panel,
        fuel_price_rub_l=fuel_price,
        vehicle_models=VEHICLE_MODELS,
        fleet_vehicles=list_fleet_vehicles(db, only_unassigned=False),
        fleet_vehicles_unassigned=list_fleet_vehicles(db, only_unassigned=True),
        autos_grid_cards=autos_grid_cards,
        autos_view=autos_view,
        fleet_period_settings=get_fleet_period_settings(),
        fleet_grid_preview=FLEET_GRID_PREVIEW,
        selected_fleet_vehicle=selected_fleet_vehicle,
        fleet_schedule_base_url=fleet_schedule_base_url,
        fleet_schedule_return_url=fleet_schedule_return_url,
        fleet_fuel_type_choices=FLEET_FUEL_TYPE_CHOICES,
        mechanic_info_success_panel=mechanic_info_success_panel,
    )


@app.route("/manager")
def manager_dashboard():
    user = get_current_user()
    if not is_manager_user(user):
        if is_admin_user(user):
            return redirect(url_for("admin_dashboard", section="reports"))
        return redirect(url_for("login", portal="staff"))
    mechanics = list_mechanics_with_stats()
    selected_user_id = request.args.get("user_id", "").strip()
    selected_mechanic, reports_for_selected, mechanic_kpis = _staff_reports_context(
        mechanics, selected_user_id
    )
    tab = request.args.get("tab", "reports").strip() or "reports"
    if tab not in ("reports", "notebooks", "equipment", "autos"):
        tab = "reports"
    equipment_ctx = equipment_catalog_page_context(request)
    db = get_db()
    from vehicle_fleet import (
        list_mechanics_with_vehicles,
        build_autos_grid_cards,
        build_vehicle_panel_context,
        get_fleet_vehicle,
        FLEET_GRID_PREVIEW,
    )

    mechanics_autos = list_mechanics_with_vehicles(db)
    fuel_price = get_fuel_price_setting()
    vehicle_panel = {"has_vehicle": False}
    autos_grid_cards = []
    autos_view = (request.args.get("view") or "").strip().lower()
    if autos_view not in ("mechanic", ""):
        autos_view = ""
    if tab == "autos":
        if selected_user_id:
            show_admin_stats = autos_view != "mechanic"
            selected_mechanic, vehicle_panel = _staff_autos_context(
                mechanics_autos, selected_user_id, show_admin_stats=show_admin_stats
            )
        else:
            selected_mechanic = None
            autos_grid_cards = build_autos_grid_cards(db, mechanics_autos, fuel_price, get_setting_fn=get_setting)
            if autos_view == "mechanic":
                selected_mechanic, vehicle_panel = _fallback_mechanic_autos_view(
                    db, mechanics_autos, fuel_price
                )
    mechanic_info_success_panel = (
        session.pop("mechanic_info_success_panel", None) if tab == "autos" else None
    )
    return render_template(
        "manager.html",
        user=user,
        role_title=staff_role_title(user),
        mechanics=mechanics,
        selected_mechanic=selected_mechanic,
        reports_for_selected=reports_for_selected,
        mechanic_kpis=mechanic_kpis,
        staff_mode="manager",
        show_admin_actions=False,
        tab=tab,
        **equipment_ctx,
        mechanics_autos=mechanics_autos,
        vehicle_panel=vehicle_panel,
        fuel_price_rub_l=fuel_price,
        autos_grid_cards=autos_grid_cards,
        autos_view=autos_view,
        fleet_period_settings=get_fleet_period_settings(),
        fleet_grid_preview=FLEET_GRID_PREVIEW,
        mechanic_info_success_panel=mechanic_info_success_panel,
    )


@app.route("/admin/mindmap")
def admin_mindmap():
    user = get_current_user()
    if not is_admin_user(user):
        return redirect(url_for("login"))
    return render_template("admin_mindmap.html", user=user)


@app.route("/admin/mindmap/data")
def admin_mindmap_data():
    user = get_current_user()
    if not is_admin_user(user):
        return jsonify({"error": "unauthorized"}), 401

    now_iso = datetime.now().date().isoformat()

    # Планеты: организации, в которых работали механики.
    organizations = set(load_organizations())
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT DISTINCT organization
        FROM reports
        WHERE organization IS NOT NULL AND organization != '' AND organization != 'None'
        """
    )
    for row in cur.fetchall():
        val = row[0]
        if val:
            organizations.add(val)
    organizations_list = sorted(organizations, key=lambda x: str(x).lower())

    planets = []
    planet_ids = {}
    for org in organizations_list:
        pid = _mindmap_equipment_key(f"planet:{org}", "", "")
        planet_ids[org] = pid
        planets.append({"id": pid, "label": org})

    # Точки: уникальные (organization, equipment_type, serial_number)
    cur.execute(
        """
        SELECT organization, equipment_type, serial_number
        FROM reports
        WHERE organization IS NOT NULL AND organization != ''
          AND serial_number IS NOT NULL AND serial_number != ''
        GROUP BY organization, equipment_type, serial_number
        ORDER BY organization ASC
        """
    )

    equipment = []
    links = []
    for row in cur.fetchall():
        org = row["organization"]
        equipment_type = row["equipment_type"] or ""
        serial_number = row["serial_number"] or ""

        eq_key = _mindmap_equipment_key(org, equipment_type, serial_number)
        last_auto_date_iso = None
        interval_days = None

        sched = _get_equipment_schedule_row(eq_key)
        if sched:
            last_auto_date_iso = sched["last_auto_date_iso"]
            interval_days = sched["interval_days"]
        else:
            last_auto_date_iso = _get_latest_report_date_iso(org, equipment_type, serial_number)
            interval_days = 30  # дефолт для визуала (потом пользователь задаст вручную)

        due_date_iso = _calc_due_date_iso(last_auto_date_iso, interval_days)
        needs_service = bool(due_date_iso and due_date_iso <= now_iso)

        equipment.append(
            {
                "id": eq_key,
                "planetId": planet_ids.get(org) or _mindmap_equipment_key(f"planet:{org}", "", ""),
                "organization": org,
                "equipment_type": equipment_type,
                "serial_number": serial_number,
                "last_auto_date_iso": last_auto_date_iso,
                "interval_days": int(interval_days) if interval_days is not None else None,
                "due_date_iso": due_date_iso,
                "needs_service": needs_service,
            }
        )
        links.append({"from": planet_ids.get(org), "to": eq_key})

    return jsonify({"now_iso": now_iso, "planets": planets, "equipment": equipment, "links": links})


@app.route("/admin/mindmap/schedule/<equipment_key>", methods=["POST"])
def admin_mindmap_save_schedule(equipment_key: str):
    user = get_current_user()
    if not is_admin_user(user):
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    last_auto_date_iso = payload.get("last_auto_date_iso", "")
    if last_auto_date_iso == "":
        last_auto_date_iso = None

    interval_days_raw = payload.get("interval_days", None)
    try:
        interval_days = int(interval_days_raw) if interval_days_raw is not None and interval_days_raw != "" else None
    except Exception:
        interval_days = None

    if interval_days is not None and interval_days < 0:
        interval_days = 0
    if last_auto_date_iso is not None:
        # Проверяем формат YYYY-MM-DD
        parsed = _parse_date_any_to_iso(last_auto_date_iso)
        if not parsed:
            return jsonify({"error": "Invalid date format"}), 400
        last_auto_date_iso = parsed

    organization = payload.get("organization", "").strip()
    equipment_type = payload.get("equipment_type", "").strip()
    serial_number = payload.get("serial_number", "").strip()
    if not organization or not serial_number:
        return jsonify({"error": "Missing organization/serial_number"}), 400

    expected_key = _mindmap_equipment_key(organization, equipment_type, serial_number)
    if expected_key != equipment_key:
        return jsonify({"error": "equipment_key mismatch"}), 400

    _upsert_equipment_schedule(
        equipment_key=equipment_key,
        organization=organization,
        equipment_type=equipment_type,
        serial_number=serial_number,
        last_auto_date_iso=last_auto_date_iso,
        interval_days=interval_days,
    )

    due_date_iso = _calc_due_date_iso(last_auto_date_iso, interval_days)
    now_iso = datetime.now().date().isoformat()
    needs_service = bool(due_date_iso and due_date_iso <= now_iso)

    return jsonify(
        {
            "equipment_key": equipment_key,
            "last_auto_date_iso": last_auto_date_iso,
            "interval_days": interval_days,
            "due_date_iso": due_date_iso,
            "needs_service": needs_service,
        }
    )


@app.route("/admin/user/<int:user_id>/approve", methods=["POST"])
def admin_approve_user(user_id):
    user = get_current_user()
    if not can_manage_users(user):
        return redirect(url_for("login"))
    approve_user(user_id)
    return redirect(url_for("admin_dashboard", section="users"))


@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
def admin_delete_user(user_id):
    user = get_current_user()
    if not can_manage_users(user):
        return redirect(url_for("login"))
    if user_id == user["id"]:
        return redirect(url_for("admin_dashboard", section="users"))
    delete_user(user_id)
    return redirect(url_for("admin_dashboard", section="users"))


def is_valid_staff_password(password: str) -> bool:
    if len(password) < 10:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"\d", password):
        return False
    if not re.match(r"^[A-Za-z0-9]+$", password):
        return False
    return True


@app.route("/admin/fleet-vehicles/create", methods=["POST"])
def admin_fleet_vehicle_create():
    user = get_current_user()
    if not can_manage_users(user):
        return redirect(url_for("login", portal="staff"))
    name = (request.form.get("name") or "").strip()
    plate = (request.form.get("plate_number") or "").strip()
    color = (request.form.get("color") or "").strip()
    is_branded = request.form.get("is_branded") == "1"
    has_field_service = request.form.get("has_field_service") == "1"
    try:
        initial_mileage = int(request.form.get("initial_mileage") or 0)
    except ValueError:
        initial_mileage = 0
    fuel_type = (request.form.get("fuel_type") or "").strip()
    from vehicle_fleet import create_fleet_vehicle, save_fleet_vehicle_photos

    db = get_db()
    try:
        vehicle_id = create_fleet_vehicle(
            db,
            name=name,
            plate_number=plate,
            color=color,
            is_branded=is_branded,
            has_field_service=has_field_service,
            initial_mileage=initial_mileage,
            fuel_type=fuel_type,
            schedule_items=[],
        )
        slot_files = {}
        for slot in range(1, 5):
            uploaded = request.files.get(f"vehicle_photo_{slot}")
            if uploaded and uploaded.filename:
                slot_files[slot] = uploaded
        if slot_files:
            save_fleet_vehicle_photos(db, vehicle_id, slot_files, upload_dir=UPLOAD_FLEET_PHOTOS_DIR)
        flash("Машина добавлена в автопарк. Настройте регламент в разделе «Автопарк».", "success")
        return redirect(url_for("admin_dashboard", section="autos", fleet_id=vehicle_id))
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin_dashboard", section="users"))


@app.route("/admin/fleet-vehicles/<int:vehicle_id>/edit-data")
def admin_fleet_vehicle_edit_data(vehicle_id: int):
    user = get_current_user()
    if not can_manage_users(user):
        return jsonify({"error": "forbidden"}), 403
    from vehicle_fleet import get_fleet_vehicle

    db = get_db()
    vehicle = get_fleet_vehicle(db, vehicle_id)
    if not vehicle:
        return jsonify({"error": "not found"}), 404
    photos = {}
    for photo in vehicle.get("photos") or []:
        photos[str(photo["slot"])] = url_for("fleet_vehicle_photo", filename=photo["file_name"])
    return jsonify(
        {
            "id": vehicle["id"],
            "name": vehicle.get("name") or "",
            "plate_number": vehicle.get("plate_number") or "",
            "color": vehicle.get("color") or "",
            "fuel_type": vehicle.get("fuel_type") or "",
            "initial_mileage": int(vehicle.get("initial_mileage") or 0),
            "is_branded": bool(vehicle.get("is_branded")),
            "has_field_service": bool(vehicle.get("has_field_service")),
            "photos": photos,
        }
    )


@app.route("/admin/fleet-vehicles/<int:vehicle_id>/edit", methods=["POST"])
def admin_fleet_vehicle_edit(vehicle_id: int):
    user = get_current_user()
    if not can_manage_users(user):
        return redirect(url_for("login", portal="staff"))
    name = (request.form.get("name") or "").strip()
    plate = (request.form.get("plate_number") or "").strip()
    color = (request.form.get("color") or "").strip()
    is_branded = request.form.get("is_branded") == "1"
    has_field_service = request.form.get("has_field_service") == "1"
    try:
        initial_mileage = int(request.form.get("initial_mileage") or 0)
    except ValueError:
        initial_mileage = 0
    fuel_type = (request.form.get("fuel_type") or "").strip()
    from vehicle_fleet import delete_fleet_vehicle_photos, save_fleet_vehicle_photos, update_fleet_vehicle

    db = get_db()
    try:
        update_fleet_vehicle(
            db,
            vehicle_id,
            name=name,
            plate_number=plate,
            color=color,
            is_branded=is_branded,
            has_field_service=has_field_service,
            initial_mileage=initial_mileage,
            fuel_type=fuel_type,
        )
        slot_files = {}
        remove_slots = []
        for slot in range(1, 5):
            uploaded = request.files.get(f"vehicle_photo_{slot}")
            if uploaded and uploaded.filename:
                slot_files[slot] = uploaded
            elif request.form.get(f"remove_vehicle_photo_{slot}") == "1":
                remove_slots.append(slot)
        if remove_slots:
            delete_fleet_vehicle_photos(db, vehicle_id, remove_slots, upload_dir=UPLOAD_FLEET_PHOTOS_DIR)
        if slot_files:
            save_fleet_vehicle_photos(db, vehicle_id, slot_files, upload_dir=UPLOAD_FLEET_PHOTOS_DIR)
        flash("Данные машины сохранены.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin_dashboard", section="users"))


@app.route("/admin/fleet-vehicles/<int:vehicle_id>/delete", methods=["POST"])
def admin_fleet_vehicle_delete(vehicle_id: int):
    user = get_current_user()
    if not can_manage_users(user):
        return redirect(url_for("login", portal="staff"))
    from vehicle_fleet import delete_fleet_vehicle

    db = get_db()
    try:
        delete_fleet_vehicle(db, vehicle_id, upload_dir=UPLOAD_FLEET_PHOTOS_DIR)
        flash("Машина удалена из автопарка.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin_dashboard", section="users"))


@app.route("/admin/fleet-vehicles/<int:vehicle_id>/schedule", methods=["POST"])
def admin_fleet_vehicle_schedule(vehicle_id: int):
    user = get_current_user()
    if not can_manage_users(user):
        return redirect(url_for("login", portal="staff"))
    return_url = (request.form.get("return_url") or "").strip()
    schedule_items = _parse_fleet_schedule_form(request.form)
    from vehicle_fleet import get_fleet_vehicle, update_fleet_vehicle_schedule

    db = get_db()
    if not get_fleet_vehicle(db, vehicle_id):
        flash("Машина не найдена в автопарке.", "error")
        return redirect(url_for("admin_dashboard", section="autos"))
    try:
        update_fleet_vehicle_schedule(db, vehicle_id, schedule_items)
        flash("Регламент обслуживания сохранён.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    if return_url.startswith("/"):
        return redirect(return_url)
    return redirect(url_for("admin_dashboard", section="autos", fleet_id=vehicle_id))


@app.route("/admin/users/create", methods=["POST"])
def admin_create_user():
    user = get_current_user()
    if not can_manage_users(user):
        return redirect(url_for("login"))
    allowed_roles = creatable_staff_roles(user)
    role = (request.form.get("role", "MECHANIC") or "MECHANIC").strip().upper()
    if role not in allowed_roles:
        role = "MECHANIC" if "MECHANIC" in allowed_roles else (allowed_roles[0] if allowed_roles else "MECHANIC")
    last_name = request.form.get("last_name", "").strip()
    first_name = request.form.get("first_name", "").strip()
    middle_name = request.form.get("middle_name", "").strip()
    position = staff_role_position(role)
    login_val = request.form.get("login", "").strip()
    password = request.form.get("password", "")
    is_approved = 1 if request.form.get("is_approved") == "1" else 0
    max_user_id = request.form.get("max_user_id", "").strip() or None
    if role != "MECHANIC":
        is_approved = 1
    if not last_name or not first_name or not login_val or not password:
        return redirect(url_for("admin_dashboard", section="users"))
    if not is_valid_staff_password(password):
        return redirect(url_for("admin_dashboard", section="users"))
    if get_user_by_login(login_val):
        return redirect(url_for("admin_dashboard", section="users"))
    vehicle_model = None
    vehicle_plate = None
    fleet_vehicle_id = None
    if role == "MECHANIC":
        raw_fv = (request.form.get("fleet_vehicle_id") or "").strip()
        if raw_fv:
            try:
                fleet_vehicle_id = int(raw_fv)
            except ValueError:
                fleet_vehicle_id = None
        if not fleet_vehicle_id:
            return redirect(url_for("admin_dashboard", section="users"))
    user_id = create_staff_user(
        login=login_val,
        password=password,
        last_name=last_name,
        first_name=first_name,
        middle_name=middle_name,
        position=position,
        role=role,
        is_approved=is_approved,
        vehicle_model=vehicle_model,
        vehicle_plate=vehicle_plate,
        fleet_vehicle_id=fleet_vehicle_id,
    )
    if max_user_id:
        update_user(user_id, max_user_id=max_user_id)
    photo = request.files.get("photo")
    if photo and photo.filename:
        ext = os.path.splitext(secure_filename(photo.filename))[1] or ".jpg"
        filename = f"user_{user_id}{ext}"
        path = os.path.join(UPLOAD_PHOTOS_DIR, filename)
        photo.save(path)
        update_user(user_id, photo_path=filename)
    return redirect(url_for("admin_dashboard", section="users"))


@app.route("/admin/user/<int:user_id>/edit", methods=["GET", "POST"])
def admin_edit_user(user_id):
    admin_user = get_current_user()
    if not can_manage_users(admin_user):
        return redirect(url_for("login"))
    target = get_user_by_id(user_id)
    if not target:
        return redirect(url_for("admin_dashboard", section="users"))

    # Ведущий менеджер / админ не редактируют админов; супер-админ — всех
    editable_roles = ("MECHANIC", "EMPLOYEE", "MANAGER", "LEAD_MANAGER")
    if is_admin_user(admin_user) and not is_super_admin(admin_user):
        editable_roles = ("MECHANIC", "EMPLOYEE", "MANAGER", "LEAD_MANAGER")
    if is_lead_manager_user(admin_user):
        editable_roles = ("MECHANIC", "EMPLOYEE", "MANAGER")
    if not is_super_admin(admin_user) and target["role"] not in editable_roles:
        return redirect(url_for("admin_dashboard", section="users"))

    if request.method == "POST":
        last_name = request.form.get("last_name", "").strip()
        first_name = request.form.get("first_name", "").strip()
        middle_name = request.form.get("middle_name", "").strip()
        position = request.form.get("position", "").strip()
        is_approved = 1 if request.form.get("is_approved") == "1" else 0
        max_user_id = request.form.get("max_user_id", "").strip() or None

        target_role = target["role"]
        if target_role in ("MECHANIC", "EMPLOYEE"):
            target_role = "MECHANIC"
        elif target_role not in STAFF_ROLE_DEFS:
            target_role = "MECHANIC"
        if not position:
            position = staff_role_position(target_role)

        update_user(
            user_id,
            last_name=last_name or None,
            first_name=first_name or None,
            middle_name=middle_name or None,
            position=position or None,
            role=target_role,
            is_approved=is_approved,
            max_user_id=max_user_id,
        )
        if request.form.get("create_device_invite") == "1":
            create_device_invite(user_id)
        photo = request.files.get("photo")
        if photo and photo.filename:
            ext = os.path.splitext(secure_filename(photo.filename))[1] or ".jpg"
            filename = f"user_{user_id}{ext}"
            path = os.path.join(UPLOAD_PHOTOS_DIR, filename)
            photo.save(path)
            update_user(user_id, photo_path=filename)
        new_pass = request.form.get("new_password", "").strip()
        if new_pass:
            update_user(user_id, password_hash=generate_password_hash(new_pass))
        if target_role == "MECHANIC":
            raw_fv = (request.form.get("fleet_vehicle_id") or "").strip()
            fleet_vehicle_id = None
            if raw_fv:
                try:
                    fleet_vehicle_id = int(raw_fv)
                except ValueError:
                    fleet_vehicle_id = None
            from vehicle_fleet import (
                assign_fleet_vehicle_to_user,
                clear_fleet_vehicle_for_user,
                get_fleet_vehicle_for_user,
                get_vehicle_for_user,
            )

            db = get_db()
            try:
                if fleet_vehicle_id:
                    assign_fleet_vehicle_to_user(db, user_id, fleet_vehicle_id)
                elif get_fleet_vehicle_for_user(db, user_id) or get_vehicle_for_user(db, user_id):
                    clear_fleet_vehicle_for_user(db, user_id)
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("admin_dashboard", section="users"))
        flash("Данные сотрудника сохранены.", "success")
        return redirect(url_for("admin_dashboard", section="users"))
    from vehicle_fleet import (
        get_fleet_vehicle_for_user,
        get_vehicle_for_user,
        list_fleet_vehicles_for_assign,
    )

    mechanic_vehicle = None
    fleet_vehicles_for_assign = []
    current_fleet_vehicle_id = None
    if target["role"] in ("MECHANIC", "EMPLOYEE"):
        db = get_db()
        mechanic_vehicle = get_vehicle_for_user(db, user_id)
        fleet_vehicles_for_assign = list_fleet_vehicles_for_assign(db, user_id)
        fv = get_fleet_vehicle_for_user(db, user_id)
        if fv:
            current_fleet_vehicle_id = fv["id"]
        elif mechanic_vehicle and mechanic_vehicle.get("fleet_vehicle_id"):
            current_fleet_vehicle_id = mechanic_vehicle["fleet_vehicle_id"]
    return render_template(
        "admin_user_edit.html",
        user=admin_user,
        target=target,
        is_super=is_super_admin(admin_user),
        device_invites=list_device_invites_for_user(user_id),
        fleet_vehicles_for_assign=fleet_vehicles_for_assign,
        current_fleet_vehicle_id=current_fleet_vehicle_id,
        mechanic_vehicle=mechanic_vehicle,
    )


@app.route("/admin/user/<int:user_id>/edit-data")
def admin_edit_user_data(user_id):
    admin_user = get_current_user()
    if not can_manage_users(admin_user):
        return jsonify({"error": "forbidden"}), 403
    target = get_user_by_id(user_id)
    if not target:
        return jsonify({"error": "not found"}), 404
    editable_roles = ("MECHANIC", "EMPLOYEE", "MANAGER", "LEAD_MANAGER")
    if is_lead_manager_user(admin_user):
        editable_roles = ("MECHANIC", "EMPLOYEE", "MANAGER")
    if not is_super_admin(admin_user) and target["role"] not in editable_roles:
        return jsonify({"error": "forbidden"}), 403

    from vehicle_fleet import get_fleet_vehicle_for_user, get_vehicle_for_user, list_fleet_vehicles_for_assign

    def field(row, key, default=""):
        try:
            val = row[key]
        except (KeyError, IndexError, TypeError):
            return default
        return default if val is None else val

    is_mechanic = target["role"] in ("MECHANIC", "EMPLOYEE")
    fleet_vehicle_id = None
    fleet_vehicles = []
    if is_mechanic:
        db = get_db()
        mv = get_vehicle_for_user(db, user_id)
        fv = get_fleet_vehicle_for_user(db, user_id)
        if fv:
            fleet_vehicle_id = fv["id"]
        elif mv and mv.get("fleet_vehicle_id"):
            fleet_vehicle_id = mv["fleet_vehicle_id"]
        fleet_vehicles = [
            {"id": v["id"], "label": v.get("display_label") or f"{v.get('name', '')} — {v.get('plate_number', '')}"}
            for v in list_fleet_vehicles_for_assign(db, user_id)
        ]

    photo_url = ""
    photo_path = field(target, "photo_path")
    if photo_path:
        photo_url = url_for("user_photo", filename=photo_path)

    invites = []
    if is_mechanic:
        for inv in list_device_invites_for_user(user_id):
            invites.append(
                {
                    "code": inv["code"],
                    "created_at": inv["created_at"],
                    "used_at": inv["used_at"] if inv["used_at"] else None,
                }
            )

    return jsonify(
        {
            "id": target["id"],
            "login": target["login"],
            "last_name": field(target, "last_name"),
            "first_name": field(target, "first_name"),
            "middle_name": field(target, "middle_name"),
            "position": field(target, "position"),
            "role": target["role"],
            "is_approved": bool(target["is_approved"]),
            "max_user_id": field(target, "max_user_id"),
            "photo_url": photo_url,
            "is_mechanic": is_mechanic,
            "fleet_vehicle_id": fleet_vehicle_id,
            "fleet_vehicles": fleet_vehicles,
            "device_invites": invites,
        }
    )


@app.route("/admin/report/<int:report_id>/signed", methods=["POST"])
def admin_report_signed(report_id):
    user = get_current_user()
    if not is_admin_user(user):
        return redirect(url_for("login"))
    row = get_report(report_id)
    if not row:
        return redirect(url_for("admin_dashboard"))
    signed = request.form.get("is_signed") == "1"
    set_report_signed(report_id, signed=signed, admin_id=user["id"])
    return redirect(url_for("admin_dashboard", section="reports", user_id=row["user_id"]))


@app.route("/admin/report/<int:report_id>/verified", methods=["POST"])
def admin_report_verified(report_id):
    user = get_current_user()
    if not is_admin_user(user):
        return redirect(url_for("login"))
    row = get_report(report_id)
    if not row:
        return redirect(url_for("admin_dashboard"))
    verified = request.form.get("is_verified") == "1"
    set_report_verified(report_id, verified=verified, admin_id=user["id"])
    return redirect(url_for("admin_dashboard", section="reports", user_id=row["user_id"]))


@app.route("/admin/settings/yandex", methods=["POST"])
def admin_yandex_settings():
    user = get_current_user()
    if not can_configure_integrations(user):
        return redirect(url_for("login"))
    group = (request.form.get("settings_group") or "pairing").strip()
    if group == "excel":
        set_setting("yandex_excel_enabled", "1" if request.form.get("excel_enabled") == "1" else "0")
    else:
        set_setting("yandex_token", request.form.get("token", "").strip())
        set_setting("yandex_login", request.form.get("login", "").strip())
        set_setting("yandex_password", request.form.get("password", "").strip())
        set_setting("yandex_folder", request.form.get("folder", "Reports").strip() or "Reports")
    return redirect(url_for("admin_dashboard", section="settings"))


@app.route("/admin/settings/max-bot", methods=["POST"])
def admin_max_bot_settings():
    user = get_current_user()
    if not can_configure_integrations(user):
        return redirect(url_for("login"))
    set_setting("max_bot_token", request.form.get("token", "").strip())
    set_setting("max_bot_webhook_url", request.form.get("webhook_url", "").strip())
    set_setting("max_bot_chat_id", request.form.get("chat_id", "").strip())
    test_mode = "1" if request.form.get("bot_test_mode") == "1" else "0"
    set_setting("bot_test_mode", test_mode)
    if request.form.get("regenerate_bot_secret") == "1":
        import secrets

        set_setting("bot_ecosystem_secret", secrets.token_urlsafe(32))
    return redirect(url_for("admin_dashboard", section="settings"))


@app.route("/admin/bot-test", methods=["GET", "POST"])
def admin_bot_test():
    user = get_current_user()
    if not can_configure_integrations(user):
        return redirect(url_for("login"))

    from max_bot.ecosystem import ensure_ecosystem_secret, is_test_mode

    bot_secret = ensure_ecosystem_secret(get_setting, set_setting)
    result = None
    lookup_result = None
    mechanics = list_mechanics_with_stats()

    if request.method == "POST":
        lookup_query = request.form.get("lookup_serial", "").strip()
        if request.form.get("form_type") == "lookup" and lookup_query:
            from max_bot.equipment_info import build_equipment_info, parse_lookup_command

            serial = parse_lookup_command(lookup_query) or lookup_query.lstrip("#/").strip().upper()
            lookup_result = build_equipment_info(get_db, serial)
        else:
            from max_bot.processor import process_uploaded_files

            upload = request.files.get("image")
            if not upload or not upload.filename:
                result = {"ok": False, "message": "Выберите файл изображения"}
            else:
                uploads_root = os.path.join(DATA_DIR, "bot_uploads")
                os.makedirs(uploads_root, exist_ok=True)
                safe_name = secure_filename(upload.filename) or "scan.jpg"
                save_path = os.path.join(
                    uploads_root,
                    "inbox",
                    datetime.now().strftime("%Y%m%d"),
                    f"{datetime.now().strftime('%H%M%S')}_{safe_name}",
                )
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                upload.save(save_path)
                user_id_raw = request.form.get("user_id", "").strip()
                report_id_raw = request.form.get("report_id", "").strip()
                user_id = int(user_id_raw) if user_id_raw.isdigit() else None
                report_id = int(report_id_raw) if report_id_raw.isdigit() else None
                caption = request.form.get("caption", "").strip()
                result = process_uploaded_files(
                    file_paths=[save_path],
                    caption=caption,
                    user_id=user_id,
                    report_id=report_id,
                    test_mode=is_test_mode(get_setting),
                    get_db=get_db,
                    get_setting=get_setting,
                    uploads_root=uploads_root,
                )
                sig = result.get("signature") or {}
                preview = sig.get("preview_path")
                if preview and preview.startswith(uploads_root):
                    sig["preview_rel"] = os.path.relpath(preview, uploads_root).replace("\\", "/")
                    result["signature"] = sig

    return render_template(
        "admin_bot_test.html",
        user=user,
        role_title=staff_role_title(user),
        bot_secret=bot_secret,
        bot_test_mode=is_test_mode(get_setting),
        mechanics=mechanics,
        result=result,
        lookup_result=lookup_result,
    )


@app.route("/api/bot/verify", methods=["POST"])
def api_bot_verify():
    """API для бота / внешнего процесса. Только с X-Bot-Ecosystem-Secret."""
    from max_bot.ecosystem import is_test_mode, validate_ecosystem_request
    from max_bot.processor import process_uploaded_files

    secret = request.headers.get("X-Bot-Ecosystem-Secret", "")
    ok, reason = validate_ecosystem_request(secret, get_setting)
    if not ok:
        return jsonify({"ok": False, "error": reason}), 403

    upload = request.files.get("image")
    if not upload:
        return jsonify({"ok": False, "error": "image_required"}), 400

    uploads_root = os.path.join(DATA_DIR, "bot_uploads")
    os.makedirs(uploads_root, exist_ok=True)
    save_path = os.path.join(uploads_root, "api", f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secure_filename(upload.filename)}")
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    upload.save(save_path)

    user_id = request.form.get("user_id", type=int)
    report_id = request.form.get("report_id", type=int)
    caption = request.form.get("caption", "")

    result = process_uploaded_files(
        file_paths=[save_path],
        caption=caption,
        user_id=user_id,
        report_id=report_id,
        test_mode=is_test_mode(get_setting),
        get_db=get_db if user_id else None,
        get_setting=get_setting,
        uploads_root=uploads_root,
    )
    status = 200 if result.get("ok") else 422
    return jsonify(result), status


@app.route("/bot-uploads/<path:rel_path>")
def bot_uploads_file(rel_path):
    user = get_current_user()
    if not is_staff_user(user):
        return "", 404
    safe = os.path.normpath(rel_path)
    if safe.startswith(".."):
        return "", 404
    full = os.path.join(DATA_DIR, "bot_uploads", safe)
    if not os.path.isfile(full):
        return "", 404
    return send_from_directory(os.path.dirname(full), os.path.basename(full))


@app.route("/uploads/mechanic-info/<path:rel_path>")
def mechanic_info_upload(rel_path):
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    safe = os.path.normpath(rel_path).replace("\\", "/")
    if safe.startswith(".."):
        return "", 404
    parts = safe.split("/")
    if len(parts) != 2 or not parts[0].isdigit():
        return "", 404
    owner_id = int(parts[0])
    file_name = secure_filename(parts[1])
    if not file_name:
        return "", 404
    if int(user["id"]) != owner_id and not (
        is_admin_user(user) or is_any_manager_user(user)
    ):
        return "", 403
    folder = os.path.join(UPLOAD_MECHANIC_INFO_DIR, str(owner_id))
    full = os.path.join(folder, file_name)
    if not os.path.isfile(full):
        return "", 404
    return send_from_directory(folder, file_name)


@app.route("/vehicle/fuel-report/<int:report_id>")
def fuel_report_pdf(report_id):
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    from vehicle_fuel_report import fuel_report_pdf_abs_path, get_fuel_report

    db = get_db()
    report = get_fuel_report(db, report_id)
    if not report:
        return "", 404
    owner_id = int(report["user_id"])
    if int(user["id"]) != owner_id and not (is_admin_user(user) or is_any_manager_user(user)):
        return "", 403
    path = fuel_report_pdf_abs_path(UPLOAD_FUEL_REPORTS_DIR, report)
    if not path:
        return "", 404
    filename = f"fuel_report_{report['start_date']}_{report['end_date']}.pdf"
    return send_file(path, mimetype="application/pdf", download_name=filename, as_attachment=False)


@app.route("/admin/user/<int:user_id>/set_role", methods=["POST"])
def admin_set_user_role(user_id):
    user = get_current_user()
    if not is_super_admin(user):
        return redirect(url_for("admin_dashboard", section="users"))
    target = get_user_by_id(user_id)
    if not target:
        return redirect(url_for("admin_dashboard", section="users"))
    role = (request.form.get("role", "ADMIN") or "ADMIN").strip().upper()
    if role not in ("ADMIN", "SUPER_ADMIN"):
        role = "ADMIN"
    update_user(user_id, role=role)
    return redirect(url_for("admin_dashboard", section="users"))


@app.route("/admin/invite", methods=["POST"])
def admin_invite():
    user = get_current_user()
    if not is_super_admin(user):
        return redirect(url_for("admin_dashboard", section="users"))
    email = (request.form.get("email", "") or "").strip()
    if email:
        ok, err = send_admin_invite_email(email)
        create_admin_invite(
            email=email,
            invited_by=user["id"],
            invited_role="ADMIN",
            status="SENT" if ok else "FAILED",
            error_text=err,
        )
    return redirect(url_for("admin_dashboard", section="users"))


@app.route("/admin/settings/mail", methods=["POST"])
def admin_mail_settings():
    user = get_current_user()
    if not is_super_admin(user):
        return redirect(url_for("admin_dashboard", section="settings"))
    set_setting("smtp_host", request.form.get("smtp_host", "").strip())
    set_setting("smtp_port", request.form.get("smtp_port", "").strip() or "587")
    set_setting("smtp_username", request.form.get("smtp_username", "").strip())
    set_setting("smtp_password", request.form.get("smtp_password", "").strip())
    set_setting("smtp_from", request.form.get("smtp_from", "").strip())
    set_setting("smtp_use_tls", "1" if request.form.get("smtp_use_tls") == "1" else "0")
    set_setting("mail_api_mode", request.form.get("api_mode", "smtp").strip() or "smtp")
    set_setting("mail_api_key", request.form.get("api_key", "").strip())
    return redirect(url_for("admin_dashboard", section="settings"))


@app.route("/admin/settings/equipment-move-password", methods=["POST"])
def admin_equipment_move_password():
    user = get_current_user()
    if not is_super_admin(user):
        return redirect(url_for("admin_dashboard", section="settings"))
    new_password = (request.form.get("equipment_move_password") or "").strip()
    if new_password:
        set_setting("equipment_move_password", new_password)
    return redirect(url_for("admin_dashboard", section="settings"))


@app.route("/admin/settings/counterparties-xml", methods=["POST"])
def admin_counterparties_xml_upload():
    """Загрузка базы компаний/контрагентов из XML (название + место эксплуатации)."""
    user = get_current_user()
    if not is_admin_user(user):
        return redirect(url_for("login", portal="staff"))
    upload = request.files.get("counterparties_xml")
    if not upload or not (upload.filename or "").strip():
        flash("Выберите XML-файл для загрузки.", "error")
        return redirect(url_for("admin_dashboard", section="settings"))
    filename = (upload.filename or "").lower()
    if not filename.endswith(".xml"):
        flash("Нужен файл с расширением .xml", "error")
        return redirect(url_for("admin_dashboard", section="settings"))
    raw = upload.read()
    try:
        imported = parse_counterparties_xml(raw)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin_dashboard", section="settings"))
    if not imported:
        flash("В XML не найдено ни одной компании/контрагента.", "error")
        return redirect(url_for("admin_dashboard", section="settings"))
    replace = request.form.get("replace_counterparties") == "1"
    stats = merge_counterparties(imported, replace=replace)
    sync_stats = sync_counterparties_from_reports()
    if replace:
        flash(
            f"База заменена: загружено {stats.get('imported', 0)} записей "
            f"(всего {stats.get('total', 0)}). "
            f"Из отчётов добавлено мест: {sync_stats.get('added_locations', 0)}.",
            "success",
        )
    else:
        flash(
            f"База обновлена: новых компаний {stats.get('added', 0)}, "
            f"обновлено {stats.get('updated', 0)}, всего {stats.get('total', 0)}. "
            f"Дубликаты отсечены.",
            "success",
        )
    return redirect(url_for("admin_dashboard", section="settings"))


@app.route("/admin/backup/start", methods=["POST"])
def admin_backup_start():
    """Запуск создания ZIP-бэкапа (db | full)."""
    user = get_current_user()
    if not can_manage_backups(user):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode") or request.form.get("mode") or "db").strip().lower()
    if mode not in ("db", "full"):
        return jsonify({"ok": False, "error": "bad_mode"}), 400
    from backup_manager import start_backup_job

    backups_dir = os.path.join(DATA_DIR, "backups")
    job_id = start_backup_job(data_dir=DATA_DIR, backups_dir=backups_dir, mode=mode)
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/admin/backup/status/<job_id>")
def admin_backup_status(job_id):
    user = get_current_user()
    if not can_manage_backups(user):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    from backup_manager import get_job

    job = get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "not_found"}), 404
    out = {
        "ok": True,
        "status": job.get("status"),
        "progress": job.get("progress", 0),
        "message": job.get("message", ""),
        "mode": job.get("mode"),
        "kind": job.get("kind"),
        "filename": job.get("filename"),
    }
    if job.get("status") == "done" and job.get("filename"):
        out["download_url"] = url_for(
            "admin_backup_download", filename=job["filename"]
        )
    return jsonify(out)


@app.route("/admin/backup/download/<path:filename>")
def admin_backup_download(filename):
    user = get_current_user()
    if not can_manage_backups(user):
        return redirect(url_for("login", portal="staff"))
    safe = os.path.basename(filename or "")
    if not safe.endswith(".zip") or ".." in safe:
        return "", 404
    backups_dir = os.path.join(DATA_DIR, "backups")
    path = os.path.join(backups_dir, safe)
    if not os.path.isfile(path):
        return "", 404
    return send_file(path, as_attachment=True, download_name=safe)


@app.route("/admin/backup/restore", methods=["POST"])
def admin_backup_restore():
    """Загрузка ZIP и запуск восстановления."""
    user = get_current_user()
    if not can_manage_backups(user):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if request.form.get("confirm_restore") != "1":
        return jsonify({"ok": False, "error": "confirm_required"}), 400
    upload = request.files.get("backup_zip")
    if not upload or not (upload.filename or "").strip():
        return jsonify({"ok": False, "error": "file_required"}), 400
    filename = (upload.filename or "").lower()
    if not filename.endswith(".zip"):
        return jsonify({"ok": False, "error": "need_zip"}), 400

    from backup_manager import start_restore_job

    inbox = os.path.join(DATA_DIR, "backups", "incoming")
    os.makedirs(inbox, exist_ok=True)
    saved_name = f"restore_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secure_filename(upload.filename)}"
    saved_path = os.path.join(inbox, saved_name)
    upload.save(saved_path)
    job_id = start_restore_job(zip_path=saved_path, data_dir=DATA_DIR)
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/user_photo/<path:filename>")
def user_photo(filename):
    """Раздача загруженного фото пользователя."""
    if ".." in filename or "/" in filename.replace("\\", "/"):
        return "", 404
    return send_from_directory(UPLOAD_PHOTOS_DIR, filename)


@app.route("/fleet_photo/<path:filename>")
def fleet_vehicle_photo(filename):
    """Раздача фото машины автопарка."""
    if ".." in filename or "/" in filename.replace("\\", "/"):
        return "", 404
    if not filename.lower().endswith(".jpg"):
        return "", 404
    return send_from_directory(UPLOAD_FLEET_PHOTOS_DIR, filename)


@app.route("/report/new")
def report_new():
    user = get_current_user()
    if not is_mechanic_user(user):
        return redirect(url_for("login"))
    organizations = load_organizations()
    company_code = normalize_company_code(session.get("company_code"))
    fio = " ".join(
        p
        for p in [user["last_name"], user["first_name"], user["middle_name"]]
        if p
    ).strip()
    return render_template(
        "form_wizard.html",
        organizations=organizations,
        mechanic_name=fio or user["login"],
        **_wizard_form_context(user, company_code),
    )


@app.route("/report/<int:report_id>/delete", methods=["POST"])
def report_delete(report_id):
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    if is_admin_user(user):
        delete_report(report_id)
        return redirect(request.referrer or url_for("admin_dashboard", section="reports"))
    if is_manager_user(user):
        return redirect(url_for("manager_dashboard"))
    delete_report(report_id, user_id=user["id"])
    return redirect(url_for("account"))


@app.route("/report/<int:report_id>/copy")
def report_copy(report_id):
    user = get_current_user()
    if not is_mechanic_user(user):
        return redirect(url_for("login"))
    row = get_report(report_id)
    if not row or row["user_id"] != user["id"]:
        return redirect(url_for("account"))

    organizations = load_organizations()
    company_code = session.get("company_code") or "ALFASS"
    fio = " ".join(
        p
        for p in [user["last_name"], user["first_name"], user["middle_name"]]
        if p
    ).strip()

    materials = []
    try:
        raw = row["materials_json"] or "[]"
        materials = json.loads(raw)
    except Exception:
        materials = []

    report_prefill = {
        "date": "", 
        "organization": row["organization"],
        "equipment_type": row["equipment_type"],
        "serial_number": row["serial_number"],
        "internal_number": row["internal_number"],
        "location": row["location"],
        "engine_hours": row["engine_hours"],
        "year": row["year"],
        "work_types": row["work_types"] or "",
        "work_description": row["work_description"] or "",
        "recommendations": row["recommendations"] or "",
    }

    return render_template(
        "form_wizard.html",
        organizations=organizations,
        mechanic_name=fio or user["login"],
        report=report_prefill,
        materials=materials,
        user=user,
        selected_company=company_code,
    )


@app.route("/report/<int:report_id>/pdf")
def report_pdf_view(report_id):
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    row = get_report(report_id)
    if not row:
        if is_staff_user(user):
            return redirect(url_for(staff_home_endpoint(user)))
        return redirect(url_for("account"))

    if (not is_admin_user(user)) and (not is_any_manager_user(user)) and row["user_id"] != user["id"]:
        return redirect(url_for("account"))

    owner = get_user_by_id(row["user_id"]) or user

    try:
        materials = json.loads(row["materials_json"] or "[]")
    except Exception:
        materials = []

    report_dict = {
        "report_id": row["report_id"],
        "created_at": row["created_at"],
        "date": row["date"] or "",
        "organization": row["organization"],
        "equipment_type": row["equipment_type"],
        "serial_number": row["serial_number"],
        "internal_number": row["internal_number"],
        "location": row["location"],
        "engine_hours": row["engine_hours"],
        "year": row["year"],
        "work_types": row["work_types"] or "",
        "work_description": row["work_description"] or "",
        "recommendations": row["recommendations"] or "",
        "materials_json": row["materials_json"] or "[]",
        "mechanic_code": "",
        "mechanic_name": format_short_name(
            owner["last_name"],
            owner["first_name"],
            owner["middle_name"],
        )
        or owner["login"],
        "mechanic_position": owner["position"] if "position" in owner.keys() else "",
        "company_code": normalize_company_code(
            row["company_code"] if "company_code" in row.keys() else "ALFASS"
        ),
    }

    pdf_io = report_to_pdf(report_dict, materials)
    filename = f"service_report_{row['report_id']}.pdf"
    return send_file(
        pdf_io,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=filename,
    )


@app.route("/report/<int:report_id>/print")
def report_print(report_id):
    user = get_current_user()
    if not is_mechanic_user(user):
        return redirect(url_for("login"))
    row = get_report(report_id)
    if not row or row["user_id"] != user["id"]:
        return redirect(url_for("account"))
    return render_template("print_pdf.html", report=row)


@app.route("/report/<int:report_id>/mark_printed", methods=["POST"])
def report_mark_printed(report_id):
    user = get_current_user()
    if not is_mechanic_user(user):
        return ("", 401)
    row = get_report(report_id)
    if not row or row["user_id"] != user["id"]:
        return ("", 404)
    mark_report_printed(report_id, user["id"])
    return ("", 204)


@app.route("/report/<int:report_id>/open_print")
def report_open_print(report_id):
    user = get_current_user()
    if not is_mechanic_user(user):
        return redirect(url_for("login"))
    row = get_report(report_id)
    if not row or row["user_id"] != user["id"]:
        return redirect(url_for("account"))
    return render_template("open_print_pdf.html", report=row)


@app.route("/report/<int:report_id>/mark_opened", methods=["POST"])
def report_mark_opened(report_id):
    user = get_current_user()
    if not is_mechanic_user(user):
        return ("", 401)
    row = get_report(report_id)
    if not row or row["user_id"] != user["id"]:
        return ("", 404)
    mark_report_opened(report_id, user["id"])
    return ("", 204)


@app.route("/organizations")
def organizations_api():
    orgs = load_organizations()
    return jsonify(orgs)


@app.route("/api/report/sign", methods=["POST"])
def api_report_sign():
    user = get_current_user()
    if not is_mechanic_user(user):
        return jsonify({"error": "unauthorized"}), 401

    client_local_id = request.form.get("client_local_id", "").strip()
    if not client_local_id:
        return jsonify({"error": "client_local_id required"}), 400

    signed_at = request.form.get("signed_at", "").strip() or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT id FROM reports WHERE client_local_id = ? AND user_id = ?",
        (client_local_id, user["id"]),
    )
    row = cur.fetchone()
    if not row:
        return jsonify({"error": "report not found"}), 404

    cur.execute(
        "UPDATE reports SET is_signed = 1, signed_at = ? WHERE id = ?",
        (signed_at, row["id"]),
    )
    db.commit()
    return jsonify({"ok": True})

# ----------------- приём формы, сохранение, PDF -----------------


@app.route("/api/report-config")
def report_config_api():
    """Публичный конфиг для формы (лимиты полей). Android позже читает тот же JSON из assets."""
    return jsonify(load_report_config())


@app.route("/api/equipment")
def api_equipment_catalog():
    """Каталог техники по отчётам (для веба и будущего Android)."""
    serial = request.args.get("sn", "").strip()
    organization = request.args.get("org", "").strip()
    location = request.args.get("loc", "").strip()
    rows = list_equipment_catalog(
        serial=serial,
        organization=organization,
        location=location,
    )
    return jsonify(
        {
            "items": [
                {
                    "organization": r["organization"],
                    "equipment_type": r["equipment_type"],
                    "serial_number": r["serial_number"],
                    "location": r["location"],
                    "report_count": r["report_count"],
                    "last_report_date": r["last_report_date"],
                    "verified_count": r["verified_count"],
                }
                for r in rows
            ]
        }
    )


@app.route("/equipment/dashboard")
def equipment_dashboard():
    """Карта компаний и их подконтрольная техника."""
    user = get_current_user()
    if not is_staff_user(user):
        return redirect(url_for("login", portal="staff"))
    groups = list_equipment_dashboard_groups(rental_only=False)
    return render_template(
        "equipment_dashboard.html",
        user=user,
        role_title=staff_role_title(user),
        dashboard_groups=groups,
        map_title="Карта компаний и их подконтрольная техника",
        map_kind="companies",
        empty_message="Пока нет данных. Они появятся из отчётов механиков (организация + серийный номер).",
        staff_mode="admin" if can_access_admin_panel(user) else "manager",
    )


@app.route("/equipment/rental-map")
def equipment_rental_map():
    """Карта арендной техники: компании, которым сдана наша техника (тип работ «Аренда»)."""
    user = get_current_user()
    if not is_staff_user(user):
        return redirect(url_for("login", portal="staff"))
    groups = list_equipment_dashboard_groups(rental_only=True)
    return render_template(
        "equipment_dashboard.html",
        user=user,
        role_title=staff_role_title(user),
        dashboard_groups=groups,
        map_title="Карта арендной техники",
        map_kind="rental",
        empty_message="Пока нет арендной техники. Она появится из отчётов, где в типах работ отмечена «Аренда».",
        staff_mode="admin" if can_access_admin_panel(user) else "manager",
    )


@app.route("/api/equipment/unit", methods=["GET", "POST"])
def api_equipment_unit():
    """Карточка единицы техники: чтение/сохранение шильдика АКБ и доп. заметок."""
    user = get_current_user()
    if not is_staff_user(user):
        return jsonify({"error": "unauthorized"}), 401

    if request.method == "GET":
        org = request.args.get("org", "").strip()
        sn = request.args.get("sn", "").strip()
        card = get_equipment_unit_card(org, sn)
        if not card:
            return jsonify({"error": "not_found"}), 404
        return jsonify(card)

    payload = request.get_json(silent=True) or {}
    org = (payload.get("organization") or payload.get("org") or "").strip()
    sn = (payload.get("serial_number") or payload.get("sn") or "").strip()
    if not sn:
        return jsonify({"error": "serial_number required"}), 400
    meta = upsert_equipment_unit_meta(
        org,
        sn,
        battery_serial=str(payload.get("battery_serial") or ""),
        extra_notes=str(payload.get("extra_notes") or ""),
    )
    card = get_equipment_unit_card(org, sn) or {}
    card.update(meta)
    return jsonify({"ok": True, "unit": card})


@app.route("/api/equipment/move/unlock", methods=["POST"])
def api_equipment_move_unlock():
    user = get_current_user()
    if not is_staff_user(user):
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password") or "")
    expected = get_setting("equipment_move_password", "Переезд2026")
    if not expected or password != expected:
        return jsonify({"ok": False, "error": "bad_password"}), 403
    session["equipment_move_unlocked"] = True
    return jsonify({"ok": True})


@app.route("/api/equipment/move/lock", methods=["POST"])
def api_equipment_move_lock():
    user = get_current_user()
    if not is_staff_user(user):
        return jsonify({"error": "unauthorized"}), 401
    session.pop("equipment_move_unlocked", None)
    return jsonify({"ok": True})


@app.route("/api/equipment/move/relocate", methods=["POST"])
def api_equipment_move_relocate():
    user = get_current_user()
    if not is_staff_user(user):
        return jsonify({"error": "unauthorized"}), 401
    if not session.get("equipment_move_unlocked"):
        return jsonify({"error": "locked"}), 403
    payload = request.get_json(silent=True) or {}
    from_org = str(payload.get("from_org") or "").strip()
    to_org = str(payload.get("to_org") or "").strip()
    sn = str(payload.get("serial_number") or payload.get("sn") or "").strip()
    map_kind = str(payload.get("map_kind") or "companies").strip().lower()
    rental_only = map_kind == "rental"
    try:
        card = relocate_equipment_unit(from_org, to_org, sn, rental_only=rental_only)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "unit": card})


@app.route("/max/info/<serial>")
def max_equipment_info_page(serial):
    """Страница для мини-приложения MAX / описания чата (справка по S/N)."""
    from max_bot.equipment_info import build_equipment_info
    from max_bot.hashtag_parser import is_valid_serial_token

    serial_clean = (serial or "").lstrip("#/").strip().upper()
    if not is_valid_serial_token(serial_clean):
        return render_template(
            "max_equipment_info.html",
            info={
                "found": False,
                "display_serial": f"#{serial}",
                "text": "Некорректный серийный номер.",
            },
            current_year=datetime.now().year,
        ), 400

    info = build_equipment_info(get_db, serial_clean)
    return render_template(
        "max_equipment_info.html",
        info=info,
        current_year=datetime.now().year,
    )


@app.route("/api/max/equipment-info")
def api_max_equipment_info():
    """JSON для бота и мини-приложения MAX."""
    from max_bot.equipment_info import build_equipment_info, parse_lookup_command
    from max_bot.hashtag_parser import is_valid_serial_token

    raw = request.args.get("serial", "").strip() or request.args.get("q", "").strip()
    serial = parse_lookup_command(raw) or parse_lookup_command(f"/{raw.lstrip('#/')}")
    if not serial:
        serial = raw.lstrip("#/").upper()
    if not is_valid_serial_token(serial):
        return jsonify({"ok": False, "error": "invalid_serial"}), 400

    info = build_equipment_info(get_db, serial)
    return jsonify({"ok": True, **info})


@app.route("/submit", methods=["POST"])
def submit():
    user = get_current_user()
    if not is_mechanic_user(user):
        return redirect(url_for("login"))

    date_raw = request.form.get("date", "").strip()
    date_str = date_raw
    try:
        if date_raw:
            dt = datetime.strptime(date_raw, "%Y-%m-%d").date()
            if dt > datetime.today().date():
                dt = datetime.today().date()
            date_str = dt.strftime("%d.%m.%Y")
    except ValueError:
        date_str = date_raw

    step1 = sanitize_step1_fields(
        {
            "date": date_str,
            "organization": request.form.get("organization", ""),
            "equipment_type": request.form.get("equipment_type", ""),
            "serial_number": request.form.get("serial_number", ""),
            "internal_number": request.form.get("internal_number", ""),
            "location": request.form.get("location", ""),
            "engine_hours": request.form.get("engine_hours", ""),
            "year": request.form.get("year", ""),
        }
    )
    org = step1["organization"]
    equipment_type = step1["equipment_type"]
    serial_number = step1["serial_number"]
    internal_number = step1["internal_number"]
    location = step1["location"]
    engine_hours = step1["engine_hours"]
    year = step1["year"]
    date_str = step1["date"] or date_str

    required_missing = []
    if not date_str:
        required_missing.append("date")
    if not org:
        required_missing.append("organization")
    if not equipment_type:
        required_missing.append("equipment_type")
    if not serial_number:
        required_missing.append("serial_number")
    if not internal_number:
        required_missing.append("internal_number")
    if not location:
        required_missing.append("location")
    if not engine_hours:
        required_missing.append("engine_hours")
    if not year:
        required_missing.append("year")

    company_code = normalize_company_code(session.get("company_code"))
    form_cfg = load_report_config().get("form", {})
    work_desc_max = int(form_cfg.get("work_description", {}).get("max_chars", 1000))
    rec_max = int(form_cfg.get("recommendations", {}).get("max_chars", 470))

    if required_missing:
        organizations = load_organizations()
        fio_full = " ".join(
            p for p in [user["last_name"], user["first_name"], user["middle_name"]] if p
        ).strip()
        report_prefill = {
            **step1,
            "date": date_raw or date_str,
            "work_types": ", ".join(request.form.getlist("work_type")),
            "work_description": request.form.get("work_description", "").strip()[:work_desc_max],
            "recommendations": request.form.get("recommendations", "").strip()[:rec_max],
        }
        raw_materials = []
        for code, name, qty in zip(
            request.form.getlist("part_code[]"),
            request.form.getlist("part_name[]"),
            request.form.getlist("part_qty[]"),
        ):
            raw_materials.append({"code": code, "name": name, "qty": qty})
        return render_template(
            "form_wizard.html",
            organizations=organizations,
            mechanic_name=fio_full or user["login"],
            report=report_prefill,
            materials=sanitize_materials(raw_materials),
            error="Шаг 1 обязателен: заполните все поля, чтобы продолжить.",
            initial_step=1,
            **_wizard_form_context(user, company_code),
        )

    if not is_valid_date_not_future(date_str):
        organizations = load_organizations()
        fio_full = " ".join(
            p for p in [user["last_name"], user["first_name"], user["middle_name"]] if p
        ).strip()
        return render_template(
            "form_wizard.html",
            organizations=organizations,
            mechanic_name=fio_full or user["login"],
            report={**step1, "date": date_raw or date_str},
            error="Дата не может быть в будущем.",
            initial_step=1,
            **_wizard_form_context(user, company_code),
        )

    if not is_valid_year_not_future(year):
        organizations = load_organizations()
        fio_full = " ".join(
            p for p in [user["last_name"], user["first_name"], user["middle_name"]] if p
        ).strip()
        return render_template(
            "form_wizard.html",
            organizations=organizations,
            mechanic_name=fio_full or user["login"],
            report={**step1, "date": date_raw or date_str},
            error="Укажите корректный год выпуска техники.",
            initial_step=1,
            **_wizard_form_context(user, company_code),
        )

    work_types = filter_work_types(request.form.getlist("work_type"))
    work_description = request.form.get("work_description", "").strip()[:work_desc_max]
    recommendations = request.form.get("recommendations", "").strip()[:rec_max]

    raw_materials = []
    for code, name, qty in zip(
        request.form.getlist("part_code[]"),
        request.form.getlist("part_name[]"),
        request.form.getlist("part_qty[]"),
    ):
        raw_materials.append({"code": code, "name": name, "qty": qty})
    materials = sanitize_materials(raw_materials)

    # обновляем базу компаний/контрагентов (название + место эксплуатации)
    upsert_counterparty(org, request.form.get("location", ""))

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_id = datetime.now().strftime("%Y%m%d%H%M%S")
    fio = format_short_name(
        user["last_name"],
        user["first_name"],
        user["middle_name"],
    )
    mechanic_name = fio or user["login"]

    company_code = normalize_company_code(session.get("company_code"))
    client_local_id = request.form.get("client_local_id", "").strip()
    if request.form.get("company_code", "").strip():
        company_code = normalize_company_code(request.form.get("company_code"))
        session["company_code"] = company_code
    is_signed_mobile = request.form.get("is_signed") == "1"
    signed_at_mobile = request.form.get("signed_at", "").strip()

    if client_local_id:
        db = get_db()
        cur = db.cursor()
        cur.execute(
            "SELECT id, is_signed FROM reports WHERE client_local_id = ? AND user_id = ?",
            (client_local_id, user["id"]),
        )
        existing = cur.fetchone()
        if existing:
            if is_signed_mobile and not int(existing["is_signed"] or 0):
                cur.execute(
                    "UPDATE reports SET is_signed = 1, signed_at = ? WHERE id = ?",
                    (signed_at_mobile or created_at, existing["id"]),
                )
                db.commit()
            return jsonify({"ok": True, "duplicate": True})

    report_dict = {
        "report_id": report_id,
        "created_at": created_at,
        "date": date_str,
        "organization": org,
        "equipment_type": equipment_type,
        "serial_number": serial_number,
        "internal_number": internal_number,
        "location": location,
        "engine_hours": engine_hours,
        "year": year,
        "work_types": ", ".join(work_types),
        "work_description": work_description,
        "recommendations": recommendations,
        "materials_json": json.dumps(materials, ensure_ascii=False),
        "mechanic_code": user["login"],
        "mechanic_name": mechanic_name,
        "mechanic_position": user["position"] if "position" in user.keys() else "",
        "company_code": company_code,
        "client_local_id": client_local_id,
        "is_signed": is_signed_mobile,
        "signed_at": signed_at_mobile if is_signed_mobile else None,
    }

    # сохраняем в Excel и в БД
    append_report_to_excel(report_dict)
    create_report(user_id=user["id"], report_dict=report_dict)

    pdf_io = report_to_pdf(report_dict, materials)

    filename = f"service_report_{report_id}.pdf"
    return send_file(
        pdf_io,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=filename,
    )


if __name__ == "__main__":
    # Android emulator connects to host machine via 10.0.2.2,
    # so the backend should listen on all interfaces.
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)