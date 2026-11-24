# Contrato de isolamento de áudio

Diretriz de segurança de captura para o runtime e suas superfícies de apresentação. O núcleo e a interface agora contêm a proteção de captura e a política de proprietário único. A homologação física ainda é necessária; o pacote público não inclui os adaptadores Discord nem o Electron nativo.

- O aplicativo controla apenas suas próprias sessões de captura. Nunca modifica o microfone global, dispositivo padrão, serviços ou permissões do sistema operacional.
- A janela principal pode capturar áudio após autorização explícita. Pet, cena e saída de transmissão são somente apresentação; não abrem microfone físico, inclusive quando recebem estados antigos.
- Quando uma integração de voz externa estiver disponível, o roteador deve escolher uma única origem de ASR. Na indisponibilidade da integração, a janela local pode assumir se autorizada. O mute manual prevalece.
- A animação de um mascote reutiliza intensidade RMS do áudio existente, sem captura adicional nem nova transcrição. Uma integração com identificação de interlocutores deve manter os níveis separados.
- Mudanças de fonte liberam apenas as tracks do aplicativo, invalidam buffers antigos e não reabrem captura silenciosamente após mute.
- Testes de regressão devem provar ausência de captura nas superfícies visuais, exclusividade, liberação e retomada autorizada, compatibilidade com estados legados e ausência de interferência em outros aplicativos. Testes simulados não substituem ensaio físico.
- O pacote público pode não distribuir o wrapper desktop nem adaptadores externos. Verifique o escopo do release antes de usar tais funções.

## Verificação desta alteração

29 testes Python e 4 verificações Node passaram no checkout público, incluindo bloqueio de captura em Pet, exclusividade, liberação e retomada de tracks simuladas. Uma dependência opcional de Minecraft ausente foi tratada como indisponibilidade da HUD. O ensaio de microfone físico e o adaptador Discord não foram homologados pelo pacote público.

## Diagnóstico seguro

Primeiro confirme a versão do servidor e dos scripts carregados. Verifique a entrada local, VAD, ASR e eventos visuais separadamente. Se outro aplicativo perder acesso ao microfone, interrompa a captura da aplicação e investigue a sessão de mídia; não altere configurações globais para contornar o problema. Não declare recuperação sem ensaio real.
