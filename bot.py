import asyncio
import os
import json
import csv
import io
import re
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
# {user_id: {"records": [{"type": "expense"|"income", "category": str, "amount": float, "date": "YYYY-MM-DD"}], "limit": float|None}}
db = {}
pinned_msg_id = None


def get_user_data(user_id: int) -> dict:
    if user_id not in db:
        db[user_id] = {"records": [], "limit": None}
    return db[user_id]


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Отчёт"), KeyboardButton(text="🎯 Лимит")],
            [KeyboardButton(text="📥 Excel (CSV)"), KeyboardButton(text="↩️ Отменить")],
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
                        item.setdefault("date", datetime.now(MSK).strftime("%Y-%m-%d"))
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
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return
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
            inc_totals[cat] = inc_totals.get(cat, 0.0) + item["amount"]
        lines.append("💚 <b>Доходы:</b>")
        for cat, amt in sorted(inc_totals.items(), key=lambda x: -x[1]):
            lines.append(f"  • {cat}: +{fmt_num(amt)}")
        lines.append(f"<b>Итого доходы:</b> +{fmt_num(total_income)}\n")

    total_expense = sum(r["amount"] for r in expenses)
    if expenses:
        exp_totals = {}
        for item in expenses:
            cat = item["category"]
            exp_totals[cat] = exp_totals.get(cat, 0.0) + item["amount"]
        lines.append("🔴 <b>Расходы:</b>")
        for cat, amt in sorted(exp_totals.items(), key=lambda x: -x[1]):
            lines.append(f"  • {cat}: {fmt_num(amt)}")
        lines.append(f"<b>Итого расходы:</b> {fmt_num(total_expense)}\n")

    balance = total_income - total_expense
    lines.append(f"💰 <b>Баланс:</b> {fmt_num(balance)}")

    limit = user_data.get("limit")
    if limit and period == "month":
        left = limit - total_expense
        if left >= 0:
            lines.append(f"🎯 <b>Остаток лимита:</b> {fmt_num(left)} из {fmt_num(limit)}")
        else:
            lines.append(f"⚠️ <b>Превышение лимита:</b> {fmt_num(abs(left))} (лимит {fmt_num(limit)})")

    return "\n".join(lines)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    get_user_data(message.from_user.id)
    text = (
        "Привет! Я помогу вести учёт расходов и доходов.\n\n"
        "<b>Как добавлять записи:</b>\n"
        "• Расход: <code>Кофе 200</code> или <code>200 Кофе</code>\n"
        "• Доход: <code>Зарплата +50000</code> или <code>+50000 Зарплата</code>\n\n"
        "<b>Команды:</b>\n"
        "/limit 25000 — задать месячный лимит\n"
        "/limit 0 — убрать лимит\n"
        "/clear — очистить все записи"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_keyboard())


@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    user_data = get_user_data(message.from_user.id)
    user_data["records"] = []
    await save_backup_to_channel()
    await message.answer("🗑️ Все твои записи очищены.", reply_markup=main_keyboard())


@dp.message(Command("limit"))
async def cmd_limit(message: types.Message):
    user_data = get_user_data(message.from_user.id)
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        lim = user_data.get("limit")
        status = fmt_num(lim) if lim else "<b>не задан</b>"
        await message.answer(
            f"🎯 Текущий месячный лимит: {status}\n\n"
            "Чтобы установить новый, отправь: <code>/limit 25000</code>\n"
            "Чтобы убрать лимит: <code>/limit 0</code>",
            parse_mode="HTML",
        )
        return

    try:
        val = float(parts[1].replace(",", ".").replace(" ", ""))
    except ValueError:
        await message.answer("❌ Укажи сумму числом, например: <code>/limit 25000</code>", parse_mode="HTML")
        return

    if val <= 0:
        user_data["limit"] = None
        await save_backup_to_channel()
        await message.answer("🎯 Месячный лимит убран.")
    else:
        user_data["limit"] = val
        await save_backup_to_channel()
        await message.answer(f"🎯 Месячный лимит установлен: <b>{fmt_num(val)}</b>", parse_mode="HTML")


@dp.message(F.text == "🎯 Лимит")
async def btn_limit(message: types.Message):
    user_data = get_user_data(message.from_user.id)
    lim = user_data.get("limit")
    status = f"<b>{fmt_num(lim)}</b>" if lim else "<b>не задан</b>"
    await message.answer(
        f"🎯 Текущий месячный лимит: {status}\n\n"
        "Чтобы установить новый, отправь: <code>/limit 25000</code>\n"
        "Чтобы убрать лимит: <code>/limit 0</code>",
        parse_mode="HTML",
    )


