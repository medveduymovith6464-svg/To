import logging
import os
import random
import json
from datetime import datetime
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_TOKEN")
GAME_NAME = "Tribes: Last Standing"

# ========== ЛОГИ ==========
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== FLASK ДЛЯ RENDER ==========
app = Flask(__name__)

# ========== РАСЫ ==========
RACES = {
    "human": {
        "name": "👤 Human",
        "food": 1000,
        "faith": 1000,
        "labor": 1000,
        "health": 1000,
        "bloodlust": 1000,
        "agility": 1000,
        "intelligence": 1000,
        "special": "+20% population growth"
    },
    "elf": {
        "name": "🧝 Elf",
        "food": 2000,
        "faith": 2000,
        "labor": 2000,
        "health": 500,
        "bloodlust": 200,
        "agility": 500,
        "intelligence": 1000,
        "special": "5% chance to steal enemy's turn"
    },
    "demon": {
        "name": "👹 Demon",
        "food": 500,
        "faith": 500,
        "labor": 500,
        "health": 5000,
        "bloodlust": 5000,
        "agility": 500,
        "intelligence": 500,
        "special": "No reproduction"
    },
    "beast": {
        "name": "🐺 Beastfolk",
        "food": 500,
        "faith": 0,
        "labor": 2500,
        "health": 2500,
        "bloodlust": 2500,
        "agility": 2500,
        "intelligence": 0,
        "special": "Low intelligence, 10% rebellion chance"
    }
}

# ========== КОМАНДЫ БОТА ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    keyboard = [
        [InlineKeyboardButton("⚔️ New Game", callback_data="new_game")],
        [InlineKeyboardButton("📊 My Stats", callback_data="my_stats")]
    ]
    
await update.message.reply_text(
    f"⚔️ Welcome to **Tribes: Last Standing**!\n\n"
    f"4 players enter. 1 leaves.\n\n"
    f"🔹 **Races:**\n"
    f"👤 Human – balanced, faster growth\n"
    f"🧝 Elf – high faith, can steal turns\n"
    f"👹 Demon – high damage, but no kids\n"
    f"🐺 Beastfolk – tanky, but always rebel\n\n"
    f"🔹 **Each round:** +500 resources\n"
    f"🔹 **Build** houses, farms, churches, factories...\n"
    f"🔹 **Watch out for depression** – it eats your resources\n"
    f"🔹 **Hate** gives crit chance in battle\n\n"
    f"⚔️ Last tribe standing wins.\n\n"
    f"Ready? Hit **New Game**!",
    reply_markup=InlineKeyboardMarkup(keyboard),
    parse_mode="Markdown"
)async def new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    room_id = f"room_{random.randint(1000, 9999)}"
    
    keyboard = [[InlineKeyboardButton("🔌 Join", callback_data=f"join_{room_id}")]]
    
    await query.edit_message_text(
        f"🏆 **Room: {room_id}**\n\n"
        f"Players (0/4):\n\n"
        f"⏳ Waiting for 4 players...",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def join_room(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    room_id = query.data.replace("join_", "")
    
    keyboard = []
    for race_id, race_data in RACES.items():
        keyboard.append([InlineKeyboardButton(
            race_data["name"], 
            callback_data=f"race_{room_id}_{race_id}"
        )])
    
    await query.edit_message_text(
        f"🎭 Choose your race for room {room_id}:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def choose_race(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data.split("_")
    room_id = data[1]
    race_id = data[2]
    
    await query.edit_message_text(
        f"✅ You joined as {RACES[race_id]['name']}!\n\n"
        f"Waiting for other players...",
        parse_mode="Markdown"
    )

# ========== FLASK РОУТЫ ==========
@app.route('/')
def index():
    return '🤖 Bot is running!'

@app.route('/health')
def health():
    return 'OK'

# ========== ЗАПУСК ==========
def run_bot():
    global application
    
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(new_game, pattern="new_game"))
    application.add_handler(CallbackQueryHandler(join_room, pattern="join_"))
    application.add_handler(CallbackQueryHandler(choose_race, pattern="race_"))
    
    print("🤖 Bot started polling...")
    application.run_polling()

if __name__ == "__main__":
    import threading
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Flask server running on port {port}")
    app.run(host="0.0.0.0", port=port)
