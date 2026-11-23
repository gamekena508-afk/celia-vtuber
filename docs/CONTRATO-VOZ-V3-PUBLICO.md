# Contrato público — áudio aprovado (OmniVoice V3)

**Status: cliente de produção implementado neste checkout e teste simulado aprovado; exige Central IA privada atualizada, sem implantação pública remota nesta execução.**

Este repositório é a superfície pública da Célia. O cliente de fala solicita áudio aprovado ao serviço local/privado da Central IA; o gerador, o ranking de seeds e a biometria ficam fora deste repositório. Não hospeda mapas de seeds, amostras vocais, checkpoints, pesos, hashes de referência, credenciais nem logs de voz.

## Situação do contrato e limites do repositório público

A implementação do cliente foi sincronizada com o perfil atual do backend local: **OmniVoice V2, 10 passos, seleção de seeds pelo servidor e aceite biométrico obrigatório**. No ambiente privado de desenvolvimento, o fluxo de geração foi validado com síntese real; isso não constitui hospedagem de um serviço público de voz.

O código público aceita `CELIA_VOICE_REFERENCE_WAV` como configuração externa e não depende mais do caminho legado de `coqui-tts-server`. Nenhuma referência vocal, áudio gerado, mapa de seeds, histórico de score ou configuração pessoal deve ser incluído neste repositório.

Os campos `APPROVED` / `NO_APPROVED_AUDIO` expressam o contrato de aceite; o serviço privado é responsável por pontuação, tentativas e gravação do WAV aprovado. O cliente não deve tentar contornar reprovações.

## Pedido (abstrato)
- `request_id`: identificador opaco para rastreio;
- `voice_id`: perfil lógico autorizado (não revela o arquivo de voz);
- `text`, `language`, `emotion`: conteúdo e contexto;
- `policy_version`: `omnivoice-v2-10steps-seedmap-v1`.

## Resposta (abstrata)
- `status: APPROVED | NO_APPROVED_AUDIO | ERROR`;
- `approved_audio`: referência privada ao WAV **somente quando APPROVED**;
- `attempts`: entre 0 e 4;
- `steps`: 10 quando houve síntese;
- `quality`: informação limitada e não sensível de qualidade (ex.: o áudio passou por checagem biométrica);
- `trace_id`: identificador para observabilidade sem expor dados privados.

A seleção de seeds, histórico positivo/negativo, score e limiares de similaridade residem no backend privado. A aplicação pública não deve implementar nem burlar o gate de similaridade. Se não houver WAV aprovado, não reproduzir um WAV de tentativa, não inserir outra voz nem encaminhar arquivo temporário ao Discord/stream/Pet.

## Divisão futura
- **Código público:** cliente/SDK, contratos, mocks, testes unitários, interface e documentação sem informações de produção.
- **Célia V02/CLI privado:** orquestração de turnos, gestão de fallback de silêncio/erro, telemetria e runtime; não está contido neste repositório.
- **Central IA local:** motor de síntese, biometria, memória de seeds, histórico de aprovações e logs privados.

**Aviso de implantação:** a cópia local da rota de produção foi migrada para 10 steps e seed adaptativa (sem fallback sem biometria); não confundir com os presets manuais do laboratório nem com serviços antigos ainda abertos. O CLI privado e a Central IA devem executar versões compatíveis.
