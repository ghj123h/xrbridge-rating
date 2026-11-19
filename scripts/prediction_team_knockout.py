"""Forecast the two team knockout events from their accepted draw and ratings.

Published fixtures and settled winners take precedence over projected fixtures.
For later unpublished rounds the preceding edition of both events paired the
outermost winning tables inwards. All 128 knockout fixtures in that edition
used 16 boards, the model default when this edition has no specified length.
"""
from __future__ import annotations

import hashlib
import math

import numpy as np

from prediction_public import finite, mask_nickname
from prediction_simulation import ALPHA, SIGMA, _positive_integer, _vp_from_margin
from prediction_team_provider import public_member
from prediction_team_swiss import team_round_scores

SCHEMA = 'xinrui-team-knockout-predictions-v1'
ITERATIONS = 10000


def _ordered_draw(fixtures, eligible):
    """Validate unique opponents and the table order used for future feeders."""
    ordered = sorted(fixtures, key=lambda match: match['table'])
    teams = [match[key] for match in ordered for key in ('team1_id', 'team2_id')]
    if (len(teams) != len(eligible) or set(teams) != set(eligible)
            or [m['table'] for m in ordered] != list(range(1, len(ordered) + 1))):
        raise ValueError('prediction_team_knockout_draw_incomplete')
    return ordered


def _fold(winners):
    half = len(winners) // 2
    return list(zip(winners[:half], reversed(winners[half:])))


def _current_draw(group, ids, current, total):
    opening = _ordered_draw(group['opening_pairings'], ids)
    history = group.get('completed_matches', [])
    if any(m['round'] not in range(1, current) for m in history):
        raise ValueError('prediction_team_knockout_result_outside_prefix')
    winners, losers = list(ids), []
    for turn in range(1, current):
        played = _ordered_draw([m for m in history if m['round'] == turn], winners)
        if turn == 1 and any({m['team1_id'], m['team2_id']} != {o['team1_id'], o['team2_id']}
                             for m, o in zip(played, opening)):
            raise ValueError('prediction_team_knockout_opening_conflict')
        winners, losers = [], []
        for match in played:
            left, right, winner = match['team1_id'], match['team2_id'], match['winner_team_id']
            if winner not in (left, right):
                raise ValueError('prediction_team_knockout_winner_unknown')
            winners.append(winner)
            losers.append(right if winner == left else left)
    official = group.get('current_pairings') or (opening if current == 1 else [])
    if current == total:
        eligible = winners + losers
        projected = [tuple(winners), tuple(losers)]
    else:
        eligible = winners
        projected = _fold(winners) if current > 1 else []
    if official:
        draw = _ordered_draw(official, eligible)
        if current == 1 and any({m['team1_id'], m['team2_id']} != {o['team1_id'], o['team2_id']}
                                for m, o in zip(draw, opening)):
            raise ValueError('prediction_team_knockout_opening_conflict')
        if current == total:
            # A championship is identified by its semifinal winners, including
            # when the organiser lists the bronze match at table one.
            draw.sort(key=lambda m: m['team1_id'] not in winners)
            if {draw[0]['team1_id'], draw[0]['team2_id']} != set(winners):
                raise ValueError('prediction_team_knockout_finalists_conflict')
        return draw, True
    return [{'table': None, 'team1_id': left, 'team2_id': right} for left, right in projected], False


def _members(teams, size):
    strengths, scores, seats = [], [], []
    seen = set()
    for team in teams:
        members = team['members']
        if len(members) != size:
            raise ValueError('prediction_team_knockout_roster_incomplete')
        team_seats = {}
        for n, member in enumerate(members):
            uid = _positive_integer(member['uid'], 'uid')
            if (uid in seen or not finite(member['rating']) or not finite(member['current_vp'])
                    or member['current_vp'] < 0):
                raise ValueError('prediction_team_knockout_member_invalid')
            team_seats[uid] = n
            seen.add(uid)
        # As in the fitted Swiss model, pre-match raw ratings are fixed.
        # Adding rating-deviation noise here would change that calibration.
        strengths.append([m['rating'] for m in members])
        scores.append([round(m['current_vp'] * 100) for m in members])
        seats.append(team_seats)
    return np.array(strengths), np.array(scores), seats


def _official_seats(draw, index, seats):
    result = {}
    for n, match in enumerate(draw):
        if not match.get('seats'):
            continue
        fixed = []
        for side in (1, 2):
            member_index = seats[index[match[f'team{side}_id']]]
            order = [p[f'uid{side}'] for p in match['seats']]
            if len(order) != len(member_index) or set(order) != set(member_index):
                raise ValueError('prediction_team_knockout_seats_incomplete')
            fixed.append([member_index[uid] for uid in order])
        result[n] = fixed
    return result


