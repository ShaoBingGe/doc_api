"""提取准入闸 —— 单体服务的并发上界。

闸错了的后果是 OOM 被内核杀进程（腾讯云 cgroup 限 900M，实测每页渲染约 30MB，
3 个 16 页文档并发就是 1.4GB），所以这组用例按"能不能真挡住"来写，
不只测计数器加减。
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.extract_gate import ExtractGate, GateTimeout


@pytest.mark.asyncio
async def test_admits_up_to_max_docs():
    gate = ExtractGate(max_docs=3, max_pages=100)
    async with gate.slot(1), gate.slot(1), gate.slot(1):
        assert gate.in_flight_docs == 3
    assert gate.in_flight_docs == 0


@pytest.mark.asyncio
async def test_fourth_doc_blocks_until_one_releases():
    """文档数维度：满 3 个后第 4 个必须等，不能放行。"""
    gate = ExtractGate(max_docs=3, max_pages=100)
    admitted = []

    async def occupy(tag, hold):
        async with gate.slot(1):
            admitted.append(tag)
            await asyncio.sleep(hold)

    t = [asyncio.create_task(occupy(i, 0.05)) for i in range(3)]
    await asyncio.sleep(0.01)
    assert len(admitted) == 3

    fourth = asyncio.create_task(occupy("fourth", 0))
    await asyncio.sleep(0.01)
    assert "fourth" not in admitted, "闸满时第 4 个不该被放行"

    await asyncio.gather(*t, fourth)
    assert "fourth" in admitted, "前面释放后第 4 个应当进来"


@pytest.mark.asyncio
async def test_page_budget_blocks_even_when_doc_slots_free():
    """页数维度：文档数没满，但页数超预算也必须挡住。

    这正是"3 个并发"看似安全实则 OOM 的场景 —— 16 页 × 3 = 1.4GB。
    """
    gate = ExtractGate(max_docs=3, max_pages=20)
    admitted = []

    async def occupy(tag, pages, hold):
        async with gate.slot(pages):
            admitted.append(tag)
            await asyncio.sleep(hold)

    big = asyncio.create_task(occupy("big16", 16, 0.05))
    await asyncio.sleep(0.01)
    assert admitted == ["big16"]

    # 文档槽位还剩 2 个，但 16+16 > 20 页预算 → 必须挡住
    second = asyncio.create_task(occupy("big16-2", 16, 0))
    await asyncio.sleep(0.01)
    assert "big16-2" not in admitted, "页数超预算时不该只看文档数就放行"

    await asyncio.gather(big, second)
    assert "big16-2" in admitted


@pytest.mark.asyncio
async def test_small_doc_fits_alongside_big_one():
    """页数还够时应当放行 —— 闸不能保守到把吞吐压没。"""
    gate = ExtractGate(max_docs=3, max_pages=20)
    admitted = []

    async def occupy(tag, pages, hold):
        async with gate.slot(pages):
            admitted.append(tag)
            await asyncio.sleep(hold)

    big = asyncio.create_task(occupy("big16", 16, 0.05))
    await asyncio.sleep(0.01)
    small = asyncio.create_task(occupy("small3", 3, 0))
    await asyncio.sleep(0.01)
    assert "small3" in admitted, "16+3 <= 20，应当放行"
    await asyncio.gather(big, small)


@pytest.mark.asyncio
async def test_oversized_doc_is_not_starved():
    """单个文档页数就超总预算时仍要能跑，否则它是永远处理不了的黑洞。"""
    gate = ExtractGate(max_docs=3, max_pages=10)
    async with gate.slot(16):          # 16 > 10，但闸内空载 → 放行
        assert gate.in_flight_pages == 16
    assert gate.in_flight_pages == 0


@pytest.mark.asyncio
async def test_oversized_doc_waits_for_empty_gate():
    """超预算的大文档必须独占：闸里还有别人时不能挤进来。"""
    gate = ExtractGate(max_docs=3, max_pages=10)
    admitted = []

    async def occupy(tag, pages, hold):
        async with gate.slot(pages):
            admitted.append(tag)
            await asyncio.sleep(hold)

    small = asyncio.create_task(occupy("small", 2, 0.05))
    await asyncio.sleep(0.01)
    huge = asyncio.create_task(occupy("huge", 16, 0))
    await asyncio.sleep(0.01)
    assert "huge" not in admitted, "闸内有人时超预算文档不该挤进来"
    await asyncio.gather(small, huge)
    assert "huge" in admitted


@pytest.mark.asyncio
async def test_timeout_raises_gate_timeout():
    """同步端点靠它回'服务繁忙'而不是无限挂着。"""
    gate = ExtractGate(max_docs=1, max_pages=100)

    async def hold():
        async with gate.slot(1):
            await asyncio.sleep(0.2)

    t = asyncio.create_task(hold())
    await asyncio.sleep(0.01)
    with pytest.raises(GateTimeout):
        async with gate.slot(1, timeout=0.05):
            pass
    await t


@pytest.mark.asyncio
async def test_timeout_does_not_leak_a_slot():
    """超时的等待者绝不能留下没归还的配额 —— 泄漏几次闸就永久堵死。"""
    gate = ExtractGate(max_docs=1, max_pages=10)

    async def hold():
        async with gate.slot(1):
            await asyncio.sleep(0.1)

    t = asyncio.create_task(hold())
    await asyncio.sleep(0.01)
    for _ in range(3):
        with pytest.raises(GateTimeout):
            async with gate.slot(1, timeout=0.01):
                pass
    await t
    assert gate.in_flight_docs == 0
    assert gate.in_flight_pages == 0
    # 泄漏的话这里会超时挂死
    async with gate.slot(1, timeout=0.5):
        pass


@pytest.mark.asyncio
async def test_exception_inside_slot_still_releases():
    """提取抛异常时配额必须归还，否则一次失败就永久少一个槽位。"""
    gate = ExtractGate(max_docs=2, max_pages=10)
    with pytest.raises(RuntimeError):
        async with gate.slot(3):
            raise RuntimeError("提取炸了")
    assert gate.in_flight_docs == 0
    assert gate.in_flight_pages == 0


@pytest.mark.asyncio
async def test_zero_or_negative_pages_normalised():
    """页数估不出来时按 1 记，不能变成 0 —— 0 页会让页数维度失效。"""
    gate = ExtractGate(max_docs=5, max_pages=3)
    async with gate.slot(0):
        assert gate.in_flight_pages == 1


@pytest.mark.asyncio
async def test_concurrent_load_never_exceeds_either_limit():
    """压力下的不变式：任何时刻两个维度都不越界。"""
    gate = ExtractGate(max_docs=3, max_pages=12)
    peak_docs = peak_pages = 0

    async def job(pages):
        nonlocal peak_docs, peak_pages
        async with gate.slot(pages):
            peak_docs = max(peak_docs, gate.in_flight_docs)
            peak_pages = max(peak_pages, gate.in_flight_pages)
            await asyncio.sleep(0.005)

    await asyncio.gather(*(job(1 + i % 5) for i in range(40)))
    assert peak_docs <= 3
    assert peak_pages <= 12
    assert gate.in_flight_docs == 0
    assert gate.in_flight_pages == 0
