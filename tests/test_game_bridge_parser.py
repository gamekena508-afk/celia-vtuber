import json
import re
import subprocess
from pathlib import Path


_BRIDGE = Path(__file__).parents[1] / "integrations/games/common/celia-game-bridge.js"
_GAMES_DIR = _BRIDGE.parents[1]


def _extract(text: str, legal_actions: list[str]):
    script = """
global.window = {};
require(process.argv[1]);
const result = window.CeliaGameBridge.extractAction(process.argv[2], JSON.parse(process.argv[3]));
process.stdout.write(JSON.stringify(result ?? null));
"""
    output = subprocess.check_output(
        ["node", "-e", script, str(_BRIDGE), text, json.dumps(legal_actions)], text=True
    )
    return json.loads(output)


def _normalize_emotion(emotion: str):
    script = """
global.window = {};
require(process.argv[1]);
process.stdout.write(window.CeliaGameBridge.normalizeEmotion(process.argv[2]));
"""
    return subprocess.check_output(
        ["node", "-e", script, str(_BRIDGE), emotion], text=True
    )


def _fallback_commentary(state: dict, recovery: bool = False):
    script = """
global.window = {};
require(process.argv[1]);
process.stdout.write(window.CeliaGameBridge.fallbackCommentary(JSON.parse(process.argv[2]), process.argv[3] === 'true'));
"""
    return subprocess.check_output(
        ["node", "-e", script, str(_BRIDGE), json.dumps(state), str(recovery).lower()], text=True
    )


def _reaction_level(state: dict, event_summary: str, recovery: bool = False):
    script = """
global.window = {};
require(process.argv[1]);
process.stdout.write(window.CeliaGameBridge.reactionLevel(
  JSON.parse(process.argv[2]), process.argv[3], 'NONE', process.argv[4] === 'true'
));
"""
    return subprocess.check_output(
        ["node", "-e", script, str(_BRIDGE), json.dumps(state), event_summary, str(recovery).lower()],
        text=True,
    )


def test_game_bridge_prefers_structured_action():
    assert _extract('{"action":"e2e4","commentary":"Teste."}', ["e2e4", "d2d4"]) == "e2e4"


def test_game_bridge_recovers_an_explicit_action_without_trusting_it():
    assert _extract("Minha jogada: 5,0-3,2. Boa sorte.", ["5,0-3,2", "5,4-4,3"]) == "5,0-3,2"


def test_game_bridge_never_invents_an_action_when_no_legal_token_is_present():
    assert _extract("Vou pensar melhor nessa.", ["e2e4", "d2d4"]) is None


def test_game_bridge_normalizes_personality_emotions_to_live2d_names():
    assert _normalize_emotion("confident") == "teasing"
    assert _normalize_emotion("arrogant") == "teasing"
    assert _normalize_emotion("unexpected-value") == "teasing"
    assert _normalize_emotion("angry") == "angry"


def test_game_bridge_fallback_commentary_reacts_to_structured_special_state():
    capture_line = _fallback_commentary(
        {"match_id": "match-1", "last_move": "5,0-4,1", "history": [], "special": "mandatory_capture"}
    )
    recovery_line = _fallback_commentary(
        {"match_id": "match-1", "last_move": "e2e4", "history": [], "special": "check"}, True
    )

    assert "captura" in capture_line.lower()
    assert any(word in recovery_line.lower() for word in ("erro", "leitura", "movimento"))


