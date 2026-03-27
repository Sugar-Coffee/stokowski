# Mux Runner Specification

## Overview

Specification for the Mux agent runner integration in Stokowski.

## Functional Requirements

### FR-1: Runner Dispatch

**Given** a workflow.yaml with `runner: mux` in a state configuration  
**When** the orchestrator dispatches an agent for that state  
**Then** the Mux runner is invoked instead of Claude or Codex

### FR-2: CLI Invocation

**Given** a task to execute  
**When** the Mux runner starts  
**Then** it invokes: `mux exec -p "<prompt>" --json [options]`

Options supported:
- `--model <model>`: Optional model override
- `--agent-id <id>`: Unique identifier for the run
- `--timeout <seconds>`: Maximum execution time

### FR-3: NDJSON Stream Parsing

**Given** Mux outputs NDJSON events to stdout  
**When** streaming output  
**Then** the runner parses and processes each event

Supported event types:
- `start`: Execution started
- `tool_use`: Tool invocation
- `assistant`: Assistant message
- `result`: Final result with status and usage

### FR-4: Status Reporting

**Given** Mux execution completes  
**When** parsing the result event or exit code  
**Then** the runner updates RunAttempt with:
- `status`: `succeeded`, `failed`, `timed_out`, or `stalled`
- `error`: Error message if failed
- `input_tokens`: Token count from usage
- `output_tokens`: Token count from usage
- `total_tokens`: Total tokens used

### FR-5: Hook Integration

**Given** hooks are configured in workflow.yaml  
**When** running Mux agent  
**Then** all hooks execute:
- `before_run`: Before starting Mux
- `after_run`: After Mux completes

### FR-6: Error Handling

| Error Condition | Expected Behavior |
|-----------------|-------------------|
| Mux not installed | `RunAttempt.status = "failed"`, error = "Mux command not found" |
| Invalid NDJSON line | Skip line, continue streaming |
| Timeout exceeded | Kill process, status = `timed_out` |
| No output for stall_timeout | Kill process, status = `stalled` |
| Non-zero exit code | status = `failed`, capture stderr |

## Non-Functional Requirements

### NFR-1: Performance

- Startup latency: Mux should start within 5 seconds
- Streaming: Events processed in real-time (< 100ms delay)

### NFR-2: Compatibility

- Mux version: Support mux CLI 0.1.0+
- Backward: Existing Claude/Codex runners unaffected

### NFR-3: Logging

- Log Mux command at INFO level
- Log event types at DEBUG level
- Log completion at INFO level with token counts

## Interface Specification

### Python Function

```python
async def run_mux_turn(
    model: str | None,
    hooks_cfg: HooksConfig,
    prompt: str,
    workspace_path: Path,
    issue: Issue,
    attempt: RunAttempt,
    on_pid: PidCallback | None = None,
    turn_timeout_ms: int = 3_600_000,
    stall_timeout_ms: int = 300_000,
    env: dict[str, str] | None = None,
) -> RunAttempt:
    """Run a single Mux exec turn."""
```

### Configuration Schema

```yaml
states:
  my-state:
    type: agent
    runner: mux              # NEW
    model: claude-4          # Optional
    turn_timeout_ms: 3600000 # Optional override
    stall_timeout_ms: 300000 # Optional override
    # ... other options same as claude
```

## Test Scenarios

### TC-1: Basic Execution
1. Configure `runner: mux` in workflow.yaml
2. Create test Linear issue
3. Run Stokowski
4. **Verify**: Mux executes and reports success

### TC-2: Error Handling
1. Temporarily rename `mux` binary
2. Run Stokowski
3. **Verify**: Graceful failure with clear error message

### TC-3: Token Tracking
1. Run Mux on a test issue
2. **Verify**: Token counts appear in Linear comment

### TC-4: Hook Execution
1. Configure `before_run` and `after_run` hooks
2. Run Mux agent
3. **Verify**: Both hooks execute in correct order

## Open Questions

1. Does Mux support session resumption (`--resume`)? → Likely no, treat as fresh session
2. What Mux event types need special handling? → To be determined during implementation
3. Should we support Mux sub-agent limits? → Future enhancement
