"""Calculation-only team provider; does not publish files or access upstreams.

This contract deliberately requires explicit match/board/VP standings. The old
team ranking's `round_vps`/`current_vp` fields do not establish these quantities
or member seat scores, and must not be guessed into a model input.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import math

from competition_knowledge import knowledge_stages
from prediction_public import AVATAR, EDITION, PUBLIC_ID, SAFE_ID, finite, mask_nickname, public_identity
from prediction_team_swiss import simulate_team_group
from prediction_registry import probability_applicability

MEMBER_DISPLAY_FIELDS = ('avatar', 'display_rating', 'provisional', 'rating_tier')


def public_member(person):
    identity = public_identity(person, None)
    member = {key: identity[key] for key in ('id', 'nickname')}
    if 'display_rating' in person:
        member.update({key: person.get(key) for key in MEMBER_DISPLAY_FIELDS})
    return member


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError("prediction_timestamp_required")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.utcoffset() is None:
        raise ValueError("prediction_timestamp_requires_timezone")
    return stamp


def _confirmed_schedule(group, bundle, rule, spec):
    """Resolve the same unique knowledge used by collection; no event rule copy."""
    reference = group.get("announcement")
    if not isinstance(reference, dict):
        raise ValueError("prediction_team_shared_knowledge_required")
    key = (reference.get("normalization"), reference.get("sha256"), reference.get("sha512"))
    if key[0] != "html_text_nodes_punctuation_join_v1" or not all(isinstance(part, str) for part in key):
        raise ValueError("prediction_team_shared_knowledge_required")
    matches = [record for record in bundle.get("competition_knowledge", [])
               if (record.get("normalization"), record.get("sha256"), record.get("sha512")) == key]
    if len(matches) != 1 or not isinstance(matches[0].get("knowledge"), dict) or matches[0]["knowledge"].get("official") is not True:
        raise ValueError("prediction_team_shared_knowledge_required")
    stages = knowledge_stages(matches[0]["knowledge"], {"group": {"groupname": rule["name"]}})
    roots = [stage for stage in stages if not stage.get("after")]
    if (len(roots) != 1 or roots[0]["format"] != "swiss" or roots[0]["rounds"] != spec["total_rounds"]
            or roots[0]["boards_per_round"] != rule["boards"]):
        raise ValueError("prediction_team_shared_schedule_unsupported")
    qualifying = [stage for stage in stages if stage.get("after") == roots[0]["name"] and stage["format"] == "knockout"
                  and stage.get("ranks") == [1, spec["qualify"]]]
    if len(qualifying) != 1:
        raise ValueError("prediction_team_qualification_rule_required")


def forecast(module, bundle, spec, *, generated_at, simulator=simulate_team_group):
    """Produce public team probabilities from a normalized accepted snapshot.

    The spec is the family's registry ``swiss_prediction`` entry. It is a
    separate calculation route while the Pages delivery remains unchanged.
    """
    if (module.get("format") != spec["format"] or module.get("family") not in ("team", "pair")
            or module.get("status") not in ("active", "ready", "complete")):
        raise ValueError("prediction_team_swiss_input_required")
    if (not isinstance(module.get("edition"), str) or not EDITION.fullmatch(module["edition"])
            or module.get("ready") is not True):
        raise ValueError("prediction_team_accepted_input_required")
    for key in ("publication_version", "snapshot_id"):
        if not isinstance(bundle.get(key), str) or len(bundle[key]) != 64 or any(c not in "0123456789abcdef" for c in bundle[key]):
            raise ValueError("prediction_team_snapshot_identity_required")
    cutoff = _stamp(bundle["rating_cutoff"])
    if _stamp(bundle["rating_generated_at"]) < cutoff or _stamp(generated_at) < _stamp(bundle["rating_generated_at"]):
        raise ValueError("prediction_team_snapshot_time_invalid")
    groups = module.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("prediction_team_groups_missing")
    output = {"schema_version": "xinrui-team-swiss-predictions-v1", "event": module["family"],
        "edition": module["edition"], "publication_version": bundle["publication_version"], "snapshot_id": bundle["snapshot_id"],
        "rating_cutoff": bundle["rating_cutoff"], "rating_generated_at": bundle["rating_generated_at"],
        "score_cutoff": bundle["rating_cutoff"], "generated_at": generated_at, "iterations": 10000,
        "groups": [], "teams": []}
    seen_groups, seen_members = set(), set()
    for group in groups:
        rules = [rule for rule in spec["group_rules"] if rule["id"] == group.get("id")]
        if len(rules) != 1 or group["id"] in seen_groups:
            raise ValueError("prediction_team_group_rules_required")
        seen_groups.add(group["id"])
        rule, teams = rules[0], group["teams"]
        _confirmed_schedule(group, bundle, rule, spec)
        if (group.get("standings_complete") is not True or group.get("score_cutoff") != bundle["rating_cutoff"]
                or group.get("boards") != rule["boards"]):
            raise ValueError("prediction_team_verified_standings_required")
        completed = group["completed_rounds"]
        if type(completed) is not int or not 0 <= completed <= spec["total_rounds"]:
            raise ValueError("prediction_team_swiss_progress_invalid")
        if not isinstance(teams, list) or not teams:
            raise ValueError("prediction_teams_missing")
        identities = {}
        for team in teams:
            public_members = []
            for member in team["members"]:
                identity = public_identity(member, group["id"])
                if identity["id"] in seen_members:
                    raise ValueError("prediction_team_public_member_duplicate")
                seen_members.add(identity["id"])
                public_members.append(public_member(member))
            # Hash domain IDs for output; raw team IDs and UIDs stay in memory.
            public_id = hashlib.sha256(f"{module['family']}:{module['edition']}:{group['id']}:{team['team_id']}".encode()).hexdigest()[:24]
            identities[team["team_id"]] = {"id": public_id, "name": mask_nickname(team["name"]),
                "group": group["id"], "members": public_members}
        staying = rule["stay"] if rule["stay"] is not None else max(0, len(teams) - rule["promote"])
        seed = int.from_bytes(hashlib.sha256((bundle["publication_version"] + bundle["snapshot_id"] +
            module["family"] + module["edition"] + group["id"]).encode()).digest()[:4], "big")
        if completed == spec["total_rounds"] and "final_ranks" not in group:
            raise ValueError("prediction_team_final_official_ranks_required")
        result = simulator(teams, spec["total_rounds"] - completed, rule["boards"], rule["promote"], staying,
            team_size=spec["team_size"], qualify=spec["qualify"], completed_rounds=completed, iterations=10000, seed=seed,
            prior_opponents=group.get("prior_opponents"), pairings=group.get("next_pairings"), final_ranks=group.get("final_ranks"))
        limits = []
        if completed and group.get("opponent_history_complete") is not True:
            limits.append("known_team_opponents_only")
        if completed and group.get("bye_history_complete") is not True:
            limits.append("known_team_byes_only")
        output["groups"].append({"id": group["id"], "name": rule["name"], "count": len(teams),
            "team_size": spec["team_size"], "boards": rule["boards"], "completed_rounds": completed,
            "total_rounds": spec["total_rounds"], "remaining_rounds": spec["total_rounds"] - completed,
            "status": "complete" if completed == spec["total_rounds"] else "forecast",
            "qualification_cutoff": spec["qualify"], "promotion_cutoff": rule["promote"],
            "retention_cutoff": rule["promote"] + staying,
            "qualify": result["summary"]["qualify_slots"], "promote": result["summary"]["promote_slots"],
            "stay": result["summary"]["stay_slots"], "relegate": result["summary"]["relegate_slots"],
            "history_limitations": limits, "model": result["summary"]})
        output['groups'][-1].update(probability_applicability(module['family'], group))
        public_fields = ("qualification_probability", "promotion_probability", "retention_probability", "relegation_probability",
                         "mean_rank", "rank_p05", "rank_p95")
        for team in result["teams"]:
            output["teams"].append({**identities[team["team_id"]], **{key: team[key] for key in public_fields}})
    return validate_result(output)


def validate_result(value):
    """Allowlist public team results; verify each quota and probability partition."""
    top = {"schema_version", "event", "edition", "publication_version", "snapshot_id", "rating_cutoff", "rating_generated_at",
           "score_cutoff", "generated_at", "iterations", "groups", "teams"}
    team_fields = {"id", "name", "group", "members", "qualification_probability", "promotion_probability", "retention_probability",
                   "relegation_probability", "mean_rank", "rank_p05", "rank_p95"}
    if set(value) != top or value["schema_version"] != "xinrui-team-swiss-predictions-v1" or value["iterations"] != 10000:
        raise ValueError("prediction_team_public_schema_invalid")
    ids, member_ids, group_ids = set(), set(), set()
    for group in value["groups"]:
        if not SAFE_ID.fullmatch(group["id"]) or group["id"] in group_ids:
            raise ValueError("prediction_team_public_group_invalid")
        group_ids.add(group["id"])
        cohort = [team for team in value["teams"] if team["group"] == group["id"]]
        if len(cohort) != group["count"] or group["promote"] + group["stay"] + group["relegate"] != len(cohort):
            raise ValueError("prediction_team_public_quota_invalid")
        for team in cohort:
            if set(team) != team_fields or not PUBLIC_ID.fullmatch(team["id"]) or team["id"] in ids:
                raise ValueError("prediction_team_public_identity_invalid")
            ids.add(team["id"])
            if len(team["members"]) != group["team_size"] or team["name"] != mask_nickname(team["name"]):
                raise ValueError("prediction_team_public_roster_invalid")
            for member in team["members"]:
                if (set(member) not in ({"id", "nickname"}, {"id", "nickname", *MEMBER_DISPLAY_FIELDS}) or not PUBLIC_ID.fullmatch(member["id"]) or member["id"] in member_ids
                        or member["nickname"] != mask_nickname(member["nickname"])):
                    raise ValueError("prediction_team_public_member_invalid")
                if 'display_rating' in member and (not finite(member['display_rating']) or type(member['provisional']) is not bool
                        or member['avatar'] is not None and (not isinstance(member['avatar'], str) or not AVATAR.fullmatch(member['avatar']))
                        or member['rating_tier'] is not None and not isinstance(member['rating_tier'], str)):
                    raise ValueError('prediction_team_public_member_display_invalid')
                member_ids.add(member["id"])
            probabilities = [team[key] for key in ("qualification_probability", "promotion_probability", "retention_probability", "relegation_probability")]
            if any(not finite(p) or not 0 <= p <= 1 for p in probabilities) or not math.isclose(sum(probabilities[1:]), 1, abs_tol=1e-10):
                raise ValueError("prediction_team_public_probability_invalid")
            if (not finite(team["mean_rank"]) or not 1 <= team["mean_rank"] <= len(cohort)
                    or type(team["rank_p05"]) is not int or type(team["rank_p95"]) is not int
                    or not 1 <= team["rank_p05"] <= team["rank_p95"] <= len(cohort)):
                raise ValueError("prediction_team_public_rank_invalid")
        for probability, quota in (("qualification_probability", "qualify"), ("promotion_probability", "promote"),
                                   ("retention_probability", "stay"), ("relegation_probability", "relegate")):
            if not math.isclose(sum(team[probability] for team in cohort), group[quota], abs_tol=1e-6):
                raise ValueError("prediction_team_public_quota_not_conserved")
    if len(ids) != len(value["teams"]):
        raise ValueError("prediction_team_public_group_coverage_invalid")
    return value
