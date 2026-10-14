"""Build the existing GitHub Pages website from the cloud's read-only public export.

The private Sites website queries its database directly. These v3 files are only
the compatibility bundle for GitHub Pages; no local database or capture is read.
Credentials come from environment variables and are never written to the bundle.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

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
PUBLIC_PARTS = frozenset(('manifest', 'index', 'history', 'recent', 'avatar'))
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


class PublicationError(RuntimeError):
    pass


class CloudRequestError(PublicationError):
    def __init__(self, part, bucket, status, error_code):
        self.part = part if part in PUBLIC_PARTS else 'invalid'
        self.bucket = bucket if type(bucket) is int and 0 <= bucket < BUCKET_COUNT else None
        self.http_status = status if type(status) is int and 100 <= status < 600 else None
        self.safe_error_code = error_code if error_code in SAFE_ERROR_CODE_VALUES else 'UNCLASSIFIED_UPSTREAM_ERROR'
        label = f'part={self.part}'
        if self.bucket is not None:
            label += f' bucket={self.bucket:03d}'
        super().__init__(f'Cloud public export failed {label} http_status={self.http_status} safe_error_code={self.safe_error_code}')


def safe_http_error(error):
    code = 'UNCLASSIFIED_UPSTREAM_ERROR'
    try:
        payload = json.loads(error.read(8192))
        if isinstance(payload, dict) and isinstance(payload.get('error'), str):
            code = SAFE_ERROR_CODES.get(payload['error'], code)
    except (ValueError, OSError, TypeError):
        pass
    finally:
        error.close()
    return code


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


class CloudPublicationClient:
    def __init__(self, site_url, token, *, service_token=None):
        parsed = urlparse(site_url)
        if (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost'))
                or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise PublicationError('Cloud Site URL must be a clean HTTPS URL')
        if not token:
            raise PublicationError('GITHUB_PUBLICATION_TOKEN is required')
        self.endpoint = site_url.rstrip('/') + '/api/admin/github-publication'
        self.headers = {'X-Publication-Authorization': 'Bearer ' + token}
        # Platform private-Site access is separate from the application read-only token.
        if service_token:
            self.headers['OAI-Sites-Authorization'] = 'Bearer ' + service_token

    def fetch(self, part, *, version=None, bucket=None, player_id=None):
        parameters = {'part': part}
        if version is not None:
            parameters['version'] = version
        if bucket is not None:
            parameters['bucket'] = bucket
        if player_id is not None:
            parameters['id'] = player_id
        request = Request(self.endpoint + '?' + urlencode(parameters), headers=self.headers)
        try:
            with urlopen(request, timeout=120) as response:
                actual = response.headers.get('X-Publication-Version', '')
                if not VERSION.fullmatch(actual) or version is not None and actual != version:
                    raise PublicationError('Successful publication changed during export')
                payload = response.read(32 * 1024 * 1024 + 1)
                if len(payload) > 32 * 1024 * 1024:
                    raise PublicationError('Public export response is too large')
                if part == 'avatar':
                    return payload, actual, response.headers.get('X-Publication-Asset')
                if not response.headers.get('Content-Type', '').startswith('application/json'):
                    raise PublicationError('Cloud endpoint did not return a public JSON export')
                data = json.loads(payload)
                finite_values(data)
                return data, actual, None
        except HTTPError as error:
            code = safe_http_error(error)
            raise CloudRequestError(part, bucket, error.code, code) from None
        except URLError:
            raise CloudRequestError(part, bucket, None, 'NETWORK_UNAVAILABLE') from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PublicationError('Cloud public export returned invalid JSON') from None


def write_json(path, value, *, compressed=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    path.write_bytes(gzip.compress(payload, compresslevel=9, mtime=0) if compressed else payload)


def build_bundle(client, source_dir, output_dir, *, workers=4):
    source, output = Path(source_dir).resolve(), Path(output_dir).resolve()
    if output.exists() or output == source or output in source.parents:
        raise PublicationError('Output must be a new directory, separate from the website sources')
    for name in FRONTEND_FILES:
        if not (source / name).is_file():
            raise PublicationError(f'Missing frontend source: {name}')
    progress = ExportProgress('manifest', 1)
    manifest, version, _ = client.fetch('manifest')
    validate_manifest(manifest)
    if not VERSION.fullmatch(version):
        raise PublicationError('Invalid publication version')
    progress.advance()
    progress.finish()
    index = {'players': [], 'aliases': {}, 'competitions': {}}
    bucket_ids = {}
    avatar_owners = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cloud-rating-pages-', dir=output.parent) as directory:
        staging = Path(directory).resolve()
        for name in FRONTEND_FILES:
            shutil.copyfile(source / name, staging / name)
        (staging / '.nojekyll').write_text('', encoding='utf-8')
        (staging / 'avatars').mkdir()
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
            progress = ExportProgress('index_buckets', BUCKET_COUNT)
            def index_bucket(bucket):
                payload, actual, _ = client.fetch('index', version=version, bucket=bucket)
                if actual != version:
                    raise PublicationError('Successful publication changed during export')
                object_fields(payload, {'players', 'aliases', 'competitions'}, 'Index bucket')
                return bucket, payload
            for bucket, payload in pool.map(index_bucket, range(BUCKET_COUNT)):
                ids = set()
                for player in payload['players']:
                    object_fields(player, PLAYER_FIELDS, 'Public player')
                    public_id(player['id']); public_name(player['nickname']); avatar_path(player['avatar'])
                    if int(player['id'], 16) % BUCKET_COUNT != bucket or player['bucket'] != bucket or player['id'] in ids:
                        raise PublicationError('Public player bucket scope is inconsistent')
                    ids.add(player['id']); index['players'].append(player)
                    if player['avatar']:
                        avatar_owners.setdefault(player['avatar'], player['id'])
                bucket_ids[bucket] = ids
                for identifier, names in payload['aliases'].items():
                    if identifier not in ids or identifier in index['aliases'] or not isinstance(names, list):
                        raise PublicationError('Alias scope differs from its public bucket')
                    for name in names:
                        public_name(name)
                    index['aliases'][identifier] = names
                for identifier, event in payload['competitions'].items():
                    public_id(identifier); object_fields(event, {'title'}, 'Competition')
                    if not isinstance(event['title'], str):
                        raise PublicationError('Invalid public competition title')
                    if identifier in index['competitions'] and index['competitions'][identifier] != event:
                        raise PublicationError('Competition title changed during export')
                    index['competitions'][identifier] = event
                progress.advance()
            all_ids = {p['id'] for p in index['players']}
            if len(index['players']) != len(all_ids) or len(all_ids) != manifest['stats']['players']:
                raise PublicationError('Published player count differs from export')
            index['players'].sort(key=lambda p: (-p['display_rating'], p['id']))
            rank = 0
            for player in index['players']:
                if not player['provisional']:
                    rank += 1
                expected = None if player['provisional'] else rank
                if player['rank'] != expected:
                    raise PublicationError('Mature-player ranks differ from the published tie order')
            index['aliases'] = dict(sorted(index['aliases'].items()))
            index['competitions'] = dict(sorted(index['competitions'].items()))
            progress.finish()
            recent_count = 0
            recent_boards = []
            progress = ExportProgress('history_recent_buckets', BUCKET_COUNT * 2)
            def data_bucket(item):
                part, bucket = item
                payload, actual, _ = client.fetch(part, version=version, bucket=bucket)
                if actual != version:
                    raise PublicationError('Successful publication changed during export')
                object_fields(payload, {'players'}, f'{part} bucket')
                if set(payload['players']) != bucket_ids[bucket]:
                    raise PublicationError(f'{part} bucket differs from its published player scope')
                return part, bucket, payload
            for part, bucket, payload in pool.map(data_bucket, ((part, bucket) for bucket in range(BUCKET_COUNT) for part in ('history', 'recent'))):
                for identifier, records in payload['players'].items():
                    if part == 'history':
                        object_fields(records, {'points'}, 'Player history')
                        last = ''
                        for point in records['points']:
                            if (not isinstance(point, list) or len(point) != 3 or not isinstance(point[0], str)
                                    or point[0] <= last or point[0] >= manifest['recent_end_exclusive']):
                                raise PublicationError('Historical point order or cutoff is invalid')
                            last = point[0]
                    else:
                        if not isinstance(records, list):
                            raise PublicationError('Recent matches must be a list')
                        recent_count += len(records)
                        for match in records:
                            object_fields(match, MATCH_FIELDS, 'Recent match')
                            public_id(match['opponent_id']); public_id(match['competition_id']); avatar_path(match['opponent_avatar'])
                            if (match['opponent_id'] not in all_ids or match['competition_id'] not in index['competitions']
                                    or not manifest['recent_start'] <= match['day'] < manifest['recent_end_exclusive']
                                    or match['score_unit'] != 'normalized_vp'):
                                raise PublicationError('Recent match scope or cutoff is invalid')
                            recent_boards.append(match['boards'])
                            if match['opponent_avatar']:
                                avatar_owners.setdefault(match['opponent_avatar'], match['opponent_id'])
                payload['players'] = dict(sorted(payload['players'].items()))
                write_json(staging / f'data/{part}/{bucket:03d}.json.gz', payload, compressed=True)
                progress.advance()
            if recent_count != manifest['stats']['valid_matches_30d'] * 2 or math.fsum(recent_boards) != manifest['stats']['boards_30d'] * 2:
                raise PublicationError('Recent ledger totals differ from the published statistics')
            progress.finish()
            def download_avatar(item):
                asset, identifier = item
                payload, actual, path = client.fetch('avatar', version=version, player_id=identifier)
                if actual != version or path != asset:
                    raise PublicationError('Avatar assignment changed during export')
                (staging / asset).write_bytes(payload)
            progress = ExportProgress('avatars', len(avatar_owners))
            for _ in pool.map(download_avatar, avatar_owners.items()):
                progress.advance()
            progress.finish()
        progress = ExportProgress('final_manifest_validation', 1)
        final, actual, _ = client.fetch('manifest', version=version)
        if actual != version or final != manifest:
            raise PublicationError('Successful publication changed during export')
        progress.advance()
        progress.finish()
        write_json(staging / 'data/manifest.json', manifest)
        write_json(staging / 'data/index.json.gz', index, compressed=True)
        # The output is installed only after every export and the final version
        # check succeeds. GitHub uploads this directory only when this function
        # returns successfully; a file-copy failure never deploys an artifact.
        progress = ExportProgress('install_bundle', 1)
        shutil.copytree(staging, output)
        progress.advance()
        progress.finish()
    return {'publication_version': version, 'players': len(index['players']), 'avatar_files': len(avatar_owners),
            'recent_matches': recent_count // 2, 'data_files': 2 + BUCKET_COUNT * 2}


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
        print(json.dumps(build_bundle(client, args.source_dir, args.output_dir, workers=args.workers), ensure_ascii=False))
    except PublicationError as error:
        parser.exit(1, str(error) + '\n')
    except (OSError, ValueError, TypeError, KeyError):
        # Do not print upstream response bodies, headers, credentials or private SQL errors.
        parser.exit(1, 'Cloud public bundle was not completed; the previous GitHub deployment remains valid.\n')


if __name__ == '__main__':
    main()
