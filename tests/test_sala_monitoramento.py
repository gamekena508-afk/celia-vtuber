"""Read-only monitoring room regression: no invented evolution or secret exposure."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _module():
    file = Path(__file__).resolve().parents[1] / "tools" / "sala_monitoramento_server.py"
    spec = importlib.util.spec_from_file_location("celia_monitoring_room", file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_monitoring_snapshot_schema_and_no_external_fetch():
    m = _module()
    status = m.snapshot()
    assert status["services"]
    assert all(x["name"] and isinstance(x["online"], bool) for x in status["services"])
    assert status["system"]["ram_total_gb"] > 0
    assert status["evolution"]["window"] == "últimos 60 minutos"
    assert "Não conectado" in status["external"]["news"]
    assert "Não conectado" in status["external"]["iss"]
    assert "aprendizagem automática" in status["limitations"][1]
    assert json.loads(json.dumps(status, ensure_ascii=False))["lessons"]


def test_hourly_evolution_is_ledger_not_fabricated_memory(tmp_path, monkeypatch):
    m = _module()
    now = datetime.now(timezone.utc)
    folder = tmp_path / "var" / "cota"
    folder.mkdir(parents=True)
    events = [
        {"ts": now.isoformat(), "model": "celia-gemma4-e2b", "code": "OK", "total_ms": 1000},
        {"ts": (now - timedelta(minutes=4)).isoformat(), "model": "groq/fail", "code": "LNT", "total_ms": 3500},
        {"ts": (now - timedelta(hours=3)).isoformat(), "model": "old", "code": "OK", "total_ms": 100},
    ]
    (folder / (now.date().isoformat() + ".jsonl")).write_text(
        "\n".join(json.dumps(x) for x in events), encoding="utf-8"
    )
    monkeypatch.setattr(m, "ROOT", tmp_path)
    activity = m._llm_activity(now)
    assert activity["calls"] == 2
    assert activity["ok"] == 1
    assert activity["errors"] == 1
    assert activity["gemma_local_ok"] == 1
    assert all("message" not in x for x in activity["events"])