def test_game_bridge_allows_silent_structural_move_without_canned_fallback():
    script = """
global.window = {};
const calls = [], applied = [];
const state = {
  match_id: 'm1', current_player: 'celia', legal_actions: ['e7e5'],
  history: [], board: [null], board_revision: 0, mode: 'criador-vs-celia', last_move: null
};
global.fetch = async (url, options = {}) => {
  const body = options.body ? JSON.parse(options.body) : null;
  calls.push({url, body});
  if (String(url).includes('/session')) {
    return {ok: true, json: async () => ({ok:true, active:true, match_id:'m1', authority_epoch:1, board_revision:0})};
  }
  if (url === '/bridge/game/action') return {ok: true, json: async () => ({action: 'e7e5'})};
  if (url === '/bridge/process') return {ok: true, json: async () => ({response: '{"commentary":"","reaction":"none"}'})};
  return {ok: true, json: async () => ({ok: true, command: null})};
};
require(process.argv[1]);
const adapter = {
  getState: () => state,
  getCurrentPlayer: () => state.current_player,
  isGameOver: () => false,
  setThinking: () => {},
  applyCeliaAction: async (action) => {
    applied.push(action);
    state.current_player = 'criador'; state.legal_actions = []; state.board_revision += 1;
    return true;
  },
  recordBridgeError: async () => {}, applyFallback: async () => false
};
(async () => {
  const bridge = new window.CeliaGameBridge('xadrez', adapter);
  const ok = await bridge.play('Criador abriu com e4.');
  await new Promise(resolve => setTimeout(resolve, 10));
  process.stdout.write(JSON.stringify({calls, applied, ok}));
})();
"""
    result = json.loads(subprocess.check_output(["node", "-e", script, str(_BRIDGE)], text=True, timeout=5))
    urls = [call["url"] for call in result["calls"]]
    assert result["ok"] is True
    assert result["applied"] == ["e7e5"]
    assert "/bridge/game/action" in urls
    assert "/bridge/speak" not in urls

def test_technical_fallback_applies_legal_move_silently():
    script = """
global.window = {};
const calls = [], fallbacks = [];
const state = {
  match_id: 'match-1', history: [], special: 'mandatory_capture', board_revision: 0,
  legal_actions: ['5,0-3,2'], current_player: 'celia', board: [[null]], mode: 'criador-vs-celia', last_move: null
};
global.fetch = async (url, options = {}) => {
  calls.push(String(url));
  if (String(url).includes('/session')) return {ok:true,json:async()=>({ok:true,active:true,match_id:'match-1',authority_epoch:1,board_revision:0})};
  if (url === '/bridge/game/action') return {ok: true, json: async () => ({action: 'not-legal'})};
  if (url === '/bridge/process') return {ok: true, json: async () => ({response: '{"action":"not-legal","commentary":""}'})};
  return {ok: true, json: async () => ({ok: true, command: null})};
};
require(process.argv[1]);
const adapter = {
  getState: () => state, getCurrentPlayer: () => state.current_player,
  isGameOver: () => false, setThinking: () => {}, applyCeliaAction: async () => false,
  recordBridgeError: async () => {},
  applyFallback: async (error, commentary, reservation) => {
    fallbacks.push({error, commentary, source: reservation.actionSource});
    state.current_player='criador'; state.legal_actions=[]; state.board_revision+=1; return true;
  }
};
(async () => {
  const bridge = new window.CeliaGameBridge('damas', adapter);
  const ok = await bridge.play('Criador deixou uma captura obrigatoria.');
  await new Promise(resolve => setTimeout(resolve, 10));
  process.stdout.write(JSON.stringify({calls, fallbacks, ok}));
})();
"""
    result = json.loads(subprocess.check_output(["node", "-e", script, str(_BRIDGE)], text=True, timeout=5))
    assert result["ok"] is True
    assert result["fallbacks"]
    assert result["fallbacks"][0]["commentary"] == ""
    assert result["fallbacks"][0]["source"] == "fallback_engine"
    assert "/bridge/speak" not in result["calls"]

