import logging
import os
import random
import sys
import sqlite3
import json
from datetime import datetime
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

print("🚀 STARTING BOT...", flush=True)

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TELEGRAM_TOKEN")
GAME_NAME = "Tribes: Last Standing"
YOUR_TELEGRAM_ID = 6950162933  # Твой ID

print(f"🔑 Token loaded: {'YES' if TOKEN else 'NO'}", flush=True)

# ========== ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect("game.db")
    c = conn.cursor()
    
    # Таблица игроков
    c.execute("""CREATE TABLE IF NOT EXISTS players (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        games_played INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        registered_at TIMESTAMP
    )""")
    
    # Таблица игр
    c.execute("""CREATE TABLE IF NOT EXISTS games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TIMESTAMP,
        winner_race TEXT,
        winner_id INTEGER,
        players TEXT,
        room_id TEXT
    )""")
    
    conn.commit()
    conn.close()
    print("✅ Database initialized", flush=True)

init_db()

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

# ========== РАСЫ ==========
RACES = {
    "human": {
        "name": "👤 Human",
        "emoji": "👤",
        "special": "+20% growth"
    },
    "elf": {
        "name": "🧝 Elf",
        "emoji": "🧝",
        "special": "5% turn steal"
    },
    "demon": {
        "name": "👹 Demon",
        "emoji": "👹",
        "special": "No reproduction"
    },
    "beast": {
        "name": "🐺 Beastfolk",
        "emoji": "🐺",
        "special": "10% rebellion"
    }
}

