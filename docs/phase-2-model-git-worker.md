# Real Model And Git Worker

## Credential Resources

| Provider | Credential | Network route |
| --- | --- | --- |
| ChatGPT Plus | `CODEX_HOME/auth.json` managed by Codex CLI | required proxy |
| ChatGPT Pro | `CODEX_HOME/auth.json` managed by Codex CLI | required proxy |
| GPT API | key in private provider environment file | required proxy |
| DeepSeek API | key in private provider environment file | direct |
| MiniMax API | key in private provider environment file | direct |

The API returns masks and credential references, never credential contents.
Account login uses `scripts/codex_account_login.sh plus|pro` and the device page
`https://auth.openai.com/codex/device`.

## Role Routes

| Role | Ordered route |
| --- | --- |
| Planner | Plus, Pro, GPT API planner model |
| Coder | Plus, Pro, GPT API coder model |
| Reviewer | DeepSeek API |
| Risk | MiniMax API |
| Supervisor | Plus, Pro, GPT API supervisor model |

Failures enter a private operational health store. A provider in cooldown is
skipped with its reason recorded in the model trace. When cooldown expires the
preferred provider is tried first again; success clears its failure state.

## Git Execution Contract

1. Only registered projects can execute.
2. Every run receives `taskhub/<run-id>` and its own worktree.
3. The coding model can write only inside that worktree.
4. Credential files and generated output are rejected or excluded.
5. Tests run in the same worktree.
6. TaskHub stores a binary patch artifact, then commits the staged source changes.
7. The authority branch is unchanged until a later governed merge action.
