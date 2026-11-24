window.CELIA_DOCS = {
  "pt-BR": {
    "ui": {
      "label": "Documentação",
      "brand": "Célia",
      "search": "Buscar",
      "searchPlaceholder": "Buscar",
      "noResults": "Nenhum resultado",
      "theme": "Tema",
      "light": "Claro",
      "dark": "Escuro",
      "language": "Idioma",
      "menu": "Menu",
      "onPage": "Nesta página",
      "skip": "Ir para o conteúdo",
      "home": "Início",
      "previous": "Anterior",
      "next": "Próximo",
      "comingSoon": "Em breve",
      "groups": {
        "start": "Primeiros passos",
        "core": "Arquitetura e voz",
        "body": "Corpo e mundo 3D",
        "release": "Integrações e publicação"
      },
      "routeIndex": "Guias técnicos",
      "models": "Modelos"
    },
    "pages": [
      {
        "id": "inicio",
        "title": "Documentação da Célia",
        "summary": "Arquitetura, operação e limites do projeto de VTuber de IA.",
        "kicker": "Primeiros passos",
        "group": "start",
        "sections": [
          {
            "title": "O que o projeto entrega",
            "text": "A Célia é um runtime conversacional com voz, apresentação 2D, um modo Pet de desktop, um Corpo 3D no navegador e um módulo de cena 3D em Godot. Este guia explica como essas superfícies recebem a conversa, executam a apresentação e se ligam a fontes externas."
          },
          {
            "title": "Roteiro de leitura",
            "text": "Comece pela arquitetura e pelo fluxo. Para trabalhar com o avatar, leia Corpo, Modo Pet e VRM. Para a cena com Bento e Kenny, siga para Mundo 3D e TikTok.",
            "links": [
              {
                "label": "Arquitetura do sistema",
                "page": "arquitetura"
              },
              {
                "label": "Mundo 3D e TikTok",
                "page": "mundo-3d"
              }
            ]
          },
          {
            "title": "Publicação prevista para dezembro",
            "text": "A documentação é uma prévia técnica. A página de lançamento explica o que integra a Célia, o que terá distribuição separada e que informações dependem do repositório público.",
            "links": [
              {
                "label": "Escopo do lançamento",
                "page": "lancamento"
              }
            ]
          }
        ]
      },
      {
        "id": "comecar",
        "title": "Comece por aqui",
        "summary": "Prepare uma instalação de referência e teste o caminho de uma resposta.",
        "group": "start",
        "kicker": "Primeiros passos",
        "sections": [
          {
            "title": "Requisitos do projeto",
            "text": "O pyproject.toml da V02 declara Python 3.10–3.12. A instalação usa um ambiente Python próprio; Electron e Godot são componentes adicionais para as superfícies de desktop e 3D. O release de dezembro definirá o pacote, a instalação por sistema e quais assets acompanham a distribuição.",
            "code": "python --version",
            "language": "bash"
          },
          {
            "title": "Primeiro teste com uma instalação preparada",
            "text": "Na instalação Linux de referência, bin/ligar.sh chama run_celia_linux.py. O projeto também conserva run_server.py como ponto de entrada do upstream. Confira o seu ambiente e o release antes de executar um lançador. Para uma primeira verificação, use texto antes de ligar microfone ou integração."
          },
          {
            "title": "Percurso de aceitação",
            "text": "Confirme a entrada de texto, a resposta, a reprodução audível e só depois as reações do avatar. Passe por microfone, Pet, VRM e Godot separadamente. Para a cena 3D, comece com o simulador."
          }
        ]
      },
      {
        "id": "lancamento",
        "title": "Escopo do lançamento",
        "summary": "O que entra no projeto principal e quais módulos terão distribuição própria.",
        "kicker": "Primeiros passos",
        "group": "start",
        "sections": [
          {
            "title": "Projeto da Célia",
            "text": "O escopo previsto reúne conversa, voz, Live2D, Pet, Corpo/VRM e Mundo 3D. O modo TikTok 3D faz parte desse escopo. O pacote final e as instruções de instalação serão definidos pelo release público."
          },
          {
            "title": "Distribuições separadas",
            "text": "O bot de Minecraft terá um repositório público próprio, ligado de volta por esta documentação. O bot de Discord será distribuído comercialmente pela Kiwify, com um link de compra aqui quando divulgado. Cada um tem um ciclo de publicação próprio."
          },
          {
            "title": "O que acompanhar no release",
            "text": "Repositório, requisitos, licença, instruções por ambiente e relação de assets distribuíveis são itens do release. Use os links oficiais publicados quando estiverem disponíveis.",
            "links": [
              {
                "label": "Links oficiais e previstos",
                "page": "links"
              }
            ]
          },
          {
            "title": "Divisão de repositórios (cada módulo no seu lugar)",
            "text": "Para manter o projeto organizado e fácil de manter, a distribuição separa responsabilidades: o repositório principal reúne o core da Célia (conversa, Motor de Emoções v3.0, Live2D/VRM/Pet, TikTok, YouTube, Twitch e React v2). O bot de Minecraft e o bot de jogos/emuladores (Super Nintendo e outros jogos) ficam em repositórios públicos separados. O bot de Discord já existe e terá distribuição com link de compra na Kiwify."
          },
          {
            "title": "Créditos à arquitetura base (Open-LLM-VTuber / OpenLive2D)",
            "text": "A arquitetura inicial da Célia foi construída tomando como base o projeto aberto Open-LLM-VTuber / OpenLive2D, expandido com motor próprio de emoções v3.0, pipeline OmniVoice, controles persistentes de corpo/Pet, módulo React v2 e cena 3D em Godot.",
            "links": [
              {
                "label": "Open-LLM-VTuber (GitHub)",
                "url": "https://github.com/Open-LLM-VTuber/Open-LLM-VTuber"
              }
            ]
          }
        ]
      },
      {
        "id": "arquitetura",
        "title": "Arquitetura",
        "summary": "A divisão entre entrada, núcleo de conversa, síntese e clientes de apresentação.",
        "kicker": "Arquitetura e voz",
        "group": "core",
        "sections": [
          {
            "title": "Um núcleo, várias superfícies",
            "text": "O backend FastAPI hospeda o fluxo conversacional e atende o cliente por WebSocket. O agente prepara contexto, preserva memória de conversa e solicita a resposta ao provedor selecionado. Áudio, legendas e reações seguem para a camada de apresentação conforme a superfície.",
            "code": "Texto ou microfone\n  → transcrição (para áudio)\n  → contexto + modelo de conversa\n  → resposta / eventos de emoção\n  → síntese de voz + cliente visual"
          },
          {
            "title": "Componentes para localizar no código",
            "text": "A estrutura abaixo é um mapa de leitura da implementação consultada. A organização do pacote público será confirmada no release.",
            "items": [
              "src/celia/core/ · orquestração e eventos de emoção",
              "src/celia/tts/ · implementações de síntese",
              "frontend/ · cliente web, Corpo e apresentação",
              "tools/pet-electron/ · janela desktop Pet",
              "integrations/live3d/ · cena Godot na instalação de referência"
            ]
          },
          {
            "title": "Separação de runtime",
            "text": "Pet e cliente web apresentam dados da sessão. O Mundo 3D é uma cena com fonte de eventos própria. Uma configuração de provedor ou uma demonstração de assets não comprova integração ativa em uma transmissão."
          },
          {
            "title": "Montagem das rotas no servidor FastAPI (server.py)",
            "text": "O servidor central registra WebSocket de clientes, rotas de ponte externa, laboratório de áudio, observabilidade, modo Live com trava de segurança e o CeliaSingleton.",
            "language": "python",
            "code": "# src/celia/server.py\nfrom fastapi import FastAPI\nfrom .routes import init_client_ws_route, init_webtool_routes, init_proxy_route\nfrom .bridge_routes import init_bridge_routes\nfrom .audio_lab_routes import init_audio_lab_routes\nfrom .observability_routes import init_observability_routes\nfrom .live_mode.routes import init_live_routes\nfrom .celia_singleton import CeliaSingleton\n\n# Cada módulo expõe suas rotas sobre a mesma instância FastAPI + ServiceContext"
          }
        ]
      },
      {
        "id": "fluxo",
        "title": "Fluxo de conversa",
        "summary": "Como o projeto recebe áudio, decide o turno e devolve uma resposta.",
        "kicker": "Arquitetura e voz",
        "group": "core",
        "sections": [
          {
            "title": "Entrada e transcrição",
            "text": "O cliente recebe texto ou captura microfone. Em áudio, a detecção de atividade de voz delimita o trecho e o ASR produz texto para a conversa. Existe rota local de transcrição nos perfis documentados; a disponibilidade depende do ambiente."
          },
          {
            "title": "Contexto, fila e 9router",
            "text": "O 9router funciona como gateway compatível com OpenAI para encaminhar pedidos a provedores. Filas e funções podem escolher modelos diferentes. O agente aproveita contexto e memória; o comportamento contínuo decide quando esperar, falar ou emitir uma reação breve."
          },
          {
            "title": "Rotas e modelos alternativos",
            "text": "Grok e outras APIs aparecem como opções de rota conforme a configuração e a função. Spell local permanece como opção mencionada para avaliação. O release indicará combinações suportadas e requisitos."
          },
          {
            "title": "Áudio e estado visual",
            "text": "A resposta pode ser sintetizada e entregue ao cliente junto de eventos de fala. Eventos de emoção podem acionar expressões ou pequenos gestos separados da fala. Legendas, sincronização labial e confirmação de reprodução pertencem ao caminho de saída.",
            "links": [
              {
                "label": "Histórico e síntese de voz",
                "page": "voz"
              },
              {
                "label": "Corpo e emoções",
                "page": "corpo"
              }
            ]
          },
          {
            "title": "Orquestração do turno via WebSocket (websocket_handler.py)",
            "text": "Cada mensagem ou áudio recebido pelo cliente passa pelo WebSocketHandler, que coordena histórico, comandos de memória, legendas do OBS e preparo do payload de áudio sincronizado.",
            "language": "python",
            "code": "# src/celia/websocket_handler.py\nfrom .service_context import ServiceContext\nfrom .message_handler import message_handler\nfrom .utils.stream_audio import prepare_audio_payload\nfrom .core.continuous_behavior import continuous_behavior\nfrom .chat_history_manager import create_new_history, get_history\nfrom .core.obs_subtitles import obs_subtitles\n\nasync def send_turn_audio(websocket, audio_path: str, text: str, actions: dict):\n    payload = prepare_audio_payload(\n        audio_path=audio_path,\n        display_text={\"text\": text, \"name\": \"Célia\"},\n        actions=actions,\n    )\n    obs_subtitles.update(text)\n    await websocket.send_json(payload)"
          },
          {
            "title": "Motor de emoções antes de cada resposta (celia_emotions.py)",
            "text": "Antes de responder, o CeliaSingleton calcula o EmotionalState v3.0 (intensidade, valência, energia e anti-repetição) e publica o evento para o corpo Live2D/3D.",
            "language": "python",
            "code": "# src/celia/celia_emotions.py — CÉLIA EMOTION ENGINE v3.0\nfrom dataclasses import dataclass, field\nfrom typing import List\n\n@dataclass\nclass EmotionalState:\n    emotion: str = \"neutral\"\n    intensity: int = 0\n    energy: float = 0.5\n    valence: float = 0.5\n    trigger: str = \"\"\n    confidence: float = 0.0\n    matched_keywords: List[str] = field(default_factory=list)\n    timestamp: str = \"\" "
          }
        ]
      },
      {
        "id": "configuracao",
        "title": "Configuração e provedores",
        "summary": "Separe reconhecimento de fala, conversa, voz e exibição.",
        "group": "core",
        "kicker": "Arquitetura e voz",
        "sections": [
          {
            "title": "Pontos de configuração",
            "text": "O conf.yaml seleciona os provedores. A referência tem reconhecimento compatível com OpenAI e voz OmniVoice. As cadeias de modelos têm configuração própria. Mantenha as credenciais no ambiente de execução e use valores de exemplo em materiais públicos.",
            "code": "# Campos selecionados na configuração de referência\nasr_model: openai_compatible_asr\ntts_model: omnivoice",
            "language": "yaml"
          },
          {
            "title": "Local e hospedado",
            "text": "9router é o gateway documentado. Verifique separadamente o endpoint, a seleção de cada função e a rota de reserva. Uma opção presente no arquivo não comprova que está selecionada no runtime."
          }
        ]
      },
      {
        "id": "voz",
        "title": "Voz e histórico do TTS",
        "summary": "De Piper e Edge TTS ao XTTS, OmniVoice e ao treinamento em andamento.",
        "kicker": "Arquitetura e voz",
        "group": "core",
        "sections": [
          {
            "title": "Piper e Edge TTS",
            "text": "As primeiras fases usaram Piper e Edge TTS. Piper também teve uma linha fine-tunada para português, com o V9-D registrado no histórico. As opções de síntese foram avaliadas em versões e ambientes diferentes."
          },
          {
            "title": "XTTS",
            "text": "O XTTS foi uma etapa de adaptação e experimentação vocal. O projeto preserva o histórico e usos de laboratório. Em 18 de novembro foi documentada sua retirada do caminho principal de fala."
          },
          {
            "title": "OmniVoice",
            "text": "O caminho operacional documentado emprega OmniVoice para sintetizar a resposta. Célia Oficial V2 é a referência aprovada na Central IA para novas falas. Uma referência de voz, um motor de síntese e um checkpoint fine-tunado são componentes distintos."
          },
          {
            "title": "Voz própria em treinamento",
            "text": "O criador anunciou que está treinando a própria voz. O resultado, o perfil adotado e os arquivos que poderão ser distribuídos ficam sujeitos a validação. Português brasileiro é a prioridade da voz da Célia; o seletor deste guia traduz a documentação."
          },
          {
            "title": "Como avaliar uma mudança de voz",
            "text": "Compare as mesmas falas em português, incluindo nomes próprios, números e frases com pausas. Registre motor, perfil de referência, velocidade, pronúncia e a reprodução audível.",
            "items": [
              "ONNX ou checkpoint são artefatos do modelo.",
              "WAV é resultado de síntese.",
              "Confirmação do cliente e escuta verificam etapas diferentes."
            ]
          },
          {
            "title": "Inferência local com OmniVoice (omnivoice_tts.py)",
            "text": "O motor padrão de produção usa OmniVoice com lock de inferência exclusivo, sanitização de texto e validação dos assets da voz oficial antes de sintetizar o WAV.",
            "language": "python",
            "code": "# src/celia/tts/omnivoice_tts.py\nimport threading\nfrom dataclasses import dataclass\nfrom .production_voice import VOICE_PROFILE_ID, validate_production_assets\nfrom ..utils.tts_preprocessor import sanitize_production_tts_text\n\n_INFERENCE_LOCK = threading.Lock()\n\n@dataclass(frozen=True)\nclass OmniVoicePreset:\n    name: str\n    label: str\n    description: str\n    speed: float\n    num_step: int\n    guidance_scale: float\n    seed: int | None = None\n    text_tag: str | None = None"
          }
        ]
      },
      {
        "id": "corpo",
        "title": "Corpo e emoções",
        "summary": "A superfície web que apresenta o avatar e administra a sessão.",
        "kicker": "Corpo e mundo 3D",
        "group": "body",
        "sections": [
          {
            "title": "Interface principal",
            "text": "Corpo é a interface que reúne conversa, controles e apresentação do avatar. Live2D usa seu próprio canvas. A camada 3D do navegador usa um VRM ligado ao código de Corpo 3D. O Pet retira o painel de controle para usar o avatar como janela de desktop."
          },
          {
            "title": "Eventos e poses",
            "text": "O núcleo tem um barramento de emoções; o cliente aplica expressões e perfis de movimento disponíveis no rig. O módulo Corpo 3D organiza ações, poses e aliases de emoções em código. A sincronização da boca acompanha a fala enquanto poses e microações podem alterar o movimento corporal."
          },
          {
            "title": "Como separar um defeito de rig e um defeito de evento",
            "text": "Teste o avatar parado e uma pose conhecida antes de introduzir resposta, áudio ou evento externo. Se a pose funciona na bancada, inspecione a ligação entre evento e estado. Se o braço deforma na pose isolada, inspecione pesos, ossos e correções do modelo.",
            "links": [
              {
                "label": "Como o VRM entra no Corpo",
                "page": "vrm"
              },
              {
                "label": "Modo Pet",
                "page": "pet"
              }
            ]
          },
          {
            "title": "Trechos do código · src/celia/core/emotion_bus.py",
            "text": "Recorte da implementação de referência; os nomes de arquivos são relativos à raiz do projeto.",
            "code": "@dataclass(frozen=True)\nclass EventoEmocao:\n    \"\"\"Um pedido de emoção, já normalizado.\"\"\"\n\n    emocao: str\n    origem: str\n    prioridade: int\n    ttl_s: float | None\n    trava_s: float\n    em: float  # time.monotonic() de quando entrou\n    persistente: bool = False\n    metadados: dict[str, Any] = field(default_factory=dict)",
            "language": "python"
          },
          {
            "title": "Controles de Corpo e Persistência (Frontend)",
            "text": "Trecho real do runtime celia-live2d-body.js que calibra a matriz do Cubism e separa a renderização do navegador e do Pet.",
            "code": "// Célia: Controle de escala, matriz de projeção e modo Pet nativo\n(function installLive2DBodyControls() {\n  \"use strict\";\n  const STORAGE_PREFIX = \"celia-live2d-body:\";\n  const PET_DEFAULT_SCALE = 2.25;\n\n  function isNativePet() {\n    return new URLSearchParams(location.search).get(\"pet\") === \"1\";\n  }\n\n  function modelMatrix() {\n    const adapter = window.getLAppAdapter && window.getLAppAdapter();\n    const model = adapter && adapter.getModel && adapter.getModel();\n    const matrix = model && model._modelMatrix;\n    return matrix && typeof matrix.scale === \"function\" ? matrix : null;\n  }\n\n  function applyTransform(scale, offsetX, offsetY) {\n    const m = modelMatrix();\n    if (!m) return false;\n    m.scale(scale, scale);\n    m.translate(offsetX, offsetY);\n    return true;\n  }\n})();",
            "language": "javascript"
          },
          {
            "title": "Processamento de Emoções no Backend",
            "text": "Classe Live2dModel que seleciona a expressão e a moção do modelo a partir da análise de sentimento do LLM.",
            "code": "# Célia Backend: Mapeamento de emoções e modelo Cubism\nclass Live2dModel:\n    def __init__(self, live2d_model_name: str, model_dict_path: str = \"model_dict.json\"):\n        self.live2d_model_name = live2d_model_name\n        self.set_model(live2d_model_name)\n\n    def set_model(self, model_name: str) -> None:\n        info = self.model_dict.get(model_name, {})\n        self.model_info = info\n        self.emo_map = info.get(\"emotionMap\", {})\n        logger.info(f\"Modelo Live2D ativo: {model_name} com {len(self.emo_map)} emoções\")\n\n    def get_motion_for_emotion(self, emotion: str) -> dict:\n        return self.emo_map.get(emotion, self.emo_map.get(\"neutral\", {}))",
            "language": "python"
          },
          {
            "title": "Como funciona o Motor de Emoções v3.0 (celia_emotions.py)",
            "text": "O motor de emoções faz parte do repositório principal da Célia. Antes de cada resposta, ele analisa o contexto e calcula intensidade, energia, valência e palavras-chave, aplicando anti-repetição para evitar que a mesma expressão fique presa em várias rodadas seguidas, e publica o estado via publish_emotion para o corpo Live2D e 3D.",
            "language": "python",
            "code": "# src/celia/celia_emotions.py — Fluxo de decisão emocional\nfrom src.celia.celia_emotions import EmotionalState\nfrom src.celia.core.track_emotion_client import publish_emotion\n\n# 1. CeliaSingleton avalia a fala/contexto com anti-repetição das últimas N rodadas\n# 2. Gera EmotionalState(emotion, intensity, energy, valence, trigger)\n# 3. Publica o evento emocional para o frontend Live2D e cena Godot 3D\nif publish_emotion:\n    publish_emotion(state.emotion, intensity=state.intensity)"
          }
        ]
      },
      {
        "id": "pet",
        "title": "Modo Pet",
        "summary": "A Célia como uma janela de desktop com o mesmo cliente de apresentação.",
        "kicker": "Corpo e mundo 3D",
        "group": "body",
        "sections": [
          {
            "title": "Isolamento de áudio — contrato de segurança",
            "text": "Pet e demais superfícies visuais não devem abrir o microfone físico. A animação do mascote reutiliza o RMS do áudio já capturado. Uma única fonte autorizada alimenta o ASR; mute e troca de fonte afetam somente a captura da aplicação. Nunca modificar o microfone global nem os serviços de áudio do sistema. Requisito de homologação, ainda não certificado no pacote público atual."
          },
          {
            "title": "Abertura no desktop",
            "text": "O lançador do Pet abre a página servida pela Célia em uma janela Electron transparente. O modo de palco isola a apresentação do avatar. O runtime conversacional continua servindo a sessão; chamadas repetidas podem focar a janela existente."
          },
          {
            "title": "Interação na janela",
            "text": "O Pet pode se comportar como sobreposição do desktop. A política de clique e o gesto de arrastar pertencem ao wrapper desktop. A interface de configurações e chat fica no Corpo."
          },
          {
            "title": "Uma única legenda",
            "text": "O contrato atual do Pet usa frontend/celia-legenda-embutida.js como proprietário visual da legenda. O fallback fica na mesma superfície. Em verificação local, uma fala deve resultar em uma legenda."
          },
          {
            "title": "Pontos de entrada no código",
            "text": "frontend/celia-pet.js e celia-browser-pet.js conectam o modo Pet ao cliente. tools/pet-electron/main.js gerencia a janela. A implementação possui também um lançador Windows em tools/run_pet.ps1."
          },
          {
            "title": "Trechos do código · tools/pet-electron/main.js",
            "text": "Recorte da implementação de referência; os nomes de arquivos são relativos à raiz do projeto.",
            "code": "minHeight: 360,\n    frame: false,\n    transparent: true,\n    backgroundColor: \"#00000000\",\n    hasShadow: false,\n    resizable: false,\n    alwaysOnTop: true,",
            "language": "javascript"
          },
          {
            "title": "Como o Live2D foi colocado dentro da interface do Modo Pet",
            "text": "A janela do Modo Pet é uma instância Electron transparente (transparent: true, frame: false, alwaysOnTop: true) que carrega a mesma interface web da Célia com o parâmetro ?pet=1. O script celia-live2d-body.js detecta isNativePet(), ativa a escala própria (PET_DEFAULT_SCALE = 2.25) e salva posição/zoom em uma chave separada no localStorage (celia-live2d-body:pet), sem alterar o enquadramento da janela principal do navegador.",
            "language": "javascript",
            "code": "// frontend/celia-live2d-body.js — Separação entre Interface Web e Pet Electron\nconst STORAGE_PREFIX = \"celia-live2d-body:\";\nconst PET_DEFAULT_SCALE = 2.25;\n\nfunction isNativePet() {\n  return new URLSearchParams(location.search).get(\"pet\") === \"1\";\n}\n\nfunction scope() {\n  // Apenas ?pet=1 grava e restaura a pose exclusiva do Modo Pet\n  return isNativePet() ? \"pet\" : \"interface\";\n}\n\nfunction storageKey() {\n  return STORAGE_PREFIX + scope();\n}"
          },
          {
            "title": "Estrutura da Janela Electron do Pet",
            "text": "Criação da viewport transparente com suporte a click-through, mantendo o avatar flutuando sobre a área de trabalho.",
            "code": "// Célia Pet: Janela transparente Electron com suporte a arrastar\nconst { app, BrowserWindow, screen } = require('electron');\n\nfunction createPetWindow() {\n  const display = screen.getPrimaryDisplay();\n  const petWin = new BrowserWindow({\n    width: 480,\n    height: 640,\n    x: display.workAreaSize.width - 500,\n    y: display.workAreaSize.height - 660,\n    transparent: true,\n    frame: false,\n    alwaysOnTop: true,\n    hasShadow: false,\n    resizable: true,\n    webPreferences: {\n      nodeIntegration: true,\n      contextIsolation: false\n    }\n  });\n\n  petWin.setIgnoreMouseEvents(false);\n  petWin.loadURL('http://127.0.0.1:12393/?pet=1');\n}",
            "language": "javascript"
          }
        ]
      },
      {
        "id": "vrm",
        "title": "VRM no Corpo",
        "summary": "Rig humanoide, expressões e correções na importação do avatar.",
        "kicker": "Corpo e mundo 3D",
        "group": "body",
        "sections": [
          {
            "title": "Modelo e cliente",
            "text": "O projeto de referência usa um VRM da Célia na interface 3D. O arquivo carrega malhas, materiais, rig humanoide e expressões. O módulo frontend/celia-3d-corpo.js controla gestos, poses e correções em runtime, com uma bancada para inspecionar comportamento visual."
          },
          {
            "title": "O que muda em Blender e no app",
            "text": "A revisão do modelo registra realinhamento de ossos, pesos de braço e ombro e formas corretivas. No app, o ângulo do braço aciona as formas correspondentes. Correções de cabelo, torção, mãos e saia podem depender do código da versão em uso."
          },
          {
            "title": "Ao trocar ou revisar um VRM",
            "text": "Preserve a versão de referência e confira mapeamento dos ossos, pose de repouso, blink, boca e braços com uma ação de teste. Revise também a procedência de rig, malhas, roupas, texturas e dependências antes de redistribuí-los."
          },
          {
            "title": "Limite do pacote V02",
            "text": "O seletor 2D/3D abre a interface da bancada e espera o VRM em frontend/modelos-3d/celia_v3_web.vrm. Na árvore V02 consultada, esse caminho padrão está sem o arquivo. A instalação original tem o modelo oficial. É preciso reconciliar a distribuição antes de prometer o carregamento 3D em uma instalação nova."
          },
          {
            "title": "Trechos do código · frontend/celia-3d-interface.js",
            "text": "Recorte da implementação de referência; os nomes de arquivos são relativos à raiz do projeto.",
            "code": "const quadro = document.createElement(\"iframe\");\n  quadro.id = \"celia-3d-quadro\"; quadro.title = \"Célia 3D\"; quadro.setAttribute(\"aria-hidden\", \"true\"); quadro.tabIndex = -1;\n  const params3d = new URLSearchParams(location.search);",
            "language": "javascript"
          }
        ]
      },
      {
        "id": "mundo-3d",
        "title": "Mundo 3D e TikTok",
        "summary": "Cena Godot, diretor de eventos, Bento, Kenny, cenário e saídas.",
        "kicker": "Corpo e mundo 3D",
        "group": "body",
        "sections": [
          {
            "title": "Como a cena é montada",
            "text": "O módulo de referência em Godot instancia cenário, Célia em VRM, Bento e Kenny em GLB, diretor de eventos e HUD. O simulador alimenta um barramento com eventos de chat, presentes, curtidas, seguidores e fala; o diretor escolhe fila, câmera e ações.",
            "code": "Simulador ou fonte de eventos\n  → barramento\n  → diretor e filas\n  → Célia / Bento / Kenny / mundo / HUD"
          },
          {
            "title": "Bento e Kenny em runtime",
            "text": "Bento tem seu próprio modelo e controlador de animação. Pode andar ou entrar numa apresentação; durante a dança, o retarget assume o corpo para evitar disputa com a pose procedural. Kenny tem modelo e animação separados; no simulador, uma Rosa aciona uma ação na tigela e uma reação visual."
          },
          {
            "title": "Cenário, câmera e HUD",
            "text": "O módulo de mundo carrega o céu e organiza iluminação, grama, elementos do cenário e pontos para os personagens. O diretor alterna enquadramentos e a camada HUD apresenta avisos, legendas e destaques. Configurações da cena separam modelos, alturas, ações e tempos."
          },
          {
            "title": "TikTok 3D no projeto principal",
            "text": "O leitor do TikTok e a cena Godot possuem papéis distintos. A cena pode ser demonstrada com eventos simulados; existe código para consumir a ponte de eventos da Célia. Uma sessão ao vivo exige configuração, evento real autorizado e validação da reprodução. O modo 3D está previsto no projeto principal."
          },
          {
            "title": "Mapa do módulo Godot",
            "text": "Na instalação de referência, integrations/live3d/godot/cenas/main.tscn é a cena. scripts/main.gd monta os componentes; mundo.gd configura o cenário, diretor.gd organiza eventos e scripts/danca/ trata retarget e apresentações. config/live3d.json e config/dancas.json separam ajustes da cena e catálogo de danças."
          },
          {
            "title": "Trechos do código · godot/project.godot",
            "text": "Recorte da implementação de referência; os nomes de arquivos são relativos à raiz do projeto.",
            "code": "[application]\n\nconfig/name=\"Celia Live 3D\"\nrun/main_scene=\"res://cenas/main.tscn\"\nconfig/features=PackedStringArray(\"4.7\", \"Forward Plus\")\n\n[autoload]\n\nBarramento=\"*res://scripts/barramento.gd\"",
            "language": "ini"
          }
        ]
      },
      {
        "id": "modelos",
        "title": "Modelos e avatares",
        "summary": "Modelos de referência, origem e compatibilidade.",
        "group": "body",
        "kicker": "Corpo e mundo 3D",
        "sections": [
          {
            "title": "Live2D e VRM",
            "text": "O modelo Live2D usa recursos Cubism. O modelo de entrada normalmente é um arquivo .model3.json acompanhado de .moc3, texturas e configurações. O renderizador 3D usa VRM ou GLB conforme a superfície; carregar um arquivo no Corpo e em Godot são verificações diferentes."
          },
          {
            "title": "Origem dos modelos",
            "text": "A galeria identifica o destino de cada referência quando ele pode ser confirmado. Confira modelo, rig, permissões de exibição e termos do autor antes de incluir arquivos em uma distribuição."
          },
          {
            "title": "Configuração no model_dict.json",
            "text": "Cada modelo Live2D é registrado no dicionário central indicando o caminho do .model3.json, texturas e mapa de emoções para o pipeline conversacional.",
            "code": "{\n  \"pachan_vts\": {\n    \"path\": \"live2d-models/elizabeth_vts/pachirisu anime girl - top half.model3.json\",\n    \"scale\": 1.2,\n    \"emotionMap\": {\n      \"happy\": \"happy.exp3.json\",\n      \"angry\": \"angry.exp3.json\"\n    }\n  },\n  \"shogun_vts\": {\n    \"path\": \"live2d-models/shogun_vts/VT_student.model3.json\",\n    \"scale\": 1.0\n  }\n}",
            "language": "json"
          }
        ],
        "models": [
          {
            "name": "Pachan 2.0 · Half-Body (medwokl2d)",
            "description": "Modelo Live2D Half-Body gratuito para VTube Studio criado por medwokl2d no BOOTH. Integrado em web/live2d-models/elizabeth_vts (pachirisu anime girl - top half.model3.json).",
            "image": "assets/model-pachan.webp",
            "links": [
              {
                "label": "BOOTH · medwokl2d (#4711410)",
                "url": "https://booth.pm/en/items/4711410"
              }
            ]
          },
          {
            "name": "VT Student · Raiden Shogun",
            "description": "Modelo Live2D VT_Student (ChoDenPA / Milkydoragon). Integrado em web/live2d-models/shogun_vts (VT_student.model3.json).",
            "image": "assets/model-shogun.webp",
            "links": [
              {
                "label": "Ver referência no Gumroad",
                "url": "https://milkydoragon.gumroad.com/l/avexk"
              }
            ]
          },
          {
            "name": "Hiyori Momo · Live2D Sample",
            "description": "Modelo clássico de referência Cubism (usado como base histórica de VTubers IA como Neuro-sama). Integrado em web/live2d-models/hiyori_vts (hiyori.model3.json).",
            "image": "assets/model-hiyori.webp",
            "links": [
              {
                "label": "Live2D Cubism Sample Models",
                "url": "https://www.live2d.com/en/learn/sample/"
              }
            ]
          },
          {
            "name": "March 7th · 三月七",
            "description": "Modelo Live2D gratuito de March 7th (Honkai: Star Rail) por Design.genius no BOOTH. Integrado em web/live2d-models/march_7th_vts (march 7th.model3.json).",
            "image": "assets/model-march7th.webp",
            "links": [
              {
                "label": "BOOTH · Design.genius (#4961342)",
                "url": "https://booth.pm/en/items/4961342"
              }
            ]
          },
          {
            "name": "Huohuo · 藿藿",
            "description": "Modelo Live2D gratuito de Huohuo por bailyovo no BOOTH, com expressões e física completa. Integrado em web/live2d-models/huohuo_vts (huohuo.model3.json).",
            "image": "assets/model-huohuo.webp",
            "links": [
              {
                "label": "BOOTH · bailyovo (#5288339)",
                "url": "https://booth.pm/en/items/5288339"
              }
            ]
          },
          {
            "name": "Sumire · 女子 モデル名:sumire",
            "description": "Modelo Live2D genérico gratuito de alta fidelidade por creape no BOOTH (sumire_free_001.model3.json).",
            "image": "assets/model-sumire.webp",
            "links": [
              {
                "label": "BOOTH · creape (#5002899)",
                "url": "https://booth.pm/en/items/5002899"
              }
            ]
          },
          {
            "name": "Hoshino Nya",
            "description": "Modelo Live2D gratuito por skiyoshi no BOOTH com texturas 4K e expressões. Integrado em web/live2d-models/hoshinonya_vts (hoshinonya_skiyoshi.model3.json).",
            "image": "assets/model-hoshinonya.webp",
            "links": [
              {
                "label": "BOOTH · skiyoshi (#3988142)",
                "url": "https://booth.pm/en/items/3988142"
              }
            ]
          },
          {
            "name": "Maid Cat Girl · 猫娘",
            "description": "Modelo Live2D de garota gato maid por comte19 no BOOTH (free1.model3.json).",
            "image": "assets/model-maidcat.webp",
            "links": [
              {
                "label": "BOOTH · comte19 (#5047582)",
                "url": "https://booth.pm/en/items/5047582"
              }
            ]
          }
        ]
      },
      {
        "id": "plataformas",
        "title": "Plataformas e integrações",
        "summary": "Como YouTube, TikTok, Twitch, Minecraft e Discord se relacionam com a Célia.",
        "kicker": "Integrações e publicação",
        "group": "release",
        "sections": [
          {
            "title": "TikTok, YouTube e Twitch",
            "text": "O TikTok tem integração de leitura de chat e caminho de apresentação 3D no escopo do projeto. O YouTube reúne as publicações da Célia e há trabalho técnico registrado para conteúdo dessa plataforma. O guia de Twitch será vinculado quando confirmado."
          },
          {
            "title": "Minecraft em repositório independente",
            "text": "O projeto relacionado de Minecraft trata da presença da Célia no jogo e das interfaces com um agente de ação. A distribuição do bot será feita em repositório público separado. A documentação principal apontará para ele.",
            "links": [
              {
                "label": "Repositório do bot de Minecraft"
              }
            ]
          },
          {
            "title": "Discord via Kiwify",
            "text": "O bot de Discord é um produto separado, previsto para venda na Kiwify. O destino comercial será incluído aqui após divulgação.",
            "links": [
              {
                "label": "Bot de Discord na Kiwify"
              }
            ]
          },
          {
            "title": "Trava de segurança do modo Live (live_mode/gate.py)",
            "text": "O leitor do chat da live do TikTok nunca conecta automaticamente ao iniciar a Célia; ele passa pelo gate de autorização explícita em live_mode.",
            "language": "python",
            "code": "# src/celia/server.py + src/celia/live_mode/gate.py\nfrom .live_mode import gate as live_gate\nfrom .live_mode.routes import init_live_routes\nfrom .live_output_routes import init_live_output_routes\n\n# Somente quando o dono confirma que está ao vivo e envia a sala:\n# live_gate libera a conexão do leitor de eventos para a cena Godot 3D / OBS."
          },
          {
            "title": "TikTok, YouTube, Twitch e React v2 (repositório principal)",
            "text": "As integrações de live e mídia da Célia — TikTok, YouTube, Twitch e o sistema de React v2 — vivem no repositório principal. No React v2 (src/celia/react_routes.py), a Célia baixa e segmenta vídeos com SegmentWorker e reage aos trechos sincronizando fala e emoções.",
            "language": "python",
            "code": "# src/celia/react_routes.py — Módulo de React v2\nfrom fastapi import APIRouter\nfrom .react.store import ReactStore\nfrom .react.worker import SegmentWorker, download_youtube, plan_segments\n\n# Carrega config/react_v2.yaml, segmenta o vídeo do YouTube e\n# agenda as reações da Célia por trecho com controle de estado em ReactStore."
          },
          {
            "title": "Minecraft, Super Nintendo/Jogos (repos separados) e Discord (Kiwify)",
            "text": "Cada ecossistema de jogo permanece desacoplado: o bot de Minecraft e o bot de jogos/emuladores (Super Nintendo e demais jogos) terão repositórios públicos próprios. Já o bot de Discord da Célia é um produto à parte, com distribuição prevista via Kiwify."
          }
        ]
      },
      {
        "id": "desenvolvimento",
        "title": "Guia de desenvolvimento",
        "summary": "Localize o componente certo e comprove a alteração pela camada afetada.",
        "group": "release",
        "kicker": "Integrações e publicação",
        "sections": [
          {
            "title": "Organize uma alteração",
            "text": "Identifique o produtor do evento, o contrato e o consumidor. Para voz, siga síntese, entrega e reprodução. Para movimento, teste primeiro uma pose na bancada. Para eventos da cena 3D, reproduza no simulador."
          },
          {
            "title": "Validação local",
            "text": "Registre em qual versão e superfície o teste ocorreu. Uma aplicação sem chat ou sem credenciais pode testar UI e render; integração e reprodução em transmissão exigem evidência própria."
          },
          {
            "title": "Execução local e testes sem uv sync",
            "text": "No ambiente local da Célia o ambiente virtual já está pronto; nunca execute uv sync. Use o Python da .venv diretamente para iniciar e rodar testes focalizados.",
            "language": "powershell",
            "code": "# Iniciar o servidor local da Célia (porta 12393)\ncd celia-vtuber\n.\\.venv\\Scripts\\python.exe run_server.py\n\n# Rodar testes focalizados de Live2D e controles de corpo\n.\\.venv\\Scripts\\pytest.exe tests/test_live2d_body_controls.py tests/test_live2d_performance_guards.py"
          }
        ],
        "links": [
          {
            "label": "Referência do template de documentação (MIT)",
            "url": "https://github.com/Open-LLM-VTuber/open-llm-vtuber.github.io"
          }
        ]
      },
      {
        "id": "api",
        "title": "Referência de API",
        "summary": "Rotas da interface na instalação local de referência.",
        "group": "release",
        "kicker": "Integrações e publicação",
        "sections": [
          {
            "title": "Ponte do Corpo e do Pet",
            "text": "A API local expõe a escolha do Corpo e o modo solicitado. GET /bridge/body pode devolver corpo como null antes da escolha. GET /bridge/mode informa o último modo selecionado. Os dois handlers exigem loopback.",
            "language": "bash",
            "code": "curl http://127.0.0.1:12393/bridge/body\ncurl http://127.0.0.1:12393/bridge/mode"
          },
          {
            "title": "Eventos do cliente",
            "text": "O WebSocket /client-ws é usado pelas superfícies de apresentação. Para contribuir, confira src/celia/routes.py e o consumidor do evento antes de mudar forma ou sequência das mensagens."
          }
        ]
      },
      {
        "id": "diagnostico",
        "title": "Diagnóstico por componente",
        "summary": "Passos de inspeção para localizar em que estágio o fluxo para.",
        "kicker": "Integrações e publicação",
        "group": "release",
        "sections": [
          {
            "title": "Conversa sem resposta",
            "text": "Comece com uma mensagem de texto simples. Confirme que ela chega ao cliente e ao agente; depois confira contexto, rota de provedor e retorno da resposta. Se só o microfone falhar, inspecione captura, detecção de voz e ASR antes de mexer no modelo."
          },
          {
            "title": "Resposta sem voz ou movimento",
            "text": "Acompanhe separadamente síntese, entrega, início e fim da reprodução. Se o áudio toca e o avatar não reage, examine eventos, sincronização labial e mapeamento de expressões. No Pet, confirme a superfície de legenda embutida."
          },
          {
            "title": "Cena 3D e integrações",
            "text": "Teste o cenário com um evento simulado. Observe barramento, fila do diretor, animação do personagem e HUD antes de conectar fonte real. Compare o rig e a bancada quando uma pose específica falhar.",
            "links": [
              {
                "label": "Arquitetura e pontos de entrada",
                "page": "arquitetura"
              },
              {
                "label": "Cena Godot",
                "page": "mundo-3d"
              }
            ]
          },
          {
            "title": "Verificação rápida de saúde dos serviços locais",
            "text": "Comandos diretos para conferir se a Célia (12393), o OmniVoice e a GPU (RTX 3060 12 GB) estão respondendo normalmente.",
            "language": "powershell",
            "code": "# 1. Conferir se a Célia responde na porta 12393\nInvoke-WebRequest -Uri \"http://127.0.0.1:12393/\" -UseBasicParsing | Select-Object StatusCode\n\n# 2. Verificar portas abertas e uso de VRAM na GPU\nGet-NetTCPConnection -LocalPort 12393 -ErrorAction SilentlyContinue\nnvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv"
          }
        ]
      },
      {
        "id": "faq",
        "title": "Perguntas frequentes",
        "summary": "Onde procurar quando a Célia fala sem mover a boca ou o modelo 3D não abre.",
        "group": "release",
        "kicker": "Integrações e publicação",
        "sections": [
          {
            "title": "Sai texto, mas não sai som",
            "text": "Confira geração do TTS, entrega ao cliente e dispositivo de saída. Ver o texto ou obter um WAV no disco são etapas que antecedem a reprodução audível."
          },
          {
            "title": "O Pet abre, mas não vejo legenda",
            "text": "O Pet usa a legenda embutida. Confira a mesma superfície e seu fallback antes de buscar um problema na janela de desktop."
          },
          {
            "title": "O 3D não abre em uma instalação nova",
            "text": "Confira se o VRM está no caminho esperado, se a bancada carrega e se a versão publicada inclui os assets. No Godot, verifique a cena e os recursos do módulo 3D separadamente."
          }
        ]
      },
      {
        "id": "links",
        "title": "Links e distribuição",
        "summary": "Destinos oficiais e espaços reservados ao lançamento público.",
        "kicker": "Integrações e publicação",
        "group": "release",
        "sections": [
          {
            "title": "Recursos e versão",
            "text": "Acompanhe o canal da Célia para anúncios. O código, o guia de instalação, a licença de distribuição e os links dos módulos separados serão acrescentados à medida que forem divulgados."
          },
          {
            "title": "Ativos e dependências",
            "text": "As condições de distribuição de modelos, texturas, animações, vozes e dependências precisam ser verificadas por artefato. A publicação do código não implica a distribuição de todos os assets utilizados na instalação de referência."
          },
          {
            "title": "Créditos e projetos base",
            "text": "Reconhecimento explícito à base Open-LLM-VTuber / OpenLive2D, que inspirou e estruturou a arquitetura inicial da Célia antes das extensões proprietárias de voz, emoções, React e 3D.",
            "links": [
              {
                "label": "Open-LLM-VTuber (repositório base)",
                "url": "https://github.com/Open-LLM-VTuber/Open-LLM-VTuber"
              },
              {
                "label": "Documentação de referência Open-LLM-VTuber",
                "url": "https://github.com/Open-LLM-VTuber/open-llm-vtuber.github.io"
              },
              {
                "label": "Kiwify (plataforma de distribuição do bot Discord)",
                "url": "https://ajuda.kiwify.com.br/pt-br/article/o-que-e-a-kiwify-como-funciona-un717r/"
              }
            ]
          }
        ],
        "links": [
          {
            "label": "Canal da Célia no YouTube",
            "url": "https://www.youtube.com/channel/UCAkPtvGuZ9XpwRZHZt8BbnQ"
          },
          {
            "label": "Repositório da Célia"
          },
          {
            "label": "Guia de instalação do release"
          },
          {
            "label": "Repositório do bot de Minecraft"
          },
          {
            "label": "Bot de Discord na Kiwify"
          },
          {
            "label": "Guia da Twitch"
          }
        ]
      }
    ],
    "cast": [],
    "home": {
      "title": "Célia",
      "tagline": "Documentação do projeto de VTuber com IA.",
      "support": "Conversa, voz e avatar em 2D, no desktop e em cenas 3D.",
      "cta": "Começar",
      "docs": "Documentação",
      "models": "Modelos",
      "image": "assets/celia-mark.webp",
      "features": [
        {
          "page": "arquitetura",
          "title": "Arquitetura",
          "text": "Da entrada ao cliente, com roteamento de modelos e estado de conversa.",
          "image": "assets/feature-arquitetura.webp"
        },
        {
          "page": "voz",
          "title": "Voz e TTS",
          "text": "O histórico de Piper, Edge TTS, XTTS e OmniVoice, com a voz em treinamento.",
          "image": "assets/feature-voz.webp"
        },
        {
          "page": "corpo",
          "title": "Corpo e avatares",
          "text": "Live2D, VRM e o caminho de eventos que aciona expressão e movimento.",
          "image": "assets/feature-corpo.webp"
        },
        {
          "page": "pet",
          "title": "Modo Pet",
          "text": "Janela Electron, controle de interação e uma superfície de legenda.",
          "image": "assets/feature-pet.webp"
        },
        {
          "page": "mundo-3d",
          "title": "Mundo 3D",
          "text": "Godot, cenário, Bento, Kenny e o módulo de TikTok 3D.",
          "image": "assets/feature-mundo-3d.webp"
        },
        {
          "page": "plataformas",
          "title": "Integrações",
          "text": "YouTube, TikTok e as distribuições de Minecraft e Discord.",
          "image": "assets/feature-plataformas.webp"
        }
      ]
    }
  },
  "en": {
    "ui": {
      "label": "Documentation",
      "brand": "Célia",
      "search": "Search",
      "searchPlaceholder": "Search",
      "noResults": "No results",
      "theme": "Theme",
      "light": "Light",
      "dark": "Dark",
      "language": "Language",
      "menu": "Menu",
      "onPage": "On this page",
      "skip": "Skip to content",
      "home": "Home",
      "previous": "Previous",
      "next": "Next",
      "comingSoon": "Coming soon",
      "groups": {
        "start": "Getting started",
        "core": "Architecture and voice",
        "body": "Body and 3D world",
        "release": "Integrations and release"
      },
      "routeIndex": "Technical guides",
      "models": "Models"
    },
    "pages": [
      {
        "id": "inicio",
        "title": "Célia documentation",
        "summary": "Architecture, operation, and release boundaries for the AI VTuber project.",
        "kicker": "Getting started",
        "group": "start",
        "sections": [
          {
            "title": "What the project includes",
            "text": "Célia combines a conversational runtime, speech, a Live2D view, a desktop Pet, a browser-based VRM Body, and a separate Godot scene. The documentation follows a request through those presentation surfaces and explains their external inputs."
          },
          {
            "title": "Where to begin",
            "text": "Read Architecture and Conversation flow first. Choose Body, Pet, or VRM if you are working on presentation. World 3D and TikTok covers the Godot scene, Bento, and Kenny."
          },
          {
            "title": "December release",
            "text": "The public release is planned for December. The release scope describes which components are part of the main project and which have independent distribution."
          }
        ]
      },
      {
        "id": "comecar",
        "title": "Getting started",
        "summary": "Run a first conversation in a prepared reference environment.",
        "group": "start",
        "kicker": "Getting started",
        "sections": [
          {
            "title": "Requirements",
            "text": "V02 declares Python 3.10–3.12 in pyproject.toml. Electron and Godot are additional components for desktop and 3D. The December release will define the installable package and supported environments.",
            "code": "python --version",
            "language": "bash"
          },
          {
            "title": "First local check",
            "text": "The Linux reference launcher bin/ligar.sh calls run_celia_linux.py; run_server.py is retained as the upstream entry point. Use an already prepared environment and start with text before trying microphone input."
          },
          {
            "title": "Acceptance sequence",
            "text": "Confirm receipt of a message, the answer, audible playback, and avatar reaction. Check microphone, Pet, browser VRM and Godot separately. Use simulated events for the 3D scene first."
          }
        ]
      },
      {
        "id": "lancamento",
        "title": "Release scope",
        "summary": "Main project and separately distributed modules.",
        "kicker": "Getting started",
        "group": "start",
        "sections": [
          {
            "title": "Main project",
            "text": "The planned scope covers conversation, voice, Live2D, Pet, browser Body/VRM, and World 3D. TikTok 3D is part of that scope. The final release will specify the installable package and environment requirements."
          },
          {
            "title": "Separate distribution",
            "text": "The Minecraft bot will have its own public repository. The Discord bot is a separate commercial product planned for Kiwify. This documentation will link both destinations as they become available."
          },
          {
            "title": "Release checklist",
            "text": "The public repository, license, install guide, supported configurations and distributable assets need to be confirmed in the release."
          }
        ]
      },
      {
        "id": "arquitetura",
        "title": "Architecture",
        "summary": "Inputs, conversation, synthesis, and presentation clients.",
        "kicker": "Architecture and voice",
        "group": "core",
        "sections": [
          {
            "title": "A shared core",
            "text": "The FastAPI backend hosts the conversation flow and serves the web client. The agent assembles context and requests a response from a selected provider. WebSocket carries client events; audio, captions, and reactions have distinct presentation paths.",
            "code": "Text or microphone → transcription (audio)\n  → context + conversation model\n  → response / emotion events\n  → speech synthesis + presentation"
          },
          {
            "title": "Code map",
            "text": "The directories below describe the reference implementation. Confirm the final layout against the public release.",
            "items": [
              "src/celia/core/ · orchestration and emotion events",
              "src/celia/tts/ · speech synthesis providers",
              "frontend/ · browser client and Body",
              "tools/pet-electron/ · desktop Pet window",
              "integrations/live3d/ · reference Godot scene"
            ]
          },
          {
            "title": "Runtime boundaries",
            "text": "Desktop Pet reuses the Célia client. The Godot scene has a separate event source and can run as a local simulation. The existence of a provider or adapter in the tree does not demonstrate a live connection."
          }
        ]
      },
      {
        "id": "fluxo",
        "title": "Conversation flow",
        "summary": "How input becomes an answer, speech, and visual state.",
        "kicker": "Architecture and voice",
        "group": "core",
        "sections": [
          {
            "title": "Input and turn handling",
            "text": "Text enters the conversation directly. Microphone input is segmented by voice activity detection and transcribed by ASR. A documented configuration can use local speech recognition as an alternative."
          },
          {
            "title": "Context and 9router",
            "text": "The agent gathers context and conversational memory. 9router is an OpenAI-compatible gateway; deployments can choose different providers and fallbacks for different roles. Grok and other APIs are routing options. Local Spell is a candidate under evaluation."
          },
          {
            "title": "Speech and reactions",
            "text": "The interaction mode determines when to wait, speak, or emit a short reaction. TTS renders the answer, and the client handles playback, captions, lip sync, and supported visual states."
          }
        ]
      },
      {
        "id": "configuracao",
        "title": "Configuration and providers",
        "summary": "Configure speech recognition, conversation and voice independently.",
        "group": "core",
        "kicker": "Architecture and voice",
        "sections": [
          {
            "title": "Selected settings",
            "text": "conf.yaml selects providers. The reference uses OpenAI-compatible ASR and OmniVoice. Keep credentials in the runtime environment and use examples in public documentation.",
            "code": "# Selected fields in the reference setup\nasr_model: openai_compatible_asr\ntts_model: omnivoice",
            "language": "yaml"
          },
          {
            "title": "Local and hosted routes",
            "text": "9router is the documented gateway. Verify the selected source, its alternatives, and the runtime selection before claiming a route is active."
          }
        ]
      },
      {
        "id": "voz",
        "title": "Voice and TTS history",
        "summary": "Piper, Edge TTS, XTTS, OmniVoice, and training in progress.",
        "kicker": "Architecture and voice",
        "group": "core",
        "sections": [
          {
            "title": "Piper and Edge TTS",
            "text": "Early versions used Piper and Edge TTS. The project also ran a Portuguese fine-tuned Piper line, including the V9-D milestone. Engine and profile selection varied across configurations."
          },
          {
            "title": "XTTS and OmniVoice",
            "text": "XTTS was used for vocal adaptation and experiments; its retirement from the main narration path is documented. The documented speech path uses OmniVoice. Célia Oficial V2 is the approved reference in Central IA for new lines."
          },
          {
            "title": "Custom voice training",
            "text": "The creator is training a custom voice. The resulting profile and any distributable files are pending validation. The project prioritizes Brazilian Portuguese; the five documentation languages do not imply five speech profiles."
          },
          {
            "title": "Compare speech versions",
            "text": "Use the same sample phrases and check proper names, numbers, timing and audible playback. Record the engine, reference profile, and result separately."
          }
        ]
      },
      {
        "id": "corpo",
        "title": "Body and emotions",
        "summary": "The web surface for session controls and avatar presentation.",
        "kicker": "Body and 3D world",
        "group": "body",
        "sections": [
          {
            "title": "Browser surfaces",
            "text": "Body groups conversation and controls with presentation. Live2D has its own canvas. The browser 3D view uses a VRM and a Body controller. Pet uses the presentation surface in a desktop window."
          },
          {
            "title": "Emotion events and motion",
            "text": "The runtime exposes an emotion event channel. Body can map events to supported expressions, pose profiles and short actions. Mouth movement is synchronized with speech independently of body pose."
          },
          {
            "title": "Isolate a rig issue",
            "text": "Try a known pose in the bench before adding audio or integrations. Check whether the problem follows an animation or an event; review the rig and model weights for a deformation that also occurs in isolation."
          },
          {
            "title": "Code excerpts · src/celia/core/emotion_bus.py",
            "text": "Excerpt from the reference implementation; file names are relative to the project root.",
            "code": "@dataclass(frozen=True)\nclass EventoEmocao:\n    \"\"\"Um pedido de emoção, já normalizado.\"\"\"\n\n    emocao: str\n    origem: str\n    prioridade: int\n    ttl_s: float | None\n    trava_s: float\n    em: float  # time.monotonic() de quando entrou\n    persistente: bool = False\n    metadados: dict[str, Any] = field(default_factory=dict)",
            "language": "python"
          }
        ]
      },
      {
        "id": "pet",
        "title": "Desktop Pet",
        "summary": "A transparent desktop surface that reuses the client.",
        "kicker": "Body and 3D world",
        "group": "body",
        "sections": [
          {
            "title": "Audio isolation — safety contract",
            "text": "Pet and other presentation surfaces must never open a physical microphone. Mascot animation reuses RMS from existing audio. Only one authorized source feeds ASR; mute and source switching affect app-owned capture only. Never change global microphone settings or system audio services. This is a release acceptance requirement, not a certification of the current public package."
          },
          {
            "title": "Electron shell",
            "text": "The launcher opens an Electron window with the Célia client in stage mode. The main runtime still hosts the conversation. A second launch can bring the existing Pet to focus."
          },
          {
            "title": "Window behavior",
            "text": "Click handling and the drag gesture are owned by the desktop wrapper. Session settings and chat remain in the main Body interface."
          },
          {
            "title": "Single caption surface",
            "text": "The current Pet contract uses frontend/celia-legenda-embutida.js for one embedded caption surface and its fallback. Confirm that one utterance produces a single visible caption."
          },
          {
            "title": "Code entry points",
            "text": "frontend/celia-pet.js and celia-browser-pet.js connect the Pet mode to the client. tools/pet-electron/main.js manages its window. tools/run_pet.ps1 is a Windows launcher."
          },
          {
            "title": "Code excerpts · tools/pet-electron/main.js",
            "text": "Excerpt from the reference implementation; file names are relative to the project root.",
            "code": "minHeight: 360,\n    frame: false,\n    transparent: true,\n    backgroundColor: \"#00000000\",\n    hasShadow: false,\n    resizable: false,\n    alwaysOnTop: true,",
            "language": "javascript"
          }
        ]
      },
      {
        "id": "vrm",
        "title": "VRM in Body",
        "summary": "How the browser uses a humanoid model, poses, and corrections.",
        "kicker": "Body and 3D world",
        "group": "body",
        "sections": [
          {
            "title": "Browser path",
            "text": "The reference application loads Célia’s VRM in a browser 3D view. frontend/celia-3d-corpo.js defines pose profiles, gestures, and corrective behavior. The bench helps inspect each state before connecting it to an event."
          },
          {
            "title": "Model versus runtime",
            "text": "The reference rig records arm and shoulder weights and corrective shapes. The application can drive those shapes from arm angle. Hair, twist, hands, and skirt adjustments may vary by revision."
          },
          {
            "title": "Migration and distribution",
            "text": "The V02 code and the original installation are separate. Check that the default asset exists in the target package, then verify bone mapping, blink, mouth and arm motion. Confirm asset licenses before redistribution."
          },
          {
            "title": "V02 package check",
            "text": "The 2D/3D selector opens the VRM bench, which expects frontend/modelos-3d/celia_v3_web.vrm. That exact asset path is missing in the reviewed V02 tree; the official model is present in the original installation. Resolve that packaging gap before claiming the browser VRM works in a fresh install."
          },
          {
            "title": "Code excerpts · frontend/celia-3d-interface.js",
            "text": "Excerpt from the reference implementation; file names are relative to the project root.",
            "code": "const quadro = document.createElement(\"iframe\");\n  quadro.id = \"celia-3d-quadro\"; quadro.title = \"Célia 3D\"; quadro.setAttribute(\"aria-hidden\", \"true\"); quadro.tabIndex = -1;\n  const params3d = new URLSearchParams(location.search);",
            "language": "javascript"
          }
        ]
      },
      {
        "id": "mundo-3d",
        "title": "World 3D and TikTok",
        "summary": "Godot, the scene director, Bento, Kenny, and event input.",
        "kicker": "Body and 3D world",
        "group": "body",
        "sections": [
          {
            "title": "Scene assembly",
            "text": "The reference Godot scene loads Célia in VRM, Bento and Kenny in GLB, the world, camera, HUD, and director. A simulator emits chat, gifts, likes, follows and speech events to the bus.",
            "code": "Event source → bus → director and queues\n  → Célia / Bento / Kenny / world / HUD"
          },
          {
            "title": "Companion controllers",
            "text": "Bento can walk or join a dance. The retarget system owns the body pose during a performance so it does not conflict with procedural motion. Kenny has a separate animation controller; a simulated Rose triggers the food-bowl action."
          },
          {
            "title": "World and broadcast path",
            "text": "The scene pipeline configures lighting, sky, grass, props, cameras, and HUD rules separately. The TikTok reader and Godot renderer have different roles. The event bridge exists in code; live use still requires explicit setup and end-to-end validation."
          },
          {
            "title": "Godot module map",
            "text": "In the reference installation, integrations/live3d/godot/cenas/main.tscn defines the scene. scripts/main.gd assembles the components, mundo.gd configures the world, diretor.gd manages events, and scripts/danca/ owns retargeting and performances. Settings and the dance catalog are in config/live3d.json and config/dancas.json."
          },
          {
            "title": "Code excerpts · godot/project.godot",
            "text": "Excerpt from the reference implementation; file names are relative to the project root.",
            "code": "[application]\n\nconfig/name=\"Celia Live 3D\"\nrun/main_scene=\"res://cenas/main.tscn\"\nconfig/features=PackedStringArray(\"4.7\", \"Forward Plus\")\n\n[autoload]\n\nBarramento=\"*res://scripts/barramento.gd\"",
            "language": "ini"
          }
        ]
      },
      {
        "id": "modelos",
        "title": "Models and avatars",
        "summary": "Model sources, provenance and compatibility.",
        "group": "body",
        "kicker": "Body and 3D world",
        "sections": [
          {
            "title": "Live2D and VRM",
            "text": "Live2D relies on Cubism resources, typically .model3.json, .moc3, textures and configuration. The browser and Godot import VRM or GLB depending on the surface and validate their assets independently."
          },
          {
            "title": "Model provenance",
            "text": "Use the creator source where it can be verified. Check the terms for display, use and redistribution before including third-party assets."
          }
        ],
        "models": [
          {
            "name": "Pachan 2.0 · Half-Body (medwokl2d)",
            "description": "Modelo Live2D Half-Body gratuito para VTube Studio criado por medwokl2d no BOOTH. Integrado em web/live2d-models/elizabeth_vts (pachirisu anime girl - top half.model3.json).",
            "image": "assets/model-pachan.webp",
            "links": [
              {
                "label": "BOOTH · medwokl2d (#4711410)",
                "url": "https://booth.pm/en/items/4711410"
              }
            ]
          },
          {
            "name": "VT Student · Raiden Shogun",
            "description": "Modelo Live2D VT_Student (ChoDenPA / Milkydoragon). Integrado em web/live2d-models/shogun_vts (VT_student.model3.json).",
            "image": "assets/model-shogun.webp",
            "links": [
              {
                "label": "Ver referência no Gumroad",
                "url": "https://milkydoragon.gumroad.com/l/avexk"
              }
            ]
          },
          {
            "name": "Hiyori Momo · Live2D Sample",
            "description": "Modelo clássico de referência Cubism (usado como base histórica de VTubers IA como Neuro-sama). Integrado em web/live2d-models/hiyori_vts (hiyori.model3.json).",
            "image": "assets/model-hiyori.webp",
            "links": [
              {
                "label": "Live2D Cubism Sample Models",
                "url": "https://www.live2d.com/en/learn/sample/"
              }
            ]
          },
          {
            "name": "March 7th · 三月七",
            "description": "Modelo Live2D gratuito de March 7th (Honkai: Star Rail) por Design.genius no BOOTH. Integrado em web/live2d-models/march_7th_vts (march 7th.model3.json).",
            "image": "assets/model-march7th.webp",
            "links": [
              {
                "label": "BOOTH · Design.genius (#4961342)",
                "url": "https://booth.pm/en/items/4961342"
              }
            ]
          },
          {
            "name": "Huohuo · 藿藿",
            "description": "Modelo Live2D gratuito de Huohuo por bailyovo no BOOTH, com expressões e física completa. Integrado em web/live2d-models/huohuo_vts (huohuo.model3.json).",
            "image": "assets/model-huohuo.webp",
            "links": [
              {
                "label": "BOOTH · bailyovo (#5288339)",
                "url": "https://booth.pm/en/items/5288339"
              }
            ]
          },
          {
            "name": "Sumire · 女子 モデル名:sumire",
            "description": "Modelo Live2D genérico gratuito de alta fidelidade por creape no BOOTH (sumire_free_001.model3.json).",
            "image": "assets/model-sumire.webp",
            "links": [
              {
                "label": "BOOTH · creape (#5002899)",
                "url": "https://booth.pm/en/items/5002899"
              }
            ]
          },
          {
            "name": "Hoshino Nya",
            "description": "Modelo Live2D gratuito por skiyoshi no BOOTH com texturas 4K e expressões. Integrado em web/live2d-models/hoshinonya_vts (hoshinonya_skiyoshi.model3.json).",
            "image": "assets/model-hoshinonya.webp",
            "links": [
              {
                "label": "BOOTH · skiyoshi (#3988142)",
                "url": "https://booth.pm/en/items/3988142"
              }
            ]
          },
          {
            "name": "Maid Cat Girl · 猫娘",
            "description": "Modelo Live2D de garota gato maid por comte19 no BOOTH (free1.model3.json).",
            "image": "assets/model-maidcat.webp",
            "links": [
              {
                "label": "BOOTH · comte19 (#5047582)",
                "url": "https://booth.pm/en/items/5047582"
              }
            ]
          }
        ]
      },
      {
        "id": "plataformas",
        "title": "Platforms and integrations",
        "summary": "How other services and modules relate to the core project.",
        "kicker": "Integrations and release",
        "group": "release",
        "sections": [
          {
            "title": "TikTok, YouTube, Twitch",
            "text": "TikTok chat and the 3D mode are in scope for the main project. YouTube hosts Célia’s videos and technical work on YouTube content is documented. A Twitch guide link is reserved."
          },
          {
            "title": "Minecraft repository",
            "text": "Minecraft has its own action agent and integration. The bot is planned as a separate public repository linked from this site.",
            "links": [
              {
                "label": "Minecraft bot repository"
              }
            ]
          },
          {
            "title": "Discord on Kiwify",
            "text": "The Discord bot is a separate commercial product. Its Kiwify purchase page will be linked when announced.",
            "links": [
              {
                "label": "Discord bot on Kiwify"
              }
            ]
          }
        ]
      },
      {
        "id": "desenvolvimento",
        "title": "Developer guide",
        "summary": "Find the producer, contract and consumer of a change.",
        "group": "release",
        "kicker": "Integrations and release",
        "sections": [
          {
            "title": "Trace a change",
            "text": "For speech, follow synthesis, delivery, and playback. For poses, verify the model in the bench. For Godot events, reproduce with the simulator first."
          },
          {
            "title": "Local verification",
            "text": "Record the build and the surface tested. Isolated rendering and a live integration require different evidence."
          }
        ],
        "links": [
          {
            "label": "Documentation template reference (MIT)",
            "url": "https://github.com/Open-LLM-VTuber/open-llm-vtuber.github.io"
          }
        ]
      },
      {
        "id": "api",
        "title": "API reference",
        "summary": "Interface routes in the local reference installation.",
        "group": "release",
        "kicker": "Integrations and release",
        "sections": [
          {
            "title": "Body and Pet bridge",
            "text": "The local API exposes the selected Body and requested window mode. GET /bridge/body can return null before a choice; GET /bridge/mode reports the most recent selection. Both handlers require loopback.",
            "language": "bash",
            "code": "curl http://127.0.0.1:12393/bridge/body\ncurl http://127.0.0.1:12393/bridge/mode"
          },
          {
            "title": "Client events",
            "text": "Presentation surfaces use the /client-ws WebSocket. Review src/celia/routes.py and the event consumer before changing the shape or sequence of messages."
          }
        ]
      },
      {
        "id": "diagnostico",
        "title": "Troubleshooting",
        "summary": "Find the stage where the flow stops.",
        "kicker": "Integrations and release",
        "group": "release",
        "sections": [
          {
            "title": "Input and answer",
            "text": "Start with a simple text message. Observe receipt, context assembly, provider routing and response. If failure is limited to microphone, check capture, VAD, and ASR first."
          },
          {
            "title": "Voice and avatar",
            "text": "Check synthesis, delivery, and audible playback independently. If audio works but motion fails, inspect event mapping and lip sync. In Pet, check the embedded caption owner."
          },
          {
            "title": "3D and integration",
            "text": "Run a simulated event before connecting live input. Verify the bus, director queue, animation, and HUD. For a single broken pose, check the model in the bench."
          }
        ]
      },
      {
        "id": "faq",
        "title": "FAQ",
        "summary": "Common voice, Pet, and VRM troubleshooting.",
        "group": "release",
        "kicker": "Integrations and release",
        "sections": [
          {
            "title": "Text appears but there is no sound",
            "text": "Check TTS output, delivery and audible client playback in that order."
          },
          {
            "title": "Pet caption is missing",
            "text": "Inspect the embedded caption surface and its fallback."
          },
          {
            "title": "The VRM does not load",
            "text": "Check distribution contents, model path and browser bench. Check Godot resources separately."
          }
        ]
      },
      {
        "id": "links",
        "title": "Links and distribution",
        "summary": "Official destinations and placeholders for the public release.",
        "kicker": "Integrations and release",
        "group": "release",
        "sections": [
          {
            "title": "Official resources",
            "text": "Use the channel for announcements. The public repository, release installation guide and external project links will be added when ready."
          },
          {
            "title": "Assets and dependencies",
            "text": "Check redistribution conditions for each rig, texture, animation, model, voice and dependency. Release documentation will identify what ships with the repository."
          }
        ],
        "links": [
          {
            "label": "Célia on YouTube",
            "url": "https://www.youtube.com/channel/UCAkPtvGuZ9XpwRZHZt8BbnQ"
          },
          {
            "label": "Célia repository"
          },
          {
            "label": "Release installation guide"
          },
          {
            "label": "Minecraft bot repository"
          },
          {
            "label": "Discord bot on Kiwify"
          },
          {
            "label": "Twitch guide"
          }
        ]
      }
    ],
    "cast": [],
    "home": {
      "title": "Célia",
      "tagline": "Documentation for the AI VTuber project.",
      "support": "Dialogue, voice and avatars across 2D, desktop and 3D scenes.",
      "cta": "Get started",
      "docs": "Documentation",
      "models": "Models",
      "image": "assets/celia-mark.webp",
      "features": [
        {
          "page": "arquitetura",
          "title": "Architecture",
          "text": "Input, model routing, state, and presentation clients.",
          "image": "assets/feature-arquitetura.webp"
        },
        {
          "page": "voz",
          "title": "Voice and TTS",
          "text": "Piper, Edge TTS, XTTS, OmniVoice, and voice training.",
          "image": "assets/feature-voz.webp"
        },
        {
          "page": "corpo",
          "title": "Body and avatars",
          "text": "Live2D, VRM, motion and expression events.",
          "image": "assets/feature-corpo.webp"
        },
        {
          "page": "pet",
          "title": "Desktop Pet",
          "text": "The Electron window, input handling, and embedded captions.",
          "image": "assets/feature-pet.webp"
        },
        {
          "page": "mundo-3d",
          "title": "3D world",
          "text": "The Godot scene, Bento, Kenny and TikTok 3D.",
          "image": "assets/feature-mundo-3d.webp"
        },
        {
          "page": "plataformas",
          "title": "Integrations",
          "text": "YouTube, TikTok, and separate Minecraft and Discord distributions.",
          "image": "assets/feature-plataformas.webp"
        }
      ]
    }
  },
  "ja": {
    "ui": {
      "label": "ドキュメント",
      "brand": "Célia",
      "search": "検索",
      "searchPlaceholder": "検索",
      "noResults": "検索結果がありません",
      "theme": "テーマ",
      "light": "ライト",
      "dark": "ダーク",
      "language": "言語",
      "menu": "メニュー",
      "onPage": "このページの内容",
      "skip": "本文へ移動",
      "home": "ホーム",
      "previous": "前へ",
      "next": "次へ",
      "comingSoon": "準備中",
      "groups": {
        "start": "はじめに",
        "core": "アーキテクチャと音声",
        "body": "Body と 3D ワールド",
        "release": "連携と公開"
      },
      "routeIndex": "技術ガイド",
      "models": "モデル"
    },
    "pages": [
      {
        "id": "inicio",
        "title": "Célia 技術ドキュメント",
        "summary": "AI VTuber プロジェクトの構成、運用、公開範囲。",
        "kicker": "はじめに",
        "group": "start",
        "sections": [
          {
            "title": "プロジェクトの構成",
            "text": "Célia は対話ランタイム、音声、Live2D、デスクトップ Pet、ブラウザー上の VRM Body、Godot の 3D シーンで構成されます。入力から各表示先までの流れを説明します。"
          },
          {
            "title": "読み始めるには",
            "text": "まずアーキテクチャと対話フローを確認してください。表示を扱う場合は Body、Pet、VRM を参照し、Godot の場面とキャラクターについては 3D ワールドを参照してください。"
          },
          {
            "title": "12月の公開予定",
            "text": "メインプロジェクトと個別に配布する機能の範囲は、公開計画にまとめています。"
          }
        ]
      },
      {
        "id": "comecar",
        "title": "はじめる",
        "summary": "準備済みの環境で最初の対話を確認します。",
        "group": "start",
        "kicker": "はじめに",
        "sections": [
          {
            "title": "要件",
            "text": "V02 の pyproject.toml は Python 3.10–3.12 を指定します。デスクトップと 3D には Electron と Godot が使われます。正式な配布環境はリリースで確認してください。",
            "code": "python --version",
            "language": "bash"
          },
          {
            "title": "初回確認",
            "text": "Linux の参考ランチャー bin/ligar.sh は run_celia_linux.py を起動します。最初はテキスト、応答、音声再生の順に確認します。"
          }
        ]
      },
      {
        "id": "lancamento",
        "title": "公開範囲",
        "summary": "本体、独立リポジトリ、商用配布の区分。",
        "kicker": "はじめに",
        "group": "start",
        "sections": [
          {
            "title": "メインプロジェクト",
            "text": "対話、音声、Live2D、Pet、Body/VRM、3D ワールドを公開範囲に含める予定です。TikTok 3D もこの範囲です。配布物と動作要件は公開時に確定します。"
          },
          {
            "title": "個別配布",
            "text": "Minecraft ボットは独立した公開リポジトリで配布予定です。Discord ボットは Kiwify で販売予定の別製品です。公開後にリンクを掲載します。"
          },
          {
            "title": "公開時に確認する項目",
            "text": "ライセンス、導入手順、対応環境、再配布できる素材を公開版で確認してください。"
          }
        ]
      },
      {
        "id": "arquitetura",
        "title": "アーキテクチャ",
        "summary": "入力、対話、音声合成、表示クライアントの役割。",
        "kicker": "アーキテクチャと音声",
        "group": "core",
        "sections": [
          {
            "title": "共通の対話基盤",
            "text": "FastAPI バックエンドが対話を処理します。エージェントは文脈を組み立て、選択したプロバイダーに応答を要求します。WebSocket でイベントを送り、クライアントが音声、字幕、動きを扱います。",
            "code": "テキスト・マイク → 音声認識（音声のみ）\n  → 文脈と対話モデル\n  → 応答・感情イベント\n  → 音声合成と表示"
          },
          {
            "title": "参考実装の構成",
            "text": "配布版の配置はリリースで確認してください。",
            "items": [
              "src/celia/core/ · オーケストレーションと感情",
              "src/celia/tts/ · 音声合成",
              "frontend/ · Web と Body",
              "tools/pet-electron/ · デスクトップ Pet",
              "integrations/live3d/ · Godot シーン"
            ]
          },
          {
            "title": "処理の境界",
            "text": "Pet は既存クライアントを再利用します。Godot シーンは別のイベント入力を持ち、シミュレーションでも動作を確認できます。配信接続には別途設定が必要です。"
          }
        ]
      },
      {
        "id": "fluxo",
        "title": "対話フロー",
        "summary": "入力から応答、音声、視覚的な反応まで。",
        "kicker": "アーキテクチャと音声",
        "group": "core",
        "sections": [
          {
            "title": "入力と発話区間",
            "text": "テキストは直接エージェントに渡ります。マイクの音声は発話検出で区切り、ASR が文字に変換します。構成によってはローカル ASR も利用できます。"
          },
          {
            "title": "文脈と 9router",
            "text": "エージェントが会話の記憶と文脈を利用します。9router は OpenAI 互換のゲートウェイで、役割ごとに経路を設定できます。Grok などの API は候補です。ローカル Spell は評価候補です。"
          },
          {
            "title": "音声と反応",
            "text": "ターン制御が待機、発話、短い反応を選びます。TTS の音声を受け取ったクライアントは再生、口の動き、字幕、対応する表情を同期させます。"
          }
        ]
      },
      {
        "id": "configuracao",
        "title": "設定とプロバイダー",
        "summary": "ASR、対話、音声合成を設定します。",
        "group": "core",
        "kicker": "アーキテクチャと音声",
        "sections": [
          {
            "title": "設定項目",
            "text": "conf.yaml でプロバイダーを選択します。参考環境は OpenAI 互換 ASR と OmniVoice を使用します。",
            "code": "# 参考設定の抜粋\nasr_model: openai_compatible_asr\ntts_model: omnivoice",
            "language": "yaml"
          },
          {
            "title": "ルート",
            "text": "9router がモデルを振り分けます。有効な設定と予備ルートを確認し、認証情報は非公開環境に保持します。"
          }
        ]
      },
      {
        "id": "voz",
        "title": "音声と TTS の履歴",
        "summary": "Piper、Edge TTS、XTTS、OmniVoice、自分の声の学習。",
        "kicker": "アーキテクチャと音声",
        "group": "core",
        "sections": [
          {
            "title": "初期の Piper と Edge TTS",
            "text": "初期に Piper と Edge TTS を使用しました。ポルトガル語向けに微調整した Piper の履歴には V9-D 系列があります。"
          },
          {
            "title": "XTTS と OmniVoice",
            "text": "XTTS は声の調整や実験に使われ、主要な発話経路からの移行が記録されています。現在文書化されている経路は OmniVoice です。新しい台詞用の承認済み参照音声は Central IA の Célia Oficial V2 です。"
          },
          {
            "title": "自分の声の学習",
            "text": "制作者は自身の声を学習中です。公開するプロファイルやファイルは検証後に決まります。音声の重点はブラジルポルトガル語です。"
          },
          {
            "title": "比較方法",
            "text": "同じ台詞で固有名詞、数値、間、再生音を確認し、エンジンと参照音声を別々に記録します。"
          }
        ]
      },
      {
        "id": "corpo",
        "title": "Body と感情",
        "summary": "セッション操作とアバター表示。",
        "kicker": "Body と 3D ワールド",
        "group": "body",
        "sections": [
          {
            "title": "Web の表示面",
            "text": "Body にチャット、設定、アバター表示があります。Live2D は専用キャンバスを使い、3D 表示は VRM を使います。Pet はデスクトップで表示部分を再利用します。"
          },
          {
            "title": "感情と動き",
            "text": "感情イベントを対応する表情やポーズ、短い動作に結び付けます。口の動きは音声に同期し、体のポーズは別に制御できます。"
          },
          {
            "title": "切り分け",
            "text": "まずベンチで既知のポーズを確認します。単独でも変形する場合は骨格やウェイトを、イベント時だけ起きる場合は状態のマッピングを確認します。"
          },
          {
            "title": "コードの抜粋 · src/celia/core/emotion_bus.py",
            "text": "参考実装からの抜粋です。ファイル名はプロジェクトルートからの相対パスです。",
            "code": "@dataclass(frozen=True)\nclass EventoEmocao:\n    \"\"\"Um pedido de emoção, já normalizado.\"\"\"\n\n    emocao: str\n    origem: str\n    prioridade: int\n    ttl_s: float | None\n    trava_s: float\n    em: float  # time.monotonic() de quando entrou\n    persistente: bool = False\n    metadados: dict[str, Any] = field(default_factory=dict)",
            "language": "python"
          }
        ]
      },
      {
        "id": "pet",
        "title": "デスクトップ Pet",
        "summary": "Electron の透明ウィンドウで同じクライアントを使用。",
        "kicker": "Body と 3D ワールド",
        "group": "body",
        "sections": [
          {
            "title": "音声分離の安全要件",
            "text": "Pet と表示専用画面は物理マイクを開いてはなりません。マスコットの動きは既存音声の RMS を再利用します。ASR の入力は許可された一つの音源に限定し、ミュートと切替はアプリ自身の収音だけに適用します。OS のマイク設定や音声サービスは変更しません。これは公開版の検証要件であり、現行版の認証ではありません。"
          },
          {
            "title": "起動と操作",
            "text": "Electron のランチャーがステージ表示のクライアントを開きます。対話は既存のランタイムが処理し、再起動要求で既存のウィンドウに切り替えられます。"
          },
          {
            "title": "字幕は一つ",
            "text": "クリック透過とドラッグはデスクトップ側が管理します。字幕は frontend/celia-legenda-embutida.js が一つの表示面で扱います。"
          },
          {
            "title": "コードの入口",
            "text": "frontend/celia-pet.js と celia-browser-pet.js がクライアントに接続し、tools/pet-electron/main.js がウィンドウを管理します。tools/run_pet.ps1 は Windows 用ランチャーです。"
          },
          {
            "title": "コードの抜粋 · tools/pet-electron/main.js",
            "text": "参考実装からの抜粋です。ファイル名はプロジェクトルートからの相対パスです。",
            "code": "minHeight: 360,\n    frame: false,\n    transparent: true,\n    backgroundColor: \"#00000000\",\n    hasShadow: false,\n    resizable: false,\n    alwaysOnTop: true,",
            "language": "javascript"
          }
        ]
      },
      {
        "id": "vrm",
        "title": "Body の VRM",
        "summary": "人型リグ、ポーズ、実行時の補正。",
        "kicker": "Body と 3D ワールド",
        "group": "body",
        "sections": [
          {
            "title": "モデルの読み込み",
            "text": "参考環境では Célia の VRM をブラウザーに読み込み、frontend/celia-3d-corpo.js がポーズやジェスチャーを制御します。ベンチで個別の動きを確認できます。"
          },
          {
            "title": "モデル側と実行時の補正",
            "text": "腕・肩のウェイトと補正シェイプはモデルにあります。アプリは腕の角度から補正を適用できます。髪、手、スカートなどは版によってコード側の調整もあります。"
          },
          {
            "title": "配布前の確認",
            "text": "設定された VRM が配布先にあるかを確認し、骨の対応、まばたき、口、腕の動き、素材の再配布条件を検証してください。"
          },
          {
            "title": "V02 パッケージの確認",
            "text": "切り替え画面が期待するパスは frontend/modelos-3d/celia_v3_web.vrm です。確認した V02 ではこのパスにファイルがなく、公式モデルは元のインストールにあります。公開前に配布物を調整してください。"
          },
          {
            "title": "コードの抜粋 · frontend/celia-3d-interface.js",
            "text": "参考実装からの抜粋です。ファイル名はプロジェクトルートからの相対パスです。",
            "code": "const quadro = document.createElement(\"iframe\");\n  quadro.id = \"celia-3d-quadro\"; quadro.title = \"Célia 3D\"; quadro.setAttribute(\"aria-hidden\", \"true\"); quadro.tabIndex = -1;\n  const params3d = new URLSearchParams(location.search);",
            "language": "javascript"
          }
        ]
      },
      {
        "id": "mundo-3d",
        "title": "3D ワールドと TikTok",
        "summary": "Godot シーン、Bento、Kenny、カメラとイベント。",
        "kicker": "Body と 3D ワールド",
        "group": "body",
        "sections": [
          {
            "title": "シーンの構成",
            "text": "Godot の参考シーンは Célia の VRM、Bento と Kenny の GLB、環境、HUD、進行役を読み込みます。シミュレーターがチャット、ギフト、いいね、フォロー、発話イベントを送ります。",
            "code": "イベント入力 → バス → ディレクターとキュー\n  → Célia / Bento / Kenny / ワールド / HUD"
          },
          {
            "title": "キャラクターと背景",
            "text": "Bento は歩行とダンスを切り替え、ダンス中はリターゲットが体を制御します。Kenny は独立したモデルとアニメーションを持ち、模擬ギフトのバラで餌皿の動作が起きます。背景には空、光、草、シーン内の設備があります。"
          },
          {
            "title": "TikTok の接続",
            "text": "TikTok リーダーと Godot の描画は別の役割です。モジュールには橋渡し用のコードがあります。実配信は設定と音声・表示の一連の検証が必要です。"
          },
          {
            "title": "Godot モジュールの構成",
            "text": "参考環境の integrations/live3d/godot/cenas/main.tscn がシーンです。scripts/main.gd が組み立て、mundo.gd が環境を設定し、diretor.gd がイベントを処理します。scripts/danca/ がダンスのリターゲットを担当します。"
          },
          {
            "title": "コードの抜粋 · godot/project.godot",
            "text": "参考実装からの抜粋です。ファイル名はプロジェクトルートからの相対パスです。",
            "code": "[application]\n\nconfig/name=\"Celia Live 3D\"\nrun/main_scene=\"res://cenas/main.tscn\"\nconfig/features=PackedStringArray(\"4.7\", \"Forward Plus\")\n\n[autoload]\n\nBarramento=\"*res://scripts/barramento.gd\"",
            "language": "ini"
          }
        ]
      },
      {
        "id": "modelos",
        "title": "モデルとアバター",
        "summary": "モデルの出典と互換性。",
        "group": "body",
        "kicker": "Body と 3D ワールド",
        "sections": [
          {
            "title": "Live2D と VRM",
            "text": "Live2D は .model3.json、.moc3、テクスチャなどで構成されます。3D 表示は VRM または GLB を使用し、クライアントごとに検証します。"
          },
          {
            "title": "素材の出典",
            "text": "モデルを利用または再配布する前に、作者の配布先と利用条件を確認してください。"
          }
        ],
        "models": [
          {
            "name": "Pachan 2.0 · Half-Body (medwokl2d)",
            "description": "Modelo Live2D Half-Body gratuito para VTube Studio criado por medwokl2d no BOOTH. Integrado em web/live2d-models/elizabeth_vts (pachirisu anime girl - top half.model3.json).",
            "image": "assets/model-pachan.webp",
            "links": [
              {
                "label": "BOOTH · medwokl2d (#4711410)",
                "url": "https://booth.pm/en/items/4711410"
              }
            ]
          },
          {
            "name": "VT Student · Raiden Shogun",
            "description": "Modelo Live2D VT_Student (ChoDenPA / Milkydoragon). Integrado em web/live2d-models/shogun_vts (VT_student.model3.json).",
            "image": "assets/model-shogun.webp",
            "links": [
              {
                "label": "Ver referência no Gumroad",
                "url": "https://milkydoragon.gumroad.com/l/avexk"
              }
            ]
          },
          {
            "name": "Hiyori Momo · Live2D Sample",
            "description": "Modelo clássico de referência Cubism (usado como base histórica de VTubers IA como Neuro-sama). Integrado em web/live2d-models/hiyori_vts (hiyori.model3.json).",
            "image": "assets/model-hiyori.webp",
            "links": [
              {
                "label": "Live2D Cubism Sample Models",
                "url": "https://www.live2d.com/en/learn/sample/"
              }
            ]
          },
          {
            "name": "March 7th · 三月七",
            "description": "Modelo Live2D gratuito de March 7th (Honkai: Star Rail) por Design.genius no BOOTH. Integrado em web/live2d-models/march_7th_vts (march 7th.model3.json).",
            "image": "assets/model-march7th.webp",
            "links": [
              {
                "label": "BOOTH · Design.genius (#4961342)",
                "url": "https://booth.pm/en/items/4961342"
              }
            ]
          },
          {
            "name": "Huohuo · 藿藿",
            "description": "Modelo Live2D gratuito de Huohuo por bailyovo no BOOTH, com expressões e física completa. Integrado em web/live2d-models/huohuo_vts (huohuo.model3.json).",
            "image": "assets/model-huohuo.webp",
            "links": [
              {
                "label": "BOOTH · bailyovo (#5288339)",
                "url": "https://booth.pm/en/items/5288339"
              }
            ]
          },
          {
            "name": "Sumire · 女子 モデル名:sumire",
            "description": "Modelo Live2D genérico gratuito de alta fidelidade por creape no BOOTH (sumire_free_001.model3.json).",
            "image": "assets/model-sumire.webp",
            "links": [
              {
                "label": "BOOTH · creape (#5002899)",
                "url": "https://booth.pm/en/items/5002899"
              }
            ]
          },
          {
            "name": "Hoshino Nya",
            "description": "Modelo Live2D gratuito por skiyoshi no BOOTH com texturas 4K e expressões. Integrado em web/live2d-models/hoshinonya_vts (hoshinonya_skiyoshi.model3.json).",
            "image": "assets/model-hoshinonya.webp",
            "links": [
              {
                "label": "BOOTH · skiyoshi (#3988142)",
                "url": "https://booth.pm/en/items/3988142"
              }
            ]
          },
          {
            "name": "Maid Cat Girl · 猫娘",
            "description": "Modelo Live2D de garota gato maid por comte19 no BOOTH (free1.model3.json).",
            "image": "assets/model-maidcat.webp",
            "links": [
              {
                "label": "BOOTH · comte19 (#5047582)",
                "url": "https://booth.pm/en/items/5047582"
              }
            ]
          }
        ]
      },
      {
        "id": "plataformas",
        "title": "プラットフォーム連携",
        "summary": "TikTok、YouTube、Twitch、Minecraft、Discord。",
        "kicker": "連携と公開",
        "group": "release",
        "sections": [
          {
            "title": "TikTok、YouTube、Twitch",
            "text": "TikTok 3D は本体の公開範囲です。YouTube には Célia の動画と関連技術があります。Twitch の案内は確認後に掲載します。"
          },
          {
            "title": "Minecraft",
            "text": "Minecraft ボットはアクションエージェントを伴う別プロジェクトで、独立した公開リポジトリを予定しています。",
            "links": [
              {
                "label": "Minecraft ボットのリポジトリ"
              }
            ]
          },
          {
            "title": "Discord と Kiwify",
            "text": "Discord ボットは別の商用製品です。販売が案内されたら Kiwify の購入ページを掲載します。",
            "links": [
              {
                "label": "Kiwify の Discord ボット"
              }
            ]
          }
        ]
      },
      {
        "id": "desenvolvimento",
        "title": "開発ガイド",
        "summary": "変更する機能とイベントの境界を探します。",
        "group": "release",
        "kicker": "連携と公開",
        "sections": [
          {
            "title": "対象の切り分け",
            "text": "音声は合成から再生まで、ポーズはベンチ、Godot のイベントはシミュレーターで確認します。"
          },
          {
            "title": "検証記録",
            "text": "テストしたビルド、表示先、確認した動作を記録します。"
          }
        ],
        "links": [
          {
            "label": "参照したドキュメントテンプレート（MIT）",
            "url": "https://github.com/Open-LLM-VTuber/open-llm-vtuber.github.io"
          }
        ]
      },
      {
        "id": "api",
        "title": "API リファレンス",
        "summary": "参考環境でのローカルインターフェース。",
        "group": "release",
        "kicker": "連携と公開",
        "sections": [
          {
            "title": "Body と Pet のブリッジ",
            "text": "GET /bridge/body は選択前に null を返せます。GET /bridge/mode は最後に選ばれた表示モードを返します。両方ともループバック接続が必要です。",
            "language": "bash",
            "code": "curl http://127.0.0.1:12393/bridge/body\ncurl http://127.0.0.1:12393/bridge/mode"
          },
          {
            "title": "クライアントイベント",
            "text": "表示部は WebSocket /client-ws を利用します。形式や順番を変更する前に src/celia/routes.py とイベント受信側を確認します。"
          }
        ]
      },
      {
        "id": "diagnostico",
        "title": "障害の切り分け",
        "summary": "入力から表示まで、問題のある段階を特定します。",
        "kicker": "連携と公開",
        "group": "release",
        "sections": [
          {
            "title": "入力と応答",
            "text": "短いテキストから試し、受信、文脈、プロバイダー、応答を順に確認します。マイクだけの問題なら入力、発話検出、ASR を確認します。"
          },
          {
            "title": "音声と表示",
            "text": "合成、クライアントへの送信、実際の再生を分けて確認します。音声が正常なら、感情イベントや口の同期、字幕の表示面を調べます。"
          },
          {
            "title": "3D シーン",
            "text": "実イベントをつなぐ前に模擬入力を使い、バス、キュー、アニメーション、HUD を確認します。"
          }
        ]
      },
      {
        "id": "faq",
        "title": "よくある質問",
        "summary": "音声、Pet、VRM の確認手順。",
        "group": "release",
        "kicker": "連携と公開",
        "sections": [
          {
            "title": "音が出ない",
            "text": "TTS の生成、クライアントへの送信、実際の再生を順に確認します。"
          },
          {
            "title": "Pet の字幕がない",
            "text": "埋め込み字幕とその表示回復処理を確認します。"
          },
          {
            "title": "VRM が読み込まれない",
            "text": "モデルのパス、配布パッケージ、ブラウザーのテスト画面を確認します。"
          }
        ]
      },
      {
        "id": "links",
        "title": "リンクと配布",
        "summary": "公式リンクと公開予定の資料。",
        "kicker": "連携と公開",
        "group": "release",
        "sections": [
          {
            "title": "公開リソース",
            "text": "リポジトリ、導入手順、Kiwify、関連プロジェクトのリンクは公開後に追加します。"
          },
          {
            "title": "素材とライセンス",
            "text": "リグ、テクスチャ、アニメーション、音声、依存関係は個別に再配布条件を確認します。"
          }
        ],
        "links": [
          {
            "label": "Célia の YouTube",
            "url": "https://www.youtube.com/channel/UCAkPtvGuZ9XpwRZHZt8BbnQ"
          },
          {
            "label": "Célia のリポジトリ"
          },
          {
            "label": "公開版の導入ガイド"
          },
          {
            "label": "Minecraft ボットのリポジトリ"
          },
          {
            "label": "Kiwify の Discord ボット"
          },
          {
            "label": "Twitch ガイド"
          }
        ]
      }
    ],
    "cast": [],
    "home": {
      "title": "Célia",
      "tagline": "AI VTuber プロジェクトの技術ドキュメント。",
      "support": "対話と音声、2D・デスクトップ・3D の表示。",
      "cta": "はじめる",
      "docs": "ドキュメント",
      "models": "モデル",
      "image": "assets/celia-mark.webp",
      "features": [
        {
          "page": "arquitetura",
          "title": "アーキテクチャ",
          "text": "入力、ルーティング、表示クライアント。",
          "image": "assets/feature-arquitetura.webp"
        },
        {
          "page": "voz",
          "title": "音声と TTS",
          "text": "Piper、Edge TTS、XTTS、OmniVoice、学習。",
          "image": "assets/feature-voz.webp"
        },
        {
          "page": "corpo",
          "title": "Body とアバター",
          "text": "Live2D、VRM、表情と動作。",
          "image": "assets/feature-corpo.webp"
        },
        {
          "page": "pet",
          "title": "デスクトップ Pet",
          "text": "Electron ウィンドウと字幕。",
          "image": "assets/feature-pet.webp"
        },
        {
          "page": "mundo-3d",
          "title": "3D ワールド",
          "text": "Godot、Bento、Kenny、TikTok 3D。",
          "image": "assets/feature-mundo-3d.webp"
        },
        {
          "page": "plataformas",
          "title": "外部連携",
          "text": "プラットフォームと独立して配布する機能。",
          "image": "assets/feature-plataformas.webp"
        }
      ]
    }
  },
  "es": {
    "ui": {
      "label": "Documentación",
      "brand": "Célia",
      "search": "Buscar",
      "searchPlaceholder": "Buscar",
      "noResults": "Sin resultados",
      "theme": "Tema",
      "light": "Claro",
      "dark": "Oscuro",
      "language": "Idioma",
      "menu": "Menú",
      "onPage": "En esta página",
      "skip": "Ir al contenido",
      "home": "Inicio",
      "previous": "Anterior",
      "next": "Siguiente",
      "comingSoon": "Próximamente",
      "groups": {
        "start": "Primeros pasos",
        "core": "Arquitectura y voz",
        "body": "Cuerpo y mundo 3D",
        "release": "Integraciones y publicación"
      },
      "routeIndex": "Guías técnicas",
      "models": "Modelos"
    },
    "pages": [
      {
        "id": "inicio",
        "title": "Documentación de Célia",
        "summary": "Arquitectura, operación y distribución del proyecto de VTuber de IA.",
        "kicker": "Primeros pasos",
        "group": "start",
        "sections": [
          {
            "title": "Qué incluye el proyecto",
            "text": "Célia combina conversación, voz, Live2D, una mascota de escritorio, un Cuerpo VRM en el navegador y un escenario Godot. Esta guía sigue la entrada hasta las distintas salidas."
          },
          {
            "title": "Por dónde empezar",
            "text": "Consulta Arquitectura y Flujo de conversación. Cuerpo, Modo Pet y VRM describen el avatar. Mundo 3D y TikTok explica el escenario, sus actores y los eventos."
          },
          {
            "title": "Lanzamiento de diciembre",
            "text": "El lanzamiento público está previsto para diciembre. El alcance distingue los componentes principales de las distribuciones independientes."
          }
        ]
      },
      {
        "id": "comecar",
        "title": "Primer inicio",
        "summary": "Prueba una conversación en un entorno preparado.",
        "group": "start",
        "kicker": "Primeros pasos",
        "sections": [
          {
            "title": "Requisitos",
            "text": "La V02 declara Python 3.10–3.12 en pyproject.toml. Electron y Godot corresponden a escritorio y 3D. El lanzamiento definirá el paquete y los entornos compatibles.",
            "code": "python --version",
            "language": "bash"
          },
          {
            "title": "Primera conversación",
            "text": "En Linux, bin/ligar.sh llama a run_celia_linux.py. run_server.py es el punto de entrada conservado del proyecto base. Prueba primero texto, respuesta y audio."
          }
        ]
      },
      {
        "id": "lancamento",
        "title": "Alcance del lanzamiento",
        "summary": "Proyecto principal, repositorio de Minecraft y producto de Discord.",
        "kicker": "Primeros pasos",
        "group": "start",
        "sections": [
          {
            "title": "Proyecto principal",
            "text": "El alcance previsto incluye conversación, voz, Live2D, Pet, Cuerpo/VRM y Mundo 3D, incluido el modo TikTok 3D. El paquete y los requisitos se confirmarán en el lanzamiento."
          },
          {
            "title": "Distribuciones independientes",
            "text": "El bot de Minecraft tendrá su propio repositorio público. El bot de Discord será un producto comercial separado en Kiwify. Los dos enlaces se añadirán al anunciarse."
          },
          {
            "title": "Publicación",
            "text": "Verifica las instrucciones por entorno, la licencia y los activos realmente redistribuibles cuando se publique la versión."
          }
        ]
      },
      {
        "id": "arquitetura",
        "title": "Arquitectura",
        "summary": "Entrada, contexto, voz y clientes de presentación.",
        "kicker": "Arquitectura y voz",
        "group": "core",
        "sections": [
          {
            "title": "Núcleo compartido",
            "text": "El backend FastAPI aloja la conversación. El agente reúne el contexto y solicita una respuesta al proveedor elegido. WebSocket entrega eventos al cliente; audio, reacciones y subtítulos tienen funciones distintas.",
            "code": "Texto o micrófono → transcripción (audio)\n  → contexto + modelo\n  → respuesta / eventos de emoción\n  → síntesis de voz + presentación"
          },
          {
            "title": "Mapa del código",
            "text": "Estos nombres corresponden a la implementación de referencia; confirma la estructura final en el repositorio público.",
            "items": [
              "src/celia/core/ · orquestación y emociones",
              "src/celia/tts/ · síntesis",
              "frontend/ · cliente web y Cuerpo",
              "tools/pet-electron/ · ventana Pet",
              "integrations/live3d/ · escenario Godot"
            ]
          },
          {
            "title": "Límites",
            "text": "Pet reutiliza el cliente. La escena Godot tiene su propia fuente de eventos y modo de simulación. La conexión en vivo depende de cada configuración."
          }
        ]
      },
      {
        "id": "fluxo",
        "title": "Flujo de conversación",
        "summary": "Cómo una entrada se convierte en respuesta, voz y estado visual.",
        "kicker": "Arquitectura y voz",
        "group": "core",
        "sections": [
          {
            "title": "Entrada y turnos",
            "text": "El texto entra directamente al agente. Para el micrófono, la detección de voz delimita la intervención y ASR la transcribe. Las configuraciones documentadas también pueden usar ASR local."
          },
          {
            "title": "Contexto y 9router",
            "text": "El agente usa contexto y memoria de conversación. 9router enruta solicitudes mediante una interfaz compatible con OpenAI. Grok y otras API son opciones de proveedor. Spell local es una opción en evaluación."
          },
          {
            "title": "Respuesta y expresión",
            "text": "La lógica de turnos permite esperar, hablar y reaccionar. TTS sintetiza la respuesta y el cliente coordina la reproducción, los subtítulos, la boca y las expresiones compatibles."
          }
        ]
      },
      {
        "id": "configuracao",
        "title": "Configuración y proveedores",
        "summary": "Reconocimiento, conversación y voz por función.",
        "group": "core",
        "kicker": "Arquitectura y voz",
        "sections": [
          {
            "title": "Campos seleccionados",
            "text": "conf.yaml selecciona proveedores. El entorno de referencia usa ASR compatible con OpenAI y síntesis con OmniVoice.",
            "code": "# Configuración de referencia\nasr_model: openai_compatible_asr\ntts_model: omnivoice",
            "language": "yaml"
          },
          {
            "title": "Rutas",
            "text": "9router enruta modelos. Verifica la configuración efectiva y conserva las credenciales en el entorno privado."
          }
        ]
      },
      {
        "id": "voz",
        "title": "Voz e historial de TTS",
        "summary": "Piper, Edge TTS, XTTS, OmniVoice y entrenamiento en curso.",
        "kicker": "Arquitectura y voz",
        "group": "core",
        "sections": [
          {
            "title": "Piper y Edge TTS",
            "text": "Las primeras versiones usaron Piper y Edge TTS. El historial incluye Piper ajustado para portugués, con V9-D entre sus versiones."
          },
          {
            "title": "XTTS y OmniVoice",
            "text": "XTTS fue una etapa de adaptación vocal y se documentó su retirada de la ruta principal. La ruta operativa documentada usa OmniVoice, con Célia Oficial V2 como referencia aprobada en Central IA para nuevas frases."
          },
          {
            "title": "Entrenamiento de voz propia",
            "text": "El creador está entrenando su propia voz. El perfil y sus archivos requieren validación. El proyecto prioriza el portugués brasileño; los idiomas de esta documentación son traducciones."
          },
          {
            "title": "Qué verificar",
            "text": "Compara las mismas frases, nombres, números y pausas. Registra motor, referencia, comportamiento de síntesis y reproducción audible."
          }
        ]
      },
      {
        "id": "corpo",
        "title": "Cuerpo y emociones",
        "summary": "Controles de la sesión y presentación del avatar.",
        "kicker": "Cuerpo y mundo 3D",
        "group": "body",
        "sections": [
          {
            "title": "Superficies del navegador",
            "text": "Cuerpo reúne el chat, el control y la vista del avatar. Live2D usa su propio lienzo y la vista 3D usa VRM. Pet reutiliza la presentación en el escritorio."
          },
          {
            "title": "Eventos y gestos",
            "text": "El canal de eventos emocionales permite activar expresiones, poses y acciones breves. La boca sigue la voz de manera independiente del movimiento corporal."
          },
          {
            "title": "Prueba de aislamiento",
            "text": "Ensaya una pose en el banco de pruebas antes de añadir voz o integraciones. Si la deformación aparece en aislamiento, revisa el rig y los pesos."
          },
          {
            "title": "Fragmentos de código · src/celia/core/emotion_bus.py",
            "text": "Fragmento de la implementación de referencia, con rutas relativas a la raíz del proyecto.",
            "code": "@dataclass(frozen=True)\nclass EventoEmocao:\n    \"\"\"Um pedido de emoção, já normalizado.\"\"\"\n\n    emocao: str\n    origem: str\n    prioridade: int\n    ttl_s: float | None\n    trava_s: float\n    em: float  # time.monotonic() de quando entrou\n    persistente: bool = False\n    metadados: dict[str, Any] = field(default_factory=dict)",
            "language": "python"
          }
        ]
      },
      {
        "id": "pet",
        "title": "Modo Pet",
        "summary": "Ventana de escritorio Electron con el mismo runtime.",
        "kicker": "Cuerpo y mundo 3D",
        "group": "body",
        "sections": [
          {
            "title": "Aislamiento de audio — contrato de seguridad",
            "text": "Pet y las superficies de presentación no deben abrir el micrófono físico. La animación de la mascota reutiliza el RMS del audio existente. Una sola fuente autorizada alimenta el ASR; el silencio y los cambios de fuente afectan únicamente a la captura de la aplicación. Nunca modificar el micrófono global ni los servicios de audio del sistema. Requisito de validación, no certificación de la versión pública actual."
          },
          {
            "title": "Inicio y ventana",
            "text": "El lanzador abre el cliente en modo escenario dentro de una ventana transparente. El runtime de Célia sigue prestando la sesión y una segunda apertura puede enfocar la ventana existente."
          },
          {
            "title": "Gestos y subtítulos",
            "text": "La ventana administra el paso de clics y el arrastre. frontend/celia-legenda-embutida.js administra una única superficie de subtítulos, incluido su respaldo visual."
          },
          {
            "title": "Entradas del código",
            "text": "frontend/celia-pet.js y celia-browser-pet.js conectan el modo al cliente. tools/pet-electron/main.js controla la ventana y tools/run_pet.ps1 es el lanzador para Windows."
          },
          {
            "title": "Fragmentos de código · tools/pet-electron/main.js",
            "text": "Fragmento de la implementación de referencia, con rutas relativas a la raíz del proyecto.",
            "code": "minHeight: 360,\n    frame: false,\n    transparent: true,\n    backgroundColor: \"#00000000\",\n    hasShadow: false,\n    resizable: false,\n    alwaysOnTop: true,",
            "language": "javascript"
          }
        ]
      },
      {
        "id": "vrm",
        "title": "VRM en Cuerpo",
        "summary": "Rig humanoide, poses y correcciones en el navegador.",
        "kicker": "Cuerpo y mundo 3D",
        "group": "body",
        "sections": [
          {
            "title": "Modelo y cliente",
            "text": "La instalación de referencia carga el VRM de Célia. frontend/celia-3d-corpo.js controla poses, gestos y ajustes en ejecución. El banco permite inspeccionar el modelo."
          },
          {
            "title": "Correcciones",
            "text": "El modelo incluye pesos y formas correctivas de brazos y hombros. El cliente puede activarlas según el ángulo del brazo. Pelo, manos, torsión y falda pueden depender de la revisión."
          },
          {
            "title": "Preparación para publicar",
            "text": "Verifica que exista el VRM configurado en el paquete final. Confirma huesos, postura, parpadeo, boca y procedencia de cada activo."
          },
          {
            "title": "Comprobación de V02",
            "text": "El visor 2D/3D espera frontend/modelos-3d/celia_v3_web.vrm. Esa ruta no contiene el archivo en V02; la instalación original contiene el modelo oficial. Resuelve esta diferencia antes de distribuir el modo 3D."
          },
          {
            "title": "Fragmentos de código · frontend/celia-3d-interface.js",
            "text": "Fragmento de la implementación de referencia, con rutas relativas a la raíz del proyecto.",
            "code": "const quadro = document.createElement(\"iframe\");\n  quadro.id = \"celia-3d-quadro\"; quadro.title = \"Célia 3D\"; quadro.setAttribute(\"aria-hidden\", \"true\"); quadro.tabIndex = -1;\n  const params3d = new URLSearchParams(location.search);",
            "language": "javascript"
          }
        ]
      },
      {
        "id": "mundo-3d",
        "title": "Mundo 3D y TikTok",
        "summary": "Escenario Godot, director y controladores de Bento y Kenny.",
        "kicker": "Cuerpo y mundo 3D",
        "group": "body",
        "sections": [
          {
            "title": "Montaje y eventos",
            "text": "La escena Godot carga a Célia en VRM, a Bento y Kenny en GLB, el entorno, el HUD y el director. El simulador emite chat, regalos, likes, seguidores y voz al bus.",
            "code": "Fuente de eventos → bus → director y colas\n  → Célia / Bento / Kenny / mundo / HUD"
          },
          {
            "title": "Actores y entorno",
            "text": "Bento puede caminar o bailar con retargeting; durante un baile, ese controlador toma la pose corporal. Kenny tiene modelo y animaciones separados. Una Rosa simulada activa la acción del cuenco. El entorno gestiona cielo, luces, césped y elementos de escena."
          },
          {
            "title": "TikTok 3D",
            "text": "La escena y el lector de TikTok tienen funciones separadas. El código incluye una fuente para la conexión con Célia. Una emisión en vivo exige configurar y verificar la ruta completa."
          },
          {
            "title": "Mapa de Godot",
            "text": "En la instalación de referencia, integrations/live3d/godot/cenas/main.tscn define la escena. scripts/main.gd la monta, mundo.gd configura el entorno, diretor.gd organiza eventos y scripts/danca/ gestiona el retargeting."
          },
          {
            "title": "Fragmentos de código · godot/project.godot",
            "text": "Fragmento de la implementación de referencia, con rutas relativas a la raíz del proyecto.",
            "code": "[application]\n\nconfig/name=\"Celia Live 3D\"\nrun/main_scene=\"res://cenas/main.tscn\"\nconfig/features=PackedStringArray(\"4.7\", \"Forward Plus\")\n\n[autoload]\n\nBarramento=\"*res://scripts/barramento.gd\"",
            "language": "ini"
          }
        ]
      },
      {
        "id": "modelos",
        "title": "Modelos y avatares",
        "summary": "Origen y compatibilidad de los modelos.",
        "group": "body",
        "kicker": "Cuerpo y mundo 3D",
        "sections": [
          {
            "title": "Live2D y VRM",
            "text": "Live2D utiliza .model3.json, .moc3 y texturas. Las capas 3D usan VRM o GLB según el cliente y deben verificarse por separado."
          },
          {
            "title": "Origen",
            "text": "Confirma la página del autor y sus condiciones antes de reutilizar o redistribuir un modelo."
          }
        ],
        "models": [
          {
            "name": "Pachan 2.0 · Half-Body (medwokl2d)",
            "description": "Modelo Live2D Half-Body gratuito para VTube Studio criado por medwokl2d no BOOTH. Integrado em web/live2d-models/elizabeth_vts (pachirisu anime girl - top half.model3.json).",
            "image": "assets/model-pachan.webp",
            "links": [
              {
                "label": "BOOTH · medwokl2d (#4711410)",
                "url": "https://booth.pm/en/items/4711410"
              }
            ]
          },
          {
            "name": "VT Student · Raiden Shogun",
            "description": "Modelo Live2D VT_Student (ChoDenPA / Milkydoragon). Integrado em web/live2d-models/shogun_vts (VT_student.model3.json).",
            "image": "assets/model-shogun.webp",
            "links": [
              {
                "label": "Ver referência no Gumroad",
                "url": "https://milkydoragon.gumroad.com/l/avexk"
              }
            ]
          },
          {
            "name": "Hiyori Momo · Live2D Sample",
            "description": "Modelo clássico de referência Cubism (usado como base histórica de VTubers IA como Neuro-sama). Integrado em web/live2d-models/hiyori_vts (hiyori.model3.json).",
            "image": "assets/model-hiyori.webp",
            "links": [
              {
                "label": "Live2D Cubism Sample Models",
                "url": "https://www.live2d.com/en/learn/sample/"
              }
            ]
          },
          {
            "name": "March 7th · 三月七",
            "description": "Modelo Live2D gratuito de March 7th (Honkai: Star Rail) por Design.genius no BOOTH. Integrado em web/live2d-models/march_7th_vts (march 7th.model3.json).",
            "image": "assets/model-march7th.webp",
            "links": [
              {
                "label": "BOOTH · Design.genius (#4961342)",
                "url": "https://booth.pm/en/items/4961342"
              }
            ]
          },
          {
            "name": "Huohuo · 藿藿",
            "description": "Modelo Live2D gratuito de Huohuo por bailyovo no BOOTH, com expressões e física completa. Integrado em web/live2d-models/huohuo_vts (huohuo.model3.json).",
            "image": "assets/model-huohuo.webp",
            "links": [
              {
                "label": "BOOTH · bailyovo (#5288339)",
                "url": "https://booth.pm/en/items/5288339"
              }
            ]
          },
          {
            "name": "Sumire · 女子 モデル名:sumire",
            "description": "Modelo Live2D genérico gratuito de alta fidelidade por creape no BOOTH (sumire_free_001.model3.json).",
            "image": "assets/model-sumire.webp",
            "links": [
              {
                "label": "BOOTH · creape (#5002899)",
                "url": "https://booth.pm/en/items/5002899"
              }
            ]
          },
          {
            "name": "Hoshino Nya",
            "description": "Modelo Live2D gratuito por skiyoshi no BOOTH com texturas 4K e expressões. Integrado em web/live2d-models/hoshinonya_vts (hoshinonya_skiyoshi.model3.json).",
            "image": "assets/model-hoshinonya.webp",
            "links": [
              {
                "label": "BOOTH · skiyoshi (#3988142)",
                "url": "https://booth.pm/en/items/3988142"
              }
            ]
          },
          {
            "name": "Maid Cat Girl · 猫娘",
            "description": "Modelo Live2D de garota gato maid por comte19 no BOOTH (free1.model3.json).",
            "image": "assets/model-maidcat.webp",
            "links": [
              {
                "label": "BOOTH · comte19 (#5047582)",
                "url": "https://booth.pm/en/items/5047582"
              }
            ]
          }
        ]
      },
      {
        "id": "plataformas",
        "title": "Plataformas e integraciones",
        "summary": "TikTok, YouTube, Twitch, Minecraft y Discord.",
        "kicker": "Integraciones y publicación",
        "group": "release",
        "sections": [
          {
            "title": "TikTok, YouTube y Twitch",
            "text": "TikTok 3D forma parte del proyecto principal. YouTube aloja los vídeos de Célia y tiene trabajo técnico documentado. El enlace de Twitch se añadirá cuando se confirme."
          },
          {
            "title": "Minecraft",
            "text": "El bot de Minecraft tendrá un repositorio público separado, con su integración y agente de acciones.",
            "links": [
              {
                "label": "Repositorio del bot de Minecraft"
              }
            ]
          },
          {
            "title": "Discord en Kiwify",
            "text": "El bot de Discord es un producto comercial independiente. Se añadirá el enlace de compra de Kiwify cuando esté disponible.",
            "links": [
              {
                "label": "Bot de Discord en Kiwify"
              }
            ]
          }
        ]
      },
      {
        "id": "desenvolvimento",
        "title": "Guía de desarrollo",
        "summary": "Productor, contrato y consumidor por componente.",
        "group": "release",
        "kicker": "Integraciones y publicación",
        "sections": [
          {
            "title": "Ruta de diagnóstico",
            "text": "Sigue el evento desde su origen. Para voz: síntesis y reproducción. Para gestos: modelo y controlador. Para Godot: simulador y cola del director."
          },
          {
            "title": "Evidencia",
            "text": "Anota la versión, el cliente y la prueba que valida el cambio."
          }
        ],
        "links": [
          {
            "label": "Plantilla de documentación de referencia (MIT)",
            "url": "https://github.com/Open-LLM-VTuber/open-llm-vtuber.github.io"
          }
        ]
      },
      {
        "id": "api",
        "title": "Referencia de API",
        "summary": "Rutas de la interfaz local.",
        "group": "release",
        "kicker": "Integraciones y publicación",
        "sections": [
          {
            "title": "Puente de Cuerpo y Pet",
            "text": "GET /bridge/body puede devolver null antes de una elección. GET /bridge/mode devuelve el último modo. Ambas rutas requieren acceso local.",
            "language": "bash",
            "code": "curl http://127.0.0.1:12393/bridge/body\ncurl http://127.0.0.1:12393/bridge/mode"
          },
          {
            "title": "Eventos del cliente",
            "text": "La interfaz usa WebSocket /client-ws. Revisa src/celia/routes.py y el consumidor antes de cambiar el formato o el orden de los mensajes."
          }
        ]
      },
      {
        "id": "diagnostico",
        "title": "Diagnóstico",
        "summary": "Localiza la etapa que falla antes de ajustar todo el sistema.",
        "kicker": "Integraciones y publicación",
        "group": "release",
        "sections": [
          {
            "title": "Conversación",
            "text": "Prueba un mensaje de texto y sigue entrada, contexto, proveedor y respuesta. Si solo falla el micrófono, revisa captura, detección y ASR."
          },
          {
            "title": "Voz y presentación",
            "text": "Comprueba síntesis, entrega y reproducción por separado. Si falla el avatar con audio correcto, inspecciona eventos, expresiones y sincronización labial."
          },
          {
            "title": "Escena 3D",
            "text": "Usa un evento simulado para revisar el bus, el director, la animación de cada actor y el HUD. Aísla una pose problemática en el banco."
          }
        ]
      },
      {
        "id": "faq",
        "title": "Preguntas frecuentes",
        "summary": "Consultas sobre voz, Pet y VRM.",
        "group": "release",
        "kicker": "Integraciones y publicación",
        "sections": [
          {
            "title": "No se oye audio",
            "text": "Confirma que TTS generó audio y que el cliente lo reproduce."
          },
          {
            "title": "Falta el subtítulo del Pet",
            "text": "Comprueba la superficie integrada y su respaldo."
          },
          {
            "title": "No carga el VRM",
            "text": "Revisa ruta y recursos de distribución, luego prueba el visor."
          }
        ]
      },
      {
        "id": "links",
        "title": "Enlaces y distribución",
        "summary": "Destinos oficiales y recursos previstos.",
        "kicker": "Integraciones y publicación",
        "group": "release",
        "sections": [
          {
            "title": "Recursos del lanzamiento",
            "text": "Los enlaces al repositorio, instalación, Kiwify y otros proyectos se añadirán tras el anuncio oficial."
          },
          {
            "title": "Activos y licencias",
            "text": "Comprueba las condiciones de cada modelo, voz, textura, animación y dependencia antes de redistribuirlos."
          }
        ],
        "links": [
          {
            "label": "Canal de Célia en YouTube",
            "url": "https://www.youtube.com/channel/UCAkPtvGuZ9XpwRZHZt8BbnQ"
          },
          {
            "label": "Repositorio de Célia"
          },
          {
            "label": "Guía de instalación"
          },
          {
            "label": "Repositorio del bot de Minecraft"
          },
          {
            "label": "Bot de Discord en Kiwify"
          },
          {
            "label": "Guía de Twitch"
          }
        ]
      }
    ],
    "cast": [],
    "home": {
      "title": "Célia",
      "tagline": "Documentación del proyecto de VTuber con IA.",
      "support": "Conversación, voz y avatares en 2D, escritorio y escenas 3D.",
      "cta": "Empezar",
      "docs": "Documentación",
      "models": "Modelos",
      "image": "assets/celia-mark.webp",
      "features": [
        {
          "page": "arquitetura",
          "title": "Arquitectura",
          "text": "Entradas, rutas de modelos y clientes.",
          "image": "assets/feature-arquitetura.webp"
        },
        {
          "page": "voz",
          "title": "Voz y TTS",
          "text": "Piper, Edge TTS, XTTS, OmniVoice y entrenamiento.",
          "image": "assets/feature-voz.webp"
        },
        {
          "page": "corpo",
          "title": "Cuerpo y avatares",
          "text": "Live2D, VRM, acciones y expresiones.",
          "image": "assets/feature-corpo.webp"
        },
        {
          "page": "pet",
          "title": "Modo Pet",
          "text": "Ventana Electron y subtítulos integrados.",
          "image": "assets/feature-pet.webp"
        },
        {
          "page": "mundo-3d",
          "title": "Mundo 3D",
          "text": "Godot, Bento, Kenny y TikTok 3D.",
          "image": "assets/feature-mundo-3d.webp"
        },
        {
          "page": "plataformas",
          "title": "Integraciones",
          "text": "Plataformas y módulos de distribución separada.",
          "image": "assets/feature-plataformas.webp"
        }
      ]
    }
  },
  "zh-CN": {
    "ui": {
      "label": "文档",
      "brand": "Célia",
      "search": "搜索",
      "searchPlaceholder": "搜索",
      "noResults": "没有搜索结果",
      "theme": "主题",
      "light": "浅色",
      "dark": "深色",
      "language": "语言",
      "menu": "菜单",
      "onPage": "本页内容",
      "skip": "跳到正文",
      "home": "首页",
      "previous": "上一页",
      "next": "下一页",
      "comingSoon": "即将推出",
      "groups": {
        "start": "开始",
        "core": "架构与声音",
        "body": "Body 与 3D 世界",
        "release": "集成与发布"
      },
      "routeIndex": "技术指南",
      "models": "模型"
    },
    "pages": [
      {
        "id": "inicio",
        "title": "Célia 技术文档",
        "summary": "AI VTuber 项目的架构、运行流程和发布范围。",
        "kicker": "开始",
        "group": "start",
        "sections": [
          {
            "title": "项目组成",
            "text": "Célia 由对话运行时、语音、Live2D、桌面 Pet、浏览器内的 VRM Body 和 Godot 场景组成。本指南说明输入如何进入核心，再到达各个展示端。"
          },
          {
            "title": "从哪里开始",
            "text": "先阅读架构和对话流程。若要调试形象，请阅读 Body、Pet 和 VRM。3D 世界与 TikTok 一章涵盖场景、Bento、Kenny 及事件。"
          },
          {
            "title": "十二月发布计划",
            "text": "计划在十二月公开主项目。发布范围一章说明主项目与独立发行模块之间的关系。"
          }
        ]
      },
      {
        "id": "comecar",
        "title": "快速开始",
        "summary": "从已经准备的环境验证首次对话。",
        "group": "start",
        "kicker": "开始",
        "sections": [
          {
            "title": "环境要求",
            "text": "V02 的 pyproject.toml 声明 Python 3.10–3.12。Electron 与 Godot 分别支持桌面与 3D 展示。最终安装包与支持的环境以正式发布为准。",
            "code": "python --version",
            "language": "bash"
          },
          {
            "title": "首次对话",
            "text": "Linux 参考环境的 bin/ligar.sh 调用 run_celia_linux.py。先检查文本、回复和实际音频播放，再启用麦克风或平台集成。"
          }
        ]
      },
      {
        "id": "lancamento",
        "title": "发布范围",
        "summary": "主项目、独立仓库和商业产品。",
        "kicker": "开始",
        "group": "start",
        "sections": [
          {
            "title": "主项目",
            "text": "计划涵盖对话、语音、Live2D、Pet、Body/VRM、3D 世界及 TikTok 3D 模式。最终包与环境要求以正式发布为准。"
          },
          {
            "title": "独立发行",
            "text": "Minecraft 机器人计划使用独立的公开仓库。Discord 机器人将作为单独产品在 Kiwify 销售。相关链接公布后会添加到此处。"
          },
          {
            "title": "发布检查",
            "text": "发布时确认安装流程、许可、支持环境以及可随代码一起分发的资源。"
          }
        ]
      },
      {
        "id": "arquitetura",
        "title": "架构",
        "summary": "输入、对话核心、语音合成和展示客户端。",
        "kicker": "架构与声音",
        "group": "core",
        "sections": [
          {
            "title": "共享核心",
            "text": "FastAPI 后端运行对话流程。代理整理上下文并请求选定的提供方。WebSocket 向客户端发送事件，音频、字幕和动作由展示层处理。",
            "code": "文本或麦克风 → 语音转写（仅音频）\n  → 上下文 + 对话模型\n  → 回应 / 情绪事件\n  → 语音合成 + 展示"
          },
          {
            "title": "参考代码结构",
            "text": "发布版的文件布局以后续仓库为准。",
            "items": [
              "src/celia/core/ · 编排与情绪事件",
              "src/celia/tts/ · 语音合成",
              "frontend/ · 浏览器客户端和 Body",
              "tools/pet-electron/ · 桌面 Pet",
              "integrations/live3d/ · Godot 场景"
            ]
          },
          {
            "title": "运行边界",
            "text": "Pet 复用 Célia 客户端。Godot 场景有独立事件来源，可使用模拟器验证。代码中存在适配器并不代表已接入实际直播。"
          }
        ]
      },
      {
        "id": "fluxo",
        "title": "对话流程",
        "summary": "输入如何成为语音、表情和回应。",
        "kicker": "架构与声音",
        "group": "core",
        "sections": [
          {
            "title": "输入与轮次",
            "text": "文本直接传给代理。麦克风输入通过语音活动检测划分片段，然后由 ASR 转写。已记录的环境也可采用本地语音识别作为备选。"
          },
          {
            "title": "上下文与 9router",
            "text": "代理使用上下文并保留对话记忆。9router 提供兼容 OpenAI 的网关，可按角色配置路由。Grok 和其他 API 是候选方案，本地 Spell 仍属评估选项。"
          },
          {
            "title": "语音与动作",
            "text": "轮次控制决定等待、发言或短暂反应。TTS 合成音频，客户端处理播放、口型同步、字幕和支持的视觉状态。"
          }
        ]
      },
      {
        "id": "configuracao",
        "title": "配置和提供方",
        "summary": "分别配置识别、对话及语音合成。",
        "group": "core",
        "kicker": "架构与声音",
        "sections": [
          {
            "title": "已选字段",
            "text": "conf.yaml 选择提供方。参考配置使用兼容 OpenAI 的 ASR 和 OmniVoice。",
            "code": "# 参考配置字段\nasr_model: openai_compatible_asr\ntts_model: omnivoice",
            "language": "yaml"
          },
          {
            "title": "路由",
            "text": "9router 负责模型路由。核对运行时实际选择和备选路由，凭据应保存在私有环境中。"
          }
        ]
      },
      {
        "id": "voz",
        "title": "声音与 TTS 历史",
        "summary": "Piper、Edge TTS、XTTS、OmniVoice 和正在训练的自有声音。",
        "kicker": "架构与声音",
        "group": "core",
        "sections": [
          {
            "title": "Piper 与 Edge TTS",
            "text": "项目早期采用 Piper 和 Edge TTS。历史中还有面向葡萄牙语微调的 Piper 系列，包括 V9-D。"
          },
          {
            "title": "XTTS 与 OmniVoice",
            "text": "XTTS 用于声音适配与实验，后来退出主要发声链路。文档记录的语音链路采用 OmniVoice。Central IA 为新台词批准的参考音频是 Célia Oficial V2。"
          },
          {
            "title": "自有声音训练",
            "text": "制作者正在训练自己的声音。采用的配置及可分发文件有待验证。Célia 优先面向巴西葡萄牙语，本站的五种语言用于阅读文档。"
          },
          {
            "title": "验证方法",
            "text": "使用相同台词评估专有名词、数字、停顿与实际播放，分别记录引擎、参考声音与结果。"
          }
        ]
      },
      {
        "id": "corpo",
        "title": "Body 与情绪",
        "summary": "会话控制与虚拟形象展示。",
        "kicker": "Body 与 3D 世界",
        "group": "body",
        "sections": [
          {
            "title": "浏览器展示层",
            "text": "Body 集成聊天、操作控件和形象。Live2D 使用自己的画布，浏览器 3D 视图使用 VRM。Pet 在桌面复用展示部分。"
          },
          {
            "title": "事件与动作",
            "text": "情绪事件可映射到模型支持的表情、姿势和简短动作。嘴部与语音同步，身体姿势单独控制。"
          },
          {
            "title": "隔离检查",
            "text": "先在测试视图验证已知姿势。若独立姿势已变形，检查骨骼和权重；若问题只在接收事件后出现，检查事件和状态映射。"
          },
          {
            "title": "代码片段 · src/celia/core/emotion_bus.py",
            "text": "摘自参考实现，文件名均相对于项目根目录。",
            "code": "@dataclass(frozen=True)\nclass EventoEmocao:\n    \"\"\"Um pedido de emoção, já normalizado.\"\"\"\n\n    emocao: str\n    origem: str\n    prioridade: int\n    ttl_s: float | None\n    trava_s: float\n    em: float  # time.monotonic() de quando entrou\n    persistente: bool = False\n    metadados: dict[str, Any] = field(default_factory=dict)",
            "language": "python"
          }
        ]
      },
      {
        "id": "pet",
        "title": "桌面 Pet",
        "summary": "基于 Electron 的透明角色窗口。",
        "kicker": "Body 与 3D 世界",
        "group": "body",
        "sections": [
          {
            "title": "音频隔离安全约定",
            "text": "Pet 和其他展示界面不得打开物理麦克风。吉祥物动画复用现有音频的 RMS。仅一个已授权的音源可进入 ASR；静音和音源切换只影响应用自身的采集。不得修改系统全局麦克风设置或音频服务。这是发布验收要求，并不代表当前公开版本已通过验证。"
          },
          {
            "title": "启动与交互",
            "text": "启动器让 Electron 使用舞台模式打开 Célia 客户端。已有运行时继续提供对话，再次打开可聚焦现有窗口。窗口负责鼠标穿透和拖动手势。"
          },
          {
            "title": "单一字幕界面",
            "text": "frontend/celia-legenda-embutida.js 在同一个界面处理 Pet 的字幕与显示回退。一条语音应对应一处字幕。"
          },
          {
            "title": "代码入口",
            "text": "frontend/celia-pet.js 和 celia-browser-pet.js 将 Pet 连接到客户端。tools/pet-electron/main.js 管理窗口，tools/run_pet.ps1 是 Windows 启动脚本。"
          },
          {
            "title": "代码片段 · tools/pet-electron/main.js",
            "text": "摘自参考实现，文件名均相对于项目根目录。",
            "code": "minHeight: 360,\n    frame: false,\n    transparent: true,\n    backgroundColor: \"#00000000\",\n    hasShadow: false,\n    resizable: false,\n    alwaysOnTop: true,",
            "language": "javascript"
          }
        ]
      },
      {
        "id": "vrm",
        "title": "Body 中的 VRM",
        "summary": "人形骨架、姿势与运行时修正。",
        "kicker": "Body 与 3D 世界",
        "group": "body",
        "sections": [
          {
            "title": "加载模型",
            "text": "参考环境在浏览器加载 Célia 的 VRM，frontend/celia-3d-corpo.js 管理姿势、手势和修正。可在测试页面单独检查每种运动。"
          },
          {
            "title": "模型与应用修正",
            "text": "模型记录手臂、肩部权重和修正形态；应用可根据手臂角度激活形态。头发、手部、扭转与裙摆的调整依版本而异。"
          },
          {
            "title": "发布检查",
            "text": "确认目标包包含默认 VRM，再检查骨骼关联、静止姿势、眨眼、嘴部和手臂。逐项确认资产的再发布许可。"
          },
          {
            "title": "检查 V02 包",
            "text": "2D/3D 切换器要求 frontend/modelos-3d/celia_v3_web.vrm。已检查的 V02 在该路径缺少文件，原始安装中包含官方模型。发布前需要解决这一打包差异。"
          },
          {
            "title": "代码片段 · frontend/celia-3d-interface.js",
            "text": "摘自参考实现，文件名均相对于项目根目录。",
            "code": "const quadro = document.createElement(\"iframe\");\n  quadro.id = \"celia-3d-quadro\"; quadro.title = \"Célia 3D\"; quadro.setAttribute(\"aria-hidden\", \"true\"); quadro.tabIndex = -1;\n  const params3d = new URLSearchParams(location.search);",
            "language": "javascript"
          }
        ]
      },
      {
        "id": "mundo-3d",
        "title": "3D 世界与 TikTok",
        "summary": "Godot 场景、导演、Bento、Kenny 与事件输入。",
        "kicker": "Body 与 3D 世界",
        "group": "body",
        "sections": [
          {
            "title": "场景组装",
            "text": "Godot 参考场景加载 Célia 的 VRM、Bento 和 Kenny 的 GLB，以及环境、HUD 和导演。模拟器向总线发送聊天、礼物、点赞、关注及发声事件。",
            "code": "事件来源 → 总线 → 导演和队列\n  → Célia / Bento / Kenny / 场景 / HUD"
          },
          {
            "title": "角色与场景",
            "text": "Bento 可以走路或参与舞蹈，舞蹈时重定向模块控制身体。Kenny 独立加载模型和动画，模拟的玫瑰礼物可触发食盆动作。环境管理天空、灯光、草地和道具。"
          },
          {
            "title": "TikTok 3D",
            "text": "TikTok 读取端和 Godot 渲染器各有职责。代码中包含连接 Célia 的事件桥接模块。连接真实直播需要配置并验证整条链路。"
          },
          {
            "title": "Godot 模块结构",
            "text": "参考安装中的 integrations/live3d/godot/cenas/main.tscn 定义场景。scripts/main.gd 组装模块，mundo.gd 配置环境，diretor.gd 处理事件，scripts/danca/ 负责舞蹈重定向。"
          },
          {
            "title": "代码片段 · godot/project.godot",
            "text": "摘自参考实现，文件名均相对于项目根目录。",
            "code": "[application]\n\nconfig/name=\"Celia Live 3D\"\nrun/main_scene=\"res://cenas/main.tscn\"\nconfig/features=PackedStringArray(\"4.7\", \"Forward Plus\")\n\n[autoload]\n\nBarramento=\"*res://scripts/barramento.gd\"",
            "language": "ini"
          }
        ]
      },
      {
        "id": "modelos",
        "title": "模型与虚拟形象",
        "summary": "模型来源与兼容性。",
        "group": "body",
        "kicker": "Body 与 3D 世界",
        "sections": [
          {
            "title": "Live2D 与 VRM",
            "text": "Live2D 模型通常包含 .model3.json、.moc3 与贴图。3D 展示采用 VRM 或 GLB，应在各自客户端单独验证。"
          },
          {
            "title": "资产来源",
            "text": "使用或再分发模型前，先确认原作者发布页面和许可条件。"
          }
        ],
        "models": [
          {
            "name": "Pachan 2.0 · Half-Body (medwokl2d)",
            "description": "Modelo Live2D Half-Body gratuito para VTube Studio criado por medwokl2d no BOOTH. Integrado em web/live2d-models/elizabeth_vts (pachirisu anime girl - top half.model3.json).",
            "image": "assets/model-pachan.webp",
            "links": [
              {
                "label": "BOOTH · medwokl2d (#4711410)",
                "url": "https://booth.pm/en/items/4711410"
              }
            ]
          },
          {
            "name": "VT Student · Raiden Shogun",
            "description": "Modelo Live2D VT_Student (ChoDenPA / Milkydoragon). Integrado em web/live2d-models/shogun_vts (VT_student.model3.json).",
            "image": "assets/model-shogun.webp",
            "links": [
              {
                "label": "Ver referência no Gumroad",
                "url": "https://milkydoragon.gumroad.com/l/avexk"
              }
            ]
          },
          {
            "name": "Hiyori Momo · Live2D Sample",
            "description": "Modelo clássico de referência Cubism (usado como base histórica de VTubers IA como Neuro-sama). Integrado em web/live2d-models/hiyori_vts (hiyori.model3.json).",
            "image": "assets/model-hiyori.webp",
            "links": [
              {
                "label": "Live2D Cubism Sample Models",
                "url": "https://www.live2d.com/en/learn/sample/"
              }
            ]
          },
          {
            "name": "March 7th · 三月七",
            "description": "Modelo Live2D gratuito de March 7th (Honkai: Star Rail) por Design.genius no BOOTH. Integrado em web/live2d-models/march_7th_vts (march 7th.model3.json).",
            "image": "assets/model-march7th.webp",
            "links": [
              {
                "label": "BOOTH · Design.genius (#4961342)",
                "url": "https://booth.pm/en/items/4961342"
              }
            ]
          },
          {
            "name": "Huohuo · 藿藿",
            "description": "Modelo Live2D gratuito de Huohuo por bailyovo no BOOTH, com expressões e física completa. Integrado em web/live2d-models/huohuo_vts (huohuo.model3.json).",
            "image": "assets/model-huohuo.webp",
            "links": [
              {
                "label": "BOOTH · bailyovo (#5288339)",
                "url": "https://booth.pm/en/items/5288339"
              }
            ]
          },
          {
            "name": "Sumire · 女子 モデル名:sumire",
            "description": "Modelo Live2D genérico gratuito de alta fidelidade por creape no BOOTH (sumire_free_001.model3.json).",
            "image": "assets/model-sumire.webp",
            "links": [
              {
                "label": "BOOTH · creape (#5002899)",
                "url": "https://booth.pm/en/items/5002899"
              }
            ]
          },
          {
            "name": "Hoshino Nya",
            "description": "Modelo Live2D gratuito por skiyoshi no BOOTH com texturas 4K e expressões. Integrado em web/live2d-models/hoshinonya_vts (hoshinonya_skiyoshi.model3.json).",
            "image": "assets/model-hoshinonya.webp",
            "links": [
              {
                "label": "BOOTH · skiyoshi (#3988142)",
                "url": "https://booth.pm/en/items/3988142"
              }
            ]
          },
          {
            "name": "Maid Cat Girl · 猫娘",
            "description": "Modelo Live2D de garota gato maid por comte19 no BOOTH (free1.model3.json).",
            "image": "assets/model-maidcat.webp",
            "links": [
              {
                "label": "BOOTH · comte19 (#5047582)",
                "url": "https://booth.pm/en/items/5047582"
              }
            ]
          }
        ]
      },
      {
        "id": "plataformas",
        "title": "平台与集成",
        "summary": "TikTok、YouTube、Twitch、Minecraft、Discord。",
        "kicker": "集成与发布",
        "group": "release",
        "sections": [
          {
            "title": "TikTok、YouTube、Twitch",
            "text": "TikTok 3D 在主项目发布计划内。YouTube 已发布 Célia 的视频，并有内容处理相关实现记录。Twitch 指南确认后将添加。"
          },
          {
            "title": "Minecraft",
            "text": "Minecraft 机器人含独立动作代理和集成接口，将采用单独的公开仓库。",
            "links": [
              {
                "label": "Minecraft 机器人仓库"
              }
            ]
          },
          {
            "title": "Discord 与 Kiwify",
            "text": "Discord 机器人将独立销售。Kiwify 购买页面公布后将在此添加链接。",
            "links": [
              {
                "label": "Kiwify 的 Discord 机器人"
              }
            ]
          }
        ]
      },
      {
        "id": "desenvolvimento",
        "title": "开发指南",
        "summary": "定位功能的事件产生方、协议与接收方。",
        "group": "release",
        "kicker": "集成与发布",
        "sections": [
          {
            "title": "分层排查",
            "text": "语音从合成追踪到实际播放；姿势在测试台验证；Godot 事件先使用模拟器。"
          },
          {
            "title": "验证记录",
            "text": "记录测试使用的构建版本、展示端和验证结果。"
          }
        ],
        "links": [
          {
            "label": "参考文档模板（MIT）",
            "url": "https://github.com/Open-LLM-VTuber/open-llm-vtuber.github.io"
          }
        ]
      },
      {
        "id": "api",
        "title": "API 参考",
        "summary": "参考安装中的本地界面路由。",
        "group": "release",
        "kicker": "集成与发布",
        "sections": [
          {
            "title": "Body 与 Pet 桥接",
            "text": "GET /bridge/body 在未选择形象时可返回 null。GET /bridge/mode 返回最后请求的界面模式。两个处理程序都要求本机回环访问。",
            "language": "bash",
            "code": "curl http://127.0.0.1:12393/bridge/body\ncurl http://127.0.0.1:12393/bridge/mode"
          },
          {
            "title": "客户端事件",
            "text": "展示端使用 WebSocket /client-ws。修改消息格式或顺序前，请检查 src/celia/routes.py 和对应的事件接收端。"
          }
        ]
      },
      {
        "id": "diagnostico",
        "title": "故障排查",
        "summary": "定位输入、生成、播放或展示出错的环节。",
        "kicker": "集成与发布",
        "group": "release",
        "sections": [
          {
            "title": "输入和回复",
            "text": "先用短文本逐步检查接收、上下文、提供方和回复。如果只有麦克风失败，优先检查采集、语音活动检测与 ASR。"
          },
          {
            "title": "音频和形象",
            "text": "分别检查合成、发送和实际播放。若音频正常而动作异常，检查事件映射、口型和字幕显示。"
          },
          {
            "title": "3D 场景",
            "text": "连接直播前用模拟事件检查总线、导演队列、动作与 HUD。用测试视图单独验证有问题的姿势。"
          }
        ]
      },
      {
        "id": "faq",
        "title": "常见问题",
        "summary": "语音、Pet 与 VRM 故障检查。",
        "group": "release",
        "kicker": "集成与发布",
        "sections": [
          {
            "title": "有文本却没有声音",
            "text": "依次检查 TTS 输出、向客户端发送和实际播放。"
          },
          {
            "title": "Pet 缺少字幕",
            "text": "检查内嵌字幕及其回退机制。"
          },
          {
            "title": "VRM 无法加载",
            "text": "检查路径、分发包资产和浏览器测试视图。"
          }
        ]
      },
      {
        "id": "links",
        "title": "链接与分发",
        "summary": "官方入口和待发布资源。",
        "kicker": "集成与发布",
        "group": "release",
        "sections": [
          {
            "title": "发布资料",
            "text": "仓库、安装说明、Kiwify 和其他项目的链接将在资源公开后添加。"
          },
          {
            "title": "许可和资源",
            "text": "单独核对每个骨架、贴图、动画、声音和依赖项的分发条件。"
          }
        ],
        "links": [
          {
            "label": "Célia 的 YouTube 频道",
            "url": "https://www.youtube.com/channel/UCAkPtvGuZ9XpwRZHZt8BbnQ"
          },
          {
            "label": "Célia 仓库"
          },
          {
            "label": "发布版安装指南"
          },
          {
            "label": "Minecraft 机器人仓库"
          },
          {
            "label": "Kiwify 的 Discord 机器人"
          },
          {
            "label": "Twitch 指南"
          }
        ]
      }
    ],
    "cast": [],
    "home": {
      "title": "Célia",
      "tagline": "AI VTuber 项目的技术文档。",
      "support": "对话、语音，以及 2D、桌面和 3D 场景展示。",
      "cta": "开始使用",
      "docs": "文档",
      "models": "模型",
      "image": "assets/celia-mark.webp",
      "features": [
        {
          "page": "arquitetura",
          "title": "架构",
          "text": "输入、模型路由与展示客户端。",
          "image": "assets/feature-arquitetura.webp"
        },
        {
          "page": "voz",
          "title": "声音与 TTS",
          "text": "Piper、Edge TTS、XTTS、OmniVoice 与声音训练。",
          "image": "assets/feature-voz.webp"
        },
        {
          "page": "corpo",
          "title": "Body 与形象",
          "text": "Live2D、VRM、动作及表情事件。",
          "image": "assets/feature-corpo.webp"
        },
        {
          "page": "pet",
          "title": "桌面 Pet",
          "text": "Electron 窗口与内嵌字幕。",
          "image": "assets/feature-pet.webp"
        },
        {
          "page": "mundo-3d",
          "title": "3D 世界",
          "text": "Godot、Bento、Kenny 和 TikTok 3D。",
          "image": "assets/feature-mundo-3d.webp"
        },
        {
          "page": "plataformas",
          "title": "平台集成",
          "text": "平台以及单独发行的项目。",
          "image": "assets/feature-plataformas.webp"
        }
      ]
    }
  }
};
