# 🤖 AGENTS.md — AI Context, Operational Guidelines & System Truth

> **Aviso para Agentes de IA (Claude, Antigravity, Copilot, Cursor, OpenAI, etc.):**  
> Este documento é a **Fonte Única da Verdade (Single Source of Truth - SSOT)** sobre a arquitetura, regras de negócio, engenharia reversa da API do TiqueTaque e guardrails operacionais deste repositório (`tique-taque-sync`). Leia atentamente antes de refatorar código, sugerir comandos, alterar a máquina de estados ou criar manifests.

---

## 🎯 1. Escopo & Filosofia do Projeto

O **`tique-taque-sync`** é um **aplicativo pessoal instalável** (projeto pessoal, fora do escopo da wiki de DevOps da Cubos) para usuários do ecossistema de ponto eletrônico **TiqueTaque**.

O alvo primário é a **máquina do próprio usuário**: instala com `pip`, configura com `tiquetaque-sync setup` ou pela tela `/settings`, e a partir daí é operado por uma **janela desktop (Tkinter)** aberta por atalho — sem terminal. Docker e Kubernetes continuam suportados, mas são caminhos secundários.

**Divisão de responsabilidades da interface:** a janela Tkinter é apenas o *painel de controle* (ligar/desligar o serviço, resumo curto, autostart, atalhos para o navegador). O *overview* completo da jornada é e continua sendo a página web em `http://127.0.0.1:8000`. Não reimplemente gráficos, timeline ou formulários em Tk.

O serviço monitora em tempo real a jornada, prevê dinamicamente o horário de encerramento, faz cumprir regras trabalhistas (Art. 71 da CLT) e dispara notificações ricas no **Telegram** e no **Slack**.

**Princípio de design:** qualquer coisa que o usuário precise ajustar no dia a dia (credenciais, canais, momentos dos avisos, autostart) tem que ser alcançável pela tela de configurações — editar arquivo à mão é fallback, não o caminho principal.

---

## 🏗️ 2. Arquitetura do Sistema e Módulos

O pacote Python se chama **`tiquetaque_sync`** (não `src`) — é o nome importável e instalável, referenciado pelos entry points do `pyproject.toml`.

```text
tiquetaque_sync/
├── __main__.py             # `python -m tiquetaque_sync` → CLI
├── cli.py                  # start / setup / gui / shortcut / autostart / config / test / open
├── gui.py                  # Janela Tkinter (painel de controle, stdlib apenas)
├── shortcut.py             # Atalho no Menu Iniciar / .desktop / .command
├── paths.py                # Diretórios por usuário (Windows, macOS, Linux)
├── store.py                # Leitura/escrita de config.json + máscara de segredos
├── config.py               # Pydantic Settings (env > .env > config.json > defaults)
├── autostart.py            # Registro no login do SO (Startup / LaunchAgent / systemd)
├── runtime.py              # AppRuntime: constrói e recarrega engine/client/scheduler
├── main.py                 # FastAPI app, rotas REST, painel, /settings e lifespan
├── tiquetaque/
│   ├── client.py           # Cliente HTTP assíncrono (httpx) para a API do TiqueTaque
│   ├── models.py           # Modelos de dados Pydantic da API TiqueTaque
│   └── security_hash.py    # Algoritmo criptográfico SHA-256 do header X-Check e twd
├── engine/
│   ├── database.py         # SQLite em modo WAL com deduplicação de batidas
│   ├── workday.py          # Máquina de estados (5 estágios, CLT 6h, contagens regressivas)
│   └── scheduler.py        # Loop duplo: Poller APScheduler (2-3m) + Fast Ticker (15s)
├── notifiers/
│   ├── base.py             # Interface abstrata BaseNotifier
│   ├── dispatcher.py       # Despachante assíncrono multi-canal
│   ├── telegram.py         # Notificador Telegram Bot (HTML parse_mode)
│   └── slack.py            # Notificador Slack Webhook (Block Kit + conversor mrkdwn)
└── web/
    ├── templates/index.html    # Painel em Dark Glassmorphism
    ├── templates/settings.html # Tela de configurações
    └── static/                 # CSS tokens/animations e JavaScript

packaging/
├── entry.py                # Entry point do executável congelado
├── tiquetaque-sync.spec    # Receita do PyInstaller
├── make_icon.py            # Gera icon.ico (só stdlib) — rode se o desenho mudar
└── icon.ico                # Ícone versionado do .exe
```

