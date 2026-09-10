# backend/debug_dify_raw.py
import asyncio
import httpx
from app.config import settings
from app.extraction_prompts import build_extraction_prompt
from app import storage_service

async def main():
    raw_text = "EMPLOYEE PAYSLIP\nEmployee: Jane Tan\nNet pay: SGD 5,200.00"
    prompt = build_extraction_prompt("payslip", raw_text)

    storage_path = "hsu-test-case-1/8055be6d-6b50-4988-a9d1-23ea3c38853b_test_below_threshold.pdf"

    signed_url = storage_service.create_signed_url(
        storage_path,
        settings.SIGNED_URL_EXPIRY_SECONDS,
        "test.pdf",
    )
    print("SIGNED URL:", signed_url)

    payload = {
        "workflow_id": settings.DIFY_EXTRACTION_WORKFLOW_ID,
        "inputs": {
            "extraction_prompt": prompt,
            "raw_text": raw_text,
            "input_file": {
                "type": "document",
                "transfer_method": "remote_url",
                "url": signed_url,
            },
        },
        "response_mode": "blocking",
        "user": "debug-test",
    }

    headers = {
        "Authorization": f"Bearer {settings.DIFY_EXTRACTION_API_KEY}",
        "Content-Type": "application/json",
    }

    print("PAYLOAD BEING SENT:", payload)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{settings.DIFY_BASE_URL}/workflows/run",
            headers=headers,
            json=payload,
        )
        print("STATUS:", response.status_code)
        print("BODY:", response.text)

asyncio.run(main())