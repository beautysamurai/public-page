from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PowerShellSyntaxTests(unittest.TestCase):
    def test_all_powershell_scripts_parse(self):
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if executable is None:
            self.skipTest("PowerShell is not installed in this environment")

        command = r'''$failed = $false
Get-ChildItem -Path scripts -Filter *.ps1 -File | ForEach-Object {
    $scriptPath = $_.FullName
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $scriptPath,
        [ref]$tokens,
        [ref]$parseErrors
    ) | Out-Null
    foreach ($parseError in $parseErrors) {
        $failed = $true
        $lineNumber = $parseError.Extent.StartLineNumber
        $message = $parseError.Message
        Write-Output ("{0}:{1}: {2}" -f $scriptPath, $lineNumber, $message)
    }
}
if ($failed) { exit 1 }
'''
        completed = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", command],
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
