from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.dify_client import DifyClient, DifyInvocationError, _response_body


def _mock_async_client(post_return=None, post_side_effect=None) -> MagicMock:
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    else:
        mock_client.post = AsyncMock(return_value=post_return)
    return mock_client


@pytest.mark.asyncio
async def test_run_workflow_success_returns_json_body() -> None:
    client = DifyClient()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"data": {"outputs": {"ok": True}}}

    with patch("app.dify_client.httpx.AsyncClient") as mock_async_client_cls:
        mock_async_client_cls.return_value = _mock_async_client(post_return=mock_response)

        result = await client.run_workflow("wf-1", {"a": 1}, user="test-user", api_key="app-key")

        assert result == {"data": {"outputs": {"ok": True}}}
        posted_kwargs = mock_async_client_cls.return_value.post.call_args
        assert posted_kwargs.args[0].endswith("/workflows/run")
        assert posted_kwargs.kwargs["json"]["workflow_id"] == "wf-1"
        assert posted_kwargs.kwargs["json"]["inputs"] == {"a": 1}
        assert posted_kwargs.kwargs["json"]["response_mode"] == "blocking"
        assert posted_kwargs.kwargs["json"]["user"] == "test-user"


@pytest.mark.asyncio
async def test_run_workflow_raises_dify_error_on_http_status_error() -> None:
    client = DifyClient()
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "bad status", request=MagicMock(), response=MagicMock()
    )

    with patch("app.dify_client.httpx.AsyncClient") as mock_async_client_cls:
        mock_async_client_cls.return_value = _mock_async_client(post_return=mock_response)

        with pytest.raises(DifyInvocationError):
            await client.run_workflow("wf-1", {"a": 1}, user="test-user", api_key="app-key")


@pytest.mark.asyncio
async def test_run_workflow_raises_dify_error_on_request_error() -> None:
    client = DifyClient()

    with patch("app.dify_client.httpx.AsyncClient") as mock_async_client_cls:
        mock_async_client_cls.return_value = _mock_async_client(
            post_side_effect=httpx.ConnectError("connection refused")
        )

        with pytest.raises(DifyInvocationError):
            await client.run_workflow("wf-1", {"a": 1}, user="test-user", api_key="app-key")


@pytest.mark.asyncio
async def test_run_workflow_with_retry_succeeds_first_try() -> None:
    client = DifyClient()
    with (
        patch.object(client, "run_workflow", new=AsyncMock(return_value={"ok": True})) as mock_run,
        patch("app.dify_client.asyncio.sleep", new=AsyncMock()) as mock_sleep,
    ):
        result = await client.run_workflow_with_retry(
            "wf-1", {"a": 1}, user="test-user", api_key="app-key"
        )

        assert result == {"ok": True}
        assert mock_run.await_count == 1
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_run_workflow_with_retry_succeeds_after_transient_failures() -> None:
    client = DifyClient()
    with (
        patch.object(
            client,
            "run_workflow",
            new=AsyncMock(
                side_effect=[
                    DifyInvocationError("blip 1"),
                    DifyInvocationError("blip 2"),
                    {"ok": True},
                ]
            ),
        ) as mock_run,
        patch("app.dify_client.asyncio.sleep", new=AsyncMock()) as mock_sleep,
    ):
        result = await client.run_workflow_with_retry(
            "wf-1", {"a": 1}, user="test-user", api_key="app-key"
        )

        assert result == {"ok": True}
        assert mock_run.await_count == 3
        # backoff happens between attempts, not after the final success
        assert mock_sleep.await_count == 2


@pytest.mark.asyncio
async def test_run_workflow_with_retry_raises_after_exhausting_all_attempts() -> None:
    client = DifyClient()
    with (
        patch.object(
            client,
            "run_workflow",
            new=AsyncMock(side_effect=DifyInvocationError("persistent failure")),
        ) as mock_run,
        patch("app.dify_client.asyncio.sleep", new=AsyncMock()) as mock_sleep,
    ):
        with pytest.raises(DifyInvocationError) as exc_info:
            await client.run_workflow_with_retry(
                "wf-1", {"a": 1}, user="test-user", api_key="app-key"
            )

        assert mock_run.await_count == 3
        # backoff happens between attempts 1->2 and 2->3, not after the last one
        assert mock_sleep.await_count == 2
        assert "after 3 attempts" in str(exc_info.value)
        assert "persistent failure" in str(exc_info.value)


@pytest.mark.asyncio
async def test_run_chatflow_success_returns_json_body() -> None:
    client = DifyClient()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"answer": '{"document_type": "payslip"}'}

    with patch("app.dify_client.httpx.AsyncClient") as mock_async_client_cls:
        mock_async_client_cls.return_value = _mock_async_client(post_return=mock_response)

        result = await client.run_chatflow(
            "chat-api-key",
            {"document_id": "doc-1"},
            "Classify this document",
            "doc-1",
            files=[{"type": "document", "transfer_method": "remote_url", "url": "https://x/doc.pdf"}],
        )

        assert result == {"answer": '{"document_type": "payslip"}'}
        posted_kwargs = mock_async_client_cls.return_value.post.call_args
        assert posted_kwargs.args[0].endswith("/chat-messages")
        assert posted_kwargs.kwargs["headers"]["Authorization"] == "Bearer chat-api-key"
        assert posted_kwargs.kwargs["json"]["inputs"] == {"document_id": "doc-1"}
        assert posted_kwargs.kwargs["json"]["query"] == "Classify this document"
        assert posted_kwargs.kwargs["json"]["user"] == "doc-1"
        assert posted_kwargs.kwargs["json"]["response_mode"] == "blocking"
        assert posted_kwargs.kwargs["json"]["files"] == [
            {"type": "document", "transfer_method": "remote_url", "url": "https://x/doc.pdf"}
        ]


