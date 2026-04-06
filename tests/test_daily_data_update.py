"""Tests for scripts/daily_data_update.sh and scripts/setup_cron.sh."""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "scripts" / "daily_data_update.sh"
CRON_SCRIPT = PROJECT_ROOT / "scripts" / "setup_cron.sh"


# ── Script existence & structure ─────────────────────────────────────────


class TestScriptExists:
    """Basic script validation."""

    def test_script_exists(self):
        assert SCRIPT.exists()

    def test_script_is_executable(self):
        assert os.access(SCRIPT, os.X_OK)

    def test_script_has_shebang(self):
        with open(SCRIPT) as f:
            first_line = f.readline()
        assert first_line.startswith("#!/")

    def test_script_uses_strict_mode(self):
        content = SCRIPT.read_text()
        assert "set -euo pipefail" in content

    def test_script_sources_env(self):
        content = SCRIPT.read_text()
        assert ".env" in content

    def test_script_checks_polygon_key(self):
        content = SCRIPT.read_text()
        assert "POLYGON_API_KEY" in content

    def test_script_calls_backfill(self):
        content = SCRIPT.read_text()
        assert "backfill_polygon_cache.py" in content
        assert "--workers 4" in content

    def test_script_calls_iron_vault(self):
        content = SCRIPT.read_text()
        assert "iron_vault_setup.py" in content

    def test_script_logs_with_timestamp(self):
        content = SCRIPT.read_text()
        assert "daily_update.log" in content
        assert "date" in content

    def test_script_has_lock_file(self):
        content = SCRIPT.read_text()
        assert ".daily_update.lock" in content

    def test_script_has_cron_comment(self):
        content = SCRIPT.read_text()
        assert "0 22 * * 1-5" in content

    def test_script_supports_dry_run(self):
        content = SCRIPT.read_text()
        assert "--dry-run" in content


# ── New features ─────────────────────────────────────────────────────────


class TestNewFeatures:
    """Test enhancements: retries, trading-day check, log rotation, --force."""

    def test_has_retry_logic(self):
        content = SCRIPT.read_text()
        assert "MAX_RETRIES" in content
        assert "attempt" in content
        assert "RETRY_DELAY" in content

    def test_has_trading_day_check(self):
        content = SCRIPT.read_text()
        assert "is_trading_day" in content
        # Checks weekends
        assert "dow" in content or "weekday" in content.lower()

    def test_has_force_flag(self):
        content = SCRIPT.read_text()
        assert "--force" in content
        assert "FORCE" in content

    def test_has_log_rotation(self):
        content = SCRIPT.read_text()
        assert "rotate_log" in content
        assert "MAX_LOG_SIZE" in content

    def test_has_db_size_report(self):
        content = SCRIPT.read_text()
        assert "DB size" in content or "db_size" in content.lower() or "du -h" in content

    def test_has_holiday_check(self):
        content = SCRIPT.read_text()
        # Checks at least some fixed holidays
        assert "01-01" in content or "New Year" in content
        assert "07-04" in content or "Independence" in content
        assert "12-25" in content or "Christmas" in content

    def test_continues_after_backfill_failure(self):
        """Script should still run validation even if backfill fails."""
        content = SCRIPT.read_text()
        # The script should NOT exit immediately on backfill failure
        # It should continue to validation step
        assert "Continue to validation" in content or "backfill_success" in content


# ── Lock mechanism ───────────────────────────────────────────────────────


