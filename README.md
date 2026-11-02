# HUB Musical + Literário

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

## Segurança
- **Painel:** salvar ajustes exige um PIN (4 a 8 números), criado no primeiro "Salvar". Vale 30 dias por aparelho;
  5 erros seguidos bloqueiam aquele aparelho por 5 min. Esqueceu: `rm ~/hub-musical/data/painel_pin.json` e crie outro.
- **Login do Spotify** (`/auth/login`, `/callback`) só funciona pelo túnel SSH, nunca pela rede.
- O servidor recusa endereços que não sejam `.local`, `localhost` ou IP da rede local (proteção contra DNS rebinding)
  e manda cabeçalhos de segurança (CSP, sem iframe).
- **Sistema** (`deploy/seguranca.sh`, rodado pelo Lucas com sudo): firewall nftables (só a rede local, portas em
  `portas_lan` no `/etc/nftables.conf`), SSH só por chave e sem root, atualizações de segurança automáticas.
- Automação nova: um serviço de usuário próprio, numa porta própria, acrescentada em `portas_lan`.

## Leituras (estante no painel)
A aba **Leituras** do painel é a estante: busca no Open Library (título, autor, ISBN ou foto do código de barras),
escolha da edição, situação (Quero ler, Lendo, Lido, Relendo, Pausado, Abandonado), marcadores (Tenho, Favorito,
Desejado, Emprestado), página atual, datas, nota e observações. As capas ficam em `data/covers/` (foto, imagem ou link).
Código em `backend/app/shelf.py` (estante, tabelas `books`, `book_reads`, `book_progress`) e `catalog.py` (Open Library,
capas, código de barras). Escritas pedem o PIN. Exportação em CSV nos Ajustes.

## Banco de dados
SQLite em `~/hub-musical/data/hub.db` no Dell (não é Supabase; é um arquivo local). Consultar pelo SSH:
`sqlite3 -readonly ~/hub-musical/data/hub.db "select * from plays order by id desc limit 10"`.
Ou copiar para o computador (`scp hub:hub-musical/data/hub.db .`) e abrir no DB Browser for SQLite.
O SSH só aceita chave: no Lenovo, `~/.ssh/config` define o apelido `hub` com a chave `~/.ssh/hub_ed25519`.

## Segredos
Client ID, tokens, PIN e dados locais ficam fora do repositório (`.gitignore`).
