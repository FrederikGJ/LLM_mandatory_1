# Review af toolchain 3 LangGraph

## Formål og grundlag

Jeg har evalueret LangGraph som orkestreringslag for et workflow med to lokale
sprogmodeller. Formålet var at undersøge, om workflowet kunne levere arkitektur,
tickets, implementering, tests, dokumentation og deploymentvalidering for samme
mødelokalebooking-API som gruppens øvrige kandidater.

Jeg har taget udgangspunkt i lærerens opgave, gruppens kravspecifikation og
bookingdemoens `SPEC.md` og `CONTRACT.md`. Evalueringen bygger på mine to lokale
kørsler, `run-01` og `run-02`, deres logs, reviewændringer og kvalitetsresultater.
Begge kørsler endte med status `passed` efter review og rettelser.

## Arkitektur og rollefordeling

Min toolchain er skrevet i Python 3.12 med LangGraph 1.2.12,
langchain-openai 1.6.7 og SQLite-checkpointer 3.1.1. Jeg kører den fra terminalen
i IntelliJ. Den fælles LLM-backend skal være startet først. Toolchainen kalder
backendens modeller; det genererede booking-API bruger ikke selv sprogmodeller.

| Endpoint | Lokal adresse | Model | Roller |
|---|---|---|---|
| llm-a | `http://127.0.0.1:8081/v1` | Qwen2.5-Coder-3B-Instruct, Q4_K_M | architect, tech_lead |
| llm-b | `http://127.0.0.1:8082/v1` | Qwen2.5-Coder-1.5B-Instruct, Q4_K_M | coder_1, coder_2, tester, docs, deploy |

Begge endpoints bruger llama.cpp i Docker på CPU med 8192 tokens kontekst.
Rollefordelingen læses fra `llm_backend/config/endpoints.yaml`, og API-nøglerne
læses fra miljøkonfigurationen. En rolle kan derfor flyttes ved at ændre
konfigurationen. En igangværende kørsel fastholder sit konfigurationssnapshot,
så ændringer i opsætningen ikke sker ubemærket under fortsættelse.

Grafen styrer rækkefølgen fra arkitektur og tickets til implementering, tests,
kvalitetskontrol, dokumentation og deploymentvalidering. `Send` fordeler
implementeringen mellem to workers: `coder_1` har modeller og storage, mens
`coder_2` har API'et. En fælles interfacekontrakt gør det muligt at udvikle de
to dele uafhængigt og samle filerne i grafens tilstand.

Jeg har dermed to opgavepartitioner. Modelkald til samme endpoint serialiseres,
fordi backendens `--parallel 1` kun giver én slot. Det giver ingen dokumenteret
hastighedsgevinst fra samtidig inferens på llm-b.

LangGraph leverer graf, tilstand, workerfordeling og reviewpauser. SQLite-integrationen
gemmer checkpoints. Prompts, filsamling, formatkontroller, cache, git,
kvalitetskommandoer og rapporter kræver egen Python-kode. Det er en væsentlig del
af opsætningsarbejdet og af den efterfølgende vedligeholdelse.

## Kontrol og sporbarhed

Jeg gennemgår plan og diff ved to reviewstop: før kode og tests anvendes og
eksekveres, og før den afsluttende leverance anvendes og valideres. `interrupt`
pauser grafen, indtil jeg godkender eller afviser. Ved en fejlet kvalitetsgate
kan grafen udføre højst én automatisk koderettelsesrunde, hvor tests fastholdes.

Hver kørsel har et isoleret git-repository uden remote. Commits og diffs gør
ændringerne læsbare. Checkpoints og cache gør det muligt at fortsætte efter
en fejl uden at gentage alle tidligere modelkald. Gemte svar valideres igen,
før de genbruges.

Resultaterne omfatter Qwen-output, rettelser i controlleren og ændringer ved
review. De rå modelsvar og afvisninger bevares, mens rettelser registreres
separat. En bestået leverance betyder derfor ikke, at alle rå modelsvar var
korrekte eller kunne anvendes direkte.

## Opfyldelse af kravene

Jeg bruger kravspecifikationen som en oversigt over toolchainens egenskaber.
Efter lærerens præcisering behandler jeg HR-punkterne som supplerende teknisk
dokumentation og ikke som obligatoriske afleveringskrav.

