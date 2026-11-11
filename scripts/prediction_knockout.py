"""Exact fixed-bracket monthly forecasts. Private strengths stay in memory.

Uses the existing fitted, rounded normal IMP model, splitting a tied IMP
margin equally between the two players. Strengths remain fixed. Dynamic
programming sums all possible opponents; no random draws are involved.
"""
from __future__ import annotations

import math

from prediction_public import AVATAR, EDITION, PUBLIC_ID, finite, public_identity
from prediction_simulation import ALPHA, SIGMA

SCHEMA = "xinrui-knockout-predictions-v1"
PROBABILITIES = ("next_round_probability", "champion_probability", "runner_up_probability", "bronze_probability")


def win_probability(left, right, boards):
    """P(rounded IMP > 0) + P(rounded IMP == 0)/2, analytically."""
    if boards not in (8, 12, 16) or not finite(left) or not finite(right):
        raise ValueError("prediction_knockout_model_input_invalid")
    mean = ALPHA * boards * (left - right) / 100
    scale = SIGMA * math.sqrt(2 * boards)
    return .25 * (math.erfc((.5 - mean) / scale) + math.erfc((-.5 - mean) / scale))


def calculate_bracket(players, opening, *, current_round=1, completed_matches=(), boards=16):
    """Fold outermost tables together at each round, including a bronze match.

    ``opening`` is the complete first round: {table, uid1, uid2}. Historical
    results must cover exactly the rounds before ``current_round``. In the
    last round, semifinal losers are still active in the bronze match.
    """
    people = {p['uid']: p for p in players}
    count = len(people)
    if count != len(players) or count < 4 or count & (count - 1):
        raise ValueError("prediction_knockout_power_of_two_required")
    total = count.bit_length() - 1
    if type(current_round) is not int or not 1 <= current_round <= total:
        raise ValueError("prediction_knockout_current_round_invalid")
    if isinstance(boards, int):
        boards = [boards] * total
    if len(boards) != total or any(type(n) is not int or n not in (8, 12, 16) for n in boards):
        raise ValueError("prediction_knockout_boards_invalid")
    ordered = sorted(opening, key=lambda m: m['table'])
    entrants = [m[key] for m in ordered for key in ('uid1', 'uid2')]
    if ([m['table'] for m in ordered] != list(range(1, count // 2 + 1))
            or len(entrants) != count or len(set(entrants)) != count or set(entrants) != set(people)):
        raise ValueError("prediction_knockout_opening_incomplete")
    known = {(m['round'], m['table']): m['winner_uid'] for m in completed_matches}
    expected = {(r, t) for r in range(1, current_round) for t in range(1, (count >> r) + 1)}
    if len(known) != len(completed_matches) or set(known) != expected:
        raise ValueError("prediction_knockout_results_prefix_required")
    probabilities = {}

    def chance(a, b, turn):
        key = (a, b, turn)
        if key not in probabilities:
            p = win_probability(people[a]['rating'], people[b]['rating'], boards[turn - 1])
            probabilities[key] = p
            probabilities[b, a, turn] = 1 - p
        return probabilities[key]

    def play(left, right, turn, table, *, bronze=False):
        winners, losers = {}, {}
        fixed = known.get((turn, table)) if not bronze else None
        if fixed is not None and (len(left) != 1 or len(right) != 1 or fixed not in left and fixed not in right):
            raise ValueError("prediction_knockout_winner_conflicts_with_bracket")
        for a, pa in left.items():
            for b, pb in right.items():
                p = float(fixed == a) if fixed is not None else chance(a, b, turn)
                weight = pa * pb
                winners[a] = winners.get(a, 0) + weight * p
                winners[b] = winners.get(b, 0) + weight * (1 - p)
                losers[a] = losers.get(a, 0) + weight * (1 - p)
                losers[b] = losers.get(b, 0) + weight * p
        return ({uid: p for uid, p in winners.items() if p > 0},
                {uid: p for uid, p in losers.items() if p > 0})

    matches = [({m['uid1']: 1.}, {m['uid2']: 1.}) for m in ordered]
    alive, next_round, semi_losers = set(), {}, None
    for turn in range(1, total + 1):
        if turn == current_round:
            alive = {uid for pair in matches for side in pair for uid in side}
            if turn == total:
                alive.update(uid for side in semi_losers for uid in side)
        outcomes = [play(a, b, turn, t) for t, (a, b) in enumerate(matches, 1)]
        if turn == current_round and turn < total:
            next_round = {uid: p for win, _ in outcomes for uid, p in win.items()}
        if turn == total - 1:
            semi_losers = [lose for _, lose in outcomes]
        if turn == total:
            champions, runners = outcomes[0]
            bronze, _ = play(*semi_losers, turn, 2, bronze=True)
            break
        winners = [win for win, _ in outcomes]
        matches = [(winners[i], winners[-1 - i]) for i in range(len(winners) // 2)]
    return {uid: {'next_round_probability': next_round.get(uid, 0.) if current_round < total else None,
                  'champion_probability': champions.get(uid, 0.),
                  'runner_up_probability': runners.get(uid, 0.), 'bronze_probability': bronze.get(uid, 0.)}
            for uid in entrants if uid in alive}


def forecast(module, bundle, spec=None, *, generated_at, **_options):
    if module.get('family') != 'monthly' or module.get('format') != 'fixed_knockout':
        raise ValueError('prediction_knockout_format_unsupported')
    result = {"schema_version": SCHEMA, "event": module['family'], "edition": module['edition'],
              "status": "forecast", "method": "exact_bracket_dp",
              **{key: bundle[key] for key in ('publication_version', 'snapshot_id', 'rating_cutoff', 'rating_generated_at')},
              "generated_at": generated_at, "groups": [], "players": []}
    for group in module['groups']:
        outcomes = calculate_bracket(group['players'], group['opening_pairings'],
            current_round=group['current_round'], completed_matches=group.get('completed_matches', []), boards=group['boards'])
        total = len(group['players']).bit_length() - 1
        result['groups'].append({'id': group['id'], 'name': group['name'], 'count': len(outcomes),
            'current_round': group['current_round'], 'total_rounds': total})
        for person in group['players']:
            if person['uid'] not in outcomes:
                continue
            result['players'].append({**public_identity(person, group['id']),
                **{key: person.get(key) for key in ('avatar', 'display_rating', 'provisional', 'rating_tier')},
                **outcomes[person['uid']]})
    return validate_result(result)


def validate_result(value):
    top = {'schema_version', 'event', 'edition', 'status', 'method', 'publication_version', 'snapshot_id',
           'rating_cutoff', 'rating_generated_at', 'generated_at', 'groups', 'players'}
    person_fields = {'id', 'nickname', 'group', 'avatar', 'display_rating', 'provisional', 'rating_tier', *PROBABILITIES}
    if (set(value) != top or value['schema_version'] != SCHEMA or value['event'] != 'monthly'
            or value['status'] != 'forecast' or value['method'] != 'exact_bracket_dp'
            or not EDITION.fullmatch(value['edition'])):
        raise ValueError('prediction_knockout_public_schema_invalid')
    import re
    if any(not re.fullmatch('[a-f0-9]{64}', value[key]) for key in ('publication_version', 'snapshot_id')):
        raise ValueError('prediction_knockout_public_identity_invalid')
    if len({p['id'] for p in value['players']}) != len(value['players']):
        raise ValueError('prediction_knockout_public_duplicate')
    groups = value['groups']
    if not groups or len({g['id'] for g in groups}) != len(groups):
        raise ValueError('prediction_knockout_public_groups_invalid')
    for g in groups:
        turn, total = g['current_round'], g['total_rounds']
        if type(turn) is not int or type(total) is not int or not 1 <= turn <= total or total < 2:
            raise ValueError('prediction_knockout_public_progress_invalid')
        cohort = [p for p in value['players'] if p['group'] == g['id']]
        expected = 4 if turn == total else 2 ** (total - turn + 1)
        if g['count'] != len(cohort) or len(cohort) != expected:
            raise ValueError('prediction_knockout_public_survivors_invalid')
        for field in PROBABILITIES:
            if field == 'next_round_probability' and turn == total:
                if any(p[field] is not None for p in cohort):
                    raise ValueError('prediction_knockout_no_next_round')
                continue
            quota = len(cohort) / 2 if field == 'next_round_probability' else 1
            if not math.isclose(sum(p[field] for p in cohort), quota, abs_tol=1e-9):
                raise ValueError('prediction_knockout_public_quota_invalid')
    for p in value['players']:
        if (set(p) != person_fields or not PUBLIC_ID.fullmatch(p['id'])
                or public_identity(p, p['group'])['nickname'] != p['nickname']
                or p['group'] not in {g['id'] for g in groups} or not finite(p['display_rating'])
                or type(p['provisional']) is not bool
                or p['avatar'] is not None and not AVATAR.fullmatch(p['avatar'])):
            raise ValueError('prediction_knockout_public_player_invalid')
        for field in PROBABILITIES:
            if p[field] is None and field == 'next_round_probability':
                continue
            if not finite(p[field]) or not 0 <= p[field] <= 1:
                raise ValueError('prediction_knockout_public_probability_invalid')
        if sum(p[field] for field in PROBABILITIES[1:]) > 1 + 1e-10:
            raise ValueError('prediction_knockout_public_medals_overlap')
    return value
