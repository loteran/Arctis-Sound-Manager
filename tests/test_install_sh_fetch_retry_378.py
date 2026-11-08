# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""install.sh retries its downloads (#378).

During a GitHub spell of timeouts on unauthenticated downloads, one failed
fetch of the repository key read as "there is no signing key". Every network
call the script makes goes through fetch(), which retries transient failures
and nothing else.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).parent.parent / "docs" / "install.sh"


def _code_lines() -> list[str]:
    """The script's lines minus comments and printed hints."""
    out = []
    for line in INSTALL_SH.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("printf"):
            continue
        out.append(stripped)
    return out


def test_no_bare_curl_outside_fetch():
    lines = _code_lines()
    start = lines.index("fetch() {")
    end = lines.index("}", start)
    outside = lines[:start] + lines[end + 1:]
    assert not [l for l in outside if re.search(r"\bcurl\b", l)]


def test_fetch_retries_a_dropped_connection_and_not_a_404(tmp_path):
    fn = re.search(r"^fetch\(\) \{.*?^\}", INSTALL_SH.read_text(), re.S | re.M).group(0)
    fake = tmp_path / "curl"
    # Fails with "connection died" twice, then succeeds; a 404 URL fails with 22.
    fake.write_text(
        "#!/bin/bash\n"
        f"n=$(cat {tmp_path}/n 2>/dev/null || echo 0); echo $((n+1)) > {tmp_path}/n\n"
        'case "$*" in *missing*) exit 22 ;; esac\n'
        "[ $n -lt 2 ] && exit 56\n"
        "exit 0\n")
    fake.chmod(0o755)
    script = (f"set -euo pipefail\nsleep() {{ :; }}\n{fn}\n"
              "fetch https://example/key && echo ok\n"
              f"echo 0 > {tmp_path}/n\n"
              "fetch https://example/missing || echo \"rc=$?\"\n"
              f"cat {tmp_path}/n\n")
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         env={"PATH": f"{tmp_path}:/usr/bin:/bin"}).stdout.split()
    assert out == ["ok", "rc=22", "1"]
