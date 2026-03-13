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
# =============================================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ (ВОТ СЮДА!)
# =============================================================================
user_languages = {}  # Хранилище языков пользователей
# =============================================================================

# =============================================================================
# БЛОК 1: НАСТРОЙКИ И ЗАПУСК (всё что нужно поменять один раз)
# =============================================================================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
YOUR_ID = 6950162933  # СЮДА ВСТАВЬ СВОЙ ID
GAME_NAME = "Tribes: Last Standing"

# =============================================================================
# БЛОК 2: БАЗА ДАННЫХ (сохраняет игры, игроков, статистику)
# =============================================================================
def init_db():
    conn = sqlite3.connect("game.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS players (
        user_id INTEGER PRIMARY KEY, username TEXT, games_played INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0, registered_at TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS games (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TIMESTAMP, winner_race TEXT,
        winner_id INTEGER, players TEXT, room_id TEXT)""")
    conn.commit()
    conn.close()

init_db()

# =============================================================================
# БЛОК 3: РАСЫ (со всеми лимитами)
# =============================================================================
RACES = {
    "human": {
        "name": "👤 Human",
        "emoji": "👤",
        "food_limit": 1000,
        "faith_limit": 1000,
        "labor_limit": 1000,
        "health_limit": 1000,
        "bloodlust": 500,  # НЕ улучшается
        "intelligence_limit": 1000,
        "special": "+20% population growth"
    },
    "elf": {
        "name": "🧝 Elf",
        "emoji": "🧝",
        "food_limit": 2000,
        "faith_limit": 2000,
        "labor_limit": 2000,
        "health_limit": 1000,  # исправлено!
        "bloodlust": 500,
        "intelligence_limit": 1000,
        "special": "5% chance to steal enemy's turn"
    },
    "demon": {
        "name": "👹 Demon",
        "emoji": "👹",
        "food_limit": 500,
        "faith_limit": 500,
        "labor_limit": 500,
        "health_limit": 5000,
        "bloodlust": 5000,
        "intelligence_limit": 500,
        "special": "No reproduction"
    },
    "beast": {
        "name": "🐺 Beastfolk",
        "emoji": "🐺",
        "food_limit": 500,
        "faith_limit": 0,  # не улучшается
        "labor_limit": 500,
        "health_limit": 2500,
        "bloodlust": 2500,
        "intelligence_limit": 0,  # не улучшается
        "special": "10% rebellion chance"
    }
}

# =============================================================================
# БЛОК 3.5: КЛАСС ИГРОКА (ресурсы, лимиты, улучшения)
# =============================================================================
class Player:
    def __init__(self, user_id, race_id):
        self.user_id = user_id
        self.race_id = race_id
        race = RACES[race_id]
        
        # Текущие значения (начинаем с макс)
        self.food = race["food_limit"]
        self.faith = race["faith_limit"]
        self.labor = race["labor_limit"]
        self.health = race["health_limit"]
        self.intelligence = race["intelligence_limit"]
        self.bloodlust = race["bloodlust"]  # не меняется
        
        # Лимиты (можно увеличивать)
        self.food_limit = race["food_limit"]
        self.faith_limit = race["faith_limit"]
        self.labor_limit = race["labor_limit"]
        self.health_limit = race["health_limit"]
        self.intelligence_limit = race["intelligence_limit"]
        
        # Специальные ресурсы
        self.depression = 0
        self.hate = 0
        self.money = 0
        self.materials = 0
        self.dev_points = 500  # очки развития за раунд
        
        # Население
        self.population = 100
        self.population_growth = 0  # % от домов
        
        # Хранилище (общее, не меняется)
        self.storage_limit = 10000
        self.storage_used = 0
        
        # Постройки
        self.buildings = []
    
    def upgrade(self, stat, amount):
        """Улучшает лимит ресурса"""
        if stat == "food" and self.food_limit < 10000:
            self.food_limit += amount
            return True
        elif stat == "faith" and self.faith_limit < 10000 and self.faith_limit != 0:
            self.faith_limit += amount
            return True
        elif stat == "labor" and self.labor_limit < 10000:
            self.labor_limit += amount
            return True
        elif stat == "health" and self.health_limit < 10000:
            self.health_limit += amount
            return True
        elif stat == "intelligence" and self.intelligence_limit < 10000 and self.intelligence_limit != 0:
            self.intelligence_limit += amount
            return True
        return False
    
    def add_building(self, building):
        """Добавляет здание"""
        self.buildings.append(building)
        # Тут эффекты зданий
        if building == "house":
            self.population_growth += 1  # +1% за раунд
        elif building == "farm":
            self.food += 50  # сразу +50 еды
        elif building == "church":
            self.faith += 50
        # и так далее
    
    def calculate_food_consumption(self):
        """Сколько еды съедают юниты за раунд"""
        if self.race_id == "demon":
            return 0  # демоны не едят
        elif self.race_id == "elf":
            return self.population // 15  # 15 юнитов = 1 еда
        elif self.race_id == "beast":
            return self.population // 5   # 5 юнитов = 1 еда
        else:  # human
            return self.population // 10  # 10 юнитов = 1 еда
    
    def apply_depression(self):
        """Применяет эффект депрессии"""
        if self.depression > 0:
            self.food = max(0, self.food - self.depression)
            self.faith = max(0, self.faith - self.depression)
            self.labor = max(0, self.labor - self.depression)
            self.health = max(0, self.health - self.depression)
            self.money = max(0, self.money - self.depression)
            self.materials = max(0, self.materials - self.depression)
    
    def cure_depression(self, amount):
        """Лечит депрессию (тратит веру+еду+труд)"""
        cost = amount
        if self.faith >= cost and self.food >= cost and self.labor >= cost:
            self.faith -= cost
            self.food -= cost
            self.labor -= cost
            self.depression = max(0, self.depression - amount)
            return True
        return False
    
    def add_hate(self, amount):
        """Добавляет ненависть"""
        self.hate += amount
    
    def get_crit_chance(self):
        """Шанс крита от ненависти"""
        return self.hate / 100  # 100 hate = 1%
    
    def get_intelligence_crit(self):
        """Шанс крита от интеллекта"""
        return self.intelligence / 100  # 100 int = 1%
    
    def to_dict(self):
        """Для сохранения в JSON"""
        return {
            "user_id": self.user_id,
            "race": self.race_id,
            "food": self.food, "food_limit": self.food_limit,
            "faith": self.faith, "faith_limit": self.faith_limit,
            "labor": self.labor, "labor_limit": self.labor_limit,
            "health": self.health, "health_limit": self.health_limit,
            "intelligence": self.intelligence, "intelligence_limit": self.intelligence_limit,
            "bloodlust": self.bloodlust,
            "depression": self.depression,
            "hate": self.hate,
            "money": self.money,
            "materials": self.materials,
            "dev_points": self.dev_points,
            "population": self.population,
            "population_growth": self.population_growth,
            "buildings": self.buildings
        }
# =============================================================================
# БЛОК 4: ВСПОМОГАТЕЛЬНЫЙ ФЛАСК (только чтобы Render не ругался)
# =============================================================================

app = Flask(__name__)
@app.route('/')
def index(): return '🤖 Bot is running!'
@app.route('/health')
def health(): return 'OK'
# =============================================================================
# БЛОК: ТЕКСТЫ (русский и английский)
# =============================================================================
TEXTS = {
    "en": {
        "welcome": "⚔️ <b>Welcome to {}!</b>\n\n4 players enter. 1 leaves.\n\n"
                   "👤 Human – balanced\n🧝 Elf – high faith\n👹 Demon – high damage\n🐺 Beastfolk – tanky\n\n"
                   "Ready? Hit <b>New Game</b>!",
        "new_game": "⚔️ New Game",
        "my_stats": "📊 My Stats",
        "balance": "⚖️ Balance",
        "language": "🌐 Language"
    },
    "ru": {
        "welcome": "⚔️ <b>Добро пожаловать в {}!</b>\n\n4 игрока заходят. 1 выходит.\n\n"
                   "👤 Человек – сбалансированный\n🧝 Эльф – высокая вера\n👹 Демон – высокий урон\n🐺 Зверолюд – живучий\n\n"
                   "Готов? Жми <b>Новая игра</b>!",
        "new_game": "⚔️ Новая игра",
        "my_stats": "📊 Моя статистика",
        "balance": "⚖️ Баланс",
        "language": "🌐 Язык"
    }
}

# Хранилище языков пользователей (потом заменим на базу данных)
user_languages = {}

# =============================================================================
# БЛОК 5: КОМАНДА START (приветствие и главное меню)
# =============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Сохраняем игрока в базу
    conn = sqlite3.connect("game.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO players (user_id, username, registered_at) VALUES (?, ?, ?)",
              (user_id, update.effective_user.username, datetime.now()))
    conn.commit()
    conn.close()
    
    # Получаем язык пользователя (по умолчанию английский)
    lang = user_languages.get(user_id, "en")
    
    # Клавиатура с кнопками на нужном языке
    keyboard = [
        [InlineKeyboardButton(TEXTS[lang]["new_game"], callback_data="new_game"),
         InlineKeyboardButton(TEXTS[lang]["my_stats"], callback_data="my_stats")],
        [InlineKeyboardButton(TEXTS[lang]["balance"], callback_data="balance")],
        [InlineKeyboardButton(TEXTS[lang]["language"], callback_data="language")]
    ]
    
    # Приветствие на нужном языке
    await update.message.reply_text(
        TEXTS[lang]["welcome"].format(GAME_NAME),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

# =============================================================================
# БЛОК 6: СТАТИСТИКА РАС (тут считается винрейт и решается кто имба)
# =============================================================================
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("game.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM games")
    total_games = c.fetchone()[0]
    
    if total_games == 0:
        await update.message.reply_text("📊 No games yet.", parse_mode="HTML")
        conn.close()
        return
    
    text = "📊 <b>BALANCE REPORT</b>\n"
    for race_id, race_data in RACES.items():
        c.execute("SELECT COUNT(*) FROM games WHERE winner_race = ?", (race_id,))
        wins = c.fetchone()[0]
        winrate = (wins / total_games * 100) if total_games > 0 else 0
        status = "🔥" if winrate > 27 else "💩" if winrate < 20 else "✅"
        text += f"\n{race_data['emoji']} {race_data['name']}: {wins} wins ({winrate:.1f}%) {status}"
    
    conn.close()
    await update.message.reply_text(text, parse_mode="HTML")

# =============================================================================
# БЛОК 7: ЛИЧНАЯ СТАТИСТИКА (сколько игрок сыграл и выиграл)
# =============================================================================
async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("game.db")
    c = conn.cursor()
    c.execute("SELECT games_played, wins FROM players WHERE user_id = ?", (update.effective_user.id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        await update.message.reply_text("You haven't played any games yet!")
        return
    
    games, wins = result
    winrate = (wins / games * 100) if games > 0 else 0
    await update.message.reply_text(
        f"📊 <b>Your Stats</b>\nGames: {games}\nWins: {wins}\nWinrate: {winrate:.1f}%",
        parse_mode="HTML"
    )

# =============================================================================
# БЛОК 8: РЕПОРТЫ (отправка багов тебе в личку)
# =============================================================================
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🐛 Use: /report [your message]", parse_mode="HTML")
        return
    
    await context.bot.send_message(
        chat_id=YOUR_ID,
        text=f"🐞 <b>Bug from @{update.effective_user.username}</b>\n{''.join(context.args)}",
        parse_mode="HTML"
    )
    await update.message.reply_text("✅ Thanks! Bug reported.", parse_mode="HTML")

#  =============================================================================
# БЛОК 9: КОМНАТЫ (создание и управление игровыми комнатами)
#  =============================================================================
active_rooms = {}

async def new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    room_id = f"room_{random.randint(1000, 9999)}"
    active_rooms[room_id] = {"players": []}
    keyboard = [[InlineKeyboardButton("🔌 Join", callback_data=f"join_{room_id}")]]
    await query.edit_message_text(f"🏆 <b>Room {room_id}</b>\n\n0/2 players", 
                                  reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def join_room(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    room_id = query.data.replace("join_", "")
    
    if room_id not in active_rooms:
        await query.edit_message_text("❌ Room expired")
        return
    
    # Показываем кнопки с расами
    keyboard = []
    for race_id in RACES:
        keyboard.append([InlineKeyboardButton(
            RACES[race_id]["name"], 
            callback_data=f"race_{room_id}_{race_id}"
        )])
    
    await query.edit_message_text(
        "🎭 Choose race:", 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode="HTML"
    )

async def choose_race(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    race_id = parts[-1]
    room_id = "_".join(parts[1:-1])
    
    if room_id not in active_rooms:
        await query.edit_message_text("❌ Room expired")
        return
    
    # Создаём игрока с ресурсами
    player = Player(query.from_user.id, race_id)
    active_rooms[room_id]["players"].append(player)   
    
# ... предыдущий код ...

if len(active_rooms[room_id]["players"]) == 2:
    winner = random.choice(active_rooms[room_id]["players"])
    
    # Сохраняем в базу
    conn = sqlite3.connect("game.db")
    c = conn.cursor()
    
    # Вставляем игру
    c.execute("INSERT INTO games (date, winner_race, winner_id, players, room_id) VALUES (?, ?, ?, ?, ?)",
              (datetime.now(), winner.race_id, winner.user_id, json.dumps([p.to_dict() for p in active_rooms[room_id]["players"]]), room_id))
    
# Получаем ID игры
game_id = c.lastrowid

for player in active_rooms[room_id]["players"]:
    c.execute("UPDATE players SET games_played = games_played + 1 WHERE user_id = ?", (player.user_id,))
    if player.user_id == winner.user_id:
        c.execute("UPDATE players SET wins = wins + 1 WHERE user_id = ?", (player.user_id,))
conn.commit()
conn.close()

del active_rooms[room_id]
await query.edit_message_text(f"🎮 Winner: {RACES[winner.race_id]['name']}", parse_mode="HTML")
return

# 👇 ТУТ ИГРОВОЕ МЕНЮ (ЕСЛИ НЕ 4 ИГРОКА)
game_keyboard = [
    [InlineKeyboardButton("🏛 My City", callback_data="my_city"),
     InlineKeyboardButton("⚒ Build", callback_data="build")],
    [InlineKeyboardButton("⚔️ War", callback_data="war"),
     InlineKeyboardButton("📜 Events", callback_data="events")]
]

await query.edit_message_text(
    f"✅ You joined as {RACES[race_id]['name']}!\n\n"
    f"Players: {len(active_rooms[room_id]['players'])}/2\n\n"
    f"<b>Game Menu:</b>",
    reply_markup=InlineKeyboardMarkup(game_keyboard),
    parse_mode="HTML"
)

# =============================================================================
# БЛОК 9.5: МОЙ ГОРОД (показывает ресурсы игрока)
# =============================================================================
async def my_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Находим игрока в активной комнате (если есть)
    user_id = query.from_user.id
    player = None
    
    for room in active_rooms.values():
        for p in room["players"]:
            if p.user_id == user_id:
                player = p
                break
        if player:
            break
    
    if not player:
    await query.edit_message_text("❌ You're not in a game!")
    return
    
    text = f"🏛 <b>Your City</b>\n\n"
    text += f"🍞 Food: {player.food}/{player.food_limit}\n"
    text += f"🙏 Faith: {player.faith}/{player.faith_limit}\n"
    text += f"⚒ Labor: {player.labor}/{player.labor_limit}\n"
    text += f"❤️ Health: {player.health}/{player.health_limit}\n"
    text += f"🧠 Intelligence: {player.intelligence}/{player.intelligence_limit}\n"
    text += f"😔 Depression: {player.depression}\n"
    text += f"😈 Hate: {player.hate}\n"
    text += f"💰 Money: {player.money}\n"
    text += f"📦 Materials: {player.materials}\n"
    text += f"👥 Population: {player.population}\n"
    text += f"🏗 Buildings: {len(player.buildings)}"
    
    await query.edit_message_text(text, parse_mode="HTML")                                                                
# =============================================================================
# БЛОК: ЯЗЫК (обработчик кнопки Language)
# =============================================================================
async def language_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"),
         InlineKeyboardButton("🇷🇺 Русский", callback_data="setlang_ru")]
    ]
    
    await query.edit_message_text(
        "🌐 <b>Select your language:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    lang = query.data.replace("setlang_", "")
    user_id = query.from_user.id
    
    # Сохраняем язык
    user_languages[user_id] = lang
    
    # Подтверждение
    if lang == "en":
        text = "✅ Language set to English"
    else:
        text = "✅ Язык установлен на русский"
    
    await query.edit_message_text(text)

# =============================================================================
# БЛОК 10: ЗАПУСК (бот + фласк в разных потоках)
# =============================================================================
def run_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("stats", my_stats))
    app.add_handler(CallbackQueryHandler(new_game, pattern="new_game"))
    app.add_handler(CallbackQueryHandler(my_stats, pattern="my_stats"))
    app.add_handler(CallbackQueryHandler(balance, pattern="balance"))
    app.add_handler(CallbackQueryHandler(join_room, pattern="join_"))
    app.add_handler(CallbackQueryHandler(choose_race, pattern="race_"))
    app.add_handler(CallbackQueryHandler(language_menu, pattern="language"))
    app.add_handler(CallbackQueryHandler(set_language, pattern="setlang_"))
    app.run_polling() 
if __name__ == "__main__":
    import threading
    flask_thread = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000))))
    flask_thread.daemon = True
    flask_thread.start()
    run_bot()
