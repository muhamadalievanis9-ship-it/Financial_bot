import asyncio
import sqlite3 
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Вставь сюда свой токен от @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# 1. Инициализация базы данных SQLite
conn = sqlite3.connect("finance.db")
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        category TEXT,
        amount REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")
conn.commit()


# 2. Команда /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я твой финансовый трекер.\n"
        "Записывай траты в формате: <b>Категория Сумма</b>\n"
        "Например: <code>Кофе 15</code> или <code>Транспорт 40.5</code>\n\n"
        "Посмотреть итоги: /report",
        parse_mode="HTML"
    )


# 3. Команда /report — сводка по категориям
@dp.message(Command("report"))
async def cmd_report(message: types.Message):
    cursor.execute(
        "SELECT category, SUM(amount) FROM expenses WHERE user_id = ? GROUP BY category",
        (message.from_user.id,)
    )
    rows = cursor.fetchall()

    if not rows:
        await message.answer("Пока нет ни одной записи.")
        return

    total = sum(amount for _, amount in rows)
    lines = ["📊 <b>Твои расходы по категориям:</b>\n"]
    for category, amount in rows:
        share = (amount / total) * 100
        lines.append(f"• {category.capitalize()}: <b>{amount:.2f}</b> ({share:.1f}%)")

    lines.append(f"\n💰 <b>Всего потрачено: {total:.2f}</b>")
    await message.answer("\n".join(lines), parse_mode="HTML")


# 4. Обработка текстовых сообщений с тратами
@dp.message()
async def add_expense(message: types.Message):
    parts = message.text.strip().rsplit(" ", 1)
    if len(parts) != 2:
        await message.answer("Не понял формат. Напиши так: <code>Еда 25</code>", parse_mode="HTML")
        return

    category, amount_str = parts[0].lower(), parts[1].replace(",", ".")
    try:
        amount = float(amount_str)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Сумма должна быть положительным числом. Пример: <code>Такси 32.5</code>", parse_mode="HTML")
        return

    cursor.execute(
        "INSERT INTO expenses (user_id, category, amount) VALUES (?, ?, ?)",
        (message.from_user.id, category, amount)
    )
    conn.commit()
    await message.answer(f"✅ Записал: <b>{category.capitalize()}</b> — <b>{amount:.2f}</b>", parse_mode="HTML")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())