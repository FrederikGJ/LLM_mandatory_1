# Run 1: run-20261005-185558

Kørt med `bash run_all.sh` (uden REVIEW) fra 2026-10-05 18:56 til 2026-10-06 ca. 07:00 (stoppet manuelt).
Data: `runs/run-20261005-185558/` (summary.md, logs/, demo/.git/git-guard.jsonl) og `docker compose logs llm-a/llm-b`.
OpenCode's egen log for kørslen findes ikke (ikke i `~/.local/share/opencode/log/opencode.log`).

## Opsætning
- OpenCode 1.18.34, orchestrator (primary) + 7 subagents via task-værktøjet, kommandoerne /plan, /implement, /finish
- git-guard-plugin håndterer branches/merge; run_all.sh starter kun kommandoerne
- llm-a: Qwen2.5-3B-Instruct Q4_K_M (orchestrator, architect, tech_lead); llm-b: Qwen2.5-1.5B-Instruct Q4_K_M (resten)
- Lokal `.env`: `LLM_CTX_SIZE=16384`, `LLM_A_MAX_TOKENS=2048`, `LLM_B_MAX_TOKENS=1024`; containere: llm-a 4 CPU / 4 GB, llm-b 3 CPU / 3 GB, ingen swap
- Agent-prompts med filnavne og linjelofter; ingen timeout pr. kommando
- orchestrator-linjen "kun ét task-kald ad gangen" kom først med fra /implement (tilføjet 19:16, mens /plan kørte)

## Forløb (lokal tid)
| Trin | Tid | Resultat |
|---|---|---|
| /plan | 18:56-19:48 (3085 s) | exit 0, outputtjek bestået |
| - orchestrator 1. tur | ca. 2,5 min | startede architect og tech_lead parallelt med samme (forkerte) prompt: "Read SPEC.md, docs/architecture/overview.md and split tickets." |
| - tech_lead | 1243 s | 4 ticket-filer, ingen egne commits (git-guard committede) |
| - architect | 2028 s | overview.md + openapi.yaml, ingen ADR'er; git-guard committede |
| - orchestrator sidste tur | 15 min (17:33-17:48 UTC) | 54 tokens på 892 s = 0,06 t/s; udskrev et task-kald som JSON-tekst (ikke udført) |
| /implement | 19:48 - | aldrig færdig |
| - orchestrator 1. tur | 23 min | delegerede til coder_1 kl. 20:11; git-guard oprettede coder_1/coder_2 og skiftede til coder_1 |
| - coder_1 | 215 s | ramte output-loftet: præcis 1024 tokens genereret, ingen filer, ingen commits |
| - kl. 20:17 | | OpenCode annullerede orchestrator-requestet + 2 nye requests (llm-a: "cancel task" x3) og sendte derefter intet mere |
| 20:17-07:00 | ca. 11 t | intet skete; ingen fejl i konsollen, opencode run afsluttede ikke |
| /finish | | ikke nået |

## Målinger fra llama.cpp
| Endpoint | Generering | Prompt-indlæsning | Bemærkning |
|---|---|---|---|
| llm-a | 3,5-7 t/s (0,06 t/s i 15 min) | 15-36 t/s | 3,74 / 4 GiB RAM (93 %) |
| llm-b | 8,65 t/s | 39 t/s | 1,61 / 3 GiB RAM |

Største kald: architect 2048 output-tokens på 507 s (4,04 t/s); coder_1 1024 output-tokens på 118 s (= loftet).

## Leverancer
| Artefakt | Status |
|---|---|
| docs/architecture/overview.md | leveret, brugbar (komponenter, ansvar, topologi, constraints) |
| docs/architecture/openapi.yaml | leveret, men ikke gyldig YAML/OpenAPI (indlejrede punktlister) |
| ADR'er | mangler |
| docs/tickets/ | 4 filer; T-001 indeholder alle tre tickets, T-002/T-003 generiske uden signaturer, T-004 ekstra fil |
| src/booking/*.py | intet |
| tests, rapport, docs, Dockerfile | ikke nået |

## Git-politik
- main: skabelon + 2 git-guard-commits; coder_1 og coder_2 oprettet fra samme commit; ingen merge (coders leverede intet)
- Ingen remote, ingen push, LLM'en kørte ingen branch-kommandoer; llm_mandatory_1 urørt

## Fejltilstande (hvad gik i stykker først)
1. Gennemløb: llm-a på CPU med 3,5 t/s; orchestrator-ture kostede ca. 18 min i /plan og 23 min før første coder-delegering
2. Hukommelse: llm-a ved 93 % af en hård 4 GB-grænse uden swap; sandsynlig årsag til perioden med 0,06 t/s
3. Delegering: orchestratoren (3B) opsummerede trinteksten og mistede filnavne; to tasks parallelt med samme prompt
4. Output-loft: 1024 tokens på llm-b er for lidt til at coder_1 kan skrive en fil i ét svar
5. Ingen timeout: OpenCode stod stille i 11 timer uden at afslutte
