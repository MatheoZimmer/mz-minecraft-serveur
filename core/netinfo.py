"""Infos reseau : IP locale (LAN) et IP publique (jeu a distance)."""
import socket

import requests


def lan_ip():
    """IP locale du PC sur le reseau (a donner aux potes en LAN / meme wifi)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # pas de vrai trafic, juste pour trouver l'interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def public_ip():
    """IP publique (a donner aux potes hors reseau, avec le port ouvert sur la box)."""
    try:
        return requests.get("https://api.ipify.org", timeout=10).text.strip()
    except requests.RequestException:
        return "indisponible"
