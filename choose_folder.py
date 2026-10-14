import sys
import os
import subprocess
from pathlib import Path

def main():
    initial_dir = ""
    if len(sys.argv) > 1 and sys.argv[1]:
        p = Path(sys.argv[1])
        if p.exists():
            initial_dir = str(p).replace("\\", "/")

    folder = ""
    if sys.platform == "win32":
        try:
            norm_init = initial_dir.replace("/", "\\").replace("'", "''")
            set_path_line = f"if (Test-Path '{norm_init}') {{ $f.SelectedPath = '{norm_init}' }}; " if norm_init else ""
            ps_script = (
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
                "$dummy = New-Object System.Windows.Forms.Form; "
                "$dummy.TopMost = $true; "
                "$dummy.TopLevel = $true; "
                "$dummy.Show(); "
                "$dummy.BringToFront(); "
                "$dummy.Visible = $false; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$f.Description = '请选择成片保存目录'; "
                "$f.ShowNewFolderButton = $true; "
                f"{set_path_line}"
                "if ($f.ShowDialog($dummy) -eq [System.Windows.Forms.DialogResult]::OK) { "
                "    [Console]::WriteLine($f.SelectedPath) "
                "} "
                "$dummy.Dispose(); "
            )
            res = subprocess.run(
                ["powershell.exe", "-Sta", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=180
            )
            if res.returncode == 0 and res.stdout.strip():
                lines = [ln.strip() for ln in res.stdout.strip().splitlines() if ln.strip()]
                if lines:
                    folder = lines[-1]
        except Exception:
            pass

    if folder:
        print(str(folder).strip().replace("\\", "/"))

if __name__ == '__main__':
    main()
