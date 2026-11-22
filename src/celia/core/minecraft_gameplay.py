"""Bounded physical facts for the existing Jev/persona conversation path."""
import json
import re


def _jev_context(data: dict, state: dict) -> dict | None:
    """Mission facts Jev authored. Only whitelisted scalar fields and lines that start with FATO: pass."""
    jev = state.get("jev") if isinstance(state.get("jev"), dict) else {}
    mission_id = data.get("mission_id") or data.get("mission") or jev.get("mission_id")
    if not isinstance(mission_id, str):
        return None
    brief = data.get("brief") if isinstance(data.get("brief"), str) else jev.get("brief")
    lines = [line[:220] for line in (brief or "").splitlines() if line.startswith("FATO:")][:9]
    epoch = data.get("epoch", jev.get("epoch"))
    status = data.get("status", jev.get("status"))
    if data.get("goal") == "idade_do_ferro" or isinstance(data.get("mission_id"), str):
        status = data.get("status", status)
    return {
        "facts": {"mission_id": mission_id[:40], "epoch": epoch if isinstance(epoch, int) and not isinstance(epoch, bool) else None,
                  "mission_status": str(status)[:24]},
        "lines": "\n".join(lines),
    }


def minecraft_feedback_context(raw: str) -> str:
    try:
        data = json.loads(raw.removeprefix("[MC] "))
        if not isinstance(data, dict):
            raise ValueError("invalid result")
    except (ValueError, TypeError):
        return "Minecraft: resultado não confirmado. Não afirmar que executou ou concluiu."
    state = data.get("state", data)
    state = state if isinstance(state, dict) else {}
    status = str(data.get("status", "observed"))[:24]
    inventory = (state.get("inventory") or {}).get("counts", {})
    if not isinstance(inventory, dict):
        inventory = {}
    wood = {str(k)[:64]: v for k, v in list(inventory.items())[:128]
            if str(k).endswith("_log") and isinstance(v, int) and not isinstance(v, bool)}
    gp = state.get("gameplay") or {}
    held = gp.get("held")
    held = held if isinstance(held, str) and re.fullmatch(r"[a-z0-9_]{1,64}", held) else None
    threats = state.get("threats") or []
    nearby_mobs = [
        {"id": mob["name"], "distance": round(mob["distance"], 1)}
        for mob in threats[:5] if isinstance(mob, dict)
        and isinstance(mob.get("name"), str) and re.fullmatch(r"[a-z0-9_]{1,64}", mob["name"])
        and isinstance(mob.get("distance"), (int, float)) and not isinstance(mob["distance"], bool)
        and 0 <= mob["distance"] <= 128
    ]
    facts = {"status": status, "goal": str(data.get("goal", ""))[:40],
             "connected": state.get("connected") is True, "wood_inventory": wood,
             "activity": str((state.get("action") or {}).get("current", "unknown"))[:32],
             "held_item_id": held, "nearby_mobs": nearby_mobs}
    jev = _jev_context(data, state)
    if jev:
        facts["mission_status"] = jev["facts"]["mission_status"]
    results = data.get("results", [data])
    if isinstance(results, list):
        collected = 0
        for result in results[:16]:
            if not isinstance(result, dict) or result.get("status") != "succeeded":
                continue
            evidence = result.get("evidence")
            delta = evidence.get("requested_block_delta") if isinstance(evidence, dict) else None
            if isinstance(delta, int) and not isinstance(delta, bool) and 0 <= delta <= 64:
                collected += delta
        facts["confirmed_collected_logs"] = collected
    jev_text = ("\n[Fatos da missão, só leitura]\n" + jev["lines"]) if jev and jev["lines"] else ""
    jev_rule = (
        "\nFale das suas ações no jogo em primeira pessoa, "
        "com fatos acima. status started/resumed/already_running é só o início: nada foi coletado ainda. "
        "Não invente progresso, coordenadas, itens, ferramentas ou próximos passos que não estejam nos fatos. "
        "Fale em primeira pessoa, em até duas frases curtas, sem dar conselho e sem citar item que não esteja nos fatos."
        if jev else ""
    )
    return (
        "[Minecraft: fatos físicos confirmados pelo executor]\n"
        + json.dumps(facts, ensure_ascii=False, separators=(",", ":"))[:1200]
        + jev_text[:1100]
        + jev_rule
        + "\nResponda ao pedido atual com sua persona habitual. Não recite JSON ou dados técnicos. "
        "Só declare conclusão se status=succeeded; accepted/running indicam apenas início. "
        "Se houve falha, cancelamento ou incerteza, diga isso sem inventar sucesso. "
        "As ações no jogo são suas, não de outro personagem. Uma falha de execução "
        "é uma limitação atual sua; não prometa que fará manualmente uma ação não confirmada. "
        "Inventário é o total atual, não a quantidade coletada neste objetivo. "
        "IDs do Minecraft são fatos: pickaxe=picareta, axe=machado, sword=espada; não os confunda. "
        "Se a informação sobre o item não veio do corpo, não chute. "
        "Com Criador, pode usar humor seco e provocação leve, mas priorize o perigo e "
        "nunca invente uma morte anterior nem uma lição que não esteja no histórico confirmado."
    )