def simulate_bracket(group, *, team_size, iterations=ITERATIONS, seed=0):
    teams = group['teams']
    ids = [_positive_integer(t['team_id'], 'team_id') for t in teams]
    count = len(teams)
    total, current = group['total_rounds'], group['current_round']
    if (type(total) is not int or not 2 <= total <= 8 or count != 2 ** total
            or type(current) is not int or not 1 <= current <= total or len(set(ids)) != count
            or team_size not in (2, 4) or group['team_size'] != team_size
            or group['completed_rounds'] != current - 1):
        raise ValueError('prediction_team_knockout_progress_invalid')
    boards = group.get('boards')
    if boards is None:
        boards = 16
    boards = _positive_integer(boards, 'boards')
    iterations = _positive_integer(iterations, 'iterations')
    draw, official = _current_draw(group, ids, current, total)
    index = {identifier: n for n, identifier in enumerate(ids)}
    strengths, start_vp, seats = _members(teams, team_size)
    fixed_seats = _official_seats(draw, index, seats)
    first_left = [index[m['team1_id']] for m in draw]
    first_right = [index[m['team2_id']] for m in draw]
    active = first_left + first_right
    advancing = np.zeros((count, total - 1), dtype=np.int64)
    advancing[active, :current - 1] = iterations
    if current == total:
        # Semifinal losers still play for bronze but did not reach the final.
        advancing[[first_left[1], first_right[1]], -1] = 0
    medals = np.zeros((count, 3), dtype=np.int64)
    rng = np.random.default_rng(seed)
    for start in range(0, iterations, 256):
        batch = min(256, iterations - start)
        rows = np.arange(batch)[:, None, None]
        personal = np.broadcast_to(start_vp, (batch, count, team_size)).copy()
        left = np.broadcast_to(first_left, (batch, len(first_left)))
        right = np.broadcast_to(first_right, left.shape)
        for turn in range(current, total + 1):
            a, b = left[..., None], right[..., None]
            left_vp, right_vp = personal[rows, a, np.arange(team_size)], personal[rows, b, np.arange(team_size)]
            left_seats = np.lexsort((rng.random(left_vp.shape), -left_vp), axis=2)
            right_seats = np.lexsort((rng.random(right_vp.shape), -right_vp), axis=2)
            if turn == current:
                for table, (one, two) in fixed_seats.items():
                    left_seats[:, table], right_seats[:, table] = one, two
            delta = strengths[a, left_seats] - strengths[b, right_seats]
            margins = np.rint(rng.normal(ALPHA * boards * delta / 100, SIGMA * math.sqrt(boards)))
            vp = np.rint(100 * _vp_from_margin(margins, boards)).astype(np.int64)
            board_left, board_right, _, _ = team_round_scores(vp)
            vp_margin = vp.sum(axis=2) - 1000 * team_size
            # Total VP breaks a board-point tie. Raw TP and finish times do
            # not exist for simulated play; split any remaining tie evenly.
            win = (board_left > board_right) | ((board_left == board_right)
                   & ((vp_margin > 0) | ((vp_margin == 0) & (rng.random(left.shape) < .5))))
            winners, losers = np.where(win, left, right), np.where(win, right, left)
            if turn == total:
                np.add.at(medals[:, 0], winners[:, 0], 1)
                np.add.at(medals[:, 1], losers[:, 0], 1)
                np.add.at(medals[:, 2], winners[:, 1], 1)
                break
            np.add.at(advancing[:, turn - 1], winners, 1)
            personal[rows, a, left_seats] += vp
            personal[rows, b, right_seats] += 2000 - vp
            if turn == total - 1:
                left = np.column_stack((winners[:, 0], losers[:, 0]))
                right = np.column_stack((winners[:, 1], losers[:, 1]))
            else:
                half = winners.shape[1] // 2
                left, right = winners[:, :half], winners[:, :half - 1:-1]
    outcomes = {}
    for match in draw:
        for team, opponent in ((match['team1_id'], match['team2_id']), (match['team2_id'], match['team1_id'])):
            n = index[team]
            probabilities = (advancing[n] / iterations).tolist()
            outcomes[team] = {'table': match['table'], 'opponent': opponent if official else None,
                'advancement_probabilities': probabilities,
                'next_round_probability': probabilities[current - 1] if current < total else None,
                **dict(zip(('champion_probability', 'runner_up_probability', 'bronze_probability'),
                           (medals[n] / iterations).tolist()))}
    return outcomes


def forecast(module, bundle, spec, *, generated_at, **_options):
    if (module.get('family') not in ('team', 'pair') or module.get('stage') != 'knockout'
            or module.get('format') != spec['format'] or module.get('ready') is not True):
        raise ValueError('prediction_team_knockout_verified_input_required')
    family, edition = module['family'], module['edition']
    result = {'schema_version': SCHEMA, 'event': family, 'edition': edition,
              'status': 'forecast', 'method': 'fixed_bracket_monte_carlo', 'iterations': ITERATIONS,
              **{k: bundle[k] for k in ('publication_version', 'snapshot_id', 'rating_cutoff', 'rating_generated_at')},
              'generated_at': generated_at, 'groups': [], 'teams': []}
    for group in module['groups']:
        if group['completed_rounds'] == group['total_rounds']:
            continue
        gid = group['id']
        identity = f'{family}:{edition}:{gid}'
        seed = hashlib.sha256(f"{bundle['publication_version']}:{bundle['snapshot_id']}:{identity}:knockout".encode()).digest()
        forecasts = simulate_bracket(group, team_size=spec['team_size'], seed=int.from_bytes(seed[:8], 'big'))
        public_ids = {team: hashlib.sha256(f'{identity}:{team}'.encode()).hexdigest()[:24] for team in forecasts}
        result['groups'].append({'id': gid, 'name': group['name'], 'count': len(forecasts),
            'team_size': spec['team_size'], 'current_round': group['current_round'], 'total_rounds': group['total_rounds']})
        for team in group['teams']:
            tid = team['team_id']
            if tid in forecasts:
                row = forecasts[tid].copy()
                opponent = row.pop('opponent')
                result['teams'].append({'id': public_ids[tid], 'name': mask_nickname(team['name']), 'group': gid,
                    'members': [public_member(m) for m in team['members']],
                    'opponent_id': public_ids.get(opponent), **row})
    if not result['groups']:
        raise ValueError('prediction_team_knockout_groups_missing')
    return result
