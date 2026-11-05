# -*- coding: utf-8 -*-
"""v1.14.0：SQLite／附件備份、驗證與可回退還原（E3）。

所有還原測試都使用 tmp_path 下的獨立資料庫，絕不覆蓋 conftest.py 的共享庫。
"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app as app_module  # noqa: E402
import backup as backup_module  # noqa: E402
from app import app  # noqa: E402
from backup import (BackupError, create_backup, restore_backup,  # noqa: E402
                    verify_backup)
from utils import APP_VERSION  # noqa: E402


def _make_db(path, marker):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT);
            CREATE TABLE IF NOT EXISTS leave_types (id INTEGER PRIMARY KEY, code TEXT);
            CREATE TABLE IF NOT EXISTS leave_requests (id INTEGER PRIMARY KEY, reason TEXT);
            CREATE TABLE IF NOT EXISTS approvals (id INTEGER PRIMARY KEY, action TEXT);
            CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER PRIMARY KEY, detail TEXT);
            DELETE FROM users;
            DELETE FROM audit_logs;
        """)
        conn.execute('INSERT INTO users (username) VALUES (?)', (marker,))
        conn.execute('INSERT INTO audit_logs (detail) VALUES (?)', (marker,))
        conn.commit()
    finally:
        conn.close()
    return path


def _marker(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute('SELECT detail FROM audit_logs LIMIT 1').fetchone()[0]
    finally:
        conn.close()


def _manifest_path(bundle):
    return Path(bundle) / 'manifest.json'


def _refresh_manifest_entry(bundle, relative):
    """測試損壞 SQLite 時重算雜湊，確保驗證會深入到資料庫檢查。"""
    bundle = Path(bundle)
    path = bundle.joinpath(*relative.split('/'))
    manifest_path = _manifest_path(bundle)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    for entry in manifest['files']:
        if entry['path'] == relative:
            entry['size'] = path.stat().st_size
            entry['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')


def _full_bundle(tmp_path, marker='snapshot'):
    db = _make_db(tmp_path / 'source' / 'source.db', marker)
    uploads = tmp_path / 'source' / 'uploads'
    (uploads / 'nested').mkdir(parents=True)
    (uploads / 'proof.pdf').write_bytes(b'%PDF-original')
    (uploads / 'nested' / 'note.txt').write_text('附件內容', encoding='utf-8')
    result = create_backup(db, uploads, tmp_path / 'bundles', app_version=APP_VERSION)
    return db, uploads, Path(result['bundle_path'])


def test_version_is_at_least_1_14_0():
    assert tuple(map(int, APP_VERSION.split('.'))) >= (1, 14, 0)


def test_backup_contains_consistent_database_uploads_and_manifest(tmp_path):
    db, uploads, bundle = _full_bundle(tmp_path)
    _make_db(db, 'changed-after-backup')
    (uploads / 'proof.pdf').write_bytes(b'changed-after-backup')

    info = verify_backup(bundle)
    assert info['includes_uploads'] is True
    assert info['file_count'] == 3
    assert _marker(bundle / 'database.sqlite3') == 'snapshot'
    assert (bundle / 'uploads' / 'proof.pdf').read_bytes() == b'%PDF-original'
    manifest = json.loads(_manifest_path(bundle).read_text(encoding='utf-8'))
    assert manifest['app_version'] == APP_VERSION
    assert {entry['path'] for entry in manifest['files']} == {
        'database.sqlite3', 'uploads/proof.pdf', 'uploads/nested/note.txt'}


def test_db_only_backup_has_no_uploads(tmp_path):
    db = _make_db(tmp_path / 'source.db', 'db-only')
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    (uploads / 'private.pdf').write_bytes(b'private')
    result = create_backup(db, uploads, tmp_path / 'bundles', include_uploads=False)
    bundle = Path(result['bundle_path'])
    assert verify_backup(bundle)['includes_uploads'] is False
    assert not (bundle / 'uploads').exists()
    assert result['upload_count'] == 0 and result['file_count'] == 1


def test_backup_rejects_output_inside_uploads(tmp_path):
    db = _make_db(tmp_path / 'source.db', 'no-recursive-backup')
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    (uploads / 'private.pdf').write_bytes(b'private')
    with pytest.raises(BackupError, match='遞迴備份'):
        create_backup(db, uploads, uploads / 'backups')
    assert not (uploads / 'backups').exists()


def test_verify_rejects_database_tampering(tmp_path):
    _, _, bundle = _full_bundle(tmp_path)
    with open(bundle / 'database.sqlite3', 'ab') as f:
        f.write(b'tampered')
    with pytest.raises(BackupError, match='大小不符|SHA-256'):
        verify_backup(bundle)


def test_verify_rejects_same_size_upload_tampering(tmp_path):
    _, _, bundle = _full_bundle(tmp_path)
    target = bundle / 'uploads' / 'proof.pdf'
    original = target.read_bytes()
    target.write_bytes(b'X' * len(original))
    with pytest.raises(BackupError, match='SHA-256'):
        verify_backup(bundle)


def test_verify_rejects_non_sqlite_even_with_refreshed_hash(tmp_path):
    _, _, bundle = _full_bundle(tmp_path)
    (bundle / 'database.sqlite3').write_bytes(b'not a sqlite database')
    _refresh_manifest_entry(bundle, 'database.sqlite3')
    with pytest.raises(BackupError, match='有效的 SQLite'):
        verify_backup(bundle)


def test_verify_rejects_database_without_core_tables(tmp_path):
    _, _, bundle = _full_bundle(tmp_path)
    database = bundle / 'database.sqlite3'
    database.unlink()
    conn = sqlite3.connect(database)
    conn.execute('CREATE TABLE unrelated (id INTEGER)')
    conn.close()
    _refresh_manifest_entry(bundle, 'database.sqlite3')
    with pytest.raises(BackupError, match='核心資料表'):
        verify_backup(bundle)


def test_verify_rejects_path_traversal_and_unlisted_files(tmp_path):
    _, _, bundle = _full_bundle(tmp_path)
    manifest_path = _manifest_path(bundle)
    original = manifest_path.read_text(encoding='utf-8')
    manifest = json.loads(original)
    manifest['files'][1]['path'] = '../outside.pdf'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(BackupError, match='不安全路徑'):
        verify_backup(bundle)

    manifest_path.write_text(original, encoding='utf-8')
    (bundle / 'unexpected.txt').write_text('not listed', encoding='utf-8')
    with pytest.raises(BackupError, match='未列出'):
        verify_backup(bundle)


def test_verify_rejects_symlinks(tmp_path):
    _, _, bundle = _full_bundle(tmp_path)
    link = bundle / 'uploads' / 'link.txt'
    try:
        link.symlink_to(bundle / 'database.sqlite3')
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'此環境不允許建立符號連結：{exc}')
    with pytest.raises(BackupError, match='符號連結|實體檔案'):
        verify_backup(bundle)


