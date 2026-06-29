"""Integration playit.gg : tunnel pour jouer a distance sans ouvrir de port sur la box.

On telecharge l'agent officiel (binaire Windows) et on le lance ; il affiche une URL
a ouvrir pour lier le compte et configurer le tunnel (vers le port local 25565).
"""
import os
import re
import subprocess
import threading

import requests

from . import paths

RELEASES_API = "https://api.github.com/repos/playit-cloud/playit-agent/releases/latest"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
# Repere les URL playit (claim/login/tunnel) dans la sortie de l'agent
URL_RE = re.compile(r"https://playit\.gg/\S+")

_agent_path = os.path.join(paths.DATA_DIR, "playit.exe")


def ensure_agent(log=print):
    """Telecharge l'agent playit s'il n'est pas deja la. Renvoie son chemin."""
    if os.path.exists(_agent_path):
        return _agent_path
    paths.ensure_dirs()
    log("Recherche de l'agent playit.gg...")
    rel = requests.get(RELEASES_API, timeout=30).json()
    asset = None
    for a in rel.get("assets", []):
        name = a["name"].lower()
        if name.endswith(".exe") and "windows" in name and ("x86_64" in name or "amd64" in name):
            asset = a
            break
    if not asset:
        raise RuntimeError("Binaire Windows playit introuvable dans la derniere release.")
    log(f"Telechargement de {asset['name']}...")
    from .downloaders import download
    download(asset["browser_download_url"], _agent_path, log=log)
    return _agent_path


class PlayitAgent:
    """Lance l'agent playit et stream sa sortie. on_url(url) appele a chaque lien playit."""

    def __init__(self, on_output, on_url, on_exit):
        self.on_output = on_output
        self.on_url = on_url
        self.on_exit = on_exit
        self.proc = None

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        self.proc = subprocess.Popen(
            [_agent_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW)
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        seen = set()
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            self.on_output(line)
            for m in URL_RE.findall(line):
                if m not in seen:
                    seen.add(m)
                    self.on_url(m)
        self.on_exit(self.proc.wait())

    def stop(self):
        if self.proc is not None:
            self.proc.terminate()