@pytest.mark.asyncio
async def test_run_chatflow_omits_files_key_when_none_given() -> None:
    client = DifyClient()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"answer": "{}"}

    with patch("app.dify_client.httpx.AsyncClient") as mock_async_client_cls:
        mock_async_client_cls.return_value = _mock_async_client(post_return=mock_response)

        await client.run_chatflow("chat-api-key", {}, "Classify this document", "doc-1")

        posted_kwargs = mock_async_client_cls.return_value.post.call_args
        assert "files" not in posted_kwargs.kwargs["json"]


@pytest.mark.asyncio
async def test_run_chatflow_includes_conversation_id_when_given() -> None:
    client = DifyClient()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"answer": "{}", "conversation_id": "conv-1"}

    with patch("app.dify_client.httpx.AsyncClient") as mock_async_client_cls:
        mock_async_client_cls.return_value = _mock_async_client(post_return=mock_response)

        await client.run_chatflow(
            "chat-api-key",
            {},
            "Classify this document",
            "doc-1",
            conversation_id="conv-1",
        )

        posted_kwargs = mock_async_client_cls.return_value.post.call_args
        assert posted_kwargs.kwargs["json"]["conversation_id"] == "conv-1"


@pytest.mark.asyncio
async def test_run_chatflow_omits_conversation_id_when_none_given() -> None:
    client = DifyClient()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"answer": "{}"}

    with patch("app.dify_client.httpx.AsyncClient") as mock_async_client_cls:
        mock_async_client_cls.return_value = _mock_async_client(post_return=mock_response)

        await client.run_chatflow("chat-api-key", {}, "Classify this document", "doc-1")

        posted_kwargs = mock_async_client_cls.return_value.post.call_args
        assert "conversation_id" not in posted_kwargs.kwargs["json"]


@pytest.mark.asyncio
async def test_run_chatflow_raises_dify_error_on_http_status_error() -> None:
    client = DifyClient()
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "bad status", request=MagicMock(), response=MagicMock()
    )

    with patch("app.dify_client.httpx.AsyncClient") as mock_async_client_cls:
        mock_async_client_cls.return_value = _mock_async_client(post_return=mock_response)

        with pytest.raises(DifyInvocationError):
            await client.run_chatflow("chat-api-key", {}, "Classify this document", "doc-1")


@pytest.mark.asyncio
async def test_run_chatflow_raises_dify_error_on_request_error() -> None:
    client = DifyClient()

    with patch("app.dify_client.httpx.AsyncClient") as mock_async_client_cls:
        mock_async_client_cls.return_value = _mock_async_client(
            post_side_effect=httpx.ConnectError("connection refused")
        )

        with pytest.raises(DifyInvocationError):
            await client.run_chatflow("chat-api-key", {}, "Classify this document", "doc-1")


@pytest.mark.asyncio
async def test_run_chatflow_with_retry_succeeds_first_try() -> None:
    client = DifyClient()
    with (
        patch.object(client, "run_chatflow", new=AsyncMock(return_value={"answer": "{}"})) as mock_run,
        patch("app.dify_client.asyncio.sleep", new=AsyncMock()) as mock_sleep,
    ):
        result = await client.run_chatflow_with_retry(
            "chat-api-key", {}, "Classify this document", "doc-1"
        )

        assert result == {"answer": "{}"}
        assert mock_run.await_count == 1
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_run_chatflow_with_retry_raises_after_exhausting_all_attempts() -> None:
    client = DifyClient()
    with (
        patch.object(
            client,
            "run_chatflow",
            new=AsyncMock(side_effect=DifyInvocationError("persistent failure")),
        ) as mock_run,
        patch("app.dify_client.asyncio.sleep", new=AsyncMock()) as mock_sleep,
    ):
        with pytest.raises(DifyInvocationError) as exc_info:
            await client.run_chatflow_with_retry("chat-api-key", {}, "Classify this document", "doc-1")

        assert mock_run.await_count == 3
        assert mock_sleep.await_count == 2
        assert "after 3 attempts" in str(exc_info.value)
        assert "persistent failure" in str(exc_info.value)


def test_response_body_notes_timeout_when_no_response_available() -> None:
    # httpx.TimeoutException's own str() is often empty, and a client-side
    # timeout never gets a response object to read a body from — this is
    # the one case that still needs to say *something* useful.
    exc = httpx.ConnectTimeout("timed out")

    assert _response_body(exc) == " (request timed out)"


def test_response_body_returns_empty_string_for_non_timeout_request_error() -> None:
    exc = httpx.ConnectError("connection refused")

    assert _response_body(exc) == ""


def test_response_body_includes_response_text_when_present() -> None:
    exc = Exception("bad request")
    exc.response = MagicMock(text='{"code": "invalid_param", "message": "Run failed"}')

    assert _response_body(exc) == ' | Dify response: {"code": "invalid_param", "message": "Run failed"}'


def test_response_body_returns_empty_string_when_reading_response_text_fails() -> None:
    class _ExplodingResponse:
        @property
        def text(self) -> str:
            raise RuntimeError("body already consumed")

    exc = Exception("bad request")
    exc.response = _ExplodingResponse()

    assert _response_body(exc) == ""
