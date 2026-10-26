"""Download and verify a complete, immutable cloud release for GitHub Pages.

The private Sites website queries its database directly. These v3 files are only
the compatibility bundle for GitHub Pages; no local database or capture is read.
Credentials come from environment variables and are never written to the bundle.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import tarfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

BUCKET_COUNT = 128
FRONTEND_FILES = ('index.html', 'app.js', 'player-name.js', 'rating-core.mjs', 'score-chart.mjs', 'styles.css')
PLAYER_FIELDS = {'id', 'bucket', 'avatar', 'nickname', 'display_rating', 'rating_deviation', 'provisional',
                 'rated_board_count', 'rated_match_count', 'rating_tier', 'last_rated_day',
                 'peak_display_rating', 'gain_1d', 'gain_7d', 'gain_30d', 'delta_1d', 'delta_7d',
                 'delta_30d', 'rank', 'avg_vp_30d', 'recent_match_count'}
MANIFEST_FIELDS = {'algorithm', 'cutoff', 'generated_at', 'history_start', 'history_end', 'recent_start',
                   'recent_end_exclusive', 'schema_version', 'player_id_format', 'competition_id_format',
                   'bucket_count', 'display_rating_policy', 'rating_tier_policy', 'stats', 'paths'}
STAT_FIELDS = {'players', 'mature_players', 'provisional_players', 'active_30d', 'valid_matches_30d',
               'boards_30d', 'median_mature_rating', 'tier_counts'}
MATCH_FIELDS = {'day', 'round', 'table', 'pairing', 'score', 'opponent_score', 'score_unit', 'boards',
                'opponent_display_rating_before', 'opponent_provisional_before', 'opponent_rating_tier_before',
                'competition_id', 'opponent_id', 'opponent_avatar'}
ID = re.compile(r'[a-f0-9]{24}\Z')
VERSION = re.compile(r'[a-f0-9]{64}\Z')
AVATAR = re.compile(r'avatars/[a-f0-9]{24}\.(?:jpg|jpeg|png|gif|webp)\Z')
PHONE = re.compile(r'1[3-9][0-9]{9}\Z')
PUBLIC_PARTS = frozenset(('release', 'package'))
PACKAGE_NAME = re.compile(r'(?:metadata|data-\d{3}|avatars-\d{5,})\.tar\Z')
MAX_PACKAGE = 16 * 1024 * 1024
# Keep diagnostics limited to exact public-route messages. SQL, response bodies,
# arbitrary headers and user data never appear in the CI error message.
SAFE_ERROR_CODES = {
    '需要 GitHub 公开产物读取权限': 'PUBLICATION_ACCESS_REQUIRED',
    '公开产物请求必须固定成功发布版本': 'PUBLICATION_VERSION_REQUIRED',
    '成功发布版本已变化，请重新生成 GitHub 产物': 'PUBLICATION_VERSION_CHANGED',
    '评级发布已更新，请刷新后重试': 'PUBLICATION_VERSION_CHANGED',
    '云端数据库尚未完成迁移': 'DATABASE_NOT_READY',
    '当前尚无可查询的成功评级发布': 'NO_SUCCESSFUL_PUBLICATION',
    '评级发布截止点无效': 'PUBLICATION_CUTOFF_INVALID',
    '评级发布政策不完整': 'PUBLICATION_POLICY_INCOMPLETE',
    '评级账本与发布截止点不一致': 'LEDGER_CUTOFF_MISMATCH',
    '公开头像路径与 GitHub 网页版本不兼容': 'AVATAR_PATH_INCOMPATIBLE',
    '评级账本中的对局坐标无效': 'MATCH_COORDINATES_INVALID',
    '数据库查询暂时不可用，请稍后重试': 'DATABASE_QUERY_UNAVAILABLE',
    '需要有效分桶参数': 'BUCKET_INVALID',
    '分桶参数应为 0–127': 'BUCKET_INVALID',
    '公开产物种类无效': 'PUBLICATION_PART_INVALID',
    '公开标识格式无效': 'PUBLIC_ID_INVALID',
    '该公开牌手没有头像': 'AVATAR_UNAVAILABLE',
    '云端头像存储尚未配置': 'AVATAR_STORAGE_NOT_CONFIGURED',
    '公开头像尚未完成迁移': 'AVATAR_NOT_READY',
}
SAFE_ERROR_CODE_VALUES = frozenset(SAFE_ERROR_CODES.values()) | {'UNCLASSIFIED_UPSTREAM_ERROR', 'NETWORK_UNAVAILABLE'}
DATABASE_FAILURE_REASONS = frozenset((
    'MISSING_TABLE', 'MISSING_COLUMN', 'SQL_SYNTAX', 'BOUND_PARAMETER_LIMIT', 'ROW_SIZE_LIMIT',
    'COMPOUND_SELECT_LIMIT', 'SQL_SIZE_LIMIT', 'SQL_FUNCTION_ARGUMENT_LIMIT', 'PATTERN_SIZE_LIMIT',
    'COLUMN_LIMIT', 'QUERY_TIMEOUT', 'QUERY_INTERRUPTED', 'SUBREQUEST_LIMIT', 'RESOURCE_LIMIT',
    'DATABASE_BUSY', 'AUTHORIZATION', 'TYPE_MISMATCH', 'CONSTRAINT', 'DAILY_ROW_READ_LIMIT',
    'DAILY_ROW_WRITE_LIMIT', 'ACCOUNT_STORAGE_LIMIT', 'DATABASE_STORAGE_LIMIT', 'DATABASE_RESET',
    'NETWORK_DISCONNECTED', 'TRANSIENT_REMOTE_NODE', 'UNKNOWN',
))
DATABASE_FAILURE_CODES = frozenset((
    'D1_ERROR', 'D1_EXEC_ERROR', 'D1_TYPE_ERROR', 'D1_COLUMN_NOTFOUND', 'SQLITE_ERROR',
    'SQLITE_TOOBIG', 'SQLITE_RANGE', 'SQLITE_NOMEM', 'SQLITE_BUSY', 'SQLITE_LOCKED', 'SQLITE_AUTH',
    'SQLITE_PERM', 'SQLITE_CONSTRAINT', 'SQLITE_CONSTRAINT_FOREIGNKEY', 'SQLITE_CONSTRAINT_UNIQUE',
    'SQLITE_CONSTRAINT_NOTNULL', 'SQLITE_CONSTRAINT_CHECK', 'SQLITE_INTERRUPT', 'SQLITE_ABORT',
    'SQLITE_FULL', 'UNKNOWN',
))
DATABASE_RESOURCE_KINDS = frozenset(('MEMORY_LIMIT', 'CPU_LIMIT', 'UNKNOWN_RESOURCE'))


class PublicationError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class CloudRequestError(PublicationError):
    def __init__(self, part, bucket, status, error_code, reason=None, code=None, resource_kind=None):
        self.part = part if part in PUBLIC_PARTS else 'invalid'
        self.bucket = bucket if type(bucket) is int and 0 <= bucket < BUCKET_COUNT else None
        self.http_status = status if type(status) is int and 100 <= status < 600 else None
        self.safe_error_code = error_code if error_code in SAFE_ERROR_CODE_VALUES else 'UNCLASSIFIED_UPSTREAM_ERROR'
        self.database_reason = reason if isinstance(reason, str) and reason in DATABASE_FAILURE_REASONS else None
        self.database_code = code if isinstance(code, str) and code in DATABASE_FAILURE_CODES else None
        self.resource_kind = (resource_kind if self.database_reason == 'RESOURCE_LIMIT'
            and isinstance(resource_kind, str) and resource_kind in DATABASE_RESOURCE_KINDS else None)
        label = f'part={self.part}'
        if self.bucket is not None:
            label += f' bucket={self.bucket:03d}'
        message = f'Cloud public export failed {label} http_status={self.http_status} safe_error_code={self.safe_error_code}'
        if self.database_reason is not None:
            message += f' reason={self.database_reason}'
        if self.database_code is not None:
            message += f' code={self.database_code}'
        if self.resource_kind is not None:
            message += f' resource_kind={self.resource_kind}'
        super().__init__(message)


def safe_http_error(error):
    code = 'UNCLASSIFIED_UPSTREAM_ERROR'
    reason = database_code = resource_kind = None
    try:
        payload = json.loads(error.read(8192))
        if isinstance(payload, dict):
            if isinstance(payload.get('error'), str):
                code = SAFE_ERROR_CODES.get(payload['error'], code)
            explicit_code = payload.get('error_code')
            if code == 'UNCLASSIFIED_UPSTREAM_ERROR' and isinstance(explicit_code, str) and explicit_code in SAFE_ERROR_CODE_VALUES:
                code = explicit_code
            if code == 'DATABASE_QUERY_UNAVAILABLE':
                candidate = payload.get('reason')
                if isinstance(candidate, str) and candidate in DATABASE_FAILURE_REASONS:
                    reason = candidate
                candidate = payload.get('code')
                if isinstance(candidate, str) and candidate in DATABASE_FAILURE_CODES:
                    database_code = candidate
                candidate = payload.get('resource_kind')
                if reason == 'RESOURCE_LIMIT' and isinstance(candidate, str) and candidate in DATABASE_RESOURCE_KINDS:
                    resource_kind = candidate
    except (ValueError, OSError, TypeError):
        pass
    finally:
        error.close()
    return code, reason, database_code, resource_kind


class ExportProgress:
    def __init__(self, stage, total):
        self.stage, self.total, self.completed = stage, total, 0
        self.started = self.last_report = time.perf_counter()
        self.emit('export_stage_start')

    def emit(self, event):
        print(json.dumps({'event': event, 'stage': self.stage, 'completed': self.completed,
            'total': self.total, 'elapsed_seconds': round(time.perf_counter() - self.started, 3)}), flush=True)
        self.last_report = time.perf_counter()

    def advance(self):
        self.completed += 1
        batch = 256 if self.stage == 'avatars' else 32
        if self.completed < self.total and (self.completed % batch == 0 or time.perf_counter() - self.last_report >= 30):
            self.emit('export_stage_progress')

    def finish(self):
        self.emit('export_stage_complete')


def object_fields(value, fields, label):
    if not isinstance(value, dict) or set(value) != fields:
        raise PublicationError(f'{label} has an incompatible public field list')


def finite_values(value):
    if isinstance(value, dict):
        for item in value.values():
            finite_values(item)
    elif isinstance(value, list):
        for item in value:
            finite_values(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise PublicationError('Public data contains a non-finite number')


def public_name(value):
    if not isinstance(value, str) or PHONE.fullmatch(value):
        raise PublicationError('Public nickname masking is incomplete')


def public_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise PublicationError('Invalid public identifier')


def avatar_path(value):
    if value is not None and (not isinstance(value, str) or not AVATAR.fullmatch(value)):
        raise PublicationError('Invalid public avatar path')


def validate_manifest(value):
    object_fields(value, MANIFEST_FIELDS, 'Manifest')
    if (value['schema_version'] != 'xinrui-rating-web-v3' or value['bucket_count'] != BUCKET_COUNT
            or value['player_id_format'] != 'hex24' or value['competition_id_format'] != 'hex24'):
        raise PublicationError('Cloud export is incompatible with the existing website')
    object_fields(value['stats'], STAT_FIELDS, 'Statistics')
    object_fields(value['display_rating_policy'], {'version', 'alignment_match_count', 'count_unit'}, 'Display policy')
    object_fields(value['rating_tier_policy'], {'version', 'mature_only', 'tiers'}, 'Tier policy')
    for tier in value['rating_tier_policy']['tiers']:
        object_fields(tier, {'code', 'color', 'lower_bound', 'upper_bound'}, 'Rating tier')
    if value['paths'] != {'index': './data/index.json.gz', 'history': './data/history/{bucket}.json.gz',
                          'recent': './data/recent/{bucket}.json.gz'}:
        raise PublicationError('Unexpected GitHub public bundle paths')
    finite_values(value)


def validate_package(package):
    object_fields(package, {'name', 'size', 'sha256', 'files'}, 'Release package')
    if (not isinstance(package['name'], str) or not PACKAGE_NAME.fullmatch(package['name'])
            or type(package['size']) is not int or not 1024 <= package['size'] <= MAX_PACKAGE
            or not isinstance(package['sha256'], str) or not VERSION.fullmatch(package['sha256'])
            or not isinstance(package['files'], list) or not package['files']):
        raise PublicationError('Invalid release package metadata')
    paths = set()
    for item in package['files']:
        object_fields(item, {'path', 'size', 'sha256'}, 'Release file')
        if (not isinstance(item['path'], str) or item['path'] in paths
                or type(item['size']) is not int or not 0 <= item['size'] <= MAX_PACKAGE
                or not isinstance(item['sha256'], str) or not VERSION.fullmatch(item['sha256'])):
            raise PublicationError('Invalid or duplicate release file metadata')
        paths.add(item['path'])
    name = package['name']
    if name == 'metadata.tar':
        expected = {'data/manifest.json', 'data/index.json.gz'}
    elif name.startswith('data-'):
        bucket = int(name[5:8])
        if bucket >= BUCKET_COUNT:
            raise PublicationError('Invalid release data bucket')
        expected = {f'data/{part}/{bucket:03d}.json.gz' for part in ('history', 'recent')}
    else:
        if len(paths) > 32 or any(not AVATAR.fullmatch(path) for path in paths):
            raise PublicationError('Invalid release avatar package scope')
        expected = paths
    if paths != expected:
        raise PublicationError('Release package has an incomplete or invalid file scope')


def validate_release(release, expected_version=None):
    object_fields(release, {'schema_version', 'publication_version', 'manifest', 'packages',
                           'file_count', 'avatar_files', 'recent_matches'}, 'Release')
    version = release['publication_version']
    if (release['schema_version'] != 'xinrui-pages-release-v1' or not isinstance(version, str)
            or not VERSION.fullmatch(version) or expected_version is not None and version != expected_version):
        raise PublicationError('Invalid or changed release publication version')
    validate_manifest(release['manifest'])
    if (not isinstance(release['packages'], list)
            or any(type(release[key]) is not int or release[key] < 0
                   for key in ('file_count', 'avatar_files', 'recent_matches'))):
        raise PublicationError('Invalid release counts')
    names, paths = set(), set()
    for package in release['packages']:
        validate_package(package)
        if package['name'] in names:
            raise PublicationError('Duplicate release package')
        names.add(package['name'])
        for item in package['files']:
            if item['path'] in paths:
                raise PublicationError('Duplicate release file')
            paths.add(item['path'])
    required = {'metadata.tar'} | {f'data-{bucket:03d}.tar' for bucket in range(BUCKET_COUNT)}
    if (not required <= names or len(paths) != release['file_count']
            or len([path for path in paths if AVATAR.fullmatch(path)]) != release['avatar_files']
            or release['file_count'] != 258 + release['avatar_files']
            or release['recent_matches'] != release['manifest']['stats']['valid_matches_30d']):
        raise PublicationError('Release package set or counts are incomplete')
    return release


class CloudPublicationClient:
    def __init__(self, site_url, token, *, service_token=None):
        parsed = urlparse(site_url)
        if (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost'))
                or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise PublicationError('Cloud Site URL must be a clean HTTPS URL')
        if not token:
            raise PublicationError('GITHUB_PUBLICATION_TOKEN is required')
        self.endpoint = site_url.rstrip('/') + '/api/admin/github-publication'
        self.opener = build_opener(NoRedirect())
        self.headers = {'X-Publication-Authorization': 'Bearer ' + token}
        # Platform private-Site access is separate from the application read-only token.
        if service_token:
            self.headers['OAI-Sites-Authorization'] = 'Bearer ' + service_token

    def _fetch(self, part, *, version=None, name=None, maximum=32 * 1024 * 1024):
        if part not in PUBLIC_PARTS:
            raise PublicationError('Only ready release downloads are supported')
        parameters = {'part': part}
        if version is not None:
            parameters['version'] = version
        if name is not None:
            parameters['name'] = name
        request = Request(self.endpoint + '?' + urlencode(parameters), headers=self.headers)
        for attempt in range(3):
            try:
                with self.opener.open(request, timeout=120) as response:
                    actual = response.headers.get('X-Publication-Version', '')
                    if not VERSION.fullmatch(actual) or version is not None and actual != version:
                        raise PublicationError('Successful publication changed during export')
                    expected_type = 'application/x-tar' if part == 'package' else 'application/json'
                    if response.headers.get('Content-Type', '').split(';', 1)[0].lower() != expected_type:
                        raise PublicationError('Cloud endpoint returned an incompatible release content type')
                    payload = response.read(maximum + 1)
                    if len(payload) > maximum:
                        raise PublicationError('Public export response is too large')
                    if part == 'package':
                        return payload, actual
                    data = json.loads(payload)
                    finite_values(data)
                    return data, actual
            except HTTPError as error:
                code, reason, database_code, resource_kind = safe_http_error(error)
                failure = CloudRequestError(part, None, error.code, code, reason, database_code, resource_kind)
                retryable = (failure.http_status == 503 and (
                    failure.safe_error_code == 'UNCLASSIFIED_UPSTREAM_ERROR' or
                    failure.safe_error_code == 'DATABASE_QUERY_UNAVAILABLE' and
                    ((failure.database_code == 'D1_ERROR' and
                      failure.database_reason in ('NETWORK_DISCONNECTED', 'DATABASE_BUSY', 'UNKNOWN')) or
                     (failure.database_reason, failure.database_code) == ('UNKNOWN', 'UNKNOWN'))))
                if not retryable or attempt == 2:
                    raise failure from None
                delay = (0.5, 1.0)[attempt]
                event = {'event': 'public_export_retry', 'part': failure.part,
                    'failed_attempt': attempt + 1, 'next_attempt': attempt + 2, 'total_attempts': 3,
                    'delay_seconds': delay, 'http_status': failure.http_status,
                    'safe_error_code': failure.safe_error_code}
                for key, value in (('bucket', failure.bucket), ('reason', failure.database_reason),
                                   ('code', failure.database_code), ('resource_kind', failure.resource_kind)):
                    if value is not None:
                        event[key] = value
                print(json.dumps(event), flush=True)
                time.sleep(delay)
            except URLError:
                raise CloudRequestError(part, None, None, 'NETWORK_UNAVAILABLE') from None
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise PublicationError('Cloud public export returned invalid JSON') from None

    def fetch_release(self, *, version=None):
        if version is not None and (not isinstance(version, str) or not VERSION.fullmatch(version)):
            raise PublicationError('Invalid publication version')
        release, actual = self._fetch('release', version=version)
        return validate_release(release, expected_version=actual)

    def fetch_package(self, package, *, version):
        validate_package(package)
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise PublicationError('Invalid publication version')
        payload, _ = self._fetch('package', version=version, name=package['name'], maximum=package['size'])
        return payload


def unpack_package(payload, package, staging, written):
    if (not isinstance(payload, bytes) or len(payload) != package['size']
            or hashlib.sha256(payload).hexdigest() != package['sha256']):
        raise PublicationError('Release package size or SHA-256 differs from its manifest')
    expected = {item['path']: item for item in package['files']}
    found, offset = set(), 0
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as archive:
            for member in archive:
                if (member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.pax_headers
                        or member.linkname or member.offset != offset
                        or payload[member.offset + 257:member.offset + 263] != b'ustar\0'
                        or member.name not in expected or member.name in found or member.name in written):
                    raise PublicationError('Release tar contains an invalid, linked or duplicate file')
                item = expected[member.name]
                if member.size != item['size']:
                    raise PublicationError('Release file size differs from its manifest')
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise PublicationError('Release file body is missing')
                with extracted:
                    data = extracted.read(item['size'] + 1)
                if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                    raise PublicationError('Release file SHA-256 differs from its manifest')
                destination = staging / member.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                found.add(member.name); written.add(member.name)
                offset = member.offset_data + ((member.size + 511) // 512) * 512
        if (found != set(expected) or len(payload) - offset < 1024
                or len(payload) % 512 or any(payload[offset:])):
            raise PublicationError('Release tar file set or ending is incomplete')
    except (tarfile.TarError, EOFError, OSError):
        raise PublicationError('Release tar could not be read completely') from None


def read_public_json(path, *, compressed=False):
    try:
        opener = gzip.open if compressed else open
        with opener(path, 'rb') as stream:
            payload = stream.read(32 * 1024 * 1024 + 1)
        if len(payload) > 32 * 1024 * 1024:
            raise PublicationError('Public JSON file is too large')
        value = json.loads(payload)
        finite_values(value)
        return value
    except (OSError, EOFError, UnicodeError, json.JSONDecodeError):
        raise PublicationError('Release contains an unreadable public JSON file') from None


def validate_downloaded_bundle(staging, release):
    manifest = read_public_json(staging / 'data/manifest.json')
    validate_manifest(manifest)
    if manifest != release['manifest']:
        raise PublicationError('Downloaded manifest differs from the ready release')
    index = read_public_json(staging / 'data/index.json.gz', compressed=True)
    object_fields(index, {'players', 'aliases', 'competitions'}, 'Public index')
    if (not isinstance(index['players'], list) or not isinstance(index['aliases'], dict)
            or not isinstance(index['competitions'], dict)):
        raise PublicationError('Public index fields have incompatible types')
    bucket_ids = {bucket: set() for bucket in range(BUCKET_COUNT)}
    avatars, all_ids = set(), set()
    for player in index['players']:
        object_fields(player, PLAYER_FIELDS, 'Public player')
        public_id(player['id']); public_name(player['nickname']); avatar_path(player['avatar'])
        bucket = int(player['id'], 16) % BUCKET_COUNT
        if (type(player['bucket']) is not int or player['bucket'] != bucket or player['id'] in all_ids
                or type(player['display_rating']) not in (int, float) or type(player['provisional']) is not bool):
            raise PublicationError('Public player bucket scope or ranking data is inconsistent')
        all_ids.add(player['id']); bucket_ids[bucket].add(player['id'])
        if player['avatar']:
            avatars.add(player['avatar'])
    if len(all_ids) != manifest['stats']['players']:
        raise PublicationError('Published player count differs from the release')
    expected_order = sorted(index['players'], key=lambda player: (-player['display_rating'], player['id']))
    if [player['id'] for player in index['players']] != [player['id'] for player in expected_order]:
        raise PublicationError('Public player ranking order is invalid')
    rank = 0
    for player in index['players']:
        if not player['provisional']:
            rank += 1
        if player['rank'] != (None if player['provisional'] else rank):
            raise PublicationError('Mature-player ranks differ from the published tie order')
    for identifier, names in index['aliases'].items():
        if identifier not in all_ids or not isinstance(names, list):
            raise PublicationError('Alias scope differs from the published players')
        for name in names:
            public_name(name)
    for identifier, event in index['competitions'].items():
        public_id(identifier); object_fields(event, {'title'}, 'Competition')
        if not isinstance(event['title'], str):
            raise PublicationError('Invalid public competition title')
    recent_count, recent_boards = 0, []
    for bucket in range(BUCKET_COUNT):
        for part in ('history', 'recent'):
            payload = read_public_json(staging / f'data/{part}/{bucket:03d}.json.gz', compressed=True)
            object_fields(payload, {'players'}, f'{part} bucket')
            if not isinstance(payload['players'], dict) or set(payload['players']) != bucket_ids[bucket]:
                raise PublicationError(f'{part} bucket differs from its published player scope')
            for records in payload['players'].values():
                if part == 'history':
                    object_fields(records, {'points'}, 'Player history')
                    if not isinstance(records['points'], list):
                        raise PublicationError('Player history points must be a list')
                    last = ''
                    for point in records['points']:
                        if (not isinstance(point, list) or len(point) != 3 or not isinstance(point[0], str)
                                or point[0] <= last or point[0] >= manifest['recent_end_exclusive']
                                or type(point[1]) not in (int, float)
                                or point[2] is not None and type(point[2]) not in (int, float)):
                            raise PublicationError('Historical point format, order or cutoff is invalid')
                        last = point[0]
                else:
                    if not isinstance(records, list):
                        raise PublicationError('Recent matches must be a list')
                    recent_count += len(records)
                    for match in records:
                        object_fields(match, MATCH_FIELDS, 'Recent match')
                        public_id(match['opponent_id']); public_id(match['competition_id']); avatar_path(match['opponent_avatar'])
                        if (match['opponent_id'] not in all_ids or match['competition_id'] not in index['competitions']
                                or not isinstance(match['day'], str)
                                or not manifest['recent_start'] <= match['day'] < manifest['recent_end_exclusive']):
                            raise PublicationError('Recent match scope or cutoff is invalid')
                        unit, scores = match['score_unit'], (match['score'], match['opponent_score'])
                        if (unit not in ('normalized_vp', 'imp')
                                or any(type(score) not in (int, float) or not math.isfinite(score) for score in scores)
                                or unit == 'normalized_vp' and any(not 0 <= score <= 20 for score in scores)
                                or unit == 'imp' and any(score != int(score) for score in scores)):
                            raise PublicationError('Recent match score or unit is invalid')
                        recent_boards.append(match['boards'])
                        if match['opponent_avatar']:
                            avatars.add(match['opponent_avatar'])
    if (recent_count != manifest['stats']['valid_matches_30d'] * 2
            or math.fsum(recent_boards) != manifest['stats']['boards_30d'] * 2
            or recent_count != release['recent_matches'] * 2):
        raise PublicationError('Recent ledger totals differ from the published statistics')
    delivered_avatars = {item['path'] for package in release['packages'] for item in package['files']
                         if AVATAR.fullmatch(item['path'])}
    if delivered_avatars != avatars:
        raise PublicationError('Release avatars differ from the index and recent-match references')


def build_bundle(client, source_dir, output_dir, *, workers=4):
    source, output = Path(source_dir).resolve(), Path(output_dir).resolve()
    if output.exists() or output == source or output in source.parents:
        raise PublicationError('Output must be a new directory, separate from the website sources')
    for name in FRONTEND_FILES:
        if not (source / name).is_file():
            raise PublicationError(f'Missing frontend source: {name}')
    release = validate_release(client.fetch_release())
    version = release['publication_version']
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cloud-rating-pages-', dir=output.parent) as directory:
        staging = Path(directory).resolve()
        for name in FRONTEND_FILES:
            shutil.copyfile(source / name, staging / name)
        (staging / '.nojekyll').write_text('', encoding='utf-8')
        progress = ExportProgress('release_packages', len(release['packages']))
        def download(package):
            return package, client.fetch_package(package, version=version)
        written = set()
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
            for package, payload in pool.map(download, release['packages']):
                unpack_package(payload, package, staging, written)
                progress.advance()
        if written != {item['path'] for package in release['packages'] for item in package['files']}:
            raise PublicationError('Downloaded release file set is incomplete')
        progress.finish()
        progress = ExportProgress('release_validation', 1)
        validate_downloaded_bundle(staging, release)
        progress.advance(); progress.finish()
        progress = ExportProgress('install_bundle', 1)
        shutil.copytree(staging, output)
        progress.advance(); progress.finish()
    return {'publication_version': version, 'players': release['manifest']['stats']['players'],
            'avatar_files': release['avatar_files'], 'recent_matches': release['recent_matches'], 'data_files': 258}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site-url', default=os.environ.get('CLOUD_SITE_URL'))
    parser.add_argument('--source-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'site')
    parser.add_argument('--output-dir', type=Path, default=Path('cloud-pages-output'))
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    try:
        client = CloudPublicationClient(args.site_url or '', os.environ.get('GITHUB_PUBLICATION_TOKEN'),
            service_token=os.environ.get('SITES_SERVICE_ACCESS_TOKEN'))
        print(json.dumps(build_bundle(client, args.source_dir, args.output_dir,
            workers=args.workers), ensure_ascii=False))
    except PublicationError as error:
        parser.exit(1, str(error) + '\n')
    except (OSError, ValueError, TypeError, KeyError):
        # Do not print upstream response bodies, headers, credentials or private SQL errors.
        parser.exit(1, 'Cloud public bundle was not completed; the previous GitHub deployment remains valid.\n')


if __name__ == '__main__':
    main()
