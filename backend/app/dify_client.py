from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

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

    async def run_workflow(
        self,
        workflow_id: str,
        inputs: dict,
        *,
        api_key: str | None = None,
        user: str = "system",
    ) -> dict:
        """
        Generic Dify workflow invocation. Dify routes by the API key in the
        Authorization header, not by workflow_id in the body — so callers
        for apps other than the default (api_key=None uses settings.DIFY_API_KEY)
        MUST pass that app's own key here, or Dify will silently run whatever
        app the default key belongs to.
        """
        payload = {
            "workflow_id": workflow_id,
            "inputs": inputs,
            "response_mode": "blocking",
            "user": user,
        }

        headers = self._headers
        if api_key is not None:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
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
                    f"Workflow {workflow_id} invocation failed: {exc}"
                ) from exc

    async def run_workflow_with_retry(
        self,
        workflow_id: str,
        inputs: dict,
        *,
        api_key: str | None = None,
        user: str = "system",
    ) -> dict:
        """AC3: same as run_workflow, but retries with linear backoff — see run_chatflow_with_retry."""
        return await self._with_retry(
            f"Workflow {workflow_id}",
            lambda: self.run_workflow(workflow_id, inputs, api_key=api_key, user=user),
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

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    f"{self._base_url}/chat-messages",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                raise DifyInvocationError(f"Chatflow invocation failed: {exc}") from exc

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