def test_background_game_tab_cannot_publish_result_ack():
    script = """
global.window = {};
const calls = [];
const state = {
  match_id: 'background-match', current_player: 'criador', legal_actions: [],
  history: [], board: ['X','X','X','','','','','',''], mode: 'criador-vs-celia'
};
global.fetch = async (url, options = {}) => {
  calls.push(url);
  if (url.includes('/session')) {
    return {ok: true, json: async () => ({ok: true, active: false})};
  }
  return {ok: true, json: async () => ({ok: true, command: null})};
};
require(process.argv[1]);
const adapter = {
  getState: () => state,
  getCurrentPlayer: () => state.current_player,
  isGameOver: () => true,
  setThinking: () => {}
};
(async () => {
  const bridge = new window.CeliaGameBridge('jogo da velha', adapter);
  await bridge.finalizeResult({
    winner: 'criador', matchId: 'background-match', turn: 5, result: 'X'
  });
  process.stdout.write(JSON.stringify(calls));
})();
"""
    calls = json.loads(
        subprocess.check_output(["node", "-e", script, str(_BRIDGE)], text=True, timeout=5)
    )
    assert any("/session" in url for url in calls)
    assert not any("/result-ack" in url for url in calls)
    assert "/bridge/speak" not in calls


def test_game_reaction_policy_keeps_ordinary_move_silent_and_marks_special_events():
    ordinary = _reaction_level({"match_id": "m1", "history": []}, "Criador jogou e4.")
    blunder = _reaction_level({"match_id": "m1", "history": []}, "Criador cometeu uma besteira grande.")
    check = _reaction_level({"match_id": "m1", "special": "check"}, "Xeque.")
    mate = _reaction_level({"match_id": "m1", "special": "checkmate"}, "Xeque-mate.")

    assert ordinary == "NONE"
    assert blunder == "MICRO"
    assert check == "SHORT"
    assert mate == "FULL"


def test_game_reaction_manager_deduplicates_and_prefers_silence_for_invalid_text():
    script = """
global.window = {};
require(process.argv[1]);
const manager = new window.CeliaGameBridge.ReactionManager();
const state = {match_id: 'm1', last_move: 'e2e4', history: [{}]};
const first = manager.decide(state, 'Criador fez uma besteira grande.', '', 'surprised', 'MICRO', false);
const duplicate = manager.decide(state, 'Criador fez uma besteira grande.', '', 'surprised', 'MICRO', false);
const technical = manager.decide(
  {match_id: 'm2', last_move: 'e7e5', history: [{}], special: 'check'},
  'Xeque.', '{"action":"e7e5"}', 'teasing', 'SHORT', false
);
process.stdout.write(JSON.stringify({first, duplicate, technical}));
"""
    result = json.loads(subprocess.check_output(["node", "-e", script, str(_BRIDGE)], text=True))
    assert result["first"]["level"] == "MICRO"
    assert result["first"]["text"]
    assert result["duplicate"]["level"] == "NONE"
    assert result["technical"] == {
        "level": "NONE", "text": "", "priority": "normal", "eventId": result["technical"]["eventId"]
    }

def test_game_reaction_manager_never_reuses_a_phrase_within_same_match():
    script = """
global.window = {};
require(process.argv[1]);
const manager = new window.CeliaGameBridge.ReactionManager();
const outputs = [];
for (let index = 0; index < 5; index += 1) {
  outputs.push(manager.decide(
    {match_id: 'm-repeat', last_move: 'move-' + index, history: Array(index + 1).fill({})},
    'turn-' + index,
    'Essa confianca toda costuma cobrar juros.',
    'teasing',
    'SHORT',
    false
  ).text);
}
process.stdout.write(JSON.stringify(outputs));
"""
    outputs = json.loads(
        subprocess.check_output(["node", "-e", script, str(_BRIDGE)], text=True)
    )
    spoken = [item for item in outputs if item]
    assert len(spoken) == len(set(spoken))
    assert spoken.count("Essa confianca toda costuma cobrar juros.") == 1