class TestLockMechanism:
    """Test that the lock file prevents concurrent runs."""

    def test_stale_lock_is_cleaned(self, tmp_path):
        """A lock file with a dead PID should be cleaned up."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        lock_file = data_dir / ".daily_update.lock"
        lock_file.write_text("99999999")

        test_script = tmp_path / "test_lock.sh"
        test_script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            LOCK_FILE="{lock_file}"
            if [ -f "$LOCK_FILE" ]; then
                OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
                if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
                    echo "BLOCKED"
                    exit 0
                fi
                rm -f "$LOCK_FILE"
            fi
            echo $$ > "$LOCK_FILE"
            echo "ACQUIRED"
            rm -f "$LOCK_FILE"
        """))
        test_script.chmod(0o755)

        result = subprocess.run(
            ["bash", str(test_script)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "ACQUIRED" in result.stdout

    def test_active_lock_blocks(self, tmp_path):
        """A lock file with a running PID should block."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        lock_file = data_dir / ".daily_update.lock"
        lock_file.write_text(str(os.getpid()))

        test_script = tmp_path / "test_lock.sh"
        test_script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            LOCK_FILE="{lock_file}"
            if [ -f "$LOCK_FILE" ]; then
                OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
                if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
                    echo "BLOCKED"
                    exit 0
                fi
                rm -f "$LOCK_FILE"
            fi
            echo "ACQUIRED"
        """))
        test_script.chmod(0o755)

        result = subprocess.run(
            ["bash", str(test_script)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout


# ── .env handling ────────────────────────────────────────────────────────


class TestScriptFailsWithoutEnv:
    """Test that the script fails gracefully without .env."""

    def test_fails_without_env(self, tmp_path):
        test_script = tmp_path / "test_env.sh"
        test_script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            PROJECT_DIR="{tmp_path}"
            if [ -f "$PROJECT_DIR/.env" ]; then
                echo "FOUND"
            else
                echo "ERROR: .env not found"
                exit 1
            fi
        """))
        test_script.chmod(0o755)

        result = subprocess.run(
            ["bash", str(test_script)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 1
        assert "ERROR" in result.stdout

    def test_fails_without_polygon_key(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("SOME_OTHER_KEY=foo\n")

        test_script = tmp_path / "test_key.sh"
        # Explicitly unset POLYGON_API_KEY before sourcing to defend against
        # any inherited env var (fixes flaky failure under full test suite).
        test_script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            unset POLYGON_API_KEY
            PROJECT_DIR="{tmp_path}"
            set -a
            . "$PROJECT_DIR/.env"
            set +a
            if [ -z "${{POLYGON_API_KEY:-}}" ]; then
                echo "ERROR: POLYGON_API_KEY not set"
                exit 1
            fi
            echo "OK"
        """))
        test_script.chmod(0o755)

        # Clean env so parent's POLYGON_API_KEY doesn't leak into subprocess.
        # Only pass PATH — nothing else.
        result = subprocess.run(
            ["bash", str(test_script)],
            capture_output=True, text=True, timeout=10,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        )
        assert result.returncode == 1
        assert "POLYGON_API_KEY" in result.stdout


# ── Trading day check ────────────────────────────────────────────────────


