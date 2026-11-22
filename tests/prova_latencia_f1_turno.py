"""Focused proof for F-1 turn correlation through ContinuousBehaviorRuntime.

This proof is local and does not start the Célia server or any external service.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from celia.core.continuous_behavior import ContinuousBehaviorRuntime

    with tempfile.TemporaryDirectory(prefix="celia-f1-turn-") as directory:
        behavior = ContinuousBehaviorRuntime(Path(directory) / "profile.json")

        # The full allocator output must survive the begin_turn round trip.
        created_a = behavior.latency.novo_turno()
        created_b = behavior.latency.novo_turno()
        returned_a = behavior.begin_turn(created_a)
        returned_b = behavior.begin_turn(created_b)
        assert created_a != created_b
        assert len(created_a) == len(created_b) == 48
        assert returned_a == created_a
        assert returned_b == created_b
        assert returned_a != returned_b

        # Marks made after the round trip remain isolated by their turn ID.
        behavior.mark_latency("asr", 111, turn_id=returned_a)
        behavior.mark_latency("asr", 222, turn_id=returned_b)
        assert behavior.latency.estagios_do_turno(returned_a) == {"asr": 111.0}
        assert behavior.latency.estagios_do_turno(returned_b) == {"asr": 222.0}

        # External labels retain the existing allowlist and 48-character cap.
        for raw in ("../../etc/passwd", "a b\nc", "x" * 500):
            normalized = behavior.begin_turn(raw)
            assert normalized != raw
            assert len(normalized) <= 48
            assert re.fullmatch(r"[A-Za-z0-9_ .:-]+", normalized)
            assert "/" not in normalized
            assert "\n" not in normalized

    print("PROVA F-1 TURN_ID: APROVADO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
