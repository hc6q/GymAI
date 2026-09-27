# Contributing to GymAI

Thanks for helping improve GymAI. Issues and focused pull requests are welcome.

## Before you start

- Search existing issues to avoid duplicates.
- For larger changes, open an issue to discuss the behavior first.
- Never include `.env`, API keys, workout data, or a populated `data/` directory in a report or commit.

## Local setup

Use Python 3.12, then:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pytest
DATABASE_PATH=./data/gym.db python -m pytest -q
node --check app/static/app.js
```

Describe what changed and how you verified it in your pull request. Keep dependencies small and preserve the rule that Groq is called only after an explicit user action. When changing data structures, include a safe migration strategy for existing databases.

By contributing, you agree to license your contribution under the repository's MIT License.
