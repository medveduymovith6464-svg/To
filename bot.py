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
        "cost": 50,
        "effect": "+1% population/round",
        "apply": lambda p: setattr(p, 'population_growth', p.population_growth + 1)
    },
    "farm": {
        "name": "🌱 Farm", 
        "cost": 100,
        "effect": "+50 food/round",
        "apply": lambda p: setattr(p, 'food', p.food + 50)
    },
    "sawmill": {
        "name": "🪵 Sawmill",
        "cost": 200, 
        "effect": "+20 materials/round",
        "apply": lambda p: setattr(p, 'materials', p.materials + 20)
    },
    "church": {
        "name": "⛪ Church",
        "cost": 1000,
        "effect": "+50 faith/round",
        "apply": lambda p: setattr(p, 'faith', p.faith + 50)
    },
    
    # Средние
    "forge": {
        "name": "⚒ Forge",
        "cost": 800,
        "effect": "+10 bloodlust",
        "apply": lambda p: setattr(p, 'bloodlust', p.bloodlust + 10)
    },
    "laboratory": {
        "name": "🔬 Laboratory",
        "cost": 1350,
        "effect": "+20 intelligence/round",
        "apply": lambda p: setattr(p, 'intelligence', p.intelligence + 20)
    },
    "mine": {
        "name": "🕳 Mine",
        "cost": 1500,
        "effect": "+100 materials/round",
        "apply": lambda p: setattr(p, 'materials', p.materials + 100)
    },
    "tax_office": {
        "name": "💰 Tax Office",
        "cost": 5000,
        "effect": "+30 money/round",
        "apply": lambda p: setattr(p, 'money', p.money + 30)
    },
    
    # Элитные
    "library": {
        "name": "📚 Library",
        "cost": 8000,
        "effect": "+50 intelligence/round",
        "apply": lambda p: setattr(p, 'intelligence', p.intelligence + 50)
    },
    "necropolis": {
        "name": "🪦 Necropolis",
        "cost": 12222,
        "effect": "Resurrect 10% units after battle",
        "apply": lambda p: None  # Будет реализовано позже
    },
    
    # Легендарные (20000)
    "sacred_grove": {
        "name": "🌳 Sacred Grove",
        "cost": 20000,
        "effect": "+1000 health, +500 bloodlust, +1000 faith",
        "apply": lambda p: (setattr(p, 'health', p.health + 1000),
                           setattr(p, 'bloodlust', p.bloodlust + 500),
                           setattr(p, 'faith', p.faith + 1000))
    },
    "hell": {
        "name": "🔥 Hell",
        "cost": 20000,
        "effect": "+10 units/round",
        "apply": lambda p: setattr(p, 'population', p.population + 10)
    },
    "bone_throne": {
        "name": "🦴 Bone Throne",
        "cost": 20000,
        "effect": "Units don't eat food",
        "apply": lambda p: None  # Будет реализовано позже
    },
    "steam_engine": {
        "name": "⚙ Steam Engine",
        "cost": 20000,
        "effect": "+500 dev points/round",
        "apply": lambda p: setattr(p, 'dev_points', p.dev_points + 500)
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
    room_id = f"room_{random.randint(1000, 9999)}"
    
    active_rooms[room_id] = {
        "creator": query.from_user.id,
        "chat_id": update.effective_chat.id,  # ← СОХРАНЯЕМ ЧАТ
        "stage": "picking",
        "choices": {},
        "allowed": [query.from_user.id],
        "players": []
    }
    
    race_keyboard = []
    for race_id in RACES:
        race_keyboard.append([InlineKeyboardButton(
            RACES[race_id]["name"], 
            callback_data=f"race_{room_id}_{race_id}"
        )])
    
    await query.edit_message_text(
        f"🏆 <b>Room {room_id}</b>\n\n"
        f"🎭 <b>Choose your race!</b>",
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
        # Сохраняем ID сообщения создателя, чтобы потом удалить
        sent_msg = await query.edit_message_text(
            f"✅ You chose {RACES[race_id]['name']}!\n\n"
            f"⏳ Waiting for second player...",
            parse_mode="HTML"
        )
        active_rooms[room_id]["creator_msg_id"] = sent_msg.message_id
        
        # Кнопка Play для всех
        play_keyboard = [[InlineKeyboardButton("🎮 Play", callback_data=f"play_{room_id}")]]
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🎮 A game is waiting! Click PLAY to join!",
            reply_markup=InlineKeyboardMarkup(play_keyboard),
            parse_mode="HTML"
        )
        return
    
    # 👇 ЕСЛИ ЭТО ВТОРОЙ ИГРОК
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
    
    parts = query.data.split("_")
    room_id = "_".join(parts[1:-1])
    target_user_id = int(parts[-1])
    
    # Проверка владельца
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    # Находим игрока
    player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
            break
    
    if not player:
        return
    
    # ✅ УБИРАЕМ ПРОВЕРКУ ОЧЕРЕДИ
    # if target_user_id not in active_rooms[room_id].get("allowed", []):
    #     return
    
    # Кнопки зданий
    buttons = []
    for b_id, b_data in BUILDINGS.items():
        cost_color = "🟢" if player.dev_points >= b_data['cost'] else "🔴"
        buttons.append([InlineKeyboardButton(
            f"{b_data['name']} | {cost_color} {b_data['cost']}💰",
            callback_data=f"construct_{room_id}_{b_id}_{target_user_id}"
        )])
    
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"back_to_game_{room_id}_{target_user_id}")])
    
    await query.edit_message_text(
        f"🏗️ **Construction Menu**\nYour Dev Points: {player.dev_points}💰",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML"
    )