def test_commentary_failure_never_blocks_confirmed_move():
    script = """
global.window = {};
const order = [];
const state = {
  match_id: 'm-timeout', current_player: 'celia', legal_actions: ['4'],
  history: [], board: [null], board_revision: 0, mode: 'criador-vs-celia', last_move: null
};
global.fetch = async (url, options = {}) => {
  if (String(url).includes('/session')) return {ok:true,json:async()=>({ok:true,active:true,match_id:'m-timeout',authority_epoch:1,board_revision:0})};
  if (url === '/bridge/game/action') return {ok: true, json: async () => ({action: '4'})};
  if (url === '/bridge/process') return {ok: true, json: async () => ({response: '{"commentary":"Agora tenta sair dessa.","reaction":"short"}'})};
  if (url === '/bridge/speak') { order.push('speak'); return {ok:true,json:async()=>({ok:false,error:'tts_failed'})}; }
  return {ok: true, json: async () => ({ok: true, command: null})};
};
require(process.argv[1]);
const adapter = {
  getState: () => state, getCurrentPlayer: () => state.current_player,
  isGameOver: () => false, setThinking: () => {},
  applyCeliaAction: async (action) => {
    order.push('apply'); state.current_player='criador'; state.legal_actions=[]; state.board_revision+=1; return true;
  },
  recordBridgeError: async () => {}, applyFallback: async () => false
};
(async () => {
  const bridge = new window.CeliaGameBridge('jogo da velha', adapter);
  const ok = await bridge.play('Nova partida.');
  await new Promise(resolve => setTimeout(resolve, 30));
  process.stdout.write(JSON.stringify({order, ok}));
})();
"""
    output = subprocess.check_output(["node", "-e", script, str(_BRIDGE)], text=True, timeout=5)
    result = json.loads(output.splitlines()[-1])
    assert result["ok"] is True
    assert result["order"][0] == "apply"
    assert "speak" in result["order"]

def test_explicit_play_control_starts_exactly_one_authoritative_turn():
    script = """
global.window = {};
const calls = [];
const state = {
  match_id: 'm-play', current_player: 'celia', legal_actions: ['4'],
  history: [], board: [null], mode: 'criador-vs-celia', last_move: null
};
global.fetch = async () => ({ok: true, json: async () => ({ok: true, command: null})});
require(process.argv[1]);
const adapter = {
  getState: () => state,
  getCurrentPlayer: () => state.current_player,
  isGameOver: () => false,
  setThinking: () => {},
  recordBridgeError: async () => {},
  applyFallback: async () => false
};
(async () => {
  const bridge = new window.CeliaGameBridge('jogo da velha', adapter);
  bridge.sessionAuthoritative = true;
  bridge.play = async (eventSummary) => { calls.push(eventSummary); return true; };
  await bridge.applyControl({match_id: 'm-play', sequence: 7, action: 'play'});
  await new Promise((resolve) => setTimeout(resolve, 10));
  await bridge.applyControl({match_id: 'm-play', sequence: 7, action: 'play'});
  await new Promise((resolve) => setTimeout(resolve, 10));
  process.stdout.write(JSON.stringify({calls, sequence: bridge.controlSequence}));
})();
"""
    result = json.loads(
        subprocess.check_output(["node", "-e", script, str(_BRIDGE)], text=True, timeout=5)
    )
    assert result["calls"] == ["Comando explicito: Celia, joga."]
    assert result["sequence"] == 7


def test_authoritative_browser_release_is_exact_and_keepalive():
    script = """
global.window = {};
const calls = [];
const state = {
  match_id: 'm-release', current_player: 'celia', legal_actions: ['4'],
  history: [], board: [null], mode: 'criador-vs-celia', last_move: null
};
global.fetch = async (url, options = {}) => {
  calls.push({url, keepalive: options.keepalive === true, body: JSON.parse(options.body)});
  return {ok: true, json: async () => ({ok: true, released: true})};
};
require(process.argv[1]);
const adapter = {
  getState: () => state,
  getCurrentPlayer: () => state.current_player,
  isGameOver: () => false,
  setThinking: () => {}
};
(async () => {
  const bridge = new window.CeliaGameBridge('jogo da velha', adapter);
  bridge.sessionAuthoritative = true;
  const released = bridge.releaseSession();
  await new Promise((resolve) => setTimeout(resolve, 10));
  process.stdout.write(JSON.stringify({
    released, authoritative: bridge.sessionAuthoritative, calls, instanceId: bridge.instanceId
  }));
})();
"""
    result = json.loads(
        subprocess.check_output(["node", "-e", script, str(_BRIDGE)], text=True, timeout=5)
    )
    assert result["released"] is True
    assert result["authoritative"] is False
    assert len(result["calls"]) == 1
    assert result["calls"][0]["url"].endswith("/tic-tac-toe/session/release")
    assert result["calls"][0]["keepalive"] is True
    assert result["calls"][0]["body"] == {
        "match_id": "m-release",
        "instance_id": result["instanceId"],
    }


