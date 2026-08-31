from __future__ import annotations

import asyncio
import logging

import httpx

from .config import settings

logger = logging.getLogger("classifier_pipeline.dify_client")


class DifyInvocationError(Exception):
    """Raised when the Classifier workflow could not be invoked or failed."""


class DifyClient:
    def __init__(self) -> None:
        self._base_url = settings.DIFY_BASE_URL
        self._headers = {
            "Authorization": f"Bearer {settings.DIFY_API_KEY}",
            "Content-Type": "application/json",
        }

    async def run_workflow(self, workflow_id: str, inputs: dict) -> dict:
        """
        Generic Dify workflow invocation — any workflow_id/inputs shape.
        Raises DifyInvocationError on any non-2xx response or network
        failure; callers decide how to handle that (retry, fail, etc).
        """
        payload = {
            "workflow_id": workflow_id,
            "inputs": inputs,
            "response_mode": "blocking",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    f"{self._base_url}/workflows/run",
                    headers=self._headers,
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                raise DifyInvocationError(
                    f"Workflow {workflow_id} invocation failed: {exc}"
                ) from exc

    async def run_workflow_with_retry(self, workflow_id: str, inputs: dict) -> dict:
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
        last_error: DifyInvocationError | None = None
        for attempt in range(1, settings.MAX_INVOCATION_RETRIES + 1):
            try:
                return await self.run_workflow(workflow_id, inputs)
            except DifyInvocationError as exc:
                last_error = exc
                logger.warning(
                    "Workflow %s invocation attempt %d/%d failed: %s",
                    workflow_id, attempt, settings.MAX_INVOCATION_RETRIES, exc,
                )
                if attempt < settings.MAX_INVOCATION_RETRIES:
                    await asyncio.sleep(settings.RETRY_BACKOFF_SECONDS * attempt)

        raise DifyInvocationError(
            f"Workflow {workflow_id} invocation failed after "
            f"{settings.MAX_INVOCATION_RETRIES} attempts: {last_error}"
        ) from last_error

    async def invoke_classifier_workflow(self, document_id: str, document_info: dict) -> dict:
        """
        Invokes the Classifier Agent's workflow with document_id and
        document_info as mandatory inputs. Superseded by orchestrator.py's
        use of run_workflow() for the Intake Coordinator/Extraction flow —
        kept here for any caller still using the single-workflow shape.
        """
        if not document_id or not document_info:
            raise ValueError("document_id and document_info are mandatory inputs")

        return await self.run_workflow(
            settings.DIFY_CLASSIFIER_WORKFLOW_ID,
            {"document_id": document_id, "document_info": document_info},
        )


dify_client = DifyClient()