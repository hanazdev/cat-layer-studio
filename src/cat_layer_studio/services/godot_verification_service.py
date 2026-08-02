from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class VerificationResult:
    passed: bool
    message: str
    output: str


def verify_godot_export(
    executable: Path,
    godot_project_directory: Path,
    verification_script_res_path: str,
    *,
    timeout: int = 120,
) -> VerificationResult:
    if not executable.is_file():
        raise ValueError("Choose a Godot 4.6 executable.")
    if executable.suffix.lower() in {".bat", ".cmd"}:
        raise ValueError("Choose the Godot .exe itself, not a batch-file shortcut.")
    user_home = godot_project_directory / ".cat_layer_studio_godot_user"
    user_home.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment["GODOT_USER_HOME"] = str(user_home)
    common = [
        str(executable),
        "--headless",
        "--path",
        str(godot_project_directory),
        "--log-file",
        str(user_home / "godot.log"),
    ]

    def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=environment,
        )

    try:
        imported = run([*common, "--editor", "--quit"])
        if imported.returncode != 0:
            output = "\n".join(part for part in (imported.stdout, imported.stderr) if part).strip()
            return VerificationResult(False, "Godot validation failed", output)
        completed = run([*common, "--quit-after", "600", "--script", verification_script_res_path])
    except subprocess.TimeoutExpired as error:
        captured = "\n".join(
            part.decode(errors="replace") if isinstance(part, bytes) else (part or "")
            for part in (error.stdout, error.stderr)
        ).strip()
        return VerificationResult(False, "Godot validation failed", captured or "Godot timed out.")
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    passed = completed.returncode == 0 and "CAT_LAYER_STUDIO_VERIFIED" in output
    return VerificationResult(
        passed,
        "Godot Verified" if passed else "Godot validation failed",
        output,
    )