def test_new_match_drops_old_authority_and_requires_fresh_heartbeat():
    script = """
global.window = {};
const calls = [];
const state = {
  match_id: 'm-old', current_player: 'celia', legal_actions: ['4'],
  history: [], board: [null], board_revision: 3, mode: 'criador-vs-celia', last_move: null
};
global.fetch = async (url, options = {}) => {
  calls.push({url, body: options.body ? JSON.parse(options.body) : null});
  if (String(url).includes('/session')) {
    return {ok: true, json: async () => ({
      ok: true, active: true, match_id: state.match_id,
      authority_epoch: state.match_id === 'm-new' ? 12 : 11,
      board_revision: state.board_revision
    })};
  }
  if (url === '/bridge/game/action') {
    return {ok: true, json: async () => ({action: state.legal_actions[0], commentary: 'Agora sim.'})};
  }
  if (url === '/bridge/speak') {
    return {ok: true, json: async () => ({ok: true, speech_id: 'speech-new', audio_duration_sec: 0.01})};
  }
  if (String(url).includes('/bridge/playback/speech-new/status')) {
    return {ok: true, json: async () => ({status: 'ended'})};
  }
  return {ok: true, json: async () => ({ok: true, command: null})};
};
require(process.argv[1]);
const adapter = {
  getState: () => state,
  getCurrentPlayer: () => state.current_player,
  isGameOver: () => false,
  setThinking: () => {},
  applyCeliaAction: async () => true,
  recordBridgeError: async () => {},
  applyFallback: async () => false
};
(async () => {
  const bridge = new window.CeliaGameBridge('xadrez', adapter);
  bridge.sessionAuthoritative = true;
  bridge.authorityEpoch = 11;
  bridge.authorityMatchId = 'm-old';
  bridge.lastAuthorityRevision = 3;

  bridge.beginNewMatch('manual_restart');
  state.match_id = 'm-new';
  state.board_revision = 0;
  state.legal_actions = ['e7e5'];

  const afterReset = {
    authoritative: bridge.sessionAuthoritative,
    authorityMatchId: bridge.authorityMatchId,
    revision: bridge.lastAuthorityRevision
  };
  const ok = await bridge.play('Partida reiniciada.');
  const actionIndex = calls.findIndex(call => call.url === '/bridge/game/action');
  const heartbeatIndex = calls.findIndex(call => String(call.url).includes('/session'));
  process.stdout.write(JSON.stringify({
    afterReset, ok, actionIndex, heartbeatIndex,
    authorityMatchId: bridge.authorityMatchId,
    authorityEpoch: bridge.authorityEpoch,
    revision: bridge.lastAuthorityRevision
  }));
})();
"""
    result = json.loads(
        subprocess.check_output(["node", "-e", script, str(_BRIDGE)], text=True, timeout=5)
    )
    assert result["afterReset"] == {
        "authoritative": False,
        "authorityMatchId": "",
        "revision": -1,
    }
    assert result["ok"] is True
    assert 0 <= result["heartbeatIndex"] < result["actionIndex"]
    assert result["authorityMatchId"] == "m-new"
    assert result["authorityEpoch"] == 12
    assert result["revision"] == 0


