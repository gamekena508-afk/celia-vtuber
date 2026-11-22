# Contrato público proposto — áudio aprovado (OmniVoice V3)

**Status: especificação de API, não funcionalidade implantada no repositório público.**

Este repositório é a superfície pública da Célia. O cliente de fala poderá solicitar áudio a um serviço local/privado por contrato versionado. Não hospeda mapas de seeds, amostras vocais, checkpoints, pesos, hashes de referência, credenciais nem logs de voz.

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

**Aviso de compatibilidade:** o motor OmniVoice presente neste checkout contém a rota legada de seed 7319 e 32 steps. O contrato V3 será ativado somente com uma implementação e validação explícitas; este documento não afirma que a atualização já foi aplicada.
