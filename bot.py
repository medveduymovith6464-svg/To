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
# БЛОК 3.6: ЗДАНИЯ (цены и эффекты)
# =============================================================================
BUILDINGS = {
    # Начальные
    "house": {
        "name": "🏠 House",
        "name_ru": "🏠 Дом",
        "cost": 50,
        "effect": "+1% population/round",
        "round_income": {"population_growth": 1}
    },
    "farm": {
        "name": "🌱 Farm", 
        "name_ru": "🌱 Ферма",
        "cost": 100,
        "effect": "+50 food/round",
        "round_income": {"food": 50}
    },
    "sawmill": {
        "name": "🪵 Sawmill",
        "name_ru": "🪵 Лесопилка",
        "cost": 200, 
        "effect": "+20 materials/round",
        "round_income": {"materials": 20}
    },
    "church": {
        "name": "⛪ Church",
        "name_ru": "⛪ Церковь",
        "cost": 1000,
        "effect": "+50 faith/round",
        "round_income": {"faith": 50}
    },
    
    # Средние
    "forge": {
        "name": "⚒ Forge",
        "name_ru": "⚒ Кузница",
        "cost": 800,
        "effect": "+10 bloodlust/round",  # ← ИСПРАВЛЕНО!
        "round_income": {"bloodlust": 10}
    },
    "laboratory": {
        "name": "🔬 Laboratory",
        "name_ru": "🔬 Лаборатория",
        "cost": 1350,
        "effect": "+20 intelligence/round",
        "round_income": {"intelligence": 20}
    },
    "mine": {
        "name": "🕳 Mine",
        "name_ru": "🕳 Шахта",
        "cost": 1500,
        "effect": "+100 materials/round",
        "round_income": {"materials": 100}
    },
    "taxoffice": {
        "name": "💰 Tax Office",
        "name_ru": "💰 Налоговая",
        "cost": 5000,
        "effect": "+30 money/round",
        "round_income": {"money": 30}
    },
    
    # Элитные
    "library": {
        "name": "📚 Library",
        "name_ru": "📚 Библиотека",
        "cost": 8000,
        "effect": "+50 intelligence/round",
        "round_income": {"intelligence": 50}
    },
    "necropolis": {
        "name": "🪦 Necropolis",
        "name_ru": "🪦 Некрополь",
        "cost": 12222,
        "effect": "Resurrect 10% units after battle",
        "round_income": {}
    },
    
    # Легендарные (20000)
    "sacredgrove": {
        "name": "🌳 Sacred Grove",
        "name_ru": "🌳 Священная роща",
        "cost": 20000,
        "effect": "+1000 health, +500 bloodlust, +1000 faith/round",
        "round_income": {"health": 1000, "bloodlust": 500, "faith": 1000}
    },
    "hell": {
        "name": "🔥 Hell",
        "name_ru": "🔥 Преисподняя",
        "cost": 20000,
        "effect": "+10 population/round",
        "round_income": {"population": 10}
    },
    "bonethrone": {
        "name": "🦴 Bone Throne",
        "name_ru": "🦴 Костяной трон",
        "cost": 20000,
        "effect": "Units don't eat food",
        "round_income": {}
    },
    "steamengine": {
        "name": "⚙ Steam Engine",
        "name_ru": "⚙ Паровая машина",
        "cost": 20000,
        "effect": "+500 dev points/round",
        "round_income": {"dev_points": 500}
    }
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
    
    user_id = query.from_user.id
    creator_lang = user_languages.get(user_id, "en")  # язык создателя
    
    room_id = f"room_{random.randint(1000, 9999)}"
    
    active_rooms[room_id] = {
        "creator": query.from_user.id,
        "chat_id": update.effective_chat.id,
        "stage": "picking",
        "choices": {},
        "allowed": [query.from_user.id],
        "players": [],
        "lang": creator_lang  # сохраняем язык комнаты
    }
    
    # Кнопки выбора расы на языке создателя
    race_keyboard = []
    for race_id in RACES:
        if creator_lang == "en":
            race_name = RACES[race_id]["name"]  # английские названия
        else:
            # русские названия
            race_names = {
                "human": "👤 Человек",
                "elf": "🧝 Эльф",
                "demon": "👹 Демон",
                "beast": "🐺 Зверолюд"
            }
            race_name = race_names.get(race_id, RACES[race_id]["name"])
        
        race_keyboard.append([InlineKeyboardButton(
            race_name, 
            callback_data=f"race_{room_id}_{race_id}"
        )])
    
    # Заголовок
    title = "🎭 Choose your race!" if creator_lang == "en" else "🎭 Выбери свою расу!"
    
    await query.edit_message_text(
        f"🏆 <b>Room {room_id}</b>\n\n"
        f"{title}",
        reply_markup=InlineKeyboardMarkup(race_keyboard),
        parse_mode="HTML"
    )

async def choose_race(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    race_id = parts[-1]
    room_id = "_".join(parts[1:-1])
    
    if room_id not in active_rooms:
        return
    
    if query.from_user.id not in active_rooms[room_id].get("allowed", []):
        return
    
    if query.from_user.id in active_rooms[room_id]["choices"]:
        return
    
    # Сохраняем выбор
    active_rooms[room_id]["choices"][query.from_user.id] = race_id
    
    # Создаём игрока
    player = Player(query.from_user.id, race_id)
    if "players" not in active_rooms[room_id]:
        active_rooms[room_id]["players"] = []
    active_rooms[room_id]["players"].append(player)
    
    # Убираем из допущенных
    if query.from_user.id in active_rooms[room_id]["allowed"]:
        active_rooms[room_id]["allowed"].remove(query.from_user.id)
    
    # 👇 ЕСЛИ ЭТО СОЗДАТЕЛЬ
    if query.from_user.id == active_rooms[room_id]["creator"]:
        room_lang = active_rooms[room_id].get("lang", "en")
        
        # Текст на языке комнаты
        if room_lang == "en":
            wait_text = f"✅ You chose {RACES[race_id]['name']}!\n\n⏳ Waiting for second player..."
        else:
            race_names = {
                "human": "Человек",
                "elf": "Эльф",
                "demon": "Демон",
                "beast": "Зверолюд"
            }
            race_name_ru = race_names.get(race_id, race_id)
            wait_text = f"✅ Ты выбрал {race_name_ru}!\n\n⏳ Ожидание второго игрока..."
        
        sent_msg = await query.edit_message_text(wait_text, parse_mode="HTML")
        active_rooms[room_id]["creator_msg_id"] = sent_msg.message_id
        
        # Кнопка Play на языке комнаты
        play_text = "🎮 Play" if room_lang == "en" else "🎮 Играть"
        play_keyboard = [[InlineKeyboardButton(play_text, callback_data=f"play_{room_id}")]]
        
        join_text = "🎮 A game is waiting! Click PLAY to join!" if room_lang == "en" else "🎮 Игра ждёт! Нажми ИГРАТЬ чтобы присоединиться!"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=join_text,
            reply_markup=InlineKeyboardMarkup(play_keyboard),
            parse_mode="HTML"
        )
        return
    
    # 👇 ЕСЛИ ЭТО ВТОРОЙ ИГРОК
    room_lang = active_rooms[room_id].get("lang", "en")
    
    # Удаляем сообщение создателя (Waiting...)
    if "creator_msg_id" in active_rooms[room_id]:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=active_rooms[room_id]["creator_msg_id"]
            )
        except:
            pass
    
    # Удаляем сообщение с выбором расы у второго
    await query.message.delete()
    
    # Запускаем игру
    await start_game(room_id, context, update.effective_chat.id)

