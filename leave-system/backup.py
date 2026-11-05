# -*- coding: utf-8 -*-
"""SQLite 與附件備份、完整性驗證及可回退還原（E3）。

備份包是一個目錄，包含 database.sqlite3、可選的 uploads/ 與 manifest.json。
這裡只處理本機檔案，不匯入 Flask app，避免 CLI 與測試互相牽連。
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

FORMAT_NAME = 'student-absence-system-backup'
FORMAT_VERSION = 1
DATABASE_NAME = 'database.sqlite3'
MANIFEST_NAME = 'manifest.json'
REQUIRED_TABLES = frozenset({
    'users', 'leave_types', 'leave_requests', 'approvals', 'audit_logs',
})
_SHA256_RE = re.compile(r'[0-9a-f]{64}\Z')
_MAX_MANIFEST_BYTES = 10 * 1024 * 1024


class BackupError(Exception):
    """備份內容無效或檔案作業無法安全完成。"""


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace(
        '+00:00', 'Z')


def _new_name(prefix):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return f'{prefix}-{stamp}-{uuid.uuid4().hex[:8]}'


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _file_entry(path, relative):
    stat = path.stat()
    return {
        'path': relative,
        'size': stat.st_size,
        'sha256': _sha256(path),
    }


def _validate_database(path):
    """確認快照可讀、SQLite 內部一致，且具備系統核心資料表。"""
    try:
        uri = path.resolve().as_uri() + '?mode=ro'
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.execute('PRAGMA query_only = ON')
            rows = conn.execute('PRAGMA integrity_check').fetchall()
            if rows != [('ok',)]:
                detail = '; '.join(str(row[0]) for row in rows[:3])
                raise BackupError(f'SQLite integrity_check 失敗：{detail}')
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
            missing = sorted(REQUIRED_TABLES - tables)
            if missing:
                raise BackupError('備份資料庫缺少核心資料表：' + '、'.join(missing))
        finally:
            conn.close()
    except BackupError:
        raise
    except sqlite3.Error as exc:
        raise BackupError(f'備份資料庫不是有效的 SQLite 資料庫：{exc}') from exc


def _safe_relative(value):
    """只接受 POSIX 相對路徑，拒絕穿越、磁碟機與反斜線。"""
    if not isinstance(value, str) or not value or '\\' in value:
        raise BackupError('manifest 含有無效檔案路徑')
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in ('', '.', '..') for part in pure.parts):
        raise BackupError(f'manifest 含有不安全路徑：{value}')
    if ':' in pure.parts[0] or str(pure) != value:
        raise BackupError(f'manifest 含有不安全路徑：{value}')
    return pure


def _bundle_file(root, relative):
    pure = _safe_relative(relative)
    candidate = root.joinpath(*pure.parts)
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise BackupError(f'備份路徑逃逸：{relative}') from exc
    return candidate


def _copy_uploads(source, destination):
    """複製附件但拒絕符號連結，避免備份把目錄外檔案捲入。"""
    source = Path(source)
    destination = Path(destination)
    if not source.exists():
        destination.mkdir(parents=True)
        return 0
    if source.is_symlink() or not source.is_dir():
        raise BackupError('附件目錄不存在或不是安全的實體目錄')

    count = 0
    destination.mkdir(parents=True)
    for current, dirs, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        for name in list(dirs):
            path = current_path / name
            if path.is_symlink():
                raise BackupError(f'附件目錄含符號連結，已停止備份：{path}')
        for name in files:
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise BackupError(f'附件不是安全的實體檔案：{path}')
            relative = path.relative_to(source)
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            count += 1
    return count


def create_backup(db_path, upload_dir, output_dir=None,
                  include_uploads=True, app_version=None):
    """以 SQLite 在線備份 API 建立一致快照，回傳備份包資訊。"""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise BackupError(f'找不到資料庫：{db_path}')
    output_root = Path(output_dir) if output_dir else db_path.parent / 'backups'
    if include_uploads:
        try:
            output_root.resolve(strict=False).relative_to(
                Path(upload_dir).resolve(strict=False))
        except ValueError:
            pass
        else:
            raise BackupError('備份輸出目錄不可位於附件目錄內，避免遞迴備份')
    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.backup-staging-', dir=output_root))
    final = output_root / _new_name('leave-backup')

    try:
        snapshot = staging / DATABASE_NAME
        try:
            source = sqlite3.connect(str(db_path), timeout=30)
            target = sqlite3.connect(str(snapshot))
            try:
                source.backup(target)
                target.commit()
            finally:
                target.close()
                source.close()
        except sqlite3.Error as exc:
            raise BackupError(f'SQLite 在線備份失敗：{exc}') from exc
        _validate_database(snapshot)

        entries = [_file_entry(snapshot, DATABASE_NAME)]
        upload_count = 0
        if include_uploads:
            upload_count = _copy_uploads(upload_dir, staging / 'uploads')
            upload_root = staging / 'uploads'
            for path in sorted(p for p in upload_root.rglob('*') if p.is_file()):
                relative = path.relative_to(staging)
                entries.append(_file_entry(path, PurePosixPath(*relative.parts).as_posix()))

        manifest = {
            'format': FORMAT_NAME,
            'format_version': FORMAT_VERSION,
            'app_version': str(app_version or 'unknown'),
            'created_at': _utc_now(),
            'includes_uploads': bool(include_uploads),
            'files': entries,
        }
        manifest_path = staging / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8')
        # 發佈前先走與外部驗證完全相同的路徑，避免產出自己也不接受的包。
        verify_backup(staging)
        os.replace(staging, final)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

    return {
        'bundle_path': str(final),
        'database_bytes': (final / DATABASE_NAME).stat().st_size,
        'upload_count': upload_count,
        'file_count': len(entries),
        'includes_uploads': bool(include_uploads),
        'created_at': manifest['created_at'],
    }


def _read_manifest(root):
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BackupError('備份包缺少 manifest.json')
    if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise BackupError('manifest.json 過大')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError(f'manifest.json 無法解析：{exc}') from exc
    if not isinstance(manifest, dict):
        raise BackupError('manifest.json 必須是 JSON 物件')
    if manifest.get('format') != FORMAT_NAME:
        raise BackupError('不是本系統的備份包格式')
    if manifest.get('format_version') != FORMAT_VERSION:
        raise BackupError('不支援的備份包格式版本')
    if not isinstance(manifest.get('includes_uploads'), bool):
        raise BackupError('manifest 的 includes_uploads 必須是布林值')
    if not isinstance(manifest.get('files'), list) or not manifest['files']:
        raise BackupError('manifest 缺少檔案雜湊清單')
    return manifest


def verify_backup(bundle_path):
    """驗證清單、路徑、檔案雜湊與 SQLite 完整性；不修改任何檔案。"""
    root = Path(bundle_path)
    if root.is_symlink() or not root.is_dir():
        raise BackupError(f'備份包不是實體目錄：{root}')
    manifest = _read_manifest(root)
    includes_uploads = manifest['includes_uploads']
    declared = {}

    for entry in manifest['files']:
        if not isinstance(entry, dict):
            raise BackupError('manifest 含有無效檔案項目')
        relative = entry.get('path')
        pure = _safe_relative(relative)
        if relative == MANIFEST_NAME or relative in declared:
            raise BackupError(f'manifest 含重複或保留路徑：{relative}')
        is_database = relative == DATABASE_NAME
        is_upload = bool(pure.parts) and pure.parts[0] == 'uploads' and len(pure.parts) > 1
        if not is_database and not is_upload:
            raise BackupError(f'manifest 含未允許的路徑：{relative}')
        if is_upload and not includes_uploads:
            raise BackupError('資料庫專用備份不得列出附件')
        size = entry.get('size')
        checksum = entry.get('sha256')
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise BackupError(f'manifest 檔案大小無效：{relative}')
        if not isinstance(checksum, str) or not _SHA256_RE.fullmatch(checksum):
            raise BackupError(f'manifest SHA-256 格式無效：{relative}')
        declared[relative] = (size, checksum)

    if DATABASE_NAME not in declared:
        raise BackupError('備份包缺少資料庫快照')
    if includes_uploads and not (root / 'uploads').is_dir():
        raise BackupError('完整備份包缺少 uploads/ 目錄')

    actual = set()
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in dirs:
            if (current_path / name).is_symlink():
                raise BackupError('備份包不得包含符號連結目錄')
        for name in files:
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise BackupError('備份包只能包含實體檔案')
            relative = PurePosixPath(*path.relative_to(root).parts).as_posix()
            if relative != MANIFEST_NAME:
                actual.add(relative)
    if actual != set(declared):
        missing = sorted(set(declared) - actual)
        extra = sorted(actual - set(declared))
        detail = []
        if missing:
            detail.append('缺少：' + '、'.join(missing[:3]))
        if extra:
            detail.append('未列出：' + '、'.join(extra[:3]))
        raise BackupError('備份包與 manifest 清單不符（' + '；'.join(detail) + '）')

    for relative, (size, checksum) in declared.items():
        path = _bundle_file(root, relative)
        if path.stat().st_size != size:
            raise BackupError(f'檔案大小不符：{relative}')
        if _sha256(path) != checksum:
            raise BackupError(f'SHA-256 驗證失敗：{relative}')
    _validate_database(root / DATABASE_NAME)

    return {
        'bundle_path': str(root),
        'app_version': str(manifest.get('app_version', 'unknown')),
        'created_at': str(manifest.get('created_at', '')),
        'includes_uploads': includes_uploads,
        'file_count': len(declared),
        'total_bytes': sum(size for size, _ in declared.values()),
    }


def _copy_current_db(db_path, rollback):
    existed = db_path.is_file()
    if existed:
        shutil.copy2(db_path, rollback / DATABASE_NAME)
    sidecars = []
    for suffix in ('-wal', '-shm'):
        source = Path(str(db_path) + suffix)
        if source.exists():
            if not source.is_file() or source.is_symlink():
                raise BackupError(f'資料庫 sidecar 不是安全實體檔案：{source}')
            target = rollback / (DATABASE_NAME + suffix)
            shutil.copy2(source, target)
            sidecars.append(suffix)
    return existed, sidecars


def _restore_current_db(db_path, rollback, existed, sidecars):
    """失敗補償：從保留副本恢復 main DB 與舊 sidecar。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ('-wal', '-shm'):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    if existed:
        recovery = db_path.parent / ('.db-recovery-' + uuid.uuid4().hex)
        shutil.copy2(rollback / DATABASE_NAME, recovery)
        os.replace(recovery, db_path)
        for suffix in sidecars:
            shutil.copy2(rollback / (DATABASE_NAME + suffix), Path(str(db_path) + suffix))
    else:
        db_path.unlink(missing_ok=True)


