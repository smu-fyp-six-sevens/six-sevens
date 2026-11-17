"""
The load test (deploy/loadtest/README.md): many officers at once, each creating a case, uploading documents into it,
approving each once it's analysed, and waiting for detection and the case analysis. Against the stand-in app
(stand_ins.py) only: it refuses any URL but localhost unless --allow-remote is given. Prints a capacity report:
uploads a minute, and p50 / p95 / max seconds per stage. Standard library plus httpx (in the backend's packages).

    python deploy/loadtest/run.py --cases 10 --documents 3 --ramp 30
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from urllib.parse import urlparse

import httpx

# The smallest PDF the upload accepts (its bytes must match its type).
_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
)


class Timings:
    def __init__(self) -> None:
        self.stages: dict[str, list[float]] = {}
        self.errors: dict[str, int] = {}

    def add(self, stage: str, seconds: float) -> None:
        self.stages.setdefault(stage, []).append(seconds)

    def fail(self, what: str) -> None:
        self.errors[what] = self.errors.get(what, 0) + 1


def _pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p * (len(ordered) - 1)))]


async def _wait_for(client: httpx.AsyncClient, document_id: str, wanted: set[str], timeout: float) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get(f"/documents/{document_id}")
        if response.status_code == 200 and (status := response.json().get("status")) in wanted | {"failed", "cancelled"}:
            return status
        await asyncio.sleep(1)
    return None


async def _document(client: httpx.AsyncClient, document_id: str, uploaded: float, timings: Timings, timeout: float) -> None:
    status = await _wait_for(client, document_id, {"analyzed"}, timeout)
    if status != "analyzed":
        timings.fail(f"not analysed ({status or 'timed out'})")
        return
    timings.add("upload -> analysed", time.monotonic() - uploaded)
    approved = time.monotonic()
    response = await client.post(f"/documents/{document_id}/approve")
    if response.status_code >= 400:
        timings.fail(f"approve {response.status_code}")
        return
    timings.add("approve (request)", time.monotonic() - approved)
    status = await _wait_for(client, document_id, {"detected"}, timeout)
    if status != "detected":
        timings.fail(f"not detected ({status or 'timed out'})")
        return
    timings.add("approve -> detected", time.monotonic() - approved)


async def _officer(base: str, token: str, index: int, documents: int, timings: Timings, timeout: float) -> None:
    async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=60) as client:
        started = time.monotonic()
        response = await client.post("/cases", json={"company_name": f"Load test {index}"})
        if response.status_code >= 400:
            timings.fail(f"create case {response.status_code}")
            return
        timings.add("create case (request)", time.monotonic() - started)
        case_id = response.json()["id"]
        files = [("files", (f"doc-{index}-{n}.pdf", _PDF, "application/pdf")) for n in range(documents)]
        uploaded = time.monotonic()
        response = await client.post("/documents/upload-batch", data={"case_id": case_id}, files=files)
        if response.status_code >= 400:
            timings.fail(f"upload {response.status_code}")
            return
        timings.add("upload (request)", time.monotonic() - uploaded)
        ids = [r["document_id"] for r in response.json()["results"] if r.get("success")]
        timings.errors["upload item failed"] = timings.errors.get("upload item failed", 0) + documents - len(ids)
        await asyncio.gather(*(_document(client, i, uploaded, timings, timeout) for i in ids))
        # The analysis starts on its own once every document is detected.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = await client.get(f"/cases/{case_id}/analysis")
            if response.status_code == 200 and response.json().get("status") in ("complete", "failed"):
                timings.add("upload -> case analysed", time.monotonic() - uploaded)
                return
            await asyncio.sleep(2)
        timings.fail("case analysis timed out")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--cases", type=int, default=10, help="officers, one case each")
    parser.add_argument("--documents", type=int, default=3, help="documents per case")
    parser.add_argument("--ramp", type=float, default=30, help="seconds over which the officers start")
    parser.add_argument("--timeout", type=float, default=900, help="seconds a document or case may take")
    parser.add_argument("--password", default="loadtest")
    parser.add_argument("--allow-remote", action="store_true", help="aim at something other than localhost")
    args = parser.parse_args()
    if urlparse(args.url).hostname not in ("localhost", "127.0.0.1") and not args.allow_remote:
        print("Refusing a URL that isn't localhost: this test is for the stand-in app (stand_ins.py).")
        return 2

    async with httpx.AsyncClient(base_url=args.url, timeout=30) as client:
        response = await client.post("/auth/login", json={"username": "loadtest", "password": args.password})
        response.raise_for_status()
        token = response.json()["token"]

    timings = Timings()
    started = time.monotonic()

    async def staggered(index: int) -> None:
        await asyncio.sleep(args.ramp * index / max(1, args.cases))
        await _officer(args.url, token, index, args.documents, timings, args.timeout)

    await asyncio.gather(*(staggered(i) for i in range(args.cases)))
    elapsed = time.monotonic() - started

    total = args.cases * args.documents
    print(f"\n{args.cases} cases x {args.documents} documents ({total}) in {elapsed:.0f} s, "
          f"officers starting over {args.ramp:.0f} s")
    print(f"Uploads a minute: {total / (args.ramp / 60):.1f} offered" if args.ramp else "")
    print(f"{'stage':28} {'n':>4} {'p50':>7} {'p95':>7} {'max':>7}")
    for stage, values in timings.stages.items():
        print(f"{stage:28} {len(values):4} {statistics.median(values):7.1f} {_pct(values, 0.95):7.1f} {max(values):7.1f}")
    failures = {k: v for k, v in timings.errors.items() if v}
    print("Failures: " + (", ".join(f"{k} x{v}" for k, v in failures.items()) if failures else "none"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
