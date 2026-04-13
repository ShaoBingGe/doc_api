"""
Usage statistics endpoint.

GET /api/v1/usage/stats   — aggregate traffic data for dashboard
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.models.usage_record import UsageRecord

router = APIRouter(prefix="/usage", tags=["Usage & Traffic"])


class DailyCount(BaseModel):
    date: str
    count: int


class TopApi(BaseModel):
    api_code: str
    total_calls: int
    success_rate: float


class UsageStatsResponse(BaseModel):
    total_calls: int = 0
    calls_today: int = 0
    calls_this_month: int = 0
    success_rate: float = 0.0
    avg_latency_ms: float = 0.0
    calls_by_day: list[DailyCount] = []
    top_apis: list[TopApi] = []


@router.get("/stats", response_model=UsageStatsResponse)
def get_usage_stats(
    time_range: str = Query(default="7d", alias="range", description="today | 7d | 30d"),
    db: Session = Depends(get_db),
) -> UsageStatsResponse:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if time_range == "today":
        since = today_start
    elif time_range == "30d":
        since = now - timedelta(days=30)
    else:
        since = now - timedelta(days=7)

    base_q = db.query(UsageRecord).filter(UsageRecord.created_at >= since)

    total_calls = base_q.count()
    if total_calls == 0:
        return UsageStatsResponse()

    success_count = base_q.filter(UsageRecord.status_code < 400).count()
    success_rate = round(success_count / total_calls * 100, 1) if total_calls else 0.0

    avg_latency = db.query(func.avg(UsageRecord.latency_ms)).filter(
        UsageRecord.created_at >= since
    ).scalar() or 0.0

    calls_today = db.query(UsageRecord).filter(UsageRecord.created_at >= today_start).count()
    calls_this_month = db.query(UsageRecord).filter(UsageRecord.created_at >= month_start).count()

    # Daily breakdown
    days = 7 if time_range == "7d" else (30 if time_range == "30d" else 1)
    calls_by_day: list[DailyCount] = []
    for i in range(days, -1, -1):
        day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        count = db.query(UsageRecord).filter(
            UsageRecord.created_at >= day_start,
            UsageRecord.created_at < day_end,
        ).count()
        calls_by_day.append(DailyCount(date=day_start.strftime("%Y-%m-%d"), count=count))

    # Top APIs
    top_rows = (
        db.query(
            UsageRecord.api_code,
            func.count(UsageRecord.id).label("total"),
            func.sum(func.cast(UsageRecord.status_code < 400, type_=db.bind.dialect.type_descriptor(type(1)) if False else None)).label("ok"),
        )
        .filter(UsageRecord.created_at >= since)
        .group_by(UsageRecord.api_code)
        .order_by(func.count(UsageRecord.id).desc())
        .limit(10)
        .all()
    ) if False else []

    # Simpler top APIs query
    top_api_rows = (
        db.query(UsageRecord.api_code, func.count(UsageRecord.id).label("cnt"))
        .filter(UsageRecord.created_at >= since)
        .group_by(UsageRecord.api_code)
        .order_by(func.count(UsageRecord.id).desc())
        .limit(10)
        .all()
    )

    top_apis = []
    for row in top_api_rows:
        code, cnt = row
        ok = db.query(UsageRecord).filter(
            UsageRecord.api_code == code,
            UsageRecord.created_at >= since,
            UsageRecord.status_code < 400,
        ).count()
        top_apis.append(TopApi(
            api_code=code,
            total_calls=cnt,
            success_rate=round(ok / cnt * 100, 1) if cnt else 0.0,
        ))

    return UsageStatsResponse(
        total_calls=total_calls,
        calls_today=calls_today,
        calls_this_month=calls_this_month,
        success_rate=success_rate,
        avg_latency_ms=round(float(avg_latency), 1),
        calls_by_day=calls_by_day,
        top_apis=top_apis,
    )