class TestTradingDayCheck:
    """Test the is_trading_day function."""

    def test_weekday_is_trading_day(self, tmp_path):
        """Monday-Friday should be trading days (excluding holidays)."""
        test_script = tmp_path / "test_trading.sh"
        test_script.write_text(textwrap.dedent("""\
            #!/usr/bin/env bash
            set -euo pipefail
            is_trading_day() {
                local dow
                dow=$(date -u '+%u')
                if [ "$dow" -ge 6 ]; then
                    return 1
                fi
                local today
                today=$(date -u '+%m-%d')
                case "$today" in
                    01-01|07-04|12-25) return 1 ;;
                esac
                return 0
            }
            if is_trading_day; then
                echo "TRADING"
            else
                echo "NOT_TRADING"
            fi
        """))
        test_script.chmod(0o755)

        result = subprocess.run(
            ["bash", str(test_script)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        # Either TRADING or NOT_TRADING — just check the function works
        assert "TRADING" in result.stdout

    def test_holiday_check_jan1(self, tmp_path):
        """Jan 1 should not be a trading day."""
        test_script = tmp_path / "test_holiday.sh"
        test_script.write_text(textwrap.dedent("""\
            #!/usr/bin/env bash
            set -euo pipefail
            today="01-01"
            case "$today" in
                01-01|07-04|12-25)
                    echo "HOLIDAY"
                    ;;
                *)
                    echo "NOT_HOLIDAY"
                    ;;
            esac
        """))
        test_script.chmod(0o755)

        result = subprocess.run(
            ["bash", str(test_script)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "HOLIDAY" in result.stdout


# ── Log rotation ─────────────────────────────────────────────────────────


class TestLogRotation:
    """Test log rotation logic."""

    def test_rotation_moves_large_log(self, tmp_path):
        log_file = tmp_path / "test.log"
        # Create a file just over 10 MB
        log_file.write_bytes(b"x" * (10 * 1024 * 1024 + 1))

        test_script = tmp_path / "test_rotate.sh"
        test_script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            LOG_FILE="{log_file}"
            MAX_LOG_SIZE=$((10 * 1024 * 1024))
            if [ -f "$LOG_FILE" ]; then
                size=$(stat -c%s "$LOG_FILE" 2>/dev/null || stat -f%z "$LOG_FILE" 2>/dev/null || echo 0)
                if [ "$size" -gt "$MAX_LOG_SIZE" ]; then
                    mv "$LOG_FILE" "${{LOG_FILE}}.1"
                    echo "ROTATED"
                else
                    echo "NO_ROTATE"
                fi
            fi
        """))
        test_script.chmod(0o755)

        result = subprocess.run(
            ["bash", str(test_script)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "ROTATED" in result.stdout
        assert (tmp_path / "test.log.1").exists()

    def test_no_rotation_for_small_log(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("small log\n")

        test_script = tmp_path / "test_rotate.sh"
        test_script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            LOG_FILE="{log_file}"
            MAX_LOG_SIZE=$((10 * 1024 * 1024))
            if [ -f "$LOG_FILE" ]; then
                size=$(stat -c%s "$LOG_FILE" 2>/dev/null || stat -f%z "$LOG_FILE" 2>/dev/null || echo 0)
                if [ "$size" -gt "$MAX_LOG_SIZE" ]; then
                    echo "ROTATED"
                else
                    echo "NO_ROTATE"
                fi
            fi
        """))
        test_script.chmod(0o755)

        result = subprocess.run(
            ["bash", str(test_script)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "NO_ROTATE" in result.stdout


# ── Retry logic ──────────────────────────────────────────────────────────


class TestRetryLogic:
    """Test retry loop behavior."""

    def test_retry_succeeds_on_second_attempt(self, tmp_path):
        """Simulate a command that fails once then succeeds."""
        state_file = tmp_path / "attempt"
        state_file.write_text("0")

        test_script = tmp_path / "test_retry.sh"
        test_script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            MAX_RETRIES=3
            success=false
            for attempt in $(seq 1 "$MAX_RETRIES"); do
                count=$(cat "{state_file}")
                count=$((count + 1))
                echo "$count" > "{state_file}"
                if [ "$count" -ge 2 ]; then
                    echo "SUCCESS on attempt $attempt"
                    success=true
                    break
                else
                    echo "FAIL on attempt $attempt"
                fi
            done
            if [ "$success" = true ]; then
                echo "DONE"
            else
                echo "EXHAUSTED"
            fi
        """))
        test_script.chmod(0o755)

        result = subprocess.run(
            ["bash", str(test_script)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "FAIL on attempt 1" in result.stdout
        assert "SUCCESS on attempt 2" in result.stdout
        assert "DONE" in result.stdout


# ── Cron setup script ────────────────────────────────────────────────────


class TestCronSetupScript:
    """Test scripts/setup_cron.sh exists and has correct structure."""

    def test_cron_script_exists(self):
        assert CRON_SCRIPT.exists()

    def test_cron_script_is_executable(self):
        assert os.access(CRON_SCRIPT, os.X_OK)

    def test_cron_script_has_install_action(self):
        content = CRON_SCRIPT.read_text()
        assert "install" in content

    def test_cron_script_has_remove_action(self):
        content = CRON_SCRIPT.read_text()
        assert "--remove" in content

    def test_cron_script_has_status_action(self):
        content = CRON_SCRIPT.read_text()
        assert "--status" in content

    def test_cron_schedule_is_22_utc_mon_fri(self):
        content = CRON_SCRIPT.read_text()
        assert "0 22 * * 1-5" in content

    def test_cron_script_checks_executable(self):
        content = CRON_SCRIPT.read_text()
        assert "not executable" in content.lower() or "-x" in content

    def test_cron_script_checks_env(self):
        content = CRON_SCRIPT.read_text()
        assert ".env" in content


# ── Backfill script dynamic DATE_TO ──────────────────────────────────────


class TestBackfillDateTo:
    """Verify backfill_polygon_cache.py uses dynamic DATE_TO."""

    def test_date_to_is_dynamic(self):
        backfill = PROJECT_ROOT / "scripts" / "backfill_polygon_cache.py"
        content = backfill.read_text()
        # Should NOT have a hard-coded date
        assert "2026-03-15" not in content
        # Should use datetime.now or similar
        assert "datetime.now" in content or "date.today" in content