async def build_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    parts = query.data.split("_")
    room_id = "_".join(parts[1:-1])
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    # Берём язык из комнаты
    room_lang = active_rooms[room_id].get("lang", "en")
    
    player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
            break
    
    if not player:
        return
    
    # Русские названия зданий
    building_names_ru = {
        "house": "🏠 Дом",
        "farm": "🌱 Ферма",
        "sawmill": "🪵 Лесопилка",
        "church": "⛪ Церковь",
        "forge": "⚒ Кузница",
        "laboratory": "🔬 Лаборатория",
        "mine": "🕳 Шахта",
        "tax_office": "💰 Налоговая",
        "library": "📚 Библиотека",
        "necropolis": "🪦 Некрополь",
        "sacred_grove": "🌳 Священная роща",
        "hell": "🔥 Преисподняя",
        "bone_throne": "🦴 Костяной трон",
        "steam_engine": "⚙ Паровая машина"
    }
    
    # Кнопки зданий
    buttons = []
    for b_id, b_data in BUILDINGS.items():
        cost_color = "🟢" if player.dev_points >= b_data['cost'] else "🔴"
        
        # Выбираем название здания
        if room_lang == "en":
            building_name = b_data['name']
        else:
            building_name = building_names_ru.get(b_id, b_data['name'])
        
        buttons.append([InlineKeyboardButton(
            f"{building_name} | {cost_color} {b_data['cost']}💰",
            callback_data=f"construct_{room_id}_{b_id}_{target_user_id}"
        )])
    
    # Кнопка назад
    back_text = "🔙 Back" if room_lang == "en" else "🔙 Назад"
    buttons.append([InlineKeyboardButton(back_text, callback_data=f"back_to_game_{room_id}_{target_user_id}")])
    
    # Заголовок меню
    title = "🏗️ Construction Menu" if room_lang == "en" else "🏗️ Меню строительства"
    
    await query.edit_message_text(
        f"{title}\nYour Dev Points: {player.dev_points}💰",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML"
    )
    
async def construct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    lang = user_languages.get(user_id, "en")
    
    parts = query.data.split("_")
    room_id = "_".join(parts[1:-2])
    building_id = parts[-2]
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
            break
    
    if not player:
        return
    
    # 👇 УБИРАЕМ ПРОВЕРКУ ОЧЕРЕДИ!
    # if target_user_id not in active_rooms[room_id].get("allowed", []):
    #     return
    
    building = BUILDINGS.get(building_id)
    if not building:
        return
    
    # Кнопка назад
    back_text = "🔙 Back to Menu" if lang == "en" else "🔙 В меню"
    back_keyboard = [[InlineKeyboardButton(back_text, callback_data=f"back_to_game_{room_id}_{target_user_id}")]]
    
    if player.dev_points < building['cost']:
        if lang == "en":
            text = f"❌ <b>Not enough Dev Points!</b>\nNeed: {building['cost']}\nYou have: {player.dev_points}"
        else:
            text = f"❌ <b>Не хватает очков развития!</b>\nНужно: {building['cost']}\nУ тебя: {player.dev_points}"
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(back_keyboard),
            parse_mode="HTML"
        )
        return
    
    # Проверка на уникальные здания
    unique_buildings = ["sacredgrove", "hell", "bonethrone", "steamengine"]
    if building_id in unique_buildings and building_id in player.buildings:
        if lang == "en":
            text = f"❌ <b>You can only build one {building['name']}!</b>"
        else:
            text = f"❌ <b>Можно построить только одно {building.get('name_ru', building['name'])}!</b>"
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(back_keyboard),
            parse_mode="HTML"
        )
        return
    
    # СТРОИМ
    player.dev_points -= building['cost']
    player.buildings.append(building_id)
    
    if lang == "en":
        building_name = building['name']
        success_text = f"✅ <b>{building_name} built!</b>\nRemaining Dev Points: {player.dev_points}"
    else:
        building_name = building.get('name_ru', building['name'])
        success_text = f"✅ <b>{building_name} построено!</b>\nОсталось очков развития: {player.dev_points}"
    
    await query.edit_message_text(
        success_text,
        reply_markup=InlineKeyboardMarkup(back_keyboard),
        parse_mode="HTML"
    )