### 2.1. Entry points (`pyproject.toml`)

| Script | Alvo | Uso |
|---|---|---|
| `tiquetaque-sync` | `tiquetaque_sync.cli:main` | Uso normal no terminal |
| `tiquetaque-syncw` | `tiquetaque_sync.cli:main` (gui-script) | Autostart no Windows, sem janela de console |
| `tiquetaque-sync-gui` | `tiquetaque_sync.cli:main_gui` (gui-script) | Alvo do atalho: sem argumento, abre a janela |

---

## ⚙️ 3. Configuração: fontes, precedência e persistência

### 3.1. Precedência (maior primeiro)

```text
variáveis de ambiente  >  .env (CWD)  >  config.json (por usuário)  >  defaults
```

Implementada em `Settings.settings_customise_sources` (`config.py`) via a fonte customizada `UserConfigSettingsSource`. **Não inverta essa ordem**: container/K8s dependem do ambiente vencer; o desktop não define nenhuma variável, então `config.json` governa.

### 3.2. Localização dos arquivos (`paths.py`)

| Sistema | `config.json` | Dados (SQLite) |
|---|---|---|
| Windows | `%APPDATA%\TiqueTaqueSync\` | `%LOCALAPPDATA%\TiqueTaqueSync\data\` |
| macOS | `~/Library/Application Support/TiqueTaqueSync/` | `.../TiqueTaqueSync/data/` |
| Linux | `$XDG_CONFIG_HOME/tiquetaque-sync/` | `$XDG_DATA_HOME/tiquetaque-sync/` |

`TIQUETAQUE_SYNC_HOME` sobrescreve tudo (usado por testes, instalações portáteis e containers).

### 3.3. Regras do `store.py`

- **`EDITABLE_KEYS`** é a allowlist do que a UI pode gravar. Chaves fora dela são descartadas na leitura e na escrita — isso impede que um `config.json` adulterado injete configuração arbitrária (ex: `api_secret_key`).
- **`SECRET_KEYS`** (`tiquetaque_code`, `telegram_bot_token`, `slack_webhook_url`) nunca são devolvidos pela API: `store.mask()` os troca por `<key>_is_set: bool`.
- Escrita é atômica (arquivo temporário + `os.replace`) e, em POSIX, o arquivo recebe `0600`.
- Ao salvar, campo de segredo em branco significa **manter o valor atual** — nunca apagar.

### 3.4. Recarga em runtime (`runtime.py`)

`PUT /api/settings` → `store.save()` → `runtime.reload()`, que para o scheduler, chama `reload_settings()` (muta o singleton `settings` no lugar, preservando os `from .config import settings` espalhados), reconstrói db/client/dispatcher/engine/scheduler e religa o scheduler se ele já estava rodando. `seed_status()` carrega o último status para o painel não piscar vazio.

**Somente `host` e `port` exigem reinício** — a resposta marca `restart_required` nesse caso.

---

## 🔬 4. Engenharia Reversa da API TiqueTaque (Mapeamento Vital)

A API do TiqueTaque foi inspecionada e mapeada a partir do cliente Web oficial (`https://tiquetaque.app`):

### 4.1. Autenticação e Obtenção de Token
* **Host**: `https://api.tiquetaque.com`
* **Endpoint**: `POST /employees/code/verify`
* **Payload**:
  ```json
  {
    "email": "usuario@empresa.com",
    "sms_verification_code": "1234",
    "source": "web"
  }
  ```
* **Resposta de Sucesso**:
  Retorna um objeto contendo o token JWT do usuário (`response.data.token`) e o ID do funcionário (`response.data._id`). O token deve ser enviado nos headers subsequentes:
  ```http
  Authorization: Bearer <token>
  ```

### 4.2. Consulta de Batidas do Dia
* **Endpoint**: `GET /employees/day-records?employee={employeeId}&period=current`
* **Estrutura de Batidas**:
  As batidas vêm na chave `data[0].times` como uma lista de strings de horário (ex: `["08:02", "12:00", "13:00"]`).