def restore_backup(bundle_path, db_path, upload_dir, restore_uploads=True):
    """還原已驗證備份；保留 pre-restore 副本，失敗時補償原狀。

    若備份不含附件，或 restore_uploads=False，現有附件目錄完全不動。
    呼叫端必須先停止應用，避免其他連線在替換期間寫入資料庫。
    """
    info = verify_backup(bundle_path)
    root = Path(bundle_path)
    db_path = Path(db_path)
    upload_dir = Path(upload_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    replace_uploads = bool(restore_uploads and info['includes_uploads'])

    rollback_parent = db_path.parent / 'restore-rollbacks'
    rollback_parent.mkdir(parents=True, exist_ok=True)
    rollback = rollback_parent / _new_name('pre-restore')
    rollback.mkdir()
    staging_db_dir = Path(tempfile.mkdtemp(prefix='.restore-db-', dir=db_path.parent))
    staged_db = staging_db_dir / DATABASE_NAME
    shutil.copy2(root / DATABASE_NAME, staged_db)

    staging_upload_dir = None
    staged_uploads = None
    old_uploads = None
    db_existed = False
    db_sidecars = []
    uploads_existed = False
    db_installed = False
    uploads_installed = False

    try:
        db_existed, db_sidecars = _copy_current_db(db_path, rollback)
        if replace_uploads:
            upload_dir.parent.mkdir(parents=True, exist_ok=True)
            staging_upload_dir = Path(tempfile.mkdtemp(
                prefix='.restore-uploads-', dir=upload_dir.parent))
            staged_uploads = staging_upload_dir / 'uploads'
            shutil.copytree(root / 'uploads', staged_uploads)
            if upload_dir.exists():
                if upload_dir.is_symlink() or not upload_dir.is_dir():
                    raise BackupError('現有附件路徑不是安全實體目錄')
                shutil.copytree(upload_dir, rollback / 'uploads', symlinks=True)
                uploads_existed = True

        rollback_info = {
            'created_at': _utc_now(),
            'source_backup': str(root.resolve()),
            'database_existed': db_existed,
            'database_sidecars': db_sidecars,
            'uploads_existed': uploads_existed,
            'uploads_replaced': replace_uploads,
        }
        (rollback / 'rollback.json').write_text(
            json.dumps(rollback_info, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8')

        os.replace(staged_db, db_path)
        db_installed = True
        for suffix in ('-wal', '-shm'):
            Path(str(db_path) + suffix).unlink(missing_ok=True)

        if replace_uploads:
            if uploads_existed:
                old_uploads = upload_dir.parent / ('.old-uploads-' + uuid.uuid4().hex)
                os.replace(upload_dir, old_uploads)
            os.replace(staged_uploads, upload_dir)
            uploads_installed = True
            if old_uploads is not None:
                shutil.rmtree(old_uploads)
                old_uploads = None

        _validate_database(db_path)
    except Exception as exc:
        # 補償順序與安裝相反；補償本身若失敗，保留 rollback 並明確報錯。
        compensation_errors = []
        if replace_uploads:
            try:
                if uploads_installed and upload_dir.exists():
                    shutil.rmtree(upload_dir)
                if old_uploads is not None and old_uploads.exists():
                    os.replace(old_uploads, upload_dir)
                    old_uploads = None
                elif uploads_existed and not upload_dir.exists():
                    shutil.copytree(rollback / 'uploads', upload_dir, symlinks=True)
            except Exception as rollback_exc:  # pragma: no cover - 極端 I/O 故障
                compensation_errors.append(str(rollback_exc))
        if db_installed:
            try:
                _restore_current_db(db_path, rollback, db_existed, db_sidecars)
            except Exception as rollback_exc:  # pragma: no cover - 極端 I/O 故障
                compensation_errors.append(str(rollback_exc))
        message = f'還原失敗，已嘗試回復原狀：{exc}；pre-restore：{rollback}'
        if compensation_errors:
            message += '；補償錯誤：' + '；'.join(compensation_errors)
        if isinstance(exc, BackupError):
            raise BackupError(message) from exc
        raise BackupError(message) from exc
    finally:
        shutil.rmtree(staging_db_dir, ignore_errors=True)
        if staging_upload_dir is not None:
            shutil.rmtree(staging_upload_dir, ignore_errors=True)

    return {
        'restored': True,
        'rollback_path': str(rollback),
        'uploads_restored': replace_uploads,
        'source': info,
    }
