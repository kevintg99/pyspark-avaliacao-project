# broll-bot: narração + roteiro → `video_final.mp4`

Automação em Python que recebe um **áudio de narração (.mp3)** e o **roteiro (.txt/.md)** e devolve um
`video_final.mp4` **sem legendas**, com b-rolls (vídeos e imagens) que combinam com o que está sendo
dito em cada momento. Os clipes são cortados na duração certa e as transições são feitas com FFmpeg.

```bash
python main.py --audio narracao.mp3 --script roteiro.txt --out video_final.mp4
```

---

## Plano de arquitetura (resumo)

```
narracao.mp3 ─┐
              ├─► 1. transcrição (faster-whisper, só timestamps) ─► 2. alinhamento com o roteiro
roteiro.md ───┘                                                       │
                                                                      ▼
            3. cenas semânticas (Claude: resumo visual, consultas EN, termos a evitar, tom)
                                                                      │
                                                                      ▼
            4. linha do tempo: cortes nas pausas da fala, clipes ≤ 7s com ms "quebrados",
               transições variadas, quantização em frames sem deriva
                                                                      │
                                                                      ▼
            5. busca por cena: Mixkit local → Pexels → Pixabay (cache SQLite, rate limit,
               retry/backoff, circuit breaker, máx. 3 chamadas de API por cena)
                                                                      │
                                                                      ▼
            6. ranking: CLIP compara o resumo visual da cena com a thumbnail real
               + resolução, duração, orientação, penalidade p/ texto/marca d'água
                                                                      │
                                                                      ▼
            7. seleção: baixa só os vencedores (+1 reserva/cena), valida com ffprobe,
               deduplica por hash, evita clipes parecidos em sequência
                                                                      │
                                                                      ▼
            8. FFmpeg: enquadramentos variados (full, zoom/crop, push, inset com fundo
               desfocado, Ken Burns) → xfade/concat em blocos → mux da narração
                                                                      │
                                                                      ▼
                                   video_final.mp4 + report.json + log JSONL
```

**Decisões principais**

| Decisão | Por quê |
|---|---|
| Uma única chamada ao Claude para o roteiro inteiro, com saída estruturada (Pydantic) e cache | Agrupa por *ideia* (não por frase), gera consultas curtas em inglês e custa uma requisição por roteiro. Reexecuções usam o cache. |
| CLIP sobre a **thumbnail** antes de baixar | Mede se a imagem realmente mostra o que a cena descreve. Baixa-se só o vencedor, então thumbnails pequenas economizam banda e requisições. |
| Orçamento de buscas por cena + parada antecipada | Para de buscar assim que há candidatos bons suficientes. Cache hit e biblioteca local não gastam orçamento. |
| Cortes em frames a partir de posições acumuladas | As durações têm milissegundos quebrados, mas a sincronia com o áudio não deriva (erro final < 1 frame + padding do AAC). |
| Tudo com plano B | LLM falhou → heurística. Whisper ausente → estimativa proporcional. Fonte falhou → próxima fonte. Download corrompido → próximo candidato. Render falhou → reserva. Sem nada → fundo gerado. |
| Seed única, com seed derivada por shot | O mesmo `--seed` gera o mesmo vídeo, inclusive com renderização em paralelo. |

---

## Instalação

Requisitos: **Python 3.11+** e **FFmpeg 5+** (com `ffprobe`) no PATH.

```bash
# Ubuntu/Debian: sudo apt install ffmpeg   ·   macOS: brew install ffmpeg   ·   Windows: winget install ffmpeg
cd video_automation
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # CPU: pip install torch --index-url https://download.pytorch.org/whl/cpu
cp .env.example .env                     # preencha PEXELS_API_KEY, PIXABAY_API_KEY (e ANTHROPIC_API_KEY)
```

- Chave do Pexels: <https://www.pexels.com/api/> · Chave do Pixabay: <https://pixabay.com/api/docs/>
- A análise de cenas usa o Claude (`claude-opus-5-5`). Sem credencial da Anthropic, o pipeline usa a
  heurística local automaticamente (funciona, mas as consultas ficam no idioma do roteiro e a relevância cai).
- `faster-whisper`, `open_clip_torch` e `torch` são **opcionais** mas recomendados. Sem eles, o bot usa
  estimativa de tempo proporcional e ranking por metadados.

## Uso

