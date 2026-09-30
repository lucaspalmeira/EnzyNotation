"""Local subprocess execution backend."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from enzynotation.backends.base import CommandSpec, ExecutionBackend
from enzynotation.provenance import (
    CommandProvenance,
    SoftwareProvenance,
    normalize_command,
    utc_now,
)


class LocalBackend(ExecutionBackend):
    """Run commands directly on the current host without a shell."""

    name = "local"

    def execute(
        self,
        command: CommandSpec,
        *,
        stdout_path: Path,
        stderr_path: Path,
    ) -> CommandProvenance:
        """Execute one command and write separate stdout/stderr logs."""

        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        cwd = (command.cwd or Path.cwd()).resolve()
        environment = os.environ.copy()
        if command.environment:
            environment.update(command.environment)

        started_at = utc_now()
        started_clock = time.monotonic()
        return_code: int
        with (
            stdout_path.open("w", encoding="utf-8") as stdout_handle,
            stderr_path.open("w", encoding="utf-8") as stderr_handle,
        ):
            try:
                completed = subprocess.run(
                    command.argv,
                    cwd=cwd,
                    env=environment,
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    text=True,
                )
                return_code = completed.returncode
            except FileNotFoundError as exc:
                stderr_handle.write(f"{exc}\n")
                return_code = 127
            except OSError as exc:
                stderr_handle.write(f"{exc}\n")
                return_code = 126

        duration = time.monotonic() - started_clock
        return CommandProvenance(
            argv=command.argv,
            cwd=str(cwd),
            backend=self.name,
            started_at=started_at,
            finished_at=utc_now(),
            duration_seconds=duration,
            return_code=return_code,
            stdout_path=str(stdout_path.resolve()),
            stderr_path=str(stderr_path.resolve()),
        )

    def capture_version(
        self,
        executable: str,
        *,
        name: str | None = None,
        arguments: Sequence[str] = ("--version",),
    ) -> SoftwareProvenance:
        """Run a conventional version command and retain its first output line."""

        command = normalize_command((executable, *arguments))
        resolved = shutil.which(executable) or executable
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = completed.stdout.strip() or completed.stderr.strip()
            version = output.splitlines()[0].strip() if output else "unknown"
            return_code = completed.returncode
        except (OSError, subprocess.SubprocessError):
            version = "unknown"
            return_code = 127

        return SoftwareProvenance(
            name=name or Path(executable).name,
            version=version,
            executable=str(resolved),
            version_command=command,
            version_return_code=return_code,
        )
