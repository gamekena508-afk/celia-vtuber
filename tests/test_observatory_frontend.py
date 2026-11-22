from pathlib import Path


OBSERVATORY = Path(__file__).resolve().parents[1] / "frontend" / "observatory.html"


def test_observatory_keeps_the_live_tree_navigation_and_truth_contract():
    html = OBSERVATORY.read_text(encoding="utf-8")

    for required in (
        "SISTEMA NERVOSO",
        "border-radius:50%",
        "input.external_turn",
        "guardiao",
        "core.narrador",
        "llm.9router",
        "mcpp.tool_executor",
        "mcp.authorization_gate",
        "mcp.ddg-search",
        "mcp.opencode-agent",
        "mcp.react-control",
        "mcp.celia-vts-v11",
        "mcp.registry",
        "output.emotion_bus",
        "output.obs_subtitles",
        "output.tool_result",
        "parent_span_id",
        "pointerdown",
        "wheel",
        "FIT TREE",
        "DESFAZER",
        "REFAZER",
        "SALVAR",
        "Ctrl+Z",
        "Ctrl+S",
        "LAYOUT_KEY",
        "localStorage.setItem",
        "startNodeDrag",
        "undoLayout",
        "redoLayout",
        "applyNodePosition",
        "selectionBox",
        "selectionDrag",
        "multi-selected",
        "ALINHAR CIMA",
        "EMPILHAR",
        "alignSelectedTop",
        "stackSelectedVertically",
        "getBoundingClientRect",
        "startClient",
        "syncWorldSize",
        "applyNodePositions",
        "allBefore",
        "layout.v3",
        "graphBounds",
        "W=9600,H=6400",
        "visualPosition",
        "pointerInLayout",
        "ensureVisualSpace",
        "active.members",
        "Space and drag to pan",
        "empty drag selects",
        "space + drag pans",
        "ARCHITECTURE SOURCE",
        "EVENT COVERAGE",
        "EXPAND DOMAIN",
        "StreamJSONDetector",
        "CONTEXT WINDOW / PROVIDER",
        "NOT REPORTED BY PROVIDER",
        "debug/observability/runtime",
        "RUNTIME LIBRARIES",
        "LIVE BUS COUNTERS",
        "CONTINUOUS BEHAVIOR (safe runtime metadata)",
        "runtime.continuous_behavior",
        "EventSource('/debug/observability/events",
        "REAL EVENTS ONLY",
        "NOT AVAILABLE",
        "--root:#4e9dff",
        "--root-pulse:#bd71ff",
        "--pulse:#58b8ff",
        "PATH ERROR",
        "CODE N/A",
        "safeErrorCode",
        "safeErrorType",
        "drawErrorBadge",
        "FAILURE CLASSIFICATION",
        "NODE FAILED",
        "NOT INSTRUMENTED / static topology only",
        "celia-narracao",
        "['celia.core','core.narrador']",
        "['mcpp.tool_executor','core.narrador']",
        "['mcpp.tool_executor','mcp.authorization_gate']",
        "['mcp.authorization_gate','mcp.registry']",
        "['core.narrador','output.websocket']",
        "['core.narrador','output.emotion_bus']",
    ):
        assert required in html


def test_observatory_does_not_keep_stale_runtime_claims_in_static_copy():
    html = OBSERVATORY.read_text(encoding="utf-8")

    for stale in (
        "ToolManager formata 63 tools",
        "bridge_routes.py:955",
        "mapa mao_pro",
        "service.xtts",
    ):
        assert stale not in html

    for required in (
        "subsystems=runtimeTopology.subsystems",
        "MODELO ATIVO:",
        "LIVE CONFIG: engine=",
        "A quantidade de tools vem de /debug/observability/topology",
    ):
        assert required in html
