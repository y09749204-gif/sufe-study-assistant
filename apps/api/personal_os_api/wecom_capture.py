"""Identity-bound local evidence capture. No task creation or outbound actions."""
import base64
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

OWNER = 0
EXPECTED_NAME = ""
EXPECTED_ORG = ""
CORP = 0


def protobuf_fields(payload):
    """Bounded wire decoder. Unknown structures are preserved, never guessed."""
    data = bytes(payload or b'')
    if len(data) > 4_000_000:
        raise ValueError('Payload too large')
    offset, fields = 0, {}
    def varint():
        nonlocal offset
        value = 0
        for shift in range(0, 70, 7):
            if offset >= len(data):
                raise ValueError('Truncated varint')
            b = data[offset]; offset += 1
            value |= (b & 127) << shift
            if not b & 128:
                return value
        raise ValueError('Invalid varint')
    while offset < len(data):
        tag = varint()
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError('Invalid field')
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            length = varint() if wire == 2 else (8 if wire == 1 else 4)
            if offset + length > len(data):
                raise ValueError('Truncated field')
            value = data[offset:offset+length]; offset += length
        else:
            raise ValueError('Unsupported wire type')
        fields.setdefault(number, []).append(value)
    return fields


def content_fields(kind, raw):
    result = {'text': None, 'attachment_name': None, 'parse_status': 'unsupported',
              'reply_status': 'unresolved'}
    try:
        fields = protobuf_fields(raw)
        if kind in (0, 2):
            pieces, unknown = [], []
            for segment in fields[1]:
                part = protobuf_fields(segment)
                segment_type = part[1][0]
                if segment_type == 0:
                    pieces.append(protobuf_fields(part[2][0])[1][0].decode('utf-8'))
                else:
                    unknown.append(segment_type)
            if not pieces:
                raise ValueError('No supported text segments')
            result.update(text=''.join(pieces), parse_status='partial_text' if unknown else 'text_parsed',
                          unsupported_segments=unknown)
        elif kind == 15:
            name = fields[2][0].decode('utf-8')
            if '/' in name or '\\' in name or name in ('.', '..'):
                raise ValueError('Unsafe attachment name')
            result.update(attachment_name=name, parse_status='metadata_only')
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        result['parse_status'] = 'unparsed'
    return result


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)


def validate_identity(snapshot_dir):
    with closing(readonly(Path(snapshot_dir)/'user.db')) as conn:
        row = conn.execute('SELECT id,name,corp_id FROM user_table WHERE id=?', (OWNER,)).fetchone()
    with closing(readonly(Path(snapshot_dir)/'company.db')) as conn:
        org = conn.execute('SELECT name FROM external_company_table_v2 WHERE corpany_id=?', (CORP,)).fetchone()
    if row != (OWNER, EXPECTED_NAME, CORP) or org != (EXPECTED_ORG,):
        raise ValueError('WeCom identity does not match the configured owner and organization')
    return {'user_id': str(OWNER), 'organization_id': str(CORP), 'name': row[1], 'organization': org[0]}


def raw_value(value):
    return {'base64': base64.b64encode(value).decode('ascii')} if isinstance(value, bytes) else value


def embedded_reference(row, source):
    """Match embedded original metadata, never infer a reply from proximity."""
    try:
        fields = protobuf_fields(row['extra_content'])
        if 3071 in fields:
            return branch_reference(row, source, fields[3071][0])
        if 1002 not in fields:
            return {'status': 'no_supported_reference_field'}
        original = protobuf_fields(protobuf_fields(fields[1002][0])[1][0])
        sender, timestamp, kind = (original[n][0] for n in (1, 2, 5))
        matches = source.execute('SELECT message_id FROM message_table WHERE conversation_id=? AND sender_id=? AND send_time=? AND content_type=?',
                                 (row['conversation_id'], sender, timestamp, kind)).fetchall()
        if len(matches) != 1:
            return {'status': 'unresolved_embedded_reference', 'matches': len(matches)}
        return {'status': 'matched_embedded_reference', 'relation': 'embedded_original',
                'target_id': f"wecom:{CORP}:{OWNER}:{row['conversation_id']}:{matches[0][0]}",
                'basis': 'same_conversation_sender_timestamp_and_type'}
    except (KeyError, IndexError, ValueError, TypeError):
        return {'status': 'unsupported_reference_format'}