### 4.3. Algoritmo Criptográfico de Assinatura (`X-Check`)
Para registrar pontos via API (`POST /employees/day-records/add-times`), a aplicação cliente deve gerar o cabeçalho de assinatura `X-Check`:
$$\text{X-Check} = \text{SHA256}(\text{reverse}(HH:mm) + \text{nome\_completo\_minusculo} + DD\text{-}MM\text{-}YYYY + \text{employeeId})$$

* Implementado fielmente em [`tiquetaque_sync/tiquetaque/security_hash.py`](tiquetaque_sync/tiquetaque/security_hash.py):
  1. `reverse(HH:mm)`: Por exemplo, `"14:51"` torna-se `"15:41"`.
  2. `nome_completo_minusculo`: `full_name.strip().lower()`.
  3. `date_str`: Formato estrito `DD-MM-YYYY`.
  4. `employeeId`: String hexadecimal de 24 caracteres do MongoDB.

---

## ⚙️ 5. Máquina de Estados da Jornada & Regras CLT

A jornada diária é processada pela classe `WorkdayEngine` em [`tiquetaque_sync/engine/workday.py`](tiquetaque_sync/engine/workday.py) e categorizada em 5 estágios:

| Estágio (`WorkdayStage`) | Condição de Batidas | Descrição & Ações |
|---|---|---|
| `NOT_STARTED` | 0 batidas | Nenhum ponto batido no dia. O painel exibe 0% e aguarda primeira entrada. |
| `WORKING_MORNING` | 1 batida (ex: 08:00) | Período da manhã. Monitora tempo contínuo de trabalho e limite legal CLT. |
| `LUNCH_BREAK` | 2 batidas (ex: 08:00, 12:00) | Intervalo intrajornada. Monitora duração do almoço (meta padrão: 60 min). |
| `WORKING_AFTERNOON` | 3 batidas (ex: 08:00, 12:00, 13:00) | Turno da tarde. Recalcula o horário exato de término para completar a meta de 8h de trabalho líquido. |
| `COMPLETED` | 4+ batidas (ou meta atingida) | Jornada diária encerrada ou com batidas pares completas. Dispara resumo do dia. |

### ⚖️ Regra de Ouro da CLT — Artigo 71 (Trabalho Contínuo $\le$ 6h)
A legislação trabalhista brasileira (CLT Art. 71) proíbe que um trabalhador sob jornada padrão exceda 6 horas ininterruptas de trabalho sem conceder intervalo para repouso ou alimentação.
* O motor calcula o tempo contínuo desde a última entrada (`continuous_worked_seconds`).
* **Aviso Preventivo**: Disparado **10 minutos** antes de atingir 6 horas (configurável).
* **Aviso Crítico/Final**: Disparado **1 minuto** antes de completar 6 horas contínuas (configurável).

### ⏱️ Avisos de Almoço e Fim de Expediente
* **Almoço (1h)**: Aviso preventivo com 10 min de antecedência e aviso final 1 min antes de completar 1h.
* **Fim de Expediente (8h)**: Aviso preventivo com 15 min de antecedência e aviso final 1 min antes do encerramento previsto.

**Todos esses valores são configuráveis pelo usuário** em `/settings` ou no wizard. Nenhum deles pode voltar a ser hardcoded no engine, no template ou no JavaScript — o painel lê a meta via `/api/config` e `target_seconds`.

---

## 🔄 6. Arquitetura de Loop Duplo (Scheduler Dual-Frequency)

Para garantir precisão cirúrgica de alertas sem sobrecarregar ou correr o risco de bloqueio/rate-limit na API do TiqueTaque:

1. **Poller Lento Remoto (180s = 3 minutos)**:
   - Consulta `GET /employees/day-records` na API remota.
   - Atualiza o banco SQLite local com novas batidas feitas pelo celular ou tablet.
2. **Fast Ticker Local (15s)**:
   - Roda a cada 15 segundos **somente em memória e no banco local**.
   - Avalia os contadores regressivos (ex: faltam 60 segundos para o almoço acabar? Faltam 60 segundos para as 6h contínuas?).
   - Dispara os alertas no minuto exato sem gerar requisições HTTP externas desnecessárias.
3. **Userscript Instant Hook (Tampermonkey)**:
   - Quando o usuário clica no botão "Registrar ponto" no navegador oficial, o userscript intercepta a ação e faz um `POST /api/webhook/clock-in` local imediatamente, sincronizando o sistema em tempo real ($< 1$s).

