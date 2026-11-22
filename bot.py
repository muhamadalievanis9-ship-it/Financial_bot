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

# Структура: {user_id: {"records": [{"category": str, "amount": float, "date": "YYYY-MM-DD"}], "limit": float | None}}
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
                # Поддержка старого формата (список) и нового (словарь)
                if isinstance(content, list):
                    for item in content:
                        item.setdefault("date", datetime.now(MSK).strftime("%Y-%m-%d"))
                    db[uid] = {"records": content, "limit": None}
                else:
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

    totals = {}
    for item in filtered:
        cat = item["category"]
        totals[cat] = totals.get(cat, 0.0) + item["amount"]

    grand_total = sum(totals.values())
    sorted_totals = sorted(totals.items(), key=lambda x: x[1], reverse=True)

    lines = [f"📊 <b>Расходы за {title}:</b>\n"]
    for category, amount in sorted_totals:
        percent = (amount / grand_total) * 100
        lines.append(f"• {category}: <b>{amount:,.2f}</b> ({percent:.1f}%)".replace(",", " "))

    lines.append(f"\n💰 <b>Итого: {grand_total:,.2f}</b>".replace(",", " "))

    if period == "month" and user_data.get("limit"):
        limit = user_data["limit"]
        left = limit - grand_total
        if left >= 0:
            lines.append(f"🎯 Остаток от лимита: <b>{left:,.2f}</b> из {limit:,.0f}".replace(",", " "))
        else:
            lines.append(f"⚠️ Перерасход лимита на <b>{abs(left):,.2f}</b>!".replace(",", " "))

    return "\n".join(lines)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    get_user_data(message.from_user.id)
    await message.answer(
        "👋 <b>Финансовый трекер готов к работе.</b>\n\n"
        "Пиши траты в формате: <code>Категория Сумма</code>\n"
        "Например: <code>Кофе 250</code> или <code>Такси 640</code>\n\n"
        "• Задать месячный бюджет: <code>/limit 30000</code>\n"
        "• Сбросить все данные: <code>/clear</code>",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("report"))
@dp.message(F.text == "📊 Отчёт")
async def cmd_report(message: types.Message):
    text = build_report_text(message.from_user.id, "month")
    await message.answer(text, parse_mode="HTML", reply_markup=report_inline_keyboard("month"))


@dp.callback_query(F.data.startswith("rep:"))
async def cb_report_period(callback: types.CallbackQuery):
    period = callback.data.split(":")[1]
    text = build_report_text(callback.from_user.id, period)
    try:
        await callback.message.edit_text(
            text, parse_mode="HTML", reply_markup=report_inline_keyboard(period)
        )
    except Exception:
        pass
    await callback.answer()


@dp.message(F.text == "↩️ Отменить")
@dp.message(Command("undo"))
async def cmd_undo(message: types.Message):
    user_data = get_user_data(message.from_user.id)
    if not user_data["records"]:
        await message.answer("Нечего отменять — список трат пуст.")
        return

    removed = user_data["records"].pop()
    await save_backup_to_channel()
    await message.answer(
        f"↩️ Удалил последнюю запись: <b>{removed['category']}</b> — {removed['amount']:.2f}",
        parse_mode="HTML",
    )


@dp.message(F.text == "🎯 Лимит")
async def btn_limit_hint(message: types.Message):
    user_data = get_user_data(message.from_user.id)
    current = f"{user_data['limit']:,.0f}".replace(",", " ") if user_data.get("limit") else "не задан"
    await message.answer(
        f"🎯 Текущий месячный лимит: <b>{current}</b>\n\n"
        "Чтобы установить новый, отправь: <code>/limit 25000</code>\n"
        "Чтобы убрать лимит: <code>/limit 0</code>",
        parse_mode="HTML",
    )


@dp.message(Command("limit"))
async def cmd_limit(message: types.Message):
    parts = message.text.strip().split()
    if len(parts) != 2:
        await btn_limit_hint(message)
        return
    try:
        val = float(parts[1].replace(",", "."))
        user_data = get_user_data(message.from_user.id)
        if val <= 0:
            user_data["limit"] = None
            await save_backup_to_channel()
            await message.answer("🎯 Месячный лимит отключён.")
        else:
            user_data["limit"] = val
            await save_backup_to_channel()
            await message.answer(f"🎯 Установлен лимит на месяц: <b>{val:,.0f}</b>".replace(",", " "), parse_mode="HTML")
    except ValueError:
        await message.answer("Укажи число, например: <code>/limit 30000</code>", parse_mode="HTML")


@dp.message(F.text == "📥 Excel (CSV)")
@dp.message(Command("export"))
async def cmd_export(message: types.Message):
    user_data = get_user_data(message.from_user.id)
    records = user_data["records"]
    if not records:
        await message.answer("Пока нечего выгружать — записей нет.")
        return

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Дата", "Категория", "Сумма"])
    for r in records:
        writer.writerow([r.get("date", ""), r["category"], str(r["amount"]).replace(".", ",")])

    csv_bytes = output.getvalue().encode("utf-8-sig")
    filename = f"expenses_{datetime.now(MSK).strftime('%Y-%m-%d')}.csv"
    doc = BufferedInputFile(csv_bytes, filename=filename)
    await message.answer_document(doc, caption="📥 Выгрузка всех трат (открывается в Excel и Numbers).")


@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    user_data = get_user_data(message.from_user.id)
    user_data["records"] = []
    await save_backup_to_channel()
    await message.answer("🗑 Все твои записи очищены.")


@dp.message()
async def handle_expense(message: types.Message):
    if not message.text:
        return

    parts = message.text.strip().rsplit(maxsplit=1)
    if len(parts) != 2:
        await message.answer(
            "Не понял формат. Пиши так: <code>Еда 250</code>",
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )
        return

    category_raw, amount_str = parts
    category = category_raw.capitalize()

    try:
        amount = float(amount_str.replace(",", "."))
        if amount <= 0:
            await message.answer("Сумма должна быть больше нуля.")
            return
    except ValueError:
        await message.answer("Не понял сумму. Пример: <code>Такси 320.5</code>", parse_mode="HTML")
        return

    user_data = get_user_data(message.from_user.id)
    today_str = datetime.now(MSK).strftime("%Y-%m-%d")
    user_data["records"].append({
        "category": category,
        "amount": amount,
        "date": today_str,
    })
    await save_backup_to_channel()

    reply_text = f"✅ Записал: <b>{category}</b> — {amount:,.2f}".replace(",", " ")

    if user_data.get("limit"):
        month_prefix = datetime.now(MSK).strftime("%Y-%m")
        month_spent = sum(
            r["amount"] for r in user_data["records"] if r.get("date", "").startswith(month_prefix)
        )
        left = user_data["limit"] - month_spent
        if left >= 0:
            reply_text += f"\n🎯 Осталось на месяц: <b>{left:,.0f}</b>".replace(",", " ")
        else:
            reply_text += f"\n⚠️ Лимит превышен на <b>{abs(left):,.0f}</b>!".replace(",", " ")

    await message.answer(reply_text, parse_mode="HTML", reply_markup=main_keyboard())


async def health_check(request):
    return web.Response(text="Bot is alive!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def main():
    await start_web_server()
    await load_history_from_channel()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
