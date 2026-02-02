# RULES FOR CLAUDE CHAT

## Bot restart
- Command: `sudo systemctl restart aila`
- Before restart: check open positions via `bot_positions.json`
- After restart: verify with `systemctl status aila | head -5`

## Git operations
- After each task: `git add`, `commit`, `push` to current branch
- No confirmation needed for git ops
- Commit to current branch only, do not create new branches
- Commit messages in English: `feat/fix/docs/refactor: description`
- Update `CLAUDE_MEMO.md` after each task

## Language
- Reply in Russian
- Code comments in English

## Trading safety
- Do NOT change trading parameters without explicit request
- Do NOT disable Risk Guard without explicit confirmation
- Show what changed when modifying config
- RISK_GUARD has absolute VETO — never override

## Code interaction
- In Manual mode: confirm understanding before executing
- In Auto mode: describe task first, wait for confirmation
- Use `[COMMAND_FOR_CODE]command[/COMMAND_FOR_CODE]` for server commands
- Always use absolute paths: `/opt/aila/...`
- Check execution results, suggest fixes on errors
- Do NOT delete files without explicit request

## Settings changes
- Verify setting saves correctly in form
- Verify value reaches trading engine
- Verify no conflicts with other settings
- Add logging for new settings
- Add audit entry in `trades_audit.py`

## API logging
- Every Claude API call must log: agent, action, model, tokens, cost, context
- Log file: `/opt/aila/logs/ai_trade/api_usage.log`

## Exchange code
- Use `normalize_symbol()` for all symbols
- Use `round_qty()` / `round_price()` before orders
- Add `@retry_async` to all API methods
- Check `/opt/aila/docs/EXCHANGE_CHECKLIST.md`

## Code quality
- async/await where possible
- Type hints, docstrings
- DRY, max 50 lines per function
- Try/except with specific exceptions
- Retry with backoff for API calls

## Knowledge management
- `[UPDATE_KNOWLEDGE]RULES.md|append|text[/UPDATE_KNOWLEDGE]` — add rule
- `[UPDATE_KNOWLEDGE]RULES.md|remove|text[/UPDATE_KNOWLEDGE]` — remove rule

## User rules
<!-- User rules added via UPDATE_KNOWLEDGE -->
