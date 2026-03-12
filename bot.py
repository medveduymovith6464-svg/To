import logging
import os
import sys
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

print("🚀 STARTING BOT...", flush=True)

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_TOKEN")
GAME_NAME = "Tribes: Last Standing"
print(f"🔑 Token loaded: {'YES' if TOKEN else 'NO'}", flush=True)

# ========== ЛОГИ ==========
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ========== FLASK ДЛЯ RENDER ==========
app = Flask(__name__)

@app.route('/')
def index():
    return '🤖 Bot is running!'

@app.route('/health')
def health():
    return 'OK'

# ========== КОМАНДЫ БОТА ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("⚔️ New Game", callback_data="new_game")],
        [InlineKeyboardButton("📊 My Stats", callback_data="my_stats")]
    ]
    await update.message.reply_text(
        f"⚔️ Welcome to **{GAME_NAME}**!\n\n"
        f"4 players enter. 1 leaves.\n\n"
        f"🔹 **Races:**\n"
        f"👤 Human – balanced, faster growth\n"
        f"🧝 Elf – high faith, can steal turns\n"
        f"👹 Demon – high damage, but no kids\n"
        f"🐺 Beastfolk – tanky, but always rebel\n\n"
        f"Ready? Hit **New Game**!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    room_id = f"room_{random.randint(1000, 9999)}"
    keyboard = [[InlineKeyboardButton("🔌 Join", callback_data=f"join_{room_id}")]]
    await query.edit_message_text(
        f"🏆 **Room: {room_id}**\n\nPlayers (0/4):\n\n⏳ Waiting...",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def join_room(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    room_id = query.data.replace("join_", "")
    keyboard = []
    for race_id, race_data in RACES.items():
        keyboard.append([InlineKeyboardButton(race_data["name"], callback_data=f"race_{room_id}_{race_id}")])
    await query.edit_message_text(f"🎭 Choose your race for room {room_id}:", reply_markup=InlineKeyboardMarkup(keyboard))

async def choose_race(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    room_id, race_id = data[1], data[2]
    await query.edit_message_text(f"✅ You joined as {RACES[race_id]['name']}!\n\nWaiting for other players...", parse_mode="Markdown")

# ========== РАСЫ ==========
RACES = {
    "human": {"name": "👤 Human", "special": "+20% growth"},
    "elf": {"name": "🧝 Elf", "special": "5% turn steal"},
    "demon": {"name": "👹 Demon", "special": "No reproduction"},
    "beast": {"name": "🐺 Beastfolk", "special": "10% rebellion"}
}

# ========== ЗАПУСК ==========
def run_bot():
    print("🤖 Starting bot in main thread...", flush=True)
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(new_game, pattern="new_game"))
    application.add_handler(CallbackQueryHandler(join_room, pattern="join_"))
    application.add_handler(CallbackQueryHandler(choose_race, pattern="race_"))
    print("✅ Bot started polling...", flush=True)
    application.run_polling()

if __name__ == "__main__":
    import threading
    # Запускаем Flask в отдельном потоке, а бота в главном
    print("🚀 Starting Flask in background...", flush=True)
    flask_thread = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000))))
    flask_thread.daemon = True
    flask_thread.start()
    run_bot()