def test_full_restore_round_trip_and_keeps_pre_restore_copy(tmp_path):
    _, _, bundle = _full_bundle(tmp_path, marker='restored-snapshot')
    target_db = _make_db(tmp_path / 'live' / 'live.db', 'before-restore')
    target_uploads = tmp_path / 'live' / 'uploads'
    target_uploads.mkdir()
    (target_uploads / 'old.pdf').write_bytes(b'old')
    Path(str(target_db) + '-wal').write_bytes(b'old-wal')
    Path(str(target_db) + '-shm').write_bytes(b'old-shm')

    result = restore_backup(bundle, target_db, target_uploads)
    assert _marker(target_db) == 'restored-snapshot'
    assert (target_uploads / 'proof.pdf').read_bytes() == b'%PDF-original'
    assert not (target_uploads / 'old.pdf').exists()
    assert not Path(str(target_db) + '-wal').exists()
    assert not Path(str(target_db) + '-shm').exists()

    rollback = Path(result['rollback_path'])
    # 先檢查模擬 sidecar；開啟 rollback DB 時 SQLite 可能會清理無效 WAL。
    assert (rollback / 'database.sqlite3-wal').read_bytes() == b'old-wal'
    assert (rollback / 'database.sqlite3-shm').read_bytes() == b'old-shm'
    assert _marker(rollback / 'database.sqlite3') == 'before-restore'
    assert (rollback / 'uploads' / 'old.pdf').read_bytes() == b'old'