async def start_game(room_id, context, chat_id):
    """Запускает игру после выбора обоих игроков"""
    if room_id not in active_rooms:
        return
    
    if len(active_rooms[room_id]["choices"]) != 2:
        return
    
    players = []
    player_usernames = []
    for user_id, race_id in active_rooms[room_id]["choices"].items():
        player = Player(user_id, race_id)
        players.append(player)
        
        # Получаем юзернейм или имя
        try:
            chat_member = await context.bot.get_chat_member(chat_id, user_id)
            username = chat_member.user.username or chat_member.user.first_name or str(user_id)
        except:
            username = str(user_id)
        player_usernames.append(username)
    
    active_rooms[room_id]["players"] = players
    active_rooms[room_id]["turn"] = 1  # номер хода (1,2,3,4...)
    active_rooms[room_id]["round"] = 1  # номер раунда
    active_rooms[room_id]["current_player"] = players[0].user_id
    
    # Определяем язык (по первому игроку)
    lang = user_languages.get(players[0].user_id, "en")
    
    # Тексты с юзернеймами и номером раунда
    if lang == "en":
        start_text = (f"⚔️ <b>GAME STARTED!</b>\n\n"
                     f"📅 Round 1 begins\n"
                     f"👤 Player 1: {players[0].race_id} (@{player_usernames[0]})\n"
                     f"👤 Player 2: {players[1].race_id} (@{player_usernames[1]})\n"
                     f"🎮 <b>{player_usernames[0]}</b>'s turn!")
        my_city_text = "🏛 My City"
        build_text = "⚒ Build"
        war_text = "⚔️ War"
        end_turn_text = "⏭ End Turn"
        income_text = "📊 Income"
    else:
        start_text = (f"⚔️ <b>ИГРА НАЧАЛАСЬ!</b>\n\n"
                     f"📅 Раунд 1 начался\n"
                     f"👤 Игрок 1: {players[0].race_id} (@{player_usernames[0]})\n"
                     f"👤 Игрок 2: {players[1].race_id} (@{player_usernames[1]})\n"
                     f"🎮 Ходит <b>{player_usernames[0]}</b>!")
        my_city_text = "🏛 Мой город"
        build_text = "⚒ Строить"
        war_text = "⚔️ Война"
        end_turn_text = "⏭ Завершить ход"
        income_text = "📊 Доход"
    
    game_keyboard = [
        [InlineKeyboardButton(my_city_text, callback_data=f"mycity_{room_id}_{players[0].user_id}"),
         InlineKeyboardButton(build_text, callback_data=f"build_{room_id}_{players[0].user_id}")],
        [InlineKeyboardButton(war_text, callback_data=f"war_{room_id}_{players[0].user_id}"),
         InlineKeyboardButton(end_turn_text, callback_data=f"endturn_{room_id}_{players[0].user_id}"),
         InlineKeyboardButton(income_text, callback_data=f"income_{room_id}_{players[0].user_id}")]
    ]
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=start_text,
        reply_markup=InlineKeyboardMarkup(game_keyboard),
        parse_mode="HTML"
    )

async def check_game_over(room_id, context):
    """Проверяет, не закончилась ли игра (только для активных игроков)"""
    if room_id not in active_rooms:
        return False
    
    # Берём ТОЛЬКО тех, кто уже в игре (выбрали расу и живы)
    players = active_rooms[room_id].get("players", [])
    
    # Если ещё нет 2 игроков - игра не началась
    if len(players) != 2:
        return False
    
    alive_players = []
    
    for player in players:
        # Проверяем, жив ли игрок
        if player.population <= 0:
            continue  # Мёртв
        if player.food <= 0 and player.race_id != "demon":
            continue  # Умер с голоду (кроме демонов)
        if player.depression >= 1000:
            continue  # Психологическая смерть
        
        alive_players.append(player)
    
    # Если остался только один живой
    if len(alive_players) == 1:
        winner = alive_players[0]
        
        # Отправляем результат ТОЛЬКО живым
        for player in players:
            try:
                if player.user_id == winner.user_id:
                    await context.bot.send_message(
                        chat_id=player.user_id,
                        text=f"🎉 **YOU WIN!**\n"
                             f"Your civilization survives!",
                        parse_mode="HTML"
                    )
                else:
                    # Проигравший уже мёртв, но отправляем ему уведомление
                    await context.bot.send_message(
                        chat_id=player.user_id,
                        text=f"💔 **Game Over**\n"
                             f"Your civilization has fallen.",
                        parse_mode="HTML"
                    )
            except:
                pass
        
        # Сохраняем в базу
        conn = sqlite3.connect("game.db")
        c = conn.cursor()
        players_data = [{"user_id": p.user_id, "race": p.race_id, "alive": (p in alive_players)} for p in players]
        c.execute("INSERT INTO games (date, winner_race, winner_id, players, room_id) VALUES (?, ?, ?, ?, ?)",
                  (datetime.now(), winner.race_id, winner.user_id, json.dumps(players_data), room_id))
        conn.commit()
        conn.close()
        
        del active_rooms[room_id]
        return True
    
    # Если оба мертвы (ничья)
    if len(alive_players) == 0 and len(players) == 2:
        for player in players:
            try:
                await context.bot.send_message(
                    chat_id=player.user_id,
                    text=f"💀 **Draw!**\n"
                         f"Both civilizations destroyed each other.",
                    parse_mode="HTML"
                )
            except:
                pass
        del active_rooms[room_id]
        return True
    
    return False

async def next_round(room_id, context):
    """Начисляет доходы и расходы за раунд"""
    if room_id not in active_rooms:
        return
    
    players = active_rooms[room_id].get("players", [])
    
    for player in players:
        # 1. ДОХОД ОТ ЗДАНИЙ
        for building_id in player.buildings:
            building = BUILDINGS.get(building_id)
            if building and building.get("round_income"):
                for resource, value in building["round_income"].items():
                    if hasattr(player, resource):
                        current = getattr(player, resource)
                        setattr(player, resource, current + value)
        
        # 2. БАЗОВЫЙ ДОХОД ЗА РАУНД (500 очков)
        player.dev_points += 500
        
        # 3. 👇 ПРОЦЕНТНЫЙ РОСТ ОТ ДОМОВ
        if player.population_growth > 0:
            growth = int(player.population * player.population_growth / 100)
            player.population += max(1, growth)  # минимум +1, если есть дома
        
        # 4. РАСХОДЫ (ЕДА)
        food_consumed = player.calculate_food_consumption()
        player.food -= food_consumed
        
        # Если еда ушла в минус - штрафуем население
        if player.food < 0:
            starvation = abs(player.food) // 10 + 1
            player.population = max(0, player.population - starvation)
            player.food = 0
        
        # 5. ДЕПРЕССИЯ
        player.depression += 1
        player.apply_depression()
        
        # 6. ЛИМИТЫ
        player.food = min(player.food, player.food_limit)
        player.faith = min(player.faith, player.faith_limit)
        player.labor = min(player.labor, player.labor_limit)
        player.health = min(player.health, player.health_limit)
        player.intelligence = min(player.intelligence, player.intelligence_limit)
        player.money = min(player.money, 10000)
        player.materials = min(player.materials, 10000)
        player.dev_points = min(player.dev_points, 10000)
        player.population = min(player.population, 10000)
    
    await check_game_over(room_id, context)

