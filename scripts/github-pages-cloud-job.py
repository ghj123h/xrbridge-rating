"""Run a persisted cloud publication job from a GitHub Actions worker.

The initiating client only dispatches the workflow. This runner holds the cloud
execution connection, and can resume the same job after a transport failure.
No business rows or publication packages are downloaded by this script.
"""
from __future__ import annotations

import argparse
import gzip
from http.client import HTTPException
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

JOB_ID = re.compile(r"[a-f0-9]{32}\Z")
VERSION = re.compile(r"[a-f0-9]{64}\Z")
RUN_ID = re.compile(r"[1-9][0-9]*\Z")
MAX_RESPONSE = 2 * 1024 * 1024
TRANSIENT_REASONS = frozenset((
    "DATABASE_BUSY", "NETWORK_DISCONNECTED", "TRANSIENT_REMOTE_NODE", "DATABASE_RESET",
))
EXECUTION_RESUME_REASONS = TRANSIENT_REASONS | frozenset(("SUBREQUEST_LIMIT",))
SAFE_REASONS = TRANSIENT_REASONS | frozenset((
    "SUBREQUEST_LIMIT", "RESOURCE_LIMIT", "QUERY_TIMEOUT", "QUERY_INTERRUPTED", "SQL_SYNTAX", "UNKNOWN",
))


class JobError(RuntimeError):
    def __init__(self, code, *, http_status=None, reason=None, retryable=False):
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.reason = reason if reason in SAFE_REASONS else None
        self.retryable = retryable


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def emit(event, **values):
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


def validate_identity(value, *, job_id, version, mode, source_build_id=None):
    if (not isinstance(value, dict) or value.get("job_id") != job_id
            or value.get("publication_version") != version or value.get("mode") != mode):
        raise JobError("cloud_job_identity_mismatch")
    if source_build_id is not None and value.get("source_build_id") != source_build_id:
        raise JobError("cloud_job_identity_mismatch")
    if value.get("status") not in ("queued", "running", "complete", "failed"):
        raise JobError("cloud_job_status_invalid")
    return value


def completed(value, *, mode):
    result = value.get("result")
    if (value.get("status") != "complete" or not isinstance(result, dict)
            or type(result.get("completed_buckets")) is not int or result["completed_buckets"] != 128):
        raise JobError("cloud_job_result_incomplete")
    if mode in ("release", "validate"):
        if (result.get("status") != "ready" or result.get("phase") != "ready"
                or type(result.get("indexed_buckets")) is not int or result["indexed_buckets"] != 128):
            raise JobError("cloud_release_not_ready")
    return value


def safe_summary(value):
    result = value.get("result", {})
    progress = {}
    for key in ("completed_buckets", "indexed_buckets", "completed_avatars", "avatar_files", "packages"):
        if type(result.get(key)) is int:
            progress[key] = result[key]
    if result.get("phase") in ("buckets", "index", "avatars", "finalize", "ready"):
        progress["phase"] = result["phase"]
    summary = {"job_id": value["job_id"], "publication_version": value["publication_version"],
               "mode": value["mode"], "status": value["status"], "result": progress}
    if type(value.get("runner_attempts")) is int:
        summary["runner_attempts"] = value["runner_attempts"]
    if isinstance(value.get("source_build_id"), str) and JOB_ID.fullmatch(value["source_build_id"]):
        summary["source_build_id"] = value["source_build_id"]
    return summary