def test_play_revalidates_authority_and_applies_before_optional_commentary():
    script = """
global.window = {};
const order = [];
const state = {
  match_id: 'm-live', current_player: 'celia', legal_actions: ['e7e5'],
  history: [], board: [null], board_revision: 5, mode: 'criador-vs-celia', last_move: null
};
global.fetch = async (url, options = {}) => {
  if (String(url).includes('/session')) {
    order.push('heartbeat');
    return {ok: true, json: async () => ({ok:true,active:true,match_id:'m-live',authority_epoch:9,board_revision:5})};
  }
  if (url === '/bridge/game/action') { order.push('decision'); return {ok:true,json:async()=>({action:'e7e5'})}; }
  if (url === '/bridge/process') return {ok:true,json:async()=>({response:'{"commentary":"Sua vez acabou.","reaction":"short"}'})};
  if (url === '/bridge/speak') { order.push('speak'); return {ok:true,json:async()=>({ok:true,speech_id:'speech-order',audio_duration_sec:0.01})}; }
  return {ok: true, json: async () => ({ok: true, command: null})};
};
require(process.argv[1]);
const adapter = {
  getState: () => state, getCurrentPlayer: () => state.current_player,
  isGameOver: () => false, setThinking: () => {},
  applyCeliaAction: async () => { order.push('apply'); state.board_revision=6; state.current_player='criador'; state.legal_actions=[]; return true; },
  recordBridgeError: async () => {}, applyFallback: async () => false
};
(async () => {
  const bridge = new window.CeliaGameBridge('xadrez', adapter);
  bridge.sessionAuthoritative=true; bridge.authorityEpoch=8; bridge.authorityMatchId='m-live'; bridge.lastAuthorityRevision=4;
  const ok = await bridge.play('Célia, joga.');
  await new Promise(resolve => setTimeout(resolve, 30));
  process.stdout.write(JSON.stringify({ok, order}));
})();
"""
    result = json.loads(subprocess.check_output(["node", "-e", script, str(_BRIDGE)], text=True, timeout=5))
    assert result["ok"] is True
    assert result["order"].index("heartbeat") < result["order"].index("decision")
    assert result["order"].index("decision") < result["order"].index("apply")
    assert result["order"].index("apply") < result["order"].index("speak")

def test_restart_invalidation_blocks_old_decision_and_old_finally_cleanup():
    script = """
global.window = {};
const applied = [], thinkingEvents = [];
const state = {
  match_id: 'm-old', current_player: 'celia', legal_actions: ['4'],
  history: [], board: [null], mode: 'criador-vs-celia', last_move: null
};
global.fetch = async (url, options = {}) => {
  if (url === '/bridge/game/action') {
    await new Promise((resolve) => setTimeout(resolve, 70));
    return {ok: true, json: async () => ({action: '4'})};
  }
  return {ok: true, json: async () => ({ok: true, command: null})};
};
require(process.argv[1]);
const adapter = {
  getState: () => state,
  getCurrentPlayer: () => state.current_player,
  isGameOver: () => false,
  setThinking: (value) => thinkingEvents.push(value),
  applyCeliaAction: async (action) => { applied.push(action); return true; },
  recordBridgeError: async () => {},
  applyFallback: async () => false
};
(async () => {
  const bridge = new window.CeliaGameBridge('jogo da velha', adapter);
  const pending = bridge.play('Nova partida.');
  await new Promise((resolve) => setTimeout(resolve, 40));
  bridge.invalidate('manual_restart');
  state.match_id = 'm-new';
  state.board = [null];
  state.legal_actions = ['0'];
  const ok = await pending;
  process.stdout.write(JSON.stringify({applied, thinkingEvents, ok, phase: bridge.phase}));
})();
"""
    result = json.loads(
        subprocess.check_output(["node", "-e", script, str(_BRIDGE)], text=True, timeout=5)
    )
    assert result["applied"] == []
    assert result["ok"] is False
    assert result["thinkingEvents"] == [True, False]


def test_all_integrated_game_inline_scripts_remain_parseable(tmp_path):
    for game in ("tic-tac-toe", "checkers", "chess"):
        html = (_GAMES_DIR / game / "index.html").read_text(encoding="utf-8")
        inline_scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, flags=re.DOTALL | re.IGNORECASE)
        assert inline_scripts, game
        for index, script in enumerate(inline_scripts):
            if not script.strip():
                continue
            path = tmp_path / f"{game}-{index}.js"
            path.write_text(script, encoding="utf-8")
            subprocess.run(["node", "--check", str(path)], check=True, capture_output=True, text=True)