```bash
python main.py --audio narracao.mp3 --script roteiro.md --out video_final.mp4
```

| Flag | Efeito |
|---|---|
| `--config config.yaml` | arquivo de configuração (padrão: `config.yaml`) |
| `--seed 123` | outra variação do vídeo, reproduzível |
| `--no-llm` | análise heurística, sem Claude |
| `--no-clip` | ranking só por metadados (mais leve, sem PyTorch) |
| `--no-transcribe` | não transcreve; estima o tempo de cada frase |
| `--reindex-library` | força reindexar a biblioteca local |
| `--keep-temp` | mantém os shots intermediários em `.work/<execução>/render` |
| `--report caminho.json` | onde salvar o relatório (padrão: ao lado do vídeo) |

Saídas:

- `video_final.mp4`: H.264 + AAC, `yuv420p`, `+faststart`, 1920x1080@30 (configurável), com exatamente a duração da narração.
- `report.json`: para cada cena, o texto narrado, o resumo visual, as consultas, as tentativas de busca
  (fonte, cache, erro) e, para cada shot, fonte, ID, URL, score, duração aplicada, transição, enquadramento,
  ponto de entrada e fallback usado. Inclui também a lista de **créditos** (autor e URL) para atribuição.
- `.work/<execução>/pipeline.jsonl`: log estruturado de todas as etapas.

### Biblioteca local (Mixkit)

O Mixkit não tem API pública e não permite scraping. Baixe os clipes manualmente em
`library/mixkit/` (os nomes originais, como `mixkit-woman-typing-on-laptop-4321-hd-ready.mp4`, já viram tags).
Opcionalmente, crie um `.json` com o mesmo nome do clipe para melhorar a busca:

```json
{"tags": ["office", "laptop", "typing"], "description": "woman typing on a laptop", "page_url": "https://mixkit.co/..."}
```

O índice (duração, resolução, thumbnail, tags) é incremental: só arquivos novos ou alterados são reprocessados.
A biblioteca local é a primeira fonte da fila por ser gratuita e ilimitada (não gasta orçamento de API).

### Estrutura

```
video_automation/
├── main.py                  # CLI
├── config.yaml              # parâmetros (resolução, fontes, limites, durações, seed…)
├── broll_bot/
│   ├── cli.py               # argumentos e execução
│   ├── pipeline.py          # orquestração + report.json
│   ├── transcription.py     # parse do roteiro, faster-whisper, alinhamento
│   ├── scenes.py            # cenas semânticas (Claude ou heurística)
│   ├── timeline.py          # cortes, durações ≤ 7s com ms quebrados, transições
│   ├── search.py            # busca por cena: orçamento, cache, fallback, circuit breaker
│   ├── ranking.py           # CLIP + critérios secundários, similaridade entre clipes
│   ├── selection.py         # download dos vencedores, reserva, deduplicação, planos B
│   ├── framing.py           # enquadramentos e Ken Burns (filtergraphs)
│   ├── assembler.py         # FFmpeg: shots, xfade/concat em blocos, mux do áudio
│   ├── cache.py             # cache SQLite com TTL
│   ├── ratelimit.py         # token bucket, retry/backoff, circuit breaker
│   ├── http.py              # cliente HTTP com leitura de X-RateLimit-*
│   ├── media.py             # ffprobe/ffmpeg
│   └── providers/           # BaseProvider, Pexels, Pixabay, biblioteca local
└── tests/                   # cache, fallback, http, timeline, alinhamento, cenas, providers
```

---

## Como o bot garante relevância

1. **Cena por ideia.** O Claude lê o roteiro com o tempo de cada frase e decide onde o assunto muda.
2. **Consulta pensada para acervos de stock.** Consultas de 2–4 palavras, nomeando sujeito e ação
   (`"woman typing laptop"`), da mais específica para a mais genérica, além de uma consulta reserva.
