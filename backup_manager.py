"""Резервное копирование и восстановление локальной экосистемы.

Яндекс.Диск не трогаем: в архив попадают только локальные данные.
PDF-отчёты генерируются из БД + шаблонов — в бэкап входят сканы/фото/БД.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable

BACKUP_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()

EXCLUDE_DIR_NAMES = {
    "tmp",
    "design",
    "bot_samples",
    "_restore_staging",
    "_pre_restore",
    "backups",
}
EXCLUDE_NAME_PREFIXES = (
    "test_",
    "debug_",
    "sig_",
    "ass_sig_",
    "pos_",
    "maxbot_",
)


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _set_job(job_id: str, **fields) -> None:
    with _JOBS_LOCK:
        job = BACKUP_JOBS.setdefault(job_id, {})
        job.update(fields)
        job["updated_at"] = time.time()


def get_job(job_id: str) -> dict | None:
    with _JOBS_LOCK:
        job = BACKUP_JOBS.get(job_id)
        return dict(job) if job else None


def _should_skip(path: Path, data_dir: Path) -> bool:
    try:
        rel = path.relative_to(data_dir)
    except ValueError:
        return True
    parts = rel.parts
    if not parts:
        return True
    if parts[0] in EXCLUDE_DIR_NAMES:
        return True
    name = path.name
    if any(name.startswith(p) for p in EXCLUDE_NAME_PREFIXES):
        return True
    if name.endswith((".pyc", ".pyo")):
        return True
    return False


def _checkpoint_sqlite(db_path: Path) -> None:
    if not db_path.is_file():
        return
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _iter_full_files(data_dir: Path) -> list[Path]:
    files: list[Path] = []
    for root, dirs, filenames in os.walk(data_dir):
        root_p = Path(root)
        # prune excluded dirs in-place
        dirs[:] = [
            d
            for d in dirs
            if d not in EXCLUDE_DIR_NAMES and not any(d.startswith(p) for p in EXCLUDE_NAME_PREFIXES)
        ]
        for fn in filenames:
            p = root_p / fn
            if _should_skip(p, data_dir):
                continue
            if p.is_file():
                files.append(p)
    return files


def _db_only_files(data_dir: Path) -> list[Path]:
    names = [
        "app.db",
        "app.db-wal",
        "app.db-shm",
        "organizations.json",
        "reports.xlsx",
    ]
    out: list[Path] = []
    for name in names:
        p = data_dir / name
        if p.is_file():
            out.append(p)
    return out


def create_backup_zip(
    *,
    data_dir: str | Path,
    backups_dir: str | Path,
    mode: str,
    job_id: str,
    progress_cb: Callable[[int, str], None] | None = None,
) -> Path:
    """Создаёт ZIP и возвращает путь к файлу."""
    data_dir = Path(data_dir)
    backups_dir = Path(backups_dir)
    backups_dir.mkdir(parents=True, exist_ok=True)

    mode = (mode or "db").strip().lower()
    if mode not in ("db", "full"):
        raise ValueError("mode must be db or full")

    def prog(pct: int, msg: str) -> None:
        pct = max(0, min(100, int(pct)))
        if progress_cb:
            progress_cb(pct, msg)
        _set_job(job_id, progress=pct, message=msg, status="running")

    prog(2, "Подготовка…")
    _checkpoint_sqlite(data_dir / "app.db")
    prog(8, "Снимок базы данных…")

    files = _db_only_files(data_dir) if mode == "db" else _iter_full_files(data_dir)
    if not any(p.name == "app.db" for p in files):
        raise FileNotFoundError("Не найден data/app.db")

    stamp = _now_stamp()
    label = "db" if mode == "db" else "full"
    zip_name = f"mechanic_report-{label}-{stamp}.zip"
    zip_path = backups_dir / zip_name

    manifest = {
        "app": "mechanic_report",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "includes_yandex_disk": False,
        "notes": [
            "Яндекс.Диск не входит в бэкап.",
            "PDF отчётов генерируются из БД; в полном бэкапе — сканы, фото, inbox.",
        ],
        "files": [],
    }

    total = max(1, len(files))
    prog(12, f"Архивация ({len(files)} файлов)…")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i, path in enumerate(files):
            rel = path.relative_to(data_dir).as_posix()
            arcname = f"data/{rel}"
            zf.write(path, arcname)
            manifest["files"].append(arcname)
            # 12..90
            pct = 12 + int(78 * (i + 1) / total)
            if i % 5 == 0 or i + 1 == total:
                prog(pct, f"Упаковка: {rel}")

        prog(92, "Запись манифеста…")
        zf.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )

    prog(100, "Готово")
    _set_job(
        job_id,
        status="done",
        progress=100,
        message="Бэкап готов",
        filename=zip_name,
        path=str(zip_path),
        mode=mode,
    )
    return zip_path


def _safe_extract_member(zf: zipfile.ZipFile, member: zipfile.ZipInfo, dest: Path) -> Path | None:
    name = member.filename.replace("\\", "/")
    if not name or name.endswith("/"):
        return None
    if name.startswith("/") or ".." in name.split("/"):
        raise ValueError(f"Небезопасный путь в архиве: {name}")
    # ожидаем data/... или manifest.json
    target = dest / name
    target.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member, "r") as src, open(target, "wb") as out:
        shutil.copyfileobj(src, out)
    return target


def restore_from_zip(
    *,
    zip_path: str | Path,
    data_dir: str | Path,
    job_id: str,
    progress_cb: Callable[[int, str], None] | None = None,
) -> dict:
    """Восстанавливает data/ из ZIP. Перед заменой копирует текущее в data/_pre_restore/."""
    zip_path = Path(zip_path)
    data_dir = Path(data_dir)
    staging = data_dir / "_restore_staging"
    pre = data_dir / "_pre_restore"

    def prog(pct: int, msg: str) -> None:
        pct = max(0, min(100, int(pct)))
        if progress_cb:
            progress_cb(pct, msg)
        _set_job(job_id, progress=pct, message=msg, status="running")

    prog(3, "Проверка архива…")
    if not zip_path.is_file():
        raise FileNotFoundError("ZIP не найден")

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if "manifest.json" not in names and not any(n.startswith("data/") for n in names):
            raise ValueError("Это не бэкап mechanic_report (нет manifest.json / data/)")

        manifest = {}
        if "manifest.json" in names:
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except Exception:
                manifest = {}

        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)

        members = [m for m in zf.infolist() if not m.is_dir()]
        total = max(1, len(members))
        prog(10, "Распаковка…")
        for i, member in enumerate(members):
            _safe_extract_member(zf, member, staging)
            pct = 10 + int(40 * (i + 1) / total)
            if i % 5 == 0 or i + 1 == total:
                prog(pct, f"Распаковка: {member.filename}")

    staged_data = staging / "data"
    if not staged_data.is_dir():
        # allow flat layout where zip root is already data contents
        if (staging / "app.db").is_file():
            staged_data = staging
        else:
            raise ValueError("В архиве нет папки data/ с app.db")

    if not (staged_data / "app.db").is_file():
        raise ValueError("В архиве нет data/app.db")

    prog(55, "Резервная копия текущего состояния…")
    if pre.exists():
        shutil.rmtree(pre, ignore_errors=True)
    pre.mkdir(parents=True, exist_ok=True)

    # snapshot current critical items
    for name in ("app.db", "app.db-wal", "app.db-shm", "organizations.json", "reports.xlsx"):
        src = data_dir / name
        if src.is_file():
            shutil.copy2(src, pre / name)

    for folder in ("uploads", "bot_uploads"):
        src = data_dir / folder
        if src.is_dir():
            shutil.copytree(src, pre / folder, dirs_exist_ok=True)

    prog(70, "Остановка записи в БД…")
    _checkpoint_sqlite(data_dir / "app.db")

    prog(78, "Подмена файлов…")
    for root, dirs, files in os.walk(staged_data):
        root_p = Path(root)
        rel_root = root_p.relative_to(staged_data)
        # не восстанавливаем служебные каталоги внутрь себя
        if rel_root.parts and rel_root.parts[0] in (
            "_restore_staging",
            "_pre_restore",
            "backups",
        ):
            continue
        dest_root = data_dir / rel_root
        dest_root.mkdir(parents=True, exist_ok=True)
        for fn in files:
            src = root_p / fn
            dest = dest_root / fn
            shutil.copy2(src, dest)

    prog(92, "Очистка временных файлов…")
    shutil.rmtree(staging, ignore_errors=True)

    prog(100, "Восстановление завершено")
    result = {
        "ok": True,
        "manifest": manifest,
        "pre_restore_dir": str(pre),
    }
    _set_job(job_id, status="done", progress=100, message="Восстановление завершено", result=result)
    return result


def start_backup_job(
    *,
    data_dir: str | Path,
    backups_dir: str | Path,
    mode: str,
) -> str:
    job_id = uuid.uuid4().hex
    _set_job(
        job_id,
        status="queued",
        progress=0,
        message="В очереди…",
        mode=mode,
        kind="backup",
    )

    def runner() -> None:
        try:
            create_backup_zip(
                data_dir=data_dir,
                backups_dir=backups_dir,
                mode=mode,
                job_id=job_id,
            )
        except Exception as exc:
            _set_job(job_id, status="error", message=str(exc), progress=0)

    threading.Thread(target=runner, daemon=True).start()
    return job_id


def start_restore_job(*, zip_path: str | Path, data_dir: str | Path) -> str:
    job_id = uuid.uuid4().hex
    _set_job(
        job_id,
        status="queued",
        progress=0,
        message="В очереди…",
        kind="restore",
    )

    def runner() -> None:
        try:
            restore_from_zip(zip_path=zip_path, data_dir=data_dir, job_id=job_id)
        except Exception as exc:
            _set_job(job_id, status="error", message=str(exc), progress=0)

    threading.Thread(target=runner, daemon=True).start()
    return job_id
