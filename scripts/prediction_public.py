"""Public prediction projection and validation, independent of business storage."""
from __future__ import annotations

import math
import re

PUBLIC_ID = re.compile(r"[a-f0-9]{24}\Z")
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}\Z")
EDITION = re.compile(r"20\d{2}(?:0[1-9]|1[0-2])\Z")
AVATAR = re.compile(r"avatars/[a-f0-9]{24}\.(?:jpg|jpeg|png|gif|webp)\Z")
PHONE = re.compile(r"(1[3-9][0-9])[0-9]{4}([0-9]{4})\Z")
PLAYER_FIELDS = frozenset(("id", "nickname", "avatar", "display_rating", "rating_deviation", "provisional",
    "rating_tier", "group", "promotion_probability", "retention_probability", "relegation_probability",
    "mean_rank", "rank_p05", "rank_p95"))
GROUP_FIELDS = frozenset(("id", "name", "count", "boards", "promote", "stay", "relegate"))
TOP_FIELDS = frozenset(("schema_version", "event", "edition", "status", "publication_version", "snapshot_id",
    "rating_cutoff", "rating_generated_at", "score_cutoff", "generated_at", "iterations", "completed_rounds",
    "total_rounds", "remaining_rounds", "groups", "players", "withdrawn_players", "withdrawn_player_count",
    "pairing_method", "next_round_pairings", "current_score_source", "history_limitations", "event_statuses"))


def mask_nickname(value):
    if not isinstance(value, str):
        raise ValueError("prediction_public_nickname_required")
    match = PHONE.fullmatch(value)
    return f"{match[1]}****{match[2]}" if match else value


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def public_identity(person, group):
    identifier = person.get("id", person.get("public_id"))
    if not isinstance(identifier, str) or not PUBLIC_ID.fullmatch(identifier):
        raise ValueError("prediction_public_identity_missing")
    return {"id": identifier, "nickname": mask_nickname(person.get("nickname")), "group": group}


def validate_public(value):
    if (set(value) != TOP_FIELDS or value["schema_version"] != "xinrui-predictions-v1"
            or value["status"] not in ("forecast", "complete", "unavailable") or value["iterations"] != 10000
            or not SAFE_ID.fullmatch(value["event"]) or not EDITION.fullmatch(value["edition"])):
        raise ValueError("prediction_public_schema_invalid")
    if any(not isinstance(value[key], str) or not re.fullmatch(r"[a-f0-9]{64}", value[key])
           for key in ("publication_version", "snapshot_id")):
        raise ValueError("prediction_public_identity_invalid")
    if (type(value["completed_rounds"]) is not int or type(value["total_rounds"]) is not int
            or not 0 <= value["completed_rounds"] <= value["total_rounds"]
            or value["remaining_rounds"] != value["total_rounds"] - value["completed_rounds"]):
        raise ValueError("prediction_public_progress_invalid")
    groups, players, withdrawn = value["groups"], value["players"], value["withdrawn_players"]
    if not all(isinstance(rows, list) for rows in (groups, players, withdrawn)):
        raise ValueError("prediction_public_rows_invalid")
    if value["withdrawn_player_count"] != len(withdrawn):
        raise ValueError("prediction_public_withdrawal_count_invalid")
    group_ids = [group["id"] for group in groups]
    if len(set(group_ids)) != len(group_ids):
        raise ValueError("prediction_public_group_duplicate")
    for person in withdrawn:
        if (set(person) != {"id", "nickname", "group"} or not PUBLIC_ID.fullmatch(person["id"])
                or person["group"] not in group_ids or PHONE.fullmatch(person["nickname"])):
            raise ValueError("prediction_public_withdrawal_invalid")
    identities = [person["id"] for person in players + withdrawn]
    if len(set(identities)) != len(identities):
        raise ValueError("prediction_public_player_duplicate")
    for group in groups:
        if (set(group) != GROUP_FIELDS or not SAFE_ID.fullmatch(group["id"])
                or any(type(group[key]) is not int or group[key] < 0 for key in ("count", "promote", "stay", "relegate"))
                or sum(group[key] for key in ("promote", "stay", "relegate")) != group["count"]):
            raise ValueError("prediction_public_quota_invalid")
        cohort = [person for person in players if person["group"] == group["id"]]
        if len(cohort) != group["count"]:
            raise ValueError("prediction_public_group_coverage_invalid")
        for field, quota in (("promotion_probability", "promote"), ("retention_probability", "stay"),
                             ("relegation_probability", "relegate")):
            if not math.isclose(sum(person[field] for person in cohort), group[quota], abs_tol=1e-6):
                raise ValueError("prediction_public_quota_not_conserved")
    counts = {group["id"]: group["count"] for group in groups}
    for person in players:
        if (set(person) != PLAYER_FIELDS or not PUBLIC_ID.fullmatch(person["id"])
                or person["group"] not in counts or PHONE.fullmatch(person["nickname"])
                or not finite(person["display_rating"]) or not finite(person["rating_deviation"])
                or person["rating_deviation"] < 0 or type(person["provisional"]) is not bool
                or person["avatar"] is not None and not AVATAR.fullmatch(person["avatar"])):
            raise ValueError("prediction_public_player_invalid")
        probabilities = [person[key] for key in ("promotion_probability", "retention_probability", "relegation_probability")]
        if any(not finite(value) or not 0 <= value <= 1 for value in probabilities) or not math.isclose(sum(probabilities), 1, abs_tol=1e-10):
            raise ValueError("prediction_public_probability_invalid")
        count = counts[person["group"]]
        if (not finite(person["mean_rank"]) or not 1 <= person["mean_rank"] <= count
                or any(type(person[key]) is not int for key in ("rank_p05", "rank_p95"))
                or not 1 <= person["rank_p05"] <= person["rank_p95"] <= count):
            raise ValueError("prediction_public_rank_invalid")
    if (not isinstance(value["history_limitations"], list) or any(not isinstance(item, str) for item in value["history_limitations"])
            or not isinstance(value["event_statuses"], dict)):
        raise ValueError("prediction_public_method_invalid")
    return value
