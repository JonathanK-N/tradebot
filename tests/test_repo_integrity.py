"""Garde-fou : tout le code source doit être suivi par git.

Un fichier présent sur le disque mais ignoré par git passe tous les tests locaux,
puis manque en production (incident réel : « data/ » dans .gitignore excluait
src/tradebot/data/, et le moteur plantait sur Railway).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="pas de dépôt git")
def test_all_source_files_are_tracked_or_addable():
    sources = [p for p in (ROOT / "src").rglob("*") if p.is_file() and p.suffix in {".py", ".html", ".js",
                                                                                     ".svg", ".webmanifest"}]
    assert sources
    rel = [p.relative_to(ROOT).as_posix() for p in sources]
    # `git check-ignore` renvoie les chemins que .gitignore exclurait
    r = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"], cwd=ROOT, input="\n".join(rel),
                       capture_output=True, text=True)
    ignored = [line for line in r.stdout.splitlines() if line.strip()]
    assert not ignored, f"fichiers source ignorés par .gitignore (absents en production) : {ignored}"
