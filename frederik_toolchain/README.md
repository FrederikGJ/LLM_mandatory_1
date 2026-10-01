# frederik_toolchain

Frederiks toolchain til LLM_mandatory_1. [Aider](https://github.com/Aider-AI/aider) 0.86.2 (Apache 2.0)
bygger "Booking af mødelokaler" med syv roller fordelt på de to lokale endpoints fra `../llm_backend`:
`llm-a` (Qwen2.5-Coder-3B) og `llm-b` (Qwen2.5-Coder-1.5B).

```
SPEC.md ─► architect ─► tech_lead ─┬─► coder_1 (branch coder_1) ─┐
           llm-a        llm-a      └─► coder_2 (branch coder_2) ─┴─► merge ─► tester ─► kvalitetsgate
           docs/arch/   tickets/       llm-b: src/booking/                    llm-b    pytest/ruff/mypy
                                                                                          │ rød
               docs ◄── deploy ◄── tester (rapport) ◄── tech_lead fix (llm-a + llm-b) ◄───┘
               llm-b    llm-b      reports/quality.md   architect-mode
```

## Indhold

```
frederik_toolchain/
├── setup.sh               # uv + Python 3.12 + .venv med låst Aider
├── run_role.sh            # én rolle: ./run_role.sh <rolle> [opgave ...] [--interactive] | --check
├── run_all.sh             # hele pipelinen i fast rækkefølge
├── .aider.conf.yml        # fælles Aider-indstillinger (architect-par, git, ingen shell)
├── requirements.in/.txt   # Aider og den fulde lås
├── config/roles.yaml      # roller -> prompt, opgaver, filer og mode
├── prompts/               # fælles regler + én prompt pr. rolle
├── scripts/
│   ├── toolchain.py       # glue: endpoints, nøgler, Aider-kommando, logning, opsummering
│   ├── aider_launch.py    # starter Aider med låst git-rod og bekræftelsespolitik
│   ├── init_demo.sh       # nyt demo-repo: git uden remote, pre-push-hook, app-venv
│   ├── quality.sh         # pytest + ruff + mypy -> reports/raw/
│   ├── check_git.sh       # git-politikken efter en kørsel
│   └── compare_runs.sh    # sammenligner to kørsler (NFR-REP-03)
├── template/              # startindhold i demo-repoet (SPEC.md, pyproject, låste requirements)
├── docs/evaluering.md     # EV-01–EV-04, sammenligning og sporbarhed
└── runs/                  # kørsler (git ignorerer mappen): runs/<id>/{demo,logs,summary.md}
```

## Opsætning (D-02)

Trin for trin til Linux, macOS og Windows (WSL2): se [SETUP.md](SETUP.md).

Forudsætninger: Linux eller macOS, `git`, en `python3` (kun til at hente uv), Docker med Compose v2,
ca. 5 GB fri disk og netadgang første gang (PyPI, GitHub og Hugging Face).

1. Start backenden, se [../llm_backend/README.md](../llm_backend/README.md):

       cd ../llm_backend && cp .env.example .env   # udfyld LLM_A_API_KEY og LLM_B_API_KEY
       docker compose up -d && ./scripts/smoke-test.sh
       cd ../frederik_toolchain

2. Installér toolchainen. Scriptet henter uv 0.12.21 og Python 3.12.14 til `.uv/` og installerer Aider fra
   `requirements.txt` i `.venv/`. Intet installeres globalt.

       ./setup.sh

3. Tjek forbindelsen. Scriptet viser rollebindingen og tjekker for hvert endpoint `/health`, at en request
   uden nøgle afvises med 401, og at modellen svarer med nøgle.

       ./run_role.sh --check

4. Kør hele pipelinen. Hver kørsel får sin egen mappe med et nyt demo-repo, logs og en opsummering.

       ./run_all.sh
       cat runs/latest/summary.md
       git -C runs/latest/demo log --graph --oneline --all

Backenden skal køre, mens pipelinen kører. En kørsel er 13 Aider-trin (plus en rettelsesrunde, hvis testene
fejler) og forventes at tage ca. 20 minutter på en i7 med fire kerner. Luk andre tunge containere, og sæt
maskinen på strøm og `powerprofilesctl set performance`. De målte tider står i `summary.md`.

**Port optaget eller backend andetsteds:** Kopiér `endpoints.yaml` og `.env`, ret porte/nøgler, og peg
toolchainen på kopierne. Selve backend-filerne skal ikke ændres:

    export ENDPOINTS_FILE=/sti/til/endpoints.yaml LLM_BACKEND_ENV=/sti/til/.env

**Låsefiler:** `./setup.sh --lock` genererer `requirements.txt` og `template/requirements*.txt` fra
`.in`-filerne. Kør kun det, når en version bevidst skal opdateres.

## Roller og endpoints (HR-01, HR-02)

Rollens endpoint slås op i `../llm_backend/config/endpoints.yaml`. En rolle flyttes ved kun at ændre dens
linje under `roles:`. `toolchain.py` genererer pr. Aider-kørsel en midlertidig model-settings-fil
(mappe med 0700 i `$XDG_RUNTIME_DIR`) med `api_base`, `api_key` og `max_tokens` pr. model. Derfor kan én
Aider-proces bruge begge endpoints i architect-mode. Nøglerne læses fra `llm_backend/.env` eller miljøet,
og alle `base_url` skal pege på localhost (NFR-SEC-01).

| Rolle | Endpoint | Mode | Skriver | Krav |
|---|---|---|---|---|
| architect | llm-a | code | `docs/arch/components.md`, `openapi.yaml`, `deployment.md`, `adr.md` | FR1 |
| tech_lead | llm-a | code | `tickets/TICKETS.md` (T-01–T-04) | FR2 |
| tech_lead (fix) | llm-a + editor llm-b | architect | rettelser i `src/booking/`, når kvalitetsgaten er rød | FR4 |
| coder_1 | llm-b | code | `src/booking/models.py`, `storage.py` (branch `coder_1`) | FR3 |
| coder_2 | llm-b | code | `src/booking/api.py` (branch `coder_2`) | FR3 |
| tester | llm-b | code | `tests/test_api.py`, `reports/quality.md` | FR4 |
| deploy | llm-b | code | `Dockerfile` | FR6 |
| docs | llm-b | code | `README.md` (setup, kørsel, API-brug), `docs/runbook.md` | FR5 |

Opgaverne står i `config/roles.yaml`. Hver opgave er ét Aider-kald med få filer, så svaret kan være inden
for `max_tokens` (1024 på llm-b). Prompten består af `prompts/common.md`, rollens prompt og opgaveteksten.

Artefakterne er bevidst små, fordi opgaven kun kræver, at hvert ansvar er dækket, og fordi hver token
koster på CPU. Markdown-filer har et hårdt loft på 20 ikke-tomme linjer i stikord (`max_lines`;
`components.md` 30 og `TICKETS.md` 25). Loftet står i beskeden til modellen, og `toolchain.py` tjekker det
bagefter. Op til 20 % over godtages med en note; er filen længere, får modellen ét nyt forsøg med besked om
at forkorte den. Aider må højst bruge én ekstra runde på at rette lint-fejl (`max_reflections: 1`, Aiders
standard er 3), og testeren skriver præcis 6 tests på højst 50 linjer, så filen kan være inden for 1024
output-tokens. Derudover:

- Deploy-validering: kun `Dockerfile` (kravet er "mindst én af").
- Designdokumenter (FR5): arkitekturfilerne i `docs/arch/`, som README'en henviser til.
- Alle fire tickets ligger i én fil, så tech lead er ét kald i stedet for fire.

Enkelte roller og opgaver kan køres for sig mod det seneste demo-repo (eller `DEMO_DIR`):

    ./run_role.sh architect                  # alle rollens opgaver
    ./run_role.sh coder_1 T-02               # én opgave
    ./run_role.sh tech_lead fix              # manuel opgave
    ./run_role.sh architect components --interactive

Med `--interactive` åbnes Aiders egen chat med samme modeller og politik for git. Architect-planen vises,
før editoren skriver, og man kan bruge `/diff`, `/undo` og `/ask` (NFR-KON-01).

## Git-politik (planens afsnit 1a)

- Aider startes altid med demo-repoet som cwd og med `force_git_root` sat til demo-repoet
  (`scripts/aider_launch.py`). `toolchain.py` stopper, hvis mappen ikke er sin egen git-rod eller har en remote.
- Bekræftelsespolitik: Aider får kun "ja" til at lade editoren skrive og til at rette lint-fejl. Nye filer uden
  for opgaven, filer modellen nævner, shell-kommandoer og URL'er afvises. Hver afvisning logges som `[politik]`.
  `suggest-shell-commands: false` og `detect-urls: false` står også i `.aider.conf.yml`.
- `.aider.conf.yml` har `git: false`. Startes Aider direkte i denne mappe (hvor git-roden er llm_mandatory_1),
  committer den derfor ingenting. `run_role.sh` tænder git med `--git`.
- Demo-repoet har ingen remote, `push.default=nothing`, egen identitet, `core.hooksPath=.git/hooks` og en
  `pre-push`-hook, der altid afviser.
- Kun `run_all.sh` opretter branches (`coder_1`, `coder_2`) og merger. Agenterne auto-committer på den branch,
  de startes på.
- `scripts/check_git.sh` tjekker efter hver kørsel: ingen remote, kun forventede branches, hook på plads,
  rent arbejdstræ, og at HEAD i llm_mandatory_1 er uændret. Resultatet står i `summary.md`.

## Kontekst (NFR-CTX-01, NFR-CTX-02)

- Overdragelse sker kun via filer i demo-repoet: `docs/arch/` → `tickets/` → `src/` → `tests/` og `reports/`.
- Hver opgave får sine edit-filer og en kort liste read-only filer, ikke hele repoet. Ét ticket ad gangen.
- Rollens faste read-filer kommer først, så llama.cpp kan genbruge prompt-cachen mellem opgaver.
- `max_input_tokens` sættes til `context_window - max_tokens`, så Aider afviser for store prompts, før de
  sendes, i stedet for at llama.cpp klipper svaret.
- Repo map er slået fra (`map-tokens: 0`), fordi filerne gives eksplicit. Ved vækst kan `map_tokens` sættes
  pr. rolle eller opgave i `config/roles.yaml`. Filerne holdes under 80 linjer (står i SPEC.md).

## Logs og evidens

Pr. kørsel i `runs/<id>/`:

- `summary.md`: tid, status, forsøg og tokens pr. trin, commits pr. branch, kvalitet og git-tjek
- `logs/steps.tsv`: de samme data som tabel
- `logs/NN-rolle-opgave.message.md`: den præcise besked til Aider
- `logs/NN-rolle-opgave.log`: Aiders output, inkl. `[politik]`-linjer
- `logs/NN-rolle-opgave.llm.md`: alt, der er sendt til og modtaget fra modellerne
- `logs/NN-rolle-opgave.diff`: diff for trinnets commits (diff-review)
- `demo/reports/raw/`: rå pytest/ruff/mypy-output, skrevet af `quality.sh`, ikke af en LLM

Reproducerbarhed (NFR-REP-03): kør to gange og sammenlign.

    RUN_ID=run-a ./run_all.sh && RUN_ID=run-b ./run_all.sh
    scripts/compare_runs.sh runs/run-a runs/run-b