| Krav | Vurdering og dokumentation |
|---|---|
| HR-01 to lokale endpoints | Begge endpoints blev brugt i begge kørsler. Endpoint-tjekket kontrollerer health, afvisning uden nøgle, model-alias og en faktisk chat-request. |
| HR-02 konfigurerbar routing | Rollefordeling, adresser og modelnavne læses fra den fælles endpointkonfiguration. |
| HR-03 open source-værktøjer | De centrale softwareværktøjer er open source. Licenser og kilder er dokumenteret i README. IntelliJ og Docker Desktop er værtsværktøjer og kan erstattes af terminal og Docker Engine/Compose. |
| FR-ARK-01 til 04 | Komponentbeskrivelse, OpenAPI, deployment-topologi og ADR'er blev leveret og rettet ved review. |
| FR-TL-01 til 04 | Tickets beskriver ejerskab, scope, fravalg, DoD og afhængigheder. Planens mangler blev rettet ved review. |
| FR-IMP-01 til 02 | To workers fordeler modeller/storage og API. Bookingfunktionen går på tværs af flere filer og består accepttest. |
| FR-TEST-01 til 04 | 8 genererede tests og 13 faste accepttest blev kørt. Alle 21 tests, Ruff og mypy bestod i begge runs. Kvalitetsresultater og en analyse af risici blev leveret. |
| FR-DOK-01 til 04 | README med opsætning og API-brug, runbook og arkitekturdokumenter blev leveret og rettet ved review. |
| FR-DEP-01 | Dockerfile og deployment-tjekliste blev leveret. Lokal kontrol af import, health, SQLite-oprettelse og persistens efter genåbning bestod. Container-build og container-run er ikke verificeret. |
| NFR-KON-01 | Plan og diffs gennemgås ved to reviewstop før eksekvering. |
| NFR-REP-01 til 02 | Begge demo-repositories har commits og reviewbare diffs. |
| NFR-REP-03 | To fulde kørsler har samme filstruktur og sammenlignelige resultater. Toolchainen blev rettet undervejs, så de er ikke et kontrolleret forsøg med identisk kodeversion. |
| NFR-CTX-01 til 02 | Kontekst udvælges gennem eksplicitte fillister, gemmes i tilstand og kontrolleres mod et tokenbudget. Begrænsninger ved projektvækst er beskrevet nedenfor. |
| NFR-SEC-01 | Backendens porte bindes til localhost, beskyttede kald kræver nøgle, og nøgler udelades fra snapshots og fejllogs. |
| EV-01 til 04 | Modelkald, kaldetider og fejl er dokumenteret. Samlet opsætningstid til første fungerende resultat blev ikke målt særskilt. |
| EV-05 | Jeg anbefaler LangGraph til lokale workflows med eksplicit styring af rækkefølge, interfaces, review og genoptagelse. Anbefalingen bygger på de to kørsler og de beskrevne begrænsninger. |
| D-01 | Afsnittet om toolchain 3 er udarbejdet til den fælles synopsis. Den samlede rapport skal opfylde kravet om 7–10 sider. |
| D-02 | README beskriver forudsætninger, backend, installation, reviews og demoens resultater. En uafhængig tredjepartsreproduktion er ikke dokumenteret. |
| D-03 | Review af en anden gruppes arbejde skal indgå efter aflevering. Dette dokument evaluerer min egen toolchain. |

Deploymentkravet kan blandt andet dækkes med en tjekliste eller konfiguration.
Jeg skelner derfor mellem det leverede deploymentmateriale og den faktisk
udførte lokale driftskontrol. Den beståede kontrol beviser ikke, at Docker-imaget
er bygget eller startet.

## Sammenligning af run-01 og run-02

| Målepunkt | run-01 | run-02 |
|---|---|---|
| Slutstatus | passed | passed |
| Beståede tests | 21 | 21 |
| Automatiske kvalitetsrettelsesrunder | 0 | 0 |
| Loggede modelkaldsforsøg | 58 | 40 |
| Afviste modelsvar | 18 | 7 |
| Summeret modeltid i sekunder | 3079,924 | 1271,971 |
| Git-commits | 6 | 5 |
| Pakker med reviewrettelser | 2 | 2 |
| Gentagelser af deploymentkontrol | 1 | 0 |
| Særskilte kontraktrettelser i controlleren | 0 | 1 |
| Anvendte endpoints | llm-a og llm-b | llm-a og llm-b |
| Anvendte roller | Alle syv | Alle syv |

Begge kørsler har samme 26 versionerede filer. 13 filer er byte-identiske, og
ingen fil findes kun i den ene kørsel. Det viser strukturel sammenlignelighed;
opgaven kræver ikke identisk genereret indhold.

Modeltiden er summen af loggede kald, inklusive fejlede forsøg. Den svarer til
cirka 51 minutter og 20 sekunder for run-01 og 21 minutter og 12 sekunder for
run-02. Den indeholder ikke hele opsætningen eller tiden brugt på review og
manuel fejlsøgning.

Run-02 havde færre kald og afvisninger. Jeg kan ikke bruge tidsforskellen som
bevis for en bestemt hastighedsforbedring, fordi controller og prompts blev
ændret under udviklingsforløbet. Begge runs krævede også reviewrettelser.

`review_edits = 2` betyder to registrerede rettelsessæt pr. run, ikke to ændrede
filer. `round = 0` betyder, at der ikke var behov for en automatisk rettelsesrunde
efter kvalitetsgaten. Det betyder ikke, at genereringen var uden fejl eller
rettelser før godkendelsen.

## Fejl og rettelser

### Manglende modeller og forkert validatorsignatur

Modellen udelod gentagne gange `Booking` og skrev en Pydantic after-validator
med `(cls, self)` i stedet for en instancemetode med `self`. Format- og
AST-kontroller fangede fejlene. Mere præcis feedback gav ikke altid et korrekt
nyt svar.

