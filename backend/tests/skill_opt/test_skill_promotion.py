"""P4 采收（skill_promotion）纯函数单测 —— SKT-P 系列。

只测确定性的 `extract_candidates_from_rows`（无 DB）：分组、跨租户去重、>5 门、
空反馈过滤、样本截断、排序。DB 绑定的 `find_promotion_candidates` 由生产真实数据演示验证。
"""
from app.ocr_optimizer.service.skill_promotion import (
    QUALIFY_MIN_TENANTS,
    extract_candidates_from_rows,
)


def _row(country, tenant, api, field, fb="需要 currency 技能"):
    return (country, tenant, api, field, fb)


def test_groups_by_country_and_field():
    rows = [
        _row("JP", "t1", "a1", "currency"),
        _row("JP", "t1", "a1", "currency"),
        _row("JP", "t1", "a1", "invoice_number"),
        _row("MY", "t1", "a2", "currency"),
    ]
    cands = extract_candidates_from_rows(rows)
    keys = {(c.country, c.field) for c in cands}
    assert keys == {("JP", "currency"), ("JP", "invoice_number"), ("MY", "currency")}
    jp_cur = next(c for c in cands if c.country == "JP" and c.field == "currency")
    assert jp_cur.occurrence_count == 2


def test_tenant_count_is_distinct():
    rows = [
        _row("JP", "t1", "a1", "currency"),
        _row("JP", "t2", "a2", "currency"),
        _row("JP", "t2", "a3", "currency"),  # same tenant t2, different api
    ]
    c = extract_candidates_from_rows(rows)[0]
    assert c.tenant_count == 2  # t1, t2
    assert c.api_count == 3
    assert c.occurrence_count == 3


def test_recommended_threshold_strictly_greater_than_5():
    # exactly 5 tenants → NOT auto-recommended ("> 5")
    rows5 = [_row("JP", f"t{i}", f"a{i}", "currency") for i in range(5)]
    assert extract_candidates_from_rows(rows5)[0].recommended is False
    # 6 tenants → recommended
    rows6 = [_row("JP", f"t{i}", f"a{i}", "currency") for i in range(6)]
    c6 = extract_candidates_from_rows(rows6)[0]
    assert c6.tenant_count == 6
    assert c6.recommended is True
    assert c6.to_dict()["recommended"] is True
    assert QUALIFY_MIN_TENANTS == 5


def test_empty_feedback_is_skipped():
    rows = [
        _row("JP", "t1", "a1", "currency", fb=None),
        _row("JP", "t1", "a1", "currency", fb=""),
        _row("JP", "t1", "a1", "currency", fb="[]"),
        _row("JP", "t1", "a1", "currency", fb="{}"),
        _row("JP", "t1", "a1", "currency", fb="  null  "),
        _row("JP", "t1", "a1", "currency", fb="真实建议"),  # only this counts
    ]
    cands = extract_candidates_from_rows(rows)
    assert len(cands) == 1
    assert cands[0].occurrence_count == 1


def test_sample_feedback_capped_at_3_and_truncated():
    long_fb = "x" * 500
    rows = [_row("JP", "t1", f"a{i}", "currency", fb=long_fb) for i in range(10)]
    c = extract_candidates_from_rows(rows)[0]
    assert len(c.sample_feedback) == 3
    assert all(len(s) <= 201 for s in c.sample_feedback)  # 200 + 省略号
    assert c.sample_feedback[0].endswith("…")


def test_sorted_by_occurrence_desc():
    rows = (
        [_row("JP", "t1", "a1", "rare")]
        + [_row("JP", "t1", "a1", "common") for _ in range(5)]
    )
    cands = extract_candidates_from_rows(rows)
    assert cands[0].field == "common"
    assert cands[-1].field == "rare"


def test_empty_input_returns_empty():
    assert extract_candidates_from_rows([]) == []
