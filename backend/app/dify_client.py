from __future__ import annotations

import httpx

from .config import settings


class DifyInvocationError(Exception):
    """Raised when the Classifier workflow could not be invoked or failed."""


class DifyClient:
    def __init__(self) -> None:
        self._base_url = settings.DIFY_BASE_URL
        self._headers = {
            "Authorization": f"Bearer {settings.DIFY_API_KEY}",
            "Content-Type": "application/json",
        }

    async def invoke_classifier_workflow(self, document_id: str, document_info: dict) -> dict:
        """
        AC2: invokes the Classifier Agent's workflow with document_id and
        document_info as mandatory inputs. Raises DifyInvocationError on any
        non-2xx response or network failure so the caller's retry logic
        (AC3) can handle it.
        """
        if not document_id or not document_info:
            raise ValueError("document_id and document_info are mandatory inputs")

        payload = {
            "workflow_id": settings.DIFY_CLASSIFIER_WORKFLOW_ID,
            "inputs": {
                "document_id": document_id,
                "document_info": document_info,
            },
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
                    f"Classifier workflow invocation failed for document {document_id}: {exc}"
                ) from exc


dify_client = DifyClient()