# ========== КОМАНДЫ БОТА ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Сохраняем игрока
    conn = sqlite3.connect("game.db")
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO players (user_id, username, registered_at) VALUES (?, ?, ?)",
        (update.effective_user.id, update.effective_user.username, datetime.now())
    )
    conn.commit()
    conn.close()
    
    keyboard = [
        [InlineKeyboardButton("⚔️ New Game", callback_data="new_game")],
        [InlineKeyboardButton("📊 My Stats", callback_data="my_stats")],
        [InlineKeyboardButton("⚖️ Balance", callback_data="balance")]
    ]
    await update.message.reply_text(
        f"⚔️ <b>Welcome to {GAME_NAME}!</b>\n\n"
        f"4 players enter. 1 leaves.\n\n"
        f"🔹 <b>Races:</b>\n"
        f"👤 Human – balanced, faster growth\n"
        f"🧝 Elf – high faith, can steal turns\n"
        f"👹 Demon – high damage, but no kids\n"
        f"🐺 Beastfolk – tanky, but always rebel\n\n"
        f"Ready? Hit <b>New Game</b>!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статистику побед всех рас"""
    conn = sqlite3.connect("game.db")
    c = conn.cursor()
    
    # Общее количество игр
    c.execute("SELECT COUNT(*) FROM games")
    total_games = c.fetchone()[0]
    
    if total_games == 0:
        await update.message.reply_text(
            "📊 <b>Balance Report</b>\n\n"
            "No games played yet. Start playing to see statistics!",
            parse_mode="HTML"
        )
        conn.close()
        return
    
    text = "📊 <b>BALANCE REPORT</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    
    for race_id, race_data in RACES.items():
        c.execute("SELECT COUNT(*) FROM games WHERE winner_race = ?", (race_id,))
        wins = c.fetchone()[0]
        
        winrate = (wins / total_games * 100) if total_games > 0 else 0
        
        if winrate > 27:
            emoji = "🔥 IMBА"
        elif winrate < 20:
            emoji = "💩 МУСОР"
        else:
            emoji = "✅ НОРМ"
        
        text += f"{race_data['emoji']} <b>{race_data['name']}</b>\n"
        text += f"  Wins: {wins}\n"
        text += f"  Winrate: {winrate:.1f}% {emoji}\n\n"
    
    conn.close()
    
    await update.message.reply_text(text, parse_mode="HTML")

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статистику игрока"""
    user_id = update.effective_user.id
    
    conn = sqlite3.connect("game.db")
    c = conn.cursor()
    
    c.execute("SELECT games_played, wins FROM players WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    
    if not result:
        await update.message.reply_text("You haven't played any games yet!")
        conn.close()
        return
    
    games_played, wins = result
    winrate = (wins / games_played * 100) if games_played > 0 else 0
    
    # Статистика по расам (какой расой чаще побеждает)
    c.execute("SELECT winner_race, COUNT(*) FROM games WHERE winner_id = ? GROUP BY winner_race", (user_id,))
    race_wins = c.fetchall()
    
    text = f"📊 <b>Your Stats</b>\n"
    text += f"━━━━━━━━━━━━━━━━\n"
    text += f"Games played: {games_played}\n"
    text += f"Wins: {wins}\n"
    text += f"Winrate: {winrate:.1f}%\n\n"
    
    if race_wins:
        text += "<b>Wins by race:</b>\n"
        for race, count in race_wins:
            text += f"{RACES[race]['emoji']} {RACES[race]['name']}: {count}\n"
    
    conn.close()
    
    await update.message.reply_text(text, parse_mode="HTML")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if not context.args:
        await update.message.reply_text(
            "🐛 <b>Report a bug</b>\n\n"
            "Please describe the issue you found:\n"
            "<code>/report [your message]</code>\n\n"
            "Example: <code>/report I clicked New Game and nothing happened</code>",
            parse_mode="HTML"
        )
        return
    
    bug_text = ' '.join(context.args)
    
    await context.bot.send_message(
        chat_id=YOUR_TELEGRAM_ID,
        text=f"🐞 <b>New Bug Report</b>\n"
             f"From: @{update.effective_user.username or 'No username'} (ID: {user.id})\n"
             f"Message: {bug_text}",
        parse_mode="HTML"
    )
    
    await update.message.reply_text(
        "✅ <b>Thank you!</b> Your bug report has been sent to the developer.",
        parse_mode="HTML"
    )

# ========== ЛОГИКА КОМНАТ (ВРЕМЕННАЯ) ==========
active_rooms = {}

async def new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    room_id = f"room_{random.randint(1000, 9999)}"
    
    # Создаём комнату
    active_rooms[room_id] = {
        "players": [],
        "owner": query.from_user.id
    }
    
    keyboard = [[InlineKeyboardButton("🔌 Join", callback_data=f"join_{room_id}")]]
    
    await query.edit_message_text(
        f"🏆 <b>Room: {room_id}</b>\n\n"
        f"Players (0/4):\n\n"
        f"⏳ Waiting for 4 players...",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def join_room(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    room_id = query.data.replace("join_", "")
    
    if room_id not in active_rooms:
        await query.edit_message_text("❌ Room expired or not found.")
        return
    
    if len(active_rooms[room_id]["players"]) >= 4:
        await query.edit_message_text("❌ Room is full!")
        return
    
    keyboard = []
    for race_id, race_data in RACES.items():
        keyboard.append([InlineKeyboardButton(
            race_data["name"], 
            callback_data=f"race_{room_id}_{race_id}"
        )])
    
    await query.edit_message_text(
        f"🎭 <b>Choose your race for room {room_id}:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def choose_race(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data.split("_")
    room_id = data[1]
    race_id = data[2]
    user_id = query.from_user.id
    
    if room_id not in active_rooms:
        await query.edit_message_text("❌ Room expired.")
        return
    
    # Добавляем игрока
    active_rooms[room_id]["players"].append({
        "user_id": user_id,
        "race": race_id
    })
    
    # Если собралось 4 игрока - записываем игру (временно, победитель рандомный)
    if len(active_rooms[room_id]["players"]) == 4:
        winner = random.choice(active_rooms[room_id]["players"])
        
        # Сохраняем в базу
        conn = sqlite3.connect("game.db")
        c = conn.cursor()
        
        # Запись игры
        c.execute(
            "INSERT INTO games (date, winner_race, winner_id, players, room_id) VALUES (?, ?, ?, ?, ?)",
            (datetime.now(), winner["race"], winner["user_id"], json.dumps(active_rooms[room_id]["players"]), room_id)
        )
        
        # Обновляем статистику игроков
        for player in active_rooms[room_id]["players"]:
            c.execute(
                "UPDATE players SET games_played = games_played + 1 WHERE user_id = ?",
                (player["user_id"],)
            )
            if player["user_id"] == winner["user_id"]:
                c.execute(
                    "UPDATE players SET wins = wins + 1 WHERE user_id = ?",
                    (player["user_id"],)
                )
        
        conn.commit()
        conn.close()
        
        # Удаляем комнату
        del active_rooms[room_id]
        
        await query.edit_message_text(
            f"✅ <b>Game Over!</b>\n\n"
            f"🏆 Winner: {RACES[winner['race']]['name']}",
            parse_mode="HTML"
        )
        return
    
    await query.edit_message_text(
        f"✅ <b>You joined as {RACES[race_id]['name']}!</b>\n\n"
        f"Players: {len(active_rooms[room_id]['players'])}/4\n"
        f"Waiting for others...",
        parse_mode="HTML"
    )

# ========== ЗАПУСК ==========
def run_bot():
    print("🤖 Starting bot in main thread...", flush=True)
    application = Application.builder().token(TOKEN).build()
    
    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("report", report))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("stats", my_stats))
    
    # Callback-кнопки
    application.add_handler(CallbackQueryHandler(new_game, pattern="new_game"))
    application.add_handler(CallbackQueryHandler(my_stats, pattern="my_stats"))
    application.add_handler(CallbackQueryHandler(balance, pattern="balance"))
    application.add_handler(CallbackQueryHandler(join_room, pattern="join_"))
    application.add_handler(CallbackQueryHandler(choose_race, pattern="race_"))
    
    print("✅ Bot started polling...", flush=True)
    application.run_polling()

if __name__ == "__main__":
    import threading
    print("🚀 Starting Flask in background...", flush=True)
    flask_thread = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000))))
    flask_thread.daemon = True
    flask_thread.start()
    run_bot()