Controlleren håndterer nu den konkrete kendte kombination, når resten af filen
matcher kontrakten: validatorsignaturen korrigeres, og den manglende klasse
tilføjes ud fra `CONTRACT.md`. Rettelsen registreres særskilt og tæller ikke som
et nyt modelsvar. Ukendte fejl afvises fortsat, og den rettede fil skal stadig
gennem review og kvalitetskontrol.

### For store svar og blandede filansvar

Nogle svar blandede modeller, storage og API sammen eller kopierede hele
applikationen ind i en testfil. Andre ramte outputloftet eller overskred
kravet om færre end 80 linjer pr. kildefil. Der var også en for stram intern
grænse på ti linjer pr. funktion, som ikke kom fra demoens SPEC.

Storage, API og tests blev derfor opdelt i mindre funktionsopgaver med faste
rammer i controlleren. De lokale modeller leverer funktionsindhold og
testassertions. Samlede kildefiler kontrolleres stadig mod SPEC, og en
formatrettelse skal bevare kodens AST og strenge.

### Fejl i storage, API og tests

Review og validering fandt blandt andet en SQLite-cursor brugt som context
manager, bookings uden sortering, API-funktioner med forkert scope og et
udefineret `ValidationError`. Disse fejl viser, at en fil ikke er korrekt,
bare fordi den har de forventede navne og kan parses.

En test for et ukendt rum brugte et ugyldigt tidsinterval og ramte derfor
inputvalidering i stedet for den forventede 404-adfærd. Testen blev rettet
til et gyldigt interval. Kontrollen af ugyldige intervaller blev bevaret.

### Dokumentation og deploymentkonfiguration

Ved leverancereview blev forkerte versionsangivelser, backupbeskrivelser og
kommandoer rettet. Dockerfile anvendte blandt andet Alpine-flags i et
Debian-baseret Python-image og manglede en skrivbar `/data`-mappe. Konfigurationen
blev tilpasset, så applikationen kan køre som en særskilt bruger med adgang til
datamappen. Det er en reviewet konfiguration; et faktisk image-build er stadig
ikke dokumenteret.

### Endpointfejl og oprydning på Windows

En health-request med status 200 var ikke tilstrækkelig til at afgøre, om
autentificerede modelkald virkede. Der opstod både transportfejl og 401-fejl.
Endpointkontrollen tester derfor også nøgle, model-alias og chat. Fejllogs
bevarer den relevante årsag uden at skrive API-nøgler ud.

Run-01 fejlede til sidst med WinError 32, fordi deploymenttesten holdt
SQLite-forbindelser åbne under oprydning. Testharnessen blev rettet til at
lukke forbindelser eksplicit, også før databasen genåbnes. Kun den afsluttende
kontrol blev gentaget; tidligere modelkald og kvalitetsresultater blev bevaret.
Run-02 bestod uden denne gentagelse.

## Kontekst og begrænsninger

Jeg bruger eksplicitte fillister pr. opgave med SPEC, CONTRACT, tickets og
relevante artefakter. Controlleren kontrollerer prompt og maksimalt svar mod
kontekstbudgettet. Svarloftet er 2048 tokens på llm-a og 1024 på llm-b.
Manglende kontekstfiler eller et overskredet budget stopper trinnet.

Det gør kontekstoverdragelsen tydelig, men skalerer ikke automatisk til et
stort repository. Flere moduler vil kræve ændrede partitioner og fillister.
Jeg har ikke dokumenteret automatisk repository-søgning eller målt opførsel
på et større projekt.

De beståede tests dokumenterer bookingdemoens aftalte adfærd. De dokumenterer
ikke produktionsdrift, belastning, samtidige databaseopdateringer eller
applikationsautentifikation. Demoen er afgrænset til én lokal instans.
Review før eksekvering giver kontrol, men er ikke en sikkerhedssandbox.

## Anbefaling og begrundelse

Jeg vurderer, at LangGraph er en relevant kandidat, når jeg vil styre
rækkefølge, interfaces, reviews og genoptagelse eksplicit. Begge lokale
kørsler leverede en fungerende bookingdemo med beståede kvalitets- og
driftskontroller.

Den største ulempe var arbejdet med egen controllerkode og gentagen
fejlsøgning. Små modeller fulgte ikke konsekvent format, scope og kontrakt,
og nye forsøg gentog flere af de samme fejl. Workflowet blev først brugbart
med mindre opgaver, stærkere validering og reviewrettelser.

Jeg kan derfor anbefale LangGraph til denne type styrede lokale workflows,
men resultaterne dokumenterer ikke en selvkørende udviklingsproces eller
stabilitet uden indgreb i en fastlåst version.

## Dokumentation for resultaterne

Sammenligningen er gemt i `runs/comparison-run-01-run-02.md`. For hvert run
findes status i `summary.json` og `summary.md`, rå kald og reviewændringer i
`logs/`, samt målte kvalitets- og driftsresultater i
`demo/reports/quality.md` og `demo/reports/deployment.json`.

Workflowets styring kan gennemgås i `mh_toolchain/graph.py`, opgaverne i
`config/workflow.yaml` og rollefordelingen i
`llm_backend/config/endpoints.yaml`.
