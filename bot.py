import logging
import os
import random
import sys
import sqlite3
import json
from datetime import datetime
import asyncio
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler
from telegram.ext import filters, PreCheckoutQueryHandler  # ← ДОБАВЬ СЮДА!

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
# БЛОК 2: БАЗА ДАННЫХ (PostgreSQL на Neon) - ТОЛЬКО ОДИН РАЗ!
# =============================================================================
import psycopg2
from psycopg2.extras import RealDictCursor
import datetime

def get_db():
    """Возвращает подключение к Neon PostgreSQL"""
    DATABASE_URL = os.environ.get("NEON_DB_URL")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    """Создаёт таблицы, если их нет"""
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Таблица игроков
        c.execute("""CREATE TABLE IF NOT EXISTS players (
            user_id BIGINT PRIMARY KEY, 
            username TEXT, 
            games_played INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0, 
            registered_at TIMESTAMP
        )""")
        
        # Таблица игр
        c.execute("""CREATE TABLE IF NOT EXISTS games (
            id SERIAL PRIMARY KEY, 
            date TIMESTAMP, 
            winner_race TEXT,
            winner_id BIGINT, 
            players TEXT, 
            room_id TEXT
        )""")
        
        # Таблица для Сенко-коинов
        c.execute("""CREATE TABLE IF NOT EXISTS neko_coins (
            user_id BIGINT PRIMARY KEY,
            coins INTEGER DEFAULT 0,
            last_bonus DATE
        )""")
        
        # Таблица для еженедельной статистики
        c.execute("""CREATE TABLE IF NOT EXISTS weekly_stats (
            id SERIAL PRIMARY KEY,
            week_start DATE,
            race TEXT,
            wins INTEGER DEFAULT 0
        )""")
        
        # Таблица для хранения даты последнего сброса
        c.execute("""CREATE TABLE IF NOT EXISTS reset_log (
            id SERIAL PRIMARY KEY,
            last_reset DATE
        )""")
        
        # 👇 ТАБЛИЦА ДЛЯ АРТОВ
        c.execute("""CREATE TABLE IF NOT EXISTS arts (
            id SERIAL PRIMARY KEY,
            file_id TEXT UNIQUE,
            rarity TEXT,
            added_at TIMESTAMP
        )""")
        
        # 👇 ТАБЛИЦА ДЛЯ КОЛЛЕКЦИЙ
        c.execute("""CREATE TABLE IF NOT EXISTS art_collections (
            user_id BIGINT,
            art_id TEXT,
            rarity TEXT,
            opened_at TIMESTAMP,
            UNIQUE(user_id, art_id)
        )""")
        
        # 👇 ТАБЛИЦА ДЛЯ ЛИДЕРБОРДА
        c.execute("""CREATE TABLE IF NOT EXISTS art_leaderboard (
            user_id BIGINT PRIMARY KEY,
            unique_arts INTEGER DEFAULT 0,
            last_updated TIMESTAMP
        )""")
        
        conn.commit()
        conn.close()
        print("✅ База данных Neon инициализирована")
    except Exception as e:
        print(f"❌ Ошибка при инициализации БД: {e}")

def add_player(user_id, username):
    """Добавляет нового игрока или игнорит, если уже есть"""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO players (user_id, username, registered_at) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
            (user_id, username, datetime.datetime.now())
        )
        conn.commit()
        conn.close()
        print(f"✅ Игрок {user_id} добавлен в БД")
    except Exception as e:
        print(f"❌ Ошибка при добавлении игрока {user_id}: {e}")

def get_all_players():
    """Возвращает список всех user_id"""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT user_id FROM players")
        users = c.fetchall()
        conn.close()
        return users
    except Exception as e:
        print(f"❌ Ошибка при получении игроков: {e}")
        return []

def update_player_stats(user_id, won=False):
    """Обновляет статистику игрока после игры"""
    try:
        conn = get_db()
        c = conn.cursor()
        if won:
            c.execute("UPDATE players SET games_played = games_played + 1, wins = wins + 1 WHERE user_id = %s", (user_id,))
        else:
            c.execute("UPDATE players SET games_played = games_played + 1 WHERE user_id = %s", (user_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Ошибка при обновлении статистики: {e}")

def save_game(winner_race, winner_id, players_data, room_id):
    """Сохраняет результаты игры"""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO games (date, winner_race, winner_id, players, room_id) VALUES (%s, %s, %s, %s, %s)",
            (datetime.datetime.now(), winner_race, winner_id, json.dumps(players_data), room_id)
        )
        conn.commit()
        conn.close()
        print(f"✅ Игра {room_id} сохранена")
    except Exception as e:
        print(f"❌ Ошибка при сохранении игры: {e}")

def save_art(file_id, rarity):
    """Сохраняет арт в базу данных"""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO arts (file_id, rarity, added_at) VALUES (%s, %s, %s) ON CONFLICT (file_id) DO NOTHING",
            (file_id, rarity, datetime.datetime.now())
        )
        conn.commit()
        conn.close()
        print(f"✅ Арт сохранён в БД: {rarity}")
    except Exception as e:
        print(f"❌ Ошибка сохранения в БД: {e}")

def load_arts_from_db():
    """Загружает все арты из базы данных в память"""
    global SENKO_ARTS
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT file_id, rarity FROM arts")
        arts = c.fetchall()
        conn.close()
        
        SENKO_ARTS["common"] = []
        SENKO_ARTS["rare"] = []
        
        for art in arts:
            if art['rarity'] == 'common':
                SENKO_ARTS["common"].append(art['file_id'])
            elif art['rarity'] == 'rare':
                SENKO_ARTS["rare"].append(art['file_id'])
        
        print(f"✅ Загружено из БД: common={len(SENKO_ARTS['common'])}, rare={len(SENKO_ARTS['rare'])}")
    except Exception as e:
        print(f"❌ Ошибка загрузки артов из БД: {e}")

async def check_weekly_reset():
    """Проверяет, не пора ли сбросить статистику"""
    conn = get_db()
    c = conn.cursor()
    
    # Узнаём, когда был последний сброс
    c.execute("SELECT last_reset FROM reset_log ORDER BY id DESC LIMIT 1")
    last_reset = c.fetchone()
    
    today = datetime.datetime.now().date()
    
    # Если никогда не сбрасывали или прошло больше 7 дней
    if not last_reset or (today - last_reset['last_reset']).days >= 7:
        print("🔄 Сбрасываем еженедельную статистику...")
        
        # Сохраняем текущую статистику в weekly_stats (на всякий случай)
        c.execute("""
            INSERT INTO weekly_stats (week_start, race, wins)
            SELECT %s, winner_race, COUNT(*)
            FROM games
            WHERE date >= %s
            GROUP BY winner_race
        """, (today - datetime.timedelta(days=7), last_reset['last_reset'] if last_reset else today))
        
        # Очищаем таблицу games (удаляем старые игры)
        c.execute("DELETE FROM games")
        
        # Записываем дату сброса
        c.execute("INSERT INTO reset_log (last_reset) VALUES (%s)", (today,))
        
        conn.commit()
        print("✅ Статистика сброшена!")
    
    conn.close()

# Инициализация при запуске
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
    # 👇 КОСТЯНОЙ ТРОН - отключает голод
        if "bonethrone" in self.buildings:
            return 0
    
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
# БЛОК: СОБЫТИЯ (вставь после BUILDINGS, до БЛОКА 4)
# =============================================================================
import random