3. **Ranking visual.** O CLIP compara o *resumo visual* da cena (ex.: "close-up de mãos contando moedas
   numa mesa de madeira") e as consultas com a thumbnail real de cada candidato. Metadados (tags, título)
   entram só como desempate.
4. **Penalidades.** Imagens com texto, logo ou marca d'água, e candidatos mais parecidos com os
   *termos a evitar* do que com a cena, perdem pontos. Resolução baixa e orientação vertical também.
5. **Score mínimo.** Se ninguém atinge `min_score`, a busca é reformulada com a próxima consulta, dentro do
   orçamento da cena.
6. **Variedade.** O mesmo clipe nunca se repete (a não ser como último plano B, com outro enquadramento e
   ponto de entrada), e clipes visualmente quase iguais (cosseno CLIP ≥ 0.93) não entram em sequência.

## Como o bot evita rate limit e bloqueio

- **Cache SQLite** de todas as buscas (TTL de 24h por padrão, como a Pixabay exige), da transcrição, do
  plano de cenas e dos embeddings. Rodar de novo o mesmo roteiro custa quase zero requisições.
- **Orçamento por cena** (`max_searches_per_scene: 3`): cache hit e biblioteca local não contam.
- **Parada antecipada:** assim que há `shots + 1 reserva` candidatos bons, a busca da cena termina.
- **Token bucket por fonte** configurado abaixo dos limites oficiais (Pexels 200/h, Pixabay 100/min).
  Os headers `X-RateLimit-Remaining`/`Reset` pausam a fonte quando a cota zera.
- **Retry** com backoff exponencial e *jitter* para 429/5xx. Em 429 com reset longo, o bot desiste na hora
  e passa para a próxima fonte, sem ficar esperando.
- **Circuit breaker:** 3 falhas seguidas desativam a fonte por 5 minutos. Chave inválida, cota esgotada ou
  falta de conexão desativam na hora.
- **Downloads mínimos:** só o vencedor de cada shot e uma reserva por cena. Os arquivos ficam em
  `.cache/media` e são reaproveitados entre execuções.

## Regras de montagem

- Cada b-roll (vídeo ou imagem) dura **no máximo 7s, já contando a sobreposição da transição**.
  As durações sempre têm milissegundos quebrados (ex.: 3.487s, 5.132s), nunca se repetem e variam entre
  `min_clip_seconds` e 7s.
- Cenas longas são cobertas por vários b-rolls. Os cortes internos procuram as **pausas da fala**.
- Os vídeos entram em pontos diferentes do clipe. Clipes curtos ganham câmera lenta leve (até 1.6x)
  em vez de loop.
- Enquadramentos: `full`, `zoom_crop` (zoom 1.08–1.28 com reenquadramento), `push` (zoom lento com
  easing), `inset` (mídia menor sobre fundo desfocado da própria mídia; sempre usado para mídia vertical)
  e `kenburns` para imagens (zoom in/out, pans e diagonais com intensidade variada).
- Transições `xfade` de 0.2 a 0.6s, sem repetir o tipo em sequência, mais cortes secos (35% dentro da cena,
  10% na troca de cena). Os tipos disponíveis são conferidos no FFmpeg instalado.
- Nenhum texto ou legenda é desenhado no vídeo.

## Adicionando uma nova fonte

1. Crie `broll_bot/providers/minha_fonte.py` com uma classe `BaseProvider` que implemente `search()` e
   `download()`. Levante `ProviderError`, `RateLimitedError` ou `AuthError` em falhas; use `HttpClient`
   para ganhar rate limit e retry.
2. Registre a fonte em `providers/__init__.py::build_providers` e inclua o nome em `search.provider_order`.

Veja **[FONTES.md](FONTES.md)** para a análise de outras fontes (gratuitas e pagas) e os cuidados de licença.

## Testes

```bash
pytest -q
```

Os testes cobrem cache (TTL, persistência, arquivo corrompido), rate limit/backoff/circuit breaker, cliente
HTTP (5xx, 429, 401, sem conexão, headers), fallback entre fontes e orçamento de buscas, planejamento de
durações (≤ 7s, ms quebrados, sem repetição, cobertura total, snap nas pausas, reprodutibilidade),
alinhamento roteiro↔fala, plano de cenas e parsing das respostas do Pexels e do Pixabay. Nenhum teste usa rede.

## Limitações conhecidas

- O CLIP avalia a thumbnail, não o clipe inteiro. Um vídeo que muda muito de cena pode ter trechos menos
  aderentes (o ponto de entrada é sorteado).
- No modo heurístico (sem LLM), as consultas saem no idioma do roteiro. O Pexels e o Pixabay aceitam
  português, mas o acervo responde melhor em inglês.
- A primeira execução com CLIP baixa os pesos do modelo (~600 MB para ViT-B-32).
