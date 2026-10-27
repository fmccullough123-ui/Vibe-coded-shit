import asyncio
import json
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
import platform
from tkinter import Menu, TclError, filedialog, simpledialog
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
import webbrowser

import customtkinter as ctk
import discord
import keyring
from tkinter import messagebox


PROFILE_SERVICE = "Orbit.DiscordBotDashboard"
PROFILE_PREFIX = "bot-profile-"
CUSTOM_API_USERNAME = "custom-api-key"


def install_features(dashboard_class: type) -> None:
    original_build_ui = dashboard_class._build_ui
    original_load_settings = dashboard_class._load_app_settings
    original_save_settings = dashboard_class._save_app_settings
    original_copilot_reply = dashboard_class._copilot_chat_reply
    original_refresh_uptime = dashboard_class._refresh_uptime
    original_start_bot = dashboard_class._start_bot
    original_add_feed_message = dashboard_class._add_feed_message
    original_guild_changed = dashboard_class._guild_changed
    original_close_window = dashboard_class._close_window

    def build_ui(self) -> None:
        original_build_ui(self)
        self.token_entry.configure(
            placeholder_text="Paste bot token, then click Start bot"
        )
        connection = self.token_entry.master
        self.connect_button.grid_configure(column=2)
        self.token_visible_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            connection,
            text="Show token",
            variable=self.token_visible_var,
            command=self._feature_toggle_token_visibility,
            text_color="#aab5ce",
            fg_color="#5865f2",
            hover_color="#4752c4",
        ).grid(row=1, column=1, padx=6, pady=10)
        self.bot_profiles = {}
        self.selected_bot_profile = None
        self.feature_settings_path = (
            Path(self.settings_path).with_name("dashboard-feature-settings.json")
        )
        def find_scrollable(widget):
            if isinstance(widget, ctk.CTkScrollableFrame):
                return widget
            for child in widget.winfo_children():
                found = find_scrollable(child)
                if found is not None:
                    return found
            return None

        main = find_scrollable(self.root)
        if main is None:
            raise RuntimeError("Could not locate the dashboard scrollable frame.")
        connection = next(
            child for child in main.winfo_children()
            if child.grid_info().get("row") == 3
        )
        for child in main.winfo_children():
            row = child.grid_info().get("row")
            if row is not None and row >= 4:
                child.grid_configure(row=row + 1)
        connection.grid_configure(row=4)
        for child in main.winfo_children():
            row = child.grid_info().get("row")
            if row is not None and row >= 7:
                child.grid_configure(row=row + 1)
        main.grid_rowconfigure(12, weight=1)
        connection.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.member_count_value = self._stat_card(
            main, 3, 0, "CACHED MEMBERS", "—"
        )
        self.latency_value = self._stat_card(
            main, 3, 1, "GATEWAY LATENCY", "—"
        )
        self.profile_menu = ctk.CTkOptionMenu(
            connection,
            values=["No saved profiles"],
            command=self._feature_select_profile,
            fg_color="#293653",
            button_color="#5865f2",
        )
        self.profile_menu.grid(
            row=4, column=0, sticky="ew", padx=(18, 6), pady=(2, 6)
        )
        ctk.CTkButton(
            connection, text="Switch bot",
            command=self._feature_switch_profile,
            fg_color="#5865f2", hover_color="#4752c4",
        ).grid(row=4, column=1, sticky="ew", padx=6, pady=(2, 6))
        ctk.CTkButton(
            connection, text="‹ Previous",
            command=lambda: self._feature_cycle_profile(-1),
            fg_color="#293653", hover_color="#354567",
        ).grid(row=4, column=2, sticky="ew", padx=6, pady=(2, 6))
        ctk.CTkButton(
            connection, text="Next ›",
            command=lambda: self._feature_cycle_profile(1),
            fg_color="#293653", hover_color="#354567",
        ).grid(row=4, column=3, sticky="ew", padx=(6, 18), pady=(2, 6))
        self.profile_name_entry = ctk.CTkEntry(
            connection, placeholder_text="Profile name", height=34,
        )
        self.profile_name_entry.grid(
            row=5, column=0, sticky="ew", padx=(18, 6), pady=(0, 7)
        )
        ctk.CTkButton(
            connection, text="Save token as profile",
            command=self._feature_save_profile,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=5, column=1, columnspan=2, sticky="ew", padx=6, pady=(0, 7))
        ctk.CTkButton(
            connection, text="Delete profile…",
            command=self._feature_delete_profile,
            fg_color="#a83f52", hover_color="#863244",
        ).grid(row=5, column=3, sticky="ew", padx=(6, 18), pady=(0, 7))
        self.profile_hint_label = ctk.CTkLabel(
            connection,
            text="Profile tokens are protected by Windows Credential Manager.",
            font=ctk.CTkFont(size=11), text_color="#8995b3",
        )
        self.profile_hint_label.grid(
            row=6, column=0, columnspan=4, sticky="w", padx=18, pady=(0, 12)
        )

        self.ai_provider_menu.configure(
            values=["Groq", "GitHub Copilot", "Custom API"]
        )
        ai_panel = next(
            child for child in main.winfo_children()
            if child.grid_info().get("row") == 9
        )
        ai_panel.grid_columnconfigure((0, 1, 2), weight=1)
        self.custom_api_url_entry = ctk.CTkEntry(
            ai_panel, placeholder_text="OpenAI-compatible API base URL", height=36,
        )
        self.custom_api_url_entry.grid(
            row=6, column=0, sticky="ew", padx=(16, 6), pady=5
        )
        self.custom_api_model_entry = ctk.CTkEntry(
            ai_panel, placeholder_text="Model name", height=36,
        )
        self.custom_api_model_entry.grid(
            row=6, column=1, columnspan=2, sticky="ew", padx=(6, 16), pady=5
        )
        self.custom_api_key_entry = ctk.CTkEntry(
            ai_panel, placeholder_text="Custom API key (stored securely)",
            show="•", height=36,
        )
        self.custom_api_key_entry.grid(
            row=7, column=0, sticky="ew", padx=(16, 6), pady=(5, 9)
        )
        ctk.CTkButton(
            ai_panel, text="Save custom API",
            command=self._feature_save_api_settings,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=7, column=1, sticky="ew", padx=6, pady=(5, 9))
        self.custom_api_test_button = ctk.CTkButton(
            ai_panel, text="Test custom API",
            command=self._feature_test_api,
            fg_color="#293653", hover_color="#354567",
        )
        self.custom_api_test_button.grid(
            row=7, column=2, sticky="ew", padx=(6, 16), pady=(5, 9)
        )
        self.custom_api_status = self.ai_status_label
        self._feature_api_key = None
        self._feature_dm_windows = {}
        self._feature_dm_appenders = {}
        self._feature_hover_after_id = None
        self._feature_hover_window = None
        self._feature_announcement_recipients = {}
        self._feature_announcement_candidates = {}
        self._feature_announcement_mode = "DM selected members"

        announcement = ctk.CTkFrame(main, corner_radius=14, fg_color="#141d32")
        announcement.grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(0, 12)
        )
        announcement.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(
            announcement, text="Announcements",
            font=ctk.CTkFont(size=16, weight="bold"), text_color="#e9edfa",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 4))
        ctk.CTkLabel(
            announcement,
            text=(
                "DM a manually selected list, DM non-bot members of a selected role, DM all cached "
                "non-bot server members, or post to the "
                "currently selected channel. DMs support {username}, {display_name}, "
                "and {server}; channel posts support {server} and {channel}. "
                "Bulk sends are limited, throttled, and require confirmation."
            ),
            font=ctk.CTkFont(size=11), text_color="#8995b3",
            wraplength=720, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))
        self.announcement_mode_menu = ctk.CTkOptionMenu(
            announcement,
            values=[
                "DM selected members",
                "DM all members with selected role",
                "DM all server members",
                "Post in selected channel",
            ],
            command=self._feature_announcement_mode_changed,
            fg_color="#293653", button_color="#5865f2",
        )
        self.announcement_mode_menu.grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=5
        )
        self.announcement_member_search = ctk.CTkEntry(
            announcement, placeholder_text="Find a member by name or user ID", height=36,
        )
        self.announcement_member_search.grid(
            row=3, column=0, sticky="ew", padx=(16, 6), pady=5
        )
        self.announcement_member_search.bind(
            "<Return>", lambda _event: self._feature_search_announcement_members()
        )
        self.announcement_find_members_button = ctk.CTkButton(
            announcement, text="Find members",
            command=self._feature_search_announcement_members,
            fg_color="#293653", hover_color="#354567",
        )
        self.announcement_find_members_button.grid(
            row=3, column=1, sticky="ew", padx=(6, 16), pady=5
        )
        self.announcement_member_menu = ctk.CTkOptionMenu(
            announcement, values=["Connect bot and select a server"],
            fg_color="#293653", button_color="#5865f2",
        )
        self.announcement_member_menu.grid(
            row=4, column=0, sticky="ew", padx=(16, 6), pady=5
        )
        self.announcement_add_member_button = ctk.CTkButton(
            announcement, text="Add selected member",
            command=self._feature_add_announcement_recipient,
            fg_color="#293653", hover_color="#354567",
        )
        self.announcement_add_member_button.grid(
            row=4, column=1, sticky="ew", padx=(6, 16), pady=5
        )
        self.announcement_member_status = ctk.CTkLabel(
            announcement, text="Search among members currently cached for the selected server.",
            font=ctk.CTkFont(size=10), text_color="#8995b3", anchor="w",
        )
        self.announcement_member_status.grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=16, pady=(2, 4)
        )
        self.announcement_role_menu = ctk.CTkOptionMenu(
            announcement, values=["Select a server first"],
            command=lambda _selection: self._feature_update_announcement_role_status(),
            fg_color="#293653", button_color="#5865f2",
        )
        self.announcement_role_menu.grid(
            row=3, column=0, sticky="ew", padx=(16, 6), pady=5
        )
        self.announcement_limit_menu = ctk.CTkOptionMenu(
            announcement,
            values=["10", "25", "50", "Infinite"],
            fg_color="#293653", button_color="#5865f2",
        )
        self.announcement_limit_menu.set("25")
        self.announcement_limit_menu.grid(
            row=4, column=0, sticky="ew", padx=(16, 6), pady=5
        )
        self.announcement_role_status = ctk.CTkLabel(
            announcement, text="Choose a role to target its non-bot members.",
            font=ctk.CTkFont(size=10), text_color="#8995b3", anchor="w",
        )
        self.announcement_role_status.grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=16, pady=(2, 4)
        )
        self.announcement_role_status.grid_remove()
        self.announcement_all_members_status = ctk.CTkLabel(
            announcement,
            text="Only non-bot members currently cached by Discord are included.",
            font=ctk.CTkFont(size=10), text_color="#8995b3", anchor="w",
        )
        self.announcement_all_members_status.grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=16, pady=(2, 4)
        )
        self.announcement_all_members_status.grid_remove()
        self.announcement_recipient_list = ctk.CTkScrollableFrame(
            announcement, height=100, corner_radius=10, fg_color="#0e1526",
        )
        self.announcement_recipient_list.grid(
            row=6, column=0, columnspan=2, sticky="ew", padx=16, pady=5
        )
        self.announcement_message = ctk.CTkTextbox(
            announcement, height=90, wrap="word",
            fg_color="#0e1526", text_color="#dce3f5",
        )
        self.announcement_message.grid(
            row=7, column=0, columnspan=2, sticky="ew", padx=16, pady=5
        )
        self.announcement_message.bind(
            "<KeyRelease>", lambda _event: self._feature_update_announcement_preview()
        )
        self.announcement_embed_panel = ctk.CTkFrame(
            announcement, corner_radius=10, fg_color="#10182b"
        )
        self.announcement_embed_panel.grid(
            row=8, column=0, columnspan=2, sticky="ew", padx=16, pady=5
        )
        self.announcement_embed_panel.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(
            self.announcement_embed_panel, text="Optional embed and menus",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#c3cce1",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 3))
        self.announcement_embed_title = ctk.CTkEntry(
            self.announcement_embed_panel, placeholder_text="Embed title (optional)",
            height=32,
        )
        self.announcement_embed_title.grid(
            row=1, column=0, sticky="ew", padx=(10, 5), pady=4
        )
        self.announcement_embed_color = ctk.CTkEntry(
            self.announcement_embed_panel, placeholder_text="Color: #5865F2",
            height=32,
        )
        self.announcement_embed_color.grid(
            row=1, column=1, sticky="ew", padx=(5, 10), pady=4
        )
        self.announcement_embed_description = ctk.CTkTextbox(
            self.announcement_embed_panel, height=60, wrap="word",
            fg_color="#0e1526", text_color="#dce3f5",
        )
        self.announcement_embed_description.grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=4
        )
        self.announcement_embed_button_label = ctk.CTkEntry(
            self.announcement_embed_panel, placeholder_text="Link button label (optional)",
            height=32,
        )
        self.announcement_embed_button_label.grid(
            row=3, column=0, sticky="ew", padx=(10, 5), pady=4
        )
        self.announcement_embed_button_url = ctk.CTkEntry(
            self.announcement_embed_panel, placeholder_text="https:// link for button",
            height=32,
        )
        self.announcement_embed_button_url.grid(
            row=3, column=1, sticky="ew", padx=(5, 10), pady=4
        )
        self.announcement_embed_choices = ctk.CTkEntry(
            self.announcement_embed_panel,
            placeholder_text="Dropdown choices, comma-separated (optional)",
            height=32,
        )
        self.announcement_embed_choices.grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 8)
        )
        for entry in (
            self.announcement_embed_title,
            self.announcement_embed_color,
            self.announcement_embed_button_label,
            self.announcement_embed_button_url,
            self.announcement_embed_choices,
        ):
            entry.bind(
                "<KeyRelease>",
                lambda _event: self._feature_update_announcement_preview(),
            )
        self.announcement_embed_description.bind(
            "<KeyRelease>",
            lambda _event: self._feature_update_announcement_preview(),
        )
        self.announcement_preview = ctk.CTkLabel(
            announcement, text="Preview: write a message to see its rendered version.",
            font=ctk.CTkFont(size=10), text_color="#aab5ce", anchor="w",
            justify="left", wraplength=720,
        )
        self.announcement_preview.grid(
            row=9, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 8)
        )
        self.announcement_send_button = ctk.CTkButton(
            announcement, text="Send announcement…",
            command=self._feature_send_announcement,
            fg_color="#5865f2", hover_color="#4752c4",
        )
        self.announcement_send_button.grid(
            row=10, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 6)
        )
        self.announcement_status = ctk.CTkLabel(
            announcement, text="Nothing sent yet.",
            font=ctk.CTkFont(size=10), text_color="#8995b3", anchor="w",
        )
        self.announcement_status.grid(
            row=11, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 12)
        )
        self._feature_render_announcement_recipients()
        self._feature_announcement_mode_changed(self._feature_announcement_mode)
        self._feature_update_announcement_preview()

        self._feature_section_frames = {}
        self._feature_section_grid = {}
        self._feature_collapsed_sections = set()
        self._feature_server_notes = {}
        self._feature_note_guild_id = None
        self._feature_note_save_after_id = None
        self._feature_welcome_channel_id = None
        for child in main.winfo_children():
            row = child.grid_info().get("row")
            if not isinstance(child, ctk.CTkFrame) or not isinstance(row, int) or row < 4:
                continue
            labels = [
                widget for widget in child.winfo_children()
                if isinstance(widget, ctk.CTkLabel)
            ]
            if not labels:
                continue
            title = labels[0].cget("text").strip()
            if title and title not in self._feature_section_frames:
                self._feature_section_frames[title] = child
                info = child.grid_info()
                self._feature_section_grid[title] = {
                    key: info[key]
                    for key in ("row", "column", "rowspan", "columnspan", "sticky", "padx", "pady")
                    if key in info
                }

        tool_row = max(
            (int(child.grid_info().get("row", 0)) for child in main.winfo_children()),
            default=0,
        ) + 1
        utilities = ctk.CTkFrame(main, corner_radius=14, fg_color="#141d32")
        utilities.grid(row=tool_row, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        utilities.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(
            utilities, text="Orbit toolkit",
            font=ctk.CTkFont(size=16, weight="bold"), text_color="#e9edfa",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 8))

        ctk.CTkLabel(
            utilities, text="Session activity",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#c3cce1",
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(2, 4))
        self._feature_counter_labels = {}
        counter_grid = ctk.CTkFrame(utilities, fg_color="transparent")
        counter_grid.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12)
        counter_grid.grid_columnconfigure((0, 1, 2, 3), weight=1)
        for column, (key, label) in enumerate((
            ("messages", "Server msgs"),
            ("ai_replies", "AI replies"),
            ("voice_replies", "Voice replies"),
            ("errors", "Errors"),
        )):
            card = ctk.CTkFrame(counter_grid, corner_radius=10, fg_color="#10182b")
            card.grid(row=0, column=column, sticky="ew", padx=4, pady=4)
            value = ctk.CTkLabel(
                card, text="0", font=ctk.CTkFont(size=18, weight="bold"),
                text_color="#edf0ff",
            )
            value.pack(anchor="w", padx=10, pady=(8, 0))
            ctk.CTkLabel(
                card, text=label, font=ctk.CTkFont(size=10), text_color="#8995b3",
            ).pack(anchor="w", padx=10, pady=(0, 8))
            self._feature_counter_labels[key] = value

        ctk.CTkLabel(
            utilities, text="Recent dashboard activity",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#c3cce1",
        ).grid(row=3, column=0, sticky="w", padx=16, pady=(10, 4))
        self._feature_activity_text = ctk.CTkTextbox(
            utilities, height=100, wrap="word", fg_color="#0e1526",
            text_color="#aeb9d3", font=ctk.CTkFont(size=10),
        )
        self._feature_activity_text.grid(
            row=4, column=0, sticky="ew", padx=(16, 6), pady=(0, 8)
        )
        self._feature_activity_text.configure(state="disabled")
        ctk.CTkLabel(
            utilities, text="Slash command playground",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#c3cce1",
        ).grid(row=3, column=1, sticky="w", padx=10, pady=(10, 4))
        self._feature_command_text = ctk.CTkTextbox(
            utilities, height=100, wrap="word", fg_color="#0e1526",
            text_color="#aeb9d3", font=ctk.CTkFont(size=10),
        )
        self._feature_command_text.grid(
            row=4, column=1, sticky="ew", padx=(6, 16), pady=(0, 8)
        )
        self._feature_command_text.bind(
            "<Double-Button-1>", self._feature_copy_command_example
        )
        self._feature_command_text.configure(state="disabled")

        self._feature_layout_menu = ctk.CTkOptionMenu(
            utilities, values=["Select a section"],
            fg_color="#293653", button_color="#5865f2",
        )
        self._feature_layout_menu.grid(
            row=5, column=0, sticky="ew", padx=(16, 6), pady=5
        )
        layout_buttons = ctk.CTkFrame(utilities, fg_color="transparent")
        layout_buttons.grid(row=5, column=1, sticky="ew", padx=(6, 16), pady=5)
        layout_buttons.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            layout_buttons, text="Collapse / expand",
            command=self._feature_toggle_section,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(
            layout_buttons, text="Reset layout",
            command=self._feature_reset_layout,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        ctk.CTkLabel(
            utilities, text="Per-server notes",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#c3cce1",
        ).grid(row=6, column=0, sticky="w", padx=16, pady=(10, 4))
        self._feature_notes_text = ctk.CTkTextbox(
            utilities, height=90, wrap="word", fg_color="#0e1526",
            text_color="#dce3f5",
        )
        self._feature_notes_text.grid(
            row=7, column=0, sticky="ew", padx=(16, 6), pady=(0, 8)
        )
        self._feature_notes_text.bind(
            "<KeyRelease>", lambda _event: self._feature_schedule_note_save()
        )
        self._feature_notes_text.bind(
            "<FocusOut>", lambda _event: self._feature_save_note()
        )
        self._feature_notes_status = ctk.CTkLabel(
            utilities, text="Notes are stored locally on this computer.",
            font=ctk.CTkFont(size=10), text_color="#8995b3", anchor="w",
        )
        self._feature_notes_status.grid(
            row=8, column=0, sticky="ew", padx=(16, 6), pady=(0, 8)
        )
        welcome_frame = ctk.CTkFrame(utilities, corner_radius=10, fg_color="#10182b")
        welcome_frame.grid(row=6, column=1, rowspan=3, sticky="nsew", padx=(6, 16), pady=(10, 8))
        welcome_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            welcome_frame, text="Welcome message",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#c3cce1",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(9, 3))
        ctk.CTkLabel(
            welcome_frame,
            text="Uses the saved text channel; Server Members Intent is required. "
            "Placeholders: {user}, {server}, {name}.",
            font=ctk.CTkFont(size=10), text_color="#8995b3", anchor="w",
            wraplength=420,
        ).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 4))
        self._feature_welcome_entry = ctk.CTkEntry(
            welcome_frame, placeholder_text="Welcome, {user}, to {server}!"
        )
        self._feature_welcome_entry.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
        self._feature_welcome_entry.bind(
            "<KeyRelease>", lambda _event: self._feature_update_welcome_preview()
        )
        self._feature_welcome_enabled = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            welcome_frame, text="Send welcome message on member join",
            variable=self._feature_welcome_enabled,
            text_color="#aab5ce", fg_color="#5865f2", hover_color="#4752c4",
        ).grid(row=3, column=0, sticky="w", padx=10, pady=4)
        ctk.CTkButton(
            welcome_frame, text="Save welcome settings",
            command=self._feature_save_welcome_settings,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=4, column=0, sticky="ew", padx=10, pady=(4, 9))
        self._feature_welcome_preview = ctk.CTkLabel(
            welcome_frame, text="Preview: your message will appear here.",
            font=ctk.CTkFont(size=10), text_color="#8995b3",
            anchor="w", justify="left", wraplength=420,
        )
        self._feature_welcome_preview.grid(
            row=5, column=0, sticky="ew", padx=10, pady=(0, 9)
        )

        self._feature_quiet_button = ctk.CTkButton(
            utilities, text="Enable quiet mode",
            command=self._feature_toggle_quiet_mode,
            fg_color="#765a36", hover_color="#5e472b",
        )
        self._feature_quiet_button.grid(
            row=9, column=0, sticky="ew", padx=(16, 6), pady=5
        )
        ctk.CTkButton(
            utilities, text="Export redacted diagnostics…",
            command=self._feature_export_diagnostics,
            fg_color="#293653", hover_color="#354567",
        ).grid(row=9, column=1, sticky="ew", padx=(6, 16), pady=5)
        self._feature_shortcut_label = ctk.CTkLabel(
            utilities,
            text="Shortcuts: Ctrl+R reload feed · Ctrl+F search · Ctrl+Enter reply · Ctrl+Q quiet mode",
            font=ctk.CTkFont(size=10), text_color="#8995b3", anchor="w",
        )
        self._feature_shortcut_label.grid(
            row=10, column=0, columnspan=2, sticky="ew", padx=16, pady=(5, 12)
        )

        feed_parent = self.feed_frame.master
        for child in feed_parent.winfo_children():
            row = child.grid_info().get("row")
            if isinstance(row, int) and row >= 3:
                child.grid_configure(row=row + 1)
        self._feature_feed_search = ctk.CTkEntry(
            feed_parent, placeholder_text="Filter messages by text, author, or attachment"
        )
        self._feature_feed_search.grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=5
        )
        self._feature_feed_search.bind(
            "<KeyRelease>", lambda _event: self._feature_filter_feed()
        )
        self._feature_feed_search.bind("<Control-f>", self._feature_focus_search)
        self.root.bind("<Control-r>", self._feature_shortcut_reload)
        self.root.bind("<Control-f>", self._feature_focus_search)
        self.root.bind("<Control-Return>", self._feature_shortcut_reply)
        self.root.bind("<Control-q>", self._feature_shortcut_quiet)
        self._feature_refresh_activity()
        self._feature_refresh_commands()
        section_options = sorted(self._feature_section_frames) or ["No sections"]
        self._feature_layout_menu.configure(values=section_options)
        self._feature_layout_menu.set(section_options[0])

    def close_window(self) -> None:
        if hasattr(self, "_feature_notes_text"):
            self._feature_save_note()
        original_close_window(self)

    def announcement_mode_changed(self, selection: str) -> None:
        self._feature_announcement_mode = selection
        is_selected_dm = selection == "DM selected members"
        is_role_dm = selection == "DM all members with selected role"
        is_all_members_dm = selection == "DM all server members"
        for widget, visible in (
            (self.announcement_member_search, is_selected_dm),
            (self.announcement_find_members_button, is_selected_dm),
            (self.announcement_member_menu, is_selected_dm),
            (self.announcement_add_member_button, is_selected_dm),
            (self.announcement_member_status, is_selected_dm),
            (self.announcement_recipient_list, is_selected_dm),
            (self.announcement_role_menu, is_role_dm),
            (self.announcement_limit_menu, is_role_dm or is_all_members_dm),
            (self.announcement_role_status, is_role_dm),
            (self.announcement_all_members_status, is_all_members_dm),
        ):
            if visible:
                widget.grid()
            else:
                widget.grid_remove()
        if not is_selected_dm:
            self.announcement_message.focus_set()
        self.announcement_send_button.configure(
            text=(
                "Send selected DMs…"
                if is_selected_dm else
                "Send role DMs…"
                if is_role_dm else
                "Send all-member DMs…"
                if is_all_members_dm else
                "Post in selected channel…"
            )
        )
        self._feature_update_announcement_preview()

    def search_announcement_members(self) -> None:
        if not self.client or not self.client.is_ready():
            self.announcement_member_status.configure(
                text="Connect the bot and select a server first."
            )
            return
        guild = self.client.get_guild(self.selected_guild_id) if self.selected_guild_id else None
        if guild is None:
            self.announcement_member_status.configure(
                text="Select a server the bot has joined first."
            )
            return
        query = self.announcement_member_search.get().strip().casefold()
        members = [
            member for member in guild.members
            if not member.bot and (
                not query
                or query in member.name.casefold()
                or query in member.display_name.casefold()
                or query in str(member.id)
            )
        ]
        members.sort(key=lambda member: (member.display_name.casefold(), member.id))
        matches = members[:50]
        self._feature_announcement_candidates = {
            f"{member.display_name} (@{member.name}) [{member.id}]": member.id
            for member in matches
        }
        options = list(self._feature_announcement_candidates) or ["No matching cached members"]
        self.announcement_member_menu.configure(values=options)
        self.announcement_member_menu.set(options[0])
        self.announcement_member_status.configure(
            text=(
                f"Showing {len(matches)} of {len(members)} matching cached members."
                if members else
                "No matching cached members. Check that Server Members Intent is enabled."
            )
        )

    def selected_announcement_role(self, guild):
        role_id = self.role_options.get(self.announcement_role_menu.get())
        role = guild.get_role(role_id) if role_id is not None else None
        if role is None or role == guild.default_role:
            return None
        return role

    def update_announcement_role_status(self) -> None:
        guild = (
            self.client.get_guild(self.selected_guild_id)
            if self.client and self.selected_guild_id else None
        )
        role = self._feature_selected_announcement_role(guild) if guild else None
        if role is None:
            self.announcement_role_status.configure(
                text="Choose a role to target its non-bot members."
            )
            return
        count = sum(
            not member.bot and role in member.roles
            for member in guild.members
        )
        self.announcement_role_status.configure(
            text=f"Selected role: {role.name} — {count} cached non-bot member(s)."
        )
        self._feature_update_announcement_preview()

    def add_announcement_recipient(self) -> None:
        if self._feature_announcement_mode != "DM selected members":
            return
        label = self.announcement_member_menu.get()
        member_id = self._feature_announcement_candidates.get(label)
        if member_id is None:
            messagebox.showinfo(
                "Select a member", "Find and select a cached server member first.",
                parent=self.root,
            )
            return
        if member_id in self._feature_announcement_recipients:
            return
        if len(self._feature_announcement_recipients) >= 25:
            messagebox.showinfo(
                "Recipient limit", "Select no more than 25 recipients for one send.",
                parent=self.root,
            )
            return
        guild = self.client.get_guild(self.selected_guild_id) if self.client else None
        member = guild.get_member(member_id) if guild else None
        if member is None:
            messagebox.showerror(
                "Member unavailable", "That member is no longer cached for the selected server.",
                parent=self.root,
            )
            return
        self._feature_announcement_recipients[member_id] = member
        self._feature_render_announcement_recipients()
        self._feature_update_announcement_preview()

    def remove_announcement_recipient(self, member_id: int) -> None:
        self._feature_announcement_recipients.pop(member_id, None)
        self._feature_render_announcement_recipients()
        self._feature_update_announcement_preview()

    def render_announcement_recipients(self) -> None:
        for child in self.announcement_recipient_list.winfo_children():
            child.destroy()
        recipients = self._feature_announcement_recipients
        if not recipients:
            ctk.CTkLabel(
                self.announcement_recipient_list,
                text="No recipients selected. Add members individually above.",
                text_color="#8995b3",
            ).pack(anchor="w", padx=8, pady=6)
            return
        for member_id, member in recipients.items():
            row = ctk.CTkFrame(self.announcement_recipient_list, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=2)
            ctk.CTkLabel(
                row, text=f"{member.display_name} (@{member.name}) [{member_id}]",
                text_color="#dce3f5", anchor="w",
            ).pack(side="left", fill="x", expand=True, padx=6)
            ctk.CTkButton(
                row, text="Remove", width=70, height=26,
                fg_color="#293653", hover_color="#354567",
                command=lambda selected_id=member_id:
                    self._feature_remove_announcement_recipient(selected_id),
            ).pack(side="right", padx=4, pady=2)

    def render_announcement_text(self, template: str, guild, member=None, channel=None) -> str:
        replacements = {
            "server": guild.name,
            "channel": getattr(channel, "name", ""),
        }
        if member is not None:
            replacements.update({
                "username": member.name,
                "display_name": member.display_name,
            })
        placeholders = set(re.findall(r"\{([a-z_]+)\}", template))
        unsupported = placeholders.difference(replacements)
        if unsupported:
            names = ", ".join(f"{{{name}}}" for name in sorted(unsupported))
            raise ValueError(f"These placeholders are unavailable here: {names}")
        return re.sub(
            r"\{([a-z_]+)\}",
            lambda match: replacements[match.group(1)],
            template,
        )

    def update_announcement_preview(self) -> None:
        template = self.announcement_message.get("1.0", "end").strip()
        if not template and not (
            self.announcement_embed_title.get().strip()
            or self.announcement_embed_description.get("1.0", "end").strip()
        ):
            self.announcement_preview.configure(
                text="Preview: write a message to see its rendered version."
            )
            return
        member = None
        channel = None
        guild = (
            self.client.get_guild(self.selected_guild_id)
            if self.client and self.selected_guild_id else None
        )
        if guild is None:
            self.announcement_preview.configure(
                text="Preview: connect the bot and select a server."
            )
            return
        try:
            if self._feature_announcement_mode in (
                "DM selected members",
                "DM all members with selected role",
                "DM all server members",
            ):
                member = (
                    next(iter(self._feature_announcement_recipients.values()), None)
                    if self._feature_announcement_mode == "DM selected members"
                    else None
                )
                if self._feature_announcement_mode == "DM all members with selected role":
                    role = self._feature_selected_announcement_role(guild)
                    if role is None:
                        self.announcement_preview.configure(
                            text="Preview: select a role to see a rendered example."
                        )
                        return
                    member = next(
                        (
                            candidate for candidate in guild.members
                            if not candidate.bot and role in candidate.roles
                        ),
                        None,
                    )
                if member is None:
                    member = SimpleNamespace(
                        name="username", display_name="display name"
                    )
                preview = self._feature_render_announcement_text(template, guild, member)
                prefix = f"Preview for {member.display_name}: " if member else "Example preview: "
            else:
                channel = self.client.get_channel(self.selected_channel_id) if self.selected_channel_id else None
                preview = self._feature_render_announcement_text(
                    template, guild, channel=channel
                )
                prefix = f"Preview for #{channel.name}: " if channel else "Preview: "
            content, embed, view = self._feature_build_announcement_payload(
                template,
                guild,
                member,
                channel,
            )
            parts = [prefix + preview] if content else []
            if embed:
                if embed.title:
                    parts.append(f"Embed: {embed.title}")
                if embed.description:
                    parts.append(embed.description)
            if view:
                parts.append("Interactive link button/dropdown included.")
            self.announcement_preview.configure(text=" · ".join(parts)[:500])
        except ValueError as error:
            self.announcement_preview.configure(
                text=f"Preview unavailable: {error}"
            )

    def build_announcement_payload(
        self, template: str, guild, member=None, channel=None, controls=None
    ) -> tuple[str | None, discord.Embed | None, discord.ui.View | None]:
        def control(name: str):
            return getattr(self, name) if controls is None else controls[name]

        content = self._feature_render_announcement_text(
            template, guild, member, channel
        ).strip()
        title_template = control("announcement_embed_title").get().strip()
        description_template = control("announcement_embed_description").get(
            "1.0", "end"
        ).strip()
        has_embed = bool(title_template or description_template)
        embed = None
        if has_embed:
            title = self._feature_render_announcement_text(
                title_template, guild, member, channel
            ) if title_template else None
            description = self._feature_render_announcement_text(
                description_template, guild, member, channel
            ) if description_template else None
            if title and len(title) > 256:
                raise ValueError("Embed titles must be 256 characters or fewer.")
            if description and len(description) > 4096:
                raise ValueError("Embed descriptions must be 4,096 characters or fewer.")
            color_text = control("announcement_embed_color").get().strip()
            color = None
            if color_text:
                normalized_color = color_text.removeprefix("#")
                if not re.fullmatch(r"[0-9a-fA-F]{6}", normalized_color):
                    raise ValueError("Embed color must be a 6-digit hex value like #5865F2.")
                color = discord.Colour(int(normalized_color, 16))
            embed = discord.Embed(title=title, description=description, color=color)

        if not content and embed is None:
            raise ValueError("Add message text or an embed title/description.")
        if content and len(content) > 2000:
            raise ValueError("Discord message text must be 2,000 characters or fewer.")

        button_label = control("announcement_embed_button_label").get().strip()
        button_url = control("announcement_embed_button_url").get().strip()
        if bool(button_label) != bool(button_url):
            raise ValueError("Enter both a link button label and URL, or leave both blank.")
        if button_label and len(button_label) > 80:
            raise ValueError("Link button labels must be 80 characters or fewer.")
        if button_url:
            parsed_url = urlsplit(button_url)
            if parsed_url.scheme != "https" or not parsed_url.netloc:
                raise ValueError("Link button URLs must use HTTPS.")

        choice_text = control("announcement_embed_choices").get().strip()
        choices = [choice.strip() for choice in choice_text.split(",") if choice.strip()]
        if len(choices) > 25:
            raise ValueError("Dropdown menus support at most 25 choices.")
        if any(len(choice) > 100 for choice in choices):
            raise ValueError("Dropdown choices must be 100 characters or fewer.")
        if len({choice.casefold() for choice in choices}) != len(choices):
            raise ValueError("Dropdown choices must be unique.")

        view = None
        if button_label or choices:
            view = discord.ui.View(timeout=86400)
            if button_label:
                view.add_item(discord.ui.Button(
                    label=button_label,
                    style=discord.ButtonStyle.link,
                    url=button_url,
                ))
            if choices:
                class AnnouncementSelect(discord.ui.Select):
                    def __init__(self, options: list[str]) -> None:
                        super().__init__(
                            placeholder="Choose an option",
                            min_values=1,
                            max_values=1,
                            options=[
                                discord.SelectOption(label=choice, value=choice)
                                for choice in options
                            ],
                        )

                    async def callback(self, interaction: discord.Interaction) -> None:
                        await interaction.response.send_message(
                            f"You selected: {self.values[0]}",
                            ephemeral=True,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )

                view.add_item(AnnouncementSelect(choices))
        return content or None, embed, view

    def send_announcement(self) -> None:
        if not self.client or not self.client.is_ready() or not self.loop or not self.loop.is_running():
            messagebox.showinfo(
                "Bot is offline", "Connect the bot before sending an announcement.",
                parent=self.root,
            )
            return
        guild = self.client.get_guild(self.selected_guild_id) if self.selected_guild_id else None
        if guild is None:
            messagebox.showinfo(
                "Select a server", "Select the server for this announcement first.",
                parent=self.root,
            )
            return
        template = self.announcement_message.get("1.0", "end").strip()
        is_selected_dm = self._feature_announcement_mode == "DM selected members"
        is_role_dm = self._feature_announcement_mode == "DM all members with selected role"
        is_all_members_dm = self._feature_announcement_mode == "DM all server members"
        channel = None
        role = None
        recipients = list(self._feature_announcement_recipients.values())
        recipient_limit = 25
        if is_selected_dm:
            if not recipients:
                messagebox.showinfo(
                    "No recipients", "Add at least one member to the selected recipient list.",
                    parent=self.root,
                )
                return
            try:
                for member in recipients:
                    self._feature_build_announcement_payload(template, guild, member)
            except ValueError as error:
                messagebox.showerror("Invalid announcement", str(error), parent=self.root)
                return
            confirmation = (
                f"Send this personalized direct message to {len(recipients)} explicitly "
                f"selected members in {guild.name}? No other members will receive it."
            )
        elif is_role_dm:
            role = self._feature_selected_announcement_role(guild)
            if role is None:
                messagebox.showinfo(
                    "Select a role", "Choose a role from the selected server first.",
                    parent=self.root,
                )
                return
            role_recipients = [
                member for member in guild.members
                if not member.bot and role in member.roles
            ]
            if not role_recipients:
                messagebox.showinfo(
                    "No recipients",
                    "No cached non-bot members have the selected role.",
                    parent=self.root,
                )
                return
            limit_choice = self.announcement_limit_menu.get()
            if limit_choice == "Infinite":
                recipient_limit = len(role_recipients)
            else:
                try:
                    recipient_limit = int(limit_choice)
                except ValueError:
                    messagebox.showerror(
                        "Invalid recipient limit", "Choose 10, 25, 50, or Infinite.",
                        parent=self.root,
                    )
                    return
            recipients = role_recipients[:recipient_limit]
            try:
                for member in recipients:
                    self._feature_build_announcement_payload(template, guild, member)
            except ValueError as error:
                messagebox.showerror("Invalid announcement", str(error), parent=self.root)
                return
            limit_description = (
                "all"
                if limit_choice == "Infinite"
                else f"up to {recipient_limit}"
            )
            confirmation = (
                f"Send this direct message to {limit_description} cached non-bot members "
                f"with the {role.name} role in {guild.name} "
                f"({len(recipients)} recipient(s) in this send)? "
                "Messages will be sent one at a time. Continue?"
            )
        elif is_all_members_dm:
            recipients = [
                member for member in guild.members
                if not member.bot
            ]
            if not recipients:
                messagebox.showinfo(
                    "No cached members",
                    "No non-bot members are currently cached for this server.",
                    parent=self.root,
                )
                return
            limit_choice = self.announcement_limit_menu.get()
            if limit_choice == "Infinite":
                recipient_limit = len(recipients)
            else:
                try:
                    recipient_limit = int(limit_choice)
                except ValueError:
                    messagebox.showerror(
                        "Invalid recipient limit", "Choose 10, 25, 50, or Infinite.",
                        parent=self.root,
                    )
                    return
            recipients = recipients[:recipient_limit]
            try:
                for member in recipients:
                    self._feature_build_announcement_payload(template, guild, member)
            except ValueError as error:
                messagebox.showerror("Invalid announcement", str(error), parent=self.root)
                return
            confirmation = (
                f"Send this direct message to {limit_choice.lower()} cached non-bot "
                f"members in {guild.name} ({len(recipients)} recipient(s) in this send)? "
                "Messages will be sent one at a time. Continue?"
            )
        else:
            channel = self.client.get_channel(self.selected_channel_id) if self.selected_channel_id else None
            if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
                messagebox.showinfo(
                    "Select a text channel",
                    "Choose a text channel from the selected server in the live feed section.",
                    parent=self.root,
                )
                return
            try:
                self._feature_build_announcement_payload(
                    template, guild, channel=channel
                )
            except ValueError as error:
                messagebox.showerror("Invalid announcement", str(error), parent=self.root)
                return
            confirmation = f"Post this announcement in #{channel.name} in {guild.name}?"
        if not template and not (
            self.announcement_embed_title.get().strip()
            or self.announcement_embed_description.get("1.0", "end").strip()
        ):
            messagebox.showinfo(
                "Message is empty",
                "Add message text or an embed title/description before sending.",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "Confirm announcement", confirmation, parent=self.root
        ):
            return
        mode = self._feature_announcement_mode
        self.announcement_send_button.configure(state="disabled", text="Sending…")
        self.announcement_status.configure(text="Sending announcement…")

        async def send() -> tuple[int, list[str]]:
            if self.selected_guild_id != guild.id:
                raise RuntimeError("The selected server changed before sending.")
            if mode == "Post in selected channel":
                bot_member = self._bot_member(guild)
                permissions = channel.permissions_for(bot_member)
                if not permissions.view_channel or not permissions.send_messages:
                    raise PermissionError(
                        "The bot needs View Channel and Send Messages permission in the selected channel."
                    )
                body, embed, view = self._feature_build_announcement_payload(
                    template, guild, channel=channel
                )
                if embed and not permissions.embed_links:
                    raise PermissionError(
                        "The bot also needs Embed Links permission in the selected channel."
                    )
                await channel.send(
                    content=body,
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return 1, []
            sent = 0
            failures = []
            for index, selected in enumerate(recipients):
                if self.selected_guild_id != guild.id:
                    raise RuntimeError("The selected server changed during sending.")
                member = guild.get_member(selected.id)
                if member is None:
                    failures.append(f"{selected.display_name}: no longer in the server")
                    continue
                if is_role_dm and role not in member.roles:
                    failures.append(
                        f"{selected.display_name}: no longer has the selected role"
                    )
                    continue
                body, embed, view = self._feature_build_announcement_payload(
                    template, guild, member
                )
                try:
                    await member.send(
                        content=body,
                        embed=embed,
                        view=view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except (discord.HTTPException, discord.Forbidden) as error:
                    failures.append(f"{member.display_name}: {type(error).__name__}")
                else:
                    sent += 1
                if index < len(recipients) - 1:
                    await asyncio.sleep(1.5 if is_role_dm or is_all_members_dm else 1)
            return sent, failures

        future = asyncio.run_coroutine_threadsafe(send(), self.loop)

        def finished(result) -> None:
            try:
                sent, failures = result.result()
            except Exception as error:
                self.root.after(
                    0, self._feature_announcement_finished, 0,
                    [f"{type(error).__name__}: {error}"],
                )
            else:
                self.root.after(
                    0, self._feature_announcement_finished, sent, failures
                )

        future.add_done_callback(finished)

    def announcement_finished(self, sent: int, failures: list[str]) -> None:
        if self.closing:
            return
        self.announcement_send_button.configure(
            state="normal",
            text=(
                "Send selected DMs…"
                if self._feature_announcement_mode == "DM selected members"
                else "Send role DMs…"
                if self._feature_announcement_mode == "DM all members with selected role"
                else "Send all-member DMs…"
                if self._feature_announcement_mode == "DM all server members"
                else "Post in selected channel…"
            ),
        )
        if failures:
            summary = f"Sent {sent}; {len(failures)} failed. First failure: {failures[0]}"
            color = "#f07878"
        else:
            summary = f"Announcement sent successfully ({sent} recipient/channel)."
            color = "#61d995"
        self.announcement_status.configure(text=summary, text_color=color)
        self._log(summary)

    def toggle_token_visibility(self) -> None:
        self.token_entry.configure(
            show="" if self.token_visible_var.get() else "•"
        )

    def load_settings(self) -> None:
        original_load_settings(self)
        try:
            data = json.loads(self.feature_settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._feature_refresh_profiles()
            self._feature_load_saved_extras({})
            return
        except (OSError, json.JSONDecodeError) as error:
            self._log(f"Could not load bot profiles/custom API settings: {error}")
            self._feature_refresh_profiles()
            return
        if not isinstance(data, dict):
            self._log("Bot profile settings file has an invalid format.")
            self._feature_refresh_profiles()
            return
        profiles = data.get("profiles", [])
        if isinstance(profiles, list):
            for profile in profiles:
                if not isinstance(profile, dict):
                    continue
                name = profile.get("name")
                credential = profile.get("credential")
                if (
                    isinstance(name, str) and name.strip()
                    and len(name.strip()) <= 40
                    and isinstance(credential, str)
                    and re.fullmatch(r"bot-profile-[a-f0-9]{32}", credential)
                ):
                    self.bot_profiles[name.strip()] = credential
        selected = data.get("selected_profile")
        if isinstance(selected, str) and selected in self.bot_profiles:
            self.selected_bot_profile = selected
        base_url = data.get("custom_api_url")
        model = data.get("custom_api_model")
        if isinstance(base_url, str) and base_url.strip():
            self.custom_api_url_entry.insert(0, base_url.strip())
        if isinstance(model, str) and model.strip():
            self.custom_api_model_entry.insert(0, model.strip())
        provider = data.get("ai_provider")
        if provider in ("Groq", "GitHub Copilot", "Custom API"):
            self.ai_provider_menu.set(provider)
            self.ai_provider = provider
        try:
            self._feature_api_key = self.credential_store.get_password(
                PROFILE_SERVICE, CUSTOM_API_USERNAME
            )
        except keyring.errors.KeyringError as error:
            self._log(f"Could not load custom API key from Credential Manager: {error}")
        if self._feature_api_key:
            self.custom_api_key_entry.configure(
                placeholder_text="Custom API key saved securely"
            )
        self._feature_refresh_profiles()
        self._feature_load_saved_extras(data)

    def save_settings(self) -> None:
        original_save_settings(self)
        if not hasattr(self, "feature_settings_path"):
            return
        data = {
            "profiles": [
                {"name": name, "credential": credential}
                for name, credential in self.bot_profiles.items()
            ],
            "selected_profile": self.selected_bot_profile,
            "custom_api_url": self.custom_api_url_entry.get().strip(),
            "custom_api_model": self.custom_api_model_entry.get().strip(),
            "ai_provider": self.ai_provider,
            "notes": getattr(self, "_feature_server_notes", {}),
            "welcome_enabled": self._feature_welcome_enabled.get(),
            "welcome_message": self._feature_welcome_entry.get(),
            "welcome_channel_id": self._feature_welcome_channel_id,
            "collapsed_sections": sorted(self._feature_collapsed_sections),
        }
        try:
            self.feature_settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.feature_settings_path.write_text(
                json.dumps(data, indent=2), encoding="utf-8"
            )
        except OSError as error:
            self._log(f"Could not save bot profiles/custom API settings: {error}")

    def load_saved_extras(self, data: dict[str, Any]) -> None:
        notes = data.get("notes", {})
        self._feature_server_notes = (
            {str(key): str(value) for key, value in notes.items()
             if isinstance(key, str) and isinstance(value, str)}
            if isinstance(notes, dict) else {}
        )
        welcome_message = data.get("welcome_message", "")
        if isinstance(welcome_message, str):
            self._feature_welcome_entry.insert(0, welcome_message[:500])
        self._feature_welcome_enabled.set(bool(data.get("welcome_enabled", False)))
        welcome_channel_id = data.get("welcome_channel_id")
        self._feature_welcome_channel_id = (
            welcome_channel_id if isinstance(welcome_channel_id, int) else None
        )
        collapsed = data.get("collapsed_sections", [])
        if isinstance(collapsed, list):
            self._feature_collapsed_sections = {
                name for name in collapsed
                if isinstance(name, str) and name in self._feature_section_frames
            }
        self._feature_refresh_activity()
        self._feature_refresh_commands()
        self._feature_update_welcome_preview()
        self._feature_load_note()
        self.root.after(100, self._feature_apply_saved_layout)

    def refresh_activity(self) -> None:
        for key, label in self._feature_counter_labels.items():
            label.configure(text=f"{self.activity_counters.get(key, 0):,}")
        lines = [
            f"{timestamp}  {message}"
            for timestamp, message in self.activity_events[-8:]
        ]
        self._feature_activity_text.configure(state="normal")
        self._feature_activity_text.delete("1.0", "end")
        self._feature_activity_text.insert(
            "end", "\n".join(lines) if lines else "No activity recorded yet."
        )
        self._feature_activity_text.see("end")
        self._feature_activity_text.configure(state="disabled")

    def refresh_commands(self) -> None:
        tree = getattr(self, "command_tree", None)
        commands = tree.get_commands() if tree else []
        rows = []
        for command in commands:
            description = command.description or "No description"
            rows.append(f"/{command.name} — {description}")
            if command.name in ("kick", "ban"):
                rows.append("  Permission: server moderator permission and bot role hierarchy.")
            else:
                rows.append("  Permission: dashboard interaction-access rules apply.")
            parameters = getattr(command, "parameters", ())
            required_parameters = [
                parameter for parameter in parameters if parameter.required
            ]
            example = f"/{command.name}"
            if required_parameters:
                example += " " + " ".join(
                    f"{parameter.name}:<value>"
                    for parameter in required_parameters
                )
            rows.append(f"  Example: {example}")
            for parameter in parameters:
                required = "required" if parameter.required else "optional"
                rows.append(f"  {parameter.name} ({required}) — {parameter.description}")
        if commands:
            rows.insert(0, "Double-click an example line to copy it.")
        if not rows:
            rows.append("Connect the bot to load registered slash commands.")
        self._feature_command_text.configure(state="normal")
        self._feature_command_text.delete("1.0", "end")
        self._feature_command_text.insert("end", "\n".join(rows))
        self._feature_command_text.configure(state="disabled")

    def toggle_section(self) -> None:
        title = self._feature_layout_menu.get()
        frame = self._feature_section_frames.get(title)
        if frame is None:
            return
        if title in self._feature_collapsed_sections:
            frame.grid(**self._feature_section_grid[title])
            self._feature_collapsed_sections.remove(title)
        else:
            frame.grid_remove()
            self._feature_collapsed_sections.add(title)
        self._feature_save_settings()

    def reset_layout(self) -> None:
        for title, frame in self._feature_section_frames.items():
            frame.grid(**self._feature_section_grid[title])
        self._feature_collapsed_sections.clear()
        self._feature_save_settings()

    def apply_saved_layout(self) -> None:
        for title in tuple(self._feature_collapsed_sections):
            frame = self._feature_section_frames.get(title)
            if frame is not None:
                frame.grid_remove()

    def save_note(self) -> None:
        if self._feature_note_save_after_id is not None:
            self.root.after_cancel(self._feature_note_save_after_id)
            self._feature_note_save_after_id = None
        guild_id = self._feature_note_guild_id or self.selected_guild_id
        if guild_id is None:
            self._feature_notes_status.configure(
                text="Select a server before saving notes.", text_color="#f4c76b"
            )
            return
        notes = getattr(self, "_feature_server_notes", {})
        text = self._feature_notes_text.get("1.0", "end").strip()
        if text:
            notes[str(guild_id)] = text[:4000]
        else:
            notes.pop(str(guild_id), None)
        self._feature_server_notes = notes
        self._feature_save_settings()
        self._feature_notes_status.configure(
            text="Notes saved locally.", text_color="#61d995"
        )

    def schedule_note_save(self) -> None:
        if self._feature_note_save_after_id is not None:
            self.root.after_cancel(self._feature_note_save_after_id)
        self._feature_note_save_after_id = self.root.after(
            500, self._feature_save_note
        )

    def load_note(self) -> None:
        self._feature_notes_text.delete("1.0", "end")
        guild_id = self.selected_guild_id
        self._feature_note_guild_id = guild_id
        if guild_id is None:
            self._feature_notes_status.configure(
                text="Select a server to view its local notes.", text_color="#8995b3"
            )
            return
        note = getattr(self, "_feature_server_notes", {}).get(str(guild_id), "")
        if note:
            self._feature_notes_text.insert("1.0", note)
        self._feature_notes_status.configure(
            text=f"Local notes for server ID {guild_id}.", text_color="#8995b3"
        )

    def save_welcome_settings(self) -> None:
        message = self._feature_welcome_entry.get().strip()
        if self._feature_welcome_enabled.get() and not message:
            messagebox.showinfo(
                "Welcome message needed",
                "Enter a welcome message or turn off the welcome-message option.",
                parent=self.root,
            )
            return
        if len(message) > 500:
            messagebox.showerror(
                "Welcome message too long",
                "Welcome messages must be 500 characters or fewer.",
                parent=self.root,
            )
            return
        if self._feature_welcome_enabled.get() and not self.selected_channel_id:
            messagebox.showinfo(
                "Select a channel",
                "Choose the destination text channel in the live feed section first.",
                parent=self.root,
            )
            return
        self._feature_welcome_channel_id = self.selected_channel_id
        self._feature_save_settings()
        target = (
            self.channel_menu.get()
            if self._feature_welcome_channel_id else "no channel selected"
        )
        self._log(f"Welcome settings saved ({target}).")

    def update_welcome_preview(self) -> None:
        template = self._feature_welcome_entry.get().strip()
        preview = (
            template.replace("{user}", "@NewMember")
            .replace("{server}", "Your Server")
            .replace("{name}", "NewMember")
            if template else "Preview: your message will appear here."
        )
        self._feature_welcome_preview.configure(text=f"Preview: {preview[:500]}")

    def toggle_quiet_mode(self) -> None:
        self.quiet_mode = not self.quiet_mode
        if self.quiet_mode:
            self._quiet_previous_ai_mode = self.ai_replies_enabled
            self.ai_replies_enabled = False
            if self.voice_listening:
                self._toggle_voice_listening()
            self._feature_quiet_button.configure(
                text="Disable quiet mode", fg_color="#a83f52", hover_color="#863244"
            )
            self._log("Quiet mode enabled; automatic AI and voice listening are paused.")
        else:
            self.ai_replies_enabled = self._quiet_previous_ai_mode
            self.ai_mode_menu.set(
                "AI auto-reply" if self.ai_replies_enabled else "Manual replies"
            )
            self._feature_quiet_button.configure(
                text="Enable quiet mode", fg_color="#765a36", hover_color="#5e472b"
            )
            self._log("Quiet mode disabled; voice listening remains off until restarted.")
        self._save_app_settings()
        self.ai_status_label.configure(
            text="Quiet mode is on; automatic replies are paused."
            if self.quiet_mode else
            "Quiet mode is off. Voice listening remains opt-in."
        )

    async def welcome_member(self, member: discord.Member) -> None:
        if not self._feature_welcome_enabled.get() or not self._feature_welcome_entry.get().strip():
            return
        channel_id = self._feature_welcome_channel_id
        if channel_id is None:
            return
        channel = self.client.get_channel(channel_id) if self.client else None
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != member.guild.id:
            return
        template = self._feature_welcome_entry.get().strip()
        content = (
            template.replace("{user}", member.mention)
            .replace("{server}", member.guild.name)
            .replace("{name}", member.display_name)
        )[:2000]
        try:
            await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions(
                    users=[member], roles=False, everyone=False
                ),
            )
        except discord.HTTPException as error:
            self.root.after(
                0, self._log, f"Welcome message failed ({type(error).__name__})."
            )
        else:
            self.root.after(0, self._log, "Welcome message sent for a new member.")

    def filter_feed(self) -> None:
        query = self._feature_feed_search.get().strip().casefold()
        visible_count = 0
        for message, card in self.feed_cards:
            attachment_text = " ".join(
                attachment.filename for attachment in message.attachments
            )
            searchable = " ".join((
                message.author.display_name,
                message.author.name,
                message.clean_content,
                attachment_text,
            )).casefold()
            if not query or query in searchable:
                card.pack(fill="x", padx=5, pady=4)
                visible_count += 1
            else:
                card.pack_forget()
        if not self.feed_cards and self.feed_empty_label.winfo_exists():
            self.feed_empty_label.pack(anchor="w", padx=10, pady=10)
        elif self.feed_empty_label.winfo_exists():
            self.feed_empty_label.pack_forget()
        if query and visible_count == 0:
            self.feed_status_label.configure(text="No messages match this filter.")
        elif query:
            self.feed_status_label.configure(text=f"Showing {visible_count} matching message(s).")
        else:
            self.feed_status_label.configure(text="")

    def open_message(self, message: discord.Message) -> None:
        if not webbrowser.open(message.jump_url):
            self._log("Could not open the Discord message link in a browser.")

    def copy_command_example(self, event=None):
        if event is None:
            return "break"
        index = self._feature_command_text.index(f"@{event.x},{event.y}")
        line = self._feature_command_text.get(f"{index} linestart", f"{index} lineend")
        prefix = "  Example: "
        if not line.startswith(prefix):
            return "break"
        self.root.clipboard_clear()
        self.root.clipboard_append(line[len(prefix):])
        self._log("Copied slash-command example.")
        return "break"

    def focus_feed_search(self, _event=None):
        self._feature_feed_search.focus_set()
        return "break"

    def shortcut_reload(self, _event=None):
        self._reload_channel_history()
        return "break"

    def shortcut_reply(self, _event=None):
        self.reply_entry.focus_set()
        self._reply_to_selected()
        return "break"

    def shortcut_quiet(self, _event=None):
        self._feature_toggle_quiet_mode()
        return "break"

    def export_diagnostics(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export redacted Orbit diagnostics",
            defaultextension=".json",
            filetypes=[("JSON file", "*.json")],
            initialfile="orbit-diagnostics.json",
        )
        if not path:
            return
        client = self.client
        data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "connection": self.status_value.cget("text"),
            "latency_ms": round(client.latency * 1000) if client and client.is_ready() else None,
            "server_count": len(client.guilds) if client and client.is_ready() else 0,
            "uptime_seconds": (
                max(0, int(time.monotonic() - self.started_at))
                if self.started_at is not None else 0
            ),
            "session_counters": dict(self.activity_counters),
            "voice_connected": bool(self.voice_client and self.voice_client.is_connected()),
            "ai_provider": self.ai_provider,
            "ai_auto_reply_enabled": self.ai_replies_enabled,
            "quiet_mode": self.quiet_mode,
            "redaction": "No tokens, API keys, server names, message content, or user IDs are included.",
        }
        try:
            Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as error:
            self._log(f"Could not export diagnostics: {error}")
            messagebox.showerror(
                "Export failed", f"Could not save diagnostics: {error}", parent=self.root
            )
            return
        self._log("Exported redacted diagnostics.")

    def refresh_profiles(self) -> None:
        options = list(self.bot_profiles) or ["No saved profiles"]
        self.profile_menu.configure(values=options)
        if self.selected_bot_profile not in self.bot_profiles:
            self.selected_bot_profile = options[0] if self.bot_profiles else None
        self.profile_menu.set(self.selected_bot_profile or options[0])

    def select_profile(self, name: str) -> None:
        self.selected_bot_profile = name if name in self.bot_profiles else None
        self._feature_save_settings()

    def save_profile(self) -> None:
        name = self.profile_name_entry.get().strip()
        token = self.token_entry.get().strip()
        if not name or len(name) > 40:
            messagebox.showerror(
                "Invalid profile name", "Enter a profile name of 1 to 40 characters."
            )
            return
        if not token:
            messagebox.showinfo(
                "Bot token needed", "Enter the bot token before saving a profile."
            )
            return
        credential = self.bot_profiles.get(name) or f"{PROFILE_PREFIX}{uuid4().hex}"
        try:
            self.credential_store.set_password(PROFILE_SERVICE, credential, token)
        except keyring.errors.KeyringError as error:
            self._log(f"Could not save bot profile '{name}': {error}")
            messagebox.showerror(
                "Could not save profile",
                "Windows Credential Manager could not store this bot token.",
            )
            return
        self.bot_profiles[name] = credential
        self.selected_bot_profile = name
        self.profile_name_entry.delete(0, "end")
        self._feature_refresh_profiles()
        self._feature_save_settings()
        self._log(f"Saved bot profile '{name}' securely.")

    def cycle_profile(self, direction: int) -> None:
        profiles = list(self.bot_profiles)
        if not profiles:
            messagebox.showinfo("No bot profiles", "Save a bot token as a profile first.")
            return
        try:
            index = profiles.index(self.selected_bot_profile or "")
        except ValueError:
            index = -1 if direction > 0 else 0
        name = profiles[(index + direction) % len(profiles)]
        self.profile_menu.set(name)
        self.selected_bot_profile = name
        self._feature_save_settings()
        self._feature_switch_profile()

    def switch_profile(self) -> None:
        name = self.selected_bot_profile
        credential = self.bot_profiles.get(name or "")
        if not name or not credential:
            messagebox.showinfo("Select a profile", "Save or select a bot profile first.")
            return
        try:
            token = self.credential_store.get_password(PROFILE_SERVICE, credential)
        except keyring.errors.KeyringError as error:
            self._log(f"Could not load bot profile '{name}': {error}")
            messagebox.showerror(
                "Could not load profile",
                "Windows Credential Manager could not access this bot token.",
            )
            return
        if not token:
            messagebox.showerror(
                "Profile token missing", f"No token is saved for profile '{name}'."
            )
            return
        self.token_entry.delete(0, "end")
        self.token_entry.insert(0, token)
        if self.worker and self.worker.is_alive():
            self._stop_bot()
            self.root.after(200, self._feature_start_after_stop)
        else:
            self._feature_start_profile()

    def start_after_stop(self) -> None:
        if self.closing:
            return
        if self.worker and self.worker.is_alive():
            self.root.after(200, self._feature_start_after_stop)
            return
        self._feature_start_profile()

    def start_profile(self) -> None:
        previous = self.remember_token.get()
        try:
            self.remember_token.set(False)
            original_start_bot(self)
        finally:
            self.remember_token.set(previous)

    def delete_profile(self) -> None:
        name = self.selected_bot_profile
        credential = self.bot_profiles.get(name or "")
        if not name or not credential:
            messagebox.showinfo("Select a profile", "Choose a saved bot profile first.")
            return
        if not messagebox.askyesno(
            "Delete bot profile",
            f"Delete the saved token for '{name}' from Windows Credential Manager?",
        ):
            return
        try:
            self.credential_store.delete_password(PROFILE_SERVICE, credential)
        except keyring.errors.PasswordDeleteError:
            self._log(f"No saved token was found for profile '{name}'.")
        except keyring.errors.KeyringError as error:
            self._log(f"Could not delete bot profile '{name}': {error}")
            messagebox.showerror(
                "Could not delete profile",
                "Windows Credential Manager could not remove this token.",
            )
            return
        del self.bot_profiles[name]
        self.selected_bot_profile = None
        self._feature_refresh_profiles()
        self._feature_save_settings()
        self._log(f"Deleted bot profile '{name}'.")

    def api_configuration(self) -> tuple[str, str]:
        base_url = self.custom_api_url_entry.get().strip().rstrip("/")
        model = self.custom_api_model_entry.get().strip()
        parsed = urlsplit(base_url)
        local_http = parsed.scheme == "http" and parsed.hostname in {
            "localhost", "127.0.0.1", "::1",
        }
        if (
            parsed.scheme != "https" and not local_http
            or not parsed.netloc or parsed.username or parsed.password
            or parsed.query or parsed.fragment
        ):
            raise ValueError(
                "Use an HTTPS API base URL (HTTP is allowed only for localhost)."
            )
        if not model or len(model) > 200:
            raise ValueError("Enter a model name between 1 and 200 characters.")
        return base_url, model

    def get_api_key(self) -> str | None:
        entered = self.custom_api_key_entry.get().strip()
        if entered:
            return entered
        return self._feature_api_key

    def save_api_settings(self) -> None:
        try:
            base_url, model = self._feature_api_configuration()
        except ValueError as error:
            messagebox.showerror("Invalid custom API settings", str(error))
            return
        key = self.custom_api_key_entry.get().strip()
        if key:
            try:
                self.credential_store.set_password(
                    PROFILE_SERVICE, CUSTOM_API_USERNAME, key
                )
            except keyring.errors.KeyringError as error:
                self._log(f"Could not save custom API key: {error}")
                messagebox.showerror(
                    "Could not save key",
                    "Windows Credential Manager could not store the custom API key.",
                )
                return
            self._feature_api_key = key
            self.custom_api_key_entry.delete(0, "end")
            self.custom_api_key_entry.configure(
                placeholder_text="Custom API key saved securely"
            )
        if not self._feature_get_api_key():
            messagebox.showinfo(
                "Custom API key needed", "Enter an API key before saving these settings."
            )
            return
        self._feature_save_settings()
        self._log(f"Saved custom API settings for model {model} at {base_url}.")

    async def custom_api_reply(
        self, history: list[dict[str, str]], prompt: str,
        api_key: str | None = None, base_url: str | None = None,
        model: str | None = None,
    ) -> str:
        credential = api_key or self._feature_get_api_key()
        if not credential:
            raise RuntimeError("Save a custom API key in the AI conversation panel.")
        if base_url is None or model is None:
            base_url, model = self._feature_api_configuration()
        endpoint = (
            base_url if base_url.endswith("/chat/completions")
            else f"{base_url}/chat/completions"
        )
        payload = json.dumps({
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a friendly Discord server bot. Keep replies concise and "
                        "respectful. Do not claim to be human, reveal secrets, or perform "
                        "moderation actions. Only provide conversational text."
                    ),
                },
                *history[-10:],
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 250,
            "temperature": 0.7,
        }).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {credential}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        def send_request() -> str:
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                details = error.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(
                    f"Custom API returned HTTP {error.code}: {details}"
                ) from error
            choices = result.get("choices") if isinstance(result, dict) else None
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise RuntimeError("Custom API response did not contain a chat completion.")
            message = choices[0].get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise RuntimeError("Custom API response did not contain text content.")
            return message["content"]

        try:
            return await asyncio.to_thread(send_request)
        except Exception as error:
            safe_error = str(error).replace(credential, "[redacted]")
            if safe_error != str(error):
                raise RuntimeError(safe_error) from error
            raise

    def test_api(self) -> None:
        try:
            base_url, model = self._feature_api_configuration()
        except ValueError as error:
            messagebox.showerror("Invalid custom API settings", str(error))
            return
        key = self._feature_get_api_key()
        if not key:
            messagebox.showinfo(
                "Custom API key needed", "Enter or save an API key before testing."
            )
            return
        self.custom_api_test_button.configure(state="disabled", text="Testing…")
        self.custom_api_status.configure(text="Testing custom API endpoint and model…")

        def test() -> None:
            try:
                asyncio.run(self._feature_custom_api_reply(
                    [], "Reply with exactly: Custom API connection works.",
                    api_key=key, base_url=base_url, model=model,
                ))
            except Exception as error:
                detail = str(error).replace(key, "[redacted]")
                message = f"Custom API test failed ({type(error).__name__}): {detail}"
                self.root.after(0, self._feature_api_test_finished, False, message)
            else:
                self.root.after(
                    0, self._feature_api_test_finished, True,
                    f"Custom API test passed using {model}.",
                )

        threading.Thread(target=test, daemon=True).start()

    def api_test_finished(self, succeeded: bool, message: str) -> None:
        if self.closing:
            return
        self.custom_api_test_button.configure(state="normal", text="Test custom API")
        self.custom_api_status.configure(
            text=message, text_color="#61d995" if succeeded else "#f07878"
        )
        self._log(message)

    def refresh_stats(self) -> None:
        original_refresh_uptime(self)
        client = self.client
        if client and client.is_ready():
            members = sum(guild.member_count or 0 for guild in client.guilds)
            self.member_count_value.configure(text=f"~{members:,}")
            self.latency_value.configure(text=f"{round(client.latency * 1000)} ms")
        else:
            self.member_count_value.configure(text="—")
            self.latency_value.configure(text="—")

    def guild_changed(self, selection: str) -> None:
        if self._feature_note_guild_id is not None:
            self._feature_save_note()
        original_guild_changed(self, selection)
        self._feature_load_note()
        self._feature_announcement_recipients.clear()
        self._feature_render_announcement_recipients()
        self._feature_announcement_candidates.clear()
        self.announcement_member_menu.configure(
            values=["Search members in the selected server"]
        )
        self.announcement_member_menu.set("Search members in the selected server")
        self.announcement_member_status.configure(
            text="Search among members currently cached for the selected server."
        )
        role_options = dict(self.role_options)
        options = list(role_options) or ["No roles available"]
        self.announcement_role_menu.configure(values=options)
        self.announcement_role_menu.set(options[0])
        self._feature_update_announcement_role_status()
        self._feature_update_announcement_preview()

    def add_feed_message(self, message) -> None:
        previous_count = len(self.feed_cards)
        original_add_feed_message(self, message)
        if len(self.feed_cards) == previous_count:
            return
        card = self.feed_cards[-1][1]
        self._feature_bind_message_hover(card, message)
        ctk.CTkButton(
            card, text="Open in Discord", width=110, height=26,
            fg_color="#293653", hover_color="#354567",
            command=lambda selected=message: self._feature_open_message(selected),
        ).pack(anchor="e", padx=8, pady=(0, 4))
        self._feature_filter_feed()

    def bind_message_hover(self, widget, message) -> None:
        widget.bind(
            "<Enter>",
            lambda event, selected=message: self._feature_schedule_user_hover(
                event, selected
            ),
            add="+",
        )
        widget.bind(
            "<Leave>",
            lambda _event: self._feature_hide_user_hover(delay=True),
            add="+",
        )
        widget.bind(
            "<Button-3>",
            lambda event, selected=message: self._feature_show_message_menu(
                event, selected
            ),
            add="+",
        )
        for child in widget.winfo_children():
            self._feature_bind_message_hover(child, message)

    def schedule_user_hover(self, event, message) -> None:
        self._feature_hide_user_hover()
        self._feature_hover_after_id = self.root.after(
            450,
            self._feature_show_user_hover,
            message,
            event.x_root + 14,
            event.y_root + 14,
        )

    def hide_user_hover(self, delay: bool = False) -> None:
        if self._feature_hover_after_id is not None:
            self.root.after_cancel(self._feature_hover_after_id)
            self._feature_hover_after_id = None
        if delay:
            self._feature_hover_after_id = self.root.after(
                650, self._feature_destroy_user_hover
            )
        else:
            self._feature_destroy_user_hover()

    def destroy_user_hover(self) -> None:
        self._feature_hover_after_id = None
        if self._feature_hover_window is not None:
            try:
                if self._feature_hover_window.winfo_exists():
                    self._feature_hover_window.destroy()
            except TclError:
                pass
            self._feature_hover_window = None

    def keep_user_hover(self) -> None:
        if self._feature_hover_after_id is not None:
            self.root.after_cancel(self._feature_hover_after_id)
            self._feature_hover_after_id = None

    def show_user_hover(self, message, x: int, y: int) -> None:
        if self.closing or not message.guild:
            return
        author = message.author
        member = message.guild.get_member(author.id)
        details = [
            f"{author}  ·  {author.display_name}",
            f"User ID: {author.id}",
            f"Account created: {author.created_at.strftime('%Y-%m-%d')}",
        ]
        if member:
            if member.joined_at:
                details.append(f"Joined server: {member.joined_at.strftime('%Y-%m-%d')}")
            details.append(f"Top role: {member.top_role.name}")
            if member.timed_out_until:
                details.append(
                    f"Timed out until: {member.timed_out_until.strftime('%Y-%m-%d %H:%M UTC')}"
                )
        window = ctk.CTkToplevel(self.root)
        self._feature_hover_window = window
        window.overrideredirect(True)
        window.geometry(f"+{x}+{y}")
        window.attributes("-topmost", True)
        card = ctk.CTkFrame(window, corner_radius=10, fg_color="#141d32")
        card.pack(fill="both", expand=True, padx=1, pady=1)
        for index, line in enumerate(details):
            ctk.CTkLabel(
                card,
                text=line,
                anchor="w",
                text_color="#8b9cff" if index == 0 else "#dce3f5",
                font=ctk.CTkFont(size=11, weight="bold" if index == 0 else "normal"),
            ).pack(fill="x", padx=12, pady=(8 if index == 0 else 2, 2))
        window.bind("<Enter>", lambda _event: self._feature_keep_user_hover())
        window.bind(
            "<Leave>", lambda _event: self._feature_hide_user_hover(delay=True)
        )

    def show_message_menu(self, event, message) -> None:
        if self.closing or not message.guild:
            return
        self._select_feed_message(message)
        menu = Menu(self.root, tearoff=False)
        menu.add_command(
            label=f"View {message.author.display_name}",
            command=lambda selected=message: self._feature_show_user_profile(selected),
        )
        menu.add_command(
            label="Copy user ID",
            command=lambda user_id=message.author.id: self._feature_copy_user_id(user_id),
        )
        menu.add_command(
            label="Reply to this message",
            command=lambda selected=message: self._select_feed_message(selected),
        )
        menu.add_command(
            label="Open direct chat…",
            command=lambda selected=message: self._feature_open_dm_chat(selected),
        )
        menu.add_separator()
        menu.add_command(
            label="Timeout…",
            command=lambda selected=message: self._feature_timeout_member(selected),
        )
        menu.add_command(
            label="Kick…",
            command=lambda selected=message: self._feature_kick_member(selected),
        )
        menu.add_command(
            label="Ban…",
            command=lambda selected=message: self._feature_ban_member(selected),
        )
        menu.add_separator()
        menu.add_command(
            label="Unpin message" if message.pinned else "Pin message",
            command=lambda selected=message: self._feature_pin_message(selected),
        )
        menu.add_command(
            label="Delete message…",
            command=lambda selected=message: self._feature_delete_message(selected),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def message_member(self, message):
        if (
            message.guild is None
            or message.guild.id != self.selected_guild_id
            or not self.client
            or not self.client.is_ready()
        ):
            messagebox.showinfo(
                "Server not selected",
                "Select the message's server and connect the bot before using these actions.",
            )
            return None
        return message.guild

    def moderate_member(self, message, action: str, reason: str, minutes: int = 0) -> None:
        guild = self._feature_message_member(message)
        if guild is None:
            return
        target_id = message.author.id
        action_label = action.capitalize()

        async def moderate() -> None:
            bot_member = self._bot_member(guild)
            permissions = bot_member.guild_permissions
            if action == "timeout" and not permissions.moderate_members:
                raise PermissionError(
                    "The bot needs the Moderate Members permission in this server."
                )
            if action == "kick" and not permissions.kick_members:
                raise PermissionError("The bot needs the Kick Members permission.")
            if action == "ban" and not permissions.ban_members:
                raise PermissionError("The bot needs the Ban Members permission.")
            member = await guild.fetch_member(target_id)
            if (
                member.id == guild.owner_id
                or member.id == bot_member.id
                or member.top_role >= bot_member.top_role
            ):
                raise PermissionError(
                    "The bot cannot moderate the server owner, itself, or a member "
                    "at/above its top role."
                )
            if action == "timeout":
                await member.timeout(timedelta(minutes=minutes), reason=reason)
            elif action == "kick":
                await member.kick(reason=reason)
            else:
                await guild.ban(member, reason=reason)

        self._run_guild_action(
            moderate(),
            f"{action_label} requested for {message.author} in {guild.name}.",
        )

    def pin_message(self, message) -> None:
        guild = self._feature_message_member(message)
        if guild is None:
            return

        async def pin() -> None:
            bot_member = self._bot_member(guild)
            if not bot_member.guild_permissions.manage_messages:
                raise PermissionError(
                    "The bot needs Manage Messages permission to pin or unpin messages."
                )
            if message.pinned:
                await message.unpin(reason="Unpinned via Orbit dashboard")
            else:
                await message.pin(reason="Pinned via Orbit dashboard")

        action = "Unpinned" if message.pinned else "Pinned"
        self._run_guild_action(pin(), f"{action} message by {message.author}.")

    def delete_message(self, message) -> None:
        guild = self._feature_message_member(message)
        if guild is None:
            return
        if not messagebox.askyesno(
            "Confirm message deletion",
            f"Delete this message by {message.author} from #{message.channel.name}?\n"
            "This cannot be undone.",
            parent=self.root,
        ):
            return

        async def delete() -> None:
            if not self._bot_member(guild).guild_permissions.manage_messages:
                raise PermissionError(
                    "The bot needs Manage Messages permission to delete messages."
                )
            await message.delete()

        self._run_guild_action(delete(), f"Deleted a message by {message.author}.")

    def copy_user_id(self, user_id: int) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(str(user_id))
        self._log(f"Copied Discord user ID {user_id}.")

    def show_user_profile(self, message) -> None:
        author = message.author
        details = [
            f"Display name: {author.display_name}",
            f"Username: {author}",
            f"User ID: {author.id}",
            f"Account created: {author.created_at.strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        member = message.guild.get_member(author.id) if message.guild else None
        if member:
            if member.joined_at:
                details.append(
                    f"Joined server: {member.joined_at.strftime('%Y-%m-%d %H:%M UTC')}"
                )
            details.append(f"Top role: {member.top_role.name}")
            if member.timed_out_until:
                details.append(
                    f"Timed out until: {member.timed_out_until.strftime('%Y-%m-%d %H:%M UTC')}"
                )
        messagebox.showinfo(
            "Discord user profile", "\n".join(details), parent=self.root
        )

    def ask_moderation_reason(self, title: str, prompt: str) -> str | None:
        reason = simpledialog.askstring(title, prompt, parent=self.root)
        if reason is None:
            return None
        reason = reason.strip() or f"{title} via Orbit dashboard"
        if len(reason) > 400:
            messagebox.showerror(
                "Reason too long", "Moderation reasons must be 400 characters or fewer."
            )
            return None
        return reason

    def timeout_member(self, message) -> None:
        if self._feature_message_member(message) is None:
            return
        minutes = simpledialog.askinteger(
            "Timeout member",
            "Timeout duration in minutes (1–40320):",
            parent=self.root,
            minvalue=1,
            maxvalue=40320,
        )
        if minutes is None:
            return
        reason = self._feature_ask_moderation_reason(
            "Timeout member", f"Reason for timing out {message.author}:"
        )
        if reason is None:
            return
        if not messagebox.askyesno(
            "Confirm timeout",
            f"Timeout {message.author} for {minutes} minute(s)?\nReason: {reason}",
            parent=self.root,
        ):
            return
        self._feature_moderate_member(message, "timeout", reason, minutes)

    def kick_member(self, message) -> None:
        if self._feature_message_member(message) is None:
            return
        reason = self._feature_ask_moderation_reason(
            "Kick member", f"Reason for kicking {message.author}:"
        )
        if reason is None:
            return
        if not messagebox.askyesno(
            "Confirm kick",
            f"Kick {message.author} from {message.guild.name}?\nReason: {reason}",
            parent=self.root,
        ):
            return
        self._feature_moderate_member(message, "kick", reason)

    def ban_member(self, message) -> None:
        if self._feature_message_member(message) is None:
            return
        reason = self._feature_ask_moderation_reason(
            "Ban member", f"Reason for banning {message.author}:"
        )
        if reason is None:
            return
        if not messagebox.askyesno(
            "Confirm ban",
            f"Ban {message.author} from {message.guild.name}?\n"
            f"This is permanent until manually reversed.\nReason: {reason}",
            parent=self.root,
        ):
            return
        self._feature_moderate_member(message, "ban", reason)

    def open_dm_chat(self, message) -> None:
        guild = self._feature_message_member(message)
        if guild is None:
            return
        target_id = message.author.id
        existing = self._feature_dm_windows.get(target_id)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return
            except TclError:
                pass

        window = ctk.CTkToplevel(self.root)
        window.title(f"Direct chat · {message.author}")
        window.geometry("520x620")
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(0, weight=1)
        transcript = ctk.CTkTextbox(
            window, fg_color="#0e1526", text_color="#dce3f5",
            wrap="word", state="disabled",
        )
        transcript.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=12, pady=12)
        entry = ctk.CTkEntry(
            window,
            placeholder_text=f"Send a direct message to {message.author.display_name}",
        )
        entry.grid(row=1, column=0, sticky="ew", padx=(12, 6), pady=(0, 12))
        send_button = ctk.CTkButton(
            window, text="Send", fg_color="#5865f2", hover_color="#4752c4"
        )
        send_button.grid(row=1, column=1, padx=(6, 12), pady=(0, 12))
        embed_panel = ctk.CTkFrame(window, corner_radius=10, fg_color="#141d32")
        embed_panel.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))
        embed_panel.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(
            embed_panel, text="Optional embed and menus",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#c3cce1",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 3))
        chat_embed_title = ctk.CTkEntry(
            embed_panel, placeholder_text="Embed title", height=32,
        )
        chat_embed_title.grid(row=1, column=0, sticky="ew", padx=(10, 5), pady=4)
        chat_embed_color = ctk.CTkEntry(
            embed_panel, placeholder_text="Color: #5865F2", height=32,
        )
        chat_embed_color.grid(row=1, column=1, sticky="ew", padx=(5, 10), pady=4)
        chat_embed_description = ctk.CTkTextbox(
            embed_panel, height=70, wrap="word",
            fg_color="#0e1526", text_color="#dce3f5",
        )
        chat_embed_description.grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=4
        )
        chat_button_label = ctk.CTkEntry(
            embed_panel, placeholder_text="Link button label", height=32,
        )
        chat_button_label.grid(row=3, column=0, sticky="ew", padx=(10, 5), pady=4)
        chat_button_url = ctk.CTkEntry(
            embed_panel, placeholder_text="https:// button URL", height=32,
        )
        chat_button_url.grid(row=3, column=1, sticky="ew", padx=(5, 10), pady=4)
        chat_choices = ctk.CTkEntry(
            embed_panel, placeholder_text="Dropdown choices (comma-separated)",
            height=32,
        )
        chat_choices.grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 8)
        )
        chat_controls = {
            "announcement_embed_title": chat_embed_title,
            "announcement_embed_color": chat_embed_color,
            "announcement_embed_description": chat_embed_description,
            "announcement_embed_button_label": chat_button_label,
            "announcement_embed_button_url": chat_button_url,
            "announcement_embed_choices": chat_choices,
        }
        self._feature_dm_windows[target_id] = window

        def append_line(line: str) -> None:
            if not window.winfo_exists():
                return
            transcript.configure(state="normal")
            transcript.insert("end", line + "\n")
            transcript.see("end")
            transcript.configure(state="disabled")

        self._feature_dm_appenders[target_id] = append_line

        def send_message() -> None:
            content = entry.get().strip()
            if len(content) > 2000:
                messagebox.showerror(
                    "Message too long", "Discord messages must be 2,000 characters or fewer.",
                    parent=window,
                )
                return
            try:
                self._feature_build_announcement_payload(
                    content, guild, message.author, controls=chat_controls
                )
            except ValueError as error:
                messagebox.showerror(
                    "Invalid message or embed", str(error), parent=window
                )
                return
            if not self.loop or not self.loop.is_running():
                messagebox.showinfo(
                    "Bot is offline", "Connect the bot before sending a direct message.",
                    parent=window,
                )
                return
            entry.delete(0, "end")
            send_button.configure(state="disabled", text="Sending…")

            async def send_dm() -> None:
                if guild.id != self.selected_guild_id:
                    raise PermissionError(
                        "The selected server changed; reopen the chat from its message."
                    )
                member = await guild.fetch_member(target_id)
                body, embed, view = self._feature_build_announcement_payload(
                    content, guild, member, controls=chat_controls
                )
                await member.send(
                    content=body,
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            future = asyncio.run_coroutine_threadsafe(send_dm(), self.loop)

            def finished(result) -> None:
                if self.closing:
                    return
                try:
                    error = result.exception()
                except Exception as caught:
                    error = caught
                if error is None:
                    summary = content or f"[Embed: {chat_embed_title.get().strip() or 'message'}]"
                    self.root.after(0, append_line, f"You: {summary}")
                    self.root.after(
                        0, self._log,
                        f"Direct message sent to {message.author} in {guild.name}.",
                    )
                else:
                    self.root.after(
                        0, append_line,
                        f"Could not send: {type(error).__name__}: {error}",
                    )
                    self.root.after(
                        0, self._log,
                        f"Direct message failed ({type(error).__name__}): {error}",
                    )
                if not self.closing:
                    self.root.after(
                        0, lambda: send_button.configure(state="normal", text="Send")
                        if window.winfo_exists() else None,
                    )

            future.add_done_callback(finished)

        send_button.configure(command=send_message)
        entry.bind("<Return>", lambda _event: send_message())

        def close_chat() -> None:
            self._feature_dm_windows.pop(target_id, None)
            self._feature_dm_appenders.pop(target_id, None)
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", close_chat)

    async def receive_dm_message(self, message) -> None:
        if self.closing or message.guild is not None or message.author.bot:
            return
        append_line = self._feature_dm_appenders.get(message.author.id)
        if append_line is None:
            return
        content = message.clean_content.strip() or "(message has no text content)"
        if message.attachments:
            content += f"  [{len(message.attachments)} attachment(s)]"
        self.root.after(
            0, append_line, f"{message.author.display_name}: {content}"
        )

    dashboard_class._build_ui = build_ui
    dashboard_class._guild_changed = guild_changed
    dashboard_class._close_window = close_window
    dashboard_class._feature_toggle_token_visibility = toggle_token_visibility
    dashboard_class._load_app_settings = load_settings
    dashboard_class._save_app_settings = save_settings
    dashboard_class._copilot_chat_reply = (
        lambda self, history, prompt:
            self._feature_custom_api_reply(history, prompt)
            if self.ai_provider == "Custom API"
            else original_copilot_reply(self, history, prompt)
    )
    dashboard_class._refresh_uptime = refresh_stats
    dashboard_class._add_feed_message = add_feed_message
    dashboard_class._feature_refresh_profiles = refresh_profiles
    dashboard_class._feature_select_profile = select_profile
    dashboard_class._feature_save_profile = save_profile
    dashboard_class._feature_cycle_profile = cycle_profile
    dashboard_class._feature_switch_profile = switch_profile
    dashboard_class._feature_start_after_stop = start_after_stop
    dashboard_class._feature_start_profile = start_profile
    dashboard_class._feature_delete_profile = delete_profile
    dashboard_class._feature_api_configuration = api_configuration
    dashboard_class._feature_get_api_key = get_api_key
    dashboard_class._feature_save_api_settings = save_api_settings
    dashboard_class._feature_custom_api_reply = custom_api_reply
    dashboard_class._feature_test_api = test_api
    dashboard_class._feature_api_test_finished = api_test_finished
    dashboard_class._feature_save_settings = save_settings
    dashboard_class._feature_announcement_mode_changed = announcement_mode_changed
    dashboard_class._feature_search_announcement_members = search_announcement_members
    dashboard_class._feature_selected_announcement_role = selected_announcement_role
    dashboard_class._feature_update_announcement_role_status = update_announcement_role_status
    dashboard_class._feature_add_announcement_recipient = add_announcement_recipient
    dashboard_class._feature_remove_announcement_recipient = remove_announcement_recipient
    dashboard_class._feature_render_announcement_recipients = render_announcement_recipients
    dashboard_class._feature_render_announcement_text = render_announcement_text
    dashboard_class._feature_build_announcement_payload = build_announcement_payload
    dashboard_class._feature_update_announcement_preview = update_announcement_preview
    dashboard_class._feature_send_announcement = send_announcement
    dashboard_class._feature_announcement_finished = announcement_finished
    dashboard_class._feature_bind_message_hover = bind_message_hover
    dashboard_class._feature_load_saved_extras = load_saved_extras
    dashboard_class._feature_refresh_activity = refresh_activity
    dashboard_class._feature_refresh_commands = refresh_commands
    dashboard_class._feature_toggle_section = toggle_section
    dashboard_class._feature_reset_layout = reset_layout
    dashboard_class._feature_apply_saved_layout = apply_saved_layout
    dashboard_class._feature_save_note = save_note
    dashboard_class._feature_schedule_note_save = schedule_note_save
    dashboard_class._feature_load_note = load_note
    dashboard_class._feature_save_welcome_settings = save_welcome_settings
    dashboard_class._feature_update_welcome_preview = update_welcome_preview
    dashboard_class._feature_toggle_quiet_mode = toggle_quiet_mode
    dashboard_class._feature_send_welcome = welcome_member
    dashboard_class._feature_filter_feed = filter_feed
    dashboard_class._feature_open_message = open_message
    dashboard_class._feature_copy_command_example = copy_command_example
    dashboard_class._feature_focus_search = focus_feed_search
    dashboard_class._feature_shortcut_reload = shortcut_reload
    dashboard_class._feature_shortcut_reply = shortcut_reply
    dashboard_class._feature_shortcut_quiet = shortcut_quiet
    dashboard_class._feature_export_diagnostics = export_diagnostics
    dashboard_class._feature_schedule_user_hover = schedule_user_hover
    dashboard_class._feature_hide_user_hover = hide_user_hover
    dashboard_class._feature_destroy_user_hover = destroy_user_hover
    dashboard_class._feature_keep_user_hover = keep_user_hover
    dashboard_class._feature_show_user_hover = show_user_hover
    dashboard_class._feature_show_message_menu = show_message_menu
    dashboard_class._feature_message_member = message_member
    dashboard_class._feature_moderate_member = moderate_member
    dashboard_class._feature_ask_moderation_reason = ask_moderation_reason
    dashboard_class._feature_timeout_member = timeout_member
    dashboard_class._feature_kick_member = kick_member
    dashboard_class._feature_ban_member = ban_member
    dashboard_class._feature_open_dm_chat = open_dm_chat
    dashboard_class._feature_receive_dm_message = receive_dm_message
    dashboard_class._feature_show_user_profile = show_user_profile
    dashboard_class._feature_copy_user_id = copy_user_id
    dashboard_class._feature_pin_message = pin_message
    dashboard_class._feature_delete_message = delete_message