EVENTS = [
    # 0.1% - Эпические
    {
        "name_en": "🦽 Disabled Robbery",
        "name_ru": "🦽 Ограбление инвалида",
        "desc_en": "You robbed a disabled person. +10 to random resource.",
        "desc_ru": "Вы обокрали бедного инвалида без рук. +10 к случайному ресурсу.",
        "chance": 0.1,
        "effect": lambda p: add_random_resource(p, 10)
    },
    {
        "name_en": "👘 Senko's Visit",
        "name_ru": "🦊 Сенко в гостях",
        "desc_en": "Senko in Niko's outfit asks you to stop fighting. YOU LOSE!",
        "desc_ru": "Сенко в костюме Нико попросила не драться! ВЫ ПРОИГРАЛИ!",
        "chance": 0.1,
        "effect": lambda p: senko_ends_game(p)  # особая функция
    },
    
    # 1% - Редкие
    {
        "name_en": "🍺 Gods with Beer",
        "name_ru": "🍺 Боги с пивом",
        "desc_en": "Gods descended and offered you beer. -5000 depression!",
        "desc_ru": "К вам спустились боги и предложили пиво. -5000 депрессии!",
        "chance": 1,
        "effect": lambda p: setattr(p, 'depression', max(0, p.depression - 5000))
    },
    {
        "name_en": "👖 Commander's Bet",
        "name_ru": "👖 Спор командира",
        "desc_en": "Commander bet he'd eat his pants and lost. +10 gold, -1 population.",
        "desc_ru": "Командир поспорил, что съест трусы и проиграл. +10 золота, -1 население.",
        "chance": 1,
        "effect": lambda p: (setattr(p, 'money', p.money + 10), 
                            setattr(p, 'population', max(1, p.population - 1)))
    },
    
    # 3-5.9% - Средние
    {
        "name_en": "👮 Tax Police",
        "name_ru": "👮 Налоговая полиция",
        "desc_en": "TAX POLICE, OPEN UP! -100% money, -50% food!",
        "desc_ru": "ЭТО НАЛОГОВАЯ, ОТКРЫВАЙТЕ! -100% денег, -50% еды!",
        "chance": 5.9,
        "effect": lambda p: (setattr(p, 'money', 0), 
                            setattr(p, 'food', p.food // 2))
    },
    {
        "name_en": "🔥 Homeless Fire",
        "name_ru": "🔥 Костёр бомжа",
        "desc_en": "A homeless man wanted to warm up and lit a fire. -30% materials.",
        "desc_ru": "Бомж захотел согреться и разжёг костёр. -30% материалов.",
        "chance": 5,
        "effect": lambda p: setattr(p, 'materials', int(p.materials * 0.7))
    },
    {
        "name_en": "👵 Grandma's Stash",
        "name_ru": "👵 Бабушкина заначка",
        "desc_en": "Your grandma died and you found her stash! +250 money.",
        "desc_ru": "Ваша бабушка умерла и вы нашли её заначку! +250 денег.",
        "chance": 5,
        "effect": lambda p: setattr(p, 'money', p.money + 250)
    },
    
    # 5-10% - Частые
    {
        "name_en": "😔 Unrest",
        "name_ru": "😔 Смута",
        "desc_en": "DAMN UNREST! +50 depression, -250 faith.",
        "desc_ru": "ЕБАННАЯ СМУТА! +50 депрессии, -250 веры.",
        "chance": 5,
        "effect": lambda p: (setattr(p, 'depression', p.depression + 50),
                            setattr(p, 'faith', max(0, p.faith - 250)))
    },
    {
        "name_en": "💸 Tribute",
        "name_ru": "💸 Дань",
        "desc_en": "PAY TRIBUTE! -150 food, money, labor.",
        "desc_ru": "ПЛАТИ ДАНЬ! -150 к еде, деньгам, труду.",
        "chance": 7.9,
        "effect": lambda p: (setattr(p, 'food', max(0, p.food - 150)),
                            setattr(p, 'money', max(0, p.money - 150)),
                            setattr(p, 'labor', max(0, p.labor - 150)))
    },
    {
        "name_en": "🌾 Farmer Dream",
        "name_ru": "🌾 Сон фермера",
        "desc_en": "You dreamed you were a farmer. But you're not. -50 food.",
        "desc_ru": "Вам приснилось, что вы фермер. Но вы не фермер. -50 еды.",
        "chance": 10,
        "effect": lambda p: setattr(p, 'food', max(0, p.food - 50))
    },
    {
        "name_en": "🇨🇳 Chinese Guy",
        "name_ru": "🇨🇳 Китаец",
        "desc_en": "You saw a Chinese guy! -10% population.",
        "desc_ru": "Вы увидели китайца! -10% населения.",
        "chance": 10,
        "effect": lambda p: setattr(p, 'population', int(p.population * 0.9))
    },
    {
        "name_en": "🎭 Jester",
        "name_ru": "🎭 Шут",
        "desc_en": "You hired a jester... but now everyone thinks YOU'RE the jester. -30% labor, -1 money.",
        "desc_ru": "Вы наняли шута... но теперь вас считают шутом. -30% труда, -1 монета.",
        "chance": 10,
        "effect": lambda p: (setattr(p, 'labor', int(p.labor * 0.7)),
                            setattr(p, 'money', max(0, p.money - 1)))
    },
    {
        "name_en": "✨ Winx Club",
        "name_ru": "✨ Винкс",
        "desc_en": "WINX TOGETHER WE ARE STRONG! +100 health.",
        "desc_ru": "ВИНКС ТОЛЬКО ВМЕСТЕ МЫ СИЛЬНЫ! +100 к жизни.",
        "chance": 10,
        "effect": lambda p: setattr(p, 'health', p.health + 100)
    },
    {
        "name_en": "👶 Baby Boom",
        "name_ru": "👶 Бейби бум",
        "desc_en": "BABY BOOOOOOM! -20 population.",
        "desc_ru": "БЕЙБИ БУУУУУУУМ! -20 населения.",
        "chance": 10,
        "effect": lambda p: setattr(p, 'population', max(1, p.population - 20))
    },
    {
        "name_en": "🧙 Shaman Scam",
        "name_ru": "🧙 Шаманка",
        "desc_en": "A shaman scammed you. -5 money, -100 food.",
        "desc_ru": "Вас наебала Шаманка. -5 монет, -100 еды.",
        "chance": 10,
        "effect": lambda p: (setattr(p, 'money', max(0, p.money - 5)),
                            setattr(p, 'food', max(0, p.food - 100)))
    },
    {
        "name_en": "🌍 African Children",
        "name_ru": "🌍 Африканские дети",
        "desc_en": "You remembered African children while drinking water. -30% food.",
        "desc_ru": "Вы вспомнили африканских детей, когда пили воду. -30% еды.",
        "chance": 10,
        "effect": lambda p: setattr(p, 'food', int(p.food * 0.7))
    }
]

def add_random_resource(player, amount):
    """Добавляет +amount к случайному ресурсу"""
    resources = ['food', 'faith', 'labor', 'health', 'intelligence', 'money', 'materials', 'dev_points']
    chosen = random.choice(resources)
    current = getattr(player, chosen)
    setattr(player, chosen, current + amount)

def senko_ends_game(player):
    """Сенко завершает игру для этого игрока"""
    player.population = 0
    player.depression = 9999
    # Функция check_game_over потом подхватит

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
        "welcome": "⚔️ <b>Welcome to {}!</b>\n\n2 players enter. 1 leaves.\n\n"
                   "👤 Human – balanced\n🧝 Elf – high faith\n👹 Demon – high damage\n🐺 Beastfolk – tanky\n\n"
                   "Ready? Hit <b>New Game</b>!",
        "new_game": "⚔️ New Game",
        "my_stats": "📊 My Stats",
        "balance": "⚖️ Balance",
        "language": "🌐 Language"
    },
    "ru": {
        "welcome": "⚔️ <b>Добро пожаловать в {}!</b>\n\n2 игрока заходят. 1 выходит.\n\n"
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
    
    # Сохраняем игрока в Neon
    add_player(user_id, update.effective_user.username)
    
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
    """Показывает баланс рас за последние 7 дней"""
    from datetime import datetime, timedelta
    
    conn = get_db()
    c = conn.cursor()
    
    week_ago = datetime.now() - timedelta(days=7)
    
    # Общее количество игр за неделю
    c.execute("SELECT COUNT(*) as count FROM games WHERE date >= %s", (week_ago,))
    result = c.fetchone()
    total_games = result['count'] if result else 0
    
    if total_games == 0:
        await update.message.reply_text(
            "📊 No games this week." if user_languages.get(update.effective_user.id, "en") == "en" 
            else "📊 На этой неделе игр не было.",
            parse_mode="HTML"
        )
        conn.close()
        return
    
    # Определяем язык
    lang = user_languages.get(update.effective_user.id, "en")
    
    if lang == "en":
        text = "📊 <b>BALANCE REPORT (last 7 days)</b>\n"
    else:
        text = "📊 <b>БАЛАНС РАС (последние 7 дней)</b>\n"
    
    for race_id, race_data in RACES.items():
        c.execute(
            "SELECT COUNT(*) as wins FROM games WHERE winner_race = %s AND date >= %s", 
            (race_id, week_ago)
        )
        wins_result = c.fetchone()
        wins = wins_result['wins'] if wins_result else 0
        
        winrate = (wins / total_games * 100) if total_games > 0 else 0
        
        # Эмодзи баланса
        if winrate > 27:
            status = "🔥"  # имба
        elif winrate < 20:
            status = "💩"  # слабаки
        else:
            status = "✅"  # норм
        
        text += f"\n{race_data['emoji']} {race_data['name']}: {wins} wins ({winrate:.1f}%) {status}"
    
    conn.close()
    
    await update.message.reply_text(text, parse_mode="HTML")
    
# =============================================================================
# БЛОК 7: ЛИЧНАЯ СТАТИСТИКА (сколько игрок сыграл и выиграл)
# =============================================================================
async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT games_played, wins FROM players WHERE user_id = %s", (update.effective_user.id,))
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
# БЛОК 8: ПРЕДЛОЖИТЬ АРТ (вместо репортов)
# =============================================================================
async def suggest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if context.user_data.get('awaiting_art'):
        await update.message.reply_text(
            "❌ You already have a pending art!\n"
            "Please wait for it to be reviewed."
        )
        return
    
    await update.message.reply_text(
        "🎨 <b>Suggest an art</b>\n\n"
        "Send me a picture you want to add to the game!\n"
        "If I approve it, you'll get Senko Coins!",
        parse_mode="HTML"
    )
    context.user_data['awaiting_art'] = True

async def handle_suggested_art(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_art'):
        return
    
    try:
        user_id = update.effective_user.id
        file_id = update.message.photo[-1].file_id
        
        user = update.effective_user
        user_name = user.username or user.first_name or str(user_id)
        
        short_file_id = file_id[-20:] if len(file_id) > 20 else file_id
        
        # Сохраняем в bot_data (глобально!)
        if 'suggested_arts' not in context.bot_data:
            context.bot_data['suggested_arts'] = {}
        context.bot_data['suggested_arts'][short_file_id] = {
            'file_id': file_id,
            'user_id': user_id
        }
        
        keyboard = [
            [InlineKeyboardButton("✅ Common (100)", callback_data=f"sug_c_{short_file_id}"),
             InlineKeyboardButton("✅ Rare (500)", callback_data=f"sug_r_{short_file_id}")],
            [InlineKeyboardButton("❌ Reject", callback_data=f"sug_x_{short_file_id}")]
        ]
        
        await context.bot.send_photo(
            chat_id=YOUR_ID,
            photo=file_id,
            caption=f"🆕 New art from @{user_name}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        await update.message.reply_text("✅ Thanks! Your art has been sent for review!")
        context.user_data['awaiting_art'] = False
        
    except Exception as e:
        print(f"❌ Error: {e}")
        await update.message.reply_text("❌ Something went wrong. Try again!")
        context.user_data['awaiting_art'] = False

async def suggest_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data.split("_")
    rarity = "common" if data[1] == "c" else "rare"
    short_file_id = data[2]
    
    # Достаём из bot_data
    art_data = context.bot_data.get('suggested_arts', {}).get(short_file_id)
    if not art_data:
        await query.edit_message_caption(caption="❌ Art not found!")
        return
    
    user_id = art_data['user_id']
    file_id = art_data['file_id']
    
    reward = 100 if rarity == "common" else 500
    
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE neko_coins SET coins = coins + %s WHERE user_id = %s",
        (reward, user_id)
    )
    conn.commit()
    conn.close()
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🎉 Your art was approved as {rarity}!\n💰 +{reward} Senko Coins!"
        )
    except:
        pass
    
    await query.edit_message_caption(
        caption=f"✅ Approved as {rarity}! +{reward} coins to user."
    )
    
    # Очищаем
    del context.bot_data['suggested_arts'][short_file_id]

async def suggest_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data.split("_")
    short_file_id = data[2]
    
    # Достаём user_id из bot_data
    art_data = context.bot_data.get('suggested_arts', {}).get(short_file_id)
    if art_data:
        user_id = art_data['user_id']
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="😔 Your art wasn't approved. Try again!"
            )
        except:
            pass
        del context.bot_data['suggested_arts'][short_file_id]
    
    await query.edit_message_caption(caption="❌ Rejected")
    
# =============================================================================
# БЛОК 8.5: РАССЫЛКА (только для тебя)
# =============================================================================
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != 6950162933:
        return
    
    if not context.args:
        await update.message.reply_text("Используй: /broadcast [текст]")
        return
    
    users = get_all_players()
    
    if not users:
        await update.message.reply_text("❌ База пуста!")
        return
    
    text = ' '.join(context.args)
    ok = 0
    bad = 0
    
    for user in users:
        try:
            await context.bot.send_message(user['user_id'], f"📢 {text}")
            ok += 1
        except:
            bad += 1
    
    await update.message.reply_text(f"✅ {ok} ок, ❌ {bad} нет")
    
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
        if player.depression >= 100:
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
        
        # 👇 СОХРАНЯЕМ В NEON (вместо SQLite)
        players_data = [{"user_id": p.user_id, "race": p.race_id, "alive": (p in alive_players)} for p in players]
        save_game(winner.race_id, winner.user_id, players_data, room_id)
        
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
        
        # 👇 ТОЖЕ СОХРАНЯЕМ (ничья)
        players_data = [{"user_id": p.user_id, "race": p.race_id, "alive": False} for p in players]
        save_game("draw", 0, players_data, room_id)
        
        del active_rooms[room_id]
        return True
    
    return False

async def next_round(room_id, context):
    """Начисляет доходы, расходы и возвращает список событий"""
    if room_id not in active_rooms:
        return []
    
    players = active_rooms[room_id].get("players", [])
    current_round = active_rooms[room_id].get("round", 1)
    chat_id = active_rooms[room_id]["chat_id"]
    lang = active_rooms[room_id].get("lang", "en")
    
    events_list = []  # список событий для вывода
    
    # 👇 ПРОВЕРКА НА 100 РАУНД (ГАРАНТИРОВАННАЯ СЕНКО)
    if current_round >= 100:
        if lang == "en":
            senko_text = "🦊 <b>SENKO'S VISIT!</b>\n\nAt round 100, Senko in Niko's outfit came and said:\n✨ <i>\"You've played so long... Time to rest!\"</i>\n\nGame over. Everyone wins! 🏆"
        else:
            senko_text = "🦊 <b>СЕНКО В ГОСТЯХ!</b>\n\nНа 100 раунде к вам пришла Сенко в костюме Нико и сказала:\n✨ <i>«Вы так долго играли... Пора отдохнуть!»</i>\n\nИгра завершена. Все молодцы! 🏆"
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=senko_text,
            parse_mode="HTML"
        )
        del active_rooms[room_id]
        return []
    
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
        
        # 3. ПРОЦЕНТНЫЙ РОСТ ОТ ДОМОВ
        if player.population_growth > 0:
            growth = int(player.population * player.population_growth / 100)
            player.population += max(1, growth)
        
        # 4. СОБЫТИЯ ДЛЯ КАЖДОГО ИГРОКА (75% шанс)
        if random.randint(1, 100) <= 75:
            # Выбираем событие по шансам
            events_pool = []
            for event in EVENTS:
                weight = int(event["chance"] * 10)  # 0.1% -> 1, 1% -> 10, 10% -> 100
                events_pool.extend([event] * weight)
            
            chosen_event = random.choice(events_pool)
            
            # Применяем эффект к игроку
            chosen_event["effect"](player)
            
            # Получаем нормальное имя игрока
            try:
                chat_member = await context.bot.get_chat_member(chat_id, player.user_id)
                player_name = chat_member.user.username or chat_member.user.first_name or str(player.user_id)
            except:
                player_name = str(player.user_id)
            
            # Добавляем в список событий
            if lang == "en":
                events_list.append(f"👤 {player_name}: {chosen_event['desc_en']}")
            else:
                events_list.append(f"👤 {player_name}: {chosen_event['desc_ru']}")
        
        # 5. РАСХОДЫ (ЕДА)
        food_consumed = player.calculate_food_consumption()
        player.food -= food_consumed
        
        # Если еда ушла в минус - штрафуем население
        if player.food < 0:
            starvation = abs(player.food) // 10 + 1
            player.population = max(0, player.population - starvation)
            player.food = 0
        
        # 6. ДЕПРЕССИЯ
        player.depression += 1
        player.apply_depression()
        
        # 👇 7. БУНТ (если вера < 200, шанс 10%)
        if player.faith < 200 and random.randint(1, 100) <= 10:
            # Бунт!
            losses = max(1, player.population // 3)  # 30% населения
            player.population -= losses
            player.depression += 5
            
            # Получаем имя игрока
            try:
                chat_member = await context.bot.get_chat_member(chat_id, player.user_id)
                player_name = chat_member.user.username or chat_member.user.first_name or str(player.user_id)
            except:
                player_name = str(player.user_id)
            
            if lang == "en":
                revolt_text = f"🔥 <b>REVOLT!</b>\n{player_name} lost {losses} units, +5 depression!"
            else:
                revolt_text = f"🔥 <b>БУНТ!</b>\n{player_name} потерял {losses} юнитов, +5 депрессии!"
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=revolt_text,
                parse_mode="HTML"
            )
        
        # 8. ЛИМИТЫ
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
    return events_list

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
    
    # Чётный ход — заканчиваем раунд
    if current_turn % 2 == 0:
        current_round += 1
        active_rooms[room_id]["round"] = current_round
    
    # 👇 ПРОВЕРКА НА ЭЛЬФА (5% шанс украсть ход)
    stolen = False
    steal_text = ""
    if player.race_id == "elf" and random.randint(1, 100) <= 5:
        # Крадём ход — оставляем текущего игрока
        other_player = player
        stolen = True
        
        # Получаем имя для сообщения
        try:
            chat_member = await context.bot.get_chat_member(chat_id, player.user_id)
            player_name = chat_member.user.username or chat_member.user.first_name or str(player.user_id)
        except:
            player_name = str(player.user_id)
        
        if lang == "en":
            steal_text = f"🧝 <b>Elf ability!</b>\n{player_name} stole the turn!"
        else:
            steal_text = f"🧝 <b>Способность эльфа!</b>\n{player_name} украл ход!"
    
    # Если не украли — нормальная смена хода
    if not stolen:
        active_rooms[room_id]["allowed"] = [other_player.user_id]
        active_rooms[room_id]["current_player"] = other_player.user_id
    else:
        active_rooms[room_id]["allowed"] = [player.user_id]
        active_rooms[room_id]["current_player"] = player.user_id
    
    # Увеличиваем номер хода
    active_rooms[room_id]["turn"] = current_turn + 1
    
    # 👇 ВЫЗЫВАЕМ next_round И ПОЛУЧАЕМ СПИСОК СОБЫТИЙ
    events = await next_round(room_id, context)
    
    if await check_game_over(room_id, context):
        return
    
    # 👇 ЕСЛИ БЫЛА КРАЖА — ПОКАЗЫВАЕМ ОТДЕЛЬНОЕ СООБЩЕНИЕ
    if stolen:
        back_keyboard = [[InlineKeyboardButton("🔙 Back" if lang == "en" else "🔙 Назад", 
                                              callback_data=f"delete_steal_{room_id}_{target_user_id}")]]
        
        sent_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=steal_text,
            reply_markup=InlineKeyboardMarkup(back_keyboard),
            parse_mode="HTML"
        )
        active_rooms[room_id]["steal_msg_id"] = sent_msg.message_id
    
    # 👇 ЕСЛИ БЫЛИ СОБЫТИЯ — ПОКАЗЫВАЕМ ИХ
    if events:
        if lang == "en":
            event_title = "🎲 <b>EVENTS THIS ROUND!</b>"
            back_text = "🔙 Back to Game"
        else:
            event_title = "🎲 <b>СОБЫТИЯ РАУНДА!</b>"
            back_text = "🔙 В игру"
        
        events_text = event_title + "\n\n" + "\n".join(events)
        
        back_keyboard = [[InlineKeyboardButton(back_text, callback_data=f"delete_events_{room_id}_{other_player.user_id}")]]
        
        sent_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=events_text,
            reply_markup=InlineKeyboardMarkup(back_keyboard),
            parse_mode="HTML"
        )
        active_rooms[room_id]["events_msg_id"] = sent_msg.message_id
    
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

