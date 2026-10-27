import asyncio
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import threading
import time
import wave
from collections.abc import Coroutine
from concurrent.futures import Future
from datetime import timedelta
from tkinter import Canvas, messagebox
from typing import Any

import customtkinter as ctk
import discord
import imageio_ffmpeg
import keyring
from keyring.backends.Windows import WinVaultKeyring
from discord import app_commands
from discord.ext import voice_recv
from groq import Groq


class BotDashboard:
    CREDENTIAL_SERVICE = "Orbit.DiscordBotDashboard"
    CREDENTIAL_USERNAME = "discord-bot-token"
    GROQ_CREDENTIAL_USERNAME = "groq-api-key"
    GROQ_CHAT_MODEL = "openai/gpt-oss-120b"
    GROQ_SPEECH_MODEL = "canopylabs/orpheus-v1-english"
    GROQ_SPEECH_VOICE = "troy"
    GROQ_TRANSCRIPTION_MODEL = "whisper-large-v3-turbo"
    VOICE_TRANSCRIPTION_COOLDOWN = 10
    MAX_FEED_MESSAGES = 100

    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.root.title("Orbit | Discord Bot Dashboard")
        self.root.geometry("1000x820")
        self.root.minsize(820, 680)
        self.root.configure(fg_color="#0b1020")

        self.client: discord.Client | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.worker: threading.Thread | None = None
        self.started_at: float | None = None
        self.closing = False
        self.guild_options: dict[str, int] = {}
        self.role_options: dict[str, int] = {}
        self.channel_options: dict[str, int] = {}
        self.selected_guild_id: int | None = None
        self.selected_channel_id: int | None = None
        self.feed_request_id = 0
        self.selected_message: discord.Message | None = None
        self.feed_cards: list[tuple[discord.Message, ctk.CTkFrame]] = []
        self.voice_channel_options: dict[str, int] = {}
        self.voice_client: discord.VoiceClient | None = None
        self.voice_self_muted = False
        self.voice_self_deafened = False
        self.voice_listening = False
        self.voice_wake_sink: voice_recv.AudioSink | None = None
        self.interaction_restricted = False
        self.allowed_user_ids: frozenset[int] = frozenset()
        self.allowed_role_ids: frozenset[int] = frozenset()
        self.wake_keywords: tuple[str, ...] = ("bot", "joe biden")
        self.ai_replies_enabled = False
        self.ai_mention_only = True
        self.ai_provider = "Groq"
        self.speak_manual_replies = False
        self.ai_history: dict[int, list[dict[str, str]]] = {}
        self.voice_processing_users: set[int] = set()
        self.ai_last_reply_at: dict[int, float] = {}
        self.gateway_latency_samples: list[float] = []
        self.activity_counters = {
            "messages": 0,
            "ai_replies": 0,
            "voice_replies": 0,
            "errors": 0,
        }
        self.activity_events: list[tuple[str, str]] = []
        self.quiet_mode = False
        self._quiet_previous_ai_mode = False
        self.motion_enabled = ctk.BooleanVar(value=True)
        self._layout_after_id: str | None = None
        self.ai_reply_lock: asyncio.Lock | None = None
        self.voice_speech_lock: asyncio.Lock | None = None
        self.groq_api_key: str | None = None
        self.credential_store = WinVaultKeyring()
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        self.settings_path = local_app_data / "Orbit" / "dashboard-settings.json"

        self._build_ui()
        self._load_app_settings()
        self._load_saved_token()
        self._load_groq_key()
        self.root.protocol("WM_DELETE_WINDOW", self._close_window)
        self._refresh_uptime()
        self._animate_ui_surfaces()
        self._animate_dashboard()

    def _build_ui(self) -> None:
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self.root, width=205, corner_radius=0, fg_color="#10182b")
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar, text="ORBIT", font=ctk.CTkFont(size=24, weight="bold"),
            text_color="#8b9cff",
        ).pack(anchor="w", padx=22, pady=(26, 2))
        ctk.CTkLabel(
            sidebar, text="DISCORD BOT PANEL", font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#7d89a8",
        ).pack(anchor="w", padx=23, pady=(0, 18))
        self.sidebar_status_label = ctk.CTkLabel(sidebar, text="\u25cf  BOT OFFLINE", text_color="#929db8")
        self.sidebar_status_label.pack(anchor="w", padx=22, pady=(0, 8))
        self.clock_label = ctk.CTkLabel(sidebar, text="LOCAL TIME  --:--:--", font=ctk.CTkFont(family="Consolas", size=10), text_color="#7d89a8")
        self.clock_label.pack(anchor="w", padx=22, pady=(0, 14))
        ctk.CTkLabel(
            sidebar, text="●  BOT OVERVIEW", font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#dce3ff",
        ).pack(anchor="w", padx=22, pady=8)
        ctk.CTkLabel(
            sidebar, text="◈  Safe local controls", font=ctk.CTkFont(size=12),
            text_color="#8995b3",
        ).pack(anchor="w", padx=22, pady=8)
        ctk.CTkLabel(
            sidebar, text="⌁  Token in Credential Manager", font=ctk.CTkFont(size=12),
            text_color="#8995b3",
        ).pack(anchor="w", padx=22, pady=8)
        ctk.CTkLabel(
            sidebar, text="discord.py  •  Gateway", font=ctk.CTkFont(size=10),
            text_color="#64708e",
        ).pack(side="bottom", anchor="w", padx=22, pady=20)
        ctk.CTkSwitch(
            sidebar, text="Motion effects", variable=self.motion_enabled,
            command=self._set_motion_effects, progress_color="#5865f2",
        ).pack(side="bottom", anchor="w", padx=22, pady=(8, 0))

        main = ctk.CTkScrollableFrame(self.root, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=28, pady=24)
        main.grid_columnconfigure((0, 1), weight=1)

        heading = ctk.CTkFrame(main, fg_color="transparent")
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        ctk.CTkLabel(
            heading, text="Bot overview", font=ctk.CTkFont(size=26, weight="bold"),
            text_color="#f0f3ff",
        ).pack(anchor="w")
        ctk.CTkLabel(
            heading, text="Manage your Discord bot connection and presence.",
            font=ctk.CTkFont(size=12), text_color="#929db8",
        ).pack(anchor="w", pady=(4, 0))

        self.status_value = self._stat_card(main, 1, 0, "CONNECTION", "Offline")
        self.servers_value = self._stat_card(main, 1, 1, "SERVERS", "—")
        self.uptime_value = self._stat_card(main, 2, 0, "SESSION UPTIME", "—")
        self.account_value = self._stat_card(main, 2, 1, "BOT ACCOUNT", "Not connected")

        control = ctk.CTkFrame(main, corner_radius=14, fg_color="#141d32")
        control.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(15, 12))
        control.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            control, text="Connection", font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#e9edfa",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 4))
        self.token_entry = ctk.CTkEntry(
            control, placeholder_text="Paste bot token for this session",
            show="•", height=38,
        )
        self.token_entry.grid(row=1, column=0, sticky="ew", padx=(18, 8), pady=10)
        self.connect_button = ctk.CTkButton(
            control, text="Start bot", width=125, height=38,
            fg_color="#5865f2", hover_color="#4752c4", command=self._start_bot,
        )
        self.connect_button.grid(row=1, column=1, padx=(8, 18), pady=10)
        self.remember_token = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            control, text="Remember token in Windows Credential Manager",
            variable=self.remember_token, text_color="#aab5ce",
            fg_color="#5865f2", hover_color="#4752c4",
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(0, 8))
        ctk.CTkButton(
            control, text="Forget saved token", command=self._forget_saved_token,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=2, column=1, sticky="e", padx=18, pady=(0, 8))
        ctk.CTkLabel(
            control, text="Saved only after a successful connection; protected by your Windows account.",
            font=ctk.CTkFont(size=11), text_color="#8995b3",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 15))

        presence = ctk.CTkFrame(main, corner_radius=14, fg_color="#141d32")
        presence.grid(row=4, column=0, sticky="nsew", padx=(0, 7), pady=(0, 12))
        ctk.CTkLabel(
            presence, text="Bot presence", font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#e9edfa",
        ).pack(anchor="w", padx=16, pady=(15, 8))
        self.presence_menu = ctk.CTkOptionMenu(
            presence, values=["Online", "Idle", "Do Not Disturb", "Invisible"],
            fg_color="#293653", button_color="#5865f2",
        )
        self.presence_menu.pack(fill="x", padx=16, pady=5)
        self.activity_entry = ctk.CTkEntry(presence, placeholder_text="Activity, e.g. building", height=34)
        self.activity_entry.pack(fill="x", padx=16, pady=5)
        ctk.CTkButton(
            presence, text="Apply presence", command=self._apply_presence,
            fg_color="#293653", hover_color="#354567",
        ).pack(fill="x", padx=16, pady=(5, 15))

        commands = ctk.CTkFrame(main, corner_radius=14, fg_color="#141d32")
        commands.grid(row=4, column=1, sticky="nsew", padx=(7, 0), pady=(0, 12))
        ctk.CTkLabel(
            commands, text="Included command", font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#e9edfa",
        ).pack(anchor="w", padx=16, pady=(15, 7))
        ctk.CTkLabel(
            commands,
            text="/ping  •  /help  •  /ask  •  /poll  •  /avatar  •  /serverinfo  •  /userinfo  •  /kick  •  /ban",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#8b9cff",
        ).pack(anchor="w", padx=16)
        ctk.CTkLabel(
            commands,
            text="/kick and /ban also available to server moderators.",
            font=ctk.CTkFont(size=11), text_color="#8995b3", wraplength=300,
        ).pack(anchor="w", padx=16, pady=(3, 14))

        admin = ctk.CTkFrame(main, corner_radius=14, fg_color="#141d32")
        admin.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        admin.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(
            admin, text="Server admin", font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#e9edfa",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(15, 4))
        ctk.CTkLabel(
            admin,
            text="Needs the bot's matching permission. Enable Server Members Intent for role changes and Message Content Intent for the feed in the Developer Portal.",
            font=ctk.CTkFont(size=11), text_color="#8995b3",
            wraplength=720, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 8))
        self.guild_menu = ctk.CTkOptionMenu(
            admin, values=["Connect bot first"], command=self._guild_changed,
            fg_color="#293653", button_color="#5865f2",
        )
        self.guild_menu.grid(row=2, column=0, sticky="ew", padx=(16, 6), pady=5)
        ctk.CTkButton(
            admin, text="Refresh server list", command=self._refresh_guilds,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=2, column=1, sticky="ew", padx=(6, 16), pady=5)

        self.member_id_entry = ctk.CTkEntry(
            admin, placeholder_text="Member user ID (numeric)", height=36,
        )
        self.member_id_entry.grid(
            row=3, column=0, sticky="ew", padx=(16, 6), pady=(10, 5)
        )
        self.ban_reason_entry = ctk.CTkEntry(
            admin, placeholder_text="Ban reason (optional)", height=36,
        )
        self.ban_reason_entry.grid(
            row=3, column=1, sticky="ew", padx=(6, 16), pady=(10, 5)
        )
        ctk.CTkButton(
            admin, text="Ban member…", command=self._ban_member,
            fg_color="#a83f52", hover_color="#863244",
        ).grid(row=4, column=0, sticky="ew", padx=(16, 6), pady=(5, 12))
        self.role_menu = ctk.CTkOptionMenu(
            admin, values=["Select a server first"],
            fg_color="#293653", button_color="#5865f2",
        )
        self.role_menu.grid(row=4, column=1, sticky="ew", padx=(6, 16), pady=(5, 12))

        ctk.CTkLabel(
            admin, text="Role name", font=ctk.CTkFont(size=11), text_color="#8995b3",
        ).grid(row=5, column=0, sticky="w", padx=16)
        ctk.CTkLabel(
            admin, text="Role color (#RRGGBB)", font=ctk.CTkFont(size=11),
            text_color="#8995b3",
        ).grid(row=5, column=1, sticky="w", padx=16)
        self.role_name_entry = ctk.CTkEntry(admin, placeholder_text="Role name", height=35)
        self.role_name_entry.grid(row=6, column=0, sticky="ew", padx=(16, 6), pady=5)
        self.role_color_entry = ctk.CTkEntry(
            admin, placeholder_text="#5865F2", height=35,
        )
        self.role_color_entry.grid(row=6, column=1, sticky="ew", padx=(6, 16), pady=5)

        role_buttons = ctk.CTkFrame(admin, fg_color="transparent")
        role_buttons.grid(row=7, column=0, columnspan=2, sticky="ew", padx=12, pady=5)
        role_buttons.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkButton(
            role_buttons, text="Create role", command=self._create_role,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=0, column=0, sticky="ew", padx=4)
        ctk.CTkButton(
            role_buttons, text="Edit selected", command=self._edit_role,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=0, column=1, sticky="ew", padx=4)
        ctk.CTkButton(
            role_buttons, text="Delete selected…", command=self._delete_role,
            fg_color="#a83f52", hover_color="#863244",
        ).grid(row=0, column=2, sticky="ew", padx=4)

        ctk.CTkLabel(
            admin, text="Member user ID for role grant/revoke:",
            font=ctk.CTkFont(size=11, weight="bold"), text_color="#c3cce1",
        ).grid(row=8, column=0, columnspan=2, sticky="w", padx=16, pady=(8, 3))
        self.role_member_id_entry = ctk.CTkEntry(
            admin, placeholder_text="Enter the member's numeric Discord user ID", height=36,
        )
        self.role_member_id_entry.grid(
            row=9, column=0, columnspan=2, sticky="ew", padx=16, pady=(2, 5)
        )
        ctk.CTkButton(
            admin, text="Grant role…", command=lambda: self._change_member_role(True),
            fg_color="#347b62", hover_color="#28624e",
        ).grid(row=10, column=0, sticky="ew", padx=(16, 6), pady=(4, 15))
        ctk.CTkButton(
            admin, text="Revoke role…", command=lambda: self._change_member_role(False),
            fg_color="#765a36", hover_color="#5e472b",
        ).grid(row=10, column=1, sticky="ew", padx=(6, 16), pady=(4, 15))

        feed = ctk.CTkFrame(main, corner_radius=14, fg_color="#141d32")
        feed.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        feed.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            feed, text="Live message feed", font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#e9edfa",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 4))
        ctk.CTkLabel(
            feed, text="Recent and live messages. Select one, type a reply, then send.",
            font=ctk.CTkFont(size=11), text_color="#8995b3",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 7))
        self.channel_menu = ctk.CTkOptionMenu(
            feed, values=["Select a server first"], command=self._channel_changed,
            fg_color="#293653", button_color="#5865f2",
        )
        self.channel_menu.grid(row=2, column=0, sticky="ew", padx=(16, 6), pady=5)
        ctk.CTkButton(
            feed, text="Reload history", command=self._reload_channel_history,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=2, column=1, sticky="ew", padx=(6, 16), pady=5)
        self.feed_status_label = ctk.CTkLabel(
            feed,
            text="Requires Message Content Intent and channel View Channel/Read Message History permissions.",
            font=ctk.CTkFont(size=10), text_color="#8995b3", anchor="w",
            wraplength=720, justify="left",
        )
        self.feed_status_label.grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(2, 4)
        )
        self.feed_frame = ctk.CTkScrollableFrame(
            feed, height=200, corner_radius=10, fg_color="#0e1526",
        )
        self.feed_frame.grid(row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=5)
        self.feed_empty_label = ctk.CTkLabel(
            self.feed_frame, text="Connect, choose a server and channel, then wait for messages.",
            text_color="#8995b3",
        )
        self.feed_empty_label.pack(anchor="w", padx=10, pady=10)
        self.reply_context_label = ctk.CTkLabel(
            feed, text="No message selected", font=ctk.CTkFont(size=11),
            text_color="#8995b3",
        )
        self.reply_context_label.grid(row=5, column=0, columnspan=2, sticky="w", padx=16, pady=(6, 2))
        self.reply_entry = ctk.CTkEntry(
            feed, placeholder_text="Write a reply…", height=36,
        )
        self.reply_entry.grid(row=6, column=0, sticky="ew", padx=(16, 6), pady=(4, 14))
        ctk.CTkButton(
            feed, text="Reply", command=self._reply_to_selected,
            fg_color="#5865f2", hover_color="#4752c4",
        ).grid(row=6, column=1, sticky="ew", padx=(6, 16), pady=(4, 14))

        ai_panel = ctk.CTkFrame(main, corner_radius=14, fg_color="#141d32")
        ai_panel.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ai_panel.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkLabel(
            ai_panel, text="AI conversation", font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#e9edfa",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 4))
        ctk.CTkLabel(
            ai_panel,
            text="AI mode replies in the selected channel when mentioned; manual mode leaves replies to you. "
            "Messages are sent to the provider you choose.",
            font=ctk.CTkFont(size=11), text_color="#8995b3",
            wraplength=720, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 7))
        self.ai_mode_menu = ctk.CTkOptionMenu(
            ai_panel, values=["Manual replies", "AI auto-reply"],
            command=self._set_ai_mode,
            fg_color="#293653", button_color="#5865f2",
        )
        self.ai_mode_menu.grid(row=2, column=0, sticky="ew", padx=(16, 6), pady=5)
        self.ai_provider_menu = ctk.CTkOptionMenu(
            ai_panel, values=["Groq", "GitHub Copilot"],
            command=self._set_ai_provider,
            fg_color="#293653", button_color="#5865f2",
        )
        self.ai_provider_menu.grid(row=2, column=1, sticky="ew", padx=(6, 16), pady=5)
        self.groq_key_entry = ctk.CTkEntry(
            ai_panel, placeholder_text="Groq API key (needed for Groq chat and voice)",
            show="•", height=36,
        )
        self.groq_key_entry.grid(row=3, column=0, sticky="ew", padx=(16, 6), pady=5)
        ctk.CTkButton(
            ai_panel, text="Save Groq key securely", command=self._save_groq_key,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=3, column=1, sticky="ew", padx=6, pady=5)
        self.groq_test_button = ctk.CTkButton(
            ai_panel, text="Test Groq", command=self._test_groq,
            fg_color="#293653", hover_color="#354567",
        )
        self.groq_test_button.grid(row=3, column=2, sticky="ew", padx=(6, 16), pady=5)
        self.ai_mentions_only_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            ai_panel, text="Only reply when the bot is mentioned",
            variable=self.ai_mentions_only_var, text_color="#aab5ce",
            command=self._set_ai_mention_rule,
            fg_color="#5865f2", hover_color="#4752c4",
        ).grid(row=4, column=0, sticky="w", padx=16, pady=(7, 4))
        self.speak_manual_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            ai_panel, text="Speak dashboard replies in voice",
            variable=self.speak_manual_var, text_color="#aab5ce",
            command=self._set_manual_speech,
            fg_color="#5865f2", hover_color="#4752c4",
        ).grid(row=4, column=1, sticky="w", padx=16, pady=(7, 4))
        self.ai_status_label = ctk.CTkLabel(
            ai_panel,
            text="AI is off. Copilot requires a signed-in Copilot SDK runtime; Groq requires an API key.",
            font=ctk.CTkFont(size=10), text_color="#8995b3", anchor="w",
            wraplength=720, justify="left",
        )
        ctk.CTkButton(
            ai_panel, text="Clear channel AI context", command=self._clear_ai_context,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=5, column=0, sticky="ew", padx=(16, 6), pady=(5, 5))
        self.ai_status_label.grid(
            row=5, column=1, sticky="ew", padx=(6, 16), pady=(5, 5)
        )

        voice_panel = ctk.CTkFrame(main, corner_radius=14, fg_color="#141d32")
        voice_panel.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        voice_panel.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)
        ctk.CTkLabel(
            voice_panel, text="Voice channel", font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#e9edfa",
        ).grid(row=0, column=0, columnspan=5, sticky="w", padx=16, pady=(14, 4))
        ctk.CTkLabel(
            voice_panel,
            text="Listening is off by default. When started, authorized speakers' short audio clips "
            "are sent to Groq for wake-word transcription; audio and transcripts are not saved to disk. "
            "Groq has a 10-second minimum charge per transcription; each speaker is limited to one "
            "transcription every 10 seconds.",
            font=ctk.CTkFont(size=11), text_color="#8995b3",
            wraplength=720, justify="left",
        ).grid(row=1, column=0, columnspan=5, sticky="w", padx=16, pady=(0, 7))
        self.voice_channel_menu = ctk.CTkOptionMenu(
            voice_panel, values=["Select a server first"],
            fg_color="#293653", button_color="#5865f2",
        )
        self.voice_channel_menu.grid(
            row=2, column=0, columnspan=5, sticky="ew", padx=16, pady=5
        )
        self.voice_join_button = ctk.CTkButton(
            voice_panel, text="Join voice", command=self._join_voice,
            fg_color="#347b62", hover_color="#28624e",
        )
        self.voice_join_button.grid(row=3, column=0, sticky="ew", padx=(16, 5), pady=(5, 14))
        self.voice_mute_button = ctk.CTkButton(
            voice_panel, text="Mute bot", command=self._toggle_voice_mute,
            fg_color="#293653", hover_color="#354567",
        )
        self.voice_mute_button.grid(row=3, column=1, sticky="ew", padx=5, pady=(5, 14))
        self.voice_deafen_button = ctk.CTkButton(
            voice_panel, text="Deafen bot", command=self._toggle_voice_deafen,
            fg_color="#293653", hover_color="#354567",
        )
        self.voice_deafen_button.grid(row=3, column=2, sticky="ew", padx=5, pady=(5, 14))
        ctk.CTkButton(
            voice_panel, text="Test voice", command=self._test_voice,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=3, column=3, sticky="ew", padx=5, pady=(5, 14))
        ctk.CTkButton(
            voice_panel, text="Leave voice", command=self._leave_voice,
            fg_color="#a83f52", hover_color="#863244",
        ).grid(row=3, column=4, sticky="ew", padx=(5, 16), pady=(5, 14))
        self.voice_listen_button = ctk.CTkButton(
            voice_panel, text="Start listening", command=self._toggle_voice_listening,
            fg_color="#765a36", hover_color="#5e472b",
        )
        self.voice_listen_button.grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=(16, 5), pady=(0, 12)
        )
        self.voice_status_label = ctk.CTkLabel(
            voice_panel, text="Not connected to voice", font=ctk.CTkFont(size=10),
            text_color="#8995b3", anchor="w",
        )
        self.voice_status_label.grid(
            row=4, column=2, columnspan=3, sticky="ew", padx=(5, 16), pady=(0, 12)
        )

        access_panel = ctk.CTkFrame(main, corner_radius=14, fg_color="#141d32")
        access_panel.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        access_panel.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(
            access_panel, text="Interaction access and wake words",
            font=ctk.CTkFont(size=16, weight="bold"), text_color="#e9edfa",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 4))
        ctk.CTkLabel(
            access_panel,
            text="Restrict slash commands, AI replies, and voice replies to the IDs/roles below. "
            "Discord channel permissions still control who can see messages.",
            font=ctk.CTkFont(size=11), text_color="#8995b3",
            wraplength=720, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 7))
        self.restrict_interactions_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            access_panel, text="Restrict interactions to allowed users/roles",
            variable=self.restrict_interactions_var, text_color="#aab5ce",
            fg_color="#5865f2", hover_color="#4752c4",
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=16, pady=4)
        self.allowed_user_ids_entry = ctk.CTkEntry(
            access_panel, placeholder_text="Allowed user IDs (comma-separated)", height=36,
        )
        self.allowed_user_ids_entry.grid(
            row=3, column=0, sticky="ew", padx=(16, 6), pady=5
        )
        self.allowed_role_ids_entry = ctk.CTkEntry(
            access_panel, placeholder_text="Allowed role IDs (comma-separated)", height=36,
        )
        self.allowed_role_ids_entry.grid(
            row=3, column=1, sticky="ew", padx=(6, 16), pady=5
        )
        self.wake_keywords_entry = ctk.CTkEntry(
            access_panel, placeholder_text="Voice wake words, comma-separated (e.g. bot, joe biden)",
            height=36,
        )
        self.wake_keywords_entry.insert(0, "bot, joe biden")
        self.wake_keywords_entry.grid(
            row=4, column=0, sticky="ew", padx=(16, 6), pady=(5, 12)
        )
        ctk.CTkButton(
            access_panel, text="Apply access and wake words",
            command=self._apply_interaction_settings,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=4, column=1, sticky="ew", padx=(6, 16), pady=(5, 12))

        self.log_box = ctk.CTkTextbox(
            main, height=95, corner_radius=12, fg_color="#0e1526",
            text_color="#aeb9d3", font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.log_box.grid(row=11, column=0, columnspan=2, sticky="nsew", pady=(0, 12))
        self.log_box.insert("end", "Dashboard ready. Start the bot to connect.\n")
        self.log_box.configure(state="disabled")
        signal_panel = ctk.CTkFrame(main, corner_radius=14, fg_color="#141d32")
        signal_panel.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        signal_panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            signal_panel, text="Gateway signal",
            font=ctk.CTkFont(size=16, weight="bold"), text_color="#e9edfa",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 2))
        self.gateway_latency_label = ctk.CTkLabel(
            signal_panel, text="— ms", font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#edf0ff",
        )
        self.gateway_latency_label.grid(row=0, column=1, sticky="e", padx=16, pady=(14, 2))
        self.gateway_health_label = ctk.CTkLabel(
            signal_panel, text="Connect the bot to start monitoring.",
            font=ctk.CTkFont(size=11), text_color="#8995b3", anchor="w",
        )
        self.gateway_health_label.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 7)
        )
        self.gateway_chart = Canvas(
            signal_panel, height=96, background="#0e1526",
            highlightthickness=0, borderwidth=0,
        )
        self.gateway_chart.grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 14)
        )
        self.gateway_chart.bind("<Configure>", self._draw_gateway_chart)

        main.grid_rowconfigure(11, weight=1)
        self.root.bind("<Configure>", self._schedule_label_reflow, add="+")
        self.root.after(100, self._reflow_labels)

    @staticmethod
    def _stat_card(
        parent: ctk.CTkFrame, row: int, column: int, title: str, value: str
    ) -> ctk.CTkLabel:
        card = ctk.CTkFrame(parent, corner_radius=14, fg_color="#141d32")
        card.grid(row=row, column=column, sticky="ew", padx=6, pady=6)
        ctk.CTkLabel(
            card, text=title, font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#8995b3",
        ).pack(anchor="w", padx=15, pady=(12, 3))
        value_label = ctk.CTkLabel(
            card, text=value, font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#edf0ff",
        )
        value_label.pack(anchor="w", padx=15, pady=(0, 12))
        return value_label

    def _log(self, message: str) -> None:
        if self.closing:
            return
        self.activity_events.append((time.strftime("%H:%M:%S"), message))
        del self.activity_events[:-50]
        if any(term in message.casefold() for term in ("failed", "error", "issue", "could not")):
            self.activity_counters["errors"] += 1
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        if hasattr(self, "_feature_refresh_activity"):
            self._feature_refresh_activity()

    def _load_saved_token(self) -> None:
        try:
            token = self.credential_store.get_password(
                self.CREDENTIAL_SERVICE, self.CREDENTIAL_USERNAME
            )
        except keyring.errors.KeyringError as error:
            self._log(f"Could not access Windows Credential Manager: {error}")
            messagebox.showwarning(
                "Credential store unavailable",
                "The saved token could not be loaded. You can enter it manually.",
            )
            return
        if token:
            self.token_entry.insert(0, token)
            self._log("Loaded the saved bot token from Windows Credential Manager.")

    def _load_groq_key(self) -> None:
        try:
            saved_key = self.credential_store.get_password(
                self.CREDENTIAL_SERVICE, self.GROQ_CREDENTIAL_USERNAME
            )
        except keyring.errors.KeyringError as error:
            self._log(f"Could not load the Groq key from Windows Credential Manager: {error}")
            return
        if saved_key:
            self.groq_key_entry.configure(placeholder_text="Groq API key saved securely")

    def _save_groq_key(self) -> None:
        api_key = self.groq_key_entry.get().strip()
        if not api_key:
            messagebox.showinfo("Groq API key needed", "Paste your Groq API key before saving.")
            return
        try:
            self.credential_store.set_password(
                self.CREDENTIAL_SERVICE, self.GROQ_CREDENTIAL_USERNAME, api_key
            )
        except keyring.errors.KeyringError as error:
            self._log(f"Could not save Groq API key: {error}")
            messagebox.showerror(
                "Could not save key",
                "Windows Credential Manager could not store the Groq API key.",
            )
            return
        self.groq_api_key = api_key
        self.groq_key_entry.delete(0, "end")
        self.groq_key_entry.configure(placeholder_text="Groq API key saved securely")
        self._log("Saved Groq API key in Windows Credential Manager.")

    def _get_groq_key(self) -> str | None:
        entered_key = self.groq_key_entry.get().strip()
        if entered_key:
            return entered_key
        try:
            return self.credential_store.get_password(
                self.CREDENTIAL_SERVICE, self.GROQ_CREDENTIAL_USERNAME
            )
        except keyring.errors.KeyringError as error:
            self._log(f"Could not read Groq API key: {error}")
            return None

    def _test_groq(self) -> None:
        api_key = self._get_groq_key()
        if not api_key:
            messagebox.showinfo(
                "Groq API key needed",
                "Enter a Groq API key or save one before testing.",
            )
            return
        self.groq_test_button.configure(state="disabled", text="Testing…")
        self.ai_status_label.configure(text="Testing Groq API connection and chat model…")

        def test() -> None:
            try:
                asyncio.run(
                    self._groq_chat_reply(
                        [], "Reply with exactly: Groq connection works.",
                        api_key=api_key,
                    )
                )
            except Exception as error:
                error_text = str(error).replace(api_key, "[redacted]")
                self.root.after(
                    0, self._groq_test_finished, False,
                    f"Groq test failed ({type(error).__name__}): {error_text}",
                )
            else:
                self.root.after(
                    0, self._groq_test_finished, True,
                    f"Groq test passed using {self.GROQ_CHAT_MODEL}.",
                )

        threading.Thread(target=test, daemon=True).start()

    def _groq_test_finished(self, succeeded: bool, message: str) -> None:
        if self.closing:
            return
        self.groq_test_button.configure(state="normal", text="Test Groq")
        self.ai_status_label.configure(
            text=message, text_color="#61d995" if succeeded else "#f07878"
        )
        self._log(message)

    def _set_ai_mode(self, selection: str) -> None:
        self.ai_replies_enabled = selection == "AI auto-reply"
        if self.quiet_mode:
            self._quiet_previous_ai_mode = self.ai_replies_enabled
            self.ai_replies_enabled = False
        self._save_app_settings()
        if self.quiet_mode:
            self.ai_status_label.configure(text="Quiet mode is on; automatic replies are paused.")
        elif self.ai_replies_enabled:
            self.ai_status_label.configure(
                text="AI auto-reply is on for the selected channel. By default, it answers only when mentioned."
            )
            self._log(f"AI auto-reply enabled with {self.ai_provider}.")
        else:
            self.ai_status_label.configure(
                text="Manual mode: AI auto-replies are off. Use the message feed to reply yourself."
            )
            self._log("Manual reply mode enabled.")

    @staticmethod
    def _parse_id_list(value: str, label: str) -> frozenset[int]:
        ids: set[int] = set()
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            if not item.isdecimal() or not 17 <= len(item) <= 20:
                raise ValueError(f"{label} must contain comma-separated Discord IDs (17-20 digits).")
            ids.add(int(item))
        return frozenset(ids)

    def _apply_interaction_settings(self) -> None:
        try:
            user_ids = self._parse_id_list(
                self.allowed_user_ids_entry.get(), "Allowed user IDs"
            )
            role_ids = self._parse_id_list(
                self.allowed_role_ids_entry.get(), "Allowed role IDs"
            )
            keywords = tuple(
                " ".join(re.findall(r"[a-z0-9']+", keyword.casefold()))
                for keyword in self.wake_keywords_entry.get().split(",")
                if keyword.strip() and re.findall(r"[a-z0-9']+", keyword.casefold())
            )
            if not keywords:
                raise ValueError("Enter at least one voice wake word or phrase.")
            if any(len(keyword) > 80 for keyword in keywords):
                raise ValueError("Each wake word or phrase must be 80 characters or fewer.")
            if len(keywords) > 10:
                raise ValueError("Enter no more than 10 wake words or phrases.")
        except ValueError as error:
            messagebox.showerror("Invalid interaction settings", str(error))
            return

        self.allowed_user_ids = user_ids
        self.allowed_role_ids = role_ids
        self.interaction_restricted = self.restrict_interactions_var.get()
        self.wake_keywords = keywords
        self._save_app_settings()
        if self.interaction_restricted and not (user_ids or role_ids):
            self._log("Interaction restriction applied; no users or roles are allowed yet.")
        else:
            self._log(
                f"Interaction access applied; {len(user_ids)} user ID(s), "
                f"{len(role_ids)} role ID(s), and {len(keywords)} wake phrase(s)."
            )

    def _load_app_settings(self) -> None:
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as error:
            self._log(f"Could not load dashboard settings: {error}")
            return
        if not isinstance(settings, dict):
            self._log("Dashboard settings file has an invalid format.")
            return

        try:
            users = settings.get("allowed_user_ids", [])
            roles = settings.get("allowed_role_ids", [])
            keywords = settings.get("wake_keywords", ["bot", "joe biden"])
            if not isinstance(users, list) or not isinstance(roles, list):
                raise ValueError("Allowed IDs must be lists.")
            if not isinstance(keywords, list) or not keywords:
                raise ValueError("Wake words must be a non-empty list.")
            user_ids = self._parse_id_list(",".join(str(item) for item in users), "Allowed user IDs")
            role_ids = self._parse_id_list(",".join(str(item) for item in roles), "Allowed role IDs")
            normalized_keywords = tuple(
                " ".join(re.findall(r"[a-z0-9']+", str(item).casefold()))
                for item in keywords
                if re.findall(r"[a-z0-9']+", str(item).casefold())
            )
            if not normalized_keywords or len(normalized_keywords) > 10:
                raise ValueError("Wake words must contain 1 to 10 phrases.")
            if any(len(item) > 80 for item in normalized_keywords):
                raise ValueError("Wake phrases cannot exceed 80 characters.")
        except ValueError as error:
            self._log(f"Dashboard settings are invalid: {error}")
            return

        self.allowed_user_ids = user_ids
        self.allowed_role_ids = role_ids
        self.wake_keywords = normalized_keywords
        self.interaction_restricted = bool(settings.get("interaction_restricted", False))
        self.restrict_interactions_var.set(self.interaction_restricted)
        self.allowed_user_ids_entry.insert(0, ", ".join(map(str, sorted(user_ids))))
        self.allowed_role_ids_entry.insert(0, ", ".join(map(str, sorted(role_ids))))
        self.wake_keywords_entry.delete(0, "end")
        self.wake_keywords_entry.insert(0, ", ".join(normalized_keywords))

        provider = settings.get("ai_provider")
        if provider in ("Groq", "GitHub Copilot"):
            self.ai_provider_menu.set(provider)
            self.ai_provider = provider
        mode = settings.get("ai_mode")
        if mode in ("Manual replies", "AI auto-reply"):
            self.ai_mode_menu.set(mode)
            self.ai_replies_enabled = mode == "AI auto-reply"
        self.ai_mentions_only_var.set(bool(settings.get("ai_mention_only", True)))
        self.ai_mention_only = self.ai_mentions_only_var.get()
        self.speak_manual_var.set(bool(settings.get("speak_manual_replies", False)))
        self.speak_manual_replies = self.speak_manual_var.get()
        self.motion_enabled.set(bool(settings.get("motion_effects", True)))
        self.ai_status_label.configure(
            text=(
                f"AI auto-reply is on with {self.ai_provider}; "
                "mention-only is enabled."
                if self.ai_replies_enabled and self.ai_mention_only
                else f"AI auto-reply is on with {self.ai_provider}."
                if self.ai_replies_enabled
                else "Manual mode: AI auto-replies are off."
            )
        )

    def _save_app_settings(self) -> None:
        settings = {
            "interaction_restricted": self.interaction_restricted,
            "allowed_user_ids": sorted(self.allowed_user_ids),
            "allowed_role_ids": sorted(self.allowed_role_ids),
            "wake_keywords": list(self.wake_keywords),
            "ai_provider": self.ai_provider,
            "ai_mode": self.ai_mode_menu.get(),
            "ai_mention_only": self.ai_mention_only,
            "speak_manual_replies": self.speak_manual_replies,
            "motion_effects": self.motion_enabled.get(),
        }
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(
                json.dumps(settings, indent=2), encoding="utf-8"
            )
        except OSError as error:
            self._log(f"Could not save dashboard settings: {error}")

    def _interaction_user_allowed(
        self, user: discord.abc.User
    ) -> bool:
        if not self.interaction_restricted:
            return True
        if user.id in self.allowed_user_ids:
            return True
        return bool(
            any(
                role.id in self.allowed_role_ids
                for role in getattr(user, "roles", ())
            )
        )

    async def _interaction_allowed(self, interaction: discord.Interaction) -> bool:
        return self._interaction_user_allowed(interaction.user)

    def _wake_phrase(self, transcript: str) -> str | None:
        normalized = " ".join(re.findall(r"[a-z0-9']+", transcript.casefold()))
        for keyword in self.wake_keywords:
            phrase = " ".join(re.findall(r"[a-z0-9']+", keyword.casefold()))
            if not phrase:
                continue
            if (
                normalized == phrase
                or normalized.startswith(f"{phrase} ")
                or normalized.endswith(f" {phrase}")
            ):
                return keyword
        return None

    def _remove_wake_phrase(self, transcript: str, keyword: str) -> str:
        phrase = re.escape(keyword)
        transcript = re.sub(
            rf"^\s*{phrase}(?:\b[\s,.:;!?-]*)",
            "",
            transcript,
            count=1,
            flags=re.IGNORECASE,
        )
        transcript = re.sub(
            rf"(?:[\s,.:;!?-]*)(?<!\w){phrase}(?:[\s,.:;!?-]*)$",
            "",
            transcript,
            count=1,
            flags=re.IGNORECASE,
        )
        return transcript.strip() or "Please respond."

    @staticmethod
    def _build_poll(
        question: str, answers: list[str], duration_hours: int
    ) -> discord.Poll:
        question = question.strip()
        cleaned_answers = [answer.strip() for answer in answers]
        if not question or len(question) > 300:
            raise ValueError("Poll questions must be between 1 and 300 characters.")
        if not 2 <= len(cleaned_answers) <= 4:
            raise ValueError("Polls need between 2 and 4 answer options.")
        if any(not answer or len(answer) > 55 for answer in cleaned_answers):
            raise ValueError("Poll answers must be between 1 and 55 characters.")
        if len({answer.casefold() for answer in cleaned_answers}) != len(cleaned_answers):
            raise ValueError("Poll answer options must be unique.")
        if not 1 <= duration_hours <= 168:
            raise ValueError("Poll duration must be from 1 to 168 hours.")

        poll = discord.Poll(question, timedelta(hours=duration_hours))
        for answer in cleaned_answers:
            poll.add_answer(text=answer)
        return poll

    def _set_ai_provider(self, selection: str) -> None:
        self.ai_provider = selection
        self._save_app_settings()
        self._log(f"AI provider set to {selection}.")

    def _clear_ai_context(self) -> None:
        if self.selected_channel_id is not None:
            self.ai_history.pop(self.selected_channel_id, None)
            self._log("Cleared AI conversation context for the selected channel.")
        else:
            self.ai_history.clear()
            self._log("Cleared AI conversation context.")

    def _set_ai_mention_rule(self) -> None:
        self.ai_mention_only = self.ai_mentions_only_var.get()
        self._save_app_settings()

    def _set_manual_speech(self) -> None:
        self.speak_manual_replies = self.speak_manual_var.get()
        self._save_app_settings()

    def _forget_saved_token(self) -> None:
        try:
            self.credential_store.delete_password(
                self.CREDENTIAL_SERVICE, self.CREDENTIAL_USERNAME
            )
        except keyring.errors.PasswordDeleteError:
            self._log("No saved token was found to forget.")
        except keyring.errors.KeyringError as error:
            self._log(f"Could not remove the saved token: {error}")
            messagebox.showerror(
                "Could not forget token",
                "Windows Credential Manager could not remove the saved token.",
            )
            return
        self.token_entry.delete(0, "end")
        self._log("Removed the saved bot token from Windows Credential Manager.")

    def _save_token(self, token: str, remember: bool) -> None:
        try:
            if remember:
                self.credential_store.set_password(
                    self.CREDENTIAL_SERVICE, self.CREDENTIAL_USERNAME, token
                )
                self.root.after(0, self._log, "Saved bot token in Windows Credential Manager.")
            else:
                try:
                    self.credential_store.delete_password(
                        self.CREDENTIAL_SERVICE, self.CREDENTIAL_USERNAME
                    )
                except keyring.errors.PasswordDeleteError:
                    pass
                self.root.after(0, self._log, "Token is not being remembered.")
        except keyring.errors.KeyringError as error:
            self.root.after(
                0, self._log, f"Could not store the token in Windows Credential Manager: {error}"
            )

    def _start_bot(self) -> None:
        token = self.token_entry.get().strip()
        if not token:
            messagebox.showinfo("Bot token needed", "Paste your bot token to start the connection.")
            return
        if self.worker and self.worker.is_alive():
            return

        self.token_entry.delete(0, "end")
        self.groq_api_key = self._get_groq_key()
        self.connect_button.configure(text="Stop bot", command=self._stop_bot)
        self.status_value.configure(text="Connecting…", text_color="#f4c76b")
        self._log("Starting Discord connection.")
        self.worker = threading.Thread(
            target=self._bot_thread,
            args=(token, self.remember_token.get()),
            daemon=True,
        )
        self.worker.start()

    def _bot_thread(self, token: str, remember_token: bool) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True
        intents.voice_states = True
        client = discord.Client(intents=intents)
        tree = app_commands.CommandTree(client)
        self.ai_reply_lock = asyncio.Lock()
        self.voice_speech_lock = asyncio.Lock()

        @tree.command(name="ping", description="Check the bot gateway latency.")
        @app_commands.check(self._interaction_allowed)
        async def ping(interaction: discord.Interaction) -> None:
            latency_ms = round(client.latency * 1000)
            await interaction.response.send_message(f"Pong! Gateway latency: {latency_ms} ms.")

        @tree.command(name="help", description="Show the bot's commands and features.")
        @app_commands.check(self._interaction_allowed)
        async def help_command(interaction: discord.Interaction) -> None:
            embed = discord.Embed(
                title="Orbit bot help",
                description=(
                    "`/ping` checks latency · `/serverinfo` shows server details · "
                    "`/userinfo` looks up a member · `/avatar` shows a user's avatar\n"
                    "`/ask` asks the selected AI provider privately\n"
                    "`/poll` creates a native Discord poll (2–4 options, up to 7 days)\n"
                    "`/kick` and `/ban` require matching moderator permissions"
                ),
                color=discord.Colour.blurple(),
            )
            embed.add_field(
                name="Voice",
                value=(
                    "The dashboard can join voice and speak. Use **Start listening** "
                    "to enable wake-word detection for allowed speakers. Listening is off "
                    "by default; short audio clips are sent to Groq for transcription."
                ),
                inline=False,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @tree.command(name="avatar", description="Show a user's Discord avatar.")
        @app_commands.check(self._interaction_allowed)
        @app_commands.describe(user="User whose avatar to show; defaults to you")
        async def avatar(
            interaction: discord.Interaction, user: discord.User | None = None
        ) -> None:
            target = user or interaction.user
            embed = discord.Embed(
                title=f"{target.name}'s avatar",
                url=target.display_avatar.url,
                color=discord.Colour.blurple(),
            )
            embed.set_image(url=target.display_avatar.url)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @tree.command(name="poll", description="Create a native Discord poll.")
        @app_commands.check(self._interaction_allowed)
        @app_commands.describe(
            question="Poll question",
            option_one="First answer",
            option_two="Second answer",
            option_three="Optional third answer",
            option_four="Optional fourth answer",
            duration_hours="How long voting stays open (1–168 hours)",
        )
        async def poll_command(
            interaction: discord.Interaction,
            question: str,
            option_one: str,
            option_two: str,
            option_three: str | None = None,
            option_four: str | None = None,
            duration_hours: app_commands.Range[int, 1, 168] = 24,
        ) -> None:
            try:
                poll = self._build_poll(
                    question,
                    [
                        option for option in
                        (option_one, option_two, option_three, option_four)
                        if option is not None
                    ],
                    duration_hours,
                )
                await interaction.response.send_message(
                    poll=poll,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except ValueError as error:
                if not interaction.response.is_done():
                    await interaction.response.send_message(str(error), ephemeral=True)
            except discord.Forbidden:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "I can't post a poll here. Check my Send Messages and Send Polls permissions.",
                        ephemeral=True,
                    )
            except discord.HTTPException as error:
                self.root.after(
                    0, self._log, f"Could not create poll ({type(error).__name__}): {error}"
                )
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Discord couldn't create that poll. Check the question, answers, and channel permissions.",
                        ephemeral=True,
                    )

        @tree.command(name="ask", description="Ask the selected AI provider privately.")
        @app_commands.check(self._interaction_allowed)
        @app_commands.describe(question="Your question (sent to the selected AI provider)")
        async def ask(interaction: discord.Interaction, question: str) -> None:
            question = question.strip()
            if not question:
                await interaction.response.send_message(
                    "Enter a question first.", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                if self.ai_provider == "Groq":
                    answer = await self._groq_chat_reply([], question[:1200])
                else:
                    answer = await self._copilot_chat_reply([], question[:1200])
                answer = answer.strip()[:1900]
                if not answer:
                    raise RuntimeError("The AI provider returned an empty response.")
                await interaction.edit_original_response(
                    content=answer,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception as error:
                self.root.after(
                    0, self._log,
                    f"Private AI question failed ({type(error).__name__}): {error}",
                )
                await interaction.edit_original_response(
                    content="I couldn't get an answer from the selected provider. Check the dashboard log and provider settings."
                )

        @tree.command(name="serverinfo", description="Show basic information about this server.")
        @app_commands.check(self._interaction_allowed)
        async def serverinfo(interaction: discord.Interaction) -> None:
            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message(
                    "This command only works in a server.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                f"**{guild.name}** — {guild.member_count or 'Unknown'} members, "
                f"{len(guild.channels)} channels."
            )

        @tree.command(name="userinfo", description="Show information about a server member.")
        @app_commands.check(self._interaction_allowed)
        @app_commands.describe(member="Member to look up; defaults to you")
        async def userinfo(
            interaction: discord.Interaction, member: discord.Member | None = None
        ) -> None:
            target = member or interaction.user
            embed = discord.Embed(
                title=target.display_name,
                color=target.color if target.color.value else discord.Colour.blurple(),
            )
            embed.set_thumbnail(url=target.display_avatar.url)
            embed.add_field(name="User ID", value=str(target.id), inline=True)
            embed.add_field(
                name="Account created",
                value=discord.utils.format_dt(target.created_at, style="D"),
                inline=True,
            )
            if isinstance(target, discord.Member) and target.joined_at:
                embed.add_field(
                    name="Joined server",
                    value=discord.utils.format_dt(target.joined_at, style="D"),
                    inline=True,
                )
                embed.add_field(name="Top role", value=target.top_role.mention, inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @tree.command(name="kick", description="Kick a member from this server.")
        @app_commands.check(self._interaction_allowed)
        @app_commands.default_permissions(kick_members=True)
        @app_commands.describe(member="Member to kick", reason="Reason for the kick")
        async def kick(
            interaction: discord.Interaction,
            member: discord.Member,
            reason: str = "No reason provided",
        ) -> None:
            guild = interaction.guild
            moderator = interaction.user
            if guild is None or not isinstance(moderator, discord.Member):
                await interaction.response.send_message(
                    "This command can only be used by a server moderator.",
                    ephemeral=True,
                )
                return
            bot_member = guild.me
            if (
                not moderator.guild_permissions.kick_members
                or bot_member is None
                or not bot_member.guild_permissions.kick_members
            ):
                await interaction.response.send_message(
                    "You and the bot both need Kick Members permission.",
                    ephemeral=True,
                )
                return
            if member == guild.owner or (
                moderator.id != guild.owner_id and member.top_role >= moderator.top_role
            ):
                await interaction.response.send_message(
                    "You cannot kick the server owner or a member at/above your top role.",
                    ephemeral=True,
                )
                return
            if member.top_role >= bot_member.top_role:
                await interaction.response.send_message(
                    "The bot cannot kick a member at/above its top role.",
                    ephemeral=True,
                )
                return
            reason = reason.strip()[:400] or "No reason provided"
            await interaction.response.defer(ephemeral=True)
            try:
                await member.kick(reason=f"{reason} (by {moderator})")
            except discord.HTTPException:
                await interaction.edit_original_response(
                    content="Discord could not complete the kick. Check the bot's permissions and role order."
                )
            else:
                await interaction.edit_original_response(
                    content=f"Kicked **{member}**. Reason: {reason[:500]}",
                    allowed_mentions=discord.AllowedMentions.none(),
                )

        @tree.command(name="ban", description="Ban a member from this server.")
        @app_commands.check(self._interaction_allowed)
        @app_commands.default_permissions(ban_members=True)
        @app_commands.describe(member="Member to ban", reason="Reason for the ban")
        async def ban_command(
            interaction: discord.Interaction,
            member: discord.Member,
            reason: str = "No reason provided",
        ) -> None:
            guild = interaction.guild
            moderator = interaction.user
            if guild is None or not isinstance(moderator, discord.Member):
                await interaction.response.send_message(
                    "This command can only be used by a server moderator.",
                    ephemeral=True,
                )
                return
            bot_member = guild.me
            if (
                not moderator.guild_permissions.ban_members
                or bot_member is None
                or not bot_member.guild_permissions.ban_members
            ):
                await interaction.response.send_message(
                    "You and the bot both need Ban Members permission.",
                    ephemeral=True,
                )
                return
            if member == guild.owner or (
                moderator.id != guild.owner_id and member.top_role >= moderator.top_role
            ):
                await interaction.response.send_message(
                    "You cannot ban the server owner or a member at/above your top role.",
                    ephemeral=True,
                )
                return
            if member.top_role >= bot_member.top_role:
                await interaction.response.send_message(
                    "The bot cannot ban a member at/above its top role.",
                    ephemeral=True,
                )
                return
            reason = reason.strip()[:400] or "No reason provided"
            await interaction.response.defer(ephemeral=True)
            try:
                await member.ban(reason=f"{reason} (by {moderator})")
            except discord.HTTPException:
                await interaction.edit_original_response(
                    content="Discord could not complete the ban. Check the bot's permissions and role order."
                )
            else:
                await interaction.edit_original_response(
                    content=                    f"Banned **{member}**. Reason: {reason[:500]}",
                    allowed_mentions=discord.AllowedMentions.none(),
                )

        @tree.error
        async def on_app_command_error(
                interaction: discord.Interaction, error: app_commands.AppCommandError
        ) -> None:
                if isinstance(error, app_commands.CheckFailure):
                    message = "This bot is restricted to approved users and roles."
                    if not interaction.response.is_done():
                        await interaction.response.send_message(message, ephemeral=True)
                    return
                self.root.after(0, self._log, f"Slash command failed: {error}")

        @client.event
        async def on_message(message: discord.Message) -> None:
            await self._feature_receive_dm_message(message)
            if message.guild is None or message.author.bot:
                return
            self.activity_counters["messages"] += 1
            self.root.after(0, self._feature_refresh_activity)
            if (
                message.guild.id == self.selected_guild_id
                and message.channel.id == self.selected_channel_id
            ):
                self.root.after(0, self._add_feed_message, message)
            if self.quiet_mode:
                return
            if not self.ai_replies_enabled or message.channel.id != self.selected_channel_id:
                return
            if not self._interaction_user_allowed(message.author):
                return
            if self.ai_mention_only and (
                client.user is None or client.user not in message.mentions
            ):
                return
            if time.monotonic() - self.ai_last_reply_at.get(message.channel.id, 0) < 8:
                return
            self.ai_last_reply_at[message.channel.id] = time.monotonic()
            await self._respond_to_message(message, client.user.id if client.user else 0)

        self.loop = loop
        self.client = client
        self.command_tree = tree
                                                                            
        @client.event
        async def on_ready() -> None:
            self.started_at = time.monotonic()
            self.root.after(0, self._connected, client.user, len(client.guilds))
            self._save_token(token, remember_token)
            try:
                await tree.sync()
                self.root.after(0, self._log, "Connected. Slash commands synced.")
            except discord.HTTPException:
                self.root.after(0, self._log, "Connected, but Discord could not sync slash commands.")

        @client.event
        async def on_member_join(member: discord.Member) -> None:
            await self._feature_send_welcome(member)

        try:
            loop.run_until_complete(client.start(token))
        except discord.LoginFailure:
            self.root.after(0, self._connection_failed, "Discord rejected the bot token.")
        except discord.PrivilegedIntentsRequired:
            self.root.after(
                0, self._connection_failed,
                "Discord requires privileged intents. Enable Server Members and Message Content "
                "intents for this bot in the Developer Portal.",
            )
        except Exception as error:
            self.root.after(
                0, self._connection_failed,
                f"Connection stopped ({type(error).__name__}). Check network and bot settings.",
            )
        finally:
            try:
                loop.run_until_complete(self._shutdown_client(client))
            except Exception as error:
                if not self.closing:
                    self.root.after(
                        0, self._log,
                        f"Discord shutdown issue ({type(error).__name__}): {error}",
                    )
            loop.close()
            self.client = None
            self.loop = None
            self.voice_client = None
            self.started_at = None
            if not self.closing:
                self.root.after(0, self._disconnected)

    def _connected(self, user: discord.ClientUser | None, guild_count: int) -> None:
        if self.closing:
            return
        self.gateway_latency_samples.clear()
        self.status_value.configure(text="Online", text_color="#61d995")
        self.servers_value.configure(text=str(guild_count))
        self.account_value.configure(text=str(user) if user else "Connected")
        self._refresh_gateway_signal()
        self._refresh_guilds()
        self._feature_refresh_commands()
        self._feature_refresh_activity()
        self._log("Bot connected to Discord.")

    def _connection_failed(self, message: str) -> None:
        if not self.closing:
            self._log(message)
            self.status_value.configure(text="Connection issue", text_color="#f07878")
            if hasattr(self, "_feature_refresh_activity"):
                self._feature_refresh_activity()
            if hasattr(self, "_feature_refresh_commands"):
                self._feature_refresh_commands()

    def _stop_bot(self) -> None:
        if self.loop and self.client and self.loop.is_running():
            self.status_value.configure(text="Stopping…", text_color="#f4c76b")
            asyncio.run_coroutine_threadsafe(self._shutdown_client(self.client), self.loop)

    async def _shutdown_client(self, client: discord.Client) -> None:
        try:
            for voice in tuple(client.voice_clients):
                if (
                    isinstance(voice, voice_recv.VoiceRecvClient)
                    and voice.is_listening()
                ):
                    voice.stop_listening()
                if voice.is_connected():
                    await voice.disconnect(force=True)
        finally:
            if not client.is_closed():
                await client.close()
            self.voice_client = None
            self.voice_wake_sink = None
            self.voice_listening = False

    def _disconnected(self) -> None:
        self.status_value.configure(text="Offline", text_color="#929db8")
        self.servers_value.configure(text="—")
        self.account_value.configure(text="Not connected")
        self.uptime_value.configure(text="—")
        self._voice_state_changed(None)
        self.connect_button.configure(text="Start bot", command=self._start_bot)
        self._log("Bot disconnected.")
        self.command_tree = None
        self._feature_refresh_commands()
        if hasattr(self, "_feature_refresh_activity"):
            self._feature_refresh_activity()

    def _apply_presence(self) -> None:
        if not self.loop or not self.client or not self.loop.is_running():
            messagebox.showinfo("Bot is offline", "Start the bot before changing its presence.")
            return

        status_name = self.presence_menu.get()
        status = {
            "Online": discord.Status.online,
            "Idle": discord.Status.idle,
            "Do Not Disturb": discord.Status.dnd,
            "Invisible": discord.Status.invisible,
        }[status_name]
        activity_text = self.activity_entry.get().strip()
        activity = discord.Game(name=activity_text) if activity_text else None
        future = asyncio.run_coroutine_threadsafe(
            self.client.change_presence(status=status, activity=activity), self.loop
        )
        future.add_done_callback(
            lambda result: self.root.after(
                0, self._log,
                "Presence updated." if result.exception() is None else "Could not update presence.",
            )
        )

    def _selected_guild(self) -> discord.Guild | None:
        if not self.client or not self.client.is_ready():
            messagebox.showinfo("Bot is offline", "Connect the bot before managing a server.")
            return None
        guild_id = self.guild_options.get(self.guild_menu.get())
        guild = self.client.get_guild(guild_id) if guild_id is not None else None
        if guild is None:
            messagebox.showinfo("Server not selected", "Select a server the bot has joined.")
        return guild

    def _refresh_guilds(self) -> None:
        if not self.client or not self.client.is_ready():
            return
        self.guild_options = {
            f"{guild.name} ({guild.id})": guild.id
            for guild in sorted(self.client.guilds, key=lambda item: item.name.casefold())
        }
        options = list(self.guild_options) or ["No servers available"]
        self.guild_menu.configure(values=options)
        self.guild_menu.set(options[0])
        self._guild_changed(options[0])

    def _guild_changed(self, selection: str) -> None:
        if not hasattr(self, "role_menu"):
            return
        guild_id = self.guild_options.get(selection)
        guild = self.client.get_guild(guild_id) if self.client and guild_id else None
        self.selected_guild_id = guild.id if guild else None
        self.role_options = {}
        self.channel_options = {}
        self.voice_channel_options = {}
        if guild:
            self.role_options = {
                f"{role.name} ({role.id})": role.id
                for role in sorted(guild.roles, key=lambda item: item.position, reverse=True)
                if role != guild.default_role
            }
            if guild.me:
                self.channel_options = {
                    f"#{channel.name} ({channel.id})": channel.id
                    for channel in guild.text_channels
                    if channel.permissions_for(guild.me).view_channel
                }
                self.voice_channel_options = {
                    f"🔊 {channel.name} ({channel.id})": channel.id
                    for channel in guild.voice_channels
                    if channel.permissions_for(guild.me).connect
                    and channel.permissions_for(guild.me).speak
                }
        options = list(self.role_options) or ["No manageable roles available"]
        self.role_menu.configure(values=options)
        self.role_menu.set(options[0])
        channel_options = list(self.channel_options) or ["No viewable text channels"]
        self.channel_menu.configure(values=channel_options)
        self.channel_menu.set(channel_options[0])
        self._channel_changed(channel_options[0])
        voice_options = list(self.voice_channel_options) or ["No joinable voice channels"]
        self.voice_channel_menu.configure(values=voice_options)
        self.voice_channel_menu.set(voice_options[0])

    def _channel_changed(self, selection: str) -> None:
        self.selected_channel_id = self.channel_options.get(selection)
        self.feed_request_id += 1
        request_id = self.feed_request_id
        self._clear_feed(
            "Loading recent messages…" if self.selected_channel_id
            else "Select a server with viewable text channels."
        )
        if not self.selected_channel_id:
            self.feed_status_label.configure(
                text="No viewable text channels. Check the bot's View Channel permission."
            )
            return
        if not self.client or not self.loop or not self.loop.is_running():
            self.feed_status_label.configure(text="Connect the bot to load channel messages.")
            return

        channel_id = self.selected_channel_id
        channel = self.client.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            self.feed_status_label.configure(text="Selected text channel is not available.")
            return
        self.feed_status_label.configure(
            text="Loading recent messages… New messages will appear while connected."
        )
        future = asyncio.run_coroutine_threadsafe(
            self._load_recent_messages(channel), self.loop
        )

        def loaded(result: Future[list[discord.Message]]) -> None:
            try:
                messages = result.result()
            except discord.Forbidden:
                error = "Cannot read this channel. Grant the bot View Channel and Read Message History."
                self.root.after(0, self._feed_load_failed, request_id, error)
            except discord.HTTPException as error:
                self.root.after(
                    0, self._feed_load_failed, request_id,
                    f"Discord could not load channel history: {error}",
                )
            except Exception as error:
                self.root.after(
                    0, self._feed_load_failed, request_id,
                    f"Could not load messages ({type(error).__name__}): {error}",
                )
            else:
                self.root.after(0, self._feed_history_loaded, request_id, messages)

        future.add_done_callback(loaded)

    def _join_voice(self) -> None:
        guild = self._selected_guild()
        if guild is None:
            return
        channel_id = self.voice_channel_options.get(self.voice_channel_menu.get())
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.VoiceChannel):
            messagebox.showinfo("Select a voice channel", "Choose a voice channel the bot can join.")
            return

        async def join() -> None:
            bot_member = self._bot_member(guild)
            permissions = channel.permissions_for(bot_member)
            if not permissions.connect or not permissions.speak:
                raise PermissionError("The bot needs Connect and Speak permissions in that channel.")
            voice = guild.voice_client
            if voice and voice.is_connected():
                if not isinstance(voice, voice_recv.VoiceRecvClient):
                    await voice.disconnect(force=True)
                    voice = None
            if voice and voice.is_connected():
                await voice.move_to(channel)
            else:
                voice = await channel.connect(
                    cls=voice_recv.VoiceRecvClient,
                    self_deaf=False,
                    self_mute=False,
                )
            self.voice_client = voice
            self.voice_self_muted = False
            self.voice_self_deafened = False
            self.root.after(0, self._voice_state_changed, channel.name)

        self._run_guild_action(join(), f"Joining voice channel {channel.name}.")

    def _toggle_voice_listening(self) -> None:
        voice = self.voice_client
        if self.quiet_mode and not self.voice_listening:
            self._voice_issue("Quiet mode is on; turn it off before starting voice listening.")
            return
        if not voice or not voice.is_connected():
            self._voice_issue("Join a voice channel before starting voice listening.")
            return
        if self.voice_listening:
            async def stop() -> None:
                if isinstance(voice, voice_recv.VoiceRecvClient):
                    voice.stop_listening()
                self.voice_wake_sink = None
                self.voice_listening = False
                self.root.after(0, self._voice_state_changed, voice.channel.name)

            self._run_guild_action(stop(), "Voice listening stopped.")
            return
        if not self.interaction_restricted or not (
            self.allowed_user_ids or self.allowed_role_ids
        ):
            self._voice_issue(
                "For privacy, apply interaction access with the restriction enabled and "
                "at least one allowed user ID or role ID before listening."
            )
            return
        if not self.groq_api_key:
            self._voice_issue("Voice wake-word detection needs a saved Groq API key.")
            return
        if not isinstance(voice, voice_recv.VoiceRecvClient):
            self._voice_issue(
                "This voice connection cannot receive audio. Leave voice, rejoin, and try again."
            )
            return
        if not messagebox.askyesno(
            "Start voice listening",
            "While listening is on, short audio clips from allowed users in this voice "
            "channel are sent to Groq to check for your wake words. Audio and transcripts "
            "are not saved to disk. Groq transcription charges apply, with a 10-second "
            "minimum charge per clip, and each speaker is limited to one clip per 10 seconds. "
            "Start listening?",
        ):
            return

        async def start() -> None:
            if not discord.opus.is_loaded() and not discord.opus._load_default():
                raise discord.ClientException(
                    "Could not load Opus. Incoming Discord voice audio cannot be decoded."
                )
            sink = self._create_voice_wake_sink()
            voice.listen(sink)
            self.voice_wake_sink = sink
            self.voice_listening = True
            self.root.after(0, self._voice_state_changed, voice.channel.name)

        self._run_guild_action(start(), "Listening for configured wake words.")

    def _create_voice_wake_sink(self) -> voice_recv.AudioSink:
        dashboard = self

        class WakeWordSink(voice_recv.AudioSink):
            MAX_AUDIO_BYTES = 48_000 * 2 * 2 * 8

            def __init__(self) -> None:
                super().__init__()
                self.lock = threading.Lock()
                self.buffers: dict[int, list[bytes]] = {}
                self.active_speakers: set[int] = set()
                self.last_speech: dict[int, float] = {}
                self.members: dict[int, discord.Member | discord.User] = {}
                self.last_submission: dict[int, float] = {}

            def wants_opus(self) -> bool:
                return False

            def write(
                self, user: discord.Member | discord.User | None,
                data: voice_recv.VoiceData,
            ) -> None:
                if user is None or not dashboard._interaction_user_allowed(user):
                    return
                pcm = data.pcm
                if len(pcm) < 2:
                    return
                usable_pcm = pcm[: len(pcm) - len(pcm) % 2]
                samples = struct.iter_unpack("<h", usable_pcm)
                squared_total = sum(sample[0] * sample[0] for sample in samples)
                sample_count = len(usable_pcm) // 2
                if not sample_count:
                    return
                is_speech = squared_total / sample_count >= 350 * 350
                now = time.monotonic()

                with self.lock:
                    user_id = user.id
                    if is_speech:
                        self.last_speech[user_id] = now
                        self.members[user_id] = user
                    if user_id in self.buffers:
                        self.buffers[user_id].append(usable_pcm)
                        buffered_bytes = sum(map(len, self.buffers[user_id]))
                        if (
                            (
                                not is_speech
                                and now - self.last_speech.get(user_id, now) >= 0.7
                            )
                            or buffered_bytes >= self.MAX_AUDIO_BYTES
                        ):
                            self._submit_locked(user_id, now)
                    elif is_speech or user_id in self.active_speakers:
                        self.buffers[user_id] = [usable_pcm]

            @voice_recv.AudioSink.listener()
            def on_voice_member_speaking_start(
                self, member: discord.Member, ssrc: int | None
            ) -> None:
                del ssrc
                if not dashboard._interaction_user_allowed(member):
                    return
                with self.lock:
                    self.active_speakers.add(member.id)
                    self.members[member.id] = member
                    self.last_speech[member.id] = time.monotonic()
                    self.buffers.setdefault(member.id, [])

            @voice_recv.AudioSink.listener()
            def on_voice_member_speaking_stop(
                self, member: discord.Member, ssrc: int | None
            ) -> None:
                del ssrc
                with self.lock:
                    self.active_speakers.discard(member.id)
                    self._submit_locked(member.id, time.monotonic())

            def _submit_locked(self, user_id: int, now: float) -> None:
                chunks = self.buffers.pop(user_id, [])
                self.last_speech.pop(user_id, None)
                member = self.members.get(user_id)
                if not member or not chunks:
                    return
                if now - self.last_submission.get(
                    user_id, 0
                ) < dashboard.VOICE_TRANSCRIPTION_COOLDOWN:
                    return
                pcm = b"".join(chunks)
                if len(pcm) < 48_000 * 2 * 2 // 10:
                    return
                self.last_submission[user_id] = now
                loop = dashboard.loop
                if loop and loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(
                        dashboard._process_voice_utterance(member, pcm), loop
                    )
                    future.add_done_callback(dashboard._voice_processing_finished)

            def cleanup(self) -> None:
                with self.lock:
                    self.buffers.clear()
                    self.active_speakers.clear()
                    self.last_speech.clear()
                    self.members.clear()

        return WakeWordSink()

    def _voice_processing_finished(self, result: Future[None]) -> None:
        try:
            error = result.exception()
        except Exception as error:
            self.root.after(0, self._voice_issue, f"Voice processing failed: {error}")
            return
        if error:
            self.root.after(
                0, self._voice_issue,
                f"Voice processing failed ({type(error).__name__}): {error}",
            )

    async def _process_voice_utterance(
        self, speaker: discord.Member | discord.User, pcm: bytes
    ) -> None:
        if (
            self.quiet_mode
            or not self.voice_listening
            or not self._interaction_user_allowed(speaker)
            or speaker.id in self.voice_processing_users
        ):
            return
        self.voice_processing_users.add(speaker.id)
        try:
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(48_000)
                output.writeframes(pcm)

            response = await asyncio.to_thread(
                lambda: Groq(api_key=self.groq_api_key).audio.transcriptions.create(
                    file=("voice.wav", wav_buffer.getvalue(), "audio/wav"),
                    model=self.GROQ_TRANSCRIPTION_MODEL,
                    language="en",
                    response_format="text",
                )
            )
            transcript = response if isinstance(response, str) else response.text
            transcript = transcript.strip()
            if not self.voice_listening:
                return
            wake_phrase = self._wake_phrase(transcript)
            if not wake_phrase:
                return

            voice = self.voice_client
            if (
                not voice
                or not voice.is_connected()
                or voice.guild.get_member(speaker.id) is None
            ):
                return
            prompt = self._remove_wake_phrase(transcript, wake_phrase)
            if self.ai_provider == "Groq":
                answer = await self._groq_chat_reply(
                    [], f"{speaker.display_name}: {prompt}"
                )
            else:
                answer = await self._copilot_chat_reply(
                    [], f"{speaker.display_name}: {prompt}"
                )
            answer = answer.strip()
            if not answer:
                raise RuntimeError("The AI provider returned an empty voice reply.")
            if self.voice_listening and self._interaction_user_allowed(speaker):
                spoke = await self._speak_text(answer)
                if spoke:
                    self.activity_counters["voice_replies"] += 1
                    self.root.after(0, self._feature_refresh_activity)
        except Exception as error:
            error_text = str(error)
            if self.groq_api_key:
                error_text = error_text.replace(self.groq_api_key, "[redacted]")
            self._voice_issue(
                f"Voice processing failed ({type(error).__name__}): {error_text}"
            )
        finally:
            self.voice_processing_users.discard(speaker.id)

    def _test_voice(self) -> None:
        voice = self.voice_client
        if not voice or not voice.is_connected():
            self._voice_issue("Join a voice channel before testing speech.")
            return

        async def test() -> None:
            if not await self._speak_text(
                "Voice test. The bot can speak in this channel."
            ):
                raise RuntimeError(
                    "Voice test could not play audio. See the Voice channel status for details."
                )

        self._run_guild_action(test(), "Voice test finished.")

    def _toggle_voice_mute(self) -> None:
        voice = self.voice_client
        if not voice or not voice.is_connected():
            messagebox.showinfo("Not in voice", "Join a voice channel first.")
            return
        mute = not self.voice_self_muted

        async def toggle() -> None:
            await voice.guild.change_voice_state(
                channel=voice.channel,
                self_mute=mute,
                self_deaf=self.voice_self_deafened,
            )
            self.voice_self_muted = mute
            if mute and voice.is_playing():
                voice.stop()
            self.root.after(0, self._set_voice_control_labels)
            self.root.after(
                0, self._log, "Bot voice muted." if mute else "Bot voice unmuted."
            )

        self._run_guild_action(toggle(), "Updated voice mute state.")

    def _toggle_voice_deafen(self) -> None:
        voice = self.voice_client
        if not voice or not voice.is_connected():
            messagebox.showinfo("Not in voice", "Join a voice channel first.")
            return
        deafen = not self.voice_self_deafened

        async def toggle() -> None:
            await voice.guild.change_voice_state(
                channel=voice.channel,
                self_mute=self.voice_self_muted,
                self_deaf=deafen,
            )
            self.voice_self_deafened = deafen
            if deafen and self.voice_listening:
                if isinstance(voice, voice_recv.VoiceRecvClient):
                    voice.stop_listening()
                self.voice_wake_sink = None
                self.voice_listening = False
            self.root.after(0, self._set_voice_control_labels)
            self.root.after(0, self._voice_state_changed, voice.channel.name)
            self.root.after(
                0, self._log, "Bot voice deafened." if deafen else "Bot voice undeafened."
            )

        self._run_guild_action(toggle(), "Updated voice deafen state.")

    def _leave_voice(self) -> None:
        voice = self.voice_client
        if not voice or not voice.is_connected():
            self._voice_state_changed(None)
            return

        async def leave() -> None:
            if (
                isinstance(voice, voice_recv.VoiceRecvClient)
                and voice.is_listening()
            ):
                voice.stop_listening()
            await voice.disconnect(force=True)
            self.voice_client = None
            self.voice_self_muted = False
            self.voice_self_deafened = False
            self.voice_wake_sink = None
            self.voice_listening = False
            self.root.after(0, self._voice_state_changed, None)

        self._run_guild_action(leave(), "Left the voice channel.")

    def _voice_state_changed(self, channel_name: str | None) -> None:
        if channel_name:
            status = (
                f"Listening in {channel_name}; transcription limited to once per speaker "
                f"every {self.VOICE_TRANSCRIPTION_COOLDOWN}s"
                if self.voice_listening else f"Connected to {channel_name}"
            )
            self.voice_status_label.configure(text=status)
        else:
            self.voice_status_label.configure(text="Not connected to voice")
            self.voice_self_muted = False
            self.voice_self_deafened = False
            self.voice_listening = False
            self.voice_wake_sink = None
        self.voice_listen_button.configure(
            text="Stop listening" if self.voice_listening else "Start listening"
        )
        self._set_voice_control_labels()

    def _set_voice_control_labels(self) -> None:
        self.voice_mute_button.configure(
            text="Unmute bot" if self.voice_self_muted else "Mute bot"
        )
        self.voice_deafen_button.configure(
            text="Undeafen bot" if self.voice_self_deafened else "Deafen bot"
        )

    async def _respond_to_message(
        self, message: discord.Message, bot_user_id: int
    ) -> None:
        lock = self.ai_reply_lock
        if lock is None:
            return
        async with lock:
            content = message.clean_content.strip()
            content = content.replace(f"<@{bot_user_id}>", "").replace(
                f"<@!{bot_user_id}>", ""
            )
            if self.client and self.client.user:
                content = content.replace(f"@{self.client.user.name}", "")
            content = content.strip()
            if not content:
                content = "Please respond to my message."
            history = self.ai_history.setdefault(message.channel.id, [])
            prompt = f"{message.author.display_name}: {content[:1200]}"
            try:
                if self.ai_provider == "Groq":
                    answer = await self._groq_chat_reply(history, prompt)
                else:
                    answer = await self._copilot_chat_reply(history, prompt)
                answer = answer.strip()[:1800]
                if not answer:
                    raise RuntimeError("The AI provider returned an empty response.")
                if (
                    not self.ai_replies_enabled
                    or self.selected_channel_id != message.channel.id
                ):
                    return
                sent = await message.channel.send(
                    answer,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception as error:
                self.root.after(
                    0, self._log,
                    f"AI reply failed ({type(error).__name__}): {error}",
                )
                self.root.after(
                    0, self._set_ai_status,
                    f"AI reply failed: {type(error).__name__}. Check provider settings and logs.",
                )
                return

            history.extend(
                [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": answer},
                ]
            )
            del history[:-12]
            self.activity_counters["ai_replies"] += 1
            self.root.after(0, self._feature_refresh_activity)
            self.root.after(0, self._add_feed_message, sent)
            self.root.after(
                0, self._log,
                f"{self.ai_provider} replied in #{getattr(message.channel, 'name', 'channel')}.",
            )
            voice = self.voice_client
            if voice and voice.is_connected() and voice.guild.id == message.guild.id:
                spoke = await self._speak_text(answer)
                if spoke:
                    self.activity_counters["voice_replies"] += 1
                    self.root.after(0, self._feature_refresh_activity)

    async def _groq_chat_reply(
        self, history: list[dict[str, str]], prompt: str,
        api_key: str | None = None,
    ) -> str:
        credential = api_key or self.groq_api_key
        if not credential:
            raise RuntimeError(
                "Save a Groq API key in the AI conversation panel for Groq chat and speech."
            )
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are a friendly Discord server bot. Keep replies concise, respectful, "
                    "and suitable for a public server. Do not claim to be a human, reveal secrets, "
                    "or perform moderation actions. You only provide conversational text."
                ),
            },
            *history,
            {"role": "user", "content": prompt},
        ]
        completion = await asyncio.to_thread(
            lambda: Groq(api_key=credential).chat.completions.create(
                model=self.GROQ_CHAT_MODEL,
                messages=messages,
                max_completion_tokens=250,
                temperature=0.7,
            )
        )
        return completion.choices[0].message.content or ""

    async def _copilot_chat_reply(
        self, history: list[dict[str, str]], prompt: str
    ) -> str:
        from copilot import CopilotClient
        from copilot.session import SystemMessageReplaceConfig
        from copilot.session_events import AssistantMessageData

        conversation = "\n".join(
            f"{item['role']}: {item['content']}" for item in history[-8:]
        )
        full_prompt = (
            f"Recent conversation:\n{conversation}\n\nNew message:\n{prompt}"
            if conversation else prompt
        )
        async with CopilotClient() as copilot_client:
            async with await copilot_client.create_session(
                available_tools=[],
                tools=[],
                enable_mcp_apps=False,
                enable_skills=False,
                enable_file_hooks=False,
                system_message=SystemMessageReplaceConfig(
                    mode="replace",
                    content=(
                        "You are a friendly Discord server bot. Keep replies concise and "
                        "respectful. Do not claim to be human, reveal secrets, perform moderation "
                        "actions, or use tools. Only produce conversational text."
                    ),
                ),
            ) as session:
                response = await session.send_and_wait(full_prompt, timeout=50)
        if response and isinstance(response.data, AssistantMessageData):
            return response.data.content
        return ""

    async def _speak_text(self, text: str) -> bool:
        if self.quiet_mode:
            return False
        voice = self.voice_client
        if not voice or not voice.is_connected():
            self._voice_issue("Voice output unavailable: the bot is not connected to a voice channel.")
            return False
        if self.voice_self_muted:
            self._voice_issue("Voice output is muted. Click Unmute bot and try again.")
            return False
        if not self.groq_api_key:
            self._voice_issue("Voice output needs a saved Groq API key.")
            return False
        lock = self.voice_speech_lock
        if lock is None:
            self._voice_issue("Voice output is not initialized. Stop and restart the bot, then try again.")
            return False
        async with lock:
            if not voice.is_connected():
                self._voice_issue("Voice output stopped because the bot disconnected.")
                return False
            if self.voice_self_muted:
                self._voice_issue("Voice output stopped because the bot is muted.")
                return False
            try:
                response = await asyncio.to_thread(
                    lambda: Groq(api_key=self.groq_api_key).audio.speech.create(
                        model=self.GROQ_SPEECH_MODEL,
                        voice=self.GROQ_SPEECH_VOICE,
                        input=text[:200],
                        response_format="wav",
                    )
                )
                audio_data = await asyncio.to_thread(response.read)
                source = discord.FFmpegOpusAudio(
                    io.BytesIO(audio_data),
                    pipe=True,
                    executable=imageio_ffmpeg.get_ffmpeg_exe(),
                )
                playback_done = asyncio.Event()
                playback_errors: list[Exception] = []

                def playback_finished(error: Exception | None) -> None:
                    loop = self.loop
                    if loop and loop.is_running():
                        def complete_playback() -> None:
                            if error:
                                playback_errors.append(error)
                            playback_done.set()

                        loop.call_soon_threadsafe(complete_playback)

                voice.play(
                    source,
                    after=playback_finished,
                )
                await asyncio.wait_for(playback_done.wait(), timeout=90)
                if playback_errors:
                    raise RuntimeError(f"Voice playback failed: {playback_errors[0]}")
                return True
            except Exception as error:
                error_text = str(error).replace(self.groq_api_key, "[redacted]")
                self._voice_issue(
                    f"Voice speech failed ({type(error).__name__}): {error_text}"
                )
                return False

    def _voice_issue(self, message: str) -> None:
        if self.closing:
            return
        self.root.after(
            0, lambda: self.voice_status_label.configure(text=message)
        )
        self.root.after(0, self._log, message)

    def _set_ai_status(self, text: str) -> None:
        if not self.closing:
            self.ai_status_label.configure(text=text)

    def _reload_channel_history(self) -> None:
        if not self.selected_channel_id:
            messagebox.showinfo("Select a channel", "Choose a server and text channel first.")
            return
        self._channel_changed(self.channel_menu.get())

    async def _load_recent_messages(
        self, channel: discord.TextChannel
    ) -> list[discord.Message]:
        guild = channel.guild
        bot_member = self._bot_member(guild)
        permissions = channel.permissions_for(bot_member)
        if not permissions.view_channel or not permissions.read_message_history:
            raise PermissionError(
                "The bot needs View Channel and Read Message History permissions."
            )
        messages = [
            message async for message in channel.history(limit=50, oldest_first=True)
        ]
        return [message for message in messages if not message.author.bot]

    def _feed_load_failed(self, request_id: int, error: str) -> None:
        if self.closing or request_id != self.feed_request_id:
            return
        self.feed_status_label.configure(
            text=f"{error} For message text, enable Message Content Intent in the Developer Portal."
        )
        self._log(f"Message feed: {error}")
        self._clear_feed("Could not load channel messages.")

    def _feed_history_loaded(
        self, request_id: int, messages: list[discord.Message]
    ) -> None:
        if self.closing or request_id != self.feed_request_id:
            return
        if not messages:
            self._clear_feed("No recent human-authored messages. Waiting for new messages…")
        else:
            for message in messages:
                self._add_feed_message(message)
        self.feed_status_label.configure(
            text=f"Feed ready — showing {len(messages)} recent messages and listening for new ones. "
            "If text is hidden, enable Message Content Intent in the Developer Portal."
        )

    def _clear_feed(self, message: str) -> None:
        for child in self.feed_frame.winfo_children():
            child.destroy()
        self.feed_cards.clear()
        self.selected_message = None
        self.reply_context_label.configure(text="No message selected")
        self.feed_empty_label = ctk.CTkLabel(
            self.feed_frame, text=message, text_color="#8995b3",
        )
        self.feed_empty_label.pack(anchor="w", padx=10, pady=10)

    def _add_feed_message(self, message: discord.Message) -> None:
        if (
            self.closing
            or message.guild is None
            or message.guild.id != self.selected_guild_id
            or message.channel.id != self.selected_channel_id
            or any(existing.id == message.id for existing, _ in self.feed_cards)
        ):
            return
        if self.feed_empty_label.winfo_exists():
            self.feed_empty_label.destroy()

        card = ctk.CTkFrame(self.feed_frame, corner_radius=8, fg_color="#18233a")
        card.pack(fill="x", padx=5, pady=4)
        channel_name = getattr(message.channel, "name", "channel")
        content = message.clean_content.strip() or "(no text content)"
        if not message.clean_content.strip() and not message.attachments:
            content = "(message text unavailable — enable Message Content Intent in the Developer Portal)"
        if message.attachments:
            filenames = ", ".join(
                attachment.filename for attachment in message.attachments[:3]
            )
            extra = "…" if len(message.attachments) > 3 else ""
            content += f"  [attachments: {filenames}{extra}]"
        ctk.CTkLabel(
            card,
            text=f"{message.author.display_name}  •  #{channel_name}",
            font=ctk.CTkFont(size=11, weight="bold"), text_color="#8b9cff",
            anchor="w",
        ).pack(fill="x", padx=10, pady=(7, 2))
        ctk.CTkLabel(
            card, text=content[:1000], font=ctk.CTkFont(size=12),
            text_color="#dce3f5", anchor="w", justify="left",
            wraplength=650,
        ).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkButton(
            card, text="Select to reply", width=110, height=26,
            fg_color="#293653", hover_color="#354567",
            command=lambda selected=message: self._select_feed_message(selected),
        ).pack(anchor="e", padx=8, pady=(0, 7))
        self.feed_cards.append((message, card))
        if len(self.feed_cards) > self.MAX_FEED_MESSAGES:
            _, oldest_card = self.feed_cards.pop(0)
            oldest_card.destroy()
        self.feed_frame._parent_canvas.yview_moveto(1.0)
        self._schedule_label_reflow()

    def _select_feed_message(self, message: discord.Message) -> None:
        self.selected_message = message
        for card_message, card in self.feed_cards:
            card.configure(
                fg_color="#293653" if card_message.id == message.id else "#18233a"
            )
        author = message.author.display_name
        channel = getattr(message.channel, "name", "channel")
        self.reply_context_label.configure(text=f"Replying to {author} in #{channel}")

    def _reply_to_selected(self) -> None:
        message = self.selected_message
        if message is None or message.guild is None:
            messagebox.showinfo("Select a message", "Choose a message from the live feed first.")
            return
        if (
            message.guild.id != self.selected_guild_id
            or message.channel.id != self.selected_channel_id
        ):
            messagebox.showinfo("Message no longer selected", "Select a message in the current channel.")
            return
        content = self.reply_entry.get().strip()
        if not content:
            messagebox.showinfo("Reply is empty", "Type a message before sending.")
            return
        if len(content) > 2000:
            messagebox.showerror("Reply too long", "Discord messages must be 2,000 characters or fewer.")
            return
        guild = message.guild
        channel = message.channel
        if not isinstance(channel, discord.TextChannel):
            messagebox.showerror("Unsupported channel", "Replies are supported in text channels only.")
            return

        async def send_reply() -> None:
            bot_member = self._bot_member(guild)
            permissions = channel.permissions_for(bot_member)
            if not (
                permissions.view_channel
                and permissions.read_message_history
                and permissions.send_messages
            ):
                raise PermissionError(
                    "The bot needs View Channel, Read Message History, and Send Messages permissions."
                )
            sent_message = await channel.send(
                content,
                reference=message,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            self.root.after(0, self._add_feed_message, sent_message)
            self.root.after(0, self.reply_entry.delete, 0, "end")
            voice = self.voice_client
            if (
                self.speak_manual_replies
                and voice
                and voice.is_connected()
                and voice.guild.id == guild.id
            ):
                await self._speak_text(content)

        self._run_guild_action(send_reply(), f"Reply sent in #{channel.name}.")

    def _selected_role(self, guild: discord.Guild) -> discord.Role | None:
        role_id = self.role_options.get(self.role_menu.get())
        role = guild.get_role(role_id) if role_id is not None else None
        if role is None:
            messagebox.showinfo("Role not selected", "Select a role from the chosen server.")
        return role

    @staticmethod
    def _bot_member(guild: discord.Guild) -> discord.Member:
        member = guild.me
        if member is None:
            raise discord.ClientException("The bot's server member is not available.")
        return member

    def _run_guild_action(
        self, coroutine: Coroutine[Any, Any, None], success_message: str
    ) -> None:
        if not self.loop or not self.loop.is_running():
            if asyncio.iscoroutine(coroutine):
                coroutine.close()
            messagebox.showinfo("Bot is offline", "Connect the bot before running server actions.")
            return
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)

        def finished(result: Future[None]) -> None:
            succeeded = False
            try:
                error = result.exception()
                succeeded = error is None
                error_message = f"Action failed: {error}" if error else ""
            except Exception as caught:
                error_message = f"Action failed: {caught}"
            message = error_message or success_message
            if not self.closing:
                self.root.after(0, self._log, message)
                if succeeded:
                    self.root.after(0, self._refresh_selected_roles)

        future.add_done_callback(finished)

    def _refresh_selected_roles(self) -> None:
        guild_id = self.guild_options.get(self.guild_menu.get())
        guild = self.client.get_guild(guild_id) if self.client and guild_id else None
        if guild is None:
            return
        selected = self.role_menu.get()
        self.role_options = {
            f"{role.name} ({role.id})": role.id
            for role in sorted(guild.roles, key=lambda item: item.position, reverse=True)
            if role != guild.default_role
        }
        options = list(self.role_options) or ["No manageable roles available"]
        self.role_menu.configure(values=options)
        self.role_menu.set(selected if selected in self.role_options else options[0])

    def _validate_user_id(self, entry: ctk.CTkEntry | None = None) -> int | None:
        target = entry or self.member_id_entry
        value = target.get().strip()
        if not value.isdecimal() or not 17 <= len(value) <= 20:
            messagebox.showerror("Invalid user ID", "Enter a Discord user ID containing 17–20 digits.")
            return None
        return int(value)

    def _ban_member(self) -> None:
        guild = self._selected_guild()
        if guild is None:
            return
        user_id = self._validate_user_id(self.member_id_entry)
        if user_id is None:
            return
        if not messagebox.askyesno(
            "Confirm ban",
            f"Ban user ID {user_id} from {guild.name}?\n\n"
            "This is a destructive action and requires the bot's Ban Members permission.",
        ):
            return
        reason = self.ban_reason_entry.get().strip() or "Banned via Orbit dashboard"

        async def ban() -> None:
            if not self._bot_member(guild).guild_permissions.ban_members:
                raise PermissionError("The bot needs the Ban Members permission in this server.")
            await guild.ban(discord.Object(id=user_id), reason=reason)

        self._run_guild_action(ban(), f"Ban requested for user ID {user_id} in {guild.name}.")

    def _create_role(self) -> None:
        guild = self._selected_guild()
        if guild is None:
            return
        name = self.role_name_entry.get().strip()
        if not name:
            messagebox.showerror("Role name required", "Enter a name for the new role.")
            return
        try:
            colour = discord.Colour.from_str(self.role_color_entry.get().strip() or "#5865F2")
        except ValueError:
            messagebox.showerror("Invalid color", "Enter a hex color such as #5865F2.")
            return

        async def create() -> None:
            if not self._bot_member(guild).guild_permissions.manage_roles:
                raise PermissionError("The bot needs the Manage Roles permission in this server.")
            await guild.create_role(name=name, colour=colour, reason="Created via Orbit dashboard")

        self._run_guild_action(create(), f"Created role '{name}' in {guild.name}.")

    def _edit_role(self) -> None:
        guild = self._selected_guild()
        if guild is None:
            return
        role = self._selected_role(guild)
        if role is None:
            return
        name = self.role_name_entry.get().strip()
        color_text = self.role_color_entry.get().strip()
        try:
            colour = discord.Colour.from_str(color_text) if color_text else None
        except ValueError:
            messagebox.showerror("Invalid color", "Enter a hex color such as #5865F2.")
            return
        if not name and colour is None:
            messagebox.showinfo("No changes", "Enter a new role name or color.")
            return

        async def edit() -> None:
            bot_member = self._bot_member(guild)
            if not bot_member.guild_permissions.manage_roles:
                raise PermissionError("The bot needs the Manage Roles permission in this server.")
            if role.managed or role >= bot_member.top_role:
                raise PermissionError("The bot cannot edit managed roles or roles at/above its top role.")
            await role.edit(
                name=name or role.name,
                colour=colour if colour is not None else role.colour,
                reason="Edited via Orbit dashboard",
            )

        self._run_guild_action(edit(), f"Updated role '{role.name}' in {guild.name}.")

    def _delete_role(self) -> None:
        guild = self._selected_guild()
        if guild is None:
            return
        role = self._selected_role(guild)
        if role is None:
            return
        if not messagebox.askyesno(
            "Confirm role deletion",
            f"Delete role '{role.name}' from {guild.name}?\nThis cannot be undone.",
        ):
            return

        async def delete() -> None:
            bot_member = self._bot_member(guild)
            if not bot_member.guild_permissions.manage_roles:
                raise PermissionError("The bot needs the Manage Roles permission in this server.")
            if role.managed or role >= bot_member.top_role:
                raise PermissionError("The bot cannot delete managed roles or roles at/above its top role.")
            await role.delete(reason="Deleted via Orbit dashboard")

        self._run_guild_action(delete(), f"Deleted role '{role.name}' from {guild.name}.")

    def _change_member_role(self, grant: bool) -> None:
        guild = self._selected_guild()
        if guild is None:
            return
        user_id = self._validate_user_id(self.role_member_id_entry)
        if user_id is None:
            return
        role = self._selected_role(guild)
        if role is None:
            return
        action = "grant" if grant else "revoke"
        if not messagebox.askyesno(
            f"Confirm role {action}",
            f"{action.capitalize()} '{role.name}' for user ID {user_id} in {guild.name}?",
        ):
            return

        async def change() -> None:
            bot_member = self._bot_member(guild)
            if not bot_member.guild_permissions.manage_roles:
                raise PermissionError("The bot needs the Manage Roles permission in this server.")
            if role.managed or role >= bot_member.top_role:
                raise PermissionError("The bot cannot assign managed roles or roles at/above its top role.")
            member = await guild.fetch_member(user_id)
            if member.top_role >= bot_member.top_role:
                raise PermissionError("The bot cannot manage a member at/above its top role.")
            if grant:
                await member.add_roles(role, reason="Granted via Orbit dashboard")
            else:
                await member.remove_roles(role, reason="Revoked via Orbit dashboard")

        self._run_guild_action(
            change(), f"Role {action} requested for user ID {user_id} in {guild.name}."
        )

    def _refresh_uptime(self) -> None:
        self.clock_label.configure(text=f"LOCAL TIME  {time.strftime('%H:%M:%S')}")
        self._refresh_gateway_signal()
        if self.started_at is not None:
            seconds = int(time.monotonic() - self.started_at)
            hours, remainder = divmod(seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            self.uptime_value.configure(text=f"{hours:02}:{minutes:02}:{seconds:02}")
        if not self.closing:
            self.root.after(1000, self._refresh_uptime)

    def _refresh_gateway_signal(self) -> None:
        client = self.client
        if client is None or not client.is_ready():
            self.gateway_latency_label.configure(text="— ms")
            self.gateway_health_label.configure(
                text="Connect the bot to start monitoring.", text_color="#8995b3"
            )
            self._draw_gateway_chart()
            return

        latency_ms = max(0.0, client.latency * 1000)
        self.gateway_latency_samples.append(latency_ms)
        del self.gateway_latency_samples[:-40]
        self.gateway_latency_label.configure(text=f"{round(latency_ms)} ms")
        if latency_ms < 120:
            health, color = "Excellent · low latency", "#61d995"
        elif latency_ms < 250:
            health, color = "Good · normal latency", "#f4c76b"
        else:
            health, color = "Slow · check network or Discord status", "#f07878"
        self.gateway_health_label.configure(text=health, text_color=color)
        self._draw_gateway_chart()

    def _draw_gateway_chart(self, event: Any = None) -> None:
        if not hasattr(self, "gateway_chart"):
            return
        chart = self.gateway_chart
        if event is not None and event.widget is not chart:
            return
        chart.delete("all")
        width = chart.winfo_width()
        height = chart.winfo_height()
        if width <= 1 or height <= 1:
            return

        left, right, top, bottom = 8, width - 8, 8, height - 8
        for fraction in (0.25, 0.5, 0.75):
            y = top + (bottom - top) * fraction
            chart.create_line(left, y, right, y, fill="#293653", dash=(2, 4))

        samples = self.gateway_latency_samples
        if len(samples) < 2:
            chart.create_text(
                width / 2, height / 2, text="Latency history appears as the bot runs",
                fill="#64708e", font=("Segoe UI", 10),
            )
            return

        points: list[float] = []
        span = right - left
        for index, latency in enumerate(samples):
            x = left + span * index / (len(samples) - 1)
            y = bottom - (bottom - top) * min(latency, 500) / 500
            points.extend((x, y))
        latest = samples[-1]
        color = "#61d995" if latest < 120 else "#f4c76b" if latest < 250 else "#f07878"
        chart.create_line(*points, fill=color, width=2, smooth=True)
        chart.create_oval(
            points[-2] - 3, points[-1] - 3, points[-2] + 3, points[-1] + 3,
            fill=color, outline="",
        )

    def _schedule_label_reflow(self, event: Any = None) -> None:
        if event is not None and event.widget is not self.root:
            return
        if self.closing or self._layout_after_id is not None:
            return
        self._layout_after_id = self.root.after(60, self._reflow_labels)

    def _reflow_labels(self) -> None:
        self._layout_after_id = None
        if self.closing:
            return

        pending = list(self.root.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if not isinstance(widget, ctk.CTkLabel):
                continue
            label_width = widget.winfo_width()
            parent_width = widget.master.winfo_width() - 32
            if label_width <= 1 or parent_width <= 0:
                continue
            wraplength = max(120, min(label_width, parent_width) - 4)
            if widget.cget("wraplength") != wraplength:
                widget.configure(wraplength=wraplength)

    def _set_motion_effects(self) -> None:
        self._save_app_settings()
        if not self.motion_enabled.get():
            status = self.status_value.cget("text")
            if status == "Online":
                self.status_value.configure(text_color="#61d995")

    def _animate_ui_surfaces(self) -> None:
        if self.closing or not self.motion_enabled.get():
            return

        surfaces: list[ctk.CTkFrame] = []
        pending = list(self.root.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if (
                isinstance(widget, ctk.CTkFrame)
                and widget.cget("fg_color") == "#141d32"
            ):
                surfaces.append(widget)

        for index, surface in enumerate(surfaces):
            surface.configure(fg_color="#0b1020")
            self.root.after(
                index * 24, self._animate_surface, surface, "#0b1020", "#141d32", 0
            )

    def _animate_surface(
        self,
        surface: ctk.CTkFrame,
        start_color: str,
        end_color: str,
        step: int,
    ) -> None:
        if self.closing:
            return
        if not self.motion_enabled.get() or step >= 12:
            surface.configure(fg_color=end_color)
            return

        progress = (step + 1) / 12
        eased_progress = 1 - (1 - progress) ** 3
        start_rgb = tuple(
            int(start_color[index:index + 2], 16) for index in (1, 3, 5)
        )
        end_rgb = tuple(
            int(end_color[index:index + 2], 16) for index in (1, 3, 5)
        )
        color = "#" + "".join(
            f"{round(start + (end - start) * eased_progress):02x}"
            for start, end in zip(start_rgb, end_rgb)
        )
        surface.configure(fg_color=color)
        self.root.after(
            18, self._animate_surface, surface, start_color, end_color, step + 1
        )

    def _animate_dashboard(self) -> None:
        if self.closing:
            return
        status = self.status_value.cget("text")
        if self.motion_enabled.get() and status == "Online":
            pulse = (1 - math.cos(time.monotonic() * math.tau / 2.4)) / 2
            low_color = (0x3F, 0xAE, 0x7B)
            high_color = (0x61, 0xD9, 0x95)
            color = "#" + "".join(
                f"{round(low + (high - low) * pulse):02x}"
                for low, high in zip(low_color, high_color)
            )
            self.status_value.configure(text_color=color)
            self.sidebar_status_label.configure(text_color=color, text="\u25cf  BOT ONLINE")
        else:
            label = "\u25cf  BOT OFFLINE" if status == "Offline" else status.upper()
            color = "#61d995" if status == "Online" else self.status_value.cget("text_color")
            if status == "Online":
                self.status_value.configure(text_color=color)
            self.sidebar_status_label.configure(text=label, text_color=color)
        if not self.closing:
            self.root.after(80, self._animate_dashboard)

    def _close_window(self) -> None:
        self.closing = True
        if self.loop and self.client and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown_client(self.client), self.loop)
        self.root.destroy()


def main() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    from orbit_features import install_features
    install_features(BotDashboard)
    BotDashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               