class CloudJobClient:
    def __init__(self, site_url, job_token, service_token, *, opener=None, sleep=time.sleep, output=emit):
        parsed = urlsplit(site_url or "")
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise JobError("clean_cloud_site_url_required")
        if not job_token or not service_token:
            raise JobError("cloud_job_credentials_required")
        self.endpoint = site_url.rstrip("/") + "/api/admin/github-publication/jobs"
        self.headers = {"X-Publication-Job-Authorization": "Bearer " + job_token,
                        "OAI-Sites-Authorization": "Bearer " + service_token,
                        "Content-Type": "application/json", "Accept-Encoding": "identity"}
        self.opener = opener if opener is not None else build_opener(NoRedirect())
        self.sleep, self.output = sleep, output

    def _request(self, payload=None, *, job_id=None):
        headers = dict(self.headers)
        if payload is None:
            url = self.endpoint + "?" + urlencode({"job_id": job_id})
            body, method = None, "GET"
        else:
            url = self.endpoint + "/run"
            body = gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"), mtime=0)
            headers["Content-Encoding"] = "gzip"
            method = "POST"
        try:
            with self.opener.open(Request(url, data=body, headers=headers, method=method), timeout=120) as response:
                if response.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
                    raise JobError("cloud_job_response_invalid")
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise JobError("cloud_job_response_too_large")
        except HTTPError as error:
            status = error.code
            error.close()
            raise JobError("cloud_job_http_failed", http_status=status,
                           retryable=status in (429, 500, 502, 503, 504)) from None
        except (URLError, HTTPException, OSError):
            raise JobError("cloud_job_transport_failed", retryable=True) from None
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise JobError("cloud_job_response_invalid", retryable=True) from None
        return value

    def status(self, *, job_id, version, mode, source_build_id=None):
        return validate_identity(self._request(job_id=job_id), job_id=job_id, version=version, mode=mode,
                                 source_build_id=source_build_id)

    def run(self, *, job_id, version, mode, run_id, source_build_id=None):
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise JobError("invalid_cloud_job_id")
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise JobError("invalid_publication_version")
        if mode not in ("buckets", "release", "validate"):
            raise JobError("invalid_cloud_job_mode")
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise JobError("invalid_github_run_id")
        if source_build_id is not None:
            if mode != "validate":
                raise JobError("source_build_id_requires_validate")
            if not isinstance(source_build_id, str) or not JOB_ID.fullmatch(source_build_id):
                raise JobError("invalid_source_build_id")
        payload = {"job_id": job_id, "version": version, "mode": mode, "run_id": run_id}
        if source_build_id is not None:
            payload["source_build_id"] = source_build_id
        for attempt in range(1, 7):
            self.output("cloud_pages_job_execution_attempt", job_id=job_id, attempt=attempt, total_attempts=6)
            try:
                value = validate_identity(self._request(payload), job_id=job_id, version=version, mode=mode,
                                          source_build_id=source_build_id)
                if value["status"] == "complete":
                    return {**completed(value, mode=mode), "runner_attempts": attempt}
                if value["status"] != "failed":
                    raise JobError("cloud_job_result_incomplete")
                error = value.get("error")
                reason = error.get("reason") if isinstance(error, dict) else None
                raise JobError("cloud_job_execution_failed", reason=reason,
                               retryable=reason in EXECUTION_RESUME_REASONS)
            except JobError as failure:
                if not failure.retryable:
                    raise
                # A failed connection does not prove that execution failed. Read
                # the same job before resubmitting; the server owns checkpoints.
                try:
                    state = self.status(job_id=job_id, version=version, mode=mode,
                                        source_build_id=source_build_id)
                except JobError as status_failure:
                    if status_failure.http_status not in (404, 429, 500, 502, 503, 504) and not status_failure.retryable:
                        raise
                    state = None
                if state is not None:
                    if state["status"] == "complete":
                        return {**completed(state, mode=mode), "runner_attempts": attempt}
                    if state["status"] == "failed":
                        error = state.get("error")
                        reason = error.get("reason") if isinstance(error, dict) else None
                        if reason not in EXECUTION_RESUME_REASONS:
                            raise JobError("cloud_job_execution_failed", reason=reason) from None
                if attempt == 6:
                    raise failure
                delay = (5, 15, 30, 30, 30)[attempt - 1]
                event = {"job_id": job_id, "failed_attempt": attempt,
                         "next_attempt": attempt + 1, "delay_seconds": delay, "code": failure.code}
                if failure.http_status is not None:
                    event["http_status"] = failure.http_status
                if failure.reason is not None:
                    event["reason"] = failure.reason
                self.output("cloud_pages_job_resume", **event)
                self.sleep(delay)
        raise JobError("cloud_job_execution_failed")


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise JobError("invalid_arguments")


def main(argv=None):
    parser = Parser(description="Execute one persisted cloud publication job in a background runner.")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--source-build-id")
    try:
        args = parser.parse_args(argv)
        client = CloudJobClient(os.environ.get("CLOUD_SITE_URL"), os.environ.get("PUBLICATION_JOB_TOKEN"),
                                os.environ.get("SITES_SERVICE_ACCESS_TOKEN"))
        result = client.run(job_id=args.job_id, version=args.version, mode=args.mode,
                            run_id=os.environ.get("GITHUB_RUN_ID"), source_build_id=args.source_build_id)
    except JobError as error:
        details = {"code": error.code}
        if error.http_status is not None:
            details["http_status"] = error.http_status
        if error.reason is not None:
            details["reason"] = error.reason
        emit("cloud_pages_job_failed", **details)
        return 1
    emit("cloud_pages_job_complete", **safe_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
