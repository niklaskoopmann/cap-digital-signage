import importlib
from pathlib import Path
from types import SimpleNamespace

from calendar_render_service import scheduler


def make_config(tmp_path: Path, run_on_start: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        render_cron="*/15 * * * *",
        run_on_start=run_on_start,
        log_level="INFO",
        calendar_timezone="Europe/Berlin",
    )


def test_build_and_write_crontab(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)

    content = scheduler.build_crontab(cfg)
    path = scheduler.write_crontab(cfg, tmp_path / "calendar.cron")

    expected = f"*/15 * * * * {scheduler.FLOCK_COMMAND} -n /tmp/calendar-render-service.lock {' '.join(scheduler.JOB_COMMAND)}\n"
    assert content == expected
    assert path.read_text(encoding="utf-8") == content


def test_crontab_commands_use_absolute_executables() -> None:
    assert scheduler.FLOCK_COMMAND.startswith("/") or Path(scheduler.FLOCK_COMMAND).is_absolute()
    assert Path(scheduler.JOB_COMMAND[0]).is_absolute()


def test_supercronic_command_is_absolute_when_found_via_relative_path(monkeypatch) -> None:
    original_which = scheduler.shutil.which

    try:
        monkeypatch.setattr(
            scheduler.shutil,
            "which",
            lambda command: "relative/bin/supercronic" if command == "supercronic" else original_which(command),
        )
        reloaded_scheduler = importlib.reload(scheduler)

        assert Path(reloaded_scheduler.SUPERCRONIC_COMMAND).is_absolute()
    finally:
        monkeypatch.undo()
        importlib.reload(scheduler)


def test_run_startup_cycle_runs_once_when_enabled(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(scheduler.subprocess, "run", lambda command, check: calls.append((command, check)) or SimpleNamespace(returncode=0))

    scheduler.run_startup_cycle(make_config(tmp_path))

    assert calls == [(list(scheduler.JOB_COMMAND), False)]


def test_run_startup_cycle_skips_when_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(scheduler.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected run")))

    scheduler.run_startup_cycle(make_config(tmp_path, run_on_start=False))


def test_run_startup_cycle_logs_failure_without_raising(monkeypatch, tmp_path: Path, caplog) -> None:
    monkeypatch.setattr(scheduler.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=7))

    scheduler.run_startup_cycle(make_config(tmp_path))

    assert "return code 7" in caplog.text


def test_run_startup_cycle_logs_launch_failure_without_raising(monkeypatch, tmp_path: Path, caplog) -> None:
    def raise_launch_error(*args, **kwargs):
        raise OSError("supercronic job could not be launched")

    monkeypatch.setattr(scheduler.subprocess, "run", raise_launch_error)

    scheduler.run_startup_cycle(make_config(tmp_path))

    assert "could not be launched" in caplog.text


def test_main_execs_supercronic_with_absolute_argv_zero_even_when_startup_fails(monkeypatch, tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    crontab = tmp_path / "calendar.cron"
    calls = {}
    monkeypatch.setattr(scheduler, "load_config", lambda: cfg)
    monkeypatch.setattr(scheduler, "setup_logging", lambda level: None)
    monkeypatch.setattr(scheduler, "write_crontab", lambda config: crontab)
    monkeypatch.setattr(scheduler.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=7))
    monkeypatch.setattr(scheduler.os, "execv", lambda executable, args: calls.update(executable=executable, args=args))

    scheduler.main()

    assert calls == {
        "executable": scheduler.SUPERCRONIC_COMMAND,
        "args": [scheduler.SUPERCRONIC_COMMAND, "-passthrough-logs", str(crontab)],
    }
    assert Path(calls["args"][0]).is_absolute()