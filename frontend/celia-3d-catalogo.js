// celia-3d-catalogo.js — catalogo das animacoes extras da bancada 3D (Kurt, 2026-10-06, rodada 9). SO DADOS.
// KAYKIT: KayKit Character Animations 1.1, Rig_Medium, CC0 (modelos-3d/anim/kaykit/LICENSE-CC0.txt). Sem dedos, sem upperChest/pescoco/ombro.
//   formato: { arquivo.glb: { NomeDoClipeNoArquivo: [chave da acao, rotulo na tela, tipo ("gesto"|"acao"), rosto {chave_do_rosto: peso}, {ampla: true}?] } }
//   ampla: o gesto passa da cabeca ou abre muito os bracos e saia do quadro na camera de gesto (p130) -> usa a camera ampla das acoes.
//   O rosto usa so sobrancelha/olho (nunca chave de boca) para nao brigar com a regra "a acao manda na boca".
// CLAWATAR: .vrma Mixamo, NON-COMMERCIAL: so carregam com ?clawatar=1 e nunca entram em produto vendido.
//   formato: { "id do arquivo (sem .vrma)": [rotulo, rosto] }
export const KAYKIT = {
  "Rig_Medium_Simulation.glb": { Waving: ["kk_acenar", "acenar (KayKit)", "gesto", { target_93: .7, target_46: .5 }], Cheering: ["kk_torcida", "torcida", "gesto", { target_93: .9, target_48: .6 }] },
  "Rig_Medium_General.glb": { Hit_A: ["kk_golpe", "levar golpe", "gesto", { target_43: .6, target_108: .5 }], Interact: ["kk_interagir", "interagir", "gesto", { target_105: .4 }],
    Throw: ["kk_arremessar", "arremessar", "gesto", { target_91: .3 }], Use_Item: ["kk_usar", "usar item", "gesto", { target_105: .3 }],
    Hit_B: ["kk_golpe2", "levar golpe (2)", "gesto", { target_43: .6, target_108: .5 }, { ampla: true }], Idle_B: ["kk_espera", "espera (KayKit)", "gesto", {}], PickUp: ["kk_pegar", "pegar do chão", "gesto", {}] },
  "Rig_Medium_CombatRanged.glb": { Ranged_Magic_Spellcasting: ["kk_feitico", "feitiço", "gesto", { target_105: .5, target_46: .4 }],
    Ranged_Magic_Spellcasting_Long: ["kk_feitico_longo", "feitiço longo", "gesto", { target_46: .4 }], Ranged_Magic_Raise: ["kk_magia_erguer", "magia: erguer", "gesto", { target_46: .4 }],
    Ranged_Magic_Shoot: ["kk_magia_lancar", "magia: lançar", "gesto", { target_46: .4 }],
    Ranged_1H_Aiming: ["kk_pistola_mirar", "pistola: mirar", "gesto", { target_31: .5 }], Ranged_1H_Shoot: ["kk_pistola_atirar", "pistola: atirar", "gesto", { target_31: .5 }],
    Ranged_1H_Shooting: ["kk_pistola_rajada", "pistola: rajada", "gesto", { target_31: .5 }], Ranged_1H_Reload: ["kk_pistola_recarregar", "pistola: recarregar", "gesto", {}],
    Ranged_2H_Aiming: ["kk_rifle_mirar", "rifle: mirar", "gesto", { target_31: .5 }], Ranged_2H_Shoot: ["kk_rifle_atirar", "rifle: atirar", "gesto", { target_31: .5 }],
    Ranged_2H_Shooting: ["kk_rifle_rajada", "rifle: rajada", "gesto", { target_31: .5 }], Ranged_2H_Reload: ["kk_rifle_recarregar", "rifle: recarregar", "gesto", {}],
    Ranged_Bow_Aiming_Idle: ["kk_arco_mirando", "arco: mirando", "gesto", { target_31: .4 }], Ranged_Bow_Idle: ["kk_arco_espera", "arco: espera", "gesto", {}],
    Ranged_Bow_Draw: ["kk_arco_puxar", "arco: puxar", "gesto", { target_31: .4 }], Ranged_Bow_Draw_Up: ["kk_arco_puxar_cima", "arco: puxar pra cima", "gesto", { target_31: .4 }, { ampla: true }],
    Ranged_Bow_Release: ["kk_arco_soltar", "arco: soltar", "gesto", { target_31: .4 }], },
  "Rig_Medium_CombatMelee.glb": { Melee_1H_Attack_Slice_Horizontal: ["kk_espada_corte", "espada: corte", "gesto", { target_31: .6 }],
    Melee_1H_Attack_Slice_Diagonal: ["kk_espada_diagonal", "espada: corte diagonal", "gesto", { target_31: .6 }, { ampla: true }], Melee_1H_Attack_Chop: ["kk_espada_cima", "espada: golpe de cima", "gesto", { target_31: .6 }, { ampla: true }],
    Melee_1H_Attack_Stab: ["kk_espada_estocada", "espada: estocada", "gesto", { target_31: .6 }],
    Melee_2H_Idle: ["kk_montante_guarda", "montante: guarda", "gesto", { target_31: .4 }], Melee_2H_Attack_Chop: ["kk_montante_cima", "montante: golpe de cima", "gesto", { target_31: .6 }, { ampla: true }],
    Melee_2H_Attack_Slice: ["kk_montante_corte", "montante: corte", "gesto", { target_31: .6 }], Melee_2H_Attack_Stab: ["kk_montante_estocada", "montante: estocada", "gesto", { target_31: .6 }],
    Melee_2H_Attack_Spin: ["kk_montante_giro", "montante: giro", "acao", { target_31: .6 }],
    Melee_Block: ["kk_bloquear", "bloquear", "gesto", { target_31: .4 }], Melee_Blocking: ["kk_bloqueando", "bloqueando", "gesto", { target_31: .4 }],
    Melee_Block_Attack: ["kk_bloquear_atacar", "bloquear e atacar", "gesto", { target_31: .6 }], Melee_Block_Hit: ["kk_bloqueio_golpe", "bloqueio levando golpe", "gesto", { target_43: .5 }],
    Melee_Dualwield_Attack_Chop: ["kk_duas_cima", "duas lâminas: golpe de cima", "gesto", { target_31: .6 }], Melee_Dualwield_Attack_Slice: ["kk_duas_corte", "duas lâminas: corte", "gesto", { target_31: .6 }, { ampla: true }],
    Melee_Dualwield_Attack_Stab: ["kk_duas_estocada", "duas lâminas: estocada", "gesto", { target_31: .6 }, { ampla: true }], Melee_Unarmed_Attack_Punch_A: ["kk_soco", "soco", "gesto", { target_31: .6 }, { ampla: true }], Melee_Unarmed_Attack_Kick: ["kk_chute", "chute", "gesto", { target_31: .6 }] },
  // REPROVADOS na regua (rodada 8) e fora do pacote: Skeletons_Taunt (pe -11 cm, sola sobe 1,74 m, perna 0,79) e Jump_Full_Short (pe -9,8 cm, perna 0,70)
  // REPROVADOS na triagem p130 (rodada 9; pe > 2,5 cm abaixo do chao ou fora do quadro): kk_magia_invocar (pe -6.9 cm); kk_arco_soltar_cima (pe -6.2 cm); kk_espada_pulo (pe -2.8 cm; fora do quadro 1280x720 (22 quadros)); kk_montante_giro2 (pe -4.5 cm); kk_luta_guarda (pe -4.3 cm)
};
// Pedidos que NAO existem no clone (conferido em 2026-10-06): 35_Female Peek And Aim, 16_Cheering, 40_Happy Idle.
export const CLAWATAR = { "161_Waving": ["acenar (Mixamo)", { target_105: .4 }], "19_Clapping": ["palmas (Mixamo)", { target_105: .4 }], "88_Thinking": ["pensando (Mixamo)", { target_105: .4 }], "145_Shrugging": ["dar de ombros (Mixamo)", { target_105: .4 }],
  "0_Angry": ["bravo (Mixamo)", { target_31: .7 }], "26_Defeat": ["derrota (Mixamo)", { target_74: .5 }], "49_Joyful Jump": ["pulo de alegria (Mixamo)", { target_46: .4 }, "acao"], "4_Belly Dance": ["dança do ventre (Mixamo)", { target_46: .4 }, "acao"] };
