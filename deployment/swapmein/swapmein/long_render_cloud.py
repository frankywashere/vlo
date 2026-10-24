"""Request-billed CPU work for the isolated long-render preview.

This client cannot scale or invoke either production preparation worker.
"""
from contextlib import contextmanager
from dataclasses import replace
from urllib.parse import urlsplit

from google.auth.transport.requests import Request
from google.oauth2 import id_token

from .cloud import Cloud
from .diagnostics import timed_stage
from .errors import PublicError


def isolated_run_url(url, service, settings):
    """Accept only this project's two canonical Cloud Run service hosts."""
    try:
        parsed = urlsplit(url)
        allowed = {f"{service}-63lqoskl5q-uk.a.run.app",
                   f"{service}-{settings.project_number}.{settings.region}.run.app"}
        return (parsed.scheme == "https" and parsed.hostname in allowed
                and not parsed.username and not parsed.password and parsed.port in {None, 443}
                and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment)
    except (ValueError, TypeError):
        return False


class LongCloud(Cloud):
    def _admin_url(self, service):
        raise PublicError("Self-hosted workers are unavailable in the isolated one-minute test.", 409)

    def enqueue(self, kind, job_id, *, task_token=None, delay_seconds=0):
        if kind not in {"long-prepare", "long-review", "long-generate", "long-poll", "long-assemble", "long-comparison"}:
            raise ValueError("Unknown long-render task")
        queue = (self.settings.render_task_queue if kind in {"long-generate", "long-poll"}
                 else self.settings.task_queue)
        if self.settings.reliable_tasks and (not isolated_run_url(self.settings.task_worker_url,
                "recastfx-long-worker", self.settings) or queue != "recastfx-long-work"):
            raise PublicError("The isolated background worker is not configured.", 503)
        scoped = Cloud(replace(self.settings, task_queue=queue), self.storage, self.tasks, self.http)
        return scoped.enqueue(kind, job_id, task_token=task_token, delay_seconds=delay_seconds)

    @contextmanager
    def worker(self, service):
        if service != "audio":
            raise PublicError("The test currently supports Replicate SAM and depth only.", 409)
        self.check_cap()
        url = self.settings.audio_url
        if not isolated_run_url(url, "recastfx-long-cpu", self.settings):
            raise PublicError("The isolated test CPU service is not configured.", 503)
        token = id_token.fetch_id_token(Request(), url)
        headers = {"Authorization": "Bearer " + token}

        def invoke(endpoint, body, timeout=600):
            if endpoint not in {"/compose", "/long-audio"}:
                raise PublicError("This operation is unavailable on the isolated CPU worker.", 409)
            with timed_stage("long_cpu_operation", job_id=self.job_id, operation=endpoint[1:]):
                response = self.http.post(url + endpoint, json=body, headers=headers,
                                          timeout=min(1200, timeout))
                if not response.is_success:
                    raise PublicError("The test CPU worker could not finish this step. Saved inputs are retained.", 502)
                return response.json()

        # The dedicated service uses request-based CPU billing, min zero, max
        # one, concurrency one. No shared worker lease or MANUAL scaler exists.
        yield invoke
