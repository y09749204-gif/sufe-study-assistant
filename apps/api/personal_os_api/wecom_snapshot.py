"""Offline, committed-only snapshots of the owner's wxSQLite3 databases.

Source files are only read. Never attach a live WAL to a decrypted database.
The caller must keep the destination in a private, access-restricted directory.
"""
import hashlib
from contextlib import closing
import sqlite3
import struct
from pathlib import Path

from personal_os_api.vendor.wecom.crypto import decrypt_page, verify_key

PAGE_SIZE = 4096


def checksum(data, endian, seed=(0, 0)):
    a, b = seed
    for x, y in struct.iter_unpack(endian + 'II', data):
        a = (a + x + b) & 0xffffffff
        b = (b + y + a) & 0xffffffff
    return a, b


def committed_frames(wal):
    if not wal:
        return {}, None, 0
    if len(wal) < 32:
        raise ValueError('Incomplete WAL header')
    magic, version, size, _, salt1, salt2, a, b = struct.unpack('>8I', wal[:32])
    if magic not in (0x377f0682, 0x377f0683) or size != PAGE_SIZE or version != 3007000:
        raise ValueError('Unsupported WAL format')
    endian = '<' if magic == 0x377f0682 else '>'
    running = checksum(wal[:24], endian)
    if running != (a, b):
        raise ValueError('WAL header checksum mismatch')
    pending, committed, final_size, count = {}, {}, None, 0
    for offset in range(32, len(wal) - 24 - PAGE_SIZE + 1, 24 + PAGE_SIZE):
        header = wal[offset:offset+24]
        page = wal[offset+24:offset+24+PAGE_SIZE]
        number, dbsize, s1, s2, a, b = struct.unpack('>6I', header)
        if (s1, s2) != (salt1, salt2):
            break  # Old frames retained after a WAL restart.
        if number == 0:
            raise ValueError('Invalid WAL page number')
        running = checksum(header[:8] + page, endian, running)
        if running != (a, b):
            raise ValueError('WAL frame checksum mismatch; retry a fresh snapshot')
        pending[number] = page
        if dbsize:
            committed.update(pending)
            committed = {n: p for n, p in committed.items() if n <= dbsize}
            pending.clear()
            final_size = dbsize
            count += 1
    return committed, final_size, count


def stable_pair(source, attempts=3):
    """Require two identical full reads; reject a moving/checkpointing source."""
    source = Path(source)
    wal_path = source.with_name(source.name + '-wal')
    def read():
        return source.read_bytes(), wal_path.read_bytes() if wal_path.exists() else b''
    for _ in range(attempts):
        first = read()
        if first == read():
            return first
    raise RuntimeError('Database changed during capture; retry when idle')


def materialize(database, wal, key):
    if len(database) < PAGE_SIZE or len(database) % PAGE_SIZE:
        raise ValueError('Incomplete source database')
    if not verify_key(key, database[:PAGE_SIZE]):
        raise ValueError('Key does not match this database')
    pages, size, commits = committed_frames(wal)
    size = size if size is not None else len(database) // PAGE_SIZE
    if size < 1 or size > len(database)//PAGE_SIZE + len(pages):
        raise ValueError('WAL references unavailable pages')
    output = bytearray()
    for number in range(1, size+1):
        page = pages.get(number, database[(number-1)*PAGE_SIZE:number*PAGE_SIZE])
        if len(page) != PAGE_SIZE:
            raise ValueError('Missing database page')
        output.extend(decrypt_page(key, page, number))
    # The new standalone snapshot has no WAL. Explicitly use rollback format.
    output[18:20] = b'\x01\x01'
    return bytes(output), commits


def snapshot(source, destination, key):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination.is_relative_to(source.parent):
        raise ValueError('Snapshot must not write inside the source directory')
    database, wal = stable_pair(source)
    plain, commits = materialize(database, wal, key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + '.pending')
    temporary.write_bytes(plain)
    with closing(sqlite3.connect(temporary.as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Snapshot integrity check failed')
    temporary.replace(destination)
    return {'sha256': hashlib.sha256(plain).hexdigest(), 'wal_commits': commits,
            'bytes': len(plain)}