async def delete_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет сообщение с событиями"""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    room_id = "_".join(parts[2:-1])
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    # Удаляем сообщение с событиями
    if "events_msg_id" in active_rooms[room_id]:
        try:
            await context.bot.delete_message(
                chat_id=active_rooms[room_id]["chat_id"],
                message_id=active_rooms[room_id]["events_msg_id"]
            )
        except:
            pass
    
    # Возвращаем игровое меню
    lang = active_rooms[room_id].get("lang", "en")
    
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
    
    game_keyboard = [
        [InlineKeyboardButton(my_city_text, callback_data=f"mycity_{room_id}_{target_user_id}"),
         InlineKeyboardButton(build_text, callback_data=f"build_{room_id}_{target_user_id}")],
        [InlineKeyboardButton(war_text, callback_data=f"war_{room_id}_{target_user_id}"),
         InlineKeyboardButton(end_turn_text, callback_data=f"endturn_{room_id}_{target_user_id}"),
         InlineKeyboardButton(income_text, callback_data=f"income_{room_id}_{target_user_id}")]
    ]
    
    await query.edit_message_text(
        "🎮 <b>Меню игры</b>" if lang == "ru" else "🎮 <b>Game Menu</b>",
        reply_markup=InlineKeyboardMarkup(game_keyboard),
        parse_mode="HTML"
    )

async def delete_steal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет сообщение о краже хода"""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    room_id = "_".join(parts[2:-1])
    target_user_id = int(parts[-1])
    
    if query.from_user.id != target_user_id:
        return
    
    if room_id not in active_rooms:
        return
    
    # Удаляем сообщение о краже
    if "steal_msg_id" in active_rooms[room_id]:
        try:
            await context.bot.delete_message(
                chat_id=active_rooms[room_id]["chat_id"],
                message_id=active_rooms[room_id]["steal_msg_id"]
            )
        except:
            pass
    
    # Возвращаем игровое меню
    lang = active_rooms[room_id].get("lang", "en")
    
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
    
    game_keyboard = [
        [InlineKeyboardButton(my_city_text, callback_data=f"mycity_{room_id}_{target_user_id}"),
         InlineKeyboardButton(build_text, callback_data=f"build_{room_id}_{target_user_id}")],
        [InlineKeyboardButton(war_text, callback_data=f"war_{room_id}_{target_user_id}"),
         InlineKeyboardButton(end_turn_text, callback_data=f"endturn_{room_id}_{target_user_id}"),
         InlineKeyboardButton(income_text, callback_data=f"income_{room_id}_{target_user_id}")]
    ]
    
    menu_text = "🎮 <b>Game Menu</b>" if lang == "en" else "🎮 <b>Меню игры</b>"
    
    await query.edit_message_text(
        menu_text,
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
                f"40% of your army will fight.")
        confirm_text = "✅ Attack!"
        back_text = "🔙 Back"
    else:
        text = (f"⚔️ <b>Атаковать {enemy_name}?</b>\n\n"
                f"Твоя армия: {player.population} юнитов\n"
                f"Армия врага: {other_player.population} юнитов\n\n"
                f"40% твоей армии пойдут в бой.")
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

