"""Singapore's date and time. Cloud Run runs in UTC, which is yesterday in Singapore until 8am."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

SINGAPORE = ZoneInfo("Asia/Singapore")


def now_sg() -> datetime:
    return datetime.now(SINGAPORE)


def today_sg() -> date:
    return now_sg().date()
