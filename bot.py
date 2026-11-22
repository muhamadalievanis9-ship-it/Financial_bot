import asyncio
import os
import json
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

BOT_TOKEN = os.getenv("BOT_TOKEN")
STORAGE_CHANNEL_ID = -1004450571164

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# В памяти храним словарь: {user_id: [{"category": ..., "amount": ...}, ...]}
expenses_db = {}


def add_record_to_memory(user_id: int, category: str, amount: float):
    expenses_db.setdefault(user_id, []).append({
        "category": category,
        "amount": amount
    })


async def load_history_from_channel():
    """При запуске бота читаем закреплённое сообщение-бэкап из канала"""
    try:
        chat = await bot.get_chat(STORAGE_CHANNEL_ID)
        if chat.pinned_message and chat.pinned_message.text:
            data = json.loads(chat.pinned_message.text)
            for uid_str, records in data.items():
                expenses_db[int(uid_str)] = records
            print("История успешно восстановлена из канала.")
    except Exception as e:
        print(f"Не удалось загрузить историю (возможно, канал пока пуст): {e}")


async def save_backup_to_channel():
    """Сохраняем актуальную базу в канал и закрепляем сообщение"""
    try:
        payload = json.dumps(expenses_db, ensure_ascii=False)
        msg = await bot.send_message(STORAGE_CHANNEL_ID, payload)
        await bot.pin_chat_message(STORAGE_CHANNEL_ID, msg.message_id, disable_notification=True)
    except Exception as e:
        print(f"Ошибка сохранения в канал: {e}")


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я твой финансовый трекер.\n"
        "Записывай траты в формате: <b>Категория Сумма</b>\n"
        "Например: <code>Кофе 150</code> или <code>Транспорт 85.5</code>\n"
        "Посмотреть итоги: /report\n"
        "Очистить всё: /clear",
        parse_mode="HTML"
    )


@dp.message(Command("report"))
async def cmd_report(message: types.Message):
    user_records = expenses_db.get(message.from_user.id, [])
    if not user_records:
        await message.answer("Пока нет ни одной записи.")
        return

    totals = {}
    for item in user_records:
        cat = item["category"]
        totals[cat] = totals.get(cat, 0.0) + item["amount"]

    grand_total = sum(totals.values())
    sorted_totals = sorted(totals.items(), key=lambda x: x[1], reverse=True)

    lines = ["📊 <b>Твои расходы по категориям:</b>\n"]
    for category, amount in sorted_totals:
        percent = (amount / grand_total) * 100
        lines.append(f"• {category}: <b>{amount:.2f}</b> ({percent:.1f}%)")

    lines.append(f"\n💰 <b>Всего потрачено: {grand_total:.2f}</b>")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    expenses_db[message.from_user.id] = []
    await save_backup_to_channel()
    await message.answer("🗑 Все твои записи очищены.")


@dp.message()
async def handle_expense(message: types.Message):
    if not message.text:
        return

    parts = message.text.strip().rsplit(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Не понял формат. Напиши так: <code>Еда 250</code>", parse_mode="HTML")
        return

    category_raw, amount_str = parts
    category = category_raw.capitalize()

    try:
        amount = float(amount_str.replace(",", "."))
        if amount <= 0:
            await message.answer("Сумма должна быть положительным числом.")
            return
    except ValueError:
        await message.answer("Не понял сумму. Пример: <code>Такси 320.5</code>", parse_mode="HTML")
        return

    add_record_to_memory(message.from_user.id, category, amount)
    await save_backup_to_channel()
    await message.answer(f"✅ Записал: <b>{category}</b> — {amount:.2f}", parse_mode="HTML")


# Мини веб-сервер для Render, чтобы сервис не падал и принимал пинги от сна
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
