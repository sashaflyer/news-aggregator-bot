"""Shared authorization check for all bot command handlers."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes


def is_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False
    return chat.id == context.bot_data["authorized_chat_id"]
