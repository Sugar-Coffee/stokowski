# Design: Mux Runner Integration

## Overview

This design adds Mux as a third agent runner in Stokowski, following the existing pattern established by Claude and Codex runners.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Orchestrator                            │
│                    (poll loop, state machine)                     │
└─────────────────────────┬───────────────────────────────────────┘
                          │
           ┌──────────────┼──────────────┐
           │              │              │
           ▼              ▼              ▼
    ┌──────────┐   ┌──────────┐   ┌──────────────┐
    │  run_    │   │  run_    │   │    run_      │
    │claude_   │   │ codex_   │   │   mux_turn   │◄── NEW
    │  turn()  │   │  turn()  │   │              │
    └────┬─────┘   └────┬─────┘   └──────┬───────┘
         │              │                │
         ▼              ▼                ▼
    ┌──────────┐   ┌──────────┐   ┌──────────────┐
    │  claude  │   │  codex   │   │     mux      │
    │   -p     │   │--prompt  │   │   exec -p    │
    └──────────┘   └──────────┘   └──────────────┘
```

## Key Design Decisions

### 1. Runner Pattern Consistency

Mux follows the exact same pattern as existing runners:
- **Entry point**: `run_turn()` dispatcher in `runner.py`
- **Implementation**: `run_mux_turn()` function
- **CLI invocation**: `mux exec -p <prompt>`
- **Output parsing**: NDJSON stream (similar to Claude)

### 2. Mux CLI Command Structure

```bash
# Mux command for Stokowski headless execution
mux exec -p "<prompt>" \
  --json \
  --agent-id stokowski-<issue-id> \
  --timeout <turn_timeout>
```

Key flags:
- `exec`: Subcommand for direct task execution (not explore/plan)
- `-p, --prompt`: The task prompt
- `--json`: Output NDJSON events (for streaming)
- `--agent-id`: Unique ID for session tracking
- `--timeout`: Maximum execution time

### 3. NDJSON Event Parsing

Mux outputs NDJSON events similar to Claude Code:

```jsonl
{"type": "start", "agent_id": "stokowski-abc123", "timestamp": "..."}
{"type": "tool_use", "tool": "Bash", "args": {...}}
{"type": "assistant", "message": {...}}
{"type": "result", "status": "success", "usage": {"input_tokens": 1500, "output_tokens": 800}}
```

The existing `_process_event()` function in `runner.py` can handle these with minimal adaptation.

### 4. Configuration Integration

```yaml
# workflow.yaml
states:
  investigate:
    type: agent
    runner: mux        # NEW: mux option
    prompt: prompts/investigate.md
    model: claude-4    # Optional: passed to Mux
    max_turns: 10
    session: inherit   # Note: Mux may not support session resumption
```

### 5. ClaudeConfig Reuse

Mux uses the same configuration structure as Claude:
- `model`: Optional model override
- `turn_timeout_ms`: Execution timeout
- `stall_timeout_ms`: Stall detection
- All hooks work identically

## Data Flow

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│ Workflow │────▶│  Config  │────▶│  Runner  │────▶│   Mux    │
│   YAML   │     │  Parse   │     │ Dispatch │     │  exec    │
└──────────┘     └──────────┘     └──────────┘     └────┬─────┘
                                                        │
                     ┌──────────────────────────────────┘
                     ▼
┌──────────┐     ┌──────────┐     ┌──────────┐
│  NDJSON  │◄────│  Parse   │◄────│  Stream  │◄───┘
│ Events   │     │ Events   │     │  stdout  │
└────┬─────┘     └──────────┘     └──────────┘
     │
     ▼
┌──────────┐
│ RunAttempt│───▶ Status, tokens, messages
└──────────┘
```

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Mux not installed | Graceful error: "Mux command not found" |
| NDJSON parse error | Skip line, continue streaming |
| Timeout | Kill process, report `timed_out` |
| Stall detected | Kill process, report `stalled` |
| Non-zero exit | Report `failed` with stderr |

## Files Modified

| File | Changes |
|------|---------|
| `stokowski/runner.py` | Add `run_mux_turn()`, update `run_turn()` dispatcher |
| `stokowski/config.py` | No changes (ClaudeConfig reused) |
| `workflow.example.yaml` | Add `runner: mux` examples |

## Backward Compatibility

- Default runner remains `claude` when not specified
- Existing configurations work unchanged
- `runner: codex` continues to work as before

## Testing Strategy

1. **Unit tests**: Mock subprocess, verify event parsing
2. **Integration test**: Run against test Linear issue
3. **Dry-run validation**: `stokowski --dry-run` with mux config

## Future Enhancements (Out of Scope)

- Mux explore mode integration (could be a separate runner)
- Mux plan mode for complex tasks
- Automatic agent selection based on task type
