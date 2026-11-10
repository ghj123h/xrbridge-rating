"""Team Swiss forecasts from explicit, cutoff-bound standings, in memory only.

Each member plays an independent robot PK against the opposing member in the
same seat. Seats follow simulated personal VP (masterpoints before round one).
Team standings use match points, then board points, then total personal VP.
The distribution is shared with the existing validated individual predictor.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from prediction_simulation import (
    ALPHA, SIGMA, MODEL_NAME, MODEL_SOURCE, _positive_integer, _repair_repeats,
    _vp_from_margin,
)


def _number(value, label, *, maximum=None):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be a finite nonnegative number")
    result = float(value)
    if not math.isfinite(result) or result < 0 or maximum is not None and result > maximum:
        raise ValueError(f"{label} must be a finite nonnegative number")
    return result


def _order(match_points, board_points, vp, rng):
    # Integer hundredths avoid floating-point noise changing the VP tie-break.
    return np.lexsort((rng.random(vp.shape), -vp, -board_points, -match_points), axis=1)


def team_round_scores(personal_vps):
    """Return doubled board points and match points for both sides of each PK set.

    Scores have shape (..., team_size). Doubled board points encode wins/draws
    exactly as 2/1/0; match points are 2/1/0 for a team win/draw/loss.
    """
    vp = np.asarray(personal_vps)
    board_left = np.sum(np.where(vp > 1000, 2, np.where(vp == 1000, 1, 0)), axis=-1)
    board_right = 2 * vp.shape[-1] - board_left
    match_left = np.where(board_left > board_right, 2, np.where(board_left == board_right, 1, 0))
    return board_left, board_right, match_left, 2 - match_left


def simulate_team_group(teams, rounds, boards, promote, stay, *, team_size,
                        qualify=32, completed_rounds=0, iterations=10000, seed=20261109,
                        batch_size=256, prior_opponents=None, pairings=None,
                        final_ranks=None):
    """Simulate only remaining Swiss rounds; never simulate the knockout stage.

    ``teams`` contain team_id, match_points, board_points, total_vp, bye_count
    and exactly team_size members. Members supply uid, rating, current_vp and
    (before round one) masterpoints. All scores are from the accepted cutoff.
    Quotas are rank cutoffs: promotion first, then retention, then relegation;
    a field smaller than a cutoff naturally has fewer effective slots.
    ``prior_opponents`` is a symmetric team_id -> team_id-list mapping. Known
    first future pairings can be supplied; otherwise all pairings approximate
    adjacent standings. Only wholly tied rankings/seats use a random order.
    """
    rounds = _positive_integer(rounds, "rounds", allow_zero=True)
    completed = _positive_integer(completed_rounds, "completed_rounds", allow_zero=True)
    iterations = _positive_integer(iterations, "iterations")
    batch_size = _positive_integer(batch_size, "batch_size")
    promote = _positive_integer(promote, "promote", allow_zero=True)
    stay = _positive_integer(stay, "stay", allow_zero=True)
    qualify = _positive_integer(qualify, "qualify", allow_zero=True)
    if type(team_size) is not int or team_size not in (2, 4):
        raise ValueError("team_size must be 2 or 4")
    if type(boards) is not int or boards not in (8, 12, 16):
        raise ValueError("boards must be 8, 12 or 16")
    teams = list(teams)
    if not teams:
        raise ValueError("A nonempty team field is required")
    ids = [_positive_integer(team["team_id"], "team_id") for team in teams]
    if len(set(ids)) != len(ids):
        raise ValueError("Team identifiers must be unique")
    count, index = len(teams), {identifier: n for n, identifier in enumerate(ids)}
    start_match, start_board, start_vp, start_byes, strengths, member_vps, masterpoints = [], [], [], [], [], [], []
    all_uids = set()
    for team in teams:
        mp = _number(team["match_points"], "match_points", maximum=2 * completed)
        bp = _number(team["board_points"], "board_points", maximum=team_size * completed)
        tvp = _number(team["total_vp"], "total_vp", maximum=20 * team_size * completed)
        if mp != int(mp) or bp * 2 != int(bp * 2) or not math.isclose(tvp * 100, round(tvp * 100), abs_tol=1e-7):
            raise ValueError("Team points must use integer match points, half board points and hundredth VP")
        bye_count = _positive_integer(team["bye_count"], "bye_count", allow_zero=True)
        if bye_count > completed:
            raise ValueError("bye_count exceeds the completed prefix")
        members = team["members"]
        if len(members) != team_size:
            raise ValueError("Every team must have its complete member roster")
        ratings, personal, masters = [], [], []
        for person in members:
            uid = _positive_integer(person["uid"], "uid")
            if uid in all_uids:
                raise ValueError("A member cannot occur twice or belong to two teams")
            all_uids.add(uid)
            rating = float(person["rating"])
            if isinstance(person["rating"], bool) or not math.isfinite(rating):
                raise ValueError("Every member needs a finite raw rating")
            score = _number(person["current_vp"], "member current_vp", maximum=20 * completed)
            if not math.isclose(score * 100, round(score * 100), abs_tol=1e-7):
                raise ValueError("Personal VP must use hundredths")
            ratings.append(rating)
            personal.append(round(score * 100))
            if completed == 0 and 'opening_seat' in person:
                seat = _positive_integer(person['opening_seat'], 'opening_seat')
                if seat > team_size:
                    raise ValueError('opening_seat exceeds team size')
                masters.append(team_size + 1 - seat)
            elif completed == 0 and pairings and count % 2 and team['team_id'] not in {
                    identifier for pair in pairings for identifier in (pair.values() if isinstance(pair, Mapping) else pair)}:
                masters.append(0)  # The known bye has no opposing first-round seats.
            else:
                masters.append(_number(person['masterpoints'], 'masterpoints') if completed == 0 else 0)
        # Personal and team scores need not be equal: official penalties may
        # affect the team total. Neither is reconstructed from retained rows.
        start_match.append(int(mp)); start_board.append(round(bp * 2)); start_vp.append(round(tvp * 100))
        start_byes.append(bye_count); strengths.append(ratings); member_vps.append(personal); masterpoints.append(masters)
    strengths, member_vps, masterpoints = np.asarray(strengths), np.asarray(member_vps), np.asarray(masterpoints)
    if prior_opponents is None:
        prior_opponents = {identifier: [] for identifier in ids}
    if not isinstance(prior_opponents, Mapping) or set(prior_opponents) != set(ids):
        raise ValueError("prior_opponents must cover exactly the active teams")
    past = []
    for identifier in ids:
        opponents = list(prior_opponents[identifier])
        if (len(opponents) > completed or len(set(opponents)) != len(opponents) or identifier in opponents
                or any(other not in index or identifier not in prior_opponents[other] for other in opponents)):
            raise ValueError("Prior team opponents must be distinct, symmetric active teams")
        if count > 1 and len(opponents) + rounds > count - 1 + count % 2:
            raise ValueError("Too many future rounds to avoid repeat team opponents")
        past.append([index[other] for other in opponents])
    past_rounds = max(map(len, past), default=0)
    initial_history = np.full((count, past_rounds), -1, dtype=np.int32)
    for n, opponents in enumerate(past):
        initial_history[n, :len(opponents)] = opponents
    first_left, first_right, seen = [], [], set()
    for left, right in pairings or []:
        if left not in index or right not in index or left == right or left in seen or right in seen:
            raise ValueError("Known next-round team pairings must be disjoint active teams")
        if right in prior_opponents[left]:
            raise ValueError("Known next-round pairing repeats a prior opponent")
        seen.update((left, right)); first_left.append(index[left]); first_right.append(index[right])
    fixed = pairings is not None
    if fixed and (not rounds or len(seen) != count - count % 2):
        raise ValueError("Known next-round pairings must cover the active field")
    first_bye = next((index[identifier] for identifier in ids if identifier not in seen), None)
    if final_ranks is not None:
        if (rounds or not isinstance(final_ranks, Mapping) or set(final_ranks) != set(ids)
                or any(type(rank) is not int for rank in final_ranks.values())
                or set(final_ranks.values()) != set(range(1, count + 1))):
            raise ValueError("Final official ranks must cover the completed field exactly")
    rng = np.random.default_rng(seed)
    histogram = np.zeros((count, count), dtype=np.int64)
    totals = np.zeros((3, count), dtype=np.int64)
    qualification = np.zeros(count, dtype=np.int64)
    point_sums = np.zeros((3, count), dtype=np.int64)
    repairs, conflicts, total_byes = 0, 0, 0
    for begin in range(0, iterations, batch_size):
        batch = min(batch_size, iterations - begin)
        rows = np.arange(batch)
        mp, bp, vp, bye_counts = [np.broadcast_to(values, (batch, count)).copy()
                                  for values in (start_match, start_board, start_vp, start_byes)]
        personal = np.broadcast_to(member_vps, (batch, count, team_size)).copy()
        history = np.full((batch, count, past_rounds + rounds), -1, dtype=np.int32)
        history[:, :, :past_rounds] = initial_history
        for turn in range(rounds):
            if fixed and turn == 0:
                left = np.broadcast_to(np.asarray(first_left, dtype=np.int32), (batch, len(first_left))).copy()
                right = np.broadcast_to(np.asarray(first_right, dtype=np.int32), left.shape).copy()
                bye = np.full(batch, first_bye, dtype=np.int32) if count % 2 else None
            else:
                order = _order(mp, bp, vp, rng)
                bye = None
                if count % 2:
                    # Prefer teams with the fewest byes; among those take the
                    # lowest standings. Pair the remaining teams in rank order.
                    eligible = bye_counts == bye_counts.min(axis=1, keepdims=True)
                    candidates = np.where(np.take_along_axis(eligible, order, axis=1), np.arange(count), -1)
                    position = candidates.argmax(axis=1)
                    bye = order[rows, position]
                    order = order[order != bye[:, None]].reshape(batch, count - 1)
                left, right = order[:, 0::2].copy(), order[:, 1::2].copy()
                bad, swaps = _repair_repeats(left, right, history, past_rounds + turn)
                conflicts += bad; repairs += swaps
            if bye is not None:
                mp[rows, bye] += 1; bp[rows, bye] += 2 * team_size; vp[rows, bye] += 1200 * team_size
                # Equal bye credits preserve each team's relative seat order.
                personal[rows, bye] += 1200
                bye_counts[rows, bye] += 1; total_byes += batch
            key = np.broadcast_to(masterpoints, personal.shape) if completed + turn == 0 else personal
            seats = np.lexsort((rng.random(personal.shape), -key), axis=2)
            r = rows[:, None, None]
            left_seats, right_seats = seats[rows[:, None], left], seats[rows[:, None], right]
            a, b = strengths[left[..., None], left_seats], strengths[right[..., None], right_seats]
            loc = ALPHA * boards * (a - b) / 100.0
            margins = np.rint(rng.normal(loc, SIGMA * math.sqrt(boards)))
            left_vp = np.rint(100 * _vp_from_margin(margins, boards)).astype(np.int64)
            lbp, rbp, lmp, rmp = team_round_scores(left_vp)
            t = rows[:, None]
            mp[t, left] += lmp; mp[t, right] += rmp
            bp[t, left] += lbp; bp[t, right] += rbp
            vp[t, left] += left_vp.sum(axis=2); vp[t, right] += (2000 - left_vp).sum(axis=2)
            personal[r, left[..., None], left_seats] += left_vp
            personal[r, right[..., None], right_seats] += 2000 - left_vp
            history[t, left, past_rounds + turn] = right
            history[t, right, past_rounds + turn] = left
        if final_ranks is None:
            order = _order(mp, bp, vp, rng)
            ranks = np.empty_like(order)
            np.put_along_axis(ranks, order, np.arange(1, count + 1)[None, :], axis=1)
        else:
            ranks = np.broadcast_to([final_ranks[identifier] for identifier in ids], (batch, count))
        qualification += (ranks <= qualify).sum(axis=0)
        totals[0] += (ranks <= promote).sum(axis=0)
        totals[1] += ((ranks > promote) & (ranks <= promote + stay)).sum(axis=0)
        totals[2] += (ranks > promote + stay).sum(axis=0)
        np.add.at(histogram, (np.broadcast_to(np.arange(count), ranks.shape).ravel(), (ranks - 1).ravel()), 1)
        point_sums += np.asarray([mp.sum(axis=0), bp.sum(axis=0), vp.sum(axis=0)])
    slots = [min(promote, count), min(stay, max(0, count - promote)), max(0, count - promote - stay)]
    if list(totals.sum(axis=1)) != [slot * iterations for slot in slots] or qualification.sum() != min(qualify, count) * iterations:
        raise AssertionError("Team forecast quota conservation failed")
    cumulative = histogram.cumsum(axis=1)
    low = (cumulative >= max(1, math.ceil(iterations * .05))).argmax(axis=1) + 1
    high = (cumulative >= max(1, math.ceil(iterations * .95))).argmax(axis=1) + 1
    mean_ranks = histogram @ np.arange(1, count + 1) / iterations
    output = []
    for n, identifier in enumerate(ids):
        output.append({"team_id": identifier, "qualification_probability": float(qualification[n] / iterations),
            "promotion_probability": float(totals[0, n] / iterations), "retention_probability": float(totals[1, n] / iterations),
            "relegation_probability": float(totals[2, n] / iterations), "mean_rank": float(mean_ranks[n]),
            "rank_p05": int(low[n]), "rank_p95": int(high[n]),
            "mean_match_points": float(point_sums[0, n] / iterations),
            "mean_board_points": float(point_sums[1, n] / (2 * iterations)), "mean_vp": float(point_sums[2, n] / (100 * iterations))})
    return {"teams": output, "summary": {"iterations": iterations, "remaining_rounds": rounds,
        "team_size": team_size, "qualify_slots": min(qualify, count), "promote_slots": slots[0], "stay_slots": slots[1],
        "relegate_slots": slots[2], "fixed_next_pairings": fixed, "total_simulated_byes": total_byes,
        "repeat_pairings_repaired": conflicts, "two_table_repair_swaps": repairs, "remaining_repeat_pairings": 0,
        "standings_order": ["match_points", "board_points", "total_vp"],
        "seat_order": ("observed_opening_seats_then_cumulative_personal_vp" if completed == 0 and any(
            'opening_seat' in member for team in teams for member in team['members'])
            else "masterpoints_in_round_one_then_cumulative_personal_vp"),
        "pairing_method": "adjacent_team_standings_no_repeat_approximation",
        "residual_ties": "uniform_random_unless_official_final_ranks_supplied",
        "strength_policy": "fixed_published_raw_rating", "outcome_model": MODEL_NAME, "model_source": MODEL_SOURCE,
        "pk_residuals": "independent_between_seats", "maximum_monte_carlo_se": .5 / math.sqrt(iterations)}}
