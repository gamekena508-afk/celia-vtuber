"""The newest studio page takes the publisher seat; an older one never takes it back."""
import asyncio

from celia.mobile_routes import MobileHub


class FakeWS:
    def __init__(self):
        self.sent, self.closed_with, self.accepted = [], None, False

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        if self.closed_with is not None:
            raise RuntimeError("closed")
        self.sent.append(data)

    async def close(self, code=1000):
        self.closed_with = code


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_newer_claim_takes_the_seat_and_old_is_closed_with_4410():
    async def go():
        hub, old, new = MobileHub(), FakeWS(), FakeWS()
        assert await hub.join(old, True, 100.0) == "studio"
        assert await hub.join(new, True, 200.0) == "studio"
        assert hub.publisher is new and hub.publisher_claim == 200.0
        assert old.closed_with == 4410 and {"type": "studio-replaced"} in old.sent
        assert new.accepted
    run(go())


def test_older_equal_or_missing_claim_is_refused_and_seat_is_kept():
    async def go():
        hub, cur = MobileHub(), FakeWS()
        assert await hub.join(cur, True, 200.0) == "studio"
        for claim in (100.0, 200.0, 0.0):
            other = FakeWS()
            assert await hub.join(other, True, claim) is None
            assert not other.accepted
        assert hub.publisher is cur and cur.closed_with is None
    run(go())


def test_legacy_publisher_without_claim_is_replaced_by_any_claimed_page():
    async def go():
        hub, legacy, new = MobileHub(), FakeWS(), FakeWS()
        assert await hub.join(legacy, True) == "studio"
        assert await hub.join(new, True, 1.0) == "studio"
        assert hub.publisher is new and legacy.closed_with == 4410
    run(go())


def test_old_page_leaving_after_takeover_does_not_clear_the_new_publisher():
    async def go():
        hub, old, new = MobileHub(), FakeWS(), FakeWS()
        await hub.join(old, True, 1.0)
        await hub.join(new, True, 2.0)
        await hub.leave(old, "studio")  # what ws_endpoint's finally does for the kicked page
        assert hub.publisher is new and hub.publisher_claim == 2.0
        await hub.leave(new, "studio")
        assert hub.publisher is None and hub.publisher_claim == 0.0
    run(go())


def test_viewers_are_handed_to_the_new_publisher():
    async def go():
        hub, old, new, viewer = MobileHub(), FakeWS(), FakeWS(), FakeWS()
        await hub.join(old, True, 1.0)
        vid = await hub.join(viewer, False)
        await hub.join(new, True, 2.0)
        ready = [m for m in new.sent if m.get("type") == "ready"][0]
        assert vid in ready["viewers"]
        assert {"type": "studio-online"} in viewer.sent
    run(go())