@dp.message(F.text == "📊 Отчёт")
async def btn_report(message: types.Message):
    text = build_report_text(message.from_user.id, "month")
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=report_inline_keyboard("month"),
    )


@dp.callback_query(F.data.startswith("rep:"))
async def cb_report(callback: types.CallbackQuery):
    period = callback.data.split(":", 1)[1]
    text = build_report_text(callback.from_user.id, period)
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=report_inline_keyboard(period),
        )
    except Exception:
        pass
    await callback.answer()


@dp.message(F.text == "📥 Excel (CSV)")
async def btn_export_csv(message: types.Message):
    user_data = get_user_data(message.from_user.id)
    records = user_data["records"]
    if not records:
        await message.answer("Пока нечего выгружать — записей нет.")
        return

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Дата", "Тип", "Категория", "Сумма"])
    for r in records:
        rec_type = "Доход" if r.get("type") == "income" else "Расход"
        writer.writerow([r.get("date", ""), rec_type, r.get("category", ""), r.get("amount", 0)])

    data = buf.getvalue().encode("utf-8-sig")
    filename = f"finances_{datetime.now(MSK).strftime('%Y-%m-%d')}.csv"
    await message.answer_document(BufferedInputFile(data, filename=filename))


@dp.message(F.text == "↩️ Отменить")
async def btn_undo(message: types.Message):
    user_data = get_user_data(message.from_user.id)
    if not user_data["records"]:
        await message.answer("Отменять нечего — список записей пуст.")
        return

    removed = user_data["records"].pop()
    await save_backup_to_channel()
    sign = "+" if removed.get("type") == "income" else "-"
    await message.answer(
        f"↩️ Удалена последняя запись: <b>{removed['category']}</b> ({sign}{fmt_num(removed['amount'])})",
        parse_mode="HTML",
    )


def parse_entry(text: str):
    text = text.strip()
    # Вариант 1: "Кофе 200" или "Кофе +200"
    m1 = re.match(r"^(.+?)\s+([+-]?\d+(?:[.,]\d+)?)$", text)
    # Вариант 2: "200 Кофе" или "+200 Кофе"
    m2 = re.match(r"^([+-]?\d+(?:[.,]\d+)?)\s+(.+?)$", text)

    if m1:
        category, num_str = m1.group(1).strip(), m1.group(2)
    elif m2:
        num_str, category = m2.group(1), m2.group(2).strip()
    else:
        return None

    rec_type = "income" if num_str.startswith("+") else "expense"
    amount = abs(float(num_str.replace(",", ".")))
    if amount <= 0 or not category:
        return None
    return rec_type, category, amount


@dp.message(F.text)
async def handle_text_entry(message: types.Message):
    parsed = parse_entry(message.text)
    if not parsed:
        await message.answer(
            "Не понял формат. Напиши, например: <code>Кофе 200</code> или <code>Зарплата +50000</code>",
            parse_mode="HTML",
        )
        return

    rec_type, category, amount = parsed
    user_data = get_user_data(message.from_user.id)
    now = datetime.now(MSK)
    user_data["records"].append({
        "type": rec_type,
        "category": category,
        "amount": amount,
        "date": now.strftime("%Y-%m-%d"),
    })
    await save_backup_to_channel()

    if rec_type == "income":
        await message.answer(f"💚 Доход записан: <b>{category}</b> +{fmt_num(amount)}", parse_mode="HTML")
    else:
        reply = f"✅ Расход записан: <b>{category}</b> {fmt_num(amount)}"
        limit = user_data.get("limit")
        if limit:
            prefix = now.strftime("%Y-%m")
            month_spent = sum(
                r["amount"]
                for r in user_data["records"]
                if r.get("type", "expense") == "expense" and r.get("date", "").startswith(prefix)
            )
            left = limit - month_spent
            if left >= 0:
                reply += f"\n🎯 Остаток лимита на месяц: {fmt_num(left)}"
            else:
                reply += f"\n⚠️ Лимит превышен на {fmt_num(abs(left))}"
        await message.answer(reply, parse_mode="HTML")


async def healthcheck(request):
    return web.Response(text="OK")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server listening on port {port}")


async def main():
    await start_web_server()
    await load_history_from_channel()
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())