# 👇 НЕКРОПОЛЬ - воскрешает 10% погибших после боя
    if "necropolis" in attacker.buildings:
        resurrect = attacker_losses * 10 // 100
        attacker.population += resurrect
    # можно оставить здание (постоянный эффект) или удалить (одноразовый)
    # attacker.buildings.remove("necropolis")  # раскомментируй, если одноразовый

    if "necropolis" in defender.buildings:
        resurrect = defender_losses * 10 // 100
        defender.population += resurrect
    # defender.buildings.remove("necropolis")  # раскомментируй, если одноразовый
    
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
    
    # Проверка владельца
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
        cure_text = "💊 Cure"
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
        cure_text = "💊 Лечить"
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
    
    # Кнопки
    buttons = [
        [InlineKeyboardButton(back_text, callback_data=f"back_to_game_{room_id}_{target_user_id}"),
         InlineKeyboardButton(upgrade_text, callback_data=f"upgrade_menu_{room_id}_{target_user_id}")],
        [InlineKeyboardButton(cure_text, callback_data=f"cure_depression_{room_id}_{target_user_id}")]
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

async def cure_depression(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    # 👇 ТЕПЕРЬ ЛЕЧЕНИЕ ТРАТИТ 100 ВЕРЫ (а не 10)
    cure_amount = 1  # лечим 1 депрессии
    faith_cost = 100  # тратим 100 веры
    food_cost = 10
    labor_cost = 10
    
    if player.faith >= faith_cost and player.food >= food_cost and player.labor >= labor_cost:
        player.faith -= faith_cost
        player.food -= food_cost
        player.labor -= labor_cost
        player.depression = max(0, player.depression - cure_amount)
        
        if lang == "en":
            text = f"✅ <b>Depression cured!</b>\nReduced by {cure_amount}\nCurrent: {player.depression}\nFaith left: {player.faith}"
            back_text = "🔙 Back to City"
        else:
            text = f"✅ <b>Депрессия вылечена!</b>\nУменьшена на {cure_amount}\nТекущая: {player.depression}\nВеры осталось: {player.faith}"
            back_text = "🔙 В город"
    else:
        if lang == "en":
            text = f"❌ <b>Not enough resources!</b>\nNeed: {faith_cost} Faith, {food_cost} Food, {labor_cost} Labor"
            back_text = "🔙 Back"
        else:
            text = f"❌ <b>Не хватает ресурсов!</b>\nНужно: {faith_cost} Веры, {food_cost} Еды, {labor_cost} Труда"
            back_text = "🔙 Назад"
    
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
# BLOCK: SENKO COINS & ARTS
# =============================================================================

# Arts from channels (filled automatically)
SENKO_ARTS = {"common": [], "rare": []}

# Channel post handler
async def channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Слушает посты в каналах и сохраняет арты"""
    if not update.channel_post or not update.channel_post.photo:
        return
    
    chat = update.channel_post.chat
    file_id = update.channel_post.photo[-1].file_id
    
    # Определяем редкость по каналу
    if chat.username == "Senkocommon":
        rarity = "common"
        SENKO_ARTS["common"].append(file_id)
        print(f"✅ Common art добавлен в память: {file_id[:30]}...")
    elif chat.username == "SenkoRare":
        rarity = "rare"
        SENKO_ARTS["rare"].append(file_id)
        print(f"✅ Rare art добавлен в память: {file_id[:30]}...")
    else:
        print(f"❌ Неизвестный канал: {chat.username}")
        return
    
    # Сохраняем в базу данных
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO arts (file_id, rarity, added_at) VALUES (%s, %s, %s) ON CONFLICT (file_id) DO NOTHING",
            (file_id, rarity, datetime.now())
        )
        conn.commit()
        conn.close()
        print(f"✅ Арт сохранён в БД: {rarity}")
    except Exception as e:
        print(f"❌ Ошибка сохранения в БД: {e}")

# Main menu
async def bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Get balance
    conn = sqlite3.connect("game.db")
    c = conn.cursor()
    c.execute("SELECT coins FROM neko_coins WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    balance = result[0] if result else 0
    conn.close()
    
    keyboard = [
        [InlineKeyboardButton("🎁 Get Bonus", callback_data="get_bonus"),
         InlineKeyboardButton("🖼 Buy Art", callback_data="buy_art_menu")]
    ]
    
    await update.message.reply_text(
        f"🐱 <b>Senko Coins Shop</b>\n\n"
        f"Your balance: {balance}💰\n\n"
        f"Choose option:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

# Get bonus handler
async def get_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("🔥🔥🔥 GET BONUS ВЫЗВАНА!")
    
    query = update.callback_query
    await query.answer()
    
    # Отправляем тестовое сообщение
    await query.edit_message_text(
        "✅ Функция работает! Сейчас начислю монеты...",
        parse_mode="HTML"
    )
    
    user_id = query.from_user.id
    today = datetime.now().date()
    
    conn = get_db()
    c = conn.cursor()
    
    # Проверяем баланс до
    c.execute("SELECT coins FROM neko_coins WHERE user_id = %s", (user_id,))
    before = c.fetchone()
    before_coins = before['coins'] if before else 0
    
    # Даём 10 монет (без проверки даты!)
    if before:
        c.execute("UPDATE neko_coins SET coins = coins + 10 WHERE user_id = %s", (user_id,))
    else:
        c.execute("INSERT INTO neko_coins (user_id, coins, last_bonus) VALUES (%s, 10, %s)",
                  (user_id, today))
    
    conn.commit()
    
    # Проверяем баланс после
    c.execute("SELECT coins FROM neko_coins WHERE user_id = %s", (user_id,))
    after = c.fetchone()
    after_coins = after['coins'] if after else 0
    
    conn.close()
    
    # Показываем результат
    await query.edit_message_text(
        f"💰 Было: {before_coins}🪙\n"
        f"➕ +10🪙\n"
        f"💎 Стало: {after_coins}🪙\n\n"
        f"✅ Get Bonus РАБОТАЕТ!",
        parse_mode="HTML"
    )
# Buy art menu
async def buy_art_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    conn = sqlite3.connect("game.db")
    c = conn.cursor()
    c.execute("SELECT coins FROM neko_coins WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    balance = result[0] if result else 0
    conn.close()
    
    keyboard = [
        [InlineKeyboardButton("10 SenkoCoins", callback_data="buy_art_10"),
         InlineKeyboardButton("50 SenkoCoins", callback_data="buy_art_50")],
        [InlineKeyboardButton("1 Telegram Star", callback_data="buy_star_1"),
         InlineKeyboardButton("5 Telegram Stars", callback_data="buy_star_5")],
        [InlineKeyboardButton("🔙 Back", callback_data="bonus_back")]
    ]
    
    await query.edit_message_text(
        f"🖼 <b>Buy Art</b>\n\n"
        f"Your balance: {balance} SenkoCoins\n\n"
        f"Choose payment method:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

# Buy art with coins
async def buy_art_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
# =============================================================================
# BLOCK: SENKO COINS & ARTS (NEON VERSION)
# =============================================================================

SENKO_ARTS = {"common": [], "rare": []}

async def channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Слушает посты в каналах и сохраняет арты"""
    if not update.channel_post or not update.channel_post.photo:
        return
    
    chat = update.channel_post.chat
    file_id = update.channel_post.photo[-1].file_id
    
    # Определяем редкость по каналу
    if chat.username == "Senkocommon":
        rarity = "common"
        SENKO_ARTS["common"].append(file_id)
        print(f"✅ Common art добавлен в память: {file_id[:30]}...")
    elif chat.username == "SenkoRare":
        rarity = "rare"
        SENKO_ARTS["rare"].append(file_id)
        print(f"✅ Rare art добавлен в память: {file_id[:30]}...")
    else:
        print(f"❌ Неизвестный канал: {chat.username}")
        return
    
    # Сохраняем в базу данных
    save_art(file_id, rarity)

async def bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT coins FROM neko_coins WHERE user_id = %s", (user_id,))
    result = c.fetchone()
    balance = result['coins'] if result else 0
    conn.close()
    
    keyboard = [
        [InlineKeyboardButton("🎁 Get Bonus", callback_data="get_bonus"),
         InlineKeyboardButton("🖼 Buy Art", callback_data="buy_art_menu")]
    ]
    
    await update.message.reply_text(
        f"🐱 <b>Senko Coins Shop</b>\n\n"
        f"Your balance: {balance}💰\n\n"
        f"Choose option:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def get_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    today = datetime.now().date()
    
    conn = get_db()
    c = conn.cursor()
    
    # Проверяем, получал ли сегодня
    c.execute("SELECT last_bonus FROM neko_coins WHERE user_id = %s", (user_id,))
    result = c.fetchone()
    
    if result and result['last_bonus'] == today:
        await query.edit_message_text(
            "❌ Already got bonus today! Wait 24h.",
            parse_mode="HTML"
        )
        conn.close()
        return
    
    # Даём бонус
    if result:
        c.execute("UPDATE neko_coins SET coins = coins + 10, last_bonus = %s WHERE user_id = %s",
                  (today, user_id))
    else:
        c.execute("INSERT INTO neko_coins (user_id, coins, last_bonus) VALUES (%s, 10, %s)",
                  (user_id, today))
    
    conn.commit()
    
    # Новый баланс
    c.execute("SELECT coins FROM neko_coins WHERE user_id = %s", (user_id,))
    new_balance = c.fetchone()['coins']
    
    conn.close()
    
    # Кнопки
    keyboard = [[
        InlineKeyboardButton("🎁 Get Bonus", callback_data="get_bonus"),
        InlineKeyboardButton("🖼 Buy Art", callback_data="buy_art_menu")
    ]]
    
    await query.edit_message_text(
        f"✅ <b>+10 Senko Coins!</b>\n\nNew balance: {new_balance}🪙",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def buy_art_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT coins FROM neko_coins WHERE user_id = %s", (user_id,))
    result = c.fetchone()
    balance = result['coins'] if result else 0
    conn.close()
    
    lang = user_languages.get(user_id, "en")
    
    if lang == "en":
        text = f"🖼 <b>Art Shop</b>\n\n"
        text += f"Your balance: {balance}🪙\n\n"
        text += f"Choose option:"
        
        buy_common = "🟦 Common (10🪙)"
        buy_rare = "🟪 Rare (50🪙)"
        buy_star_1 = "⭐ 1 Star"
        buy_star_5 = "⭐⭐ 5 Stars"
        sell_text = "💰 Sell Arts"
        leaderboard_text = "🏆 Leaderboard"
        back_text = "🔙 Back"
    else:
        text = f"🖼 <b>Магазин Артов</b>\n\n"
        text += f"Твой баланс: {balance}🪙\n\n"
        text += f"Выбери действие:"
        
        buy_common = "🟦 Обычный (10🪙)"
        buy_rare = "🟪 Редкий (50🪙)"
        buy_star_1 = "⭐ 1 Звезда"
        buy_star_5 = "⭐⭐ 5 Звёзд"
        sell_text = "💰 Продать Арты"
        leaderboard_text = "🏆 Лидерборд"
        back_text = "🔙 Назад"
    
    # 👇 ТЕПЕРЬ ЗДЕСЬ ВСЕ КНОПКИ: и монеты, и звёзды, и продажа, и лидерборд!
    keyboard = [
        [InlineKeyboardButton(buy_common, callback_data="buy_art_10"),
         InlineKeyboardButton(buy_rare, callback_data="buy_art_50")],
        [InlineKeyboardButton(buy_star_1, callback_data="buy_star_1"),
         InlineKeyboardButton(buy_star_5, callback_data="buy_star_5")],
        [InlineKeyboardButton(sell_text, callback_data="sell_menu"),
         InlineKeyboardButton(leaderboard_text, callback_data="art_leaderboard")],
        [InlineKeyboardButton(back_text, callback_data="bonus_back")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def buy_art_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    cost = 10 if "10" in query.data else 50
    rarity = "common" if cost == 10 else "rare"
    user_id = query.from_user.id
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT coins FROM neko_coins WHERE user_id = %s", (user_id,))
    result = c.fetchone()
    
    if not result or result['coins'] < cost:
        await query.edit_message_text(
            f"❌ Not enough SenkoCoins!\nNeed: {cost}, You have: {result['coins'] if result else 0}",
            parse_mode="HTML"
        )
        conn.close()
        return
    
    if not SENKO_ARTS[rarity]:
        await query.edit_message_text(
            "❌ No arts available yet!\nTry again later.",
            parse_mode="HTML"
        )
        conn.close()
        return
    
    file_id = random.choice(SENKO_ARTS[rarity])
    
    # 👇 ЭТИ 2 СТРОЧКИ НАДО ДОБАВИТЬ (после выбора file_id, до отправки)
    c.execute("""
        INSERT INTO art_collections (user_id, art_id, rarity, opened_at) 
        VALUES (%s, %s, %s, %s)
    """, (user_id, file_id, rarity, datetime.now()))
    
    await update_art_leaderboard(user_id, conn)  # эта функция будет ниже
    
    # Списание монет
    c.execute("UPDATE neko_coins SET coins = coins - %s WHERE user_id = %s", (cost, user_id))
    conn.commit()
    conn.close()
    
    await context.bot.send_photo(
        chat_id=user_id,
        photo=file_id,
        caption=f"🎨 <b>Your {rarity} art!</b>",
        parse_mode="HTML"
    )
    
    keyboard = [
        [InlineKeyboardButton("🎁 Get Bonus", callback_data="get_bonus"),
         InlineKeyboardButton("🖼 Buy Art", callback_data="buy_art_menu")]
    ]
    
    await query.edit_message_text(
        f"✅ Art sent! Check your PM.\n\nChoose option:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def buy_star(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    cost = 1 if "1" in query.data else 5
    rarity = "common" if cost == 1 else "rare"
    user_id = query.from_user.id
    
    # Проверяем, есть ли арты в этой категории
    if not SENKO_ARTS.get(rarity):
        await query.edit_message_text(
            f"❌ No {rarity} arts available yet!\nTry again later.",
            parse_mode="HTML"
        )
        return
    
    # Создаём инвойс для оплаты
    try:
        invoice_link = await context.bot.create_invoice_link(
            title="🎨 Senko Art",
            description=f"Get a random {rarity} Senko art!",
            payload=f"art_{rarity}_{user_id}_{random.randint(1000,9999)}",
            provider_token="",  # Для Stars оставляем пустым
            currency="XTR",       # XTR = Telegram Stars
            prices=[{"label": f"{rarity.capitalize()} Art", "amount": cost}]
        )
        
        # Отправляем ссылку на оплату
        await query.edit_message_text(
            f"⭐ <b>Pay {cost} Star{'s' if cost > 1 else ''}</b>\n\n"
            f"[Click here to pay]({invoice_link})\n\n"
            f"After payment, you'll automatically receive your art!",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Error: {e}")
        print(f"❌ Invoice error: {e}")

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обязательно подтверждаем оплату"""
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выдаём арт после успешной оплаты"""
    user_id = update.effective_user.id
    payment = update.message.successful_payment
    
    # Парсим payload, чтобы узнать, какой арт нужен
    try:
        payload_parts = payment.invoice_payload.split("_")
        rarity = payload_parts[1]  # common или rare
    except:
        rarity = "common"  # на всякий случай
    
    # Берём случайный арт из нужной категории
    if SENKO_ARTS.get(rarity) and SENKO_ARTS[rarity]:
        file_id = random.choice(SENKO_ARTS[rarity])
        
        # Отправляем арт
        await context.bot.send_photo(
            chat_id=user_id,
            photo=file_id,
            caption=f"🎨 <b>Thank you for your support!</b>\n\nHere's your {rarity} Senko art! ❤️",
            parse_mode="HTML"
        )
        
        # Подтверждение в чат
        await update.message.reply_text(
            f"✅ Art sent! Check your PM."
        )
    else:
        # Если артов нет (маловероятно, но вдруг)
        await update.message.reply_text(
            f"✅ Payment received! But no arts available yet.\n"
            f"We'll add them soon!"
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
🎮 TRIBES: LAST STANDING — COMPLETE GAMEPLAY GUIDE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔹 ENGLISH 🇬🇧

📊 RESOURCES:
• 🍞 Food — consumed by units. If Food < 0, population STARVES!
• 🙏 Faith — prevents rebellions. If Faith < 200, 10% REVOLT chance
• ⚒ Labor — for building
• ❤️ Health — unit health
• 🔪 Bloodlust — damage (CANNOT increase!)
• 🧠 Intelligence — crit chance: 100 Int = 1% x2 damage
• 😔 Depression — +1 per round. At 100 — YOU LOSE!
• 😈 Hate — crit chance in battle
• 💰 Money — upgrade limits
• 📦 Materials — construction
• ⚡ Dev Points — main currency

🏗️ BUILDINGS:
• 🏠 House — +1% population growth
• 🌱 Farm — +50 food
• 🪵 Sawmill — +20 materials
• ⛪ Church — +50 faith
• ⚒ Forge — +10 bloodlust
• 🔬 Laboratory — +20 intelligence
• 🕳 Mine — +100 materials
• 💰 Tax Office — +30 money
• 📚 Library — +50 intelligence
• 🪦 Necropolis — resurrect 10% after battle
• 🌳 Sacred Grove — +1000 health, +500 bloodlust, +1000 faith
• 🔥 Hell — +10 population
• 🦴 Bone Throne — no food consumption
• ⚙ Steam Engine — +500 dev points

⚔️ WAR:
• Attack with 40% army
• Damage = attackers × bloodlust vs defenders × health
• Critical hits (Hate) = x2 damage
• Turn passes after attack

😔 DEPRESSION:
• Depression +1 per round
• Cure: 100 Faith + 10 Food + 10 Labor = -1 depression
• If Faith < 200: 10% REVOLT chance
• Revolt = -30% population +5 depression

🐱 SENKO COINS:
• /bonus — 10 daily
• /open 10 — common art
• /open 50 — RARE art (🔥 HORNY! 🔥)
• /suggest — propose art (100-500 coins!)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔹 РУССКИЙ 🇷🇺

📊 РЕСУРСЫ:
• 🍞 Еда — если < 0, население ГОЛОДАЕТ
• 🙏 Вера — если < 200, 10% БУНТ
• ⚒ Труд — для построек
• ❤️ Жизнь — здоровье
• 🔪 Кровожадность — урон (НЕ улучшается)
• 🧠 Интеллект — 100 = 1% крита x2
• 😔 Депрессия — +1 за раунд. При 100 — ТЫ ПРОИГРАЛ!
• 😈 Ненависть — шанс крита
• 💰 Деньги — улучшение лимитов
• 📦 Материалы — стройка
• ⚡ Очки развития — основная валюта

🏗️ ПОСТРОЙКИ:
• 🏠 Дом — +1% роста
• 🌱 Ферма — +50 еды
• 🪵 Лесопилка — +20 материалов
• ⛪ Церковь — +50 веры
• ⚒ Кузница — +10 кровожадности
• 🔬 Лаборатория — +20 интеллекта
• 🕳 Шахта — +100 материалов
• 💰 Налоговая — +30 денег
• 📚 Библиотека — +50 интеллекта
• 🪦 Некрополь — воскрешение 10%
• 🌳 Священная роща — +1000 жизни, +500 кровожадности, +1000 веры
• 🔥 Преисподняя — +10 населения
• 🦴 Костяной трон — отключает голод
• ⚙ Паровая машина — +500 очков

⚔️ ВОЙНА:
• Атака 40% армии
• Урон = атакующие × кровожадность vs защитники × жизнь
• Криты от ненависти = x2 урона
• После атаки ход передаётся

😔 ДЕПРЕССИЯ:
• Лечение: 100 веры + 10 еды + 10 труда = -1 депрессии
• Бунт = -30% населения +5 депрессии

🐱 СЕНКО-КОИНЫ:
• /bonus — 10 в день
• /open 10 — обычный арт
• /open 50 — РЕДКИЙ арт (🔥 ХОРНИ! 🔥)
• /suggest — предложить арт (100-500 монет!)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Commands / Команды:
/start - Begin / Начать
/suggest - Suggest art / Предложить арт
/balance - Race stats / Статистика рас
/stats - Your progress / Твой прогресс
/bonus - Daily Senko coins / Ежедневные монеты
/howtoplay - This message / Это сообщение
    """
    
    await update.message.reply_text(text)  # БЕЗ parse_mode!
    
async def bonus_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT coins FROM neko_coins WHERE user_id = %s", (user_id,))
    result = c.fetchone()
    balance = result['coins'] if result else 0
    conn.close()
    
    keyboard = [
        [InlineKeyboardButton("🎁 Get Bonus", callback_data="get_bonus"),
         InlineKeyboardButton("🖼 Buy Art", callback_data="buy_art_menu")]
    ]
    
    await query.edit_message_text(
        f"🐱 <b>Senko Coins Shop</b>\n\n"
        f"Your balance: {balance}💰\n\n"
        f"Choose option:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def reload_arts_from_channels(bot):
    """Загружает все file_id из каналов при старте бота (С ОТЛАДКОЙ)"""
    print("🔴🔴🔴 НАЧАЛО ЗАГРУЗКИ АРТОВ 🔴🔴🔴")
    
    SENKO_ARTS["common"] = []
    SENKO_ARTS["rare"] = []
    
    try:
        # Пробуем получить информацию о каналах
        try:
            common_chat = await bot.get_chat("@Senkocommon")
            print(f"✅ Канал @Senkocommon найден: {common_chat.title}")
        except Exception as e:
            print(f"❌ Канал @Senkocommon НЕ НАЙДЕН: {e}")
        
        try:
            rare_chat = await bot.get_chat("@SenkoRare")
            print(f"✅ Канал @SenkoRare найден: {rare_chat.title}")
        except Exception as e:
            print(f"❌ Канал @SenkoRare НЕ НАЙДЕН: {e}")
        
        # Читаем обычный канал
        print("📥 Читаю @Senkocommon...")
        common_count = 0
        async for message in bot.get_chat_history("@Senkocommon", limit=100):
            if message.photo:
                file_id = message.photo[-1].file_id
                SENKO_ARTS["common"].append(file_id)
                common_count += 1
                print(f"  ✅ Common арт #{common_count}: {file_id[:30]}...")
            else:
                print(f"  ⏭️ Не фото: {message.date}")
        
        # Читаем редкий канал
        print("📥 Читаю @SenkoRare...")
        rare_count = 0
        async for message in bot.get_chat_history("@SenkoRare", limit=100):
            if message.photo:
                file_id = message.photo[-1].file_id
                SENKO_ARTS["rare"].append(file_id)
                rare_count += 1
                print(f"  ✅ Rare арт #{rare_count}: {file_id[:30]}...")
            else:
                print(f"  ⏭️ Не фото: {message.date}")
        
        print(f"✅ ИТОГО: common={len(SENKO_ARTS['common'])}, rare={len(SENKO_ARTS['rare'])}")
        
    except Exception as e:
        print(f"❌ ГЛОБАЛЬНАЯ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    
    print("🔴🔴🔴 КОНЕЦ ЗАГРУЗКИ 🔴🔴🔴")

# =============================================================================
# БЛОК: ПРОДАЖА АРТОВ И ЛИДЕРБОРД
# =============================================================================

async def update_art_leaderboard(user_id, conn=None):
    """Обновляет запись в лидерборде для пользователя"""
    should_close = False
    if not conn:
        conn = get_db()
        should_close = True
    
    c = conn.cursor()
    
    # Считаем количество уникальных артов у пользователя
    c.execute("""
        SELECT COUNT(DISTINCT art_id) as unique_count 
        FROM art_collections 
        WHERE user_id = %s
    """, (user_id,))
    
    result = c.fetchone()
    unique_count = result['unique_count'] if result else 0
    
    # Обновляем лидерборд
    c.execute("""
        INSERT INTO art_leaderboard (user_id, unique_arts, last_updated) 
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) 
        DO UPDATE SET 
            unique_arts = %s,
            last_updated = %s
    """, (user_id, unique_count, datetime.now(), unique_count, datetime.now()))
    
    conn.commit()
    
    if should_close:
        conn.close()

async def sell_art_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню продажи артов"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    lang = user_languages.get(user_id, "en")
    
    conn = get_db()
    c = conn.cursor()
    
    # Получаем все уникальные арты пользователя
    c.execute("""
        SELECT art_id, rarity, COUNT(*) as count 
        FROM art_collections 
        WHERE user_id = %s 
        GROUP BY art_id, rarity
    """, (user_id,))
    
    user_arts = c.fetchall()
    
    if not user_arts:
        if lang == "en":
            text = "🖼️ <b>You don't have any arts yet!</b>\nBuy some first!"
            back_text = "🔙 Back"
        else:
            text = "🖼️ <b>У тебя ещё нет артов!</b>\nСначала купи!"
            back_text = "🔙 Назад"
        
        keyboard = [[InlineKeyboardButton(back_text, callback_data="bonus_back")]]
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        conn.close()
        return
    
    if lang == "en":
        text = f"💰 <b>Sell Your Arts</b>\n\n"
        text += f"Common: 5🪙 each\n"
        text += f"Rare: 25🪙 each\n\n"
        text += f"Choose an art to sell:\n"
        sell_text = "Sell"
        back_text = "🔙 Back"
    else:
        text = f"💰 <b>Продажа Артов</b>\n\n"
        text += f"Обычный: 5🪙 за штуку\n"
        text += f"Редкий: 25🪙 за штуку\n\n"
        text += f"Выбери арт для продажи:\n"
        sell_text = "Продать"
        back_text = "🔙 Назад"
    
    keyboard = []
    for art in user_arts:
        # Берём короткий ID для callback
        short_id = art['art_id'][-20:] if len(art['art_id']) > 20 else art['art_id']
        rarity_emoji = "🟦" if art['rarity'] == "common" else "🟪"
        price = 5 if art['rarity'] == "common" else 25
        
        button_text = f"{rarity_emoji} {art['rarity'].capitalize()} x{art['count']} | {price}🪙"
        keyboard.append([InlineKeyboardButton(
            button_text,
            callback_data=f"sell_{short_id}_{art['rarity']}_{user_id}"
        )])
    
    keyboard.append([InlineKeyboardButton(back_text, callback_data="bonus_back")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    
    conn.close()

async def sell_art_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение продажи арта"""
    query = update.callback_query
    await query.answer()
    
    data = query.data.split("_")
    short_id = data[1]
    rarity = data[2]
    user_id = int(data[3])
    
    if query.from_user.id != user_id:
        return
    
    lang = user_languages.get(user_id, "en")
    
    # Находим полный file_id в базе
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT file_id FROM arts WHERE file_id LIKE %s", (f"%{short_id}%",))
    result = c.fetchone()
    
    if not result:
        if lang == "en":
            text = "❌ Art not found!"
        else:
            text = "❌ Арт не найден!"
        
        keyboard = [[InlineKeyboardButton(
            "🔙 Back" if lang == "en" else "🔙 Назад",
            callback_data="sell_menu"
        )]]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        conn.close()
        return
    
    file_id = result['file_id']
    price = 5 if rarity == "common" else 25
    
    if lang == "en":
        text = f"💰 <b>Sell {rarity.capitalize()} Art?</b>\n\n"
        text += f"You will receive: {price}🪙\n\n"
        text += f"This art will be removed from your collection."
        confirm_text = "✅ Sell"
        cancel_text = "❌ Cancel"
    else:
        text = f"💰 <b>Продать {rarity} арт?</b>\n\n"
        text += f"Ты получишь: {price}🪙\n\n"
        text += f"Этот арт исчезнет из твоей коллекции."
        confirm_text = "✅ Продать"
        cancel_text = "❌ Отмена"
    
    keyboard = [
        [InlineKeyboardButton(confirm_text, callback_data=f"sell_confirm_{file_id[-20:]}_{rarity}_{user_id}")],
        [InlineKeyboardButton(cancel_text, callback_data="sell_menu")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    
    conn.close()

async def sell_art_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполняет продажу арта"""
    query = update.callback_query
    await query.answer()
    
    data = query.data.split("_")
    short_id = data[2]
    rarity = data[3]
    user_id = int(data[4])
    
    if query.from_user.id != user_id:
        return
    
    lang = user_languages.get(user_id, "en")
    
    conn = get_db()
    c = conn.cursor()
    
    # Находим полный file_id
    c.execute("SELECT file_id FROM arts WHERE file_id LIKE %s", (f"%{short_id}%",))
    result = c.fetchone()
    
    if not result:
        if lang == "en":
            text = "❌ Art not found!"
        else:
            text = "❌ Арт не найден!"
        
        keyboard = [[InlineKeyboardButton(
            "🔙 Back" if lang == "en" else "🔙 Назад",
            callback_data="sell_menu"
        )]]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        conn.close()
        return
    
    file_id = result['file_id']
    price = 5 if rarity == "common" else 25
    
    # Удаляем ОДИН экземпляр арта из коллекции
    c.execute("""
        DELETE FROM art_collections 
        WHERE user_id = %s AND art_id = %s 
        LIMIT 1
    """, (user_id, file_id))
    
    if c.rowcount == 0:
        if lang == "en":
            text = "❌ You don't own this art!"
        else:
            text = "❌ У тебя нет этого арта!"
        
        keyboard = [[InlineKeyboardButton(
            "🔙 Back" if lang == "en" else "🔙 Назад",
            callback_data="sell_menu"
        )]]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        conn.close()
        return
    
    # Добавляем монеты
    c.execute("""
        INSERT INTO neko_coins (user_id, coins, last_bonus) 
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) 
        DO UPDATE SET coins = neko_coins.coins + %s
    """, (user_id, price, datetime.now().date(), price))
    
    # Обновляем лидерборд
    await update_art_leaderboard(user_id, conn)
    
    # Получаем новый баланс
    c.execute("SELECT coins FROM neko_coins WHERE user_id = %s", (user_id,))
    new_balance = c.fetchone()['coins']
    
    conn.commit()
    conn.close()
    
    if lang == "en":
        text = f"✅ <b>Art sold!</b>\n\n"
        text += f"+{price}🪙\n"
        text += f"New balance: {new_balance}🪙"
        menu_text = "📊 Leaderboard"
        back_text = "🔙 Menu"
    else:
        text = f"✅ <b>Арт продан!</b>\n\n"
        text += f"+{price}🪙\n"
        text += f"Новый баланс: {new_balance}🪙"
        menu_text = "📊 Лидерборд"
        back_text = "🔙 Меню"
    
    keyboard = [
        [InlineKeyboardButton(menu_text, callback_data="art_leaderboard")],
        [InlineKeyboardButton(back_text, callback_data="bonus_back")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def art_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает топ игроков по количеству уникальных артов"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    lang = user_languages.get(user_id, "en")
    
    conn = get_db()
    c = conn.cursor()
    
    # Получаем топ-10 игроков
    c.execute("""
        SELECT user_id, unique_arts 
        FROM art_leaderboard 
        ORDER BY unique_arts DESC 
        LIMIT 10
    """)
    
    leaders = c.fetchall()
    
    if not leaders:
        if lang == "en":
            text = "📊 <b>Art Leaderboard</b>\n\n"
            text += "No collectors yet!\n"
            text += "Buy arts to be the first! 🎨"
            back_text = "🔙 Back"
        else:
            text = "📊 <b>Лидерборд Артов</b>\n\n"
            text += "Пока нет коллекционеров!\n"
            text += "Купи арты, чтобы стать первым! 🎨"
            back_text = "🔙 Назад"
        
        keyboard = [[InlineKeyboardButton(back_text, callback_data="bonus_back")]]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        conn.close()
        return
    
    # Получаем позицию текущего пользователя
    c.execute("""
        SELECT COUNT(*) + 1 as rank 
        FROM art_leaderboard 
        WHERE unique_arts > (
            SELECT unique_arts FROM art_leaderboard WHERE user_id = %s
        )
    """, (user_id,))
    
    rank_result = c.fetchone()
    user_rank = rank_result['rank'] if rank_result else "???"
    
    # Получаем количество артов у текущего пользователя
    c.execute("SELECT unique_arts FROM art_leaderboard WHERE user_id = %s", (user_id,))
    user_result = c.fetchone()
    user_arts = user_result['unique_arts'] if user_result else 0
    
    if lang == "en":
        text = "📊 <b>🏆 ART LEADERBOARD 🏆</b>\n\n"
        text += "Top collectors:\n\n"
        
        medal_emojis = ["🥇", "🥈", "🥉"]
        for i, leader in enumerate(leaders):
            medal = medal_emojis[i] if i < 3 else f"{i+1}."
            
            # Получаем username
            try:
                chat_member = await context.bot.get_chat_member(query.message.chat.id, leader['user_id'])
                username = chat_member.user.username or chat_member.user.first_name or f"User{leader['user_id']}"
            except:
                username = f"User{leader['user_id']}"
            
            text += f"{medal} {username}: {leader['unique_arts']} arts\n"
        
        text += f"\n🔹 Your rank: #{user_rank} with {user_arts} arts"
        back_text = "🔙 Back"
        refresh_text = "🔄 Refresh"
    else:
        text = "📊 <b>🏆 ЛИДЕРБОРД АРТОВ 🏆</b>\n\n"
        text += "Лучшие коллекционеры:\n\n"
        
        medal_emojis = ["🥇", "🥈", "🥉"]
        for i, leader in enumerate(leaders):
            medal = medal_emojis[i] if i < 3 else f"{i+1}."
            
            try:
                chat_member = await context.bot.get_chat_member(query.message.chat.id, leader['user_id'])
                username = chat_member.user.username or chat_member.user.first_name or f"User{leader['user_id']}"
            except:
                username = f"User{leader['user_id']}"
            
            text += f"{medal} {username}: {leader['unique_arts']} артов\n"
        
        text += f"\n🔹 Твой ранг: #{user_rank} с {user_arts} артами"
        back_text = "🔙 Назад"
        refresh_text = "🔄 Обновить"
    
    keyboard = [
        [InlineKeyboardButton(refresh_text, callback_data="art_leaderboard")],
        [InlineKeyboardButton(back_text, callback_data="bonus_back")]
    ]
    
    conn.close()
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
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
# БЛОК 10: ЗАПУСК (бот + фласк в разных потоках) - ИСПРАВЛЕНО!
# =============================================================================
def run_bot():
    app = Application.builder().token(TOKEN).build()
    
    # 👇 ЗАГРУЖАЕМ АРТЫ ИЗ БАЗЫ ПРИ СТАРТЕ
    load_arts_from_db()
    
    # 👇 ПРОВЕРЯЕМ СБРОС СТАТИСТИКИ
    async def check_reset(context):
        await check_weekly_reset()
    app.job_queue.run_once(check_reset, 0)
    
    # ===== 1. КОМАНДЫ =====
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("suggest", suggest))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("howtoplay", help_command))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("bonus", bonus))
    
    # ===== 2. ОСНОВНАЯ ИГРА =====
    app.add_handler(CallbackQueryHandler(cure_depression, pattern="cure_depression_"))
    app.add_handler(CallbackQueryHandler(construct, pattern="construct_"))
    app.add_handler(CallbackQueryHandler(my_city, pattern="mycity_"))
    app.add_handler(CallbackQueryHandler(build_menu, pattern="build_"))
    app.add_handler(CallbackQueryHandler(war, pattern="war_"))
    app.add_handler(CallbackQueryHandler(end_turn, pattern="endturn_"))
    app.add_handler(CallbackQueryHandler(confirm_endturn, pattern="confirm_endturn_"))
    app.add_handler(CallbackQueryHandler(cancel_endturn, pattern="cancel_endturn_"))
    app.add_handler(CallbackQueryHandler(delete_steal, pattern="delete_steal_"))
    app.add_handler(CallbackQueryHandler(delete_events, pattern="delete_events_"))
    app.add_handler(CallbackQueryHandler(attack, pattern="attack_"))
    app.add_handler(CallbackQueryHandler(upgrade_menu, pattern="upgrade_menu_"))
    app.add_handler(CallbackQueryHandler(do_upgrade, pattern="upgrade_"))
    app.add_handler(CallbackQueryHandler(income, pattern="income_"))
    
    # ===== 3. КОМНАТЫ И РАСЫ =====
    app.add_handler(CallbackQueryHandler(choose_race, pattern="race_"))
    app.add_handler(CallbackQueryHandler(new_game, pattern="new_game"))
    app.add_handler(CallbackQueryHandler(play_game, pattern="play_"))
    app.add_handler(CallbackQueryHandler(cancel_game, pattern="cancel_"))
    app.add_handler(CallbackQueryHandler(back_button, pattern="back_"))
    app.add_handler(CallbackQueryHandler(back_to_game, pattern="back_to_game_"))
    
    # ===== 4. ЯЗЫК =====
    app.add_handler(CallbackQueryHandler(language_menu, pattern="language"))
    app.add_handler(CallbackQueryHandler(set_language, pattern="setlang_"))
    
    # ===== 5. АРТЫ И МАГАЗИН =====
    app.add_handler(MessageHandler(
        filters.Chat(username="@Senkocommon") | filters.Chat(username="@SenkoRare"), 
        channel_post
    ))
    
    app.add_handler(CallbackQueryHandler(get_bonus, pattern="^get_bonus$"))
    app.add_handler(CallbackQueryHandler(buy_art_menu, pattern="^buy_art_menu$"))
    app.add_handler(CallbackQueryHandler(bonus_back, pattern="^bonus_back$"))
    app.add_handler(CallbackQueryHandler(buy_art_coins, pattern="^buy_art_10$"))
    app.add_handler(CallbackQueryHandler(buy_art_coins, pattern="^buy_art_50$"))
    app.add_handler(CallbackQueryHandler(buy_star, pattern="^buy_star_1$"))
    app.add_handler(CallbackQueryHandler(buy_star, pattern="^buy_star_5$"))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(CallbackQueryHandler(sell_art_menu, pattern="^sell_menu$"))
    app.add_handler(CallbackQueryHandler(sell_art_confirm, pattern="^sell_"))
    app.add_handler(CallbackQueryHandler(sell_art_execute, pattern="^sell_confirm_"))
    app.add_handler(CallbackQueryHandler(art_leaderboard, pattern="^art_leaderboard$"))
    
    # ===== 6. ПРЕДЛОЖЕНИЯ АРТОВ =====
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_suggested_art))
    app.add_handler(CallbackQueryHandler(suggest_approve, pattern="^sug_c_"))
    app.add_handler(CallbackQueryHandler(suggest_approve, pattern="^sug_r_"))
    app.add_handler(CallbackQueryHandler(suggest_reject, pattern="^sug_x_"))
    
    # 👇 ЗАПУСКАЕМ БОТА (он БЛОКИРУЕТ выполнение)
    app.run_polling()

# ===== ЭТОТ БЛОК ТЕПЕРЬ ВНЕ ФУНКЦИИ run_bot() =====
if __name__ == "__main__":
    import threading
    
    # Запускаем Flask в отдельном потоке
    flask_app = Flask(__name__)
    
    @flask_app.route('/')
    def index(): return '🤖 Bot is running!'
    
    @flask_app.route('/health')
    def health(): return 'OK'
    
    flask_thread = threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
    )
    flask_thread.daemon = True
    flask_thread.start()
    
    # 👇 ЗАПУСКАЕМ БОТА ТОЛЬКО ОДИН РАЗ!
    run_bot()
