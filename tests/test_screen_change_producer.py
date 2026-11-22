import asyncio
import base64
import io

import httpx
from fastapi import FastAPI
from PIL import Image
import pytest

from celia import bridge_routes
from celia.core.continuous_behavior import ContinuousBehaviorRuntime
from celia.core.screen_vision import ScreenVisionAnalyzer
from celia.core.vision_cache import vision_cache


def jpeg_data_url() -> str:
    img = Image.new("RGB", (8, 8), (30, 80, 140))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.mark.asyncio
async def test_vision_frame_relevance_feeds_behavior_without_changing_frame_contract(tmp_path, monkeypatch):
    behavior = ContinuousBehaviorRuntime(tmp_path / "profile.json")
    behavior.select_profile("cohost")
    monkeypatch.setattr(bridge_routes, "continuous_behavior", behavior)
    from celia.core.screen_vision import screen_vision
    from celia.core.vision_sources import vision_sources
    vision_sources.set_active("screen", True)
    scheduled = []
    monkeypatch.setattr(
        screen_vision,
        "schedule",
        lambda image, *, relevance, **kw: scheduled.append(relevance) or True,
    )
    app = FastAPI()
    app.include_router(bridge_routes.init_bridge_routes(None))
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        normal = await client.post("/bridge/vision/frame", json={"image": jpeg_data_url()})
        assert normal.status_code == 200
        assert normal.json()["ok"] is True
        assert normal.json()["screen"] is None
        assert behavior.safe_snapshot()["metrics"]["screen_events"] == 0

        vision_cache.clear()
        before_note = vision_cache.put("editor", "timeline parada")
        await asyncio.sleep(0.001)
        unchanged = await client.post(
            "/bridge/vision/frame",
            json={"image": jpeg_data_url(), "unchanged": True},
        )
        assert unchanged.status_code == 200
        refreshed_note, refreshed_age = vision_cache.get()
        assert refreshed_note is not None
        assert refreshed_note.verified_ts > before_note.verified_ts
        assert refreshed_age is not None and refreshed_age < 1000

        changed = await client.post(
            "/bridge/vision/frame",
            json={"image": jpeg_data_url(), "relevance": 0.9},
        )
        assert changed.status_code == 200
        assert changed.json()["screen"]["action"] == "candidate"
        assert changed.json()["vision_scheduled"] is True
        assert scheduled == [0.9]
        snapshot = behavior.safe_snapshot()
        assert snapshot["metrics"]["screen_events"] == 1
        assert any(item["type"] == "screen_comment" for item in snapshot["candidates"])


def test_low_relevance_change_does_not_invalidate_inflight_analysis(monkeypatch):
    analyzer = ScreenVisionAnalyzer()
    monkeypatch.setenv("CELIA_VISION_MIN_RELEVANCE", "0.62")
    monkeypatch.setenv("CELIA_VISION_MIN_INTERVAL", "0")
    from celia.core.vision_sources import vision_sources
    vision_sources.set_active("screen", True)

    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def fake_analyze(generation, image, relevance, source="screen"):
            started.set()
            await release.wait()
            finished.set()

        monkeypatch.setattr(analyzer, "_analyze", fake_analyze)
        assert analyzer.schedule("important", relevance=0.9) is True
        await asyncio.wait_for(started.wait(), 1)
        accepted_generation = analyzer._generation

        assert analyzer.schedule("tiny-change", relevance=0.1) is False
        assert analyzer._generation == accepted_generation

        release.set()
        await asyncio.wait_for(finished.wait(), 1)

    asyncio.run(exercise())
