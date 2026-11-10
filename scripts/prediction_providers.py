"""Registered forecast providers operating only on a verified in-memory bundle."""
from __future__ import annotations

from decimal import Decimal
import hashlib
import math

from prediction_public import finite, public_identity, validate_public
from prediction_simulation import simulate_group
from prediction_registry import probability_applicability


def integer(value, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("prediction_integer_invalid")
    return value


def numeric(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("prediction_number_invalid")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("prediction_number_invalid")
    return result


def group_inputs(group):
    """Sum actual per-round VP; opponent history contains retained cloud rows only."""
    completed = integer(group["completed_rounds"])
    people = group["players"]
    by_uid, scores, byes = {}, {}, {}
    for person in people:
        uid = integer(person["uid"], minimum=1)
        if uid in by_uid:
            raise ValueError("prediction_player_duplicate")
        by_uid[uid] = person
        vps = person["round_vps"]
        if not isinstance(vps, list) or len(vps) < completed:
            raise ValueError("prediction_round_vp_incomplete")
        completed_vps = [numeric(value) for value in vps[:completed]]
        if any(not 0 <= value <= 20 for value in completed_vps):
            raise ValueError("prediction_round_vp_invalid")
        total = sum((Decimal(str(value)) for value in vps[:completed]), Decimal(0))
        if not math.isclose(float(total), numeric(person["current_vp"]), abs_tol=1e-6):
            raise ValueError("prediction_cumulative_vp_mismatch")
        scores[uid] = float(total)
        byes[uid] = integer(person.get("initial_byes", person.get("bye_count", 0)))
        if not finite(person["rating"]) or not finite(person["rating_deviation"]) or person["rating_deviation"] < 0:
            raise ValueError("prediction_rating_projection_invalid")
    if not by_uid:
        raise ValueError("prediction_roster_empty")
    opponents = {uid: [] for uid in by_uid}
    seen = set()
    for row in group.get("retained_rows", []):
        turn = integer(row["round"], minimum=1)
        if turn > completed:
            raise ValueError("prediction_retained_round_outside_prefix")
        left, right = integer(row["uid1"], minimum=1), integer(row["uid2"], minimum=1)
        if left == right or (turn, left) in seen or (turn, right) in seen:
            raise ValueError("prediction_retained_pairing_duplicate")
        seen.update(((turn, left), (turn, right)))
        for uid, score in ((left, row["vp1"]), (right, row["vp2"])):
            if uid in by_uid and not math.isclose(numeric(score), numeric(by_uid[uid]["round_vps"][turn - 1]), abs_tol=1e-6):
                raise ValueError("prediction_retained_vp_mismatch")
        if left in by_uid and right in by_uid:
            if right not in opponents[left]:
                opponents[left].append(right)
                opponents[right].append(left)
    return people, scores, opponents, byes


def _progress(module, spec):
    groups = module.get("groups", [])
    if module.get("status") == "complete" and "completed_rounds" in module and "total_rounds" in module:
        done = integer(module["completed_rounds"])
        total = integer(module["total_rounds"], minimum=1)
    elif groups:
        completed = {integer(group["completed_rounds"]) for group in groups}
        totals = {integer(group.get("total_rounds", spec["total_rounds"]), minimum=1) for group in groups}
        if len(completed) != 1 or len(totals) != 1:
            raise ValueError("prediction_group_progress_mismatch")
        done, total = completed.pop(), totals.pop()
    else:
        done = integer(module["completed_rounds"])
        total = integer(module["total_rounds"], minimum=1)
    if done > total:
        raise ValueError("prediction_completed_rounds_invalid")
    return done, total


def forecast(module, bundle, spec, *, generated_at, simulator=simulate_group):
    done, total = _progress(module, spec)
    unavailable = module.get("status") == "unavailable"
    complete = module.get("status") == "complete" or done == total
    value = {
        "schema_version": "xinrui-predictions-v1", "event": module["family"], "edition": module["edition"],
        "status": "unavailable" if unavailable else "complete" if complete else "forecast", "publication_version": bundle["publication_version"],
        "snapshot_id": bundle["snapshot_id"], "rating_cutoff": bundle["rating_cutoff"],
        "rating_generated_at": bundle["rating_generated_at"], "score_cutoff": bundle["rating_cutoff"],
        "generated_at": generated_at, "iterations": 10000, "completed_rounds": done,
        "total_rounds": total, "remaining_rounds": total - done,
        "groups": [], "players": [], "withdrawn_players": [], "withdrawn_player_count": 0,
        "pairing_method": "adjacent_cumulative_vp_no_repeat_approximation", "next_round_pairings": "approximate",
        "current_score_source": "verified_cloud_round_vp", "history_limitations": [], "event_statuses": {},
    }
    if unavailable:
        return validate_public(value)
    if complete:
        if done != total:
            raise ValueError("prediction_complete_progress_inconsistent")
        return validate_public(value)
    if module.get("status") not in ("active", "ready"):
        raise ValueError("prediction_module_not_ready")
    if module.get("format") not in spec["supported_formats"] or module.get("format") != "vp_swiss":
        raise ValueError("prediction_format_unsupported")
    if any(group.get("entity_kind") == "team" or "teams" in group for group in module["groups"]):
        raise ValueError("prediction_format_unsupported")
    for group in module["groups"]:
        if group.get("ranking_complete") is not True:
            raise ValueError("prediction_ranking_not_verified")
        people, scores, opponents, byes = group_inputs(group)
        group_id = group["id"]
        count = len(people)
        promote, stay = integer(group["promote"]), integer(group["stay"])
        relegate = count - promote - stay
        if relegate < 0:
            raise ValueError("prediction_active_roster_below_fixed_quotas")
        boards = group["boards"]
        qualify = group.get('qualify')
        seed = int.from_bytes(hashlib.sha256((bundle["publication_version"] + bundle["snapshot_id"] +
            module["id"] + group_id).encode()).digest()[:4], "big")
        modeled = [{"uid": person["uid"], "nickname": "", "rating": person["rating"],
            "rd": person["rating_deviation"], "provisional": person["provisional"]} for person in people]
        simulated = simulator(modeled, None, total - done, boards, promote, relegate, iterations=10000,
            seed=seed, initial_scores=scores, prior_opponents=opponents, initial_byes=byes,
            **({'qualify': qualify} if qualify is not None else {}))
        outcomes = {person["uid"]: person for person in simulated["players"]}
        value["groups"].append({"id": group_id, "name": group["name"], "count": count,
            "boards": boards, "promote": promote, "stay": stay, "relegate": relegate,
            **probability_applicability(module['family'], group),
            **({'qualify': min(qualify, count)} if qualify is not None else {})})
        for person in people:
            outcome = outcomes[person["uid"]]
            value["players"].append({**public_identity(person, group_id), "avatar": person.get("avatar"),
                "display_rating": person["display_rating"], "rating_deviation": person["rating_deviation"],
                "provisional": person["provisional"], "rating_tier": person.get("rating_tier"),
                "promotion_probability": outcome["promote_probability"], "retention_probability": outcome["stay_probability"],
                **({'qualification_probability': outcome['qualification_probability']} if qualify is not None else {}),
                "relegation_probability": outcome["relegate_probability"], "mean_rank": outcome["mean_rank"],
                "rank_p05": outcome["rank_90_interval"][0], "rank_p95": outcome["rank_90_interval"][1]})
        value["withdrawn_players"].extend(public_identity(person, group_id) for person in group.get("withdrawn_players", []))
        if done and not group.get("opponent_history_complete", False):
            value["history_limitations"].append("valid_retained_opponent_history_only")
        if done and not group.get("bye_history_complete", False):
            value["history_limitations"].append("bye_history_unavailable")
        if group.get("withdrawn_players"):
            value["history_limitations"].append("withdrawn_opponents_excluded")
    value["history_limitations"] = list(dict.fromkeys(value["history_limitations"]))
    value["withdrawn_player_count"] = len(value["withdrawn_players"])
    return validate_public(value)


def monthly(module, bundle, spec, **options):
    return forecast(module, bundle, spec, **options)


def team(module, bundle, spec, **options):
    return forecast(module, bundle, spec, **options)


def pair(module, bundle, spec, **options):
    return forecast(module, bundle, spec, **options)
