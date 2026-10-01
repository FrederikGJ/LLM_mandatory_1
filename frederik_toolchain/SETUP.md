# Setup af frederik_toolchain

Du sætter to ting op:

```
llm_backend/          to lokale sprogmodeller i Docker
  llm-a  127.0.0.1:8081  ◄── architect, tech_lead
  llm-b  127.0.0.1:8082  ◄── coder_1, coder_2, tester, deploy, docs
frederik_toolchain/   Aider i en lokal .venv, som styrer rollerne
```

Kun Docker og git installeres på maskinen. Python, uv og Aider havner i projektmappen (`.uv/` og `.venv/`).

Du skal bruge ca. 5 GB fri disk, ca. 8 GB RAM til Docker og netadgang første gang.

- **Linux eller macOS:** følg afsnit 1.
- **Windows:** følg afsnit 2.

---

## 1. Linux og macOS

### 1.1 Installér forudsætningerne

**Arch, Manjaro og andre Arch-baserede systemer:**

```bash
sudo pacman -S --needed git python curl openssl docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"   # log ud og ind igen bagefter
```

Arch' `python`-pakke har allerede venv med, så der er ikke brug for en ekstra pakke.

**Ubuntu og Debian:**

```bash
sudo apt update && sudo apt install -y git python3 python3-venv curl openssl
```

