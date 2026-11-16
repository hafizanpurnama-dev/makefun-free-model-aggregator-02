# OpenRouter Free Model Proxy

> 🏆 Entry for [make.fun bounty: The best live free-model aggregator](https://make.fun/bounty/free-model-aggregator-02)

[![make.fun · free-model-aggregator-02](https://make.fun/badge/free-model-aggregator-02.svg)](https://make.fun/bounty/free-model-aggregator-02?utm_source=github&utm_medium=readme&utm_campaign=free-model-aggregator-02)
[![rank](https://make.fun/badge/free-model-aggregator-02/hafizanpurnama-dev/makefun-free-model-aggregator-02/rank.svg)](https://make.fun/bounty/free-model-aggregator-02/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=free-model-aggregator-02)
[![reward](https://make.fun/badge/free-model-aggregator-02/hafizanpurnama-dev/makefun-free-model-aggregator-02/reward.svg)](https://make.fun/bounty/free-model-aggregator-02?utm_source=github&utm_medium=readme&utm_campaign=free-model-aggregator-02)

A single server that exposes both OpenAI-compatible and Anthropic-compatible endpoints, routing requests to OpenRouter's free models automatically.

## How it works

- `POST /v1/chat/completions` — OpenAI-compatible chat completions
- `POST /v1/messages` — Anthropic-compatible messages API
- Auto-discovers free models from OpenRouter
- Routes to the cheapest available free model

## Run

```bash
python3 main.py --port 8080
```

## License

MIT
