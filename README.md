# LLM_mandatory_1

```
llm_mandatory_1/
├── llm_backend/          # fælles: to lokale LLM-endpoints (Docker Compose, llama.cpp)
├── frederik_toolchain/   # Frederiks agenter
├── lukas_toolchain/      # Lukas' agenter
└── mahdi_toolchain/      # Mahdis agenter
```

1. Start backenden på din egen maskine. Se [llm_backend/README.md](llm_backend/README.md):
   ```bash
   cd llm_backend && cp .env.example .env   # udfyld API-nøglerne
   docker compose up -d && ./scripts/smoke-test.sh
   ```
2. Byg dine agenter i din egen `*_toolchain/`-mappe og forbind lokalt:
   - `architect` og `tech_lead` → `http://127.0.0.1:8081/v1`
   - `coder_1`, `coder_2`, `tester`, `docs` og `deploy` → `http://127.0.0.1:8082/v1`

   Mappingen ligger i `llm_backend/config/endpoints.yaml`.
