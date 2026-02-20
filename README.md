# Repo Oracle Studio (AI-First Tool)

`repo_oracle.py` + `web_app.py` are a practical AI engineering toolset powered by OpenGradient.

## What makes it AI-first

- 2-stage reasoning pipeline in `ask`:
1. Planner stage: model picks relevant files and analysis focus.
2. Analyst stage: model answers with citations from selected files.
- Dedicated AI code-review mode over git diff.
- Web UI is just an interface; reasoning and output quality come from the model pipeline.

## Requirements

- Python 3.10+
- Dependencies in `requirements.txt`
- OpenGradient private key in one of:
  - `OG_PRIVATE_KEY`
  - `OPENGRADIENT_PRIVATE_KEY` (alias)
  - `~/.opengradient_config.json` (`private_key`)
- Wallet fee gate:
  - Ask/Review uses credit packs on Base Sepolia:
    - 1 payment of `0.0001 OPG` unlocks 10 runs
  - Optional env overrides:
    - `OPG_FEE_AMOUNT` (default `0.0001`)
    - `RUNS_PER_FEE_TX` (default `10`)
    - `OPG_FEE_RECEIVER` (default: backend wallet)
    - `OPG_TOKEN_ADDRESS` (default OPG token on Base Sepolia)

## CLI usage

```bash
cd /Users/rivale/Documents/New\ project/tool_workspace
python3 repo_oracle.py ask "How does wallet payment gate work?" --root /Users/rivale/Documents/New\ project/oracle_game
```

```bash
python3 repo_oracle.py review --root /Users/rivale/Documents/New\ project --target HEAD~1
```

GitHub source example:

```bash
python3 repo_oracle.py ask "Summarize architecture" --root https://github.com/pallets/flask
```

## Web usage

```bash
cd /Users/rivale/Documents/New\ project/tool_workspace
python3 web_app.py
```

Open: `http://localhost:8090`

UI features:
- Ask mode: question + max files + optional model override
- Review mode: git diff review by target
- Repository source accepts local path OR GitHub link
- Planner files + focus visible
- Copy output button
