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

## Microfone (fase 2)
`backend/app/listener.py` lê o microfone com `arecord` (dispositivo em `HUB_MIC_DEVICE`, padrão `plughw:1,0`).
A cada segundo, o YAMNet avalia os últimos 2 s; com música por alguns segundos seguidos, o Shazam identifica a faixa.
Só roda quando o Spotify não está tocando. Parâmetros em `/api/settings` (botão "Parâmetros" na tela).

O modelo `backend/models/yamnet.tflite` e o `yamnet_class_map.csv` vêm do projeto
[now-playing](https://github.com/maurocastermans/now-playing) (MIT), que por sua vez usa o YAMNet do Google.

## Modo quiosque (tela do Dell)
**Em espera:** não está instalado; será ativado quando a tela final chegar, com `bash ~/hub-musical/deploy/install-kiosk.sh`.

`deploy/kiosk.sh` abre a tela em tela cheia com `cage` + `chromium --kiosk`. Ele roda no tty1, com login automático,
a partir do `~/.bash_profile` do usuário `lucas`. Para sair do quiosque pelo SSH: `pkill cage` (ele volta sozinho em
3 s; para parar de vez, comente a linha no `~/.bash_profile` e rode `pkill -f kiosk.sh`).

## Segredos
Client ID, tokens e dados locais ficam fora do repositório (`.gitignore`).
