# Add Mux Runner Support

## Summary

Add support for Mux as a third agent runner alongside Claude Code and Codex CLI. This enables Stokowski to leverage Mux's unique capabilities: sub-agent parallelization, structured planning, and cost-effective exploration modes.

## Motivation

Currently, Stokowski supports two agent runners:
- **Claude Code**: Full-featured agent with NDJSON streaming output
- **Codex CLI**: OpenAI's CLI with basic stdout/stderr output

Mux (https://mux.coder.com) offers distinct advantages for autonomous workflows:

| Capability | Claude | Codex | Mux |
|------------|--------|-------|-----|
| Direct execution | ✓ | ✓ | ✓ |
| Sub-agent parallelization | ✗ | ✗ | ✓ |
| Structured planning mode | Limited | ✗ | ✓ |
| Exploration mode | Limited | ✗ | ✓ |
| Cost-effective for investigations | - | ✓ | ✓ |

Adding Mux enables more sophisticated autonomous workflows where investigation and planning can be offloaded to Mux sub-agents while keeping implementation focused.

## Goals

1. Add Mux as a configurable runner (`runner: mux` in workflow.yaml)
2. Support Mux's exec agent for direct task execution
3. Parse Mux's NDJSON output stream similar to Claude Code
4. Maintain backward compatibility with existing Claude/Codex configurations

## Non-Goals

- Not implementing Mux's sub-agent dispatch within Stokowski (Mux handles this internally)
- Not adding Mux-specific configuration beyond standard runner options
- Not replacing Claude as the default runner

## Success Criteria

- `runner: mux` can be specified in any agent state in workflow.yaml
- Mux successfully executes tasks and reports completion status
- Token usage and timing metrics are captured
- Configuration validates successfully with `stokowski --dry-run`

## References

- Mux CLI Reference: https://mux.coder.com/reference/cli
- Existing runner implementation: `stokowski/runner.py`
- Workflow configuration: `workflow.example.yaml`
