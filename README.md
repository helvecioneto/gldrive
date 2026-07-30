# gldrive

Upload, download e sincronização de arquivos e pastas do Google Drive no estilo
`scp`, direto do terminal. Login via OAuth no navegador (com cliques), token
salvo localmente — você só faz login uma vez.

Caminhos remotos usam o prefixo `gd:`, como o `remoto:` do scp:

```
gldrive cp relatorio.pdf gd:docs/
```

## Instalação

Direto do GitHub:

```bash
pip install git+https://github.com/helvecioneto/gldrive.git
```

Para atualizar depois:

```bash
pip install -U --force-reinstall git+https://github.com/helvecioneto/gldrive.git
```

Ou, para desenvolver:

```bash
git clone https://github.com/helvecioneto/gldrive.git
cd gldrive
pip install -e .
```

## Configuração inicial (uma vez só)

Basta rodar:

```bash
gldrive login
```

Na primeira vez, o comando mostra o link do Google Cloud Console com o passo a
passo para criar o OAuth client (tipo **Desktop app**) e fica aguardando as
credenciais. Ele detecta automaticamente o que você colar:

- o **caminho** do JSON baixado;
- o **conteúdo** do JSON colado direto no terminal;
- o **Client ID** ou o **Client secret** mostrados na tela do console — ao
  colar um, ele pede o outro e monta a configuração sozinho.

Se preferir, aponte o arquivo de uma vez:

```bash
gldrive login --secrets ~/Downloads/client_secret_XXXX.json
```

Em seguida o navegador abre, você autoriza com alguns cliques e pronto. O token fica salvo
em `~/.config/gldrive/` e é renovado automaticamente — os próximos comandos não
pedem login. Em servidores sem navegador, use `gldrive login --no-browser`.

## Uso

```bash
# Listar
gldrive ls gd:                     # raiz do Drive
gldrive ls -l gd:backup            # listagem longa (tamanho, data)

# Upload (local -> Drive)
gldrive cp relatorio.pdf gd:docs/
gldrive cp -r ./dados gd:backup/dados

# Download (Drive -> local)
gldrive cp gd:docs/relatorio.pdf .
gldrive cp -r gd:backup/dados ./dados

# Sincronização (uma via, incremental por md5; nunca apaga nada)
gldrive sync ./fotos gd:fotos              # avulsa: local -> Drive
gldrive sync gd:fotos ./fotos              # avulsa: Drive -> local
gldrive sync ./fotos gd:fotos --watch      # fica sincronizando (a cada 60s)

# Sincronizações permanentes (salvas em ~/.config/gldrive/syncs.json)
gldrive sync add ./fotos gd:fotos          # registra o par (nome automático: "fotos")
gldrive sync add ./dados gd:backup --name backup-dados
gldrive sync list                          # lista os pares salvos e a última execução
gldrive sync run                           # roda todos os pares salvos
gldrive sync run fotos                     # roda só um (por nome ou número)
gldrive sync run --watch --interval 300    # roda todos em loop
gldrive sync remove fotos                  # remove do registro (por nome ou número)

# Modo Dropbox: sincronização contínua das pastas salvas (e SÓ delas)
gldrive sync watch                         # roda no terminal até Ctrl+C
gldrive sync watch --interval 60           # checa o lado do Drive a cada 60s
gldrive service install                    # instala como serviço: inicia no login
gldrive service status                     #   e roda para sempre em segundo plano
gldrive service uninstall                  # para e remove o serviço

# Outros
gldrive mkdir gd:backup/2026
gldrive whoami
gldrive logout           # revoga o acesso no Google e apaga o token
gldrive logout --all     # idem, e apaga também o OAuth client salvo
```

No modo contínuo (`sync watch` / serviço), mudanças nas pastas **locais** são
detectadas na hora (eventos do sistema de arquivos, via watchdog) e enviadas
após alguns segundos de calmaria; mudanças no **Drive** são detectadas na
checagem periódica (`--interval`, padrão 300s). Só as pastas registradas com
`sync add` são monitoradas — nunca o Drive inteiro nem o computador inteiro.
`sync add`/`remove` valem na hora, sem reiniciar o serviço. Funciona em macOS
(launchd), Linux (systemd) e Windows (Task Scheduler).

Pastas são criadas automaticamente no destino quando não existem. Arquivos com
mesmo conteúdo (md5) são pulados; arquivos alterados são atualizados no lugar
(sem duplicar no Drive). Arquivos nativos do Google (Docs, Sheets...) não têm
conteúdo binário e são pulados no download.

## Uso como biblioteca

```python
from pathlib import Path
from gldrive import GDrive, RemotePath, get_credentials

drive = GDrive(get_credentials())
folder_id = drive.mkdirs(RemotePath.parse("gd:backup/dados"))
drive.sync_up(Path("./dados"), folder_id)
```

## Notas

- Escopo OAuth: `https://www.googleapis.com/auth/drive` (leitura e escrita).
- Config em `~/.config/gldrive/` (sobrescreva com a variável `GLDRIVE_CONFIG_DIR`).
- `sync` é uma via e nunca deleta arquivos, nem no destino nem na origem.
