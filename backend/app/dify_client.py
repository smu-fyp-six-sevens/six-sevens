from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import httpx

from .config import settings

logger = logging.getLogger("classifier_pipeline.dify_client")


class DifyInvocationError(Exception):
    """Raised when the Classifier workflow could not be invoked or failed."""


def _response_body(exc: Exception) -> str:
    """
    httpx.HTTPStatusError's own str() is just "Client error '400 Bad
    Request' for url '...'" — it never includes Dify's actual response
    body, which is where the real reason lives (e.g. {"code":
    "invalid_param", "message": "Run failed: ..."}). Without this, every
    Dify 400 has needed a manual curl reproduction to find out why.
    RequestError (network-level failures) has no response to read.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    try:
        return f" | Dify response: {response.text[:500]}"
    except Exception:  # noqa: BLE001 — this is best-effort diagnostic text
        return ""


class DifyClient:
    def __init__(self) -> None:
        self._base_url = settings.DIFY_BASE_URL
        self._headers = {
            "Authorization": f"Bearer {settings.DIFY_API_KEY}",
            "Content-Type": "application/json",
        }
        # Caps simultaneous Classifier chatflow calls independently of how
        # many queue workers are running — see DIFY_CLASSIFIER_MAX_CONCURRENCY.
        self._chatflow_semaphore = asyncio.Semaphore(settings.DIFY_CLASSIFIER_MAX_CONCURRENCY)

    async def run_workflow(
        self,
        workflow_id: str,
        inputs: dict, 
        user: str,
    ) -> dict:
        """
        Generic Dify workflow invocation — any workflow_id/inputs shape.
        `user` is required by Dify's /workflows/run API (identifies the
        end-user); omitting it fails with "Arg user must be provided."
        Raises DifyInvocationError on any non-2xx response or network
        failure; callers decide how to handle that (retry, fail, etc).
        """
        payload = {
            "workflow_id": workflow_id,
            "inputs": inputs,
            "response_mode": "blocking",
            "user": user,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    f"{self._base_url}/workflows/run",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                raise DifyInvocationError(
                    f"Workflow {workflow_id} invocation failed: {exc}{_response_body(exc)}"
                ) from exc

    async def run_workflow_with_retry(self, workflow_id: str, inputs: dict, user: str) -> dict:
        """
        AC3: same as run_workflow, but retries up to
        settings.MAX_INVOCATION_RETRIES times (linear backoff) before giving
        up. This is the one place invocation retry lives — every caller that
        needs AC3 behavior (orchestrator's Intake Coordinator and Extraction
        calls, both the initial attempt and every escalation-loop
        re-attempt) should call this instead of run_workflow directly, so
        the retry policy can't drift out of sync between call sites.

        Raises DifyInvocationError, with the message noting the attempt
        count, once retries are exhausted.
        """
        return await self._with_retry(
            f"Workflow {workflow_id}",
            lambda: self.run_workflow(workflow_id, inputs, user),
        )

    async def run_chatflow(
        self,
        api_key: str,
        inputs: dict,
        query: str,
        user: str,
        *,
        files: list[dict] | None = None,
    ) -> dict:
        payload: dict = {
            "inputs": inputs,
            "query": query,
            "response_mode": "blocking",
            "user": user,
        }
        if files:
            payload["files"] = files

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async with self._chatflow_semaphore, httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    f"{self._base_url}/chat-messages",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                raise DifyInvocationError(
                    f"Chatflow invocation failed: {exc}{_response_body(exc)}"
                ) from exc

    async def run_chatflow_with_retry(
        self,
        api_key: str,
        inputs: dict,
        query: str,
        user: str,
        *,
        files: list[dict] | None = None,
    ) -> dict:
        return await self._with_retry(
            "Chatflow", lambda: self.run_chatflow(api_key, inputs, query, user, files=files)
        )

    async def _with_retry(self, label: str, call: Callable[[], Awaitable[dict]]) -> dict:
        last_error: DifyInvocationError | None = None
        for attempt in range(1, settings.MAX_INVOCATION_RETRIES + 1):
            try:
                return await call()
            except DifyInvocationError as exc:
                last_error = exc
                logger.warning(
                    "%s invocation attempt %d/%d failed: %s",
                    label, attempt, settings.MAX_INVOCATION_RETRIES, exc,
                )
                if attempt < settings.MAX_INVOCATION_RETRIES:
                    await asyncio.sleep(settings.RETRY_BACKOFF_SECONDS * attempt)

        raise DifyInvocationError(
            f"{label} invocation failed after "
            f"{settings.MAX_INVOCATION_RETRIES} attempts: {last_error}"
        ) from last_error


dify_client = DifyClient()