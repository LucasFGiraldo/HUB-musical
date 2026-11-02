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

## Deploy
Faça commit e rode `scripts/deploy.sh` no Git Bash. Ele envia o último commit para `~/hub-musical` no servidor e reinicia o serviço de usuário `hub-musical` (porta 8888).
A interface fica em `http://hub-automacoes.local:8888`.

Login do Spotify (só na primeira vez ou se o token for perdido): abra um túnel com
`ssh -L 8888:127.0.0.1:8888 lucas@hub-automacoes.local` e acesse `http://127.0.0.1:8888/auth/login`.

## Segredos
Client ID, tokens e dados locais ficam fora do repositório (`.gitignore`).