Installér derefter Docker Engine med Compose v2 ([docs.docker.com/engine/install](https://docs.docker.com/engine/install/)).

**Alle Linux-systemer:** tjek, at `docker compose version` virker uden `sudo`.

**macOS:** Installér [Homebrew](https://brew.sh) og [Docker Desktop](https://www.docker.com/products/docker-desktop/).
Kør derefter:

```bash
brew install bash coreutils git
```

Det er nødvendigt, fordi macOS leveres med bash 3.2. Scripts som `scripts/quality.sh` kræver bash 4 eller nyere
og kommandoen `timeout`, som kommer fra `coreutils`. Åbn en ny terminal og tjek:

```bash
bash --version      # skal vise version 5.x
timeout --version   # skal findes
```

### 1.2 Start backenden

```bash
git clone https://github.com/FrederikGJ/LLM_mandatory_1.git
cd LLM_mandatory_1/llm_backend
cp .env.example .env
openssl rand -hex 32   # kør to gange og få to nøgler
```

Åbn `.env` i en editor:

- Indsæt den ene nøgle i `LLM_A_API_KEY=` og den anden i `LLM_B_API_KEY=`.
- Sæt `HOST_UID` og `HOST_GID` til det, som `id -u` og `id -g` viser. På macOS er det typisk 501 og 20.

Start derefter modellerne:

```bash
docker compose up -d       # første gang hentes ca. 3,2 GB modeller
docker compose ps          # vent til llm-a og llm-b står som "healthy"
./scripts/smoke-test.sh    # alle tjek skal være ok
```

### 1.3 Installér toolchainen

```bash
cd ../frederik_toolchain
./setup.sh
```

Scriptet henter uv og Python 3.12 til `.uv/` og installerer Aider i `.venv/`. Intet ændres uden for mappen.

### 1.4 Tjek og kør

```bash
./run_role.sh --check          # alle linjer skal vise [ok]
./run_all.sh                   # hele pipelinen, ca. 30 min på en bærbar uden GPU
cat runs/latest/summary.md     # resultatet
```

Backenden skal køre, mens pipelinen kører. Sæt maskinen på strøm.

---

## 2. Windows

Toolchainen består af bash-scripts med Linux-stier, så den kører ikke direkte i Windows. Brug i stedet
**WSL2**, som er en rigtig Linux inde i Windows. Så kan du følge Linux-vejledningen.

### 2.1 Installér WSL og Ubuntu (kun første gang)

1. Åbn startmenuen og skriv **PowerShell**. Højreklik, og vælg **Kør som administrator**.
2. Kør:

   ```powershell
   wsl --install -d Ubuntu
   ```

3. Genstart computeren.
4. Efter genstarten åbner et Ubuntu-vindue af sig selv. Sker det ikke, så start **Ubuntu** fra startmenuen.
   Vinduet installerer i et par minutter og spørger derefter om et brugernavn og en adgangskode:
   - Brugernavnet skal være med små bogstaver og uden mellemrum.
   - Adgangskoden vises ikke, mens du skriver. Det er normalt. Husk den, for `sudo` beder om den senere.
5. Tjek i PowerShell, at Ubuntu kører som version 2:

   ```powershell
   wsl -l -v      # Ubuntu skal stå med VERSION 2
   ```

### 2.2 Sådan kommer du ind i Ubuntu

Du kan åbne Ubuntu på tre måder:

- Start **Ubuntu** fra startmenuen.
- Klik på pilen ved siden af fanerne i **Windows Terminal**, og vælg Ubuntu.
- Skriv `wsl` i PowerShell.

Når prompten ser sådan ud, er du inde i Ubuntu, og alle kommandoer herfra er Linux-kommandoer:

    dit-navn@din-pc:~$

Praktiske ting:

- `exit` lukker Ubuntu igen.
- Indsæt tekst med **Ctrl+Shift+V** eller **højreklik**.
- Rediger en fil med `nano .env`. Gem med **Ctrl+O** og Enter, og luk med **Ctrl+X**.
- `explorer.exe .` åbner den mappe, du står i, i Windows Stifinder.
- Windows' C-drev ligger under `/mnt/c`, men arbejd ikke dér. Se afsnit 2.4.

### 2.3 Installér Docker Desktop

1. Installér [Docker Desktop](https://www.docker.com/products/docker-desktop/) i Windows, og start programmet.
2. Under *Settings*:
   - *General*: slå "Use the WSL 2 based engine" til.
   - *Resources → WSL integration*: slå Ubuntu til.
3. Åbn Ubuntu (afsnit 2.2) og kør:

   ```bash
   sudo apt update && sudo apt install -y git python3 python3-venv curl openssl
   docker compose version   # skal virke
   ```

Docker Desktop skal være startet i Windows, hver gang du bruger `docker` i Ubuntu.

### 2.4 Følg Linux-trinene inde i Ubuntu

Kør afsnit **1.2 til 1.4** i Ubuntu-terminalen.

Klon repoet ind i Linux-hjemmemappen (`cd ~` først), **ikke** under `/mnt/c/...`. Windows-drevet er langsomt set
fra WSL, og dér virker eksekverbare scripts og git-hooks ikke korrekt.

### 2.5 Uden WSL (ikke understøttet)

Vil du køre direkte i Windows, skal følgende ændres først:

| Fil | Det Linux-specifikke |
|---|---|
| `setup.sh`, `run_role.sh`, `run_all.sh`, `scripts/*.sh` | Bash-scripts. Kræver Git Bash eller omskrivning til PowerShell. |
| `scripts/common.sh`, `setup.sh`, `run_role.sh`, `run_all.sh` | Stier som `.venv/bin/python` og `.uv/venv/bin/uv`. På Windows hedder de `.venv\Scripts\python.exe` osv. |
| `scripts/toolchain.py` (linje 38) | `VENV_PY = .venv/bin/python` |
| `scripts/init_demo.sh` | Demo-repoets `.venv/bin/python` og `chmod +x` på pre-push-hooken |
| `scripts/quality.sh` | `.venv/bin/python`, `ruff` og `mypy`, kommandoen `timeout` og `declare -A` (bash 4) |
| `run_all.sh` | Symlinket `runs/latest` (`ln -sfn`) kræver Developer Mode i Windows |
| `setup.sh` | Kalder `python3`. På Windows hedder den typisk `py` eller `python`. |
| `../llm_backend/scripts/smoke-test.sh` | Bash-script |

---

## Fejlfinding

| Symptom | Løsning |
|---|---|
| `ensurepip is not available` under `./setup.sh` (Ubuntu/Debian) | `sudo apt install python3-venv` |
| `permission denied` på `/var/run/docker.sock` | Din bruger er ikke i `docker`-gruppen. Kør `sudo usermod -aG docker "$USER"`, og log ud og ind. |
| `declare: -A: invalid option` (macOS) | bash er for gammel. Kør `brew install bash`, og åbn en ny terminal. |
| `timeout: command not found` (macOS) | `brew install coreutils` |
| `--check` viser FEJL ved `/health` | Backenden kører ikke. Kør `docker compose ps` i `llm_backend/`. |
| `Range of CPUs` når backenden startes | Docker har færre end 4 CPU'er. Giv Docker flere under *Settings → Resources*. |
| Containere genstarter (exit 137) | Docker mangler RAM. Giv Docker mindst 8 GB (på WSL2 i `%UserProfile%\.wslconfig`). |
| `Permission denied` i `model-init` | `HOST_UID`/`HOST_GID` i `.env` matcher ikke `id -u`/`id -g`. |
| Kørslen er meget langsom | Sæt maskinen på strøm. På Linux: `powerprofilesctl set performance`. |

Flere detaljer om roller, git-politik og logs står i [README.md](README.md). Backenden er beskrevet i
[../llm_backend/README.md](../llm_backend/README.md).
