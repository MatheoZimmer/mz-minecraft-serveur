"""MZ Minecraft Server Launcher.

Interface a onglets pour auto-heberger un serveur Minecraft
(vanilla / paper / fabric / forge / neoforge) :
- Serveur     : loader/version, RAM (+garde-fou), port, install, demarrage, reseau
                (copier IP LAN/distant), tunnel playit.gg, auto-restart
- Configuration : seed, mode de jeu, difficulte, hardcore, type de monde, distance de rendu...
- Mods/Plugins : ajouter ses .jar OU chercher/installer depuis Modrinth
- Admin        : OP, kick, ban, whitelist, time, weather, backup du monde... pendant la partie

Console toujours visible en bas + compteur de joueurs et indicateur d'etat dans le header.
Profil sauvegarde (config_store) et rapport de crash auto (crash_reporter) inclus.

Lancer depuis les sources : python launcher.py   |   Compiler : build.bat
"""
import collections
import os
import re
import shutil
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from core import (config_store, crash_reporter, downloaders, java_manager,
                  mailer, modrinth, netinfo, paths, playit, sysinfo)
from core.java_manager import required_java
from core.server_process import (ServerProcess, accept_eula, read_properties,
                                 update_properties)

APP_VERSION = "0.3.2"
LOADERS = ["vanilla", "paper", "fabric", "forge", "neoforge"]
DEFAULT_PORT = 25565
DEFAULT_RAM = sysinfo.recommended_ram_mb()  # ~60% de la RAM, plafonne

# Detection des connexions/deconnexions dans la console serveur
JOIN_RE = re.compile(r"]: (\w+) joined the game")
LEFT_RE = re.compile(r"]: (\w+) left the game")

GAMEMODES = ["survival", "creative", "adventure", "spectator"]
DIFFICULTIES = ["peaceful", "easy", "normal", "hard"]
# Libelle affiche -> valeur reelle dans server.properties (format moderne 1.19+)
LEVEL_TYPES = {
    "Normal": "minecraft:normal",
    "Plat (superflat)": "minecraft:flat",
    "Grands biomes": "minecraft:large_biomes",
    "Amplifie": "minecraft:amplified",
}

# Couleurs du theme
BG = "#1e1f26"
PANEL = "#272935"
FG = "#e6e6e6"
ACCENT = "#3a7d2c"
DANGER = "#a83232"
CONSOLE_BG = "#0d0d0d"
CONSOLE_FG = "#cfcfcf"


