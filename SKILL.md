---
name: antigravity_aonsavation_export1
description: |
  Local API conversation capture skill. v2 provides thread-log-sync style per-thread log+sqlite,
  heartbeat-capable events model, retention pruning, and optional legacy mirror for compatibility.
---

# Conversation Watchdog

## What changed (v2)
- Keep existing capture method: HTTP POST to `http://localhost:18888/log`
- Add thread-log-sync style storage:
  - Per-thread log: `<thread>.log`
  - Per-thread SQLite: `<thread>.sqlite` (`events`, `kv_state`)
- Add health endpoint: `GET /health`
- Add optional legacy mirror to desktop files:
  - `~/Desktop/conversation_log.md`
  - `~/Desktop/conversation_log.db`
- Add retention pruning: keep latest `20` threads by default

## Step-by-step rollout (with rollback)

### Step 1: Keep old service untouched, test v2 on temp port
```bash
python3 /Users/gaoxinwei/.gemini/antigravity/skills/antigravity_aonsavation_export1/scripts/server_v2.py \
  --port 18989 \
  --log-root /tmp/conversation_threads_test \
  --enable-legacy-mirror \
  --legacy-md /tmp/conversation_log_test.md \
  --legacy-db /tmp/conversation_log_test.db
```

Verify:
```bash
curl http://localhost:18989/health
curl -X POST http://localhost:18989/log -H 'Content-Type: application/json' \
  -d '{"role":"assistant","thread_name":"demo-thread","channel":"final","content":"test"}'
sqlite3 /tmp/conversation_threads_test/demo-thread.sqlite 'select reason,action,result from events;'
```

If failed: stop and fix `server_v2.py`, do not continue.

### Step 2: Switch startup to v2 (default), keep rollback path
```bash
/Users/gaoxinwei/.gemini/antigravity/skills/antigravity_aonsavation_export1/scripts/start.sh
```

Rollback to old server:
```bash
/Users/gaoxinwei/.gemini/antigravity/skills/antigravity_aonsavation_export1/scripts/start.sh legacy
```

### Step 3: Verify production port
```bash
curl http://localhost:18888/health
curl -X POST http://localhost:18888/log -H 'Content-Type: application/json' \
  -d '{"role":"assistant","thread_name":"prod-check","channel":"final","content":"hello"}'
```

## Payload schema (recommended)
```json
{
  "role": "assistant",
  "content": "text to store",
  "thread_name": "thread/window name",
  "channel": "final"
}
```

## Scripts
- `scripts/server.py`: legacy single-file logger
- `scripts/server_v2.py`: thread-log-sync style logger
- `scripts/start.sh`: default v2 startup with legacy fallback
- `scripts/send_log_sync.py`: reliable sender with retry + ack parsing

## Recommended call chain
- Use command: `send_log <ROLE> <CONTENT>`
- Command location: `/opt/homebrew/bin/send_log`
- Internally delegates to:
  `python3 /Users/gaoxinwei/.gemini/antigravity/skills/antigravity_aonsavation_export1/scripts/send_log_sync.py`
- Success criterion:
  sender returns JSON with `{"ok": true, "message_id": <id>}`.
