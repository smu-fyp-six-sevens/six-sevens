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