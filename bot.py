import asyncio
import os
import json
import csv
import io
from datetime import datetime, timedelta, timezone
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
STORAGE_CHANNEL_ID = -1004450571164
MSK = timezone(timedelta(hours=3))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Структура:
# {user_id: {"records": [{"type": "expense"|"income", "category": str, "amount": float, "date": "YYYY-MM-DD"}], "limit": float | None}}
db = {}
pinned_msg_id = None


def get_user_data(user_id: int) -> dict:
    if user_id not in db:
        db[user_id] = {"records": [], "limit": None}
    return db[user_id]


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Отчёт"), KeyboardButton(text="↩️ Отменить")],
            [KeyboardButton(text="📥 Excel (CSV)"), KeyboardButton(text="🎯 Лимит")],
        ],
        resize_keyboard=True,
    )


def report_inline_keyboard(active_period: str) -> InlineKeyboardMarkup:
    periods = [
        ("today", "Сегодня"),
        ("week", "7 дней"),
        ("month", "Месяц"),
        ("all", "Всё время"),
    ]
    buttons = [
        InlineKeyboardButton(
            text=f"• {label} •" if key == active_period else label,
            callback_data=f"rep:{key}",
        )
        for key, label in periods
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[:2], buttons[2:]])


async def load_history_from_channel():
    global pinned_msg_id
    try:
        chat = await bot.get_chat(STORAGE_CHANNEL_ID)
        if chat.pinned_message and chat.pinned_message.text:
            pinned_msg_id = chat.pinned_message.message_id
            raw = json.loads(chat.pinned_message.text)
            for uid_str, content in raw.items():
                uid = int(uid_str)
                if isinstance(content, list):
                    for item in content:
                        item.setdefault("type", "expense")
                        item.setdefault("date", datetime.now(MSK).strftime("%Y-%m-%d"))
                    db[uid] = {"records": content, "limit": None}
                else:
                    for item in content.get("records", []):
                        item.setdefault("type", "expense")
                    db[uid] = content
            print("История успешно восстановлена из канала.")
    except Exception as e:
        print(f"Ошибка загрузки истории: {e}")


async def save_backup_to_channel():
    global pinned_msg_id
    payload = json.dumps(db, ensure_ascii=False)
    try:
        if pinned_msg_id:
            await bot.edit_message_text(
                chat_id=STORAGE_CHANNEL_ID,
                message_id=pinned_msg_id,
                text=payload,
            )
            return
    except Exception:
        pinned_msg_id = None

    try:
        msg = await bot.send_message(STORAGE_CHANNEL_ID, payload)
        await bot.pin_chat_message(STORAGE_CHANNEL_ID, msg.message_id, disable_notification=True)
        pinned_msg_id = msg.message_id
    except Exception as e:
        print(f"Ошибка сохранения в канал: {e}")


def fmt_num(val: float, decimals: int = 2) -> str:
    return f"{val:,.{decimals}f}".replace(",", " ")


def build_report_text(user_id: int, period: str = "month") -> str:
    user_data = get_user_data(user_id)
    records = user_data["records"]
    now = datetime.now(MSK)

    if period == "today":
        cutoff = now.strftime("%Y-%m-%d")
        filtered = [r for r in records if r.get("date") == cutoff]
        title = f"сегодня ({now.strftime('%d.%m')})"
    elif period == "week":
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        filtered = [r for r in records if r.get("date", "") >= cutoff]
        title = "последние 7 дней"
    elif period == "month":
        prefix = now.strftime("%Y-%m")
        filtered = [r for r in records if r.get("date", "").startswith(prefix)]
        title = f"текущий месяц ({now.strftime('%m.%Y')})"
    else:
        filtered = records
        title = "всё время"

    if not filtered:
        return f"📊 За <b>{title}</b> записей пока нет."

    incomes = [r for r in filtered if r.get("type") == "income"]
    expenses = [r for r in filtered if r.get("type", "expense") == "expense"]

    lines = [f"📊 <b>Финансы за {title}:</b>\n"]

    total_income = sum(r["amount"] for r in incomes)
    if incomes:
        inc_totals = {}
        for item in incomes:
            cat = item["category"]
            inc_totals[cat] = inc_totals.get(cat, 0.0) + item["amount
