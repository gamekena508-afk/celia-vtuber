"use strict";

const assert = require("node:assert");
const path = require("node:path");

global.window = {};
global.fetch = async () => ({ ok: true, json: async () => ({ ok: true, command: null }) });
require(path.resolve(__dirname, "..", "integrations", "games", "common", "celia-game-bridge.js"));

function adapterFor(state, hooks = {}) {
  return {
    getState: () => state,
    getCurrentPlayer: () => state.current_player,
    isGameOver: () => false,
    setThinking: () => {},
    recordBridgeError: async () => {},
    applyFallback: async () => false,
    ...hooks,
  };
}

async function controlContract() {
  const state = {
    match_id: "m-control", current_player: "celia", legal_actions: ["6"],
    board_revision: 3, board: ["", "", "", "", "", "", "", "", ""], history: [],
    mode: "criador-vs-celia",
  };
  const calls = [];
  const bridge = new window.CeliaGameBridge("jogo da velha", adapterFor(state));
  bridge.sessionAuthoritative = true;
  bridge.authorityEpoch = 9;
  bridge.play = async (_summary, options) => { calls.push(options); return true; };

  const command = {
    match_id: "m-control", sequence: 1, action: "play", action_id: "action-1",
    instance_id: bridge.instanceId, authority_epoch: 9, board_revision: 3,
    requested_action: "6",
  };
  await bridge.applyControl(command);
  await bridge.applyControl(command);
  await bridge.applyControl({ ...command, sequence: 2, instance_id: "old-tab" });
  await bridge.applyControl({ ...command, sequence: 3, authority_epoch: 8 });
  await bridge.applyControl({ ...command, sequence: 4, board_revision: 2 });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].requestedAction, "6");
  assert.equal(calls[0].actionId, "action-1");
}

async function staleReservationCancels() {
  const state = {
    match_id: "m-stale", current_player: "celia", legal_actions: ["4"],
    board_revision: 1, board: ["", "", "", "", "", "", "", "", ""], history: [],
    mode: "criador-vs-celia", last_move: null,
  };
  let applied = 0;
  const bridge = new window.CeliaGameBridge("jogo da velha", adapterFor(state, {
    applyCeliaAction: async () => { applied += 1; return true; },
  }));
  bridge.sessionAuthoritative = true;
  bridge.authorityEpoch = 2;
  bridge.decide = async () => ({ action: "4", commentary: "vou jogar", emotion: "teasing", reaction: "short" });
  bridge.speakBeforeMove = async () => {
    state.board_revision = 2;
    return true;
  };
  const result = await bridge.play("turno");
  assert.equal(result, false);
  assert.equal(applied, 0);
}

async function reservationCarriesSpokenMove() {
  const state = {
    match_id: "m-ok", current_player: "celia", legal_actions: ["4"],
    board_revision: 7, board: ["", "", "", "", "", "", "", "", ""], history: [],
    mode: "criador-vs-celia", last_move: null,
  };
  let spoken = "";
  let received = null;
  const bridge = new window.CeliaGameBridge("jogo da velha", adapterFor(state, {
    formatReservedCommentary: () => "Vou no centro, casa 5.",
    applyCeliaAction: async (_action, _commentary, _emotion, reservation) => {
      received = reservation;
      return true;
    },
  }));
  bridge.sessionAuthoritative = true;
  bridge.authorityEpoch = 4;
  bridge.decide = async () => ({ action: "4", commentary: "texto livre", emotion: "teasing", reaction: "short" });
  bridge.speakBeforeMove = async (reaction) => {
    spoken = reaction.text;
    return true;
  };
  const result = await bridge.play("turno", { actionId: "reserved-1" });
  assert.equal(result, true);
  assert.equal(spoken, "Vou no centro, casa 5.");
  assert.equal(received.action, "4");
  assert.equal(received.actionId, "reserved-1");
  assert.equal(received.revision, 7);
  assert.equal(received.authorityEpoch, 4);
}

async function failedResultReactionDoesNotAck() {
  const state = {
    match_id: "m-result-fail", current_player: "criador", legal_actions: [],
    board_revision: 9, board: ["X"], history: [], mode: "criador-vs-celia",
    game_over: true, result: "criador", winner_controller: "criador",
  };
  let retry = null;
  let ackCalls = 0;
  const originalFetch = global.fetch;
  global.fetch = async () => {
    ackCalls += 1;
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  try {
    const bridge = new window.CeliaGameBridge("jogo da velha", adapterFor(state));
    bridge.pollControl = async () => {
      bridge.sessionAuthoritative = true;
      bridge.authorityEpoch = 3;
      bridge.authorityMatchId = state.match_id;
      bridge.lastAuthorityRevision = state.board_revision;
    };
    bridge.speakCommentary = async () => ({ playback_status: "error" });
    bridge.scheduleResultRetry = (info, reactionCompleted) => {
      retry = { ...info, _reactionCompleted: Boolean(reactionCompleted) };
    };

    const result = await bridge.finalizeResult({
      matchId: state.match_id, turn: 5, winner: "criador", result: "criador",
    });

    assert.equal(result, false);
    assert.equal(ackCalls, 0);
    assert.equal(retry._reactionCompleted, false);
    assert.equal(bridge.resultAcked.size, 0);
  } finally {
    global.fetch = originalFetch;
  }
}

async function ackConflictRetriesWithoutRepeatingReaction() {
  const state = {
    match_id: "m-result-ack", current_player: "criador", legal_actions: [],
    board_revision: 11, board: ["X"], history: [], mode: "criador-vs-celia",
    game_over: true, result: "criador", winner_controller: "criador",
  };
  let retry = null;
  let speechCalls = 0;
  let ackCalls = 0;
  const originalFetch = global.fetch;
  global.fetch = async (url) => {
    if (!String(url).includes("/result-ack")) {
      return { ok: true, status: 200, json: async () => ({ ok: true, command: null }) };
    }
    ackCalls += 1;
    if (ackCalls === 1) {
      return {
        ok: false,
        status: 409,
        json: async () => ({ detail: { code: "stale_game_revision", field: "board_revision" } }),
      };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true, duplicate: false }) };
  };
  try {
    const bridge = new window.CeliaGameBridge("jogo da velha", adapterFor(state));
    bridge.pollControl = async () => {
      bridge.sessionAuthoritative = true;
      bridge.authorityEpoch = 7;
      bridge.authorityMatchId = state.match_id;
      bridge.lastAuthorityRevision = state.board_revision;
    };
    bridge.speakCommentary = async () => {
      speechCalls += 1;
      return { playback_status: "completed" };
    };
    bridge.scheduleResultRetry = (info, reactionCompleted) => {
      retry = { ...info, _reactionCompleted: Boolean(reactionCompleted) };
    };

    const first = await bridge.finalizeResult({
      matchId: state.match_id, turn: 8, winner: "criador", result: "criador",
    });
    assert.equal(first, false);
    assert.equal(speechCalls, 1);
    assert.equal(retry._reactionCompleted, true);

    const second = await bridge.finalizeResult(retry);
    assert.equal(second, true);
    assert.equal(speechCalls, 1);
    assert.equal(ackCalls, 2);
    assert.equal(bridge.resultAcked.has("m-result-ack|criador"), true);
  } finally {
    global.fetch = originalFetch;
  }
}

(async () => {
  await controlContract();
  await staleReservationCancels();
  await reservationCarriesSpokenMove();
  await failedResultReactionDoesNotAck();
  await ackConflictRetriesWithoutRepeatingReaction();
  console.log("game authority bridge: PASS");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
