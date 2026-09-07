"""Unit tests for CLI provider backends (agent/services/video_reviewer.py)
and the provider-switch API (agent/api/providers.py).

Heavy mocking of asyncio.create_subprocess_exec — no real subprocesses,
no real filesystem writes to agent/providers.json.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from agent.services.video_reviewer import (
    _run_claude_cli,
    _run_agy_cli,
    _run_codex_cli,
    _analyze_cli,
    _build_prompt,
)
from agent.api import providers as providers_api


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_proc(stdout=b"", stderr=b"", returncode=0):
    """Build a MagicMock standing in for an asyncio subprocess handle."""
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# _run_claude_cli
# ---------------------------------------------------------------------------

class TestRunClaudeCli:
    @pytest.mark.asyncio
    async def test_argv_and_return_value(self):
        proc = make_proc(stdout=b'{"ok": true}', stderr=b"", returncode=0)
        with patch(
            "agent.services.video_reviewer.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ) as mock_exec:
            result = await _run_claude_cli("hello")

        args, kwargs = mock_exec.call_args
        assert args == ("claude", "-p", "hello", "--allowedTools", "Read", "--output-format", "text")
        assert result == '{"ok": true}'


# ---------------------------------------------------------------------------
# _run_agy_cli
# ---------------------------------------------------------------------------

class TestRunAgyCli:
    @pytest.mark.asyncio
    async def test_argv_and_return_value(self):
        proc = make_proc(stdout=b'{"ok": true}', stderr=b"", returncode=0)
        with patch(
            "agent.services.video_reviewer.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ) as mock_exec:
            result = await _run_agy_cli("hello")

        args, kwargs = mock_exec.call_args
        assert args == ("agy", "-p", "hello", "--dangerously-skip-permissions", "--output-format", "text")
        assert result == '{"ok": true}'


# ---------------------------------------------------------------------------
# _run_codex_cli
# ---------------------------------------------------------------------------

class TestRunCodexCli:
    @pytest.mark.asyncio
    async def test_argv_structure_and_output_file_roundtrip(self):
        contact_sheets = [Path("/tmp/sheet_00.jpg"), Path("/tmp/sheet_01.jpg")]
        captured = {}

        async def fake_create_subprocess_exec(*args, **kwargs):
            captured["args"] = args
            # Locate the -o path and write our known content to it, simulating
            # what the real codex CLI would do before exiting.
            o_index = args.index("-o")
            out_path = Path(args[o_index + 1])
            out_path.write_text("codex analysis result")
            return make_proc(stdout=b"", stderr=b"", returncode=0)

        with patch(
            "agent.services.video_reviewer.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=fake_create_subprocess_exec),
        ):
            result = await _run_codex_cli("analyze this", contact_sheets)

        args = captured["args"]
        o_index = args.index("-o")
        out_path = Path(args[o_index + 1])
        assert args == (
            "codex", "exec",
            "-i", str(contact_sheets[0]),
            "-i", str(contact_sheets[1]),
            "-o", str(out_path),
            "--dangerously-bypass-approvals-and-sandbox",
            "analyze this",
        )

        assert result == "codex analysis result"
        # finally: out_path.unlink(missing_ok=True) must have run
        assert not out_path.exists()


# ---------------------------------------------------------------------------
# Timeout path (shared by all three runners via _communicate_with_timeout)
# ---------------------------------------------------------------------------

class TestCliTimeout:
    @pytest.mark.asyncio
    async def test_claude_cli_timeout_kills_proc_and_raises(self):
        proc = make_proc(stdout=b"", stderr=b"", returncode=0)

        async def fake_wait_for(coro, timeout):
            # Close the real coroutine passed in (e.g. proc.communicate()) so
            # it doesn't leak a "coroutine was never awaited" warning.
            coro.close()
            raise asyncio.TimeoutError

        with patch(
            "agent.services.video_reviewer.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ), patch(
            "agent.services.video_reviewer.asyncio.wait_for",
            new=AsyncMock(side_effect=fake_wait_for),
        ):
            with pytest.raises(RuntimeError) as excinfo:
                await _run_claude_cli("hello")

        proc.kill.assert_called_once()
        assert "claude" in str(excinfo.value)
        assert "timed out" in str(excinfo.value)


# ---------------------------------------------------------------------------
# _analyze_cli prompt branching per provider
# ---------------------------------------------------------------------------

class TestAnalyzeCliPromptBranching:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "provider,runner_name", [("claude", "_run_claude_cli"), ("agy", "_run_agy_cli")]
    )
    async def test_claude_agy_providers_include_read_the_images_at(self, provider, runner_name):
        captured = {}

        async def fake_run(full_prompt):
            captured["prompt"] = full_prompt
            return '{"dimensions": {}, "errors": [], "usable_segments": []}'

        contact_sheets = [Path("/tmp/sheet_00.jpg"), Path("/tmp/sheet_01.jpg")]
        with patch("agent.services.video_reviewer.config.CLI_PROVIDERS", {"active": provider}), \
             patch("agent.services.video_reviewer._build_prompt", return_value="BASE_PROMPT"), \
             patch(f"agent.services.video_reviewer.{runner_name}", new=AsyncMock(side_effect=fake_run)):
            result = await _analyze_cli(contact_sheets, 10, 4.0, {})

        assert "Read the images at:" in captured["prompt"]
        assert str(contact_sheets[0]) in captured["prompt"]
        assert str(contact_sheets[1]) in captured["prompt"]
        assert "BASE_PROMPT" in captured["prompt"]
        assert result == {"dimensions": {}, "errors": [], "usable_segments": []}

    @pytest.mark.asyncio
    async def test_codex_provider_excludes_read_the_image_at(self):
        captured = {}

        async def fake_run_codex(full_prompt, contact_sheets):
            captured["prompt"] = full_prompt
            return '{"dimensions": {}, "errors": [], "usable_segments": []}'

        contact_sheets = [Path("/tmp/sheet_00.jpg"), Path("/tmp/sheet_01.jpg")]
        with patch("agent.services.video_reviewer.config.CLI_PROVIDERS", {"active": "codex"}), \
             patch("agent.services.video_reviewer._build_prompt", return_value="BASE_PROMPT"), \
             patch("agent.services.video_reviewer._run_codex_cli", new=AsyncMock(side_effect=fake_run_codex)):
            result = await _analyze_cli(contact_sheets, 10, 4.0, {})

        assert "Read the image at" not in captured["prompt"]
        assert "sequential contact sheets" in captured["prompt"]
        assert "BASE_PROMPT" in captured["prompt"]
        assert result == {"dimensions": {}, "errors": [], "usable_segments": []}

    @pytest.mark.asyncio
    async def test_single_sheet_wrapper_wording_matches_original_singular_form(self):
        """With exactly 1 sheet, the wrapper sentence must read like the original
        pre-multi-sheet prompt ('Read the image at <p>. It is a contact sheet of...') —
        not the plural 'Read the images at: <p>, in that order. These are 1 sequential
        contact sheets...' which is both grammatically wrong and misleading.
        """
        captured = {}

        async def fake_run_claude(full_prompt):
            captured["prompt"] = full_prompt
            return '{"dimensions": {}, "errors": [], "usable_segments": []}'

        contact_sheets = [Path("/tmp/sheet_00.jpg")]
        with patch("agent.services.video_reviewer.config.CLI_PROVIDERS", {"active": "claude"}), \
             patch("agent.services.video_reviewer._build_prompt", return_value="BASE_PROMPT"), \
             patch("agent.services.video_reviewer._run_claude_cli", new=AsyncMock(side_effect=fake_run_claude)):
            await _analyze_cli(contact_sheets, 9, 4.0, {})

        assert captured["prompt"].startswith("Read the image at /tmp/sheet_00.jpg.")
        assert "Read the images at:" not in captured["prompt"]
        assert "sequential contact sheets" not in captured["prompt"]
        assert "It is a contact sheet of 9 video frames at 4.0fps with timestamps." in captured["prompt"]


# ---------------------------------------------------------------------------
# _build_prompt :: sheet_note behavior (single-sheet vs multi-sheet)
# ---------------------------------------------------------------------------

class TestBuildPromptSheetNote:
    def test_single_sheet_has_no_sheet_note(self):
        result = _build_prompt(9, 4.0, 1, {"prompt": "", "video_prompt": ""})
        assert "sequential contact sheets" not in result

    def test_multi_sheet_includes_sheet_note(self):
        result = _build_prompt(32, 4.0, 4, {"prompt": "", "video_prompt": ""})
        assert "4 sequential contact sheets" in result
        assert "sheet 1 is earliest" in result
        assert "sheet 4 is latest" in result


# ---------------------------------------------------------------------------
# agent/api/providers.py :: patch_providers
# ---------------------------------------------------------------------------

class TestPatchProviders:
    @pytest.mark.asyncio
    async def test_unknown_provider_raises_400(self):
        with pytest.raises(HTTPException) as excinfo:
            await providers_api.patch_providers({"active": "not-a-real-provider"})
        assert excinfo.value.status_code == 400

    @pytest.mark.asyncio
    async def test_known_provider_missing_binary_raises_400(self, monkeypatch):
        monkeypatch.setattr(providers_api.shutil, "which", lambda binary: None)
        with pytest.raises(HTTPException) as excinfo:
            await providers_api.patch_providers({"active": "claude"})
        assert excinfo.value.status_code == 400

    @pytest.mark.asyncio
    async def test_known_provider_with_binary_updates_file_and_config(self, monkeypatch, tmp_path):
        tmp_file = tmp_path / "providers.json"
        tmp_file.write_text(json.dumps({"active": "claude"}))

        monkeypatch.setattr(providers_api, "_PROVIDERS_FILE", tmp_file)
        monkeypatch.setattr(providers_api.shutil, "which", lambda binary: "/usr/local/bin/fake")

        # Ensure config.CLI_PROVIDERS mutation doesn't leak to other tests.
        original_cli_providers = dict(providers_api.config.CLI_PROVIDERS)
        monkeypatch.setattr(
            providers_api.config, "CLI_PROVIDERS", dict(original_cli_providers), raising=False
        )

        result = await providers_api.patch_providers({"active": "codex"})

        assert result == {"status": "updated", "active": "codex"}
        assert json.loads(tmp_file.read_text())["active"] == "codex"
        assert providers_api.config.CLI_PROVIDERS["active"] == "codex"
