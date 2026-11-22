/* Célia Motion Director — direct Cubism motion, no VTube Studio dependency.
 *
 * Ported from the useful behavior in Fake Track V11:
 * procedural breathing/nod/sway/roll/body bob/shoulders/hair, emotion-aware
 * preset scheduling, micro-events and three runtime states.
 *
 * Ownership:
 * - face expressions: celia-face.js
 * - mouth: celia-lipsync.js
 * - stuck-eye safety: celia-olhos.js
 * - model matrix pose: celia-live2d-body.js
 * - this file: exclusive head/body drivers before physics; accents before flush.
 */
(function installCeliaMotionDirector() {
  "use strict";

  const VERSION = "motion-director-electron-v12.14-confused-once";
  const STATES = Object.freeze({ IDLE: "IDLE", SPEAKING: "SPEAKING", REACTION: "REACTION" });
  // Stage gain: the first direct-Cubism pass was intentionally conservative.
  // V2 keeps the same emotional choreography but makes body language camera-readable.
  const MOTION_GAIN = Object.freeze({
    // V5: still visibly rebellious, but only on semantic DRIVER parameters.
    // Physics outputs (hair/chest/hips/shoulders) are no longer forced directly.
    head: 1.90, bodyBob: 2.10, shoulder: 2.15, hair: 1.00,
    speaking: 1.62, reaction: 1.85, eye: 1.10, rebel: .38,
  });
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const randomBetween = (min, max) => min + Math.random() * (max - min);
  const wallNowMs = () => (typeof performance !== "undefined" && performance.now ? performance.now() : Date.now());
  // Intentional gestures run on real wall time. Procedural texture keeps its own
  // slower clock so "calm" no longer means globally sedated transitions.
  const MOTION_SPEED = 1.0;
  const PROCEDURAL_SPEED = .82;
  const VERTICAL_PROCEDURAL_SPEED = .62; // keep vertical bob inside the proven jerk envelope
  const EMOTION_HOLD_MS = 7200;
  const EMOTION_TRANSITION_MS = 1450;
  const nowMs = () => wallNowMs();
  const minimumJerk = value => {
    const t = clamp(value, 0, 1);
    return t * t * t * (10 + t * (-15 + t * 6));
  };
  const smoothStep = value => {
    const t = clamp(value, 0, 1);
    return t * t * (3 - 2 * t);
  };

  function mp(category, breathAmp, breathFreq, nodAmp, nodFreq, swayAmp, swayFreq,
              rollAmp, rollFreq, bobAmp, shoulderAmp, hairAmp, hairFreq, chaos = 0) {
    return { category, breathAmp, breathFreq, nodAmp, nodFreq, swayAmp, swayFreq,
      rollAmp, rollFreq, bobAmp, shoulderAmp, hairAmp, hairFreq, chaos };
  }

  // Qualified V11 presets. Values are preserved as source material, then scaled
  // by the direct-Cubism mixer so the browser result stays natural.
  const PRESETS = Object.freeze({
    "Idle Base": mp("Idle",3.4,.90,1.5,1.10,5.5,.55,1.9,.36,10,7,7,.90),
    "Idle Suave": mp("Idle",2.4,.70,1,.85,3.8,.42,1.4,.28,7.5,5,5,.70),
    "Idle Lento": mp("Idle",3,.45,1.2,.60,5,.30,1.7,.20,9,6,6,.45),
    "Idle Ativo": mp("Idle",4.2,1.20,2.2,1.40,6.8,.75,2.5,.48,13,9,9.5,1.20,.15),
    "Idle Amplo": mp("Idle",6,.85,2.8,1.05,8.5,.60,3.5,.38,15,10,11,.85,.10),
    "Idle Float": mp("Idle",2.6,.62,1.4,.78,4.5,.48,2.2,.31,9.5,5.5,7.5,.62),
    "Idle Desequilibrado": mp("Idle",3.2,.77,1.8,1.33,6,.53,2.6,.67,11,6.5,7,.88,.30),
    "Idle Maré": mp("Idle",3.8,.58,1.5,.72,7.5,.40,2.8,.25,12.5,9,8.5,.58),
    "Idle Arena": mp("Idle",4.5,1.10,2.5,1.35,7.5,.82,2.8,.52,14,10,10.5,1.35,.20),
    "Idle Âncora": mp("Idle",2.8,.65,1.2,.80,7,.45,2.5,.28,11.5,8.5,7,.65),
    "Nod Bounce": mp("Aceno",2.6,.85,7,1.35,5,.62,1.8,.40,13,7.5,12,1.35,.15),
    "Nod Entusiasta": mp("Aceno",2.6,.80,7.5,1.60,5,.55,1.6,.35,13.5,7,13,1.60,.15),
    "Nod Duplo": mp("Aceno",1.6,.60,5.5,2.10,3,.42,1.2,.28,9.5,4.5,8.5,2.10),
    "Nod Triplo": mp("Aceno",2,.65,6,2.80,3.5,.45,1.3,.30,10,5,9.5,2.80,.10),
    "Nod Ultra": mp("Aceno",3,.90,9,1.80,5.5,.65,2,.42,15,8,14,1.80,.20),
    "Nod Groove": mp("Aceno",2.5,1,6.5,1.50,5.5,.78,1.8,.50,12,7,11,1.50,.18),
    "Nod Trance": mp("Aceno",2.5,.70,6,1.10,5,.52,3,.35,11,6.5,10,1.10,.10),
    "Nod Pesado": mp("Aceno",2.8,.60,8.5,.90,4.5,.45,2.2,.28,14,7,12,.90,.10),
    "Sway Relaxado": mp("Balanço",3.2,.50,1.4,.62,10,.38,3.5,.24,12.5,10,7.5,.50),
    "Sway Dançante": mp("Balanço",2.6,.90,2,1.15,9.5,.72,2.8,.48,12,10,9.5,1.15,.15),
    "Sway Metrônomo": mp("Balanço",1.4,.70,.7,.70,9,.70,1.5,.35,7.5,9.5,5,.70),
    "Sway Clube": mp("Balanço",2.5,.95,1.8,1.20,9.5,.85,2.5,.55,11.5,10,9,1.20,.15),
    "Sway Arco Amplo": mp("Balanço",2.8,.68,1.5,.85,13,.52,4,.34,13.5,13,9.5,.85,.10),
    "Sway Navio": mp("Balanço",3.5,.45,1,.55,12,.32,5,.20,14,12,8.5,.45),
    "Sway Acalanto": mp("Balanço",2.5,.40,.9,.50,11,.28,4,.18,12,11,8,.40),
    "Sway Lento Dramático": mp("Balanço",3.5,.55,1.5,.68,12.5,.40,5,.25,14,12.5,9,.55,.12),
    "Roll Fluida": mp("Rotação",2.6,.65,1.6,.80,4.8,.45,7.5,.32,10,5,7,.80),
    "Roll Dramática": mp("Rotação",3.5,.72,2.2,.88,5.5,.50,11,.38,12.5,5.5,9,.88,.15),
    "Roll Tilt Extremo": mp("Rotação",2,.55,1.5,.68,3.5,.40,12,.25,8,3.5,6,.68),
    "Roll Bebum": mp("Rotação",4,.68,2.5,.85,7,.50,9,.42,12,7.5,8.5,.85,.40),
    "Roll Pescoço": mp("Rotação",2,.85,1.5,1.05,4,.62,9,.52,9,4,7,1.05,.15),
    "Hair Sacudindo": mp("Cabelo",3.2,1.10,3.2,1.35,6.5,.80,2.4,.52,12,6.5,16,1.35,.20),
    "Hair Pesado": mp("Cabelo",3,.55,2.8,.70,6,.40,2.5,.25,11,6,13,.55),
    "Hair Tempestade": mp("Cabelo",4,1.20,4,1.50,7.5,.90,3,.60,14,7.5,18,1.50,.25),
    "Hair Cascata": mp("Cabelo",2.4,.48,2,.60,5.5,.35,2.8,.22,10.5,6,13.5,.48),
    "Hair Chicote": mp("Cabelo",3.5,1.30,4.5,1.60,6.5,.95,2.5,.62,13,6.5,17.5,1.60,.25),
    "Hair Ultra": mp("Cabelo",4.5,1.25,5,1.55,8,.95,3,.62,16,8,20,1.55,.30),
    "Bob Ondular": mp("Corpo",2.6,.65,1.4,.80,5.5,.48,2.8,.30,14,7,7.5,.65),
    "Bob Saltitante": mp("Corpo",3.6,1.05,2.8,1.25,6,.78,2.2,.50,18,9,11,1.25,.12),
    "Bob Lento": mp("Corpo",2.4,.42,1.2,.52,4,.30,2,.19,14.5,8,6,.42),
    "Bob Terremoto": mp("Corpo",5,1.10,4,1.35,7,.82,3,.55,22,12,14,1.35,.30),
    "Bob Pistão": mp("Corpo",3,1.15,2,1.40,4.5,.85,1.6,.58,17,8,9,1.40,.10),
    "Excited Pop": mp("Combo",4,1.20,5,1.50,9,.90,3,.60,17,9.5,15,1.50,.22),
    "Confident Stride": mp("Combo",3,.90,4,1.15,7.5,.68,2.5,.45,14.5,8.5,10.5,1.15,.10),
    "Dance Light": mp("Combo",3.5,1,3.5,1.25,8.5,.78,2.8,.50,15,9.5,13,1.25,.18),
    "Drunk Mode": mp("Combo",4.5,.65,3.5,.83,10,.50,6,.40,14.5,10.5,10,.83,.40),
    "Dramatic Enter": mp("Combo",5,.78,5.5,.95,9.5,.58,4.5,.38,18,11,16.5,.95,.25),
    "Nervous Wreck": mp("Combo",2,1.50,1.8,1.80,3.5,1.10,1.5,.80,7.5,3.5,4.5,1.80,.30),
    "Arena Hype": mp("Combo",5,1.25,6,1.55,10,.95,3.5,.62,20,11.5,17,1.55,.30),
    "Possessão": mp("Combo",4,.90,3.5,1.15,8,.68,7,.55,13,7.5,12,1.15,.45),
    "Ultra Vibe": mp("Combo",4.5,1.15,5.5,1.40,9.5,.88,3.5,.58,19,11,16,1.40,.25),
    "Tempestade": mp("Combo",5.5,1.10,5,1.35,9,.82,4.5,.55,20,11.5,17.5,1.35,.35),
    "Full Send": mp("Combo",6,1.30,7,1.60,10.5,1,4,.65,22,13,18.5,1.60,.35),
    "Onda Total": mp("Combo",4,.85,4.5,1.05,9.5,.65,4.5,.42,17.5,10.5,14.5,1.05,.20),
    "Final Épico": mp("Combo",6.5,1,6.5,1.25,11,.75,5,.50,22,13,20,1.25,.30),
  });

  const PROFILES = Object.freeze({
    neutral: { amp:1.18, energy:.60, idle:["Idle Ativo","Idle Amplo","Sway Dançante","Bob Ondular","Roll Fluida","Hair Sacudindo","Dance Light"], speaking:["Sway Dançante","Bob Saltitante","Nod Bounce","Roll Pescoço","Hair Sacudindo","Dance Light"], reaction:["Idle Ativo","Roll Fluida","Hair Sacudindo"], posture:{x:0,y:0,z:0,body:0} },
    happy: { amp:1.65, energy:.96, idle:["Ultra Vibe","Excited Pop","Dance Light","Bob Saltitante","Sway Dançante","Full Send"], speaking:["Ultra Vibe","Excited Pop","Nod Ultra","Dance Light","Full Send"], reaction:["Excited Pop","Nod Ultra","Full Send","Final Épico"], posture:{x:0,y:3,z:0,body:1.2} },
    in_love: { amp:.68, energy:.58, idle:["Sway Acalanto","Sway Relaxado","Idle Float","Roll Fluida"], speaking:["Sway Acalanto","Roll Fluida","Idle Float"], reaction:["Roll Fluida"], posture:{x:0,y:1.5,z:1.2,body:.6} },
    sad: { amp:.44, energy:.18, idle:["Idle Lento","Sway Lento Dramático","Bob Lento","Hair Cascata"], speaking:["Idle Lento","Bob Lento","Nod Pesado"], reaction:["Nod Pesado"], posture:{x:0,y:-4,z:-.8,body:-1} },
    angry: { amp:.62, energy:.72, idle:["Idle Âncora","Nod Pesado","Confident Stride"], speaking:["Idle Âncora","Nod Pesado","Confident Stride"], reaction:["Nod Pesado"], posture:{x:0,y:1,z:0,body:1.5} },
    surprised: { amp:1.00, energy:.86, idle:["Idle Ativo","Idle Base"], speaking:["Nod Ultra","Bob Saltitante","Excited Pop"], reaction:["Dramatic Enter","Nod Ultra"], posture:{x:0,y:4,z:0,body:-.5} },
    confused: { amp:.58, energy:.42, idle:["Idle Desequilibrado","Roll Tilt Extremo","Idle Suave"], speaking:["Roll Bebum","Roll Tilt Extremo","Nervous Wreck"], reaction:["Roll Tilt Extremo"], posture:{x:0,y:0,z:1.4,body:.2} },
    scared: { amp:.55, energy:.62, idle:["Nervous Wreck","Idle Desequilibrado","Bob Lento"], speaking:["Nervous Wreck","Hair Chicote","Bob Pistão"], reaction:["Nervous Wreck"], posture:{x:0,y:2,z:-.7,body:-1.5} },
    teasing: { amp:1.45, energy:.90, idle:["Confident Stride","Sway Dançante","Roll Pescoço","Dance Light","Ultra Vibe"], speaking:["Confident Stride","Roll Pescoço","Dance Light","Ultra Vibe"], reaction:["Roll Pescoço","Dramatic Enter"], posture:{x:0,y:1,z:1.4,body:1.4} },
    funny: { amp:1.55, energy:.94, idle:["Bob Saltitante","Sway Dançante","Nod Bounce","Ultra Vibe","Full Send"], speaking:["Bob Saltitante","Nod Bounce","Dance Light","Excited Pop","Full Send"], reaction:["Bob Saltitante","Nod Ultra","Final Épico"], posture:{x:0,y:1,z:.8,body:.9} },
    holding_back_laughter: { amp:.65, energy:.55, idle:["Idle Desequilibrado","Bob Ondular","Nod Duplo"], speaking:["Nod Duplo","Bob Ondular","Sway Relaxado"], reaction:["Nod Duplo"], posture:{x:0,y:0,z:.7,body:.3} },
    passionate: { amp:.90, energy:.72, idle:["Idle Amplo","Onda Total","Roll Dramática"], speaking:["Onda Total","Dramatic Enter","Roll Dramática"], reaction:["Dramatic Enter"], posture:{x:0,y:2,z:.6,body:1.2} },
  });

  const EMOTION_ALIASES = Object.freeze({
    trol_idle: "teasing", trol_speaking: "teasing", excited: "happy",
    calm: "neutral", nervous: "scared", confident: "teasing",
  });

  // Semantic pose phrases are excursions, not permanent postures. Each gesture
  // has explicit travel/hold/return timing and is rendered with minimum jerk.
  const POSE_PHRASES = Object.freeze({
    center:       { headX: 0,    headY: 0,    headZ: 0,    bodyX: 0,    bodyY: 0,    bodyZ: 0,    side: 0,  move: 520, hold: 900,  back: 760,  cost: 0 },
    turn_left:    { headX: -5.8, headY: .1,   headZ: .4,   bodyX: -.65, bodyY: 0,    bodyZ: -.2,  side: -1, move: 430, hold: 820,  back: 920,  cost: .22 },
    turn_right:   { headX: 5.8,  headY: .1,   headZ: -.4,  bodyX: .65,  bodyY: 0,    bodyZ: .2,   side: 1,  move: 430, hold: 820,  back: 920,  cost: .22 },
    tilt_left:    { headX: -.5,  headY: .2,   headZ: -4.2, bodyX: -.35, bodyY: 0,    bodyZ: .65,  side: -1, move: 560, hold: 1050, back: 1080, cost: .24 },
    tilt_right:   { headX: .5,   headY: .2,   headZ: 4.2,  bodyX: .35,  bodyY: 0,    bodyZ: -.65, side: 1,  move: 560, hold: 1050, back: 1080, cost: .24 },
    lean_forward: { headX: 0,    headY: -3.0, headZ: 0,    bodyX: 0,    bodyY: .42,  bodyZ: 0,    side: 0,  move: 920, hold: 760,  back: 1320, cost: .34 },
    lean_back:    { headX: 0,    headY: 2.5,  headZ: 0,    bodyX: 0,    bodyY: -.30, bodyZ: 0,    side: 0,  move: 820, hold: 820,  back: 1180, cost: .30 },
  });

  const POSE_WEIGHTS = Object.freeze({
    neutral: { center:5, turn_left:1, turn_right:1, tilt_left:.7, tilt_right:.7, lean_forward:.55, lean_back:.35 },
    happy: { center:2.6, turn_left:1, turn_right:1, tilt_left:.8, tilt_right:.8, lean_forward:1.5, lean_back:.35 },
    in_love: { center:4, turn_left:.7, turn_right:.7, tilt_left:1.1, tilt_right:1.1, lean_forward:1.15, lean_back:.25 },
    sad: { center:6, turn_left:.35, turn_right:.35, tilt_left:.75, tilt_right:.75, lean_forward:.15, lean_back:1.1 },
    angry: { center:4.5, turn_left:.55, turn_right:.55, tilt_left:.35, tilt_right:.35, lean_forward:.95, lean_back:.35 },
    surprised: { center:3.5, turn_left:.5, turn_right:.5, tilt_left:.4, tilt_right:.4, lean_forward:.45, lean_back:1.3 },
    confused: { center:3.5, turn_left:.7, turn_right:.7, tilt_left:2.2, tilt_right:2.2, lean_forward:.55, lean_back:.45 },
    scared: { center:6, turn_left:.18, turn_right:.18, tilt_left:.35, tilt_right:.35, lean_forward:.1, lean_back:1.65 },
    teasing: { center:2.8, turn_left:.75, turn_right:.75, tilt_left:1.3, tilt_right:1.3, lean_forward:1.9, lean_back:.25 },
    funny: { center:2.5, turn_left:.9, turn_right:.9, tilt_left:.8, tilt_right:.8, lean_forward:1.45, lean_back:.4 },
    holding_back_laughter: { center:4, turn_left:.5, turn_right:.5, tilt_left:1.0, tilt_right:1.0, lean_forward:.85, lean_back:.3 },
    passionate: { center:3, turn_left:.75, turn_right:.75, tilt_left:.9, tilt_right:.9, lean_forward:1.55, lean_back:.25 },
  });

  const GAZE_MAX_X_DEG = 22;
  const GAZE_MAX_Y_DEG = 14;
  const GAZE_EYE_ONLY_DEG = 10;
  const GAZE_EYE_LIMIT = .82;
  const HEAD_STABILIZATION_RATIO = .46;
  const TELEMETRY_INTERVAL_MS = 5000;
  const PRESET_TRANSITION_MS = 900;
  const BLINK_MIN_INTERVAL_MS = 2600;
  const CONFUSED_SHAKE_DURATION_MS = 1680;
  const CONFUSED_SHAKE_AMPLITUDE = 5.4;
  const ACTING_RHYTHM = Object.freeze({
    // rest means "no intentional pose", never "freeze the character".
    neutral:{rest:.12,micro:1.15,idle:1.00,ears:1.12}, happy:{rest:.05,micro:.95,idle:1.10,ears:1.32},
    in_love:{rest:.18,micro:1.25,idle:.88,ears:1.00}, sad:{rest:.28,micro:1.45,idle:.70,ears:.82},
    angry:{rest:.12,micro:1.25,idle:.92,ears:1.10}, surprised:{rest:.10,micro:1.00,idle:1.00,ears:1.34},
    confused:{rest:.14,micro:1.10,idle:.92,ears:1.14}, scared:{rest:.18,micro:1.10,idle:.95,ears:1.18},
    teasing:{rest:.05,micro:.95,idle:1.12,ears:1.40}, funny:{rest:.05,micro:.90,idle:1.14,ears:1.36},
    holding_back_laughter:{rest:.15,micro:1.15,idle:.94,ears:1.14}, passionate:{rest:.08,micro:1.05,idle:1.05,ears:1.22},
  });

  const runtime = {
    core: null, cubism: null, mapped: null, emotion: "neutral", emotionSource: "startup",
    state: STATES.IDLE, preset: "Idle Base", previousPreset: "Idle Base", previousPresets: [], nextPresetAt: 0, presetTransitionStartedAt: 0, presetTransitionUntil: 0,
    reactionUntil: 0, reactionStartedAt: 0, speechEnergy: 0, speechSlope: 0,
    confusedShakeStartedAt: 0, confusedShakeUntil: 0, confusedShakeTurnKey: "",
    speechRunning: false, speechStartedAt: 0, speechStoppedAt: 0, speechDrive: 0, speechSlopeDrive: 0,
    breathAnticipationUntil: 0, breathRecoveryUntil: 0, breathScale: 1, activity: .62, lastSpeechAt: 0,
    emotionHoldUntil: 0, previousEmotion: "neutral", emotionTransitionStartedAt: 0,
    emotionTransitionUntil: 0, lastFrameAt: 0, phase: Math.random() * Math.PI * 2,
    gazeTargetX: 0, gazeTargetY: 0, gazeEyeX: 0, gazeEyeY: 0, gazeHeadX: 0, gazeHeadY: 0,
    gazeRawEyeX: 0, gazeRawEyeY: 0, gazeHeadFromX: 0, gazeHeadFromY: 0, gazeHeadGoalX: 0, gazeHeadGoalY: 0,
    gazeMode: "camera", gazeHeadMoveStartedAt: 0, gazeHeadMoveUntil: 0, gazeHoldUntil: 0, nextIdleGazeAt: 0, gazeLastSide: 0,
    weightShift: 0, weightTarget: 0, nextWeightAt: 0, headStabilization: 0,
    arousal: .30, motionBudget: 1, gestureCooldownUntil: 0,
    poseName: "center", previousPoses: [], poseLastSide: 0, nextPoseAt: 0, posePhase: "rest",
    poseHeadX: 0, poseHeadY: 0, poseHeadZ: 0, poseBodyX: 0, poseBodyY: 0, poseBodyZ: 0,
    poseTransition: null, poseHoldUntil: 0,
    microEvent: null, microUntil: 0, nextMicroAt: 0, lastBlinkRequestAt: -Infinity,
    contributions: {}, frames: 0, installs: 0, smooth: {}, physics: null, hookStage: null,
    hairMotionEnergy: 0, hairMotionDirection: 0, hairMotionLastAt: 0, hairMotionPrev: null, hairMotionPrevVelocity: null,
    hairGhostX: 0, hairGhostY: 0, hairGhostZ: 0, hairGhostVX: 0, hairGhostVY: 0, hairGhostVZ: 0,
    hairImpulseEnergy: 0, hairGhostInputHooks: 0,
    bangMidFilteredVelocity: null,
    bangMidVelocityX: 0, bangMidVelocityY: 0, bangMidVelocityZ: 0,
    bangMidAccelerationX: 0, bangMidAccelerationY: 0, bangMidAccelerationZ: 0,
    bangMidImpulseEnergy: 0,
    bangMidGhostX: 0, bangMidGhostY: 0, bangMidGhostZ: 0,
    bangMidGhostVX: 0, bangMidGhostVY: 0, bangMidGhostVZ: 0,
    metricSamples: 0, metricStill: 0, metricHeadXSum: 0, metricHeadZSum: 0,
    metricNeutralHeadX: 0, metricNeutralHeadZ: 0, metricEyeSamples: 0, metricEyeClipSamples: 0, metricEyeMaxAbs: 0,
    metricHeadXSide: 0, metricHeadXSideMs: 0, metricHeadXMaxSameSideMs: 0,
    metricHeadZSide: 0, metricHeadZSideMs: 0, metricHeadZMaxSameSideMs: 0,
    metricMaxJerk: {}, metricPrev: null, metricPrevVelocity: null, metricPrevAcceleration: null,
    metricTraceHeadX: [], metricTraceHeadZ: [], metricTraceBodyY: [], metricNextTraceAt: 0,
  };

  function getCore() {
    try {
      const adapter = typeof window.getLAppAdapter === "function" ? window.getLAppAdapter() : null;
      const lapp = adapter && typeof adapter.getModel === "function" ? adapter.getModel() : null;
      const cubism = lapp && lapp._model;
      const core = cubism && typeof cubism.getModel === "function" ? cubism.getModel() : null;
      return core && core.parameters ? { cubism, core, lapp } : null;
    } catch (_) {
      return null;
    }
  }

  function descriptor(core, id) {
    const ids = Array.from(core.parameters.ids || []);
    const index = ids.indexOf(id);
    if (index < 0) return null;
    return {
      id, index,
      min: Number(core.parameters.minimumValues?.[index] ?? -Infinity),
      max: Number(core.parameters.maximumValues?.[index] ?? Infinity),
      default: Number(core.parameters.defaultValues?.[index] ?? 0),
    };
  }

  function firstDescriptor(core, aliases) {
    for (const id of aliases) {
      const found = descriptor(core, id);
      if (found) return found;
    }
    return null;
  }

  function mapParameters(core) {
    const map = {
      headX: firstDescriptor(core, ["AngleX","ParamAngleX"]),
      headY: firstDescriptor(core, ["AngleY","ParamAngleY"]),
      headZ: firstDescriptor(core, ["AngleZ","ParamAngleZ"]),
      bodyX: firstDescriptor(core, ["ParamBodyAngleX"]),
      bodyY: firstDescriptor(core, ["Param93"]),
      bodyZ: firstDescriptor(core, ["Param94"]),
      hipSway: firstDescriptor(core, ["ParamBodyAngleZ2"]),
      shoulderSway: firstDescriptor(core, ["ParamBodyAngleZ3"]),
      upperBodyX: firstDescriptor(core, ["ParamBodyAngleX3"]),
      bodyHeight: firstDescriptor(core, ["Param154"]),
      breath: firstDescriptor(core, ["ParamBreath"]),
      starCycle: firstDescriptor(core, ["Param310"]),
      eyeX: firstDescriptor(core, ["ParamEyeBallX"]),
      eyeY: firstDescriptor(core, ["ParamEyeBallY"]),
      eyeL: firstDescriptor(core, ["ParamEyeLOpen"]),
      eyeR: firstDescriptor(core, ["ParamEyeROpen"]),
      ears: ["Param283","Param284","Param285","Param286"].map(id => descriptor(core,id)).filter(Boolean),
      boobs: ["Param107","Param108","Param109","Param110"].map(id => descriptor(core,id)).filter(Boolean),
      hair: ["Param158","Param159","Param160","Param194","Param195","Param196",
             "Param163","Param164","Param189","Param190"].map(id => descriptor(core,id)).filter(Boolean),
      // The three visible face-hair chains the user wants exaggerated:
      // forehead bang + left/right strands beside the eyes.
      faceHair: ["Param155","Param156","Param157",
                 "Param158","Param159","Param160",
                 "Param194","Param195","Param196"].map(id => descriptor(core,id)).filter(Boolean),
      neckHairL: ["Param178","Param179","Param180","Param181"].map(id => descriptor(core,id)).filter(Boolean),
      neckHairR: ["Param184","Param185","Param186","Param187"].map(id => descriptor(core,id)).filter(Boolean),
    };
    return map;
  }

  function add(mix, channel, value) {
    if (!Number.isFinite(value) || Math.abs(value) < 0.00001) return;
    mix[channel] = (mix[channel] || 0) + value;
  }

  function writeAdditive(core, desc, delta) {
    if (!desc || !Number.isFinite(delta)) return;
    const values = core.parameters.values;
    const base = Number(values[desc.index]) || 0;
    values[desc.index] = clamp(base + delta, desc.min, desc.max);
  }

  function writeAbsoluteCeiling(core, desc, ceiling) {
    if (!desc || !Number.isFinite(ceiling)) return;
    const values = core.parameters.values;
    const base = Number(values[desc.index]) || 0;
    values[desc.index] = clamp(Math.min(base, ceiling), desc.min, desc.max);
  }

  function profileFor(emotion) {
    return PROFILES[emotion] || PROFILES.neutral;
  }

  function profile() {
    return profileFor(runtime.emotion);
  }

  function blendedProfile(now) {
    const current = profileFor(runtime.emotion);
    const previous = profileFor(runtime.previousEmotion);
    if (runtime.previousEmotion === runtime.emotion || now >= runtime.emotionTransitionUntil) return current;
    const span = Math.max(1, runtime.emotionTransitionUntil - runtime.emotionTransitionStartedAt);
    const mix = smoothStep((now - runtime.emotionTransitionStartedAt) / span);
    const blendPosture = {};
    for (const key of ["x", "y", "z", "body"]) {
      blendPosture[key] = (previous.posture?.[key] || 0) * (1 - mix) + (current.posture?.[key] || 0) * mix;
    }
    return {
      ...current,
      amp: previous.amp * (1 - mix) + current.amp * mix,
      energy: previous.energy * (1 - mix) + current.energy * mix,
      posture: blendPosture,
    };
  }

  function choosePreset(state, force = false) {
    const p = profile();
    const pool = (p[state.toLowerCase()] || p.idle || []).filter(name => PRESETS[name]);
    if (!pool.length) return "Idle Base";
    let candidates = pool.filter(name => !runtime.previousPresets.includes(name));
    if (!candidates.length) candidates = pool.slice();
    if (!force && candidates.includes(runtime.preset) && candidates.length > 1) {
      candidates = candidates.filter(name => name !== runtime.preset);
    }
    const next = candidates[Math.floor(Math.random() * candidates.length)] || pool[0];
    if (next !== runtime.preset) {
      runtime.previousPreset = runtime.preset;
      runtime.presetTransitionStartedAt = nowMs();
      runtime.presetTransitionUntil = runtime.presetTransitionStartedAt + PRESET_TRANSITION_MS * randomBetween(.88, 1.12);
      runtime.preset = next;
    }
    runtime.previousPresets.push(next);
    runtime.previousPresets = runtime.previousPresets.slice(-3);
    const baseDelay = state === STATES.SPEAKING ? randomBetween(900, 1900) : randomBetween(1200, 2600);
    runtime.nextPresetAt = nowMs() + baseDelay;
    return next;
  }

  function setState(next) {
    if (runtime.state === next) return;
    runtime.state = next;
    // Do not swap the procedural texture on the exact same frame as a state edge.
    // Schedule it shortly after so speech/reaction envelopes can settle first.
    runtime.nextPresetAt = Math.min(runtime.nextPresetAt || Infinity, nowMs() + 240);
  }

  function normalizedEmotion(raw) {
    const value = String(raw || "neutral").trim().toLowerCase();
    const alias = EMOTION_ALIASES[value] || value;
    return PROFILES[alias] ? alias : "neutral";
  }

  function emotionTurnKey(metadata = {}) {
    const nested = metadata.metadata && typeof metadata.metadata === "object"
      ? metadata.metadata
      : {};
    return String(
      nested.turn_id
      || nested.response_id
      || metadata.turn_id
      || metadata.response_id
      || ""
    ).trim();
  }

  function setEmotion(raw, metadata = {}) {
    const next = normalizedEmotion(raw);
    const now = nowMs();
    const changed = next !== runtime.emotion;
    const source = String(metadata.source || metadata.origem || runtime.emotionSource || "unknown").trim().toLowerCase();
    const turnKey = emotionTurnKey(metadata);
    if (changed) {
      runtime.previousEmotion = runtime.emotion;
      runtime.emotionTransitionStartedAt = now;
      runtime.emotionTransitionUntil = now + EMOTION_TRANSITION_MS;
    }
    runtime.emotion = next;
    runtime.emotionSource = source || "unknown";

    const newConfusedTurn = (
      next === "confused"
      && source !== "sincronizacao"
      && (
        (turnKey && turnKey !== runtime.confusedShakeTurnKey)
        || (!turnKey && changed)
      )
    );
    if (newConfusedTurn) {
      runtime.confusedShakeStartedAt = now;
      runtime.confusedShakeUntil = now + CONFUSED_SHAKE_DURATION_MS;
      runtime.confusedShakeTurnKey = turnKey || `emotion-edge:${Math.round(now)}`;
    } else if (next !== "confused") {
      runtime.confusedShakeStartedAt = 0;
      runtime.confusedShakeUntil = 0;
      runtime.confusedShakeTurnKey = "";
    }
    if (next !== "neutral") {
      const ttl = Number(metadata.ttlSec || metadata.ttl_sec || 0);
      runtime.emotionHoldUntil = now + Math.max(EMOTION_HOLD_MS, ttl > 0 ? ttl * 1000 : 0);
    }
    if (changed) {
      runtime.reactionStartedAt = now;
      runtime.reactionUntil = now + randomBetween(1100, 2100);
      runtime.nextMicroAt = now + randomBetween(900, 1600);
      runtime.state = STATES.REACTION;
      choosePreset(STATES.REACTION, true);
    }
    return changed;
  }

  function requestBlink(reason = "motion", durationMs = 110) {
    const now = wallNowMs();
    if (now - runtime.lastBlinkRequestAt < BLINK_MIN_INTERVAL_MS) return false;
    runtime.lastBlinkRequestAt = now;
    if (typeof window.dispatchEvent === "function" && typeof window.CustomEvent === "function") {
      window.dispatchEvent(new window.CustomEvent("celia-blink-request", {
        detail: { reason, durationMs: clamp(Number(durationMs) || 110, 70, 170) },
      }));
      return true;
    }
    return false;
  }

  function setGazeTarget(x, y, metadata = {}) {
    const now = nowMs();
    const nextX = clamp(Number(x) || 0, -1, 1);
    const nextY = clamp(Number(y) || 0, -1, 1);
    const targetXDeg = nextX * GAZE_MAX_X_DEG;
    const targetYDeg = nextY * GAZE_MAX_Y_DEG;
    const deltaDeg = Math.hypot((nextX - runtime.gazeTargetX) * GAZE_MAX_X_DEG, (nextY - runtime.gazeTargetY) * GAZE_MAX_Y_DEG);
    const largeX = Math.abs(targetXDeg) > GAZE_EYE_ONLY_DEG;
    const largeY = Math.abs(targetYDeg) > GAZE_EYE_ONLY_DEG * .72;
    runtime.gazeTargetX = nextX; runtime.gazeTargetY = nextY;
    runtime.gazeMode = metadata.mode || (Math.abs(nextX) + Math.abs(nextY) < .02 ? "camera" : "environment");
    const delay = Math.max(0, Number(metadata.headDelayMs ?? 110));
    const duration = Math.max(220, Number(metadata.headDurationMs ?? (runtime.gazeMode === "camera" ? 460 : 340)));
    runtime.gazeHeadFromX = runtime.gazeHeadX; runtime.gazeHeadFromY = runtime.gazeHeadY;
    runtime.gazeHeadGoalX = largeX ? targetXDeg * .42 : 0;
    runtime.gazeHeadGoalY = largeY ? targetYDeg * .34 : 0;
    runtime.gazeHeadMoveStartedAt = now + delay; runtime.gazeHeadMoveUntil = runtime.gazeHeadMoveStartedAt + duration;
    runtime.gazeHoldUntil = metadata.holdMs === Infinity ? Infinity : now + Math.max(0, Number(metadata.holdMs ?? 700));
    if (Math.abs(nextX) > .05) runtime.gazeLastSide = Math.sign(nextX);
    const emotionBlinkProbability = {
      angry:.07, sad:.08, teasing:.12, in_love:.10, scared:.12,
      confused:.14, neutral:.16, happy:.18, funny:.18, surprised:.18, passionate:.14,
    }[runtime.emotion] ?? .14;
    const blinkProbability = clamp(Number(metadata.blinkProbability ?? emotionBlinkProbability), 0, 1);
    if (deltaDeg >= 13 && metadata.blink !== false && Math.random() < blinkProbability) requestBlink("gaze-saccade", randomBetween(90, 125));
  }

  function updateGaze(now, dt) {
    if (now >= runtime.gazeHoldUntil && runtime.gazeMode !== "camera") {
      setGazeTarget(0, 0, { mode: "camera", holdMs: Infinity, headDelayMs: 0, blink: false });
    }
    if (now >= runtime.gazeHeadMoveStartedAt) {
      const span = Math.max(1, runtime.gazeHeadMoveUntil - runtime.gazeHeadMoveStartedAt);
      const q = minimumJerk((now - runtime.gazeHeadMoveStartedAt) / span);
      runtime.gazeHeadX = runtime.gazeHeadFromX + (runtime.gazeHeadGoalX - runtime.gazeHeadFromX) * q;
      runtime.gazeHeadY = runtime.gazeHeadFromY + (runtime.gazeHeadGoalY - runtime.gazeHeadFromY) * q;
    }
    const targetXDeg = runtime.gazeTargetX * GAZE_MAX_X_DEG;
    const targetYDeg = runtime.gazeTargetY * GAZE_MAX_Y_DEG;
    const rawEyeTargetX = (targetXDeg - runtime.gazeHeadX) / GAZE_MAX_X_DEG;
    const rawEyeTargetY = (targetYDeg - runtime.gazeHeadY) / GAZE_MAX_Y_DEG;
    runtime.gazeRawEyeX = rawEyeTargetX; runtime.gazeRawEyeY = rawEyeTargetY;
    const eyeTargetX = clamp(rawEyeTargetX, -GAZE_EYE_LIMIT, GAZE_EYE_LIMIT);
    const eyeTargetY = clamp(rawEyeTargetY, -GAZE_EYE_LIMIT, GAZE_EYE_LIMIT);
    const eyeFollow = 1 - Math.exp(-dt / .045);
    runtime.gazeEyeX += (eyeTargetX - runtime.gazeEyeX) * eyeFollow;
    runtime.gazeEyeY += (eyeTargetY - runtime.gazeEyeY) * eyeFollow;
  }

  function scheduleMicro(now) {
    if (now < runtime.nextMicroAt) return;
    const p = profile();
    const rhythm = ACTING_RHYTHM[runtime.emotion] || ACTING_RHYTHM.neutral;
    // Micro-events are punctuation, not the animation engine. Most frames should
    // breathe/dance through the main choreography instead of twitching.
    const choices = ["rest", "rest", "rest", "rest", "ear_flick"];
    if (runtime.state !== STATES.IDLE) choices.push("micro_nod");
    if (runtime.state === STATES.IDLE && now >= runtime.nextIdleGazeAt) choices.push("glance");
    const event = choices[Math.floor(Math.random() * choices.length)];
    runtime.microEvent = event;
    if (event === "glance") {
      const side = runtime.gazeLastSide ? -runtime.gazeLastSide : (Math.random() < .5 ? -1 : 1);
      setGazeTarget(side * randomBetween(.16, .30), randomBetween(-.10, .12), { mode: "idle-glance", holdMs: randomBetween(420, 760), headDelayMs: 120 });
      runtime.nextIdleGazeAt = now + randomBetween(3200, 6800);
      runtime.microUntil = now + 800;
    } else if (event === "ear_flick") {
      runtime.microUntil = now + 480;
    } else if (event === "micro_nod") {
      runtime.microUntil = now + randomBetween(220, 360);
    } else {
      runtime.microUntil = now + randomBetween(450, 900);
    }
    const pace = clamp((1.35 - p.energy * .25) * rhythm.micro, .88, 1.70);
    runtime.nextMicroAt = now + randomBetween(2600, 5600) * pace;
  }

  function weightedPoseChoice(pool) {
    const weights = POSE_WEIGHTS[runtime.emotion] || POSE_WEIGHTS.neutral;
    const weighted = pool.map(name => [name, Math.max(.01, Number(weights[name] ?? 1))]);
    const total = weighted.reduce((sum, item) => sum + item[1], 0);
    let cursor = Math.random() * total;
    for (const [name, weight] of weighted) {
      cursor -= weight;
      if (cursor <= 0) return name;
    }
    return weighted.at(-1)?.[0] || "center";
  }

  function currentPoseVector() {
    return { headX: runtime.poseHeadX, headY: runtime.poseHeadY, headZ: runtime.poseHeadZ, bodyX: runtime.poseBodyX, bodyY: runtime.poseBodyY, bodyZ: runtime.poseBodyZ };
  }

  function beginPoseTransition(target, duration, phase, now) {
    runtime.poseTransition = { from: currentPoseVector(), to: target, startedAt: now, duration: Math.max(120, duration), phase };
    runtime.posePhase = phase;
  }

  function applyPoseTransition(now) {
    const tr = runtime.poseTransition;
    if (!tr) return false;
    const q = minimumJerk((now - tr.startedAt) / tr.duration);
    for (const name of ["HeadX", "HeadY", "HeadZ", "BodyX", "BodyY", "BodyZ"]) {
      const key = name[0].toLowerCase() + name.slice(1);
      runtime["pose" + name] = tr.from[key] + (tr.to[key] - tr.from[key]) * q;
    }
    return now - tr.startedAt >= tr.duration;
  }

  function updateArousal(dt) {
    const p = profile();
    const target = runtime.state === STATES.REACTION ? .82
      : runtime.state === STATES.SPEAKING ? clamp(.32 + runtime.speechEnergy * .42 + Math.max(0, runtime.speechSlope) * .18, .30, .90)
      : clamp(.16 + p.energy * .25, .18, .42);
    runtime.arousal += (target - runtime.arousal) * (1 - Math.exp(-dt / 1.6));
    const recovery = runtime.state === STATES.IDLE ? .17 : .22;
    runtime.motionBudget = clamp(runtime.motionBudget + dt * recovery, 0, 1);
  }

  function updatePosePhrase(now) {
    const done = applyPoseTransition(now);
    if (runtime.posePhase === "moving" && done) {
      const pose = POSE_PHRASES[runtime.poseName] || POSE_PHRASES.center;
      runtime.poseTransition = null; runtime.posePhase = "holding";
      runtime.poseHoldUntil = now + pose.hold * randomBetween(.88, 1.18);
      return;
    }
    if (runtime.posePhase === "holding") {
      if (now < runtime.poseHoldUntil) return;
      const pose = POSE_PHRASES[runtime.poseName] || POSE_PHRASES.center;
      beginPoseTransition(POSE_PHRASES.center, pose.back * randomBetween(.95, 1.18), "returning", now);
      return;
    }
    if (runtime.posePhase === "returning" && done) {
      runtime.poseTransition = null; runtime.posePhase = "rest"; runtime.poseName = "center";
      runtime.nextPoseAt = now + randomBetween(320, 820);
      runtime.gestureCooldownUntil = now + randomBetween(220, 620);
      return;
    }
    if (runtime.posePhase !== "rest" || now < runtime.nextPoseAt || now < runtime.gestureCooldownUntil) return;
    const idle = runtime.state === STATES.IDLE;
    const rhythm = ACTING_RHYTHM[runtime.emotion] || ACTING_RHYTHM.neutral;
    const restChance = idle ? rhythm.rest : runtime.state === STATES.SPEAKING ? .22 : .12;
    if (runtime.motionBudget < .18 || Math.random() < restChance) {
      runtime.nextPoseAt = now + (idle ? randomBetween(480, 1050) : randomBetween(420, 900));
      return;
    }
    let pool = Object.keys(POSE_PHRASES).filter(name => name !== "center" && !runtime.previousPoses.includes(name));
    if (runtime.poseLastSide) {
      const opposite = pool.filter(name => POSE_PHRASES[name].side === -runtime.poseLastSide);
      if (opposite.length && Math.random() < .58) pool = opposite.concat(pool);
    }
    const nextName = weightedPoseChoice(pool);
    const pose = POSE_PHRASES[nextName] || POSE_PHRASES.center;
    runtime.poseName = nextName; runtime.previousPoses.push(nextName); runtime.previousPoses = runtime.previousPoses.slice(-2);
    if (pose.side) runtime.poseLastSide = pose.side;
    runtime.motionBudget = clamp(runtime.motionBudget - pose.cost, 0, 1);
    beginPoseTransition(pose, pose.move * randomBetween(.92, 1.12), "moving", now);
  }

  function updateWeightShift(now, dt) {
    if (now >= runtime.nextWeightAt) {
      const p = profile();
      const scale = runtime.state === STATES.SPEAKING ? 1.00 : .88;
      runtime.weightTarget = clamp(runtime.weightTarget * .48 + randomBetween(-.72, .72) * scale * (0.78 + p.energy * .28), -.82, .82);
      runtime.nextWeightAt = now + randomBetween(1500, 3400);
    }
    runtime.weightShift += (runtime.weightTarget - runtime.weightShift) * (1 - Math.exp(-dt / 1.12));
    // Head stabilization follows the body's weight transfer with its own delay.
    // It deliberately compensates only ~46%, avoiding a gyro-stabilized floating head.
    runtime.headStabilization += (runtime.weightShift - runtime.headStabilization) * (1 - Math.exp(-dt / .44));
  }

  function breathEnvelope(now, dt) {
    let target = 1;
    if (now < runtime.breathAnticipationUntil) target = 1.34;
    else if (runtime.speechRunning) target = clamp(.66 - runtime.speechDrive * .20, .42, .66);
    else if (now < runtime.breathRecoveryUntil) target = 1.15;
    const tau = target > runtime.breathScale ? 0.6 : .44;
    runtime.breathScale += (target - runtime.breathScale) * (1 - Math.exp(-dt / tau));
    return runtime.breathScale;
  }

  function presetSignals(preset, t) {
    const breath = Math.sin(t * preset.breathFreq + runtime.phase);
    const breath2 = Math.sin(t * preset.breathFreq * 1.618 + 1.57 + runtime.phase) * .28;
    const nod = Math.sin(t * preset.nodFreq + runtime.phase * .7);
    const nod2 = Math.sin(t * preset.nodFreq * 1.618 + 1.2) * .40;
    const sway = Math.sin(t * preset.swayFreq + runtime.phase * .4);
    const sway2 = Math.sin(t * preset.swayFreq * 1.618 + .78) * .45;
    const roll = Math.sin(t * preset.rollFreq + runtime.phase * .9);
    const roll2 = Math.sin(t * preset.rollFreq * 2.414 + 1.04) * .38;
    const hairWave = Math.sin(t * preset.hairFreq + runtime.phase * 1.17);
    const hairWave2 = Math.sin(t * preset.hairFreq * 1.73 + .92 + runtime.phase * .33) * .45;
    const chaos = preset.chaos || 0;
    return {
      headBreath: (breath + breath2) * preset.breathAmp,
      headNod: (nod + nod2) * preset.nodAmp,
      headSway: (sway + sway2) * preset.swayAmp,
      headRoll: (roll + roll2) * preset.rollAmp,
      bodyBob: (breath + breath2 * .55) * preset.bobAmp,
      shoulder: (sway + sway2 * .75) * preset.shoulderAmp,
      hair: (hairWave + hairWave2) * preset.hairAmp,
      secondary: Math.sin(t * (preset.swayFreq * .52 + .18) + runtime.phase * .3),
      breathDriver: Math.sin(t * (preset.breathFreq || .8)),
      chaosX: Math.sin(t * 1.7 + chaos * 10) * chaos * 1.25,
      chaosY: Math.cos(t * 1.4 + chaos * 8) * chaos * 1.10,
      chaosZ: Math.sin(t * 2.1 + chaos * 6) * chaos * .85,
    };
  }

  function blendedPresetSignals(now, t) {
    const current = presetSignals(PRESETS[runtime.preset] || PRESETS["Idle Base"], t);
    if (now >= runtime.presetTransitionUntil || runtime.previousPreset === runtime.preset) return current;
    const previous = presetSignals(PRESETS[runtime.previousPreset] || PRESETS["Idle Base"], t);
    const span = Math.max(1, runtime.presetTransitionUntil - runtime.presetTransitionStartedAt);
    const q = minimumJerk((now - runtime.presetTransitionStartedAt) / span);
    const out = {};
    for (const key of Object.keys(current)) out[key] = previous[key] * (1 - q) + current[key] * q;
    return out;
  }

  function stateForFrame(now) {
    const speaking = runtime.speechEnergy > .010 || wallNowMs() - runtime.lastSpeechAt < 700;
    if (speaking) return STATES.SPEAKING;
    if (now < runtime.reactionUntil) return STATES.REACTION;
    return STATES.IDLE;
  }

  function confusedShakeOffset(now) {
    if (runtime.emotion !== "confused") return 0;
    if (now < runtime.confusedShakeStartedAt || now >= runtime.confusedShakeUntil) return 0;
    const progress = clamp(
      (now - runtime.confusedShakeStartedAt) / Math.max(1, runtime.confusedShakeUntil - runtime.confusedShakeStartedAt),
      0,
      1
    );
    const keyframes = [
      { t: 0, value: 0 },
      { t: .18, value: -1 },
      { t: .30, value: -1 },
      { t: .48, value: 1 },
      { t: .62, value: 1 },
      { t: .80, value: -.82 },
      { t: .88, value: -.82 },
      { t: 1, value: 0 },
    ];
    let right = 1;
    while (right < keyframes.length && progress > keyframes[right].t) right += 1;
    right = Math.min(right, keyframes.length - 1);
    const left = Math.max(0, right - 1);
    const a = keyframes[left];
    const b = keyframes[right];
    const span = Math.max(.0001, b.t - a.t);
    const local = minimumJerk((progress - a.t) / span);
    const value = a.value * (1 - local) + b.value * local;
    return value * CONFUSED_SHAKE_AMPLITUDE;
  }

  function computeMotion(now, dt) {
    const p = blendedProfile(now);
    const t = now / 1000 * PROCEDURAL_SPEED;
    const verticalT = now / 1000 * VERTICAL_PROCEDURAL_SPEED;
    updateArousal(dt);
    updateWeightShift(now, dt);
    updatePosePhrase(now);
    updateGaze(now, dt);
    runtime.speechDrive += (runtime.speechEnergy - runtime.speechDrive) * (1 - Math.exp(-dt / .22));
    runtime.speechSlopeDrive += (runtime.speechSlope - runtime.speechSlopeDrive) * (1 - Math.exp(-dt / .16));
    const gesturing = runtime.posePhase !== "rest";
    const rhythm = ACTING_RHYTHM[runtime.emotion] || ACTING_RHYTHM.neutral;
    // Continuous life is the floor. Gestures/speech/reactions add energy on top;
    // rest only means "no semantic pose", never "turn the rig down to 25%".
    const baseActivity = runtime.state === STATES.IDLE ? (gesturing ? .74 : .58)
      : runtime.state === STATES.SPEAKING ? .82 : .94;
    const arousalGain = runtime.state === STATES.IDLE ? .10 : .14;
    const rhythmGain = runtime.state === STATES.IDLE ? rhythm.idle : 1;
    const activityTarget = clamp((baseActivity + runtime.arousal * arousalGain) * rhythmGain, .48, 1.08);
    runtime.activity += (activityTarget - runtime.activity) * (1 - Math.exp(-dt / .34));
    const activity = runtime.activity;
    const speakingBoost = 1 + runtime.speechDrive * .55;
    const amp = p.amp * speakingBoost * activity;
    const sig = blendedPresetSignals(now, t);
    const verticalSig = blendedPresetSignals(now, verticalT);
    const breathScale = breathEnvelope(now, dt);
    // Body Y has its own conservative envelope: lateral/rotation can be lively
    // without reintroducing the old vertical jerk regression.
    const verticalActivity = clamp(.16 + activity * .14, .22, .34);
    const verticalAmp = p.amp * speakingBoost * verticalActivity;

    let headX = (sig.headBreath * .18 + sig.headNod * .24) * amp * MOTION_GAIN.head;
    let headY = (sig.headSway * .26 + sig.headNod * .18) * amp * MOTION_GAIN.head;
    let headZ = (sig.headRoll * .32 + sig.headSway * .10) * amp * MOTION_GAIN.head;
    let bodyBob = verticalSig.bodyBob * .060 * verticalAmp * MOTION_GAIN.bodyBob * breathScale;
    let shoulder = sig.shoulder * .18 * amp * MOTION_GAIN.shoulder;
    let hair = sig.hair * .060 * amp * MOTION_GAIN.hair;
    const secondaryLag = sig.secondary * amp;
    const verticalSecondaryLag = verticalSig.secondary * verticalAmp;
    // Hair physics is fed by AngleX/Y/Z in the rig. Use the independent hair
    // oscillator as a subtle driver impulse instead of writing physics outputs.
    const hairPhysicsDrive = hair * .72;

    const weightBodyX = runtime.weightShift * 1.25 * activity;
    const weightBodyZ = runtime.weightShift * .52 * activity;
    const gazeBodyX = Math.abs(runtime.gazeHeadX) > 5 ? runtime.gazeHeadX * .055 : 0;
    headZ += -runtime.headStabilization * .52 * activity * HEAD_STABILIZATION_RATIO;
    shoulder += runtime.weightShift * .42 * activity + secondaryLag * .26;
    bodyBob += verticalSecondaryLag * .06;
    headX += runtime.poseHeadX + runtime.gazeHeadX + hairPhysicsDrive * .55;
    headY += runtime.poseHeadY + runtime.gazeHeadY + hairPhysicsDrive * .22;
    headZ += runtime.poseHeadZ + hairPhysicsDrive * .90;
    const confusedShake = confusedShakeOffset(now);
    const confusedShakeActive = runtime.emotion === "confused"
      && now >= runtime.confusedShakeStartedAt
      && now < runtime.confusedShakeUntil;
    headX = confusedShakeActive ? headX * .30 + confusedShake : headX + confusedShake;
    hair += secondaryLag * .18;
    headX += sig.chaosX * amp;
    headY += sig.chaosY * amp;
    headZ += sig.chaosZ * amp;


    // Emotion changes distributions and energy, never a permanent DC head tilt.
    const bodyPosture = 0;

    if (runtime.speechDrive > .003 || Math.abs(runtime.speechSlopeDrive) > .003) {
      const speech = runtime.speechDrive;
      const anticipatoryAccent = clamp(Math.max(0, runtime.speechSlopeDrive) * 1.35, 0, 1);
      bodyBob += anticipatoryAccent * .16 * MOTION_GAIN.speaking;
      headY += anticipatoryAccent * .52 * MOTION_GAIN.speaking;
      headX += Math.sin(t * 1.7) * speech * .38;
      headZ += Math.sin(t * 1.15 + .4) * speech * .28;
      shoulder += anticipatoryAccent * .34 + Math.sin(t * 1.3 + .6) * speech * .20;
      hair += speech * .28 * MOTION_GAIN.speaking;
    }

    if (now < runtime.reactionUntil && runtime.reactionUntil > runtime.reactionStartedAt) {
      const progress = clamp((now - runtime.reactionStartedAt) / (runtime.reactionUntil - runtime.reactionStartedAt), 0, 1);
      const pulse = Math.sin(Math.PI * minimumJerk(progress)) * MOTION_GAIN.reaction;
      if (runtime.emotion === "surprised") { headY += 7 * pulse; bodyBob += .56 * pulse; }
      else if (runtime.emotion === "angry") { headX += 2.4 * Math.sin(progress * Math.PI * 2) * pulse; shoulder += .8 * pulse; }
      else if (runtime.emotion === "sad") { headY -= 2.2 * pulse; }
      else if (runtime.emotion === "confused") { headZ += 1.4 * pulse; }
      else if (runtime.emotion === "scared") { headY += 3 * pulse; bodyBob -= .56 * pulse; }
      else if (runtime.emotion === "happy" || runtime.emotion === "funny") { headX += 2.8 * pulse; bodyBob += .64 * pulse; }
      else if (runtime.emotion === "in_love" || runtime.emotion === "passionate") { headZ += 2.2 * pulse; }
      else if (runtime.emotion === "teasing") { headZ += 2.6 * pulse; }
    }

    if (runtime.microEvent === "micro_nod" && now < runtime.microUntil) {
      const q = 1 - (runtime.microUntil - now) / Math.max(1, 300);
      headX += Math.sin(minimumJerk(clamp(q,0,1)) * Math.PI * 2) * 1.1;
    }

    return {
      headX, headY, headZ, bodyBob, shoulder, hair, bodyPosture, weightBodyX, weightBodyZ, gazeBodyX,
      breathDriver: .06 + (sig.breathDriver + 1) * .10 * breathScale,
      poseBodyX: runtime.poseBodyX, poseBodyY: runtime.poseBodyY, poseBodyZ: runtime.poseBodyZ,
    };
  }

  const DRIVER_CHANNELS = ["headX", "headY", "headZ", "bodyX", "bodyY", "bodyZ"];

  function writeDriver(channel, target, dt) {
    const desc = runtime.mapped[channel];
    if (!desc || !Number.isFinite(target)) return;
    target = clamp(desc.default + target, desc.min, desc.max);
    // Fast tracking spring filters reactive texture without defining gesture duration.
    // Intentional pose duration now lives in the minimum-jerk trajectory above.
    const state = runtime.smooth[channel] || (runtime.smooth[channel] = {
      value: Number(runtime.core.parameters.values[desc.index]) || desc.default, velocity: 0,
    });
    const omegaByChannel = { headX: 14, headY: 13, headZ: 13, bodyX: 9, bodyY: 2.8, bodyZ: 8 };
    const confusedHorizontal = channel === "headX"
      && runtime.emotion === "confused"
      && nowMs() >= runtime.confusedShakeStartedAt
      && nowMs() < runtime.confusedShakeUntil;
    const omega = confusedHorizontal ? 24 : (omegaByChannel[channel] || 5);
    const decay = Math.exp(-omega * dt);
    const difference = state.value - target;
    const step = (state.velocity + omega * difference) * dt;
    state.value = clamp(target + (difference + step) * decay, desc.min, desc.max);
    state.velocity = (state.velocity - omega * step) * decay;
    runtime.core.parameters.values[desc.index] = state.value;
  }


  function updateSameSideMetric(value, dtMs, axis) {
    const sign = value > 0 ? 1 : value < 0 ? -1 : 0;
    const sideKey = `metric${axis}Side`;
    const runKey = `metric${axis}SideMs`;
    const maxKey = `metric${axis}MaxSameSideMs`;
    if (!sign) { runtime[sideKey] = 0; runtime[runKey] = 0; return; }
    if (runtime[sideKey] === sign) runtime[runKey] += dtMs;
    else { runtime[sideKey] = sign; runtime[runKey] = dtMs; }
    runtime[maxKey] = Math.max(runtime[maxKey], runtime[runKey]);
  }

  function sampleMotionMetrics(now, dt) {
    const current = {};
    for (const channel of DRIVER_CHANNELS) {
      const desc = runtime.mapped[channel];
      current[channel] = desc ? Number(runtime.core.parameters.values[desc.index]) || 0 : 0;
    }
    runtime.metricSamples += 1; runtime.metricHeadXSum += current.headX; runtime.metricHeadZSum += current.headZ;
    if (Math.abs(current.headX) <= 1) runtime.metricNeutralHeadX += 1;
    if (Math.abs(current.headZ) <= 1) runtime.metricNeutralHeadZ += 1;
    updateSameSideMetric(current.headX, dt * 1000, "HeadX");
    updateSameSideMetric(current.headZ, dt * 1000, "HeadZ");
    const rawEyeAbs = Math.max(Math.abs(runtime.gazeRawEyeX || 0), Math.abs(runtime.gazeRawEyeY || 0));
    runtime.metricEyeSamples += 1; runtime.metricEyeMaxAbs = Math.max(runtime.metricEyeMaxAbs, rawEyeAbs);
    if (rawEyeAbs > GAZE_EYE_LIMIT) runtime.metricEyeClipSamples += 1;
    if (runtime.metricPrev) {
      let maxVelocity = 0; const velocity = {}, acceleration = {};
      for (const channel of DRIVER_CHANNELS) {
        velocity[channel] = (current[channel] - runtime.metricPrev[channel]) / Math.max(.001, dt);
        maxVelocity = Math.max(maxVelocity, Math.abs(velocity[channel]));
        const pv = runtime.metricPrevVelocity?.[channel] || 0;
        acceleration[channel] = (velocity[channel] - pv) / Math.max(.001, dt);
        const pa = runtime.metricPrevAcceleration?.[channel] || 0;
        const jerk = Math.abs((acceleration[channel] - pa) / Math.max(.001, dt));
        if (runtime.metricSamples > 30) runtime.metricMaxJerk[channel] = Math.max(runtime.metricMaxJerk[channel] || 0, jerk);
      }
      if (maxVelocity < .55) runtime.metricStill += 1;
      runtime.metricPrevVelocity = velocity; runtime.metricPrevAcceleration = acceleration;
    }
    runtime.metricPrev = current;
    if (now >= runtime.metricNextTraceAt) {
      runtime.metricTraceHeadX.push(current.headX); runtime.metricTraceHeadZ.push(current.headZ); runtime.metricTraceBodyY.push(current.bodyY);
      for (const trace of [runtime.metricTraceHeadX, runtime.metricTraceHeadZ, runtime.metricTraceBodyY]) if (trace.length > 6000) trace.shift();
      runtime.metricNextTraceAt = now + 100;
    }
  }

  function percentile(samples, q) {
    if (!samples.length) return null;
    const sorted = [...samples].sort((a, b) => a - b);
    const pos = (sorted.length - 1) * q; const lo = Math.floor(pos), hi = Math.ceil(pos);
    if (lo === hi) return sorted[lo];
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  }

  function traceAutocorrelation(samples, lag = 10) {
    if (samples.length < lag + 20) return null;
    const mean = samples.reduce((a, b) => a + b, 0) / samples.length; let num = 0, den = 0;
    for (let i = 0; i < samples.length; i++) { const a = samples[i] - mean; den += a * a; if (i >= lag) num += a * (samples[i - lag] - mean); }
    return den > 1e-9 ? num / den : 0;
  }

  function autocorrelationCurve(samples) {
    const curve = [];
    for (let lagSec = .5; lagSec <= 15.001; lagSec += .5) {
      const value = traceAutocorrelation(samples, Math.round(lagSec * 10));
      if (value !== null) curve.push({ lagSec: Number(lagSec.toFixed(1)), value });
    }
    return curve;
  }

  function maxVisibleSameSideSec(samples, deadband = 1) {
    let side = 0, run = 0, maxRun = 0;
    for (const value of samples) {
      const next = value > deadband ? 1 : value < -deadband ? -1 : 0;
      if (!next) { side = 0; run = 0; continue; }
      if (next === side) run += .1; else { side = next; run = .1; }
      maxRun = Math.max(maxRun, run);
    }
    return maxRun;
  }

  function secondaryAutocorrPeaks(curve) {
    const peaks = [];
    for (let i = 1; i < curve.length - 1; i++) {
      if (curve[i].lagSec < 2) continue;
      const a = Math.abs(curve[i - 1].value), b = Math.abs(curve[i].value), c = Math.abs(curve[i + 1].value);
      if (b >= a && b >= c) peaks.push(curve[i]);
    }
    return peaks.sort((a, b) => Math.abs(b.value) - Math.abs(a.value)).slice(0, 3);
  }

  function motionMetrics() {
    const n = Math.max(1, runtime.metricSamples);
    const curve = autocorrelationCurve(runtime.metricTraceHeadX);
    return {
      samples: runtime.metricSamples, headXMean: runtime.metricHeadXSum / n, headZMean: runtime.metricHeadZSum / n,
      headXPercentiles: { p10: percentile(runtime.metricTraceHeadX, .10), p50: percentile(runtime.metricTraceHeadX, .50), p90: percentile(runtime.metricTraceHeadX, .90) },
      headZPercentiles: { p10: percentile(runtime.metricTraceHeadZ, .10), p50: percentile(runtime.metricTraceHeadZ, .50), p90: percentile(runtime.metricTraceHeadZ, .90) },
      headXNeutralPct: runtime.metricSamples ? runtime.metricNeutralHeadX / runtime.metricSamples * 100 : 0,
      headZNeutralPct: runtime.metricSamples ? runtime.metricNeutralHeadZ / runtime.metricSamples * 100 : 0,
      headXMaxSameSideSec: runtime.metricHeadXMaxSameSideMs / 1000, headZMaxSameSideSec: runtime.metricHeadZMaxSameSideMs / 1000,
      headXMaxVisibleSameSideSec: maxVisibleSameSideSec(runtime.metricTraceHeadX), headZMaxVisibleSameSideSec: maxVisibleSameSideSec(runtime.metricTraceHeadZ),
      stillnessPct: runtime.metricSamples > 1 ? runtime.metricStill / (runtime.metricSamples - 1) * 100 : 0,
      maxJerk: { ...runtime.metricMaxJerk }, eyeRawMaxAbs: runtime.metricEyeMaxAbs,
      eyeClipPct: runtime.metricEyeSamples ? runtime.metricEyeClipSamples / runtime.metricEyeSamples * 100 : 0,
      headXAutocorrCurve: curve, headXSecondaryPeaks: secondaryAutocorrPeaks(curve), traceSeconds: runtime.metricTraceHeadX.length / 10,
    };
  }

  function applyFrame() {
    const current = getCore();
    if (!current || current.core !== runtime.core) return false;
    const now = nowMs();
    const nextState = stateForFrame(now);
    if (nextState !== runtime.state) setState(nextState);
    if (runtime.state !== STATES.REACTION && now >= runtime.nextPresetAt) choosePreset(runtime.state);
    scheduleMicro(now);

    const dt = runtime.lastFrameAt ? clamp((now - runtime.lastFrameAt) / 1000, .001, .05) : 1 / 60;
    const motion = computeMotion(now, dt);
    for (const channel of ["headX", "headY", "headZ"]) {
      const state = runtime.smooth[channel];
      if (!state) continue;
      const confusedHorizontal = channel === "headX"
        && runtime.emotion === "confused"
        && now >= runtime.confusedShakeStartedAt
        && now < runtime.confusedShakeUntil;
      const limit = confusedHorizontal ? 58 : (runtime.state === STATES.SPEAKING ? 42 : 34);
      motion[channel] = clamp(motion[channel], state.value - limit * dt, state.value + limit * dt);
    }
    const mix = {};
    add(mix, "headX", motion.headX);
    add(mix, "headY", motion.headY);
    add(mix, "headZ", motion.headZ);
    // Write semantic drivers BEFORE physics in this same frame. The model's
    // loadParameters() discards late writes before the next physics evaluation.
    add(mix, "bodyX", motion.shoulder * .96 + motion.weightBodyX * 1.45 + motion.gazeBodyX + motion.poseBodyX * 1.80);
    // Body Y may still reach an expressive low/high pose; the explicit minimum-jerk
    // trajectory defines how long that travel takes instead of a slow global spring.
    add(mix, "bodyY", motion.bodyBob * 1.42 + motion.poseBodyY);
    add(mix, "bodyZ", motion.headZ * .28 + motion.weightBodyZ * 1.45 + motion.poseBodyZ * 1.60);
    add(mix, "breath", motion.breathDriver);
    add(mix, "eyeX", runtime.gazeEyeX * MOTION_GAIN.eye);
    add(mix, "eyeY", runtime.gazeEyeY * MOTION_GAIN.eye);

    const map = runtime.mapped;
    for (const channel of DRIVER_CHANNELS) writeDriver(channel, mix[channel] || 0, dt);
    for (const channel of ["breath", "eyeX", "eyeY"]) writeAdditive(runtime.core, map[channel], mix[channel] || 0);
    sampleMotionMetrics(now, dt);

    runtime.contributions = { ...mix, physicsDriven: true, hairIntent: motion.hair, speechEnergy: runtime.speechEnergy };
    runtime.lastFrameAt = now;
    runtime.frames += 1;
    return true;
  }

  function currentPresetChaos(now = nowMs()) {
    const current = PRESETS[runtime.preset] || PRESETS["Idle Base"];
    let raw = Number(current.chaos) || 0;
    if (now < runtime.presetTransitionUntil && runtime.previousPreset !== runtime.preset) {
      const previous = PRESETS[runtime.previousPreset] || PRESETS["Idle Base"];
      const span = Math.max(1, runtime.presetTransitionUntil - runtime.presetTransitionStartedAt);
      const q = minimumJerk((now - runtime.presetTransitionStartedAt) / span);
      raw = (Number(previous.chaos) || 0) * (1 - q) + raw * q;
    }
    const normalized = clamp(raw / .45, 0, 1);
    const speechChaos = clamp(runtime.speechDrive * .14, 0, .14);
    const reactionChaos = runtime.state === STATES.REACTION ? .10 : 0;
    return clamp(normalized + speechChaos + reactionChaos, 0, 1);
  }

  function updateBangMidInertia(rawVelocity, dt, chaos) {
    const previous = runtime.bangMidFilteredVelocity || {
      x: rawVelocity.x, y: rawVelocity.y, z: rawVelocity.z,
    };
    const velocityTau = .070;
    const blend = 1 - Math.exp(-dt / velocityTau);
    const filtered = {
      x: previous.x + (rawVelocity.x - previous.x) * blend,
      y: previous.y + (rawVelocity.y - previous.y) * blend,
      z: previous.z + (rawVelocity.z - previous.z) * blend,
    };
    const acceleration = runtime.bangMidFilteredVelocity ? {
      x: (filtered.x - previous.x) / dt,
      y: (filtered.y - previous.y) / dt,
      z: (filtered.z - previous.z) / dt,
    } : { x:0, y:0, z:0 };

    const deadZone = (value, threshold) => {
      const magnitude = Math.max(0, Math.abs(value) - threshold);
      return Math.sign(value) * magnitude;
    };
    const drivenAcceleration = {
      x: deadZone(acceleration.x, 10),
      y: deadZone(acceleration.y, 8),
      z: deadZone(acceleration.z, 10),
    };
    const accelMag = Math.hypot(drivenAcceleration.x, drivenAcceleration.y * .62, drivenAcceleration.z * 1.05);
    const impulseTarget = smoothStep(clamp((accelMag - 6) / 130, 0, 1));
    const impulseTau = impulseTarget > runtime.bangMidImpulseEnergy ? .050 : .180;
    runtime.bangMidImpulseEnergy += (impulseTarget - runtime.bangMidImpulseEnergy) *
      (1 - Math.exp(-dt / impulseTau));

    const strength = (.86 + chaos * .70) * runtime.bangMidImpulseEnergy;
    const targetX = clamp(drivenAcceleration.x / 95, -1, 1) * 2.35 * strength;
    const targetY = clamp(drivenAcceleration.y / 145, -1, 1) * 1.15 * strength;
    const targetZ = clamp(drivenAcceleration.z / 85, -1, 1) * 2.55 * strength;

    const springStep = (position, velocityValue, target, omega, damping, limit) => {
      const accel = omega * omega * (target - position) - 2 * damping * omega * velocityValue;
      const nextVelocity = clamp(velocityValue + accel * dt, -limit * 9, limit * 9);
      const nextPosition = clamp(position + nextVelocity * dt, -limit, limit);
      return [nextPosition, nextVelocity];
    };
    [runtime.bangMidGhostX, runtime.bangMidGhostVX] =
      springStep(runtime.bangMidGhostX, runtime.bangMidGhostVX, targetX, 8.4, .78, 2.35);
    [runtime.bangMidGhostY, runtime.bangMidGhostVY] =
      springStep(runtime.bangMidGhostY, runtime.bangMidGhostVY, targetY, 8.0, .80, 1.25);
    [runtime.bangMidGhostZ, runtime.bangMidGhostVZ] =
      springStep(runtime.bangMidGhostZ, runtime.bangMidGhostVZ, targetZ, 8.2, .76, 2.75);

    runtime.bangMidFilteredVelocity = filtered;
    runtime.bangMidVelocityX = filtered.x; runtime.bangMidVelocityY = filtered.y; runtime.bangMidVelocityZ = filtered.z;
    runtime.bangMidAccelerationX = acceleration.x; runtime.bangMidAccelerationY = acceleration.y; runtime.bangMidAccelerationZ = acceleration.z;
  }

  function updateHairMotionDynamics(now = nowMs()) {
    const map = runtime.mapped;
    const values = runtime.core && runtime.core.parameters.values;
    if (!map || !values || !map.headX || !map.headY || !map.headZ) {
      return { energy: 0, impulse: 0, chaos: currentPresetChaos(now), ghostX: 0, ghostY: 0, ghostZ: 0 };
    }

    const sample = {
      x: Number(values[map.headX.index]) || 0,
      y: Number(values[map.headY.index]) || 0,
      z: Number(values[map.headZ.index]) || 0,
      body: map.bodyX ? (Number(values[map.bodyX.index]) || 0) : 0,
    };
    const dt = runtime.hairMotionLastAt ? clamp((now - runtime.hairMotionLastAt) / 1000, .008, .05) : 1 / 60;
    let velocity = { x:0, y:0, z:0, body:0 };
    let acceleration = { x:0, y:0, z:0, body:0 };

    if (runtime.hairMotionPrev) {
      velocity = {
        x: (sample.x - runtime.hairMotionPrev.x) / dt,
        y: (sample.y - runtime.hairMotionPrev.y) / dt,
        z: (sample.z - runtime.hairMotionPrev.z) / dt,
        body: (sample.body - runtime.hairMotionPrev.body) / dt,
      };
      if (runtime.hairMotionPrevVelocity) {
        acceleration = {
          x: (velocity.x - runtime.hairMotionPrevVelocity.x) / dt,
          y: (velocity.y - runtime.hairMotionPrevVelocity.y) / dt,
          z: (velocity.z - runtime.hairMotionPrevVelocity.z) / dt,
          body: (velocity.body - runtime.hairMotionPrevVelocity.body) / dt,
        };
      }
    }

    const speed = Math.hypot(velocity.x, velocity.y * .62, velocity.z * 1.05, velocity.body * .50);
    const accelMag = Math.hypot(acceleration.x, acceleration.y * .58, acceleration.z * 1.10, acceleration.body * .42);
    const motionTarget = smoothStep(clamp((speed - .8) / 22, 0, 1));
    const impulseTarget = smoothStep(clamp((accelMag - 18) / 260, 0, 1));
    const motionTau = motionTarget > runtime.hairMotionEnergy ? .08 : .46;
    const impulseTau = impulseTarget > runtime.hairImpulseEnergy ? .045 : .24;
    runtime.hairMotionEnergy += (motionTarget - runtime.hairMotionEnergy) * (1 - Math.exp(-dt / motionTau));
    runtime.hairImpulseEnergy += (impulseTarget - runtime.hairImpulseEnergy) * (1 - Math.exp(-dt / impulseTau));

    const chaos = currentPresetChaos(now);
    updateBangMidInertia(velocity, dt, chaos);
    const strength = (.78 + chaos * .72) * runtime.hairImpulseEnergy;
    // Acceleration creates a short driver displacement. Constant velocity does
    // not keep pulling the strand, so the native Cubism particles are free to
    // overshoot and settle on their own.
    const targetX = clamp((acceleration.x + acceleration.body * .30) / 150, -1, 1) * 2.05 * strength;
    const targetY = clamp(acceleration.y / 205, -1, 1) * 1.10 * strength;
    const targetZ = clamp((acceleration.z + acceleration.body * .16) / 125, -1, 1) * 2.45 * strength;

    const springStep = (position, velocityValue, target, omega, damping, limit) => {
      const accel = omega * omega * (target - position) - 2 * damping * omega * velocityValue;
      const nextVelocity = clamp(velocityValue + accel * dt, -limit * 11, limit * 11);
      const nextPosition = clamp(position + nextVelocity * dt, -limit, limit);
      return [nextPosition, nextVelocity];
    };
    [runtime.hairGhostX, runtime.hairGhostVX] = springStep(runtime.hairGhostX, runtime.hairGhostVX, targetX, 10.5, .68, 2.35);
    [runtime.hairGhostY, runtime.hairGhostVY] = springStep(runtime.hairGhostY, runtime.hairGhostVY, targetY, 9.6, .72, 1.25);
    [runtime.hairGhostZ, runtime.hairGhostVZ] = springStep(runtime.hairGhostZ, runtime.hairGhostVZ, targetZ, 10.0, .64, 2.75);

    runtime.hairMotionDirection = clamp(-(velocity.x * .42 + velocity.z * .72 + velocity.body * .22) / 20, -1, 1);
    runtime.hairMotionPrev = sample;
    runtime.hairMotionPrevVelocity = velocity;
    runtime.hairMotionLastAt = now;
    runtime.faceHairChaos = chaos;
    return {
      energy: runtime.hairMotionEnergy,
      impulse: runtime.hairImpulseEnergy,
      chaos,
      ghostX: runtime.hairGhostX,
      ghostY: runtime.hairGhostY,
      ghostZ: runtime.hairGhostZ,
    };
  }

  function installHairPhysicsInputGhosts(physics) {
    const rig = physics && physics._physicsRig;
    const map = runtime.mapped;
    if (!rig || !map || !rig.settings || !rig.inputs || !rig.outputs) return 0;
    const at = (vector, index) => {
      if (!vector) return null;
      if (typeof vector.at === "function") return vector.at(index);
      if (typeof vector.get === "function") return vector.get(index);
      return vector[index] || null;
    };
    const indexes = descriptors => new Set((descriptors || []).map(item => item.index));
    const chains = [
      { name:"bang-mid", outputs:indexes((map.faceHair || []).slice(0,3)), scale:.92 },
      { name:"side-left", outputs:indexes((map.faceHair || []).slice(3,6)), scale:1.00 },
      { name:"side-right", outputs:indexes((map.faceHair || []).slice(6,9)), scale:1.08 },
      { name:"neck-left", outputs:indexes(map.neckHairL), scale:1.13 },
      { name:"neck-right", outputs:indexes(map.neckHairR), scale:1.13 },
    ].filter(chain => chain.outputs.size);
    let hooks = 0;

    for (let groupIndex = 0; groupIndex < Number(rig.subRigCount || 0); groupIndex++) {
      const setting = at(rig.settings, groupIndex);
      if (!setting) continue;
      let chain = null;
      for (let oi = 0; oi < Number(setting.outputCount || 0) && !chain; oi++) {
        const output = at(rig.outputs, Number(setting.baseOutputIndex || 0) + oi);
        const destinationIndex = output && Number(output.destinationParameterIndex);
        if (!Number.isFinite(destinationIndex)) continue;
        chain = chains.find(candidate => candidate.outputs.has(destinationIndex)) || null;
      }
      if (!chain) continue;

      for (let ii = 0; ii < Number(setting.inputCount || 0); ii++) {
        const input = at(rig.inputs, Number(setting.baseInputIndex || 0) + ii);
        if (!input || typeof input.getNormalizedParameterValue !== "function") continue;
        if (input.__celiaHairGhostHook) { hooks++; continue; }
        const original = input.getNormalizedParameterValue;
        input.getNormalizedParameterValue = function celiaHairGhostNormalizedInput(...args) {
          let ghost = 0;
          const sourceIndex = Number(input.sourceParameterIndex);
          const bangMid = chain.name === "bang-mid";
          if (map.headX && sourceIndex === map.headX.index) ghost = bangMid ? runtime.bangMidGhostX : runtime.hairGhostX;
          else if (map.headY && sourceIndex === map.headY.index) ghost = bangMid ? runtime.bangMidGhostY : runtime.hairGhostY;
          else if (map.headZ && sourceIndex === map.headZ.index) ghost = bangMid ? runtime.bangMidGhostZ : runtime.hairGhostZ;
          if (ghost && Number.isFinite(Number(args[2]))) {
            const minimum = Number(args[3]);
            const maximum = Number(args[4]);
            let value = Number(args[2]) + ghost * chain.scale;
            if (Number.isFinite(minimum) && Number.isFinite(maximum)) value = clamp(value, minimum, maximum);
            args[2] = value;
          }
          return original.apply(this, args);
        };
        input.__celiaHairGhostHook = true;
        input.__celiaHairGhostChain = chain.name;
        hooks++;
      }
    }
    return hooks;
  }

  function applyAccents() {
    const now = nowMs();
    const map = runtime.mapped;

    // Idle.motion3 exports Star as 0..7, but this SDK clamps its 0..1 rig.
    // Own only the animation phase; Param290 still belongs to expressions,
    // and positional Star X/Y remain physics outputs. No blink dependency.
    if (map.starCycle) {
      const pulse = .5 - .5 * Math.cos(wallNowMs() / 1000 * (Math.PI * 2 / 1.8));
      const desc = map.starCycle;
      runtime.core.parameters.values[desc.index] = desc.min + pulse * (desc.max - desc.min);
    }
    if (map.ears.length) {
      const emotionTilt = {
        happy: .16, in_love: .11, sad: -.18, angry: .10, surprised: .22,
        confused: .11, scared: -.08, teasing: .18, funny: .18,
        holding_back_laughter: .09, passionate: .13, neutral: .05,
      }[runtime.emotion] || 0;
      const rhythm = ACTING_RHYTHM[runtime.emotion] || ACTING_RHYTHM.neutral;
      const slow = Math.sin(now / 1000 * .74 + runtime.phase) * .10;
      map.ears.forEach((desc, index) => {
        const side = index % 2 ? -1 : 1;
        writeAdditive(runtime.core, desc, (emotionTilt * (index < 2 ? 1 : .55) + slow * side) * rhythm.ears);
      });
    }

    if (runtime.microEvent === "ear_flick" && now < runtime.microUntil) {
      const progress = 1 - clamp((runtime.microUntil - now) / 480, 0, 1);
      const pulse = Math.sin(progress * Math.PI);
      map.ears.forEach((desc, index) => writeAdditive(runtime.core, desc, pulse * (index % 2 ? -.40 : .40)));
    }

    // Ears keep their small post-physics accent. Hair remains fully owned
    // by Cubism Physics; extra inertia is injected only into selected hair inputs.
  }

  function installCoreHook() {
    const current = getCore();
    if (!current) return false;
    if (runtime.core === current.core && current.cubism.__celiaMotionDirectorHook &&
        runtime.physics === (current.lapp._physics || null)) return true;

    runtime.core = current.core;
    runtime.cubism = current.cubism;
    runtime.mapped = mapParameters(current.core);
    runtime.smooth = {};
    runtime.lastFrameAt = 0;
    runtime.hairMotionEnergy = 0; runtime.hairMotionDirection = 0; runtime.hairMotionLastAt = 0; runtime.hairMotionPrev = null; runtime.hairMotionPrevVelocity = null;
    runtime.hairGhostX = 0; runtime.hairGhostY = 0; runtime.hairGhostZ = 0;
    runtime.hairGhostVX = 0; runtime.hairGhostVY = 0; runtime.hairGhostVZ = 0;
    runtime.hairImpulseEnergy = 0; runtime.hairGhostInputHooks = 0;
    runtime.bangMidFilteredVelocity = null;
    runtime.bangMidVelocityX = 0; runtime.bangMidVelocityY = 0; runtime.bangMidVelocityZ = 0;
    runtime.bangMidAccelerationX = 0; runtime.bangMidAccelerationY = 0; runtime.bangMidAccelerationZ = 0;
    runtime.bangMidImpulseEnergy = 0;
    runtime.bangMidGhostX = 0; runtime.bangMidGhostY = 0; runtime.bangMidGhostZ = 0;
    runtime.bangMidGhostVX = 0; runtime.bangMidGhostVY = 0; runtime.bangMidGhostVZ = 0;
    runtime.physics = current.lapp._physics || null;
    runtime.hookStage = runtime.physics ? "before-physics" : "before-flush-fallback";
    if (runtime.physics && !runtime.physics.__celiaMotionDirectorHook) {
      const physics = runtime.physics;
      runtime.hairGhostInputHooks = installHairPhysicsInputGhosts(physics);
      const evaluate = physics.evaluate;
      physics.evaluate = function celiaMotionDirectorPhysics(...args) {
        if (runtime.core === current.core) {
          try {
            applyFrame();
            updateHairMotionDynamics();
          } catch (error) { console.warn("[motion-director] pre-physics frame failed", error); }
        }
        const result = evaluate.apply(this, args);
        // Cubism resolves destination/source parameter indices lazily on its
        // first evaluation. If install-time discovery found none, retry after
        // that first native pass; hooks affect only subsequent hair inputs.
        if (runtime.core === current.core && runtime.hairGhostInputHooks === 0) {
          try { runtime.hairGhostInputHooks = installHairPhysicsInputGhosts(physics); }
          catch (error) { console.warn("[motion-director] hair input hook install failed", error); }
        }
        return result;
      };
      physics.__celiaMotionDirectorHook = true;
    }
    runtime.installs += 1;
    runtime.nextPresetAt = nowMs() + randomBetween(900, 1800);
    runtime.nextMicroAt = nowMs() + randomBetween(1800, 3200);
    runtime.nextPoseAt = nowMs() + randomBetween(420, 950);
    runtime.nextIdleGazeAt = nowMs() + randomBetween(2600, 5200);
    runtime.nextWeightAt = nowMs() + randomBetween(800, 1800);

    if (!current.cubism.__celiaMotionDirectorHook) {
      const originalUpdate = current.cubism.update.bind(current.cubism);
      current.cubism.update = function celiaMotionDirectorUpdate() {
        try {
          if (!runtime.physics) applyFrame();
          applyAccents();
        } catch (error) { console.warn("[motion-director] frame failed", error); }
        return originalUpdate();
      };
      current.cubism.__celiaMotionDirectorHook = true;
    }
    console.log(`[motion-director] ${VERSION} mapped`, mappedState());
    return true;
  }

  function mappedState() {
    if (!runtime.mapped) return {};
    const out = {};
    for (const [key, value] of Object.entries(runtime.mapped)) {
      out[key] = Array.isArray(value) ? value.map(item => item.id) : (value && value.id) || null;
    }
    return out;
  }

  function onEmotion(event) {
    const detail = event && event.detail ? event.detail : {};
    setEmotion(detail.emotion || detail.emocao || "neutral", detail);
  }

  function onSpeechEnergy(event) {
    const detail = event && event.detail ? event.detail : {};
    const energy = clamp(Number(detail.energy) || 0, 0, 1);
    const running = Boolean(detail.running) || energy > .018;
    runtime.speechEnergy = energy; runtime.speechSlope = clamp(Number(detail.slope) || 0, -1, 1);
    const now = wallNowMs();
    if (running && !runtime.speechRunning) {
      runtime.speechRunning = true; runtime.speechStartedAt = now; runtime.breathAnticipationUntil = now + 320;
      const side = runtime.gazeLastSide ? -runtime.gazeLastSide : (Math.random() < .5 ? -1 : 1);
      setGazeTarget(side * randomBetween(.50, .66), randomBetween(.10, .24), { mode: "formulation", holdMs: randomBetween(520, 880), headDelayMs: randomBetween(85, 145) });
    }
    if (running) runtime.lastSpeechAt = now;
  }

  function stopSpeechMotion() {
    const now = wallNowMs();
    runtime.speechRunning = false; runtime.speechEnergy = 0; runtime.speechSlope = 0; runtime.speechStoppedAt = now; runtime.breathRecoveryUntil = now + 900;
    setGazeTarget(0, 0, { mode: "camera", holdMs: Infinity, headDelayMs: 0, blink: false });
  }

  window.addEventListener("celia-emotion", onEmotion);
  window.addEventListener("celia-speech-energy", onSpeechEnergy);
  window.addEventListener("celia-lipsync-stop", stopSpeechMotion);

  const retry = setInterval(() => {
    if (installCoreHook() && runtime.core) {
      // Keep the interval alive: model swaps create a new core.
    }
  }, 500);

  let debugMirror = null;
  setInterval(() => {
    if (typeof document === "undefined" || !document.body) return;
    if (!debugMirror) {
      debugMirror = document.createElement("div");
      debugMirror.id = "celia-motion-debug";
      debugMirror.hidden = true;
      document.body.appendChild(debugMirror);
    }
    debugMirror.textContent = JSON.stringify(window.celiaMotionDirector.state());
  }, 750);

  window.celiaMotionDirector = {
    version: VERSION,
    states: STATES,
    presets: PRESETS,
    profiles: PROFILES,
    posePhrases: POSE_PHRASES,
    install: installCoreHook,
    setGazeTarget,
    metrics: motionMetrics,
    resetMetrics() {
      runtime.metricSamples = 0; runtime.metricStill = 0; runtime.metricHeadXSum = 0; runtime.metricHeadZSum = 0;
      runtime.metricNeutralHeadX = 0; runtime.metricNeutralHeadZ = 0; runtime.metricEyeSamples = 0; runtime.metricEyeClipSamples = 0; runtime.metricEyeMaxAbs = 0;
      runtime.metricHeadXSide = 0; runtime.metricHeadXSideMs = 0; runtime.metricHeadXMaxSameSideMs = 0;
      runtime.metricHeadZSide = 0; runtime.metricHeadZSideMs = 0; runtime.metricHeadZMaxSameSideMs = 0;
      runtime.metricMaxJerk = {}; runtime.metricPrev = null; runtime.metricPrevVelocity = null; runtime.metricPrevAcceleration = null;
      runtime.metricTraceHeadX = []; runtime.metricTraceHeadZ = []; runtime.metricTraceBodyY = []; runtime.metricNextTraceAt = nowMs();
    },
    debug: {
      resetPose() {
        runtime.poseName = "center"; runtime.posePhase = "rest"; runtime.poseTransition = null; runtime.poseHoldUntil = 0;
        runtime.poseHeadX = 0; runtime.poseHeadY = 0; runtime.poseHeadZ = 0; runtime.poseBodyX = 0; runtime.poseBodyY = 0; runtime.poseBodyZ = 0;
        runtime.nextPoseAt = Infinity; runtime.gestureCooldownUntil = 0; return true;
      },
      forcePose(name) {
        const pose = POSE_PHRASES[name]; if (!pose || name === "center") return false;
        runtime.poseName = name; beginPoseTransition(pose, pose.move, "moving", nowMs()); return true;
      },
      setMotionBudget(value) { runtime.motionBudget = clamp(Number(value) || 0, 0, 1); return runtime.motionBudget; },
      confusedShakeOffset,
      poseVector: currentPoseVector,
    },
    ownsParameter(id, core) {
      return runtime.core === core && runtime.frames > 0 && nowMs() - runtime.lastFrameAt < 250 &&
        DRIVER_CHANNELS.some(channel => runtime.mapped[channel]?.id === id);
    },
    setEmotion,
    forcePreset(name) {
      if (!PRESETS[name]) return false;
      runtime.preset = name;
      runtime.previousPresets.push(name);
      runtime.previousPresets = runtime.previousPresets.slice(-3);
      runtime.nextPresetAt = nowMs() + 10000;
      return true;
    },
    state() {
      return {
        version: VERSION, installed: Boolean(runtime.core), emotion: runtime.emotion,
        emotionSource: runtime.emotionSource, state: runtime.state, preset: runtime.preset,
        speechEnergy: runtime.speechEnergy, speechSlope: runtime.speechSlope,
        microEvent: runtime.microEvent, poseName: runtime.poseName, posePhase: runtime.posePhase, mapped: mappedState(),
        gaze: { mode: runtime.gazeMode, targetX: runtime.gazeTargetX, targetY: runtime.gazeTargetY, eyeX: runtime.gazeEyeX, eyeY: runtime.gazeEyeY, rawEyeX: runtime.gazeRawEyeX || 0, rawEyeY: runtime.gazeRawEyeY || 0, headX: runtime.gazeHeadX, headY: runtime.gazeHeadY },
        activity: runtime.activity, arousal: runtime.arousal, motionBudget: runtime.motionBudget, weightShift: runtime.weightShift, headStabilization: runtime.headStabilization,
        metrics: motionMetrics(),
        contributions: {
          ...runtime.contributions,
          faceHairChaos: runtime.faceHairChaos || 0,
          hairMotionEnergy: runtime.hairMotionEnergy || 0,
          hairMotionDirection: runtime.hairMotionDirection || 0,
          hairImpulseEnergy: runtime.hairImpulseEnergy || 0,
          hairGhostX: runtime.hairGhostX || 0,
          hairGhostY: runtime.hairGhostY || 0,
          hairGhostZ: runtime.hairGhostZ || 0,
          hairGhostInputHooks: runtime.hairGhostInputHooks || 0,
          bangMidVelocityX: runtime.bangMidVelocityX || 0,
          bangMidVelocityY: runtime.bangMidVelocityY || 0,
          bangMidVelocityZ: runtime.bangMidVelocityZ || 0,
          bangMidAccelerationX: runtime.bangMidAccelerationX || 0,
          bangMidAccelerationY: runtime.bangMidAccelerationY || 0,
          bangMidAccelerationZ: runtime.bangMidAccelerationZ || 0,
          bangMidImpulseEnergy: runtime.bangMidImpulseEnergy || 0,
          bangMidGhostX: runtime.bangMidGhostX || 0,
          bangMidGhostY: runtime.bangMidGhostY || 0,
          bangMidGhostZ: runtime.bangMidGhostZ || 0,
        }, frames: runtime.frames,
        installs: runtime.installs, hookStage: runtime.hookStage, motionSpeed: MOTION_SPEED, proceduralSpeed: PROCEDURAL_SPEED, verticalProceduralSpeed: VERTICAL_PROCEDURAL_SPEED,
        previousEmotion: runtime.previousEmotion,
        confusedShakeStartedAt: runtime.confusedShakeStartedAt,
        confusedShakeUntil: runtime.confusedShakeUntil,
        confusedShakeTurnKey: runtime.confusedShakeTurnKey,
        emotionHoldMsLeft: Math.max(0, runtime.emotionHoldUntil - nowMs()) / MOTION_SPEED,
        emotionTransitionMsLeft: Math.max(0, runtime.emotionTransitionUntil - nowMs()) / MOTION_SPEED,
        fps: window.celiaLive2DBody && window.celiaLive2DBody.getFps
          ? window.celiaLive2DBody.getFps() : null,
      };
    },
  };

  setInterval(() => {
    try {
      const nativeApi = window.celiaPetNative;
      if (!nativeApi || typeof nativeApi.write_motion_telemetry !== "function") return;
      nativeApi.write_motion_telemetry({
        ts: Date.now(), version: VERSION, state: runtime.state, emotion: runtime.emotion,
        poseName: runtime.poseName, posePhase: runtime.posePhase, gazeMode: runtime.gazeMode,
        activity: runtime.activity, arousal: runtime.arousal, motionBudget: runtime.motionBudget, metrics: motionMetrics(),
      }).catch(() => {});
    } catch (_) {}
  }, TELEMETRY_INTERVAL_MS);

  installCoreHook();
  console.log(`[motion-director] ${VERSION} installed`);
}());
