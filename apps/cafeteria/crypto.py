"""Versioned authenticated streams. Key material never enters Django's database/logs."""
import base64
import io
import json
import os
import struct
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files import File
from django.core.files.storage import FileSystemStorage
from django.views.decorators.debug import sensitive_variables
from nacl import bindings as sodium
from nacl.exceptions import CryptoError

MAGIC = b"AFAENC\x01"
CHUNK = 64 * 1024
MAX_CLEAR_BYTES = 300 * 1024 * 1024


@sensitive_variables()
def keyring(path=None):
    try:
        source = Path(path or settings.ENCRYPTION_KEY_FILE)
        if not settings.DEBUG and source.stat().st_mode & 0o077:
            raise ValueError("Key file must have mode 0400 or 0600")
        ring = json.loads(source.read_text())
        if ring["version"] != 1:
            raise ValueError("Unsupported key file")
        keys = {kid: base64.b64decode(value, validate=True) for kid, value in ring["keys"].items()}
        if any(len(value) != 32 or not kid.isascii() or not kid.isalnum() or len(kid) > 64 for kid, value in keys.items()):
            raise ValueError("Invalid keys")
        active = ring["active"]
        if any(active[purpose] not in keys for purpose in ("database", "media", "backup")):
            raise ValueError("Missing active keys")
        if len({keys[active[purpose]] for purpose in ("database", "media", "backup")}) != 3:
            raise ValueError("Keys must be independent")
        return active, keys
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ImproperlyConfigured("Encryption key file missing or invalid; no plaintext fallback is permitted.") from error


@sensitive_variables()
def active_key(purpose):
    active, keys = keyring()
    return active[purpose], keys[active[purpose]]


@sensitive_variables()
def encrypt_stream(source, target, *, purpose, context=b""):
    kid, key = active_key(purpose)
    state = sodium.crypto_secretstream_xchacha20poly1305_state()
    header = sodium.crypto_secretstream_xchacha20poly1305_init_push(state, key)
    prefix = MAGIC + bytes([len(kid)]) + kid.encode("ascii") + header
    target.write(prefix)
    aad = prefix + purpose.encode("ascii") + b"\0" + context
    while True:
        block = source.read(CHUNK)
        final = not block
        tag = sodium.crypto_secretstream_xchacha20poly1305_TAG_FINAL if final else sodium.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE
        encrypted = sodium.crypto_secretstream_xchacha20poly1305_push(state, block, aad, tag)
        target.write(struct.pack(">I", len(encrypted)))
        target.write(encrypted)
        if final:
            return


def _read(source, size):
    data = source.read(size)
    if len(data) != size:
        raise ValueError("Incomplete encrypted file")
    return data


@sensitive_variables()
def decrypt_stream(source, target, *, purpose, context=b"", limit=MAX_CLEAR_BYTES):
    """Callers must not publish target until authentication of the FINAL tag succeeds."""
    try:
        if _read(source, len(MAGIC)) != MAGIC:
            raise ValueError("Only encrypted backups/files are accepted")
        length = _read(source, 1)
        if not 1 <= length[0] <= 64:
            raise ValueError("Invalid encrypted header")
        kid_bytes = _read(source, length[0])
        kid = kid_bytes.decode("ascii")
        header = _read(source, sodium.crypto_secretstream_xchacha20poly1305_HEADERBYTES)
        prefix = MAGIC + length + kid_bytes + header
        _, keys = keyring()
        state = sodium.crypto_secretstream_xchacha20poly1305_state()
        sodium.crypto_secretstream_xchacha20poly1305_init_pull(state, header, keys[kid])
        aad = prefix + purpose.encode("ascii") + b"\0" + context
        total = 0
        while True:
            size = struct.unpack(">I", _read(source, 4))[0]
            if not sodium.crypto_secretstream_xchacha20poly1305_ABYTES <= size <= CHUNK + sodium.crypto_secretstream_xchacha20poly1305_ABYTES:
                raise ValueError("Invalid encrypted block")
            clear, tag = sodium.crypto_secretstream_xchacha20poly1305_pull(state, _read(source, size), aad)
            total += len(clear)
            if total > limit:
                raise ValueError("Encrypted file exceeds size limit")
            target.write(clear)
            if tag == sodium.crypto_secretstream_xchacha20poly1305_TAG_FINAL:
                if source.read(1):
                    raise ValueError("Trailing encrypted data")
                return kid
            if tag != sodium.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE:
                raise ValueError("Invalid stream tag")
    except (CryptoError, KeyError, UnicodeError, struct.error) as error:
        raise ValueError("Encrypted file cannot be authenticated with the available keys") from error


class EncryptedStorage(FileSystemStorage):
    """Medical/receipt files are bounded to 10 MiB and fully verified before download."""
    def _save(self, name, content):
        if content.size > 10 * 1024 * 1024:
            raise ValueError("Private documents must not exceed 10 MiB")
        # Reserve the final random path before binding the ciphertext to that path.
        full_path = Path(self.path(name))
        full_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(full_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as target:
                encrypt_stream(content, target, purpose="media", context=name.encode())
        except Exception:
            full_path.unlink(missing_ok=True)
            raise
        return name

    def _open(self, name, mode="rb"):
        if mode not in {"r", "rb"}:
            raise ValueError("Encrypted files are immutable; use storage.save()")
        clear = io.BytesIO()
        with open(self.path(name), "rb") as source:
            decrypt_stream(source, clear, purpose="media", context=name.encode(), limit=10 * 1024 * 1024)
        clear.seek(0)
        return File(clear, name=name)

    def url(self, name):
        raise ValueError("Private files have no public URL")


def secure_temporary_file():
    return tempfile.TemporaryFile(dir=settings.PRIVATE_TEMP_DIR)
