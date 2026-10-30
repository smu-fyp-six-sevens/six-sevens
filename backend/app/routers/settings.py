"""
App-wide settings: every officer reads them (the pages hide what's switched off), only an admin changes them.

finding_reviews_enabled: whether loan officers mark detection findings correct or wrong and report what detection
missed. On, a case's findings must be reviewed before it can be finalised; off, the review controls and the
precision and recall they give are hidden everywhere, the review endpoints refuse, and an analysed case can be
finalised without them.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import app_settings_repository, case_repository, case_stage
from ..auth import CurrentUser, is_admin, require_admin
from ..models import CaseStage
from ._dependencies import bad_gateway_on_error

router = APIRouter(prefix="/settings", tags=["settings"])
logger = logging.getLogger(__name__)


class AppSettings(BaseModel):
    finding_reviews_enabled: bool
    # Whether the signed-in officer can change these settings.
    is_admin: bool


class SetFindingReviewsRequest(BaseModel):
    enabled: bool


@router.get("", response_model=AppSettings)
async def get_settings(user: CurrentUser) -> AppSettings:
    enabled = await asyncio.to_thread(app_settings_repository.get_finding_reviews_enabled)
    return AppSettings(finding_reviews_enabled=enabled, is_admin=is_admin(user))


@router.put("/finding-reviews", response_model=AppSettings)
async def set_finding_reviews(request: SetFindingReviewsRequest, admin: str = Depends(require_admin)) -> AppSettings:
    """Turns finding reviews on or off for every officer. Admin only. Reviews already saved are kept either way."""
    with bad_gateway_on_error(
        "Failed to save the setting. Check Supabase table access.",
        "Failed to set finding reviews to %s", request.enabled,
    ):
        await asyncio.to_thread(app_settings_repository.set_finding_reviews_enabled, request.enabled, admin)
    logger.info("Finding reviews turned %s by %s", "on" if request.enabled else "off", admin)
    # An analysed case waits for its reviews only while they are on (case_stage.analysis_stage).
    moved = await asyncio.to_thread(_resync_analysed_cases)
    logger.info("%d analysed case(s) changed stage", moved)
    return AppSettings(finding_reviews_enabled=request.enabled, is_admin=True)


def _resync_analysed_cases() -> int:
    """Re-derives the stage of every case the setting decides between Pending Evaluation and Case Analysed."""
    stages = [CaseStage.PENDING_EVALUATION.value, CaseStage.CASE_ANALYSED.value]
    moved = 0
    for case in case_repository.list_matching_cases(None, stages):
        try:
            if case_stage.sync_case_stage(case.id) is not None:
                moved += 1
        except Exception:
            logger.exception("Failed to update the stage of case %s", case.id)
    return moved