async def end_turn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    lang = user_languages.get(user_id, "en")
    
    parts = query.data.split("_")
    room_id = "_".join(parts[1:-1])
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
            break
    
    if not player:
        return
    
    # Тексты подтверждения
    if lang == "en":
        confirm_text = ("⚠️ <b>Are you sure you want to end your turn?</b>\n\n"
                       "Once you end your turn, you won't be able to take any more actions until your next turn.")
        yes_text = "✅ Yes"
        no_text = "❌ No"
        back_text = "🔙 Back"
    else:
        confirm_text = ("⚠️ <b>Ты уверен, что хочешь закончить ход?</b>\n\n"
                       "После завершения хода ты не сможешь делать действия до следующего хода.")
        yes_text = "✅ Да"
        no_text = "❌ Нет"
        back_text = "🔙 Назад"
    
    confirm_keyboard = [
        [InlineKeyboardButton(yes_text, callback_data=f"confirm_endturn_{room_id}_{target_user_id}"),
         InlineKeyboardButton(no_text, callback_data=f"cancel_endturn_{room_id}_{target_user_id}")],
        [InlineKeyboardButton(back_text, callback_data=f"back_to_game_{room_id}_{target_user_id}")]
    ]
    
    await query.edit_message_text(
        text=confirm_text,
        reply_markup=InlineKeyboardMarkup(confirm_keyboard),
        parse_mode="HTML"
    )

async def confirm_endturn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    lang = user_languages.get(user_id, "en")
    
    parts = query.data.split("_")
    room_id = "_".join(parts[2:-1])
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    if room_id not in active_rooms:
        return
    
    player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
            break
    if not player:
        return
    
    other_player = None
    for p in active_rooms[room_id]["players"]:
        if p.user_id != target_user_id:
            other_player = p
            break
    
    if not other_player:
        return
    
    # Получаем юзернеймы
    chat_id = active_rooms[room_id]["chat_id"]
    
    async def get_username(user_id):
        try:
            chat_member = await context.bot.get_chat_member(chat_id, user_id)
            return chat_member.user.username or chat_member.user.first_name or str(user_id)
        except:
            return str(user_id)
    
    current_name = await get_username(target_user_id)
    next_name = await get_username(other_player.user_id)
    
    current_turn = active_rooms[room_id].get("turn", 1)
    current_round = active_rooms[room_id].get("round", 1)
    
    # 👇 ЕСЛИ ЭТО БЫЛ ЧЁТНЫЙ ХОД (2,4,6...) — ЗАКАНЧИВАЕМ РАУНД
    if current_turn % 2 == 0:
        current_round += 1
        active_rooms[room_id]["round"] = current_round
        # 👇 НАЧИСЛЯЕМ РЕСУРСЫ ТОЛЬКО В КОНЦЕ РАУНДА!
        await next_round(room_id, context)
    
    # Увеличиваем номер хода
    active_rooms[room_id]["turn"] = current_turn + 1
    active_rooms[room_id]["allowed"] = [other_player.user_id]
    active_rooms[room_id]["current_player"] = other_player.user_id
    
    # Тексты с номером раунда
    if lang == "en":
        turn_ended_text = (f"🔄 <b>Turn ended!</b>\n\n"
                          f"📅 Round {current_round}\n"
                          f"👤 {current_name} finished their turn.\n"
                          f"🎮 Now <b>{next_name}</b>'s turn!")
        my_city_text = "🏛 My City"
        build_text = "⚒ Build"
        war_text = "⚔️ War"
        end_turn_text = "⏭ End Turn"
        income_text = "📊 Income"
    else:
        turn_ended_text = (f"🔄 <b>Ход закончен!</b>\n\n"
                          f"📅 Раунд {current_round}\n"
                          f"👤 {current_name} завершил ход.\n"
                          f"🎮 Теперь ходит <b>{next_name}</b>!")
        my_city_text = "🏛 Мой город"
        build_text = "⚒ Строить"
        war_text = "⚔️ Война"
        end_turn_text = "⏭ Завершить ход"
        income_text = "📊 Доход"
    
    game_keyboard = [
        [InlineKeyboardButton(my_city_text, callback_data=f"mycity_{room_id}_{other_player.user_id}"),
         InlineKeyboardButton(build_text, callback_data=f"build_{room_id}_{other_player.user_id}")],
        [InlineKeyboardButton(war_text, callback_data=f"war_{room_id}_{other_player.user_id}"),
         InlineKeyboardButton(end_turn_text, callback_data=f"endturn_{room_id}_{other_player.user_id}"),
         InlineKeyboardButton(income_text, callback_data=f"income_{room_id}_{other_player.user_id}")]
    ]

    await query.edit_message_text(
        text=turn_ended_text,
        reply_markup=InlineKeyboardMarkup(game_keyboard),
        parse_mode="HTML"
    )

async def cancel_endturn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    lang = user_languages.get(user_id, "en")
    
    parts = query.data.split("_")
    room_id = "_".join(parts[2:-1])
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    if room_id not in active_rooms:
        return
    
    # Тексты
    if lang == "en":
        menu_text = "🎮 <b>Game Menu</b>"
        my_city_text = "🏛 My City"
        build_text = "⚒ Build"
        war_text = "⚔️ War"
        end_turn_text = "⏭ End Turn"
        income_text = "📊 Income"
    else:
        menu_text = "🎮 <b>Меню игры</b>"
        my_city_text = "🏛 Мой город"
        build_text = "⚒ Строить"
        war_text = "⚔️ Война"
        end_turn_text = "⏭ Завершить ход"
        income_text = "📊 Доход"
    
    # ВАЖНО: используем target_user_id, а не other_player!
    game_keyboard = [
        [InlineKeyboardButton(my_city_text, callback_data=f"mycity_{room_id}_{target_user_id}"),
         InlineKeyboardButton(build_text, callback_data=f"build_{room_id}_{target_user_id}")],
        [InlineKeyboardButton(war_text, callback_data=f"war_{room_id}_{target_user_id}"),
         InlineKeyboardButton(end_turn_text, callback_data=f"endturn_{room_id}_{target_user_id}"),
         InlineKeyboardButton(income_text, callback_data=f"income_{room_id}_{target_user_id}")]
    ]
    
    await query.edit_message_text(
        menu_text,
        reply_markup=InlineKeyboardMarkup(game_keyboard),
        parse_mode="HTML"
    )

