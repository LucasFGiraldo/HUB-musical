# HUB Musical

Tela que mostra a música que está tocando (capa, título, artista, duração), integrando Spotify e, depois, reconhecimento de áudio ambiente (vinil).

## Arquitetura
- **Servidor** (Dell XPS, Debian 13, `hub-automacoes.local`): backend Python (FastAPI) que lê o Spotify e, na fase 2, detecta e identifica música pelo microfone.
- **Tela**: página web em tela cheia, atualizada por WebSocket.

## Fases
1. Spotify: música atual + painel de histórico/parâmetros.
2. Reconhecimento por microfone (YAMNet filtra música; Shazam identifica).
3. Entrada de linha para vitrola.

## Estrutura
- `backend/` servidor Python
- `frontend/` interface web
- `.env.example` modelo de configuração (o `.env` real nunca vai para o Git)

## Segredos
Client ID, tokens e dados locais ficam fora do repositório (`.gitignore`).
