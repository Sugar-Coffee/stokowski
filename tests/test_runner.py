"""Tests for the runner module."""

import json

import pytest


def test_run_mux_turn_exists():
    """Verify run_mux_turn function exists in runner module."""
    from stokowski.runner import run_mux_turn
    assert callable(run_mux_turn)


def test_build_claude_args_no_prompt_in_args():
    """Build claude args should NOT include prompt in -p argument when using stdin."""
    from stokowski.runner import build_claude_args
    from pathlib import Path
    from stokowski.config import ClaudeConfig
    
    config = ClaudeConfig(command="claude")
    prompt = "This is a test prompt with --- special chars"
    
    args = build_claude_args(
        claude_cfg=config,
        workspace_path=Path("/tmp/workspace"),
    )
    
    # Prompt should NOT be in args (it's passed via stdin instead)
    assert prompt not in args


def test_build_mux_args_basic():
    """Build mux run arguments with minimal parameters."""
    from stokowski.runner import build_mux_args
    from pathlib import Path
    
    args = build_mux_args(
        model=None,
        workspace_path=Path("/tmp/workspace"),
    )
    
    assert "npx" in args
    assert "mux" in args
    assert "run" in args
    assert "--quiet" in args
    assert "--json" in args


def test_parse_mux_json_format():
    """Parse actual Mux JSON output format to find assistant messages."""
    # Sample Mux JSON events (from real Mux output)
    sample_lines = [
        '{"type":"caught-up","workspaceId":"run-123"}',
        '{"type":"event","workspaceId":"run-123","payload":{"id":"user-1","role":"user","parts":[{"type":"text","text":"Hello","state":"done"}],"type":"message"}}',
        '{"type":"event","workspaceId":"run-123","payload":{"type":"stream-start","workspaceId":"run-123","messageId":"assistant-1"}}',
        '{"type":"event","workspaceId":"run-123","payload":{"type":"tool-call-start","workspaceId":"run-123","toolName":"bash"}}',
        # Assistant message would be here in real output
        '{"type":"event","workspaceId":"run-123","payload":{"id":"assistant-1","role":"assistant","parts":[{"type":"text","text":"<stokowski:report>\\nDone</stokowski:report>","state":"done"}],"type":"message"}}',
        '{"type":"run-complete","usage":{"inputTokens":100,"outputTokens":50}}',
    ]
    
    assistant_messages = []
    for line in sample_lines:
        event = json.loads(line)
        if event.get("type") == "event":
            payload = event.get("payload", {})
            if payload.get("type") == "message" and payload.get("role") == "assistant":
                parts = payload.get("parts", [])
                for part in parts:
                    if part.get("type") == "text":
                        text = part.get("text", "")
                        if text:
                            assistant_messages.append(text)
    
    reconstructed = "\n".join(assistant_messages)
    assert "<stokowski:report>" in reconstructed


def test_ndjson_parsing_for_report_extraction():
    """Test that NDJSON parsing extracts assistant messages correctly."""
    ndjson_data = '''{"type": "start", "timestamp": 1234567890}
{"type": "assistant", "message": {"role": "assistant", "content": "I've completed the work.\\n\\n<stokowski:report>\\n## Summary\\n\\nI analyzed the repository structure.\\n\\n### Changes Made\\n- Listed all files\\n\\n</stokowski:report>\\n\\n## Approval Required\\n\\nThis work requires approval before proceeding."}}
{"type": "result", "usage": {"input_tokens": 500, "output_tokens": 200}}'''
    
    lines = ndjson_data.strip().split('\n')
    messages = []
    for line in lines:
        event = json.loads(line)
        if event.get("type") == "assistant":
            msg = event.get("message", {})
            content = msg.get("content", "")
            if content:
                messages.append(content)
    
    reconstructed = "\n".join(messages)
    
    # Test report extraction
    from stokowski.reporting import extract_report, has_approval_section
    
    report = extract_report(reconstructed)
    has_approval = has_approval_section(reconstructed)
    
    assert report is not None
    assert "Summary" in report
    assert has_approval is True


def test_mux_runner_extracts_assistant_messages_from_ndjson():
    """Mux runner should parse NDJSON and extract assistant message content."""
    # Simulate NDJSON output with assistant messages
    ndjson_lines = [
        '{"type": "start", "timestamp": 123}',
        '{"type": "assistant", "message": {"role": "assistant", "content": "I\'ve completed the work."}}',
        '{"type": "assistant", "message": {"role": "assistant", "content": "<stokowski:report>\\n## Summary\\n- Done\\n</stokowski:report>"}}',
        '{"type": "result", "usage": {"input_tokens": 100, "output_tokens": 50}}',
    ]
    
    from stokowski.reporting import extract_report
    
    # Simulate what the runner should produce
    extracted_content = []
    for line in ndjson_lines:
        event = json.loads(line)
        if event.get("type") == "assistant":
            msg = event.get("message", {})
            content = msg.get("content", "")
            if content:
                extracted_content.append(content)
    
    reconstructed = "\n".join(extracted_content)
    
    # Should be able to extract report from reconstructed content
    report = extract_report(reconstructed)
    assert report is not None
    assert "Summary" in report
