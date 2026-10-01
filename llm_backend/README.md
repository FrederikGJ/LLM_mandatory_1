# llm_backend

To lokale LLM-endpoints, der kun kører på CPU, hver i sin container. De bruges som backend i et multi-LLM
kodnings-workflow. Begge tilbyder et OpenAI-kompatibelt API (`/v1/chat/completions`) via
[llama.cpp server](https://github.com/ggml-org/llama.cpp).

| Service | Rolle            | Model                                   | Port (kun localhost) | Tråde | RAM-loft |
|---------|------------------|-----------------------------------------|----------------------|-------|----------|
| `llm-a` | architect / lead | Qwen2.5-Coder-3B-Instruct, Q4_K_M (2,1 GB)   | `127.0.0.1:8081`     | 4     | 4 GB     |
| `llm-b` | worker / tester  | Qwen2.5-Coder-1.5B-Instruct, Q4_K_M (1,1 GB) | `127.0.0.1:8082`     | 3     | 3 GB     |

```
llm_backend/
├── docker-compose.yml
├── .env.example            # kopiér til .env (som git ignorerer)
├── config/endpoints.yaml   # rolle -> endpoint (HR-01/HR-02)
├── scripts/download-models.sh
├── scripts/smoke-test.sh
└── models/                 # GGUF-filer (git ignorerer dem)
```

## Forudsætninger

- Docker Engine med Compose v2 (`docker compose version`)
- ~3,5 GB fri disk til modellerne plus ~150 MB til imaget
- `curl` og `python3` på host til smoke-testen
- Netadgang til `huggingface.co` og `ghcr.io` ved første opstart

## Opstart

```bash
cd llm_backend
cp .env.example .env
# Udfyld LLM_A_API_KEY og LLM_B_API_KEY, fx:
sed -i "s/^LLM_A_API_KEY=.*/LLM_A_API_KEY=$(openssl rand -hex 32)/; s/^LLM_B_API_KEY=.*/LLM_B_API_KEY=$(openssl rand -hex 32)/" .env

docker compose up -d
```

Første gang henter `model-init` begge modeller (~3,2 GB) til `./models`. `llm-a` og `llm-b`
starter først, når downloaden er færdig. Følg downloaden med `docker compose logs -f model-init`.
Ved senere opstarter springes downloaden over. Modellerne kan også hentes uden Docker:
`./scripts/download-models.sh`.

Compose nægter at starte, hvis en API-nøgle mangler. Så kan der ikke opstå et endpoint uden autentificering.

## Verifikation

```bash
docker compose ps                # llm-a og llm-b skal stå som "healthy"
./scripts/smoke-test.sh          # eller: ./scripts/smoke-test.sh "din egen prompt"
```

Smoke-testen tjekker for hvert endpoint:
1. at `/health` svarer
2. at en request uden nøgle afvises med `401`
3. at en chat-request med nøgle lykkes; den viser svartid, antal tokens og tokens/sek

Manuel request:

```bash
source .env
curl -s http://127.0.0.1:8081/v1/chat/completions \
  -H "Authorization: Bearer $LLM_A_API_KEY" -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Hej!"}],"max_tokens":64}'
```

## Forbind fra din toolchain

Backenden kører én gang pr. maskine. Hver person arbejder med sine agenter i sin egen
`*_toolchain/`-mappe og forbinder lokalt til `http://127.0.0.1:8081/v1` (A) og
`http://127.0.0.1:8082/v1` (B) med nøglerne fra `llm_backend/.env`. Enhver OpenAI-kompatibel
klient virker. Eksempel i Python (`pip install openai pyyaml`), kørt fra en toolchain-mappe:

```python
import os, yaml
from openai import OpenAI

cfg = yaml.safe_load(open("../llm_backend/config/endpoints.yaml"))

def client_for(role: str) -> tuple[OpenAI, dict]:
    ep = cfg["endpoints"][cfg["roles"][role]]
    return OpenAI(base_url=ep["base_url"], api_key=os.environ[ep["api_key_env"]]), ep

client, ep = client_for("architect")
resp = client.chat.completions.create(
    model=ep["model"],
    messages=[{"role": "user", "content": "Foreslå en mappestruktur til et lille REST-API."}],
    max_tokens=ep["max_tokens"],
)
print(resp.choices[0].message.content)
```

Nøglerne skal ligge i miljøet, fx: `set -a; source ../llm_backend/.env; set +a`.

## Routing

`config/endpoints.yaml` mapper roller til endpoints. `architect` og `tech_lead` går til `llm-a`.
`coder_1`, `coder_2`, `tester`, `docs` og `deploy` går til `llm-b`. Vil du flytte en rolle, ændrer du
kun dens linje under `roles:`. Selve nøglerne står ikke i filen; den henviser til variablerne i
`.env` via `api_key_env`.

## Designvalg ift. hardwaren

**Hvorfor llama.cpp og ikke Ollama:** llama-server har indbygget `--api-key`. Det har Ollama ikke;
der skulle en ekstra reverse proxy til for at opfylde NFR-SEC-01. Med llama.cpp styrer man også
tråde, kontekst og den præcise GGUF-fil pr. container direkte, og hver model får sin egen
proces med sit eget hukommelsesloft.

**Kvantisering, Q4_K_M:** Token-generering på CPU er begrænset af hukommelsesbåndbredden. Hver token
kræver, at hele modellen læses fra RAM én gang. Q4_K_M giver ca. 3,5× mindre modeller end FP16 og
dermed tilsvarende højere hastighed, med et lille kvalitetstab. Q5/Q8 er langsommere. Under Q4
(Q3/Q2) skader kvantiseringen små modeller som 1,5B–3B markant.

**Tråde, 4 + 3:** i7 Tiger Lake har 4 fysiske kerner. Hyperthreading hjælper næsten ikke på
matrix-beregningerne i llama.cpp, så én model bør højst bruge 4 tråde. `llm-a` får 4, fordi
den største model er flaskehalsen. `llm-b` får 3, så der er en tråd tilbage til OS og
agent-processen. Docker-grænsen `cpus` matcher `--threads`. Kører begge modeller tungt på samme
tid, deler de stadig de 4 fysiske kerner. Se fejlfinding.

**Kontekst, 8192:** Qwen2.5 bruger grouped-query attention (2 KV-hoveder), så KV-cachen ved 8192
tokens kun fylder ~290 MB (3B) og ~225 MB (1.5B). RAM er altså ikke problemet her. Den reelle
pris for lange prompts er prompt-eval-tiden på CPU (se estimater). `--parallel 1` giver
hele vinduet til én request ad gangen, så det ikke deles mellem flere slots. `--n-predict`
begrænser svar til 2048 (A) og 1024 (B) tokens.

## Forventet RAM-forbrug

| Service | Vægte  | KV-cache (8192) | Buffere | I alt (ca.) | Loft |
|---------|--------|-----------------|---------|-------------|------|
| `llm-a` | 2,0 GB | 0,3 GB          | ~0,3 GB | **~2,6 GB** | 4 GB |
| `llm-b` | 1,1 GB | 0,2 GB          | ~0,2 GB | **~1,5 GB** | 3 GB |

Samlet bruges ca. 4–4,5 GB, og det samlede loft er 7 GB. `memswap_limit = mem_limit` forhindrer
swap. Følg det reelle forbrug med `docker stats`. Bemærk, at vægtene er mmap'et, så en del af
forbruget vises som page cache.

## Forventet hastighed (estimat, ikke garanti)

Estimatet gælder dual-channel DDR4 på strøm/performance-profil. På batteri eller ved
termisk throttling kan tallene halveres.

| Model | Generering, alene | Generering, begge aktive | Prompt-eval |
|-------|-------------------|--------------------------|-------------|
| 3B (`llm-a`)   | ~8–14 tokens/s  | ~5–9 tokens/s   | ~40–80 tokens/s   |
| 1.5B (`llm-b`) | ~15–25 tokens/s | ~10–16 tokens/s | ~80–160 tokens/s |

Med 4000 tokens kontekst til `llm-a` tager det altså ~1 minut, før svaret begynder.

## Fejlfinding

**Container genstarter / out-of-memory (exit code 137)**
- Tjek med `docker inspect -f '{{.State.OOMKilled}}' llm-toolchain-llm-a-1`.
- Sænk `LLM_CTX_SIZE` (fx 4096) i `.env`, eller brug en mindre kvantisering/model.
- Sørg for, at `--parallel` stadig er 1. Flere slots betyder mere KV-cache.

**Langsom inferens**
- Sæt laptoppen på strøm og vælg en performance-profil, fx `powerprofilesctl set performance`.
- Hvis begge modeller genererer samtidig, deler de 4 fysiske kerner. Sænk så `llm-b` til 2
  tråde (`--threads`, `--threads-batch` og `cpus` i `docker-compose.yml`), eller kør rollerne
  efter hinanden.
- Lange prompts er dyre på CPU. Hold konteksten til agenterne kort.
- Se tokens/s i `docker compose logs llm-a` (linjerne `prompt eval` og `eval time`).

**`unhealthy` eller `starting` i lang tid:** Modellen indlæses stadig, eller filen mangler.
Se `docker compose logs llm-a`.

**`model-init` fejler**
- `Permission denied`: `HOST_UID` og `HOST_GID` i `.env` skal matche `id -u` og `id -g`, og
  `./models` skal ejes af din bruger.
- `404` eller ugyldig GGUF: tjek `MODEL_*_URL`. Gated repos kræver `HF_TOKEN`.
- En afbrudt download fortsætter fra `*.part` ved næste `docker compose up -d`.

**`401 Unauthorized` fra din klient:** Headeren skal være `Authorization: Bearer <nøgle>` med den
nøgle fra `.env`, der hører til det rigtige endpoint. Efter ændringer i `.env` skal du køre
`docker compose up -d` igen.

**Port optaget:** Ret `LLM_A_PORT` eller `LLM_B_PORT` i `.env` og `base_url` i `config/endpoints.yaml`.
