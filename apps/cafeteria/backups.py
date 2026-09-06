"""Encrypted complete backups; ZIP is an internal container, never the public format."""
import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings

from .crypto import active_key, decrypt_stream, encrypt_stream, secure_temporary_file
from .database import snapshot_database, verify_database

MAX_BACKUP_BYTES = 100 * 1024 * 1024


def build_encrypted_backup(*, database_connection=None, media_root=None, restrictions=None):
    target = secure_temporary_file()
    try:
        with tempfile.TemporaryDirectory(dir=settings.PRIVATE_TEMP_DIR) as work, secure_temporary_file() as archive:
            snapshot = Path(work) / "database.sqlite3"
            snapshot_database(snapshot, database_connection)
            verify_database(snapshot)
            media = Path(media_root or settings.MEDIA_ROOT).resolve()
            files = []
            if media.exists():
                if any(p.is_symlink() for p in media.rglob("*")):
                    raise ValueError("Private media must not contain symlinks")
                files = [p for p in media.rglob("*") if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(media)]
            if len(files) > 4997 or sum(p.stat().st_size for p in files) + snapshot.stat().st_size > MAX_BACKUP_BYTES - 1024 * 1024:
                raise ValueError("Backup exceeds the supported 100 MiB restore limit")
            from .privacy import load_restriction_ledger
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
                bundle.writestr("backup.json", json.dumps({
                    "format": "afa-ordis-backup", "version": 2,
                    "database_key": active_key("database")[0],
                }))
                bundle.write(snapshot, "database.sqlite3")
                bundle.writestr("restrictions.json", json.dumps(load_restriction_ledger() if restrictions is None else restrictions))
                for file in files:
                    relative = file.relative_to(media).as_posix()
                    # Refuse plaintext or damaged media before handing out a backup.
                    with file.open("rb") as source:
                        decrypt_stream(source, io.BytesIO(), purpose="media", context=relative.encode(), limit=10 * 1024 * 1024)
                    bundle.write(file, "media/" + relative)
            archive.seek(0)
            encrypt_stream(archive, target, purpose="backup")
        if target.tell() > MAX_BACKUP_BYTES:
            raise ValueError("Backup exceeds the supported 100 MiB restore limit")
        target.seek(0)
        return target
    except Exception:
        target.close()
        raise


def extract_encrypted_backup(source):
    staging = Path(tempfile.mkdtemp(prefix="afa-cipher-restore-", dir=settings.PRIVATE_TEMP_DIR))
    try:
        with secure_temporary_file() as clear:
            decrypt_stream(source, clear, purpose="backup", limit=MAX_BACKUP_BYTES)
            clear.seek(0)
            with zipfile.ZipFile(clear) as archive:
                items = archive.infolist()
                if not {"backup.json", "database.sqlite3", "restrictions.json"}.issubset({p.filename for p in items}):
                    raise ValueError("Incomplete encrypted backup")
                if len(items) > 5000 or len({p.filename for p in items}) != len(items) or sum(p.file_size for p in items) > MAX_BACKUP_BYTES:
                    raise ValueError("Invalid backup size or duplicate members")
                if archive.getinfo("backup.json").file_size > 4096:
                    raise ValueError("Invalid backup manifest")
                manifest = json.loads(archive.read("backup.json"))
                if not isinstance(manifest, dict) or manifest.get("format") != "afa-ordis-backup" or manifest.get("version") != 2 or not isinstance(manifest.get("database_key"), str):
                    raise ValueError("Unsupported encrypted backup version")
                for item in items:
                    member = Path(item.filename)
                    if member.is_absolute() or ".." in member.parts or item.is_dir() or "\\" in item.filename:
                        raise ValueError("Unsafe backup path")
                    if item.filename not in {"backup.json", "database.sqlite3", "restrictions.json"} and not item.filename.startswith("media/"):
                        raise ValueError("Unexpected backup member")
                    dest = staging / member
                    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with archive.open(item) as entry, dest.open("xb") as output:
                        shutil.copyfileobj(entry, output)
                    dest.chmod(0o600)
                for file in (staging / "media").rglob("*"):
                    if file.is_file():
                        relative = file.relative_to(staging / "media").as_posix()
                        with file.open("rb") as entry:
                            decrypt_stream(entry, io.BytesIO(), purpose="media", context=relative.encode(), limit=10 * 1024 * 1024)
                verify_database(staging / "database.sqlite3", manifest["database_key"])
        return staging, staging / "database.sqlite3", manifest
    except Exception:
        shutil.rmtree(staging)
        raise


def atomic_encrypted_write(path, payload, *, purpose="backup", context=b""):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".encrypted-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            encrypt_stream(io.BytesIO(payload), output, purpose=purpose, context=context)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
