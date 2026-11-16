# Free Model Aggregator

A minimal Python server that aggregates [OpenRouter](https://openrouter.ai) free models behind OpenAI-compatible and Anthropic-compatible API endpoints.

**Zero dependencies** — stdlib only (`http.server`, `urllib`, `json`).

## Quick start

```bash
export OPENROUTER_API_KEY=sk-or-...
python main.py
```

Server listens on `0.0.0.0:8080` by default.

## Endpoints

| Method | Path                    | Description                    |
|--------|-------------------------|--------------------------------|
| POST   | `/v1/chat/completions`  | OpenAI-compatible chat         |
| POST   | `/v1/messages`          | Anthropic-compatible messages  |
| GET    | `/v1/models`            | List available free models     |
| GET    | `/health`               | Health check                   |

## How it works

1. On first request, the server fetches the free-model catalog from `https://openrouter.ai/api/v1/models?free=true`.
2. Incoming requests are routed to the appropriate free model on OpenRouter's `/api/v1/chat/completions`.
3. Anthropic-format requests are transparently converted to OpenAI format before proxying, and responses are converted back.

## Configuration

| Variable              | Default                  | Description              |
|-----------------------|--------------------------|--------------------------|
| `OPENROUTER_API_KEY`  | (required)               | OpenRouter API key       |
| `PORT`                | `8080`                   | Server port              |
| `HOST`                | `0.0.0.0`                | Bind address             |

## Example — OpenAI

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hello!"}]}'
```

## Example — Anthropic

```bash
curl -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"messages": [{"role": "user", "content": "Hello!"}], "max_tokens": 256}'
```

## License

MIT