def branch_reference(row, source, metadata):
    """Resolve a branch root by its sender, time and opaque original message ID."""
    original = protobuf_fields(protobuf_fields(metadata)[1][0])
    sender, timestamp, token = (original[n][0] for n in (1, 2, 3))
    if not isinstance(token, bytes) or not token:
        raise ValueError('Missing branch original ID')
    matches = []
    for candidate in source.execute(
            'SELECT message_id,extra_content FROM message_table WHERE conversation_id=? AND sender_id=? AND send_time=?',
            (row['conversation_id'], sender, timestamp)):
        try:
            signed = protobuf_fields(protobuf_fields(candidate[1])[3045][0])
            candidate_token = protobuf_fields(signed[3][0])[3][0]
            if candidate_token == token and candidate[0] != row['message_id']:
                matches.append(candidate[0])
        except (KeyError, IndexError, ValueError, TypeError):
            continue
    if len(matches) != 1:
        return {'status': 'unresolved_branch_reply', 'matches': len(matches)}
    return {'status': 'matched_branch_reply', 'relation': 'branch_root',
            'target_id': f"wecom:{CORP}:{OWNER}:{row['conversation_id']}:{matches[0]}",
            'basis': 'same_conversation_sender_timestamp_and_original_id'}


def file_catalog(snapshot_dir, cache_directory):
    if cache_directory is None:
        return {}, {}
    root = Path(cache_directory).resolve()
    if root.name != 'File' or root.parent.name != 'Cache' or root.parent.parent.name != str(OWNER):
        raise ValueError('Unexpected owner attachment cache')
    candidates = {}
    for path in root.rglob('*'):
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):
            candidates.setdefault((path.name, path.stat().st_size), []).append(path)
    with closing(readonly(Path(snapshot_dir)/'file.db')) as conn:
        conn.row_factory = sqlite3.Row
        files = {}
        for row in conn.execute('SELECT message_id,name,size,md5 FROM file_table4'):
            files.setdefault(row['message_id'], []).append(dict(row))
    return files, candidates


def attachments_for(message_id, files, candidates, hashes):
    result = []
    for record in files.get(message_id, []):
        item = {'name': record['name'], 'bytes': record['size'], 'status': 'not_cached'}
        expected = record['md5']
        if not isinstance(expected, str) or not re.fullmatch('[a-fA-F0-9]{32}', expected):
            item['status'] = 'unverified_digest'
        else:
            for path in candidates.get((record['name'], record['size']), []):
                if path not in hashes:
                    with path.open('rb') as handle:
                        hashes[path] = hashlib.file_digest(handle, 'md5').hexdigest()
                if hashes[path] == expected.lower():
                    item.update(status='verified_local_file', path=str(path.resolve()))
                    break
        result.append(item)
    return result


