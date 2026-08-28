from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = ROOT / "scripts" / "sync_on_startup.ps1"


def extract_invoke_native_logged(source: str) -> str:
    start = source.index("function Invoke-NativeLogged {")
    end = source.index("\nfunction Resolve-LocalPath", start)
    return source[start:end]


def ps_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class NativeCommandLoggingTests(unittest.TestCase):
    def test_wrapper_relaxes_stop_only_while_capturing_native_output(self):
        source = SYNC_SCRIPT.read_text(encoding="utf-8")
        function = extract_invoke_native_logged(source)

        save = "$previousErrorActionPreference = $ErrorActionPreference"
        relax = '$ErrorActionPreference = "Continue"'
        invoke = "$output = @(& $FilePath @Arguments 2>&1)"
        capture = "$exitCode = $LASTEXITCODE"
        restore = "$ErrorActionPreference = $previousErrorActionPreference"

        for marker in (save, relax, invoke, capture, restore):
            self.assertIn(marker, function)
        self.assertLess(function.index(save), function.index(relax))
        self.assertLess(function.index(relax), function.index(invoke))
        self.assertLess(function.index(invoke), function.index(capture))
        self.assertLess(function.index(capture), function.index(restore))

    @unittest.skipUnless(
        os.name == "nt",
        "Windows PowerShell 5.1 behavior is Windows-specific",
    )
    def test_successful_native_stderr_does_not_abort_windows_powershell(self):
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("Windows PowerShell is not installed")

        source = SYNC_SCRIPT.read_text(encoding="utf-8")
        function = extract_invoke_native_logged(source)

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            log_path = temporary_path / "native.log"
            harness_path = temporary_path / "harness.ps1"
            harness = f'''$ErrorActionPreference = "Stop"
$logFile = {ps_single_quote(str(log_path))}
{function}
$exitCode = Invoke-NativeLogged `
    -FilePath {ps_single_quote(sys.executable)} `
    -Arguments @(
        "-c",
        "import sys; sys.stderr.write('From test remote\\n')"
    ) `
    -AllowFailure
if ($exitCode -ne 0) {{ throw "Unexpected exit code: $exitCode" }}
if (-not (Select-String -LiteralPath $logFile -SimpleMatch "From test remote")) {{
    throw "Redirected native stderr was not logged."
}}
'''
            harness_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=(completed.stdout + completed.stderr).strip(),
            )


if __name__ == "__main__":
    unittest.main()