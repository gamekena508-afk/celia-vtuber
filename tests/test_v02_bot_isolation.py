import asyncio
import dataclasses
from pathlib import Path

import pytest

from v02_bot_test_support import manager_for, satisfy_startup


@pytest.mark.asyncio
async def test_subprocess_gets_only_filtered_parent_and_allowlisted_fixture_env(tmp_path, monkeypatch):
    monkeypatch.setenv("V02_FAKE_SECRET", "fixture-only-value")
    script = r"""
import os, time
print('allow=' + os.environ.get('BOT_FIXTURE_MODE', 'missing'), flush=True)
print('ambient=' + ('present' if 'V02_FAKE_SECRET' in os.environ else 'filtered'), flush=True)
print('secret_key=' + ('present' if 'BOT_API_TOKEN' in os.environ else 'filtered'), flush=True)
time.sleep(30)
"""
    manager = manager_for(tmp_path, script, restart_policy="never")
    runtime = manager._runtime["probe"]
    env_file = runtime.entry.root / "probe.env"
    env_file.write_text(
        "BOT_FIXTURE_MODE=isolated\nBOT_API_TOKEN=fixture-only-value\n",
        encoding="utf-8",
    )
    runtime.spec = dataclasses.replace(
        runtime.spec,
        env_file=env_file,
        env_allowlist=("BOT_FIXTURE_MODE",),
    )
    start_task = asyncio.create_task(manager.start("probe"))
    started = await satisfy_startup(manager, start_task)
    log_path = tmp_path / "logs" / "probe.stdout.log"
    for _ in range(100):
        if log_path.exists() and "allow=isolated" in log_path.read_text(encoding="utf-8"):
            break
        await asyncio.sleep(0.01)
    await manager.stop("probe")
    stdout = log_path.read_text(encoding="utf-8")
    assert "allow=isolated" in stdout
    assert "ambient=filtered" in stdout
    assert "secret_key=filtered" in stdout
    assert "fixture-only-value" not in stdout
    assert started.process_alive and started.connected
    await manager.shutdown()


def test_sensitive_environment_names_cannot_be_allowlisted(tmp_path):
    manager = manager_for(tmp_path, "pass", restart_policy="never")
    runtime = manager._runtime["probe"]
    with pytest.raises(ValueError, match="unsafe name"):
        dataclasses.replace(runtime.spec, env_allowlist=("BOT_API_TOKEN",))

