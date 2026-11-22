"""Deterministic Minecraft body simulator for offline Jev tests.

It implements the SAME typed-action contract as body_server.js/physical.js (craft, smelt,
mine, scan, tunnel, navigate, equip, stop) with simplified physics: no terrain mesh, but real
recipe rules, real tool-tier gating, real fuel math and ore exposure by tunnel adjacency. It is
a test double for the executive's logic, NOT proof of gameplay (that is the live-world run).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from celia_minecraft.planner import RECIPES, pickaxe_tier

FUEL = {"coal": 8, "charcoal": 8, "stick": 0.5}


@dataclass
class Ore:
    name: str
    x: int
    y: int
    z: int
    exposed: bool = False


@dataclass
class SimWorld:
    trees: list[dict[str, Any]] = field(default_factory=lambda: [{"x": 10, "z": -10, "log": "oak_log", "logs": 12}, {"x": -30, "z": 20, "log": "birch_log", "logs": 10}])
    ores: list[Ore] = field(default_factory=lambda: [Ore("iron_ore", 13 + i, 56, -26 + i) for i in range(5)] + [Ore("coal_ore", 8, 57, -22)])
    surface_y: int = 64
    expose_radius: float = 4.5
    break_stone_pickaxe_after_blocks: int | None = None


class SimBody:
    def __init__(self, world: SimWorld | None = None) -> None:
        self.world = world or SimWorld()
        self.session = "sim-session"
        self.seq = 1
        self.connected = True
        self.pos = {"x": 0.5, "y": float(self.world.surface_y), "z": 0.5}
        self.inv: dict[str, int] = {}
        self.held: str | None = None
        self.health = 20.0
        self.hunger = 20.0
        self.threats: list[dict[str, Any]] = []
        self.stations: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []
        self.blocks_dug = 0
        self.fail_next: dict[str, str] = {}
        self.stops = 0
        self.dimension = "overworld"

    # ---- BodyPort -------------------------------------------------------------------
    async def state(self) -> dict[str, Any] | None:
        if not self.connected:
            return None
        self.seq += 1
        return {
            "session_id": self.session, "seq": self.seq, "connected": True,
            "gameplay": {"position": dict(self.pos), "health": self.health, "hunger": self.hunger, "held": self.held, "on_ground": True},
            "inventory": {"counts": dict(self.inv)},
            "nearby": {"blocks": [], "humanPlayers": [], "entityTypes": []},
            "world": {"time_of_day": 6000, "dimension": self.dimension, "difficulty": "peaceful", "game_mode": "survival", "raining": False,
                      "spawn": {"x": 0.0, "y": 64.0, "z": 0.0}},
            "threats": list(self.threats),
            "action": {"current": "idle", "isIdle": True},
        }

    async def stop(self) -> dict[str, Any]:
        self.stops += 1
        return {"ok": True, "status": "succeeded"}

    async def act(self, action: Any) -> dict[str, Any]:
        t, p = action.type, action.params
        self.actions.append({"type": t, "params": p})
        if t in self.fail_next:
            return self._fail(self.fail_next.pop(t))
        handler = getattr(self, f"_do_{t}", None)
        if handler is None:
            return self._fail(f"unsupported:{t}")
        result = handler(p)
        result.setdefault("status", "succeeded" if result.get("ok") else "failed")
        return result

    # ---- helpers ------------------------------------------------------------------------
    def _fail(self, error: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        out = {"ok": False, "status": "failed", "error": error}
        if evidence:
            out["evidence"] = evidence
        return out

    def _ok(self, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": True, "status": "succeeded"}
        if evidence is not None:
            out["evidence"] = evidence
        return out

    def _count(self, name: str) -> int:
        return self.inv.get(name, 0)

    def _add(self, name: str, n: int) -> None:
        self.inv[name] = self.inv.get(name, 0) + n
        if self.inv[name] <= 0:
            del self.inv[name]

    def _planks(self) -> int:
        return sum(n for k, n in self.inv.items() if k.endswith("_planks"))

    def _take_planks(self, n: int) -> None:
        for name in [k for k in self.inv if k.endswith("_planks")]:
            take = min(self.inv.get(name, 0), n)
            self._add(name, -take)
            n -= take

    def _tier(self) -> int:
        return pickaxe_tier(self.inv)

    def _near_station(self, name: str) -> dict[str, Any] | None:
        for st in self.stations:
            if st["name"] == name and math.dist((st["x"], st["z"]), (self.pos["x"], self.pos["z"])) <= 24:
                return st
        return None

    def _place_station(self, name: str) -> dict[str, Any]:
        self._add(name, -1)
        st = {"name": name, "x": round(self.pos["x"]) + 1, "y": round(self.pos["y"]), "z": round(self.pos["z"])}
        self.stations.append(st)
        return st

    # ---- actions ------------------------------------------------------------------------
    def _do_navigate(self, p: dict[str, Any]) -> dict[str, Any]:
        self.pos.update(x=float(p["x"]), z=float(p["z"]))
        return self._ok()

    def _do_equip(self, p: dict[str, Any]) -> dict[str, Any]:
        if self._count(p["item"]) < 1:
            return self._fail("item not in inventory")
        self.held = p["item"]
        return self._ok()

    def _do_eat(self, p: dict[str, Any]) -> dict[str, Any]:
        foods = [k for k in self.inv if k in {"bread", "cooked_beef", "apple"}]
        if not foods:
            return self._fail("no_food")
        self._add(foods[0], -1)
        self.hunger = min(20.0, self.hunger + 5)
        return self._ok()

    def _do_attack(self, p: dict[str, Any]) -> dict[str, Any]:
        self.threats = [t for t in self.threats if t["name"] != p["target"]]
        return self._ok()

    def _do_jump(self, p: dict[str, Any]) -> dict[str, Any]:
        return self._ok()

    def _do_scan(self, p: dict[str, Any]) -> dict[str, Any]:
        found: dict[str, list[dict[str, Any]]] = {name: [] for name in p["blocks"]}
        for tree in self.world.trees:
            if tree["log"] in found and tree["logs"] > 0 and math.dist((tree["x"], tree["z"]), (self.pos["x"], self.pos["z"])) <= p["radius"]:
                d = math.dist((tree["x"], tree["z"]), (self.pos["x"], self.pos["z"]))
                found[tree["log"]].append({"x": tree["x"], "y": self.world.surface_y, "z": tree["z"], "distance": round(d, 1), "exposed": True})
        for ore in self.world.ores:
            d = math.dist((ore.x, ore.y, ore.z), (self.pos["x"], self.pos["y"], self.pos["z"]))
            if ore.name in found and d <= p["radius"]:
                found[ore.name].append({"x": ore.x, "y": ore.y, "z": ore.z, "distance": round(d, 1), "exposed": ore.exposed})
        for name in found:
            found[name] = found[name][: p["max"]]
        return self._ok({"origin": dict(self.pos), "radius": p["radius"], "found": found})

    def _do_mine(self, p: dict[str, Any]) -> dict[str, Any]:
        block = p["block"]
        if block.endswith("_log"):
            for tree in self.world.trees:
                if tree["log"] == block and tree["logs"] > 0 and math.dist((tree["x"], tree["z"]), (self.pos["x"], self.pos["z"])) <= p["radius"]:
                    take = min(p["count"], tree["logs"])
                    tree["logs"] -= take
                    self._add(block, take)
                    self.pos.update(x=float(tree["x"]) + 1, z=float(tree["z"]))
                    return self._ok({"requested_block_delta": take})
            return self._fail("block not found")
        if "x" in p:
            for ore in list(self.world.ores):
                if (ore.x, ore.y, ore.z) == (int(p["x"]), int(p["y"]), int(p["z"])) and ore.name == block:
                    if ore.x == int(self.pos["x"]) and ore.z == int(self.pos["z"]) and ore.y < self.pos["y"]:
                        return self._fail("no reachable safe block found")  # the body never digs the block under its own feet
                    if self._tier() < 2:
                        return self._fail("needs_tool:stone_pickaxe|iron_pickaxe")
                    if not ore.exposed and not p.get("dig"):
                        return self._fail("no reachable safe block found")
                    self.world.ores.remove(ore)
                    self._add("raw_iron" if "iron" in block else "coal", 1)
                    self.pos.update(x=float(ore.x), y=float(ore.y), z=float(ore.z))
                    for other in self.world.ores:
                        if math.dist((other.x, other.y, other.z), (ore.x, ore.y, ore.z)) <= 1.5:
                            other.exposed = True
                    return self._ok({"requested_block_delta": 1})
            return self._fail("target block changed before mining")
        return self._fail("block not found")

    def _do_craft(self, p: dict[str, Any]) -> dict[str, Any]:
        item, n = p["item"], p["count"]
        before = self._count(item)
        station = None
        placed_here = False
        if item.endswith("_planks"):
            wood = item[: -len("_planks")]
            crafts = -(-n // 4)
            if self._count(f"{wood}_log") < crafts:
                return self._fail("missing_ingredients")
            self._add(f"{wood}_log", -crafts)
            self._add(item, crafts * 4)
        else:
            recipe = RECIPES.get(item)
            if recipe is None:
                return self._fail("missing_ingredients")
            crafts = -(-n // recipe.out)
            for ingredient, qty in recipe.ingredients.items():
                have = self._planks() if ingredient == "#planks" else self._count(ingredient)
                if have < qty * crafts:
                    return self._fail("missing_ingredients")
            if recipe.table:
                station = self._near_station("crafting_table")
                if station is None:
                    if self._count("crafting_table") < 1:
                        return self._fail("needs_crafting_table")
                    placed_here = True
                    station = {"name": "crafting_table", "x": round(self.pos["x"]) + 1, "y": round(self.pos["y"]), "z": round(self.pos["z"])}
            for ingredient, qty in recipe.ingredients.items():
                if ingredient == "#planks":
                    self._take_planks(qty * crafts)
                else:
                    self._add(ingredient, -qty * crafts)
            self._add(item, crafts * recipe.out)
        evidence = {"crafted_delta": self._count(item) - before, "requested_item": item}
        if station:
            evidence["station"] = {**station, "picked_up": placed_here}  # the real body takes a table it placed back
        return self._ok(evidence)

    def _do_smelt(self, p: dict[str, Any]) -> dict[str, Any]:
        if self._count(p["item"]) < p["count"]:
            return self._fail("missing_ingredients")
        station = self._near_station("furnace")
        placed_here = False
        if station is None:
            if self._count("furnace") < 1:
                return self._fail("needs_furnace")
            placed_here = True
            station = {"name": "furnace", "x": round(self.pos["x"]) + 1, "y": round(self.pos["y"]), "z": round(self.pos["z"])}
        fuel_units = self._count("coal") * 8 + self._count("charcoal") * 8 + self._planks() * 1.5 + sum(n for k, n in self.inv.items() if k.endswith("_log")) * 1.5
        if fuel_units < p["count"]:
            return self._fail("missing_fuel")
        need = float(p["count"])
        for name, per in (("coal", 8), ("charcoal", 8)):
            take = min(self._count(name), math.ceil(need / per))
            if take:
                self._add(name, -take)
                need -= take * per
        if need > 0:
            self._take_planks(math.ceil(need / 1.5))
        self._add(p["item"], -p["count"])
        self._add("iron_ingot" if p["item"] == "raw_iron" else p["item"], p["count"])
        return self._ok({"smelted_delta": p["count"], "station": {**station, "picked_up": placed_here}})

    def _do_tunnel(self, p: dict[str, Any]) -> dict[str, Any]:
        dx, dz = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}[p["direction"]]
        done, stopped, found = 0, "completed", None
        for _ in range(p["steps"]):
            if p["mode"] == "down":
                if self.pos["y"] - 1 <= 11:
                    stopped = "void_below"
                    break
                self.pos["y"] -= 1
                self._dig(2 if self.pos["y"] < self.world.surface_y - 3 else 1)
            elif p["mode"] == "up":
                self.pos["y"] += 1
                self._dig(3)
            else:
                self._dig(2)
            self.pos["x"] += dx
            self.pos["z"] += dz
            done += 1
            for ore in self.world.ores:
                if math.dist((ore.x, ore.y, ore.z), (self.pos["x"], self.pos["y"], self.pos["z"])) <= self.world.expose_radius:
                    ore.exposed = True
            if p["watch"]:
                hits = [o for o in self.world.ores if o.name in p["watch"] and o.exposed and math.dist((o.x, o.y, o.z), (self.pos["x"], self.pos["y"], self.pos["z"])) <= 7]
                if hits:
                    stopped, found = "watch_found", [{"x": o.x, "y": o.y, "z": o.z, "name": o.name} for o in hits[:4]]
                    break
        evidence = {"steps_done": done, "stopped_reason": stopped, "found": found}
        if self.world.break_stone_pickaxe_after_blocks and self.blocks_dug >= self.world.break_stone_pickaxe_after_blocks and self._count("stone_pickaxe"):
            self._add("stone_pickaxe", -1)
            self.world.break_stone_pickaxe_after_blocks = None
        if stopped not in {"completed", "watch_found"}:
            return self._fail(f"tunnel_stopped:{stopped}", evidence)
        return self._ok(evidence)

    def _dig(self, blocks: int) -> None:
        if self._tier() < 1 and self.pos["y"] < self.world.surface_y - 3:
            return
        self.blocks_dug += blocks
        if self.pos["y"] < self.world.surface_y - 3:
            self._add("cobblestone", blocks)
        else:
            self._add("dirt", blocks)