async def war(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    lang = user_languages.get(user_id, "en")
    
    parts = query.data.split("_")
    room_id = "_".join(parts[1:-1])
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    player = None
    other_player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
        else:
            other_player = p
    
    if not player or not other_player:
        return
    
    chat_id = active_rooms[room_id]["chat_id"]
    try:
        chat_member = await context.bot.get_chat_member(chat_id, other_player.user_id)
        enemy_name = chat_member.user.username or chat_member.user.first_name or str(other_player.user_id)
    except:
        enemy_name = str(other_player.user_id)
    
    if lang == "en":
        text = (f"⚔️ <b>Attack {enemy_name}?</b>\n\n"
                f"Your army: {player.population} units\n"
                f"Enemy army: {other_player.population} units\n\n"
                f"20% of your army will fight.")
        confirm_text = "✅ Attack!"
        back_text = "🔙 Back"
    else:
        text = (f"⚔️ <b>Атаковать {enemy_name}?</b>\n\n"
                f"Твоя армия: {player.population} юнитов\n"
                f"Армия врага: {other_player.population} юнитов\n\n"
                f"20% твоей армии пойдут в бой.")
        confirm_text = "✅ Атаковать!"
        back_text = "🔙 Назад"
    
    buttons = [
        [InlineKeyboardButton(confirm_text, callback_data=f"attack_{room_id}_{other_player.user_id}_{target_user_id}")],
        [InlineKeyboardButton(back_text, callback_data=f"back_to_game_{room_id}_{target_user_id}")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML"
    )

async def attack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    room_id = "_".join(parts[1:-2])
    enemy_id = int(parts[-2])
    attacker_id = int(parts[-1])
    
    if query.from_user.id != attacker_id:
        return
    
    if room_id not in active_rooms:
        return
    
    attacker = None
    defender = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == attacker_id:
            attacker = p
        elif p.user_id == enemy_id:
            defender = p
    
    if not attacker or not defender:
        return
    
    lang = active_rooms[room_id].get("lang", "en")
    
    # 👇 40% АРМИИ (было 20%)
    attack_force = attacker.population * 40 // 100
    defend_force = defender.population * 40 // 100
    
    # Расчёт силы
    attack_power = attack_force * attacker.bloodlust
    defense_power = defend_force * defender.health
    
    # Крит от ненависти
    crit_msg = ""
    if attacker.hate > 0 and random.random() < attacker.hate / 100:
        attack_power *= 2
        attacker.hate = 0
        crit_msg = "🔥 CRIT! " if lang == "en" else "🔥 КРИТ! "
    
    # Расчёт потерь
    if attack_power > defense_power:
        defender_losses = min(defend_force, (attack_power - defense_power) // defender.health + 1)
        attacker_losses = attack_force // 4
    else:
        attacker_losses = min(attack_force, (defense_power - attack_power) // attacker.health + 1)
        defender_losses = defend_force // 4
    
    attacker.population = max(0, attacker.population - attacker_losses)
    defender.population = max(0, defender.population - defender_losses)
    
    chat_id = active_rooms[room_id]["chat_id"]
    try:
        attacker_name = (await context.bot.get_chat_member(chat_id, attacker_id)).user.username or str(attacker_id)
        defender_name = (await context.bot.get_chat_member(chat_id, enemy_id)).user.username or str(enemy_id)
    except:
        attacker_name = str(attacker_id)
        defender_name = str(enemy_id)
    
    # 👇 АВТОМАТИЧЕСКАЯ ПЕРЕДАЧА ХОДА
    # Находим другого игрока
    other_player = defender  # противник становится следующим
    
    # Меняем очередь
    active_rooms[room_id]["allowed"] = [other_player.user_id]
    active_rooms[room_id]["current_player"] = other_player.user_id
    active_rooms[room_id]["turn"] = active_rooms[room_id].get("turn", 1) + 1
    
    # Проверяем, не закончилась ли игра
    if await check_game_over(room_id, context):
        return
    
    # Тексты с результатом
    if lang == "en":
        result = (f"{crit_msg}⚔️ <b>Battle Results</b>\n\n"
                  f"{attacker_name}\n├ Lost: {attacker_losses}\n└ Left: {attacker.population}\n\n"
                  f"{defender_name}\n├ Lost: {defender_losses}\n└ Left: {defender.population}\n\n"
                  f"🔄 Turn passed to {defender_name}")
        
        # Кнопки для следующего игрока
        my_city_text = "🏛 My City"
        build_text = "⚒ Build"
        war_text = "⚔️ War"
        end_turn_text = "⏭ End Turn"
        income_text = "📊 Income"
    else:
        result = (f"{crit_msg}⚔️ <b>Результаты битвы</b>\n\n"
                  f"{attacker_name}\n├ Потери: {attacker_losses}\n└ Осталось: {attacker.population}\n\n"
                  f"{defender_name}\n├ Потери: {defender_losses}\n└ Осталось: {defender.population}\n\n"
                  f"🔄 Ход передан {defender_name}")
        
        my_city_text = "🏛 Мой город"
        build_text = "⚒ Строить"
        war_text = "⚔️ Война"
        end_turn_text = "⏭ Завершить ход"
        income_text = "📊 Доход"
    
    # 👇 КНОПКИ ДЛЯ НОВОГО ИГРОКА (уже без "Attack", только меню)
    game_keyboard = [
        [InlineKeyboardButton(my_city_text, callback_data=f"mycity_{room_id}_{other_player.user_id}"),
         InlineKeyboardButton(build_text, callback_data=f"build_{room_id}_{other_player.user_id}")],
        [InlineKeyboardButton(war_text, callback_data=f"war_{room_id}_{other_player.user_id}"),
         InlineKeyboardButton(end_turn_text, callback_data=f"endturn_{room_id}_{other_player.user_id}"),
         InlineKeyboardButton(income_text, callback_data=f"income_{room_id}_{other_player.user_id}")]
    ]
    
    await query.edit_message_text(
        result,
        reply_markup=InlineKeyboardMarkup(game_keyboard),
        parse_mode="HTML"
    )
    
    await check_game_over(room_id, context)

async def back_to_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    lang = user_languages.get(user_id, "en")
    
    parts = query.data.split("_")
    room_id = "_".join(parts[3:-1])
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
            break
    
    if not player:
        return
    
    # Тексты на нужном языке
    if lang == "en":
        my_city_text = "🏛 My City"
        build_text = "⚒ Build"
        war_text = "⚔️ War"
        end_turn_text = "⏭ End Turn"
        income_text = "📊 Income"
    else:
        my_city_text = "🏛 Мой город"
        build_text = "⚒ Строить"
        war_text = "⚔️ Война"
        end_turn_text = "⏭ Завершить ход"
        income_text = "📊 Доход"
    
    # НАСТОЯЩАЯ КЛАВИАТУРА
    game_keyboard = [
        [InlineKeyboardButton(my_city_text, callback_data=f"mycity_{room_id}_{target_user_id}"),
         InlineKeyboardButton(build_text, callback_data=f"build_{room_id}_{target_user_id}")],
        [InlineKeyboardButton(war_text, callback_data=f"war_{room_id}_{target_user_id}"),
         InlineKeyboardButton(end_turn_text, callback_data=f"endturn_{room_id}_{target_user_id}"),
         InlineKeyboardButton(income_text, callback_data=f"income_{room_id}_{target_user_id}")]
    ]
    
    await query.edit_message_text(
        "🎮 <b>Меню игры</b>",
        reply_markup=InlineKeyboardMarkup(game_keyboard),
        parse_mode="HTML"
    )
    
async def play_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    room_id = query.data.replace("play_", "")
    
    # Если комнаты нет - игнорим
    if room_id not in active_rooms:
        return
    
    # Язык комнаты (от создателя)
    room_lang = active_rooms[room_id].get("lang", "en")
    
    # Если комната уже полная - игнорим
    if len(active_rooms[room_id]["choices"]) >= 2:
        return
    
    # Если это создатель - игнорим
    if query.from_user.id == active_rooms[room_id]["creator"]:
        return
    
    # Добавляем второго игрока в допущенные
    if "allowed" not in active_rooms[room_id]:
        active_rooms[room_id]["allowed"] = []
    active_rooms[room_id]["allowed"].append(query.from_user.id)
    
    # Кнопки выбора расы на языке комнаты
    race_keyboard = []
    for race_id in RACES:
        if room_lang == "en":
            race_name = RACES[race_id]["name"]
        else:
            race_names = {
                "human": "👤 Человек",
                "elf": "🧝 Эльф",
                "demon": "👹 Демон",
                "beast": "🐺 Зверолюд"
            }
            race_name = race_names.get(race_id, RACES[race_id]["name"])
        
        race_keyboard.append([InlineKeyboardButton(
            race_name, 
            callback_data=f"race_{room_id}_{race_id}"
        )])
    
    # Заголовок на языке комнаты
    title = "🎭 Choose your race!" if room_lang == "en" else "🎭 Выбери свою расу!"
    
    await query.edit_message_text(
        f"{title}",
        reply_markup=InlineKeyboardMarkup(race_keyboard),
        parse_mode="HTML"
    )

async def cancel_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    room_id = query.data.replace("cancel_", "")
    
    if room_id not in active_rooms:
        return
    
    if query.from_user.id != active_rooms[room_id]["creator"]:
        await query.answer("Only the game creator can cancel!", show_alert=True)
        return
    
    del active_rooms[room_id]
    await query.edit_message_text("❌ Game cancelled.", parse_mode="HTML")

async def back_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    room_id = query.data.replace("back_", "")
    
    if room_id not in active_rooms:
        return
    
    active_rooms[room_id]["stage"] = "waiting"
    active_rooms[room_id]["choices"] = {}
    active_rooms[room_id]["allowed"] = [active_rooms[room_id]["creator"]]
    
    # Кнопка Play для всех
    play_keyboard = [[InlineKeyboardButton("🎮 Play", callback_data=f"play_{room_id}")]]
    
    await query.edit_message_text(
        f"🏆 <b>Room {room_id}</b>\n\n"
        f"⏳ Waiting for someone to join...",
        reply_markup=InlineKeyboardMarkup(play_keyboard),
        parse_mode="HTML"
    )
    
# =============================================================================
# БЛОК 9.5: МОЙ ГОРОД (показывает ресурсы игрока)
# =============================================================================

async def my_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    lang = user_languages.get(user_id, "en")
    
    parts = query.data.split("_")
    room_id = "_".join(parts[1:-1])
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
            break
    
    if not player:
        return
    
    # Заголовок
    title = "🏛 Your City" if lang == "en" else "🏛 Мой город"
    
    # Названия ресурсов
    if lang == "en":
        food_text = "Food"
        faith_text = "Faith"
        labor_text = "Labor"
        health_text = "Health"
        int_text = "Intelligence"
        dep_text = "Depression"
        hate_text = "Hate"
        money_text = "Money"
        mat_text = "Materials"
        pop_text = "Population"
        build_text = "Buildings"
        upgrade_text = "📈 Upgrade"
        back_text = "🔙 Back"
    else:
        food_text = "Еда"
        faith_text = "Вера"
        labor_text = "Труд"
        health_text = "Жизнь"
        int_text = "Интеллект"
        dep_text = "Депрессия"
        hate_text = "Ненависть"
        money_text = "Деньги"
        mat_text = "Материалы"
        pop_text = "Население"
        build_text = "Постройки"
        upgrade_text = "📈 Улучшить"
        back_text = "🔙 Назад"
    
    text = f"<b>{title}</b>\n\n"
    text += f"🍞 {food_text}: {player.food}/{player.food_limit}\n"
    text += f"🙏 {faith_text}: {player.faith}/{player.faith_limit}\n"
    text += f"⚒ {labor_text}: {player.labor}/{player.labor_limit}\n"
    text += f"❤️ {health_text}: {player.health}/{player.health_limit}\n"
    text += f"🧠 {int_text}: {player.intelligence}/{player.intelligence_limit}\n"
    text += f"😔 {dep_text}: {player.depression}\n"
    text += f"😈 {hate_text}: {player.hate}\n"
    text += f"💰 {money_text}: {player.money}\n"
    text += f"📦 {mat_text}: {player.materials}\n"
    text += f"👥 {pop_text}: {player.population}\n"
    text += f"🏗 {build_text}: {len(player.buildings)}"
    
    # Кнопки: Назад и Улучшить
    buttons = [
        [InlineKeyboardButton(back_text, callback_data=f"back_to_game_{room_id}_{target_user_id}"),
         InlineKeyboardButton(upgrade_text, callback_data=f"upgrade_menu_{room_id}_{target_user_id}")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML"
    )

async def upgrade_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    room_id = "_".join(parts[2:-1])
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
            break
    
    if not player:
        return
    
    lang = active_rooms[room_id].get("lang", "en")
    
    # Цены на улучшение (за +100 к лимиту)
    upgrade_costs = {
        "food": 100,
        "faith": 200,
        "labor": 500,
        "health": 1000,
        "intelligence": 750
    }
    
    if lang == "en":
        title = "📈 <b>Upgrade Limits</b>"
        food_text = f"🍞 Food: +100 limit for {upgrade_costs['food']}💰"
        faith_text = f"🙏 Faith: +100 limit for {upgrade_costs['faith']}💰"
        labor_text = f"⚒ Labor: +100 limit for {upgrade_costs['labor']}💰"
        health_text = f"❤️ Health: +100 limit for {upgrade_costs['health']}💰"
        int_text = f"🧠 Intelligence: +100 limit for {upgrade_costs['intelligence']}💰"
        back_text = "🔙 Back"
        money_text = f"Your money: {player.money}💰"
    else:
        title = "📈 <b>Улучшение лимитов</b>"
        food_text = f"🍞 Еда: +100 к лимиту за {upgrade_costs['food']}💰"
        faith_text = f"🙏 Вера: +100 к лимиту за {upgrade_costs['faith']}💰"
        labor_text = f"⚒ Труд: +100 к лимиту за {upgrade_costs['labor']}💰"
        health_text = f"❤️ Жизнь: +100 к лимиту за {upgrade_costs['health']}💰"
        int_text = f"🧠 Интеллект: +100 к лимиту за {upgrade_costs['intelligence']}💰"
        back_text = "🔙 Назад"
        money_text = f"Твои деньги: {player.money}💰"
    
    buttons = [
        [InlineKeyboardButton(food_text, callback_data=f"upgrade_{room_id}_food_{target_user_id}")],
        [InlineKeyboardButton(faith_text, callback_data=f"upgrade_{room_id}_faith_{target_user_id}")],
        [InlineKeyboardButton(labor_text, callback_data=f"upgrade_{room_id}_labor_{target_user_id}")],
        [InlineKeyboardButton(health_text, callback_data=f"upgrade_{room_id}_health_{target_user_id}")],
        [InlineKeyboardButton(int_text, callback_data=f"upgrade_{room_id}_intelligence_{target_user_id}")],
        [InlineKeyboardButton(back_text, callback_data=f"mycity_{room_id}_{target_user_id}")]
    ]
    
    await query.edit_message_text(
        f"{title}\n\n{money_text}",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML"
    )

async def do_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    room_id = "_".join(parts[1:-2])
    resource = parts[-2]
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
            break
    
    if not player:
        return
    
    lang = active_rooms[room_id].get("lang", "en")
    
    # Цены на улучшение
    upgrade_costs = {
        "food": 100,
        "faith": 200,
        "labor": 500,
        "health": 1000,
        "intelligence": 750
    }
    
    cost = upgrade_costs.get(resource)
    if not cost:
        return
    
    if player.money < cost:
        if lang == "en":
            text = f"❌ Not enough money! Need {cost}💰"
            back_text = "🔙 Back"
        else:
            text = f"❌ Не хватает денег! Нужно {cost}💰"
            back_text = "🔙 Назад"
        
        back_button = [[InlineKeyboardButton(back_text, callback_data=f"mycity_{room_id}_{target_user_id}")]]
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(back_button),
            parse_mode="HTML"
        )
        return
    
    # Увеличиваем лимит
    if resource == "food":
        player.food_limit += 100
    elif resource == "faith":
        player.faith_limit += 100
    elif resource == "labor":
        player.labor_limit += 100
    elif resource == "health":
        player.health_limit += 100
    elif resource == "intelligence":
        player.intelligence_limit += 100
    
    # Списываем деньги
    player.money -= cost
    
    if lang == "en":
        text = f"✅ <b>{resource.capitalize()} limit increased!</b>\n"
        text += f"New limit: {getattr(player, f'{resource}_limit')}\n"
        text += f"Remaining money: {player.money}💰"
        back_text = "🔙 Back to City"
    else:
        resource_names = {
            "food": "Еды",
            "faith": "Веры",
            "labor": "Труда",
            "health": "Жизни",
            "intelligence": "Интеллекта"
        }
        rus_name = resource_names.get(resource, resource)
        text = f"✅ <b>Лимит {rus_name} увеличен!</b>\n"
        text += f"Новый лимит: {getattr(player, f'{resource}_limit')}\n"
        text += f"Осталось денег: {player.money}💰"
        back_text = "🔙 В город"
    
    back_button = [[InlineKeyboardButton(back_text, callback_data=f"mycity_{room_id}_{target_user_id}")]]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(back_button),
        parse_mode="HTML"
    )

async def income(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    room_id = "_".join(parts[1:-1])
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
            break
    
    if not player:
        return
    
    lang = active_rooms[room_id].get("lang", "en")
    
    # Считаем доход от зданий
    food_income = 0
    faith_income = 0
    material_income = 0
    money_income = 0
    int_income = 0
    pop_growth = 0
    bloodlust_bonus = 0
    dev_points_bonus = 0
    health_bonus = 0
    
    # Расходы (пока только еда, потом добавятся другие)
    food_consumption = player.calculate_food_consumption()
    faith_consumption = 0  # потом от событий
    material_consumption = 0  # потом от стройки
    money_consumption = 0  # потом от налогов
    int_consumption = 0  # потом от стресса
    health_consumption = 0  # от ран
    pop_consumption = 0  # от голода
    
    buildings_list = []
    unique_buildings_owned = set()
    
    for b_id in player.buildings:
        building = BUILDINGS.get(b_id)
        if not building:
            continue
        
        buildings_list.append(building['name'])
        
        # Считаем эффекты по каждому зданию
        if b_id == "farm":
            food_income += 50
        elif b_id == "sawmill":
            material_income += 20
        elif b_id == "church":
            faith_income += 50
        elif b_id == "forge":
            bloodlust_bonus += 10
        elif b_id == "laboratory":
            int_income += 20
        elif b_id == "mine":
            material_income += 100
        elif b_id == "tax_office":
            money_income += 30
        elif b_id == "library":
            int_income += 50
        elif b_id == "house":
            pop_growth += 1
        elif b_id == "necropolis":
            pass  # эффект в бою
        elif b_id == "sacred_grove":
            health_bonus += 1000
            bloodlust_bonus += 500
            faith_income += 1000
            unique_buildings_owned.add("sacred_grove")
        elif b_id == "hell":
            pop_growth += 10
            unique_buildings_owned.add("hell")
        elif b_id == "bone_throne":
            pass  # эффект в еде
            unique_buildings_owned.add("bone_throne")
        elif b_id == "steam_engine":
            dev_points_bonus += 500
            unique_buildings_owned.add("steam_engine")
    
    # Чистый доход
    net_food = food_income - food_consumption
    net_faith = faith_income - faith_consumption
    net_material = material_income - material_consumption
    net_money = money_income - money_consumption
    net_int = int_income - int_consumption
    net_health = health_bonus - health_consumption
    net_pop = pop_growth - pop_consumption
    
async def income(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    room_id = "_".join(parts[1:-1])
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
            break
    
    if not player:
        return
    
    lang = active_rooms[room_id].get("lang", "en")
    
    # Считаем доход от зданий
    food_income = 0
    faith_income = 0
    material_income = 0
    money_income = 0
    int_income = 0
    pop_growth = 0
    bloodlust_bonus = 0
    dev_points_bonus = 0
    health_bonus = 0
    population_bonus = 0
    
    # Расходы
    food_consumption = player.calculate_food_consumption()
    
    buildings_list = []
    unique_buildings_owned = set()
    
    for b_id in player.buildings:
        building = BUILDINGS.get(b_id)
        if not building:
            continue
        
        buildings_list.append(building.get('name_ru' if lang != 'en' else 'name', building['name']))
        
        # Считаем эффекты через round_income (универсально)
        if building.get("round_income"):
            for resource, value in building["round_income"].items():
                if resource == "food":
                    food_income += value
                elif resource == "faith":
                    faith_income += value
                elif resource == "materials":
                    material_income += value
                elif resource == "money":
                    money_income += value
                elif resource == "intelligence":
                    int_income += value
                elif resource == "population_growth":
                    pop_growth += value
                elif resource == "bloodlust":
                    bloodlust_bonus += value
                elif resource == "dev_points":
                    dev_points_bonus += value
                elif resource == "health":
                    health_bonus += value
                elif resource == "population":
                    population_bonus += value
        
        # Отмечаем уникальные здания
        if b_id in ["sacredgrove", "hell", "bonethrone", "steamengine"]:
            unique_buildings_owned.add(b_id)
    
    # Чистый доход
    net_food = food_income - food_consumption
    
    # Заголовки
    if lang == "en":
        title = "📊 <b>Income Report</b>"
        buildings_title = "🏗️ Buildings:"
        income_title = "💰 Income per round:"
        unique_title = "⭐ Unique buildings:"
        food_text = f"🍞 Food: +{food_income} | -{food_consumption} | Net: {net_food}"
        faith_text = f"🙏 Faith: +{faith_income}"
        material_text = f"📦 Materials: +{material_income}"
        money_text = f"💰 Money: +{money_income}"
        int_text = f"🧠 Intelligence: +{int_income}"
        pop_text = f"👥 Population growth: +{pop_growth}%"
        pop_bonus_text = f"👥 Direct population: +{population_bonus}"
        bloodlust_text = f"🔪 Bloodlust: +{bloodlust_bonus}"
        health_text = f"❤️ Health: +{health_bonus}"
        dev_text = f"⚙️ Dev Points: +{dev_points_bonus}"
    else:
        title = "📊 <b>Отчёт о доходе</b>"
        buildings_title = "🏗️ Постройки:"
        income_title = "💰 Доход за раунд:"
        unique_title = "⭐ Уникальные постройки:"
        food_text = f"🍞 Еда: +{food_income} | -{food_consumption} | Итого: {net_food}"
        faith_text = f"🙏 Вера: +{faith_income}"
        material_text = f"📦 Материалы: +{material_income}"
        money_text = f"💰 Деньги: +{money_income}"
        int_text = f"🧠 Интеллект: +{int_income}"
        pop_text = f"👥 Рост населения: +{pop_growth}%"
        pop_bonus_text = f"👥 Прямой прирост: +{population_bonus}"
        bloodlust_text = f"🔪 Кровожадность: +{bloodlust_bonus}"
        health_text = f"❤️ Жизнь: +{health_bonus}"
        dev_text = f"⚙️ Очки развития: +{dev_points_bonus}"
    
    # Формируем текст
    text = f"{title}\n\n"
    text += f"{buildings_title}\n"
    text += ", ".join(buildings_list) if buildings_list else ("None" if lang == "en" else "Нет")
    text += f"\n\n{income_title}\n"
    text += f"{food_text}\n"
    if faith_income > 0:
        text += f"{faith_text}\n"
    if material_income > 0:
        text += f"{material_text}\n"
    if money_income > 0:
        text += f"{money_text}\n"
    if int_income > 0:
        text += f"{int_text}\n"
    if bloodlust_bonus > 0:
        text += f"{bloodlust_text}\n"
    if health_bonus > 0:
        text += f"{health_text}\n"
    if pop_growth > 0:
        text += f"{pop_text}\n"
    if population_bonus > 0:
        text += f"{pop_bonus_text}\n"
    if dev_points_bonus > 0:
        text += f"{dev_text}\n"
    
    # Уникальные постройки
    if unique_buildings_owned:
        text += f"\n{unique_title}\n"
        for b_id in unique_buildings_owned:
            building = BUILDINGS.get(b_id)
            if building:
                name = building.get('name_ru' if lang != 'en' else 'name', building['name'])
                text += f"• {name}\n"
    
    back_text = "🔙 Back" if lang == "en" else "🔙 Назад"
    back_keyboard = [[InlineKeyboardButton(back_text, callback_data=f"back_to_game_{room_id}_{target_user_id}")]]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(back_keyboard),
        parse_mode="HTML"
    )
    
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
    
    # Сначала самые длинные/точные паттерны
    app.add_handler(CallbackQueryHandler(construct, pattern="construct_"))
    app.add_handler(CallbackQueryHandler(my_city, pattern="mycity_"))
    
    # Потом основные игровые действия
    app.add_handler(CallbackQueryHandler(build_menu, pattern="build_"))
    app.add_handler(CallbackQueryHandler(war, pattern="war_"))
    app.add_handler(CallbackQueryHandler(end_turn, pattern="endturn_"))
    
    # Потом выбор расы и язык
    app.add_handler(CallbackQueryHandler(confirm_endturn, pattern="confirm_endturn_"))
    app.add_handler(CallbackQueryHandler(cancel_endturn, pattern="cancel_endturn_"))
    app.add_handler(CallbackQueryHandler(choose_race, pattern="race_"))
    app.add_handler(CallbackQueryHandler(language_menu, pattern="language"))
    app.add_handler(CallbackQueryHandler(set_language, pattern="setlang_"))
    
    # Потом системные (новая игра, статистика)
    app.add_handler(CallbackQueryHandler(new_game, pattern="new_game"))
    app.add_handler(CallbackQueryHandler(my_stats, pattern="my_stats"))
    app.add_handler(CallbackQueryHandler(balance, pattern="balance"))
    
    # Самые общие — в конце
    app.add_handler(CallbackQueryHandler(back_to_game, pattern="back_to_game_"))
    app.add_handler(CallbackQueryHandler(upgrade_menu, pattern="upgrade_menu_"))
    app.add_handler(CallbackQueryHandler(attack, pattern="attack_"))
    app.add_handler(CallbackQueryHandler(do_upgrade, pattern="upgrade_"))
    app.add_handler(CallbackQueryHandler(play_game, pattern="play_"))
    app.add_handler(CallbackQueryHandler(income, pattern="income_"))
    app.add_handler(CallbackQueryHandler(cancel_game, pattern="cancel_"))
    app.add_handler(CallbackQueryHandler(back_button, pattern="back_"))
   
    app.run_polling()
if __name__ == "__main__":
    import threading
    flask_thread = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000))))
    flask_thread.daemon = True
    flask_thread.start()
    run_bot()
