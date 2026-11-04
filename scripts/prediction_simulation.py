"""In-memory monthly league forecasts using fixed current rating strengths.

No login, network access, business-data reads, or result persistence occurs here.
The score distribution is the existing 2026-10-26 validation-selected normal
total-IMP model. Later pairings approximate Swiss movement by adjacent current
VP standings, with random ties and no repeat opponents. They are not claimed to
reproduce XRBridge's unpublished pairing or tie-break implementation.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import time

import numpy as np


ALPHA = 0.20640989161726025
SIGMA = 5.572962448600062
MODEL_NAME = "nll_discrete_normal_imp"
MODEL_SOURCE = "reports/vp-model-fits-2026-10-26.json"
# Match the locally verified table in scripts/vp_conversion.py. Its historical
# displayed values differ from direct formula rounding by 0.01 at seven margins.
_WBF_16_BOARD_VP = np.asarray((
    10.00, 10.31, 10.61, 10.91, 11.20, 11.48, 11.76, 12.03, 12.29, 12.55,
    12.80, 13.04, 13.28, 13.52, 13.75, 13.97, 14.18, 14.39, 14.60, 14.80,
    15.00, 15.19, 15.38, 15.56, 15.74, 15.92, 16.09, 16.26, 16.42, 16.58,
    16.73, 16.88, 17.03, 17.17, 17.31, 17.45, 17.59, 17.72, 17.85, 17.97,
    18.09, 18.21, 18.33, 18.44, 18.55, 18.66, 18.77, 18.87, 18.97, 19.07,
    19.16, 19.25, 19.34, 19.43, 19.52, 19.61, 19.69, 19.77, 19.85, 19.93,
    20.00,
))


def _positive_integer(value, name, *, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer")
    if value < (0 if allow_zero else 1):
        raise ValueError(f"{name} is out of range")
    return int(value)


def _vp_from_margin(margin, boards):
    """The existing WBF candidate formula, with displayed 0.01-VP rounding."""
    if boards == 16:
        winner = _WBF_16_BOARD_VP[np.minimum(np.abs(margin).astype(np.int64), 60)]
        return np.where(margin >= 0, winner, np.round(20.0 - winner, 2))
    ratio = ((math.sqrt(5.0) - 1.0) / 2.0) ** 3
    amount = -10.0 * np.expm1(
        math.log(ratio) * np.abs(margin) / (15.0 * math.sqrt(boards))
    ) / (1.0 - ratio)
    return np.round(10.0 + np.sign(margin) * np.minimum(10.0, amount), 2)


def _previous_opponents(history, rows, player, opponent, turn):
    return np.any(history[rows, player, :turn] == opponent[:, None], axis=1)


def _repair_repeats(left, right, history, turn):
    """Swap two tables' opponents; both resulting tables must be legal.

    Each previous round forbids at most one candidate player per participant.
    Thus a large group always has a local two-table repair: when there are more
    than 2*turn other tables, the two participants cannot forbid every table.
    The actual 1,000-player fields are comfortably in this range.
    """
    if turn == 0 or left.shape[1] == 0:
        return 0, 0
    batch_rows = np.arange(left.shape[0])[:, None]
    bad = np.any(history[batch_rows, left, :turn] == right[..., None], axis=2)
    conflicts = int(bad.sum())
    swaps = 0
    for column in np.flatnonzero(np.any(bad, axis=0)):
        remaining = np.flatnonzero(bad[:, column])
        if not len(remaining):
            continue
        for offset in range(1, left.shape[1]):
            target = (int(column) + offset) % left.shape[1]
            a, b = left[remaining, column], right[remaining, column]
            c, d = left[remaining, target], right[remaining, target]
            legal = ~_previous_opponents(history, remaining, a, d, turn)
            legal &= ~_previous_opponents(history, remaining, c, b, turn)
            fixed = remaining[legal]
            if len(fixed):
                saved = right[fixed, column].copy()
                right[fixed, column] = right[fixed, target]
                right[fixed, target] = saved
                bad[fixed, column] = False
                bad[fixed, target] = False
                swaps += len(fixed)
            remaining = remaining[~legal]
            if not len(remaining):
                break

            # The alternative orientation is useful for very small test fields.
            a, b = left[remaining, column], right[remaining, column]
            c, d = left[remaining, target], right[remaining, target]
            legal = ~_previous_opponents(history, remaining, a, c, turn)
            legal &= ~_previous_opponents(history, remaining, b, d, turn)
            fixed = remaining[legal]
            if len(fixed):
                saved = right[fixed, column].copy()
                right[fixed, column] = left[fixed, target]
                left[fixed, target] = saved
                bad[fixed, column] = False
                bad[fixed, target] = False
                swaps += len(fixed)
            remaining = remaining[~legal]
            if not len(remaining):
                break
        if len(remaining):
            raise ValueError(
                "No legal two-table repair exists for this small field and "
                "round history; use fewer rounds or a full pairing solver."
            )
    if np.any(history[batch_rows, left, :turn] == right[..., None]):
        raise AssertionError("Repeated opponent survived pairing repair")
    return conflicts, swaps


def simulate_group(
    players,
    pairings,
    rounds,
    boards,
    promote,
    relegate,
    iterations=10000,
    seed=20261101,
    *,
    bye_vp=12.0,
    batch_size=256,
    initial_scores=None,
    prior_opponents=None,
    initial_byes=None,
):
    """Return per-player forecasts and an audit summary, entirely in memory.

    ``players``: dictionaries containing uid, nickname, rating, rd, provisional.
    ``pairings``: actual first-round dictionaries {uid1, uid2} or two-item pairs.
    They must cover everyone except one bye in an odd-sized field. An empty
    list/None pairs the first remaining round by current scores (random if tied).
    ``boards``: one of 8, 12, 16, or a per-round sequence of those board counts.
    ``initial_scores``: complete UID -> cumulative official VP mapping, or None.
    ``prior_opponents``: complete UID -> sequence of previously faced UIDs.
    ``initial_byes``: complete UID -> number of earlier byes. These inputs stay
    fixed throughout the simulations; only ``rounds`` future rounds are drawn.

    Probabilities are fractions. The inclusive rank_90_interval is the 5th to
    95th percentile of simulated ranks. monte_carlo_se measures only simulation
    sampling error, not uncertainty in the model, ratings, or future attendance.
    RD/provisional are reported as supplied and do not perturb fixed strengths.
    """
    started = time.perf_counter()
    rounds = _positive_integer(rounds, "rounds")
    iterations = _positive_integer(iterations, "iterations")
    batch_size = _positive_integer(batch_size, "batch_size")
    promote = _positive_integer(promote, "promote", allow_zero=True)
    relegate = _positive_integer(relegate, "relegate", allow_zero=True)
    players = list(players)
    count = len(players)
    if count == 0 or promote + relegate > count:
        raise ValueError("A nonempty field and disjoint outcome quotas are required")
    if not math.isfinite(float(bye_vp)) or not 0 <= float(bye_vp) <= 20:
        raise ValueError("bye_vp must be a finite VP score between 0 and 20")
    if isinstance(boards, (int, np.integer)) and not isinstance(boards, bool):
        board_counts = [int(boards)] * rounds
    elif isinstance(boards, Sequence) and not isinstance(boards, (str, bytes)):
        board_counts = list(boards)
    else:
        raise ValueError("boards must be a board count or per-round sequence")
    if len(board_counts) != rounds or any(b not in (8, 12, 16) for b in board_counts):
        raise ValueError("The fitted model supports 8, 12, or 16 boards per round")
    uids = [player["uid"] for player in players]
    if len(set(uids)) != count:
        raise ValueError("Player UIDs must be unique")
    uid_to_index = {uid: index for index, uid in enumerate(uids)}
    strengths = np.asarray([player["rating"] for player in players], dtype=float)
    if not np.all(np.isfinite(strengths)):
        raise ValueError("Every player needs a finite raw rating")
    if count > 1 and rounds > count - 1 + count % 2:
        raise ValueError("Too many rounds to avoid repeat opponents")

    def complete_mapping(value, name, default):
        if value is None:
            return {uid: default for uid in uids}
        if not isinstance(value, Mapping) or set(value) != set(uids):
            raise ValueError(f"{name} must cover exactly the player field")
        return value

    score_mapping = complete_mapping(initial_scores, "initial_scores", 0.0)
    starting_scores = np.asarray([score_mapping[uid] for uid in uids], dtype=float)
    if not np.all(np.isfinite(starting_scores)) or np.any(starting_scores < 0):
        raise ValueError("Initial cumulative VP must be finite and nonnegative")
    bye_mapping = complete_mapping(initial_byes, "initial_byes", 0)
    starting_byes = np.asarray([
        _positive_integer(bye_mapping[uid], "initial_byes", allow_zero=True)
        for uid in uids
    ], dtype=np.int16)
    opponent_mapping = complete_mapping(prior_opponents, "prior_opponents", ())
    past_opponents = []
    for uid in uids:
        opponents = list(opponent_mapping[uid])
        if len(set(opponents)) != len(opponents) or uid in opponents or any(
            opponent not in uid_to_index for opponent in opponents
        ):
            raise ValueError("Previous opponents must be distinct other field members")
        if any(uid not in opponent_mapping[opponent] for opponent in opponents):
            raise ValueError("Previous opponent history must be symmetric")
        past_opponents.append([uid_to_index[opponent] for opponent in opponents])
    past_rounds = max((len(opponents) for opponents in past_opponents), default=0)
    initial_history = np.full((count, past_rounds), -1, dtype=np.int32)
    for index, opponents in enumerate(past_opponents):
        initial_history[index, :len(opponents)] = opponents
    if count > 1 and any(
        len(opponents) + rounds > count - 1 for opponents in past_opponents
    ):
        raise ValueError("Too many future rounds to avoid earlier opponents")

    first_left, first_right, first_seen = [], [], set()
    for pairing in pairings or []:
        if isinstance(pairing, Mapping):
            first, second = pairing["uid1"], pairing["uid2"]
        else:
            first, second = pairing
        if first not in uid_to_index or second not in uid_to_index:
            raise ValueError("A first-round pairing contains an unknown UID")
        if first == second or first in first_seen or second in first_seen:
            raise ValueError("First-round pairings must be disjoint")
        if second in opponent_mapping[first]:
            raise ValueError("A fixed remaining-round pairing repeats an earlier opponent")
        first_seen.update((first, second))
        first_left.append(uid_to_index[first])
        first_right.append(uid_to_index[second])
    fixed_first_round = bool(first_left)
    if fixed_first_round and len(first_seen) != count - count % 2:
        raise ValueError("First-round pairings do not cover the full field")
    first_bye = next((uid_to_index[uid] for uid in uids if uid not in first_seen), None)
    first_left = np.asarray(first_left, dtype=np.int32)
    first_right = np.asarray(first_right, dtype=np.int32)

    rng = np.random.default_rng(seed)
    promotion_counts = np.zeros(count, dtype=np.int64)
    relegation_counts = np.zeros(count, dtype=np.int64)
    rank_sums = np.zeros(count, dtype=np.int64)
    score_sums = np.zeros(count, dtype=float)
    bye_totals = np.zeros(count, dtype=np.int64)
    rank_histogram = np.zeros((count, count), dtype=np.int64)
    player_indices = np.arange(count)
    conflicts = repair_swaps = 0
    for begin in range(0, iterations, batch_size):
        batch = min(batch_size, iterations - begin)
        row_indices = np.arange(batch)
        scores = np.broadcast_to(starting_scores, (batch, count)).copy()
        history = np.full((batch, count, past_rounds + rounds), -1, dtype=np.int32)
        history[:, :, :past_rounds] = initial_history
        bye_counts = np.broadcast_to(starting_byes, (batch, count)).copy()
        for turn, board_count in enumerate(board_counts):
            if turn == 0 and fixed_first_round:
                left = np.broadcast_to(first_left, (batch, len(first_left))).copy()
                right = np.broadcast_to(first_right, (batch, len(first_right))).copy()
                bye = np.full(batch, first_bye, dtype=np.int32) if count % 2 else None
            else:
                sort_key = -scores + rng.random(scores.shape) * 1e-8
                bye = None
                if count % 2:
                    eligible = bye_counts == np.min(bye_counts, axis=1)[:, None]
                    bye_key = np.where(eligible, scores + rng.random(scores.shape) * 1e-8, np.inf)
                    bye = np.argmin(bye_key, axis=1)
                    sort_key[row_indices, bye] = np.inf
                ordering = np.argsort(sort_key, axis=1)
                if count % 2:
                    ordering = ordering[:, :-1]
                left, right = ordering[:, 0::2].copy(), ordering[:, 1::2].copy()
                round_conflicts, round_swaps = _repair_repeats(left, right, history, past_rounds + turn)
                conflicts += round_conflicts
                repair_swaps += round_swaps

            if bye is not None:
                scores[row_indices, bye] += float(bye_vp)
                bye_counts[row_indices, bye] += 1
            loc = ALPHA * board_count * (strengths[left] - strengths[right]) / 100.0
            margins = np.rint(rng.normal(loc, SIGMA * math.sqrt(board_count)))
            vp = _vp_from_margin(margins, board_count)
            batch_rows = row_indices[:, None]
            scores[batch_rows, left] += vp
            scores[batch_rows, right] += 20.0 - vp
            history[batch_rows, left, past_rounds + turn] = right
            history[batch_rows, right, past_rounds + turn] = left

        # Break exact VP ties uniformly; no published official tie-break is assumed.
        ordering = np.argsort(-scores + rng.random(scores.shape) * 1e-8, axis=1)
        ranks = np.empty_like(ordering)
        np.put_along_axis(ranks, ordering, np.arange(1, count + 1)[None, :], axis=1)
        promotion_counts += np.sum(ranks <= promote, axis=0)
        relegation_counts += np.sum(ranks > count - relegate, axis=0)
        rank_sums += np.sum(ranks, axis=0)
        score_sums += np.sum(scores, axis=0)
        bye_totals += np.sum(bye_counts, axis=0)
        np.add.at(rank_histogram,
                  (np.broadcast_to(player_indices, ranks.shape).ravel(), (ranks - 1).ravel()), 1)

    stay_counts = iterations - promotion_counts - relegation_counts
    if int(promotion_counts.sum()) != promote * iterations:
        raise AssertionError("Promotion quota conservation failed")
    if int(relegation_counts.sum()) != relegate * iterations:
        raise AssertionError("Relegation quota conservation failed")
    cumulative = np.cumsum(rank_histogram, axis=1)
    lower_target = max(1, math.ceil(iterations * 0.05))
    upper_target = max(1, math.ceil(iterations * 0.95))
    rank_low = np.argmax(cumulative >= lower_target, axis=1) + 1
    rank_high = np.argmax(cumulative >= upper_target, axis=1) + 1
    output = []
    for index, player in enumerate(players):
        probabilities = {
            "promote": float(promotion_counts[index] / iterations),
            "stay": float(stay_counts[index] / iterations),
            "relegate": float(relegation_counts[index] / iterations),
        }
        output.append({
            "uid": player["uid"],
            "nickname": player.get("nickname", ""),
            "rating": float(strengths[index]),
            "rd": player.get("rd"),
            "provisional": player.get("provisional"),
            "promote_probability": probabilities["promote"],
            "stay_probability": probabilities["stay"],
            "relegate_probability": probabilities["relegate"],
            "mean_rank": float(rank_sums[index] / iterations),
            "rank_90_interval": [int(rank_low[index]), int(rank_high[index])],
            "mean_vp": float(score_sums[index] / iterations),
            "expected_byes": float(bye_totals[index] / iterations - starting_byes[index]),
            "monte_carlo_se": {
                key: math.sqrt(probability * (1.0 - probability) / iterations)
                for key, probability in probabilities.items()
            },
        })
    return {
        "players": output,
        "summary": {
            "players": count,
            "iterations": iterations,
            "batch_size": batch_size,
            "seed": int(seed) if isinstance(seed, (int, np.integer)) else seed,
            "rounds": rounds,
            "prior_opponent_rounds": past_rounds,
            "initial_scores_supplied": initial_scores is not None,
            "initial_score_total": float(starting_scores.sum()),
            "boards_per_round": [int(b) for b in board_counts],
            "promote_slots": promote,
            "stay_slots": count - promote - relegate,
            "relegate_slots": relegate,
            "expected_promotion_count": float(promotion_counts.sum() / iterations),
            "expected_stay_count": float(stay_counts.sum() / iterations),
            "expected_relegation_count": float(relegation_counts.sum() / iterations),
            "fixed_first_round": fixed_first_round,
            "bye_vp": float(bye_vp),
            "total_simulated_byes": int(bye_totals.sum() - starting_byes.sum() * iterations),
            "initial_repeat_pairings_repaired": conflicts,
            "two_table_repair_swaps": repair_swaps,
            "remaining_repeat_pairings": 0,
            "maximum_monte_carlo_se": 0.5 / math.sqrt(iterations),
            "outcome_model": MODEL_NAME,
            "model_source": MODEL_SOURCE,
            "model_alpha": ALPHA,
            "model_sigma": SIGMA,
            "strength_policy": "fixed raw rating; supplied RD is descriptive only",
            "later_pairings": "adjacent cumulative VP standings with local no-repeat repair",
            "tie_break": "uniform random order for exact VP ties",
            "elapsed_seconds": time.perf_counter() - started,
        },
    }
