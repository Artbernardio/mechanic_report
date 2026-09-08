"""Топливный отчёт механика: данные периода, PDF и приложение с чеками."""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image as RLImage,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

MAX_RECEIPTS = 12
_FONT_REGISTERED = False
RED = colors.HexColor("#CC3333")
INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#475569")
LINE = colors.HexColor("#cbd5e1")
HEAD_BG = colors.HexColor("#1e293b")
ROW_ALT = colors.HexColor("#f8fafc")
TOTAL_BG = colors.HexColor("#fee2e2")


def _register_fonts(static_dir: str) -> str:
    global _FONT_REGISTERED
    family = "FuelReportSans"
    if _FONT_REGISTERED:
        return family
    candidates = [
        (
            os.path.join(static_dir, "fonts", "DejaVuSans.ttf"),
            os.path.join(static_dir, "fonts", "DejaVuSans-Bold.ttf"),
        ),
        (
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
        ),
    ]
    for normal, bold in candidates:
        if os.path.isfile(normal):
            pdfmetrics.registerFont(TTFont(family, normal))
            if os.path.isfile(bold):
                pdfmetrics.registerFont(TTFont(family + "-Bold", bold))
            else:
                pdfmetrics.registerFont(TTFont(family + "-Bold", normal))
            _FONT_REGISTERED = True
            return family
    raise RuntimeError("Не найден шрифт для PDF топливного отчёта (DejaVuSans или Arial).")


def format_ru_quoted_date(iso_date: str | None) -> str:
    raw = (iso_date or "").strip()[:10]
    if len(raw) < 10:
        return "«» __________ 20__ г."
    try:
        dt = date.fromisoformat(raw)
    except ValueError:
        return "«» __________ 20__ г."
    return f"«{dt.day:02d}» {_MONTHS_GENITIVE[dt.month - 1]} {dt.year} г."


def format_ru_dot_date(iso_date: str | None) -> str:
    raw = (iso_date or "").strip()[:10]
    if len(raw) < 10:
        return "—"
    return f"{raw[8:10]}.{raw[5:7]}.{raw[0:4]}"


def fmt_ru_number(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    text = f"{number:,.{digits}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", " ")