class App:
    def __init__(self, root):
        self.root = root
        root.title("MZ Minecraft Server Launcher")
        root.geometry("980x720")
        root.minsize(880, 620)
        root.configure(bg=BG)

        paths.ensure_dirs()

        self.server = None
        self.java_exe = None
        self.current_dir = None
        self.prepared = False
        self._lan_ip = "..."
        self._public_ip = "..."
        self._user_stopped = False     # True si arret demande (evite l'auto-restart)
        self._players = set()          # joueurs connectes (compteur)
        self.playit_agent = None
        self._playit_address = ""
        self._console_tail = collections.deque(maxlen=60)  # pour le rapport de crash

        self._setup_style()
        self._build_ui()
        self._load_config()            # restaure le profil sauvegarde
        self._refresh_versions()
        self._refresh_existing()
        threading.Thread(target=self._load_network_info, daemon=True).start()

    # ---------- Theme ----------
    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        f = ("Segoe UI", 10)
        field = "#333645"   # fond des champs (entries, combobox)
        btn = "#3a3d4a"      # fond des boutons normaux
        style.configure(".", background=BG, foreground=FG, font=f)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG, font=f)
        style.configure("Panel.TLabel", background=PANEL, foreground=FG, font=f)
        style.configure("Title.TLabel", background=BG, foreground="#7fd35a",
                        font=("Segoe UI Semibold", 16))
        style.configure("Hint.TLabel", background=PANEL, foreground="#9aa0ad",
                        font=("Segoe UI", 9))

        # Boutons : couleurs explicites (sinon clam met un gris clair illisible)
        style.configure("TButton", padding=7, font=f, background=btn, foreground=FG,
                        borderwidth=0, focuscolor=btn)
        style.map("TButton",
                  background=[("active", "#4a4e5e"), ("disabled", "#2a2c34")],
                  foreground=[("disabled", "#6a6d77")])
        style.configure("Accent.TButton", foreground="white", background=ACCENT)
        style.map("Accent.TButton",
                  background=[("active", "#2f6a23"), ("disabled", "#2a2c34")],
                  foreground=[("disabled", "#6a6d77")])
        style.configure("Danger.TButton", foreground="white", background=DANGER)
        style.map("Danger.TButton",
                  background=[("active", "#8a2828"), ("disabled", "#2a2c34")],
                  foreground=[("disabled", "#6a6d77")])

        style.configure("TCheckbutton", background=PANEL, foreground=FG, font=f)
        style.map("TCheckbutton", background=[("active", PANEL)],
                  indicatorcolor=[("selected", ACCENT), ("!selected", field)])

        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=FG,
                        padding=(16, 8), font=("Segoe UI", 10))
        style.map("TNotebook.Tab", background=[("selected", ACCENT)],
                  foreground=[("selected", "white")])

        style.configure("TLabelframe", background=PANEL, bordercolor="#3a3d4a")
        style.configure("TLabelframe.Label", background=PANEL, foreground="#9aa0ad")

        # Champs : fond sombre + texte clair, y compris en etat readonly/selected
        # (c'est l'etat readonly qui causait l'overlay blanc sur les combobox).
        style.configure("TCombobox", fieldbackground=field, background=field, foreground=FG,
                        arrowcolor=FG, selectbackground=field, selectforeground=FG,
                        borderwidth=0)
        style.map("TCombobox",
                  fieldbackground=[("readonly", field), ("disabled", "#2a2c34")],
                  foreground=[("readonly", FG), ("disabled", "#6a6d77")],
                  selectbackground=[("readonly", field)],
                  selectforeground=[("readonly", FG)],
                  background=[("readonly", field), ("active", field)],
                  arrowcolor=[("readonly", FG)])
        style.configure("TEntry", fieldbackground=field, foreground=FG,
                        insertcolor=FG, borderwidth=0)
        style.configure("TSpinbox", fieldbackground=field, foreground=FG,
                        background=field, arrowcolor=FG, borderwidth=0)
        style.map("TSpinbox", fieldbackground=[("readonly", field)])

        # Liste deroulante des combobox (popup natif, pas controle par le theme ttk)
        self.root.option_add("*TCombobox*Listbox.background", field)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "white")

    # ---------- Construction UI ----------
    def _build_ui(self):
        header = ttk.Frame(self.root, padding=(14, 10))
        header.pack(fill="x")
        ttk.Label(header, text="MZ Minecraft Server", style="Title.TLabel").pack(side="left")
        self.head_status = ttk.Label(header, text="", foreground="#9aa0ad")
        self.head_status.pack(side="right")
        self.players_lbl = ttk.Label(header, text="0 joueur", foreground="#9aa0ad")
        self.players_lbl.pack(side="right", padx=14)
        self.state_dot = ttk.Label(header, text="● arrete", foreground="#c8553d")
        self.state_dot.pack(side="right")

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="x", padx=12)
        self._tab_server()
        self._tab_config()
        self._tab_mods()
        self._tab_admin()

        # Console partagee (toujours visible)
        cons_frame = ttk.Frame(self.root, padding=(12, 6))
        cons_frame.pack(fill="both", expand=True)
        ttk.Label(cons_frame, text="Console du serveur").pack(anchor="w")
        self.console = scrolledtext.ScrolledText(
            cons_frame, height=12, bg=CONSOLE_BG, fg=CONSOLE_FG,
            insertbackground=CONSOLE_FG, font=("Consolas", 9), state="disabled",
            relief="flat")
        self.console.pack(fill="both", expand=True, pady=(2, 6))

        cmd = ttk.Frame(cons_frame)
        cmd.pack(fill="x")
        self.cmd_var = tk.StringVar()
        entry = ttk.Entry(cmd, textvariable=self.cmd_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self._send_command())
        ttk.Button(cmd, text="Envoyer", command=self._send_command).pack(side="left", padx=5)

        self.status_var = tk.StringVar(value="Pret.")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken",
                  anchor="w", padding=5).pack(fill="x", side="bottom")

    def _tab_server(self):
        tab = ttk.Frame(self.nb, padding=14, style="Panel.TFrame")
        self.nb.add(tab, text="  Serveur  ")

        row = ttk.Frame(tab, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="Type :", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.loader_var = tk.StringVar(value="paper")
        cb = ttk.Combobox(row, textvariable=self.loader_var, values=LOADERS,
                          state="readonly", width=12)
        cb.grid(row=0, column=1, padx=5)
        cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_versions())

        ttk.Label(row, text="Version :", style="Panel.TLabel").grid(row=0, column=2, padx=(15, 0))
        self.version_var = tk.StringVar()
        self.version_cb = ttk.Combobox(row, textvariable=self.version_var,
                                       state="readonly", width=18)
        self.version_cb.grid(row=0, column=3, padx=5)

        self.snapshot_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Snapshots (vanilla)", variable=self.snapshot_var,
                        command=self._refresh_versions).grid(row=0, column=4, padx=10)

        ttk.Label(row, text="RAM (Mo) :", style="Panel.TLabel").grid(row=1, column=0, sticky="w",
                                                                     pady=(10, 0))
        self.ram_var = tk.StringVar(value=str(DEFAULT_RAM))
        ttk.Entry(row, textvariable=self.ram_var, width=10).grid(row=1, column=1, pady=(10, 0))
        ttk.Label(row, text="Port :", style="Panel.TLabel").grid(row=1, column=2, padx=(15, 0),
                                                                 pady=(10, 0))
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(row, textvariable=self.port_var, width=10).grid(row=1, column=3, pady=(10, 0))

        total = sysinfo.total_ram_mb()
        ttk.Label(row, style="Hint.TLabel",
                  text=f"RAM PC : {total} Mo  -  max conseille : {sysinfo.max_safe_ram_mb()} Mo"
                  ).grid(row=1, column=4, padx=10, pady=(10, 0), sticky="w")

        opts = ttk.Frame(tab, style="Panel.TFrame")
        opts.pack(fill="x", pady=(12, 0))
        self.eula_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="J'accepte le CLUF Minecraft (obligatoire) - aka.ms/MinecraftEULA",
                        variable=self.eula_var).pack(side="left")
        self.autorestart_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Redemarrage auto si crash",
                        variable=self.autorestart_var).pack(side="left", padx=20)

        actions = ttk.Frame(tab, style="Panel.TFrame")
        actions.pack(fill="x", pady=6)
        self.prepare_btn = ttk.Button(actions, text="1) Installer / Preparer",
                                      command=self._on_prepare)
        self.prepare_btn.pack(side="left")
        self.start_btn = ttk.Button(actions, text="2) Demarrer", style="Accent.TButton",
                                    command=self._on_start, state="disabled")
        self.start_btn.pack(side="left", padx=6)
        self.stop_btn = ttk.Button(actions, text="Arreter", style="Danger.TButton",
                                   command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left")
        ttk.Button(actions, text="Ouvrir le dossier",
                   command=self._open_folder).pack(side="left", padx=6)
        ttk.Button(actions, text="Rapports auto",
                   command=self._config_mail).pack(side="left")

        # Serveurs deja installes : recharger une partie sans tout reparametrer
        existing = ttk.LabelFrame(tab, text="Serveurs installes (relancer une partie)",
                                  padding=10)
        existing.pack(fill="x", pady=(12, 0))
        self.existing_var = tk.StringVar()
        self.existing_cb = ttk.Combobox(existing, textvariable=self.existing_var,
                                        state="readonly", width=34)
        self.existing_cb.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(existing, text="Charger", style="Accent.TButton",
                   command=self._load_existing).grid(row=0, column=1, padx=2)
        ttk.Button(existing, text="Rafraichir",
                   command=self._refresh_existing).grid(row=0, column=2, padx=2)
        ttk.Button(existing, text="Supprimer", style="Danger.TButton",
                   command=self._delete_existing).grid(row=0, column=3, padx=2)

        # Bloc reseau avec boutons Copier
        net = ttk.LabelFrame(tab, text="Acces / Reseau", padding=10)
        net.pack(fill="x", pady=(14, 0))
        ttk.Label(net, text="LAN (meme wifi) :").grid(row=0, column=0, sticky="w")
        self.lan_lbl = ttk.Label(net, text="...", font=("Consolas", 10))
        self.lan_lbl.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(net, text="Copier", width=8,
                   command=lambda: self._copy(self._addr(self._lan_ip))).grid(row=0, column=2)

        ttk.Label(net, text="Distant (internet) :").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.pub_lbl = ttk.Label(net, text="...", font=("Consolas", 10))
        self.pub_lbl.grid(row=1, column=1, sticky="w", padx=8, pady=(6, 0))
        ttk.Button(net, text="Copier", width=8,
                   command=lambda: self._copy(self._addr(self._public_ip))).grid(row=1, column=2,
                                                                                 pady=(6, 0))
        ttk.Button(net, text="Aide acces distant (port forwarding)",
                   command=self._show_remote_info).grid(row=2, column=0, columnspan=3,
                                                        sticky="w", pady=(10, 0))

        # Tunnel playit.gg : jouer a distance sans toucher a la box
        tun = ttk.LabelFrame(tab, text="Tunnel playit.gg (distant sans ouvrir de port)", padding=10)
        tun.pack(fill="x", pady=(12, 0))
        self.playit_btn = ttk.Button(tun, text="Demarrer le tunnel",
                                     command=self._toggle_playit)
        self.playit_btn.pack(side="left")
        self.playit_addr = ttk.Label(tun, text="(arrete)", font=("Consolas", 10))
        self.playit_addr.pack(side="left", padx=10)
        ttk.Button(tun, text="Copier l'adresse", width=14,
                   command=self._copy_playit).pack(side="left")

    def _tab_config(self):
        tab = ttk.Frame(self.nb, padding=14, style="Panel.TFrame")
        self.nb.add(tab, text="  Configuration  ")

        g = ttk.Frame(tab, style="Panel.TFrame")
        g.pack(fill="x")

        def lbl(t, r, c):
            ttk.Label(g, text=t, style="Panel.TLabel").grid(row=r, column=c, sticky="w",
                                                            padx=(0, 6), pady=4)

        lbl("Seed (vide = aleatoire) :", 0, 0)
        self.seed_var = tk.StringVar()
        ttk.Entry(g, textvariable=self.seed_var, width=24).grid(row=0, column=1, sticky="w", pady=4)

        lbl("MOTD :", 0, 2)
        self.motd_var = tk.StringVar(value="Serveur des potes")
        ttk.Entry(g, textvariable=self.motd_var, width=26).grid(row=0, column=3, sticky="w", pady=4)

        lbl("Mode de jeu :", 1, 0)
        self.gamemode_var = tk.StringVar(value="survival")
        ttk.Combobox(g, textvariable=self.gamemode_var, values=GAMEMODES,
                     state="readonly", width=14).grid(row=1, column=1, sticky="w", pady=4)

        lbl("Difficulte :", 1, 2)
        self.difficulty_var = tk.StringVar(value="normal")
        ttk.Combobox(g, textvariable=self.difficulty_var, values=DIFFICULTIES,
                     state="readonly", width=14).grid(row=1, column=3, sticky="w", pady=4)

        lbl("Type de monde :", 2, 0)
        self.leveltype_var = tk.StringVar(value="Normal")
        ttk.Combobox(g, textvariable=self.leveltype_var, values=list(LEVEL_TYPES),
                     state="readonly", width=18).grid(row=2, column=1, sticky="w", pady=4)

        lbl("Joueurs max :", 2, 2)
        self.maxplayers_var = tk.StringVar(value="10")
        ttk.Entry(g, textvariable=self.maxplayers_var, width=8).grid(row=2, column=3, sticky="w",
                                                                     pady=4)

        lbl("Distance de rendu :", 3, 0)
        self.viewdist_var = tk.StringVar(value="10")
        ttk.Spinbox(g, from_=3, to=32, textvariable=self.viewdist_var, width=6).grid(
            row=3, column=1, sticky="w", pady=4)

        lbl("Distance simulation :", 3, 2)
        self.simdist_var = tk.StringVar(value="10")
        ttk.Spinbox(g, from_=3, to=32, textvariable=self.simdist_var, width=6).grid(
            row=3, column=3, sticky="w", pady=4)

        # Cases a cocher
        checks = ttk.Frame(tab, style="Panel.TFrame")
        checks.pack(fill="x", pady=(12, 0))
        self.hardcore_var = tk.BooleanVar(value=False)
        self.pvp_var = tk.BooleanVar(value=True)
        self.monsters_var = tk.BooleanVar(value=True)
        self.nether_var = tk.BooleanVar(value=True)
        self.flight_var = tk.BooleanVar(value=False)
        self.cmdblock_var = tk.BooleanVar(value=True)
        self.online_var = tk.BooleanVar(value=True)
        self.whitelist_var = tk.BooleanVar(value=False)
        cks = [
            ("Hardcore", self.hardcore_var),
            ("PVP", self.pvp_var),
            ("Monstres", self.monsters_var),
            ("Nether", self.nether_var),
            ("Vol autorise", self.flight_var),
            ("Command blocks", self.cmdblock_var),
            ("Mode en ligne (premium)", self.online_var),
            ("Whitelist", self.whitelist_var),
        ]
        for i, (t, v) in enumerate(cks):
            ttk.Checkbutton(checks, text=t, variable=v).grid(row=i // 4, column=i % 4,
                                                             sticky="w", padx=6, pady=4)

        ttk.Label(tab, style="Hint.TLabel",
                  text="Astuce : 'Plat' = superflat. Skyblock/Bedwars necessitent un datapack ou "
                       "un plugin (Paper). La config est appliquee a la preparation/au demarrage."
                  ).pack(anchor="w", pady=(12, 0))
        ttk.Button(tab, text="Appliquer la config maintenant",
                   command=self._apply_config_now).pack(anchor="w", pady=(8, 0))

    def _tab_mods(self):
        tab = ttk.Frame(self.nb, padding=14, style="Panel.TFrame")
        self.nb.add(tab, text="  Mods / Plugins  ")

        self.mods_info = ttk.Label(
            tab, style="Panel.TLabel",
            text="Prepare d'abord un serveur. Paper -> plugins, Fabric -> mods, vanilla -> aucun.")
        self.mods_info.pack(anchor="w")

        self.mods_list = tk.Listbox(tab, height=12, bg=PANEL, fg=FG,
                                    selectbackground=ACCENT, relief="flat",
                                    font=("Consolas", 9))
        self.mods_list.pack(fill="both", expand=True, pady=8)

        bar = ttk.Frame(tab, style="Panel.TFrame")
        bar.pack(fill="x")
        ttk.Button(bar, text="Ajouter des .jar", command=self._mods_add).pack(side="left")
        ttk.Button(bar, text="Supprimer la selection", style="Danger.TButton",
                   command=self._mods_remove).pack(side="left", padx=6)
        ttk.Button(bar, text="Rafraichir", command=self._mods_refresh).pack(side="left")
        ttk.Button(bar, text="Ouvrir le dossier", command=self._mods_open).pack(side="left", padx=6)

        # Recherche Modrinth
        search = ttk.LabelFrame(tab, text="Chercher sur Modrinth (telechargement direct)", padding=8)
        search.pack(fill="x", pady=(10, 0))
        self.modr_query = tk.StringVar()
        e = ttk.Entry(search, textvariable=self.modr_query)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self._modr_search())
        ttk.Button(search, text="Rechercher", command=self._modr_search).pack(side="left", padx=6)
        ttk.Button(search, text="Installer la selection", style="Accent.TButton",
                   command=self._modr_install).pack(side="left")
        self.modr_list = tk.Listbox(tab, height=7, bg=PANEL, fg=FG,
                                    selectbackground=ACCENT, relief="flat", font=("Consolas", 9))
        self.modr_list.pack(fill="x", pady=(6, 0))
        self._modr_results = []  # slugs alignes avec modr_list

    def _tab_admin(self):
        tab = ttk.Frame(self.nb, padding=14, style="Panel.TFrame")
        self.nb.add(tab, text="  Admin  ")

        ttk.Label(tab, style="Hint.TLabel",
                  text="Ces actions envoient des commandes au serveur (doit etre demarre)."
                  ).pack(anchor="w")

        pl = ttk.Frame(tab, style="Panel.TFrame")
        pl.pack(fill="x", pady=(10, 0))
        ttk.Label(pl, text="Joueur :", style="Panel.TLabel").pack(side="left")
        self.admin_player = tk.StringVar()
        ttk.Entry(pl, textvariable=self.admin_player, width=20).pack(side="left", padx=6)

        pbtns = ttk.Frame(tab, style="Panel.TFrame")
        pbtns.pack(fill="x", pady=8)
        player_actions = [
            ("OP", "op {p}"), ("De-OP", "deop {p}"),
            ("Kick", "kick {p}"), ("Ban", "ban {p}"),
            ("Whitelist +", "whitelist add {p}"), ("Whitelist -", "whitelist remove {p}"),
            ("TP vers spawn", "spawnpoint {p}"),
        ]
        for i, (label, tmpl) in enumerate(player_actions):
            ttk.Button(pbtns, text=label, command=lambda t=tmpl: self._admin_player(t)).grid(
                row=i // 4, column=i % 4, sticky="w", padx=4, pady=4)

        gm = ttk.LabelFrame(tab, text="Mode de jeu du joueur", padding=8)
        gm.pack(fill="x", pady=6)
        for i, mode in enumerate(GAMEMODES):
            ttk.Button(gm, text=mode.capitalize(),
                       command=lambda m=mode: self._admin_player(f"gamemode {m} {{p}}")).grid(
                row=0, column=i, padx=4)

        world = ttk.LabelFrame(tab, text="Monde / partie", padding=8)
        world.pack(fill="x", pady=6)
        world_actions = [
            ("Jour", "time set day"), ("Nuit", "time set night"),
            ("Beau temps", "weather clear"), ("Pluie", "weather rain"),
            ("Sauvegarder", "save-all"), ("Liste joueurs", "list"),
            ("Difficulte facile", "difficulty easy"), ("Difficulte difficile", "difficulty hard"),
        ]
        for i, (label, cmd) in enumerate(world_actions):
            ttk.Button(world, text=label, command=lambda c=cmd: self._admin_cmd(c)).grid(
                row=i // 4, column=i % 4, sticky="w", padx=4, pady=4)

        backup = ttk.LabelFrame(tab, text="Sauvegardes du monde", padding=8)
        backup.pack(fill="x", pady=6)
        ttk.Button(backup, text="Sauvegarder le monde (.zip)",
                   command=self._backup_world).pack(side="left")
        ttk.Button(backup, text="Ouvrir le dossier des sauvegardes",
                   command=self._open_backups).pack(side="left", padx=6)

        say = ttk.Frame(tab, style="Panel.TFrame")
        say.pack(fill="x", pady=(6, 0))
        ttk.Label(say, text="Message :", style="Panel.TLabel").pack(side="left")
        self.say_var = tk.StringVar()
        e = ttk.Entry(say, textvariable=self.say_var)
        e.pack(side="left", fill="x", expand=True, padx=6)
        e.bind("<Return>", lambda ev: self._admin_say())
        ttk.Button(say, text="Annoncer (say)", command=self._admin_say).pack(side="left")

    # ---------- Helpers thread-safe ----------
    def ui(self, func):
        self.root.after(0, func)

    def log(self, text):
        self._console_tail.append(text)
        def _append():
            self.console.configure(state="normal")
            self.console.insert("end", text + "\n")
            self.console.see("end")
            self.console.configure(state="disabled")
        self.ui(_append)

    def _handle_output(self, line):
        """Sortie du serveur : log + detection des connexions/deconnexions."""
        self.log(line)
        m = JOIN_RE.search(line)
        if m:
            self._players.add(m.group(1))
            self._update_players()
        m = LEFT_RE.search(line)
        if m:
            self._players.discard(m.group(1))
            self._update_players()

    def _update_players(self):
        n = len(self._players)
        label = f"{n} joueur" + ("s" if n > 1 else "")
        self.ui(lambda: self.players_lbl.configure(text=label))

    def set_status(self, text):
        self.ui(lambda: self.status_var.set(text))

    def _copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status(f"Copie dans le presse-papier : {text}")

    def _addr(self, ip):
        return f"{ip}:{self.port_var.get()}"

    # ---------- Versions ----------
    def _refresh_versions(self):
        loader = self.loader_var.get()
        self.set_status(f"Chargement des versions {loader}...")

        def work():
            try:
                versions = downloaders.list_versions(loader, self.snapshot_var.get())
                self.ui(lambda: self._set_versions(versions))
                self.set_status(f"{len(versions)} versions {loader} disponibles.")
            except Exception as e:
                self.set_status(f"Erreur liste versions : {e}")

        threading.Thread(target=work, daemon=True).start()

    def _set_versions(self, versions):
        self.version_cb["values"] = versions
        if versions:
            self.version_var.set(versions[0])

    # ---------- Properties ----------
    def _collect_properties(self):
        return dict(
            server_port=int(self.port_var.get()),
            online_mode=self.online_var.get(),
            max_players=int(self.maxplayers_var.get()),
            motd=self.motd_var.get(),
            level_seed=self.seed_var.get(),
            gamemode=self.gamemode_var.get(),
            difficulty=self.difficulty_var.get(),
            hardcore=self.hardcore_var.get(),
            level_type=LEVEL_TYPES[self.leveltype_var.get()],
            pvp=self.pvp_var.get(),
            view_distance=int(self.viewdist_var.get()),
            simulation_distance=int(self.simdist_var.get()),
            spawn_monsters=self.monsters_var.get(),
            allow_nether=self.nether_var.get(),
            allow_flight=self.flight_var.get(),
            white_list=self.whitelist_var.get(),
            enable_command_block=self.cmdblock_var.get(),
        )

    def _apply_config_now(self):
        if not self.current_dir:
            messagebox.showinfo("Config", "Prepare d'abord un serveur (onglet Serveur).")
            return
        try:
            update_properties(self.current_dir, **self._collect_properties())
            self.set_status("Config ecrite dans server.properties.")
            self.log("Config appliquee. (Redemarre le serveur pour qu'elle prenne effet.)")
        except ValueError as e:
            messagebox.showwarning("Config", f"Valeur invalide : {e}")

    # ---------- Preparer ----------
    # ---------- Serveurs installes (recharger une partie) ----------
    def _refresh_existing(self):
        servers = paths.list_servers()
        self.existing_cb["values"] = servers
        if servers and not self.existing_var.get():
            self.existing_var.set(servers[0])

    def _apply_props_to_ui(self, props):
        """Recopie une config server.properties lue vers les champs de l'UI (onglet Config)."""
        def b(key, default="true"):
            return str(props.get(key, default)).lower() == "true"

        try:
            self.port_var.set(props.get("server-port", self.port_var.get()))
            self.online_var.set(b("online-mode"))
            self.maxplayers_var.set(props.get("max-players", self.maxplayers_var.get()))
            self.motd_var.set(props.get("motd", self.motd_var.get()))
            self.seed_var.set(props.get("level-seed", ""))
            self.gamemode_var.set(props.get("gamemode", self.gamemode_var.get()))
            self.difficulty_var.set(props.get("difficulty", self.difficulty_var.get()))
            self.hardcore_var.set(b("hardcore", "false"))
            lt = props.get("level-type", "minecraft:normal")
            label = next((k for k, v in LEVEL_TYPES.items() if v == lt), None)
            if label:
                self.leveltype_var.set(label)
            self.pvp_var.set(b("pvp"))
            self.viewdist_var.set(props.get("view-distance", self.viewdist_var.get()))
            self.simdist_var.set(props.get("simulation-distance", self.simdist_var.get()))
            self.monsters_var.set(b("spawn-monsters"))
            self.nether_var.set(b("allow-nether"))
            self.flight_var.set(b("allow-flight", "false"))
            self.whitelist_var.set(b("white-list", "false"))
            self.cmdblock_var.set(b("enable-command-block", "false"))
        except (KeyError, AttributeError):
            pass  # config partielle : on garde les valeurs par defaut pour le reste

    def _load_existing(self):
        name = self.existing_var.get()
        if not name:
            return
        sdir = os.path.join(paths.SERVERS_DIR, name)
        if not os.path.isdir(sdir):
            self._refresh_existing()
            return
        loader, _, version = name.partition("-")  # "paper-1.21.11" -> paper / 1.21.11
        if not version:
            self.log(f"Nom de serveur inattendu : {name}")
            return

        self.loader_var.set(loader)
        self.version_var.set(version)
        self._apply_props_to_ui(read_properties(sdir))
        self.prepare_btn.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self.prepared = False

        def work():
            try:
                feature = required_java(version)
                self.log(f"=== Chargement de {name} (Java {feature}) ===")
                java_exe = java_manager.ensure_java(
                    feature, log=self.log,
                    progress=lambda d, t: self.set_status(f"Java : {d*100//t}%"))
                self.java_exe = java_exe
                self.current_dir = sdir
                self.prepared = True
                self.log("=== Serveur charge, pret a demarrer ===")
                self.set_status(f"Serveur charge : {name}")
                self.ui(lambda: self.start_btn.configure(state="normal"))
                self.ui(self._mods_refresh)
            except Exception as e:
                self.log(f"ERREUR chargement : {e}")
                self.set_status(f"Erreur : {e}")
            finally:
                self.ui(lambda: self.prepare_btn.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    def _delete_existing(self):
        name = self.existing_var.get()
        if not name:
            return
        if self.server and self.server.is_running():
            messagebox.showwarning("Impossible", "Arrete le serveur avant de supprimer.")
            return
        if not messagebox.askyesno(
                "Supprimer le serveur",
                f"Supprimer DEFINITIVEMENT '{name}' (monde, config, mods) ?\n"
                "Cette action est irreversible. Pense a sauvegarder le monde avant."):
            return
        sdir = os.path.join(paths.SERVERS_DIR, name)
        try:
            shutil.rmtree(sdir)
            self.log(f"Serveur supprime : {name}")
            if self.current_dir == sdir:
                self.current_dir = None
                self.prepared = False
                self.start_btn.configure(state="disabled")
            self.existing_var.set("")
            self._refresh_existing()
        except OSError as e:
            messagebox.showerror("Erreur", f"Suppression impossible :\n{e}")

    def _on_prepare(self):
        if not self.eula_var.get():
            messagebox.showwarning(
                "CLUF requis",
                "Tu dois accepter le CLUF Minecraft pour heberger un serveur.")
            return
        loader = self.loader_var.get()
        version = self.version_var.get()
        if not version:
            messagebox.showwarning("Version", "Choisis une version.")
            return

        self.prepare_btn.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self.prepared = False

        def work():
            try:
                feature = required_java(version)
                self.log(f"=== Preparation {loader} {version} (Java {feature}) ===")
                java_exe = java_manager.ensure_java(
                    feature, log=self.log,
                    progress=lambda d, t: self.set_status(f"Java : {d*100//t}%"))

                sdir = paths.server_dir(loader, version)
                downloaders.prepare(
                    loader, version, sdir, java_exe, log=self.log,
                    progress=lambda d, t: self.set_status(f"Telechargement : {d*100//t}%"))

                accept_eula(sdir)
                update_properties(sdir, **self._collect_properties())

                self.java_exe = java_exe
                self.current_dir = sdir
                self.prepared = True
                self.log("=== Pret a demarrer ===")
                self.set_status("Preparation terminee.")
                self.ui(lambda: self.start_btn.configure(state="normal"))
                self.ui(self._mods_refresh)
                self.ui(self._refresh_existing)
            except Exception as e:
                self.log(f"ERREUR : {e}")
                self.set_status(f"Erreur : {e}")
            finally:
                self.ui(lambda: self.prepare_btn.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    # ---------- Demarrer / Arreter ----------
    def _on_start(self):
        if not self.prepared:
            return
        try:
            ram = int(self.ram_var.get())
        except ValueError:
            messagebox.showwarning("RAM", "RAM invalide (nombre de Mo attendu).")
            return
        safe = sysinfo.max_safe_ram_mb()
        if ram > safe and not messagebox.askyesno(
                "RAM elevee",
                f"{ram} Mo depasse le max conseille ({safe} Mo).\nDemarrer quand meme ?"):
            return
        try:
            update_properties(self.current_dir, **self._collect_properties())
        except ValueError as e:
            messagebox.showwarning("Valeur", f"Valeur invalide : {e}")
            return

        args = downloaders.launch_args(self.loader_var.get(), self.java_exe,
                                       self.current_dir, ram)
        self._user_stopped = False
        self._players.clear()
        self._update_players()
        self.server = ServerProcess(
            args, self.current_dir,
            on_output=self._handle_output, on_exit=self._on_server_exit)
        self.server.start()
        self.start_btn.configure(state="disabled")
        self.prepare_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.state_dot.configure(text="● en marche", foreground="#7fd35a")
        self.set_status(f"Serveur lance sur le port {self.port_var.get()}.")
        self.log(f">>> Demarrage ({ram} Mo de RAM) <<<")

    def _on_server_exit(self, code):
        self.log(f">>> Serveur arrete (code {code}) <<<")
        self.set_status("Serveur arrete.")
        self._players.clear()
        self._update_players()
        self.ui(lambda: self.state_dot.configure(text="● arrete", foreground="#c8553d"))
        self.ui(lambda: self.stop_btn.configure(state="disabled"))
        self.ui(lambda: self.start_btn.configure(state="normal"))
        self.ui(lambda: self.prepare_btn.configure(state="normal"))
        # Redemarrage auto si crash (code != 0) et arret non demande par l'utilisateur
        if self.autorestart_var.get() and code != 0 and not self._user_stopped:
            self.log(">>> Crash detecte : redemarrage automatique dans 5s... <<<")
            self.ui(lambda: self.root.after(5000, self._auto_restart))

    def _auto_restart(self):
        if not (self.server and self.server.is_running()):
            self._on_start()

    def _on_stop(self):
        if self.server and self.server.is_running():
            self._user_stopped = True
            self.log(">>> Arret en cours (sauvegarde du monde)... <<<")
            self.server.stop()

    def _send_command(self):
        cmd = self.cmd_var.get().strip()
        if cmd and self.server and self.server.is_running():
            self.server.send(cmd)
            self.log(f"> {cmd}")
            self.cmd_var.set("")

    # ---------- Admin ----------
    def _server_running(self):
        if not (self.server and self.server.is_running()):
            self.set_status("Le serveur doit etre demarre pour cette action.")
            return False
        return True

    def _admin_cmd(self, command):
        if self._server_running():
            self.server.send(command)
            self.log(f"> {command}")

    def _admin_player(self, template):
        p = self.admin_player.get().strip()
        if not p:
            self.set_status("Entre un nom de joueur d'abord.")
            return
        self._admin_cmd(template.format(p=p))

    def _admin_say(self):
        msg = self.say_var.get().strip()
        if msg:
            self._admin_cmd(f"say {msg}")
            self.say_var.set("")

    # ---------- Sauvegardes ----------
    def _backups_dir(self):
        d = os.path.join(paths.DATA_DIR, "backups")
        os.makedirs(d, exist_ok=True)
        return d

    def _backup_world(self):
        if not self.current_dir:
            messagebox.showinfo("Sauvegarde", "Prepare d'abord un serveur.")
            return
        world = os.path.join(self.current_dir, "world")
        if not os.path.isdir(world):
            messagebox.showinfo("Sauvegarde", "Pas encore de monde a sauvegarder "
                                              "(demarre le serveur au moins une fois).")
            return
        if self.server and self.server.is_running():
            self.server.send("save-all")  # force l'ecriture sur disque avant la copie
        import datetime
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{os.path.basename(self.current_dir)}_{stamp}"
        base = os.path.join(self._backups_dir(), name)

        def work():
            try:
                self.log(f"Sauvegarde du monde en cours ({name}.zip)...")
                shutil.make_archive(base, "zip", world)
                self.log("Sauvegarde terminee.")
                self.set_status(f"Monde sauvegarde : {name}.zip")
            except OSError as e:
                self.log(f"Erreur sauvegarde : {e}")

        threading.Thread(target=work, daemon=True).start()

    def _open_backups(self):
        d = self._backups_dir()
        try:
            os.startfile(d)
        except OSError:
            webbrowser.open(d)

    # ---------- Mods ----------
    def _mods_dir(self):
        if not self.current_dir:
            return None
        return paths.content_folder(self.loader_var.get(), self.current_dir)

    def _mods_refresh(self):
        d = self._mods_dir()
        self.mods_list.delete(0, "end")
        if d is None:
            self.mods_info.configure(text="Vanilla : pas de mods/plugins. Choisis paper ou fabric.")
            return
        os.makedirs(d, exist_ok=True)
        self.mods_info.configure(text=f"Dossier : {d}")
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(".jar"):
                self.mods_list.insert("end", f)

    def _mods_add(self):
        d = self._mods_dir()
        if d is None:
            messagebox.showinfo("Mods", "Prepare d'abord un serveur paper ou fabric.")
            return
        files = filedialog.askopenfilenames(title="Choisir des .jar",
                                            filetypes=[("Java archive", "*.jar")])
        os.makedirs(d, exist_ok=True)
        for src in files:
            try:
                shutil.copy(src, os.path.join(d, os.path.basename(src)))
                self.log(f"Mod ajoute : {os.path.basename(src)}")
            except OSError as e:
                self.log(f"Erreur copie {src} : {e}")
        self._mods_refresh()

    def _mods_remove(self):
        d = self._mods_dir()
        if d is None:
            return
        for i in reversed(self.mods_list.curselection()):
            name = self.mods_list.get(i)
            try:
                os.remove(os.path.join(d, name))
                self.log(f"Mod supprime : {name}")
            except OSError as e:
                self.log(f"Erreur suppression {name} : {e}")
        self._mods_refresh()

    def _mods_open(self):
        d = self._mods_dir()
        if d is None:
            messagebox.showinfo("Mods", "Prepare d'abord un serveur paper ou fabric.")
            return
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)
        except OSError:
            webbrowser.open(d)

    # ---------- Modrinth ----------
    def _modr_search(self):
        q = self.modr_query.get().strip()
        if not q:
            return
        loader = self.loader_var.get()
        version = self.version_var.get()
        self.modr_list.delete(0, "end")
        self.set_status("Recherche Modrinth...")

        def work():
            try:
                results = modrinth.search(q, loader, version)
                self._modr_results = results
                self.ui(lambda: self._modr_show(results))
                self.set_status(f"{len(results)} resultats Modrinth.")
            except Exception as e:
                self.set_status(f"Erreur Modrinth : {e}")

        threading.Thread(target=work, daemon=True).start()

    def _modr_show(self, results):
        self.modr_list.delete(0, "end")
        for r in results:
            self.modr_list.insert("end", f"{r['title']}  ({r['downloads']} dl)  - {r['slug']}")

    def _modr_install(self):
        d = self._mods_dir()
        if d is None:
            messagebox.showinfo("Modrinth", "Prepare d'abord un serveur paper/fabric/forge.")
            return
        sel = self.modr_list.curselection()
        if not sel:
            self.set_status("Selectionne un mod dans la liste.")
            return
        proj = self._modr_results[sel[0]]
        loader = self.loader_var.get()
        version = self.version_var.get()
        os.makedirs(d, exist_ok=True)

        def work():
            try:
                self.log(f"Installation Modrinth : {proj['title']}...")
                name = modrinth.install(proj["slug"], loader, version, d, log=self.log)
                self.log(f"Installe : {name}")
                self.ui(self._mods_refresh)
            except Exception as e:
                self.log(f"Erreur installation Modrinth : {e}")
                self.set_status(f"Erreur : {e}")

        threading.Thread(target=work, daemon=True).start()

    # ---------- playit.gg ----------
    def _toggle_playit(self):
        if self.playit_agent and self.playit_agent.is_running():
            self.playit_agent.stop()
            self.playit_btn.configure(text="Demarrer le tunnel")
            self.playit_addr.configure(text="(arrete)")
            return

        def work():
            try:
                playit.ensure_agent(log=self.log)
                self.log("Demarrage de playit.gg (suis le lien affiche pour configurer)...")
                self.playit_agent = playit.PlayitAgent(
                    on_output=self.log, on_url=self._on_playit_url,
                    on_exit=lambda c: self.log(f">>> playit arrete (code {c}) <<<"))
                self.playit_agent.start()
                self.ui(lambda: self.playit_btn.configure(text="Arreter le tunnel"))
            except Exception as e:
                self.log(f"Erreur playit : {e}")
                self.set_status(f"Erreur playit : {e}")

        threading.Thread(target=work, daemon=True).start()

    def _on_playit_url(self, url):
        # Lien de configuration/claim : on l'ouvre dans le navigateur
        if "claim" in url or "login" in url or "setup" in url:
            webbrowser.open(url)
        self._playit_address = url
        self.ui(lambda: self.playit_addr.configure(text=url))
        self.log(f"playit : {url}")

    def _copy_playit(self):
        if self._playit_address:
            self._copy(self._playit_address)

    # ---------- Profil (config persistante) ----------
    def _gather_state(self):
        return dict(
            loader=self.loader_var.get(), version=self.version_var.get(),
            ram=self.ram_var.get(), port=self.port_var.get(),
            snapshots=self.snapshot_var.get(), autorestart=self.autorestart_var.get(),
            seed=self.seed_var.get(), motd=self.motd_var.get(),
            gamemode=self.gamemode_var.get(), difficulty=self.difficulty_var.get(),
            leveltype=self.leveltype_var.get(), maxplayers=self.maxplayers_var.get(),
            viewdist=self.viewdist_var.get(), simdist=self.simdist_var.get(),
            hardcore=self.hardcore_var.get(), pvp=self.pvp_var.get(),
            monsters=self.monsters_var.get(), nether=self.nether_var.get(),
            flight=self.flight_var.get(), cmdblock=self.cmdblock_var.get(),
            online=self.online_var.get(), whitelist=self.whitelist_var.get(),
        )

    def _save_config(self):
        config_store.save(self._gather_state())

    def _load_config(self):
        c = config_store.load()
        if not c:
            return
        setters = {
            "loader": self.loader_var, "version": self.version_var, "ram": self.ram_var,
            "port": self.port_var, "seed": self.seed_var, "motd": self.motd_var,
            "gamemode": self.gamemode_var, "difficulty": self.difficulty_var,
            "leveltype": self.leveltype_var, "maxplayers": self.maxplayers_var,
            "viewdist": self.viewdist_var, "simdist": self.simdist_var,
        }
        for key, var in setters.items():
            if key in c:
                var.set(c[key])
        bools = {
            "snapshots": self.snapshot_var, "autorestart": self.autorestart_var,
            "hardcore": self.hardcore_var, "pvp": self.pvp_var, "monsters": self.monsters_var,
            "nether": self.nether_var, "flight": self.flight_var, "cmdblock": self.cmdblock_var,
            "online": self.online_var, "whitelist": self.whitelist_var,
        }
        for key, var in bools.items():
            if key in c:
                var.set(bool(c[key]))

    # ---------- Contexte pour le rapport de crash ----------
    def crash_context(self):
        return {
            "loader": self.loader_var.get(),
            "version": self.version_var.get(),
            "ram": self.ram_var.get(),
            "port": self.port_var.get(),
            "prepared": self.prepared,
            "current_dir": self.current_dir,
            "server_running": bool(self.server and self.server.is_running()),
            "players_online": len(self._players),
            "_console_tail": list(self._console_tail),
        }

    # ---------- Reseau / divers ----------
    def _load_network_info(self):
        self._lan_ip = netinfo.lan_ip()
        self.ui(lambda: self.lan_lbl.configure(text=self._addr(self._lan_ip)))
        self.ui(lambda: self.head_status.configure(text=f"LAN {self._lan_ip}"))
        self._public_ip = netinfo.public_ip()
        self.ui(lambda: self.pub_lbl.configure(text=self._addr(self._public_ip)))

    def _show_remote_info(self):
        port = self.port_var.get()
        msg = (
            "=== JOUER EN LAN (meme reseau / wifi) ===\n"
            f"Les potes se connectent a :  {self._lan_ip}:{port}\n\n"
            "=== JOUER A DISTANCE (internet) ===\n"
            f"IP publique :  {self._public_ip}:{port}\n\n"
            f"Pour l'exterieur, ouvre le port {port} (TCP) sur ta box :\n"
            "  1. Interface de la box (souvent http://192.168.1.1)\n"
            "  2. Section 'NAT/PAT' ou 'Redirection de ports'\n"
            f"  3. Redirige le port {port} (TCP) vers {self._lan_ip}\n\n"
            "Alternative sans toucher a la box : un tunnel comme playit.gg.\n"
            "Autorise aussi java.exe dans le pare-feu Windows."
        )
        messagebox.showinfo("Acces au serveur", msg)

    def _open_folder(self):
        target = self.current_dir or paths.DATA_DIR
        try:
            os.startfile(target)
        except OSError:
            webbrowser.open(target)

    # ---------- Config envoi auto des rapports de crash ----------
    def _ask_secret(self, title, prompt):
        """Petite boite de dialogue avec saisie masquee (mot de passe)."""
        top = tk.Toplevel(self.root)
        top.title(title)
        top.configure(bg=PANEL)
        top.transient(self.root)
        top.grab_set()
        ttk.Label(top, text=prompt, style="Panel.TLabel").pack(padx=14, pady=(14, 6))
        var = tk.StringVar()
        ent = ttk.Entry(top, textvariable=var, show="*", width=34)
        ent.pack(padx=14)
        ent.focus_set()
        result = {"val": None}

        def ok():
            result["val"] = var.get().strip()
            top.destroy()

        btns = ttk.Frame(top, style="Panel.TFrame")
        btns.pack(pady=12)
        ttk.Button(btns, text="OK", command=ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Annuler", command=top.destroy).pack(side="left", padx=4)
        ent.bind("<Return>", lambda e: ok())
        self.root.wait_window(top)
        return result["val"]

    def _config_mail(self):
        configured = " (deja configure)" if mailer.is_configured() else ""
        if not messagebox.askyesno(
                "Envoi auto des rapports" + configured,
                "Pour ENVOYER les rapports de crash automatiquement (sans clic), il faut "
                "un compte Gmail + un 'mot de passe d'application' (16 caracteres).\n\n"
                "Cree-le sur https://myaccount.google.com/apppasswords (2FA requise).\n\n"
                "Continuer la configuration ?"):
            return
        address = simpledialog.askstring(
            "Adresse Gmail", "Adresse Gmail qui ENVOIE les rapports :",
            initialvalue=crash_reporter.DEV_EMAIL, parent=self.root)
        if not address:
            return
        pwd = self._ask_secret("Mot de passe d'application",
                               "Mot de passe d'application Gmail (16 car.) :")
        if not pwd:
            return
        mailer.save_creds(address.strip(), pwd)
        if messagebox.askyesno("Test", "Identifiants enregistres.\nEnvoyer un mail de test ?"):
            self._test_mail()

    def _test_mail(self):
        def work():
            try:
                mailer.send(crash_reporter.DEV_EMAIL, "MZ Launcher - test",
                            "Ceci est un mail de test : l'envoi auto des rapports fonctionne.")
                self.ui(lambda: messagebox.showinfo("Test", "Mail de test envoye !"))
            except Exception as e:
                self.ui(lambda: messagebox.showerror(
                    "Echec", f"Envoi impossible :\n{e}\n\n"
                             "Verifie l'adresse et le mot de passe d'application."))
        threading.Thread(target=work, daemon=True).start()


def main():
    root = tk.Tk()
    app = App(root)

    # Rapport de crash : envoi auto (SMTP) si configure, sinon ouverture du client mail.
    def on_report(path, mode):
        try:
            if mode == "auto":
                detail = "Le rapport a ete ENVOYE automatiquement au dev."
            else:
                detail = ("Ton client mail s'est ouvert : clique sur Envoyer.\n"
                          "(Active l'envoi auto via le bouton 'Rapports auto' dans l'onglet Serveur.)")
            messagebox.showerror(
                "Plantage",
                f"Une erreur est survenue.\n\n{detail}\n\nFichier du rapport :\n{path}")
        except tk.TclError:
            pass

    handler = crash_reporter.install(app.crash_context, APP_VERSION, on_report)
    root.report_callback_exception = lambda et, e, tb: handler(et, e, tb)

    def on_close():
        if app.server and app.server.is_running():
            if not messagebox.askyesno("Quitter", "Le serveur tourne. L'arreter et quitter ?"):
                return
            app.server.kill()
        if app.playit_agent and app.playit_agent.is_running():
            app.playit_agent.stop()
        app._save_config()  # memorise le profil pour la prochaine fois
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
