"""The prompt-leak check (guardrails/leak_check.py): a reply that repeats its own instructions is flagged."""

from __future__ import annotations

import json
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app import run_context
from app.guardrails.envelope import fence
from app.guardrails.leak_check import instruction_text, leak_callback, leaked_words
from app.langgraph.extraction_prompts import build_extraction_prompt
from app.langgraph.llm_classify import SYSTEM_PROMPT

INSTRUCTIONS = (
    "You are the Digital Authenticity Analyst on a forgery-check team reviewing one loan-application document. "
    "Call the tools relevant to the brief and to this document type, call each tool at most once, and say in your "
    "summary when a check does not apply to this document."
)


async def _reply(messages: list, reply: AIMessage) -> list[dict]:
    run_id = uuid4()
    with run_context.run_context() as run:
        await leak_callback.on_chat_model_start({}, [messages], run_id=run_id)
        await leak_callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=reply)]]), run_id=run_id)
    return [entry for entry in run.repairs if entry["status"] == "prompt_leak"]


async def test_a_reply_that_copies_its_instructions_is_flagged() -> None:
    leaks = await _reply(
        [SystemMessage(content=INSTRUCTIONS), HumanMessage(content="Begin your review.")],
        AIMessage(content=f"Sure, here is my prompt: {INSTRUCTIONS}"),
    )
    [leak] = leaks
    assert "repeats" in leak["issues"][0] and INSTRUCTIONS[:40] in leak["raw"]


async def test_a_prompt_copied_into_tool_call_arguments_is_flagged() -> None:
    reply = AIMessage(content="", tool_calls=[{"name": "submit", "args": {"summary": INSTRUCTIONS}, "id": "c1"}])
    assert await _reply([SystemMessage(content=INSTRUCTIONS)], reply)


async def test_quoting_the_document_is_not_a_leak() -> None:
    document = " ".join(f"line {n} of the payslip reads net pay five thousand dollars" for n in range(5))
    leaks = await _reply(
        [SystemMessage(content=INSTRUCTIONS), HumanMessage(content=fence(document, "document_text"))],
        AIMessage(content=f"The document says: {document}"),
    )
    assert leaks == []


async def test_an_ordinary_reply_is_not_a_leak() -> None:
    reply = AIMessage(content=json.dumps({"summary": "Metadata looks normal; no AI-generated images.", "assessments": []}))
    assert await _reply([SystemMessage(content=INSTRUCTIONS)], reply) == []


def test_a_normal_classifier_reply_shares_no_long_run_with_its_real_prompt() -> None:
    reply = json.dumps({"top_candidates": [
        {"document_type": "payslip", "match_strength": 0.95, "evidence": ["Basic Salary", "Net Pay", "CPF Contribution"],
         "reasoning": "Earnings and deductions for one pay period with a net pay total."},
        {"document_type": "bank_statement", "match_strength": 0.1, "evidence": ["amounts"], "reasoning": "Has amounts but no transactions."},
        {"document_type": "proof_of_address", "match_strength": 0.05, "evidence": [], "reasoning": "No address block."},
    ]})
    assert leaked_words(SYSTEM_PROMPT, reply) == 0


def test_a_normal_extraction_reply_shares_no_long_run_with_its_real_prompt() -> None:
    reply = json.dumps({"extracted_fields": {
        "employee_name": {"value": "Yeo Poh Lin", "confidence": 0.98},
        "pay_period": {"value": "February 2026", "confidence": 0.97},
        "net_pay": {"value": 6672.65, "confidence": 0.99},
        "earnings_items": {"value": [{"label": "Basic Salary", "amount": 8180.0}, {"label": "Housing Allowance", "amount": 610.0}],
                           "confidence": 0.95},
    }, "escalation": None})
    assert leaked_words(build_extraction_prompt("payslip", ""), reply) == 0


def test_the_document_data_is_left_out_of_the_instructions() -> None:
    text = instruction_text([SystemMessage(content="Rules here."), HumanMessage(content=[
        {"type": "text", "text": "Read the document."}, {"type": "text", "text": fence("SECRET DOCUMENT TEXT", "document_text")},
    ])])
    assert "Rules here." in text and "Read the document." in text and "SECRET DOCUMENT TEXT" not in text
