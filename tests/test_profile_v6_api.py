"""Exercise V6 APIs through the same router used by the browser."""
import asyncio

import httpx
from fastapi import FastAPI
from celia import bridge_routes
from celia.core.continuous_behavior import ContinuousBehaviorRuntime


def test_v6_editor_http_read_write_reset_and_revision(tmp_path,monkeypatch):
    runtime=ContinuousBehaviorRuntime(tmp_path/"settings.json")
    monkeypatch.setattr(bridge_routes,"continuous_behavior",runtime)
    app=FastAPI()
    app.include_router(bridge_routes.init_bridge_routes(None))

    async def scenario():
        transport=httpx.ASGITransport(app=app,client=("testclient",54321))
        async with httpx.AsyncClient(transport=transport,base_url="http://testserver") as client:
            original=await client.get("/bridge/behavior/profiles/cohost")
            assert original.status_code==200
            values=original.json()["settings"]
            values["social"]["participation"]=75
            response=await client.put("/bridge/behavior/profiles/cohost",
                json={"settings":values,"expected_revision":0})
            assert response.status_code==200
            assert response.json()["revision"]==1
            conflict=await client.put("/bridge/behavior/profiles/cohost",
                json={"settings":values,"expected_revision":0})
            assert conflict.status_code==409
            wrong=await client.get("/bridge/behavior/profiles/made-up")
            assert wrong.status_code==404
            bad=await client.put("/bridge/behavior/profiles/cohost",
                json={"settings":{**values,"gameplay":{**values["gameplay"],"ideal_distance":500}}})
            assert bad.status_code==422
            reset=await client.post("/bridge/behavior/profiles/cohost/reset")
            assert reset.status_code==200
            assert reset.json()["customized"] is False
            read=await client.get("/bridge/behavior/profiles/chat")
            assert read.status_code==200
            assert read.json()["settings"]["gameplay"]["ideal_distance"]==6
    asyncio.run(scenario())


def test_v6_editor_rejects_non_local_client(tmp_path,monkeypatch):
    runtime=ContinuousBehaviorRuntime(tmp_path/"settings.json")
    monkeypatch.setattr(bridge_routes,"continuous_behavior",runtime)
    app=FastAPI()
    app.include_router(bridge_routes.init_bridge_routes(None))
    async def scenario():
        transport=httpx.ASGITransport(app=app,client=("8.8.8.8",1234))
        async with httpx.AsyncClient(transport=transport,base_url="http://testserver") as client:
            response=await client.get("/bridge/behavior/profiles/cohost")
            assert response.status_code==403
    asyncio.run(scenario())
