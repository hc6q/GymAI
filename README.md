<div align="center">

# GymAI

**Plan your training. Track every set. Ask AI only when you choose.**

A lightweight, self-hosted workout journal with optional Groq-powered feedback.
Built with FastAPI, SQLite, and plain JavaScript.

[![License: MIT](https://img.shields.io/badge/license-MIT-8fd6a3)](LICENSE) [![Python 3.12](https://img.shields.io/badge/python-3.12-3776ab)](Dockerfile) [![Tests](https://github.com/hc6q/GymAI/actions/workflows/tests.yml/badge.svg)](https://github.com/hc6q/GymAI/actions/workflows/tests.yml)

[Quick start](#quick-start) · [Features](#features) · [Configuration](#configuration) · [Backups](#backups) · [Contributing](CONTRIBUTING.md)

</div>

## Why GymAI?

Your training data stays on your server. AI is an optional tool, never a background process: viewing, editing, and logging workouts make **zero Groq calls**. Before sending a question, GymAI previews the compact training context. Repeated questions about unchanged data can reuse a cached answer.

## Features

| Training | AI and data |
| --- | --- |
| Seven-day split with rest days | Explicit, question-driven Groq requests |
| Exercise library plus custom exercises | Context preview: week, one day, or exercise history |
| Simple or per-set targets, load and notes | Response cache with manual refresh |
| Mobile-friendly session logging | Token usage log when Groq provides usage |
| History, exercise filters, progression chart | JSON export/import and SQLite backups |

Optional single-user login, signed session cookies, CSRF checks, and server-side API keys are included. No frontend build step, CDN, or external chart library is needed.

## Quick start

Requires Docker and Docker Compose.

```bash
git clone https://github.com/hc6q/GymAI.git
cd GymAI
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' # copy to SECRET_KEY in .env
nano .env
mkdir -p data
sudo chown -R 10001:10001 data
docker compose up -d --build
```

Open **http://YOUR_SERVER_IP:8085**. Run `docker compose logs -f gymai` if startup fails. On older installations, `docker-compose` may replace `docker compose`.

### Linux Mint (resumo)

Clone o repositório, copie `.env.example` para `.env`, preencha `GROQ_API_KEY`, gere um `SECRET_KEY` aleatório e execute os comandos acima. Acesse `http://IP_DO_SERVIDOR:8085`. Se for publicar na internet, configure usuário, senha e HTTPS em um proxy reverso.

## Configuration

Edit `.env` before starting the container:

| Variable | Purpose |
| --- | --- |
| `GROQ_API_KEY` | Optional Groq key. AI requests require it. Never commit your `.env`. |
| `GROQ_MODEL` | Default: `openai/gpt-oss-120b`. Change it if the model is unavailable to your account. |
| `GROQ_MAX_TOKENS` | Token limit for **Normal** responses. Default: `400`. |
| `APP_USERNAME`, `APP_PASSWORD` | Optional login; set both to enable it. |
| `SECRET_KEY` | Random secret for signed login cookies. Required when login is enabled. |
| `DATABASE_PATH` | Default: `/app/data/gym.db`. |

Port `8085` on your host maps to `8000` in the container. Change the left side of `8085:8000` in `docker-compose.yml` if needed. The bind-mounted `./data` folder survives container recreation.

**Public access:** Enable login and put the app behind HTTPS before exposing it to the internet. Plain HTTP does not protect login credentials in transit. If both login variables are empty, anyone who can reach the app can change its data. Limit network access accordingly.

## How AI requests work

1. Select a week, day, or exercise in **Assistant**.
2. Review the training context shown on screen and write a question.
3. Click **Send to AI**. This is the only action that requests a generated answer from Groq.

The **Test Groq** button makes an explicit connectivity request but does not generate an answer. A matching question, context, and model can reuse a cached response. Check **Generate new response** to bypass the cache. The AI is not a substitute for medical or physiotherapy advice.

In **Settings**, choose **Short** (default: 250 completion tokens, about 150 words), **Normal** (400 tokens by default, about 250 words), or **Detailed** (900 tokens, about 600 words). Asking for a detailed analysis temporarily selects Detailed for that question. These are upper bounds, not targets; the response may be shorter. GPT-OSS uses low reasoning effort to conserve its completion-token budget. The cache includes the selected size, so older long answers are not reused. Questions naming a single weekday send only that day's plan; questions about a named exercise's progression send only its recent history. The context preview updates as you type without calling Groq.

## Backups

**Export data** downloads a JSON backup of exercises, schedules, and workout history. **Import backup** replaces those records after confirmation; it does not include Groq keys or AI usage logs.

For a consistent, complete SQLite backup while the service is running:

```bash
docker compose exec gymai python -c 'import sqlite3; c=sqlite3.connect("/app/data/gym.db"); b=sqlite3.connect("/app/data/gym-backup.db"); c.backup(b); b.close(); c.close()'
cp data/gym-backup.db ~/gym-backup.db
```

Restore with the service stopped:

```bash
docker compose down
cp ~/gym-backup.db data/gym.db
sudo chown 10001:10001 data/gym.db
docker compose up -d
```

Keep `.env` separately. You can also copy the entire `data/` folder while the service is stopped.

## Update

```bash
git pull
docker compose up -d --build
```

Back up `data/` before updating.

## Development

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pytest
DATABASE_PATH=./data/gym.db uvicorn app.main:app --reload
python -m pytest -q
```

Project layout:

```text
app/main.py             FastAPI routes, SQLite schema, authentication, Groq
app/templates/           Jinja2 page shells
app/static/              Responsive interface in vanilla JS and CSS
tests/                   API and authentication tests
data/                    Persistent SQLite database (ignored by Git)
```

The schema is created automatically on first launch. Training logs retain exercise names so historic records remain readable if a routine changes. The current app supports one training routine per weekday and one owner; it does not manage multiple users. An active session remains in browser memory until it is finalized, so reloading the page can discard unfinished entries. Docker startup and live Groq calls should be checked in your own deployment environment.

## Contributing

Bug reports, small improvements, and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). For sensitive security reports, see [SECURITY.md](SECURITY.md).

## License

GymAI is available under the [MIT License](LICENSE). Copyright © 2026 hc6q.