async def construct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
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
    
    # ✅ ВОТ ЗДЕСЬ ПРОВЕРКА ОЧЕРЕДИ НУЖНА!
    if target_user_id not in active_rooms[room_id].get("allowed", []):
        return
    
    building = BUILDINGS.get(building_id)
    if not building:
        return
    
    if player.dev_points < building['cost']:
        return
    
    # Строим
    player.dev_points -= building['cost']
    player.add_building(building_id)
    
    await query.edit_message_text(
        f"✅ **{building['name']} built!**\nRemaining Dev Points: {player.dev_points}",
        parse_mode="HTML"
    )

async def start_game(room_id, context, chat_id):
    """Запускает игру после выбора обоих игроков"""
    if room_id not in active_rooms:
        return
    
    if len(active_rooms[room_id]["choices"]) != 2:
        return
    
    players = []
    for user_id, race_id in active_rooms[room_id]["choices"].items():
        player = Player(user_id, race_id)
        players.append(player)
    
    active_rooms[room_id]["players"] = players
    active_rooms[room_id]["turn"] = 1
    active_rooms[room_id]["current_player"] = players[0].user_id
    
    # 👇 КНОПКИ С ID ИГРОКА
    game_keyboard = [
        [InlineKeyboardButton("🏛 My City", callback_data=f"mycity_{room_id}_{players[0].user_id}"),
         InlineKeyboardButton("⚒ Build", callback_data=f"build_{room_id}_{players[0].user_id}")],
        [InlineKeyboardButton("⚔️ War", callback_data=f"war_{room_id}_{players[0].user_id}"),
         InlineKeyboardButton("⏭ End Turn", callback_data=f"endturn_{room_id}_{players[0].user_id}")]
    ]
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⚔️ **GAME STARTED!**\n\n"
             f"👤 Player 1: {players[0].race_id}\n"
             f"👤 Player 2: {players[1].race_id}\n"
             f"🎮 {players[0].user_id}'s turn!",
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

async def end_turn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Парсим callback_data: endturn_room123_456
    parts = query.data.split("_")
    room_id = "_".join(parts[1:-1])
    target_user_id = int(parts[-1])
    
    # Проверка: нажимает ли владелец кнопки
    if query.from_user.id != target_user_id:
        return
    
    # Проверка: существует ли комната
    if room_id not in active_rooms:
        return
    
    # Находим игрока
    player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
            break
    
    if not player:
        return
    
    # ✅ ПОКАЗЫВАЕМ ПОДТВЕРЖДЕНИЕ
    confirm_keyboard = [
        [InlineKeyboardButton("✅ Yes", callback_data=f"confirm_endturn_{room_id}_{target_user_id}"),
         InlineKeyboardButton("❌ No", callback_data=f"cancel_endturn_{room_id}_{target_user_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"back_to_game_{room_id}_{target_user_id}")]
    ]
    
    await query.edit_message_text(
        text=f"⚠️ **Are you sure you want to end your turn?**\n\n"
             f"Once you end your turn, you won't be able to take any more actions until your next turn.",
        reply_markup=InlineKeyboardMarkup(confirm_keyboard),
        parse_mode="HTML"
    )

async def confirm_endturn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение завершения хода"""
    query = update.callback_query
    await query.answer()
    
    # Парсим: confirm_endturn_room123_456
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
    
    # Находим другого игрока
    other_player = None
    for p in active_rooms[room_id]["players"]:
        if p.user_id != target_user_id:
            other_player = p
            break
    
    if not other_player:
        return
    
    # Меняем очередь
    active_rooms[room_id]["allowed"] = [other_player.user_id]
    active_rooms[room_id]["current_player"] = other_player.user_id
    
    # Проверяем, не закончилась ли игра
    if await check_game_over(room_id, context):
        return
    
    # Новое меню для следующего игрока
    game_keyboard = [
        [InlineKeyboardButton("🏛 My City", callback_data=f"mycity_{room_id}_{other_player.user_id}"),
         InlineKeyboardButton("⚒ Build", callback_data=f"build_{room_id}_{other_player.user_id}")],
        [InlineKeyboardButton("⚔️ War", callback_data=f"war_{room_id}_{other_player.user_id}"),
         InlineKeyboardButton("⏭ End Turn", callback_data=f"endturn_{room_id}_{other_player.user_id}")]
    ]
    
    await query.edit_message_text(
        text=f"🔄 **Turn ended!**\n\n"
             f"👤 {target_user_id} finished.\n"
             f"🎮 Now **{other_player.user_id}'s** turn!",
        reply_markup=InlineKeyboardMarkup(game_keyboard),
        parse_mode="HTML"
    )

async def cancel_endturn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена завершения хода"""
    query = update.callback_query
    await query.answer()
    
    # Парсим: cancel_endturn_room123_456
    parts = query.data.split("_")
    room_id = "_".join(parts[2:-1])
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    if room_id not in active_rooms:
        return
    
    # Просто возвращаем игровое меню
    game_keyboard = [
        [InlineKeyboardButton("🏛 My City", callback_data=f"mycity_{room_id}_{target_user_id}"),
         InlineKeyboardButton("⚒ Build", callback_data=f"build_{room_id}_{target_user_id}")],
        [InlineKeyboardButton("⚔️ War", callback_data=f"war_{room_id}_{target_user_id}"),
         InlineKeyboardButton("⏭ End Turn", callback_data=f"endturn_{room_id}_{target_user_id}")]
    ]
    
    await query.edit_message_text(
        "🎮 **Game Menu**",
        reply_markup=InlineKeyboardMarkup(game_keyboard),
        parse_mode="HTML"
    )

async def war(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Парсим callback_data: war_room123_456
    parts = query.data.split("_")
    room_id = "_".join(parts[1:-1])
    target_user_id = int(parts[-1])
    
    # Проверка: нажимает ли владелец кнопки
    if query.from_user.id != target_user_id:
        return
    
    # Проверка: существует ли комната
    if room_id not in active_rooms:
        return
    
    # Кнопка "Назад" в игровое меню
    back_keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=f"back_to_game_{room_id}_{target_user_id}")]]
    
    # Отправляем временное сообщение (потом заменится на реальную войну)
    await query.edit_message_text(
        text=f"⚔️ **War is not implemented yet!**\n\n"
             f"Stay tuned for future updates.",
        reply_markup=InlineKeyboardMarkup(back_keyboard),
        parse_mode="HTML"
    )

