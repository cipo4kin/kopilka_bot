import asyncio, os, logging
from aiogram import Bot, Dispatcher, F
from dotenv import load_dotenv
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
import aiosqlite
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
load_dotenv()
logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.getenv("BOT_TOKEN")
session = AiohttpSession(proxy="http://127.0.0.1:10809")
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()
DB_NAME = "finance.db"
class GoalSetup(StatesGroup):
    waiting_for_goal_name = State()
    waiting_for_goal_amount = State()
class TransactionInput(StatesGroup):
    waiting_for_input = State()
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
CREATE TABLE IF NOT EXISTS users (
user_id INTEGER PRIMARY KEY,
goal_name TEXT,
goal_amount REAL)
""")
        await db.execute("""
CREATE TABLE IF NOT EXISTS transactions (
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER,
type TEXT,
amount REAL,
category TEXT,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
""")
        await db.commit()
dashboard_kb = InlineKeyboardMarkup(
    inline_keyboard= [
        [
            InlineKeyboardButton(text="+ Добавить доход", callback_data="add_income"),
            InlineKeyboardButton(text="- Добавить расход", callback_data="add_expense")
        ],
        [
            InlineKeyboardButton(text="📜 История операций", callback_data="show_history")
        ]
    ]
)
history_kb = InlineKeyboardMarkup(
    inline_keyboard = [
        [InlineKeyboardButton(text="🔙 Назад к табло", callback_data="back_to_dashboard")]
    ]
)
async def get_dashboard_text(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:

        async with db.execute("SELECT goal_name, goal_amount FROM users WHERE user_id = ?",(user_id,)) as cur:
            user = await cur.fetchone()
            goal_name, goal_amount = user[0], user[1]
        async with db.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE user_id = ? AND type = 'income'", (user_id,)) as cur:
                total_income = (await cur.fetchone())[0]
        async with db.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE user_id = ? AND type = 'expense'", (user_id,)) as cur:
                total_expense = (await cur.fetchone())[0]
    saved = total_income - total_expense
    percent = (saved / goal_amount * 100) if goal_amount > 0 else 0
    percent_clamped = max(0.0, min(100.0, percent))
    filled = int(percent_clamped // 10)
    empty = 10 - filled
    bar = f"[{'█' * filled}{'░' * empty}] {percent:.1f}%"
    return (
    f"Цель: {goal_name}\n"
    f"Нужно: {goal_amount:,.0f} Р\n\n"
    f"Доходы: {total_income:,.0f} Р\n"
    f"Расходы: {total_expense:,.0f} Р\n\n"
    f"Накоплено: {saved:,.0f} Р\n\n"
    f"Прогресс: {bar}"
    )

@dp.callback_query(F.data == "add_income")
async def add_income_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(TransactionInput.waiting_for_input)
    prompt_msg = await callback.message.answer("Введи сумму дохода и описание через пробел\n(например: 40000 премия):")
    await state.update_data(
        operation_type="income",
        dashboard_message_id=callback.message.message_id,
        prompt_message_id=prompt_msg.message_id
    )   
@dp.callback_query(F.data == "add_expense")
async def add_expense_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(TransactionInput.waiting_for_input)
    prompt_msg = await callback.message.answer("Введи сумму расхода и описание через пробел\n(например: 250 такси):")
    await state.update_data(
        operation_type="expense",
        dashboard_message_id=callback.message.message_id,
        prompt_message_id=prompt_msg.message_id
    )   
@dp.callback_query(F.data == "show_history")
async def show_history_callback(callback: CallbackQuery):
    await callback.answer()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT type, amount, category FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 5",
            (callback.from_user.id,)
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        text = "📜 История операций пока пуста."
    else:
        text = "📜 Последние операции:\n\n"
        for r in rows:
            op_type, amount, category = r[0], r[1], r[2]
            icon = "🟢 +" if op_type == "income" else "🔴 -"
            text += f"{icon} {amount:,.0f} Р — {category}\n"
    await callback.message.edit_text(text, reply_markup=history_kb)
@dp.callback_query(F.data == "back_to_dashboard")
async def back_to_dashboard_callback(callback: CallbackQuery):
    await callback.answer()
    text = await get_dashboard_text(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=dashboard_kb)

  
@dp.message(TransactionInput.waiting_for_input)
async def process_transaction(message: Message, state: FSMContext):
    parts = message.text.strip().split(maxsplit=1)
    try:
        amount = float(parts[0].replace(" ", "").replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Сумма должна быть положительным числом. Попробуй еще раз.")
        return
    category = parts[1] if len(parts) > 1 else "Прочее"
    data = await state.get_data()
    operation_type = data.get("operation_type")
    dashboard_message_id = data.get("dashboard_message_id")
    prompt_message_id = data.get("prompt_message_id")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO transactions (user_id, type, amount, category) VALUES (?,?,?,?)",
            (message.from_user.id, operation_type, amount, category)
        )
        await db.commit()
    await state.clear()
    try:
        if prompt_message_id:
            await bot.delete_message(chat_id=message.chat.id, message_id=prompt_message_id)
        await message.delete()
    except Exception:
        pass
    new_text = await get_dashboard_text(message.from_user.id)
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=dashboard_message_id,
            text=new_text,
            reply_markup=dashboard_kb
        )
    except Exception:
        await message.answer(new_text, reply_markup=dashboard_kb)  
@dp.message(Command("start"))
async def start_cmd(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT goal_name, goal_amount FROM users WHERE user_id = ?",(message.from_user.id,)) as cursor:
            user = await cursor.fetchone()
            if user:
                text = await get_dashboard_text(message.from_user.id)
                await message.answer(text, reply_markup=dashboard_kb)
            else:
                await state.set_state(GoalSetup.waiting_for_goal_name)
                await message.answer("Привет! Я копилка!\nКакая у тебя финансовая цель или мечта?")
@dp.message(GoalSetup.waiting_for_goal_name)
async def goal_name_chosen(message: Message, state: FSMContext):
    await state.update_data(goal_name=message.text)
    await state.set_state(GoalSetup.waiting_for_goal_amount)
    await message.answer("Отличная цель! А какая сумма нужна? (напиши только число, например: 250000)")
@dp.message(GoalSetup.waiting_for_goal_amount)
async def goal_amount_chosen(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(" ", "").replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Пожалуйста, введи корректную сумму числом (больше 0):")
        return
    data = await state.get_data()
    goal_name = data.get("goal_name")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO users (user_id, goal_name, goal_amount) VALUES (?,?,?)",
            (message.from_user.id, goal_name, amount)
        )
        await db.commit()
    await state.clear()
    text = await get_dashboard_text(message.from_user.id)
    await message.answer(text, reply_markup=dashboard_kb)
async def main():
   await init_db()
   await dp.start_polling(bot)



if __name__ == "__main__":
    asyncio.run(main())