---

## 🚀 7. Autostart (`autostart.py`)

| Sistema | Mecanismo | Caminho |
|---|---|---|
| Windows | `.vbs` na pasta Startup, com `WScript.Shell.Run(..., 0, False)` para não abrir console | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TiqueTaqueSync.vbs` |
| macOS | LaunchAgent + `launchctl load -w` | `~/Library/LaunchAgents/io.github.resendegu.tiquetaque-sync.plist` |
| Linux | Unit systemd **do usuário** + `systemctl --user enable --now`; fallback `.desktop` XDG quando não há systemd | `~/.config/systemd/user/tiquetaque-sync.service` |

Regras invioláveis:
1. **Nunca exigir privilégio de administrador/root.** Tudo mora no perfil do usuário. Não escreva em `HKLM`, `/etc/systemd/system` ou `/Library/LaunchDaemons`.
2. O comando registrado é sempre `... start --no-browser` — o serviço sobe em background, sem roubar o foco no login.
3. `_launch_command()` prefere o console script no PATH e só cai para `python -m tiquetaque_sync` quando rodando de um checkout.

---

## 🪟 8. Janela desktop (`gui.py`) e atalhos (`shortcut.py`)

Regras da janela:

1. **Somente biblioteca padrão.** Tkinter e nada mais — sem PyQt, sem pywebview. O import é protegido e levanta `TkinterUnavailable` com instrução de instalação (`python3-tk`), porque algumas distros Linux empacotam o Tk à parte.
2. **Nunca bloquear o loop do Tk.** A sondagem HTTP (`probe_service`) roda em thread e entrega o resultado por `queue.Queue`; o loop do Tk só consome (`_drain_loop`, 200 ms) e re-agenda o probe (`_refresh`, 2 s). Nenhuma chamada de rede pode acontecer direto num callback de botão.
3. **A janela não é dona do serviço.** Ela sobe um processo filho com `autostart.launch_command(windowless=True)`; fechar a janela **não** encerra o serviço, e ela se recusa a matar uma instância que não subiu (autostart/terminal) — apenas explica.
4. **Estados da jornada** vêm de `WorkdayStage`; `STAGE_LABELS` precisa cobrir todos (há teste garantindo isso).

Atalhos (`shortcut.py`): `.lnk` via `WScript.Shell` (Windows, sem dependência COM extra), `.desktop` em `~/.local/share/applications` (Linux), `.command` em `~/Applications` (macOS). Sempre no perfil do usuário e apontando para `tiquetaque-sync-gui`.

---

## 💬 9. Notificadores: Regras de Formatação e Compatibilidade

Os notificadores em [`tiquetaque_sync/notifiers/`](tiquetaque_sync/notifiers/) operam de maneira desacoplada:

* **Telegram (`telegram.py`)**:
  - Utiliza `parse_mode="HTML"`.
  - Suporta tags `<b>`, `<i>`, `<code>`, e emojis nativos.
* **Slack (`slack.py`)**:
  - Utiliza Incoming Webhooks com a API **Block Kit**.
  - **Atenção:** O Slack não renderiza tags HTML cruas como `<b>` e `<i>` em blocos `mrkdwn`.  
    Por isso, existe a função `html_to_mrkdwn(text)` que converte automaticamente tags `<b>` para `*negrito*` e `<i>` para `_itálico_`.  
    **Nunca remova essa sanitização.**

---

## 🛡️ 10. Guardrails & Regras de Ouro para Agentes de IA

1. **Zero Secret Leaks**: Nunca escreva tokens JWT, chaves de bot, chat IDs ou webhooks em arquivos versionados. Sempre utilize variáveis de ambiente, `config.json` (não versionado) ou templates `.example`. A API nunca devolve segredo em claro — mantenha `store.mask()` no caminho de resposta.
2. **Preservação de Testes**: Toda alteração no motor de cálculo (`workday.py`), no banco ou na camada de configuração deve ser acompanhada de `python tests/run_tests.py`. Todos os testes devem passar.
3. **Testes não tocam o ambiente real**: `tests/_bootstrap.py` aponta `TIQUETAQUE_SYNC_HOME` para um diretório temporário e faz `chdir` para dentro dele **antes** de qualquer import do pacote (importar `tiquetaque_sync.main` já constrói o runtime e o SQLite). Importe-o como primeira linha de qualquer módulo de teste novo. Testes também não devem depender de rede.
4. **Facilidade acima de tudo**: o usuário-alvo não quer terminal. Ao adicionar uma opção, pergunte "isso aparece na tela de configurações?". Se a resposta for não, provavelmente falta trabalho — adicione a chave em `store.EDITABLE_KEYS`, no `SettingsPayload` e no formulário. Se for uma ação (não uma configuração), pergunte se cabe um botão na janela Tkinter.
5. **Compatibilidade multiplataforma**: o app roda em Windows, macOS e Linux. Nada de caminhos com `/` hardcoded, `os.geteuid()` sem guarda, ou emoji impresso sem passar por `_say()` (o console Windows em cp1252 quebra com `UnicodeEncodeError`).
6. **Compatibilidade de Arquitetura de Processador**: O Dockerfile é multi-arch (`linux/amd64` e `linux/arm64`). Não utilize pacotes nativos C não portáveis que quebrem em nós ARM.
7. **Volume Efêmero vs Persistência**: A aplicação tolera reinicializações com `emptyDir: {}`, pois re-sincroniza o dia corrente da API ao iniciar. Caso persistência seja requerida, use `PersistentVolumeClaim`.
8. **Projeto pessoal**: este repositório **não** deve ser registrado na wiki de DevOps da Cubos nem tratado como serviço corporativo.

---

## 📦 11. CI/CD e publicação da imagem

Workflows em `.github/workflows/`:

| Workflow | Dispara | Responsabilidade |
|---|---|---|
| `ci.yml` | push `main`, PR | Suíte de testes em Python 3.11/3.12/3.13 + `pip install .` em Linux/Windows/macOS, verificando entry points e arquivos de template empacotados |
| `docker.yml` | push `main`, tag `v*.*.*`, PR que toca a imagem | Build multi-arch (`linux/amd64`, `linux/arm64`) e push para `ghcr.io/resendegu/tique-taque-sync`, com proveniência e SBOM |
| `release.yml` | `release: published`, manual | Compila `TiqueTaqueSync.exe` (PyInstaller), roda smoke test real do executável, gera `.sha256`, anexa aos assets e completa as notas da release |

Regras:

1. **A imagem é pública e de terceiros.** Ela é baixada por pessoas que não leram este repositório — não introduza nela nada que exija configuração manual pós-`docker run`. Os defaults de container vivem como `ENV` no Dockerfile (`HOST=0.0.0.0`, `DATA_DIR=/app/data`, `TIQUETAQUE_SYNC_HOME=/app/config`).
2. **Não quebre o arm64.** O build roda em QEMU sobre runner amd64; dependências que exijam compilação nativa pesada estouram o tempo do job. Mantenha as rodas puras/portáveis.
3. **Tags:** `latest` só sai da branch default; versões vêm de tags `vX.Y.Z` (geram `X.Y.Z`, `X.Y`, `X`); `sha-<short>` sempre. Não publique `latest` a partir de branch de feature.
4. **Em PR o job builda mas não dá push** — nenhum segredo de registry é exposto a fork.
5. **O teste de empacotamento do `ci.yml` é intencional**: a falha mais provável deste projeto é o `pip install` sem os templates/CSS/JS (`[tool.setuptools.package-data]`). Ao adicionar um arquivo estático novo, acrescente-o ao glob do `pyproject.toml` **e** à lista verificada no workflow.
6. **Primeira publicação exige um passo manual** (documentado no README): pacotes GHCR nascem privados; a visibilidade pública é definida uma vez nas *Package settings*.

Os manifests em `k8s/` já apontam para a imagem publicada. `kustomization.yaml` referencia `03-secret.yaml` (não versionado) — o `.example` é só modelo.

---

## 🪟 12. Executável Windows (PyInstaller)

`packaging/tiquetaque-sync.spec` gera `dist/TiqueTaqueSync.exe`: arquivo único, sem console,
que **abre a janela quando executado sem argumentos e age como CLI quando recebe argumentos**
(`packaging/entry.py` → `cli.main_gui`).

### Armadilhas do modo congelado — não regrida nestes pontos

1. **`sys.executable` deixa de ser o Python.** Por isso `autostart._launch_command()` e
   `shortcut._gui_command()` checam `autostart.is_frozen()` e devolvem `[sys.executable, ...]`,
   fazendo o .exe se re-invocar. Qualquer código novo que queira subir o serviço deve usar
   `autostart.launch_command()`, nunca montar `python -m ...` na mão.
2. **`python -m tiquetaque_sync` não existe dentro do .exe** — e por isso `_launch_workdir()`
   devolve `None` quando congelado (não há checkout para apontar).
3. **Os templates não ficam ao lado do módulo.** `main._base_dir()` usa `sys._MEIPASS` quando
   congelado. Ao mover arquivos de `web/`, ajuste também `datas` no `.spec`.
4. **uvicorn e apscheduler importam por nome em runtime**; o `.spec` os inclui via
   `collect_submodules`. Dependência nova que faça import dinâmico precisa entrar em
   `hiddenimports`, senão o .exe compila e quebra só em execução.
5. **Sem console, não há `stdin` nem `stdout` — e isso mata o servidor.** Quando ninguém
   redireciona a saída (duplo clique, ou o filho lançado pela janela), a configuração de
   logging do uvicorn falha *antes* do bind: processo vivo, porta fechada, nenhuma mensagem.
   Por isso `cli.main()` chama `_attach_log_sink()` quando congelado, mandando stdout/stderr
   para `<data_dir>/tiquetaque-sync.log`. **Não remova isso**, e não teste o .exe só com a
   saída redirecionada — mascara exatamente essa falha. `cmd_setup` recusa rodar sem `stdin`;
   não introduza `input()` fora do assistente.
6. **`multiprocessing.freeze_support()`** fica na primeira linha do entry point — sem ele um
   processo filho reabriria a janela.
7. **Sem UPX** no `.spec`: compressão dispara falso-positivo de antivírus.

### Notas da release

O último passo do `release.yml` acrescenta ao corpo da release as instruções de download, o
checksum, as alternativas de instalação e a mensagem do último commit. Duas invariantes:

1. **Nunca sobrescrever o texto do mantenedor.** O bloco gerado fica abaixo de
   `<!-- gerado automaticamente pelo workflow Release -->`; o que existe acima é preservado.
2. **Idempotente.** Se o marcador já existe, o corpo é cortado nele e o bloco é regerado —
   re-executar o workflow atualiza, não duplica.

Detalhe de PowerShell: dentro do here-string `@"..."@` a crase é caractere de escape, então
uma cerca de código Markdown precisa ser escrita com **seis** crases para produzir três.

O executável não é assinado; o SmartScreen avisa na primeira execução. Isso está documentado
no README junto do `.sha256` publicado. Se um dia houver certificado, assine no `release.yml`
entre o build e o upload.

### Gerar localmente (Windows)
```bash
pip install pyinstaller
pyinstaller packaging/tiquetaque-sync.spec --noconfirm
```

O smoke test do `release.yml` sobe o .exe de verdade, **sem redirecionar a saída**, e checa
`/healthz`, o painel, a tela de configurações e um arquivo estático — é a rede de proteção
contra "compilou mas não renderiza" e contra a regressão do item 5. Em caso de falha ele
imprime o código de saída e o final do log do app. Mantenha-o ao adicionar telas novas.

---

## 🛠️ 13. Comandos e Runbooks de Desenvolvimento

### Instalar em modo editável
```bash
pip install -e .
```

### Executar Testes
```bash
python tests/run_tests.py
```

### Executar Localmente (sem instalar)
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m tiquetaque_sync start --reload
```

### Abrir a janela desktop
```bash
python -m tiquetaque_sync gui
tiquetaque-sync shortcut create   # atalho no Menu Iniciar / Área de Trabalho
```

### Inspecionar a configuração do usuário
```bash
tiquetaque-sync config show     # valores salvos, com segredos mascarados
tiquetaque-sync config path     # caminho do config.json
tiquetaque-sync autostart status
```

### Subir com Docker Compose
```bash
docker compose up -d --build
```

### Deploy no Kubernetes
```bash
cp k8s/03-secret.example.yaml k8s/03-secret.yaml
kubectl apply -k k8s/
```

### Reproduzir o build da imagem localmente
```bash
docker buildx build --platform linux/amd64,linux/arm64 -t tique-taque-sync:test .
```