def fmt_ru_int(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def _dash(value: str | None) -> str:
    text = (value or "").strip()
    return text if text else "—"


def mechanic_full_name(user: dict | None) -> str:
    if not user:
        return "—"
    parts = [
        (user.get("last_name") or "").strip(),
        (user.get("first_name") or "").strip(),
        (user.get("middle_name") or "").strip(),
    ]
    return " ".join(p for p in parts if p) or _dash(user.get("login"))


def parse_fuel_report_period(start_raw: str | None, end_raw: str | None) -> tuple[str, str]:
    start = (start_raw or "").strip()[:10]
    end = (end_raw or "").strip()[:10]
    if start and not end:
        end = start
    elif end and not start:
        start = end
    if not start or not end:
        raise ValueError("Укажите контрольную дату или период отчёта.")
    try:
        start_d = date.fromisoformat(start)
        end_d = date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("Некорректная дата периода.") from exc
    if start_d > end_d:
        start_d, end_d = end_d, start_d
    return start_d.isoformat(), end_d.isoformat()


def build_fuel_report_payload(
    db,
    user_id: int,
    mechanic_user: dict,
    start_date: str,
    end_date: str,
) -> dict:
    from vehicle_fleet import (
        WINTER_MONTHS,
        _mileage_near_date,
        get_vehicle_for_user,
        list_fuel_fills_in_period,
    )

    vehicle = get_vehicle_for_user(db, user_id)
    if not vehicle:
        raise ValueError("Служебное авто не привязано.")

    fills_raw = list_fuel_fills_in_period(db, user_id, start_date, end_date)
    fills = []
    liters_total = 0.0
    amount_total = 0.0
    for idx, row in enumerate(fills_raw, start=1):
        liters = float(row.get("liters") or 0)
        price = float(row.get("price_per_liter") or 0)
        amount = round(liters * price, 2)
        liters_total += liters
        amount_total += amount
        station = (row.get("station_name") or "").strip()
        fills.append(
            {
                "n": idx,
                "date": row.get("fill_date") or "",
                "date_label": format_ru_dot_date(row.get("fill_date")),
                "liters": liters,
                "price": price,
                "amount": amount,
                "document": station or "Чек / заправочная ведомость",
            }
        )

    start_km = _mileage_near_date(db, user_id, start_date, prefer_last=True)
    end_km = _mileage_near_date(db, user_id, end_date, prefer_last=True)
    period_km = None
    if start_km is not None and end_km is not None and end_km >= start_km:
        period_km = end_km - start_km

    avg_price = (amount_total / liters_total) if liters_total > 0 else None
    actual_l100 = (liters_total / period_km * 100) if period_km and period_km > 0 and liters_total > 0 else None
    factory = float(vehicle.get("factory_l100") or 10.0)
    try:
        month = int(end_date[5:7])
    except (TypeError, ValueError):
        month = date.today().month
    if month in WINTER_MONTHS:
        factory *= float(vehicle.get("winter_factor") or 1.1)
    factory = round(factory, 1)
    norm_liters = round(factory / 100.0 * period_km, 1) if period_km and period_km > 0 else None
    deviation = round(liters_total - norm_liters, 1) if norm_liters is not None else None

    return {
        "user_id": int(user_id),
        "fleet_vehicle_id": vehicle.get("fleet_vehicle_id"),
        "start_date": start_date,
        "end_date": end_date,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "composed_date": date.today().isoformat(),
        "vehicle_name": vehicle.get("model_name") or "—",
        "plate_number": vehicle.get("plate_number") or "—",
        "mechanic_name": mechanic_full_name(mechanic_user),
        "department": _dash((mechanic_user or {}).get("position") or "Автопарк"),
        "start_km": start_km,
        "end_km": end_km,
        "period_km": period_km,
        "fills": fills,
        "fill_count": len(fills),
        "liters_total": round(liters_total, 1),
        "amount_total": round(amount_total, 2),
        "avg_price": round(avg_price, 2) if avg_price is not None else None,
        "actual_l100": round(actual_l100, 1) if actual_l100 is not None else None,
        "factory_l100": factory,
        "norm_liters": norm_liters,
        "deviation": deviation,
    }


def init_fuel_report_schema(db) -> None:
    cur = db.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_fuel_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            fleet_vehicle_id INTEGER,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            fill_count INTEGER NOT NULL DEFAULT 0,
            liters_total REAL NOT NULL DEFAULT 0,
            amount_total REAL NOT NULL DEFAULT 0,
            pdf_relpath TEXT,
            payload_json TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vehicle_fuel_reports_user
        ON vehicle_fuel_reports (user_id, start_date, end_date, id)
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_fuel_report_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            relpath TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (report_id) REFERENCES vehicle_fuel_reports(id) ON DELETE CASCADE
        )
        """
    )


def list_fuel_reports(db, user_id: int, limit: int = 40) -> list[dict]:
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, user_id, start_date, end_date, created_at, fill_count,
               liters_total, amount_total, pdf_relpath
        FROM vehicle_fuel_reports
        WHERE user_id = ?
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
        """,
        (int(user_id), int(limit)),
    )
    rows = []
    for row in cur.fetchall():
        item = dict(row)
        item["period_label"] = f"{format_ru_dot_date(item['start_date'])} — {format_ru_dot_date(item['end_date'])}"
        item["created_label"] = (item.get("created_at") or "")[:16].replace("T", " ")
        rows.append(item)
    return rows


def get_fuel_report(db, report_id: int) -> dict | None:
    cur = db.cursor()
    cur.execute("SELECT * FROM vehicle_fuel_reports WHERE id = ?", (int(report_id),))
    row = cur.fetchone()
    return dict(row) if row else None