def capture(snapshot_dir, evidence_path, now=None, cache_directory=None):
    identity = validate_identity(snapshot_dir)
    now = now or datetime.now(timezone.utc)
    floor = int((now-timedelta(days=30)).timestamp())
    evidence_path = Path(evidence_path).resolve()
    if evidence_path.is_relative_to(Path(snapshot_dir).resolve()):
        raise ValueError('Evidence database must be separate from snapshots')
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    files, candidates = file_catalog(snapshot_dir, cache_directory)
    file_hashes = {}
    with closing(readonly(Path(snapshot_dir)/'session.db')) as conn:
        conversations = dict(conn.execute('SELECT id,name FROM conversation_table'))
    with closing(readonly(Path(snapshot_dir)/'user.db')) as conn:
        people = dict(conn.execute('SELECT id,name FROM user_table'))
    with closing(readonly(Path(snapshot_dir)/'message.db')) as source, closing(sqlite3.connect(evidence_path)) as dest:
        source.row_factory = sqlite3.Row
        dest.executescript('''
          CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY, revision TEXT NOT NULL, payload TEXT NOT NULL, captured_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS revisions (
            id TEXT, revision TEXT, payload TEXT NOT NULL, captured_at TEXT NOT NULL, PRIMARY KEY(id,revision));
          CREATE TABLE IF NOT EXISTS runs (finished_at TEXT NOT NULL, report TEXT NOT NULL);
        ''')
        report = {'identity': identity, 'new': 0, 'updated': 0, 'unchanged': 0,
                  'text_parsed': 0, 'partial_text': 0, 'metadata_only': 0, 'unparsed': 0, 'unsupported': 0,
                  'source_duplicates': 0, 'earliest': None, 'latest': None,
                  'verified_local_attachments': 0, 'missing_attachments': 0,
                  'attachment_statuses': {}, 'matched_embedded_references': 0,
                  'matched_branch_replies': 0}
        seen, chats = {}, set()
        with dest:
            for table in ('message_table', 'message_small_table'):
                for row in source.execute(f'SELECT * FROM {table} WHERE send_time>=? ORDER BY send_time,message_id', (floor,)):
                    if not row['message_id'] or not row['conversation_id']:
                        raise ValueError('Message missing stable identity')
                    identifier = f"wecom:{CORP}:{OWNER}:{row['conversation_id']}:{row['message_id']}"
                    raw = {k: raw_value(row[k]) for k in row.keys()}
                    digest = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
                    if identifier in seen:
                        if seen[identifier] != digest:
                            raise ValueError('Conflicting duplicate across message tables')
                        report['source_duplicates'] += 1
                        continue
                    seen[identifier] = digest
                    parsed = content_fields(row['content_type'], row['content'])
                    reference = embedded_reference(row, source)
                    attachments = attachments_for(row['message_id'], files, candidates, file_hashes)
                    report['verified_local_attachments'] += sum(a['status']=='verified_local_file' for a in attachments)
                    report['missing_attachments'] += sum(a['status']!='verified_local_file' for a in attachments)
                    for attachment in attachments:
                        status = attachment['status']
                        report['attachment_statuses'][status] = report['attachment_statuses'].get(status, 0) + 1
                    report['matched_embedded_references'] += reference['status']=='matched_embedded_reference'
                    report['matched_branch_replies'] += reference['status']=='matched_branch_reply'
                    if reference['status'] == 'matched_branch_reply':
                        parsed['reply_status'] = 'branch_root_resolved'
                    payload = {**parsed, 'source': 'wecom', 'account': str(OWNER), 'organization': str(CORP),
                               'conversation_id': row['conversation_id'],
                               'conversation_name': conversations.get(row['conversation_id']),
                               'sender_id': str(row['sender_id']), 'sender_name': people.get(row['sender_id']),
                               'direction': 'outbound' if row['sender_id'] == OWNER else 'inbound',
                               'sent_at': row['send_time'], 'content_type': row['content_type'],
                               'attachment_status': 'checked' if cache_directory is not None else 'not_checked',
                               'attachments': attachments, 'embedded_reference': reference, 'raw': raw}
                    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                    # Include resolved fields in the revision so names/parser upgrades propagate.
                    revision = hashlib.sha256(serialized.encode()).hexdigest()
                    prior = dest.execute('SELECT revision FROM evidence WHERE id=?', (identifier,)).fetchone()
                    report['new' if not prior else 'unchanged' if prior[0] == revision else 'updated'] += 1
                    stamp = now.isoformat()
                    dest.execute('INSERT OR IGNORE INTO revisions VALUES (?,?,?,?)', (identifier, revision, serialized, stamp))
                    dest.execute('INSERT INTO evidence VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,captured_at=excluded.captured_at WHERE evidence.revision<>excluded.revision', (identifier, revision, serialized, stamp))
                    report[parsed['parse_status']] += 1
                    chats.add(row['conversation_id'])
                    t = row['send_time']
                    report['earliest'] = t if report['earliest'] is None else min(report['earliest'], t)
                    report['latest'] = t if report['latest'] is None else max(report['latest'], t)
            report['conversations'] = len(chats)
            report['conversation_types'] = {
                'group': sum(chat.startswith('R:') for chat in chats),
                'direct': sum(chat.startswith('S:') for chat in chats),
                'other': sum(not chat.startswith(('R:', 'S:')) for chat in chats),
            }
            report['messages'] = len(seen)
            report['scope'] = 'locally_available_last_30_days'
            report['raw_payloads_retained'] = True
            # The end-to-end acceptance evaluator owns the public gate.  Capture
            # alone cannot prove controlled replies or restart persistence.
            report['gate_passed'] = False
            dest.execute('INSERT INTO runs VALUES (?,?)', (now.isoformat(), json.dumps(report, ensure_ascii=False)))
        return report
