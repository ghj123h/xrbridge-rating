"""Run registered forecasts in a GitHub worker; all private inputs stay in memory.

Only public files and their manifest are uploaded. No Xinrui transport, local
business cache, previous repository forecast, or arbitrary SQL is used here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
from http.client import HTTPException
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from prediction_public import EDITION, SAFE_ID, validate_public
from prediction_registry import get_prediction_registry, get_prediction_spec, load_provider

VERSION = re.compile(r"[a-f0-9]{64}\Z")
MAX_INPUT = 64 * 1024 * 1024


class PredictionError(RuntimeError):
    def __init__(self, code, *, http_status=None):
        super().__init__(code)
        self.code, self.http_status = code, http_status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def json_text(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"


def validate_bundle(bundle, version, registry):
    if (not isinstance(bundle, dict) or bundle.get("schema_version") != "xinrui-prediction-input-v1"
            or bundle.get("publication_version") != version or not VERSION.fullmatch(bundle.get("snapshot_id", ""))
            or bundle.get("ready") is False or not isinstance(bundle.get("modules"), list)):
        raise PredictionError("prediction_verified_input_required")
    if any(not isinstance(bundle.get(key), str) for key in ("rating_cutoff", "rating_generated_at")):
        raise PredictionError("prediction_rating_identity_required")
    seen = set()
    families = set()
    for module in bundle["modules"]:
        if not isinstance(module, dict):
            raise PredictionError("prediction_module_input_invalid")
        module.setdefault("id", module.get("module_id"))
        if (not isinstance(module.get("id"), str) or not SAFE_ID.fullmatch(module["id"])
                or module["id"] in seen or not isinstance(module.get("edition"), str)
                or not EDITION.fullmatch(module["edition"])):
            raise PredictionError("prediction_module_identity_invalid")
        seen.add(module["id"])
        families.add(module.get("family"))
        get_prediction_spec(module.get("family"), registry)
    if families != {spec["family"] for spec in registry}:
        raise PredictionError("prediction_registered_module_coverage_incomplete")
    return bundle


def build_results(bundle, *, version, registry=None, generated_at=None, simulator=None):
    registry = get_prediction_registry() if registry is None else registry
    validate_bundle(bundle, version, registry)
    generated_at = generated_at or datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    documents = []
    for module in bundle["modules"]:
        spec = get_prediction_spec(module["family"], registry)
        options = {"generated_at": generated_at}
        if simulator is not None:
            options["simulator"] = simulator
        document = load_provider(spec)(module, bundle, spec, **options)
        documents.append((module, document, spec))
    event_statuses = {}
    for module, document, spec in documents:
        if document["status"] == "complete":
            swiss = spec["total_rounds"]
            event_statuses[module["family"]] = {"status": "complete", "edition": module["edition"],
                "completed_swiss_rounds": min(document["completed_rounds"], swiss), "total_swiss_rounds": swiss,
                "remaining_swiss_rounds": max(0, swiss - document["completed_rounds"]),
                "completed_rounds": document["completed_rounds"], "total_rounds": document["total_rounds"]}
    manifest = {"schema_version": "xinrui-prediction-manifest-v1", "publication_version": version,
        "snapshot_id": bundle["snapshot_id"], "rating_cutoff": bundle["rating_cutoff"],
        "rating_generated_at": bundle["rating_generated_at"], "generated_at": generated_at,
        "iterations": 10000, "items": [], "files": []}
    files = []
    paths = set()
    for module, document, _spec in documents:
        document["event_statuses"] = event_statuses
        validate_public(document)
        item = {"id": module["id"], "family": module["family"], "edition": module["edition"],
            "status": document["status"], "completed_rounds": document["completed_rounds"],
            "total_rounds": document["total_rounds"], "remaining_rounds": document["remaining_rounds"]}
        manifest["items"].append(item)
        if document["status"] == "unavailable":
            continue
        stem = module["id"] if module["id"].endswith("-" + module["edition"]) else module["id"] + "-" + module["edition"]
        path = "predictions/" + stem + ".json"
        if path in paths or path == "predictions/index.json":
            raise PredictionError("prediction_public_path_duplicate")
        paths.add(path)
        content = json_text(document)
        raw = content.encode("utf-8")
        files.append({"path": path, "content": content})
        manifest["files"].append({"path": path, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        item["path"] = path
    return {"manifest": manifest, "files": files}


def reusable_manifest(manifest, version, registry):
    if not manifest:
        return False
    if (manifest.get("schema_version") != "xinrui-prediction-manifest-v1"
            or manifest.get("publication_version") != version
            or not isinstance(manifest.get("snapshot_id"), str) or not VERSION.fullmatch(manifest["snapshot_id"])
            or any(not isinstance(manifest.get(key), str) for key in ("rating_cutoff", "rating_generated_at"))
            or manifest.get("iterations") != 10000):
        return False
    items, files = manifest.get("items"), manifest.get("files")
    if not isinstance(items, list) or not isinstance(files, list):
        return False
    if not all(isinstance(item, dict) for item in items) or not all(isinstance(file, dict) for file in files):
        return False
    if {item.get("family") for item in items} != {spec["family"] for spec in registry} or len({item.get("id") for item in items}) != len(items):
        return False
    paths = {item["path"] for item in items if "path" in item}
    if len(paths) != sum("path" in item for item in items) or len(files) != len(paths) or {file.get("path") for file in files} != paths:
        return False
    for item in items:
        if (not isinstance(item.get("id"), str) or not SAFE_ID.fullmatch(item["id"])
                or not isinstance(item.get("edition"), str) or not EDITION.fullmatch(item["edition"])
                or item.get("status") not in ("forecast", "complete", "unavailable")
                or "path" not in item and item.get("status") not in ("complete", "unavailable")
                or any(type(item.get(key)) is not int or item[key] < 0 for key in ("completed_rounds", "total_rounds", "remaining_rounds"))
                or item["completed_rounds"] + item["remaining_rounds"] != item["total_rounds"]):
            return False
    return all(isinstance(file.get("path"), str) and re.fullmatch(r"predictions/[a-z0-9][a-z0-9_-]*\.json", file["path"])
        and file["path"] != "predictions/index.json" and type(file.get("size")) is int and file["size"] > 0
        and isinstance(file.get("sha256"), str) and VERSION.fullmatch(file["sha256"]) for file in files)


class PredictionClient:
    def __init__(self, site_url, job_token, service_token, *, opener=None, sleep=time.sleep):
        parsed = urlsplit(site_url or "")
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise PredictionError("clean_cloud_site_url_required")
        if not job_token or not service_token:
            raise PredictionError("prediction_credentials_required")
        self.endpoint = site_url.rstrip("/") + "/api/admin/predictions"
        self.headers = {"X-Publication-Job-Authorization": "Bearer " + job_token,
            "OAI-Sites-Authorization": "Bearer " + service_token,
            "Content-Type": "application/json", "Accept-Encoding": "identity"}
        self.opener, self.sleep = opener or build_opener(NoRedirect()), sleep

    def request(self, *, part=None, version=None, payload=None, allow_missing=False):
        headers = dict(self.headers)
        if payload is None:
            url, body, method = self.endpoint + "?" + urlencode({"part": part, "version": version}), None, "GET"
        else:
            url, body, method = self.endpoint, gzip.compress(json_text(payload).encode(), mtime=0), "POST"
            headers["Content-Encoding"] = "gzip"
        for attempt in range(6):
            try:
                with self.opener.open(Request(url, data=body, headers=headers, method=method), timeout=120) as response:
                    if response.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
                        raise PredictionError("prediction_response_invalid")
                    raw = response.read(MAX_INPUT + 1)
                    if len(raw) > MAX_INPUT:
                        raise PredictionError("prediction_response_too_large")
                value = json.loads(raw)
                if not isinstance(value, dict) or value.get("error"):
                    raise PredictionError("prediction_response_invalid")
                return value
            except HTTPError as error:
                status = error.code
                error.close()
                if allow_missing and status == 404:
                    return None
                if status not in (429, 500, 502, 503, 504) or attempt == 5:
                    raise PredictionError("prediction_http_failed", http_status=status) from None
            except (URLError, HTTPException, OSError):
                if attempt == 5:
                    raise PredictionError("prediction_transport_failed") from None
            except (ValueError, UnicodeError):
                raise PredictionError("prediction_response_invalid") from None
            self.sleep((1, 2, 4, 8, 15)[attempt])
        raise AssertionError("unreachable")


def run(version, client, *, registry=None, generated_at=None, simulator=None):
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise PredictionError("invalid_publication_version")
    existing = client.request(part="manifest", version=version, allow_missing=True)
    selected = get_prediction_registry() if registry is None else registry
    if reusable_manifest(existing, version, selected):
        return {"status": "reused", "publication_version": version, "snapshot_id": existing["snapshot_id"],
            "modules": len(existing.get("items", []))}
    bundle = client.request(part="input", version=version)
    validate_bundle(bundle, version, selected)
    results = build_results(bundle, version=version, registry=selected, generated_at=generated_at, simulator=simulator)
    response = client.request(payload={"action": "publish", "version": version, "snapshot_id": bundle["snapshot_id"], "results": results})
    if response.get("publication_version") != version or response.get("snapshot_id") != bundle["snapshot_id"]:
        raise PredictionError("prediction_publication_identity_mismatch")
    return {"status": "published", "publication_version": version, "snapshot_id": bundle["snapshot_id"],
        "modules": len(results["manifest"]["items"]), "files": len(results["files"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    try:
        client = PredictionClient(os.environ.get("CLOUD_SITE_URL"), os.environ.get("PUBLICATION_JOB_TOKEN"),
            os.environ.get("SITES_SERVICE_ACCESS_TOKEN"))
        result = run(args.version, client)
    except PredictionError as error:
        print(json.dumps({"event": "prediction_failed", "code": error.code,
            **({"http_status": error.http_status} if error.http_status else {})}), flush=True)
        return 1
    except (ValueError, TypeError, KeyError, ImportError) as error:
        known = {"prediction_format_unsupported", "prediction_module_not_ready", "prediction_ranking_not_verified",
            "prediction_group_progress_mismatch", "prediction_public_identity_missing", "prediction_active_roster_below_fixed_quotas"}
        code = str(error) if isinstance(error, ValueError) and str(error) in known else "prediction_input_or_model_invalid"
        print(json.dumps({"event": "prediction_failed", "code": code}), flush=True)
        return 1
    print(json.dumps({"event": "prediction_complete", **result}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