def test_db_only_backup_restore_preserves_existing_uploads(tmp_path):
    source_db = _make_db(tmp_path / 'source.db', 'database-snapshot')
    source_uploads = tmp_path / 'source-uploads'
    source_uploads.mkdir()
    (source_uploads / 'not-in-backup.pdf').write_bytes(b'not included')
    created = create_backup(source_db, source_uploads, tmp_path / 'bundles',
                            include_uploads=False)
    target_db = _make_db(tmp_path / 'live.db', 'old-database')
    target_uploads = tmp_path / 'live-uploads'
    target_uploads.mkdir()
    (target_uploads / 'keep.pdf').write_bytes(b'keep me')

    result = restore_backup(created['bundle_path'], target_db, target_uploads)
    assert _marker(target_db) == 'database-snapshot'
    assert (target_uploads / 'keep.pdf').read_bytes() == b'keep me'
    assert result['uploads_restored'] is False
    assert not (Path(result['rollback_path']) / 'uploads').exists()


def test_db_only_option_preserves_uploads_even_for_full_bundle(tmp_path):
    _, _, bundle = _full_bundle(tmp_path)
    target_db = _make_db(tmp_path / 'live.db', 'old')
    target_uploads = tmp_path / 'live-uploads'
    target_uploads.mkdir()
    (target_uploads / 'keep.pdf').write_bytes(b'keep')
    result = restore_backup(bundle, target_db, target_uploads,
                            restore_uploads=False)
    assert result['uploads_restored'] is False
    assert (target_uploads / 'keep.pdf').read_bytes() == b'keep'
    assert not (target_uploads / 'proof.pdf').exists()


def test_restore_failure_rolls_back_database_and_uploads(tmp_path, monkeypatch):
    _, _, bundle = _full_bundle(tmp_path, marker='new-snapshot')
    target_db = _make_db(tmp_path / 'live' / 'live.db', 'original-database')
    target_uploads = tmp_path / 'live' / 'uploads'
    target_uploads.mkdir()
    (target_uploads / 'original.pdf').write_bytes(b'original-upload')
    original_validate = backup_module._validate_database

    def fail_after_install(path):
        if Path(path) == target_db:
            raise BackupError('模擬還原後檢查失敗')
        return original_validate(path)

    monkeypatch.setattr(backup_module, '_validate_database', fail_after_install)
    with pytest.raises(BackupError, match='已嘗試回復原狀'):
        restore_backup(bundle, target_db, target_uploads)
    assert _marker(target_db) == 'original-database'
    assert (target_uploads / 'original.pdf').read_bytes() == b'original-upload'
    assert not (target_uploads / 'proof.pdf').exists()
    rollbacks = list((target_db.parent / 'restore-rollbacks').iterdir())
    assert len(rollbacks) == 1
    assert _marker(rollbacks[0] / 'database.sqlite3') == 'original-database'


def test_cli_backup_and_verify(tmp_path, monkeypatch):
    db = _make_db(tmp_path / 'cli.db', 'cli-backup')
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    (uploads / 'cli.pdf').write_bytes(b'cli')
    monkeypatch.setattr(app_module, 'DB_PATH', str(db))
    monkeypatch.setattr(app_module, 'UPLOAD_DIR', str(uploads))
    runner = app.test_cli_runner()

    created = runner.invoke(args=['backup', '--output-dir', str(tmp_path / 'out')])
    assert created.exit_code == 0, created.output
    bundle = next((tmp_path / 'out').iterdir())
    verified = runner.invoke(args=['verify-backup', str(bundle)])
    assert verified.exit_code == 0, verified.output
    assert '驗證通過' in verified.output


def test_cli_restore_requires_yes_and_honours_db_only(tmp_path, monkeypatch):
    _, _, bundle = _full_bundle(tmp_path)
    target_db = _make_db(tmp_path / 'live.db', 'original')
    target_uploads = tmp_path / 'live-uploads'
    target_uploads.mkdir()
    (target_uploads / 'keep.pdf').write_bytes(b'keep')
    monkeypatch.setattr(app_module, 'DB_PATH', str(target_db))
    monkeypatch.setattr(app_module, 'UPLOAD_DIR', str(target_uploads))
    runner = app.test_cli_runner()

    refused = runner.invoke(args=['restore-backup', str(bundle)])
    assert refused.exit_code != 0
    assert '--yes' in refused.output
    assert _marker(target_db) == 'original'

    restored = runner.invoke(args=['restore-backup', str(bundle), '--yes', '--db-only'])
    assert restored.exit_code == 0, restored.output
    assert _marker(target_db) == 'snapshot'
    assert (target_uploads / 'keep.pdf').read_bytes() == b'keep'
    assert '還原前副本' in restored.output