def list_fuel_report_receipts(db, report_id: int) -> list[dict]:
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, relpath, sort_order FROM vehicle_fuel_report_receipts
        WHERE report_id = ?
        ORDER BY sort_order ASC, id ASC
        """,
        (int(report_id),),
    )
    return [dict(r) for r in cur.fetchall()]


def _styles(font: str) -> dict:
    return {
        "title": ParagraphStyle(
            "FuelTitle",
            fontName=font + "-Bold",
            fontSize=16,
            leading=20,
            alignment=TA_CENTER,
            textColor=RED,
            spaceAfter=4,
        ),
        "period": ParagraphStyle(
            "FuelPeriod",
            fontName=font,
            fontSize=11,
            leading=15,
            alignment=TA_CENTER,
            textColor=INK,
            spaceAfter=12,
        ),
        "h": ParagraphStyle(
            "FuelH",
            fontName=font + "-Bold",
            fontSize=11,
            leading=14,
            textColor=INK,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "FuelBody",
            fontName=font,
            fontSize=10,
            leading=14,
            textColor=INK,
        ),
        "small": ParagraphStyle(
            "FuelSmall",
            fontName=font,
            fontSize=8,
            leading=11,
            textColor=MUTED,
        ),
        "th": ParagraphStyle(
            "FuelTh",
            fontName=font + "-Bold",
            fontSize=8,
            leading=10,
            alignment=TA_CENTER,
            textColor=colors.white,
        ),
        "td": ParagraphStyle(
            "FuelTd",
            fontName=font,
            fontSize=8.5,
            leading=11,
            alignment=TA_CENTER,
            textColor=INK,
        ),
        "td_left": ParagraphStyle(
            "FuelTdLeft",
            fontName=font,
            fontSize=8.5,
            leading=11,
            alignment=TA_LEFT,
            textColor=INK,
        ),
        "td_total": ParagraphStyle(
            "FuelTdTotal",
            fontName=font + "-Bold",
            fontSize=8.5,
            leading=11,
            alignment=TA_CENTER,
            textColor=INK,
        ),
        "sign": ParagraphStyle(
            "FuelSign",
            fontName=font,
            fontSize=10,
            leading=14,
            textColor=INK,
        ),
        "app_title": ParagraphStyle(
            "FuelAppTitle",
            fontName=font + "-Bold",
            fontSize=14,
            leading=18,
            alignment=TA_CENTER,
            textColor=INK,
            spaceAfter=6,
        ),
        "caption": ParagraphStyle(
            "FuelCaption",
            fontName=font,
            fontSize=8,
            leading=10,
            alignment=TA_CENTER,
            textColor=MUTED,
        ),
    }


def _kv_table(rows: list[tuple[str, str]], styles: dict, width: float) -> Table:
    data = [
        [Paragraph(label, styles["body"]), Paragraph(value, styles["body"])]
        for label, value in rows
    ]
    table = Table(data, colWidths=[width * 0.46, width * 0.54])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _fills_table(payload: dict, styles: dict, width: float) -> Table:
    fills = list(payload.get("fills") or [])
    while len(fills) < 3:
        fills.append(
            {
                "n": len(fills) + 1,
                "date_label": "",
                "liters": None,
                "price": None,
                "amount": None,
                "document": "",
            }
        )
    header = [
        Paragraph("№", styles["th"]),
        Paragraph("Дата заправки", styles["th"]),
        Paragraph("Кол-во топлива, л", styles["th"]),
        Paragraph("Цена за 1 л, руб.", styles["th"]),
        Paragraph("Сумма, руб.", styles["th"]),
        Paragraph("Документ (чек, ведомость)", styles["th"]),
    ]
    data = [header]
    for row in fills:
        empty = row.get("liters") is None
        data.append(
            [
                Paragraph(str(row.get("n") or ""), styles["td"]),
                Paragraph(row.get("date_label") or "", styles["td"]),
                Paragraph("" if empty else fmt_ru_number(row.get("liters"), 1), styles["td"]),
                Paragraph("" if empty else fmt_ru_number(row.get("price"), 2), styles["td"]),
                Paragraph("" if empty else fmt_ru_number(row.get("amount"), 2), styles["td"]),
                Paragraph(row.get("document") or "", styles["td_left"]),
            ]
        )
    data.append(
        [
            Paragraph("Итого", styles["td_total"]),
            Paragraph("—", styles["td_total"]),
            Paragraph(fmt_ru_number(payload.get("liters_total"), 1), styles["td_total"]),
            Paragraph("—", styles["td_total"]),
            Paragraph(fmt_ru_number(payload.get("amount_total"), 2), styles["td_total"]),
            Paragraph("", styles["td"]),
        ]
    )
    col_w = [12 * mm, 28 * mm, 28 * mm, 28 * mm, 28 * mm, width - 124 * mm]
    table = Table(data, colWidths=col_w, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("BACKGROUND", (0, -1), (-1, -1), TOTAL_BG),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i in range(1, len(data) - 1):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), ROW_ALT))
    table.setStyle(TableStyle(style_cmds))
    return table


def _deviation_label(value) -> str:
    if value is None:
        return "—"
    if value > 0:
        return f"перерасход {fmt_ru_number(value, 1)} л"
    if value < 0:
        return f"экономия {fmt_ru_number(abs(value), 1)} л"
    return "без отклонения"


def _receipt_placeholder(styles: dict, n: int, width: float) -> Table:
    inner = Table(
        [[Paragraph(f"Место для чека № {n}", styles["caption"])]],
        colWidths=[width],
        rowHeights=[48 * mm],
    )
    inner.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ]
        )
    )
    return inner


def build_fuel_report_pdf(
    payload: dict,
    receipt_paths: list[str],
    static_dir: str,
) -> bytes:
    font = _register_fonts(static_dir)
    styles = _styles(font)
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="Топливный отчёт",
        author=payload.get("mechanic_name") or "",
    )
    width = A4[0] - 32 * mm
    story = []
    start_q = format_ru_quoted_date(payload.get("start_date"))
    end_q = format_ru_quoted_date(payload.get("end_date"))
    story.append(Paragraph("ТОПЛИВНЫЙ ОТЧЁТ", styles["title"]))
    story.append(Paragraph(f"за период с {start_q} по {end_q}", styles["period"]))
    story.append(
        _kv_table(
            [
                ("Автомобиль (марка, модель):", _dash(payload.get("vehicle_name"))),
                ("Государственный номер:", _dash(payload.get("plate_number"))),
                ("Водитель / ответственный механик:", _dash(payload.get("mechanic_name"))),
                ("Подразделение:", _dash(payload.get("department"))),
            ],
            styles,
            width,
        )
    )
    story.append(Paragraph("Показания одометра (пробег):", styles["h"]))
    story.append(
        _kv_table(
            [
                ("– на начало периода:", f"{fmt_ru_int(payload.get('start_km'))} км"),
                ("– на конец периода:", f"{fmt_ru_int(payload.get('end_km'))} км"),
                ("– пробег за период:", f"{fmt_ru_int(payload.get('period_km'))} км"),
            ],
            styles,
            width,
        )
    )
    story.append(Paragraph("Сведения о заправках:", styles["h"]))
    story.append(_fills_table(payload, styles, width))
    story.append(Paragraph("Итого заправок за период:", styles["h"]))
    story.append(
        _kv_table(
            [
                ("– всего топлива:", f"{fmt_ru_number(payload.get('liters_total'), 1)} литров"),
                ("– общая сумма затрат:", f"{fmt_ru_number(payload.get('amount_total'), 2)} руб."),
                ("– средняя цена 1 литра:", f"{fmt_ru_number(payload.get('avg_price'), 2)} руб."),
            ],
            styles,
            width,
        )
    )
    story.append(Paragraph("Фактический расход топлива:", styles["h"]))
    story.append(
        _kv_table(
            [
                ("– расход на 100 км:", f"{fmt_ru_number(payload.get('actual_l100'), 1)} л/100 км"),
                ("– нормативный расход (по норме):", f"{fmt_ru_number(payload.get('norm_liters'), 1)} л"),
                ("– отклонение (экономия / перерасход):", _deviation_label(payload.get("deviation"))),
            ],
            styles,
            width,
        )
    )
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph("Подписи:", styles["h"]))
    story.append(Paragraph("Механик: ______________ / ______________ /", styles["sign"]))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Бухгалтер (при необходимости): ______________ / ______________ /", styles["sign"]))
    story.append(Spacer(1, 4 * mm))
    story.append(
        Paragraph(f"Дата составления: {format_ru_quoted_date(payload.get('composed_date'))}", styles["sign"])
    )

    story.append(PageBreak())
    story.append(Paragraph("ПРИЛОЖЕНИЕ к топливному отчёту", styles["app_title"]))
    story.append(
        Paragraph(
            "Лист подтверждает количество заправок, цены за топливо и пробег за указанный период. "
            "К отчёту прикладываются чеки (сканы или оригиналы).",
            styles["period"],
        )
    )
    story.append(
        _kv_table(
            [
                ("Период:", f"{format_ru_dot_date(payload.get('start_date'))} — {format_ru_dot_date(payload.get('end_date'))}"),
                ("Автомобиль / госномер:", f"{_dash(payload.get('vehicle_name'))}, {_dash(payload.get('plate_number'))}"),
                ("Количество заправок:", str(payload.get("fill_count") or 0)),
                ("Всего топлива:", f"{fmt_ru_number(payload.get('liters_total'), 1)} л"),
                ("Общая сумма:", f"{fmt_ru_number(payload.get('amount_total'), 2)} руб."),
                ("Пробег за период:", f"{fmt_ru_int(payload.get('period_km'))} км"),
                ("Одометр начало / конец:", f"{fmt_ru_int(payload.get('start_km'))} / {fmt_ru_int(payload.get('end_km'))} км"),
            ],
            styles,
            width,
        )
    )
    story.append(Paragraph("Чеки заправок:", styles["h"]))
    existing = [path for path in receipt_paths if path and os.path.isfile(path)]
    if existing:
        for idx, path in enumerate(existing, start=1):
            try:
                img = RLImage(path)
                img_w, img_h = img.imageWidth, img.imageHeight
                max_w, max_h = width, 90 * mm
                scale = min(max_w / float(img_w or 1), max_h / float(img_h or 1), 1.0)
                img.drawWidth = img_w * scale
                img.drawHeight = img_h * scale
                story.append(KeepTogether([img, Paragraph(f"Чек № {idx}", styles["caption"]), Spacer(1, 4 * mm)]))
            except Exception:
                story.append(_receipt_placeholder(styles, idx, width))
                story.append(Spacer(1, 4 * mm))
    else:
        boxes = max(int(payload.get("fill_count") or 0), 3)
        pair = []
        box_w = (width - 6 * mm) / 2
        for n in range(1, boxes + 1):
            pair.append(_receipt_placeholder(styles, n, box_w))
            if len(pair) == 2:
                row = Table([pair], colWidths=[box_w, box_w])
                row.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (0, 0), 6 * mm)]))
                story.append(row)
                story.append(Spacer(1, 4 * mm))
                pair = []
        if pair:
            story.append(pair[0])
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Механик подтверждает достоверность сведений: ______________ / ______________ /", styles["sign"]))

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.4)
        canvas.line(16 * mm, 10 * mm, A4[0] - 16 * mm, 10 * mm)
        canvas.setFont(font, 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(16 * mm, 6 * mm, "Топливный отчёт служебного автомобиля")
        canvas.drawRightString(A4[0] - 16 * mm, 6 * mm, f"стр. {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def _save_receipts(files, upload_root: str, user_id: int, report_id: int) -> list[str]:
    from integrations.mechanic_info_image import compress_image_to_jpeg

    rels = []
    folder = os.path.join(upload_root, str(int(user_id)), str(int(report_id)))
    os.makedirs(folder, exist_ok=True)
    saved = 0
    for storage in files or []:
        if saved >= MAX_RECEIPTS:
            break
        if not storage or not getattr(storage, "filename", None):
            continue
        try:
            jpeg = compress_image_to_jpeg(storage, max_bytes=500 * 1024)
        except ValueError:
            continue
        name = f"receipt_{saved + 1:02d}.jpg"
        path = os.path.join(folder, name)
        with open(path, "wb") as out:
            out.write(jpeg)
        rels.append(f"{int(user_id)}/{int(report_id)}/{name}")
        saved += 1
    return rels


def create_fuel_report(
    db,
    user_id: int,
    mechanic_user: dict,
    start_date: str,
    end_date: str,
    files,
    upload_root: str,
    static_dir: str,
) -> dict:
    payload = build_fuel_report_payload(db, user_id, mechanic_user, start_date, end_date)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO vehicle_fuel_reports (
            user_id, fleet_vehicle_id, start_date, end_date, created_at,
            fill_count, liters_total, amount_total, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            payload.get("fleet_vehicle_id"),
            start_date,
            end_date,
            now,
            int(payload.get("fill_count") or 0),
            float(payload.get("liters_total") or 0),
            float(payload.get("amount_total") or 0),
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    report_id = int(cur.lastrowid)
    rels = _save_receipts(files, upload_root, user_id, report_id)
    for idx, rel in enumerate(rels):
        cur.execute(
            """
            INSERT INTO vehicle_fuel_report_receipts (report_id, relpath, sort_order, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (report_id, rel, idx, now),
        )
    abs_receipts = [os.path.join(upload_root, rel.replace("/", os.sep)) for rel in rels]
    pdf_bytes = build_fuel_report_pdf(payload, abs_receipts, static_dir)
    pdf_rel = f"{int(user_id)}/{report_id}.pdf"
    pdf_abs = os.path.join(upload_root, pdf_rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(pdf_abs), exist_ok=True)
    with open(pdf_abs, "wb") as out:
        out.write(pdf_bytes)
    cur.execute(
        "UPDATE vehicle_fuel_reports SET pdf_relpath = ? WHERE id = ?",
        (pdf_rel, report_id),
    )
    db.commit()
    return {
        "id": report_id,
        "pdf_relpath": pdf_rel,
        "start_date": start_date,
        "end_date": end_date,
        "fill_count": payload.get("fill_count") or 0,
    }


def fuel_report_pdf_abs_path(upload_root: str, report: dict) -> str | None:
    rel = (report.get("pdf_relpath") or "").replace("\\", "/")
    if not rel or ".." in rel:
        return None
    path = os.path.join(upload_root, rel.replace("/", os.sep))
    return path if os.path.isfile(path) else None