async def back_to_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возвращает в главное игровое меню"""
    query = update.callback_query
    await query.answer()
    
    # Парсим данные: back_to_game_room123_456
    parts = query.data.split("_")
    # Отбрасываем "back", "to", "game" — берём всё после 3-го элемента
    room_id = "_".join(parts[3:-1])
    target_user_id = int(parts[-1])
    
    # Проверка: нажимает ли владелец кнопки
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    # Находим игрока
    player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
            break
    
    if not player:
        return
    
    # Игровое меню
    game_keyboard = [
        [InlineKeyboardButton("🏛 My City", callback_data=f"mycity_{room_id}_{target_user_id}"),
         InlineKeyboardButton("⚒ Build", callback_data=f"build_{room_id}_{target_user_id}")],
        [InlineKeyboardButton("⚔️ War", callback_data=f"war_{room_id}_{target_user_id}"),
         InlineKeyboardButton("⏭ End Turn", callback_data=f"endturn_{room_id}_{target_user_id}")]
    ]
    
    await query.edit_message_text(
        "🎮 **Game Menu**",
        reply_markup=InlineKeyboardMarkup(game_keyboard),
        parse_mode="HTML"
    )
    
async def play_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    room_id = query.data.replace("play_", "")
    
    if room_id not in active_rooms:
        await query.edit_message_text("❌ Room expired")
        return
    
    # Проверяем, не полная ли комната
    if len(active_rooms[room_id]["choices"]) >= 2:
        await query.edit_message_text("❌ Game already full!")
        return
    
    # Проверяем, не создатель ли это
    if query.from_user.id == active_rooms[room_id]["creator"]:
        await query.answer("You can't join your own game!", show_alert=True)
        return
    
    # Добавляем второго игрока в допущенные
    if "allowed" not in active_rooms[room_id]:
        active_rooms[room_id]["allowed"] = []
    active_rooms[room_id]["allowed"].append(query.from_user.id)
    
    # Показываем второму игроку выбор расы
    race_keyboard = []
    for race_id in RACES:
        race_keyboard.append([InlineKeyboardButton(
            RACES[race_id]["name"], 
            callback_data=f"race_{room_id}_{race_id}"
        )])
    
    await query.edit_message_text(
        f"🎭 <b>Choose your race!</b>",
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
    
    # Парсим данные из callback: mycity_room123_456
    parts = query.data.split("_")
    room_id = "_".join(parts[1:-1])
    target_user_id = int(parts[-1])
    
    # Проверка: нажимает ли владелец кнопки
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    # Ищем игрока в комнате
    player = None
    for p in active_rooms[room_id].get("players", []):
        if p.user_id == target_user_id:
            player = p
            break
    
    if not player:
        return
    
    # Формируем текст
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
    
    # Кнопка назад в игровое меню
    back_keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=f"back_to_game_{room_id}_{target_user_id}")]]
    
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
    app.add_handler(CallbackQueryHandler(play_game, pattern="play_"))
    app.add_handler(CallbackQueryHandler(cancel_game, pattern="cancel_"))
    app.add_handler(CallbackQueryHandler(back_button, pattern="back_"))
   
    app.run_polling()
if __name__ == "__main__":
    import threading
    flask_thread = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000))))
    flask_thread.daemon = True
    flask_thread.start()
    run_bot()
