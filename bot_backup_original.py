"""
Семейная ферма — Телеграм/MAX бот на библиотеке aiomax.
Помощник для родителей (Мамы, Папы и Вадима).

Ключевой функционал:
1. Для клиентов: быстрый кнопочный маркетплейс (Каменка или Кургановка).
2. Для Мамы: сводка "Что приготовить" — суммирует все заказы на нужное направление (сколько литров молока, кг творога, сыров и т.д.).
3. Для Папы:
   - Кнопка "🚚 Я выехал на маршрут" (оповещает клиентов и выдает путевой лист с адресами и суммами).
   - Кнопка "🏠 Я приехал / Завершить рейс" — закрывает рейс.
4. Календарик и база истории:
   - При завершении рейса день автоматически заносится в БД Календаря.
   - Папа и Мама могут в любой момент открыть "📅 История рейсов" и посмотреть: когда, куда, чего и насколько продали!

Совместимость с aiomax и MAX:
- ВСЕ кнопки строго распределены по рядам через kb.row (устранена ошибка errors.maxRowSize).
- Пароли скрыты при вводе команды /admin.
- Путь к базе SQLite привязан к расположению скрипта.
"""

import asyncio
import datetime
import html
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional, List

import aiomax
import aiosqlite
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Настройка логирования
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("farm_bot")
STARTED_AT = time.monotonic()

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в переменных окружения!")

# База данных создается строго рядом с файлом бота (на Рабочем столе)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "farm_data.db"))

bot = aiomax.Bot(TOKEN, default_format="html", use_certificate=True)

# ---------------------------------------------------------------------------
# Направления доставки
# ---------------------------------------------------------------------------
ROUTES = {
    "kamenka": {
        "name": "г. Каменка",
        "days": "Среда",
        "full_title": "г. Каменка (Среда)"
    },
    "kurganovka": {
        "name": "с. Кургановка",
        "days": "Вторник, Пятница",
        "full_title": "с. Кургановка (Вторник, Пятница)"
    }
}

# Категории товаров
CATEGORIES = {
    "milk": "🥛 Молоко",
    "sour": "🥣 Творог/Сметана",
    "oil": "🧈 Масла",
    "cheese": "🧀 Сыры"
}

CATALOG_DEFAULT = {
    # Молоко
    "milk_3l": {"name": "🥛 Молоко 3л", "price": 270, "unit": "шт", "stock": 25, "cat": "milk"},
    "milk_1_5l": {"name": "🥛 Молоко 1.5л", "price": 150, "unit": "шт", "stock": 25, "cat": "milk"},
    "cream_1l": {"name": "🍶 Сливки 1л", "price": 700, "unit": "шт", "stock": 10, "cat": "milk"},
    "ryazhenka_0_5l": {"name": "🧃 Ряженка 0.5л", "price": 140, "unit": "шт", "stock": 15, "cat": "milk"},
    "condensed_milk_0_5l": {"name": "🥫 Сгущенка 0.5л", "price": 450, "unit": "шт", "stock": 10, "cat": "milk"},

    # Творог и сметана
    "sour_cream_1l": {"name": "🥣 Сметана 1л", "price": 700, "unit": "шт", "stock": 12, "cat": "sour"},
    "cottage_fat_free": {"name": "🥣 Творог обезжир. 1кг", "price": 300, "unit": "кг", "stock": 10, "cat": "sour"},
    "cottage_whole": {"name": "🥣 Творог цельный 1кг", "price": 550, "unit": "кг", "stock": 10, "cat": "sour"},

    # Масла
    "butter_1kg": {"name": "🧈 Масло сливочное 1кг", "price": 1400, "unit": "кг", "stock": 8, "cat": "oil"},
    "ghee_1kg": {"name": "🍯 Масло гхи 1 кг", "price": 1800, "unit": "кг", "stock": 8, "cat": "oil"},

    # Сыры
    "cheese_belper": {"name": "🧀 Белпер Кнолле 0.5л", "price": 600, "unit": "шт", "stock": 8, "cat": "cheese"},
    "cheese_mozzarella": {"name": "🧀 Моцарелла (200г)", "price": 300, "unit": "порция", "stock": 20, "cat": "cheese"},
    "cheese_kosichka": {"name": "🧀 Косичка (200г)", "price": 300, "unit": "порция", "stock": 20, "cat": "cheese"},
    "cheese_suluguni": {"name": "🧀 Сулугуни (200г)", "price": 300, "unit": "порция", "stock": 20, "cat": "cheese"},
    "cheese_semi_hard": {"name": "🧀 Полутвердые сыры (200г)", "price": 360, "unit": "порция", "stock": 20, "cat": "cheese"},
}


_admin_pin_values = {
    "mom": os.getenv("ADMIN_PIN_MOM"),
    "dad": os.getenv("ADMIN_PIN_DAD"),
    "vadim": os.getenv("ADMIN_PIN_VADIM"),
}
_invalid_admin_pin_roles = [
    role for role, pin in _admin_pin_values.items()
    if not pin or not re.fullmatch(r"\d{6}", pin)
]
if _invalid_admin_pin_roles:
    raise RuntimeError(
        "Задайте в .env шестизначные PIN-коды для ролей: "
        + ", ".join(_invalid_admin_pin_roles)
    )
if len(set(_admin_pin_values.values())) != len(_admin_pin_values):
    raise RuntimeError("PIN-коды администраторов должны быть уникальными.")
ADMIN_PINS = {pin: role for role, pin in _admin_pin_values.items()}
del _admin_pin_values, _invalid_admin_pin_roles

ROLE_NAMES = {
    "mom": "Мама (Склад и сборка заказов)",
    "dad": "Папа (Логистика и доставка)",
    "vadim": "Вадим (Управление)"
}

WAITING_FIO = "waiting_fio"
WAITING_PHONE = "waiting_phone"
WAITING_ADDRESS = "waiting_address"
WAITING_ROUTE = "waiting_route"
WAITING_PIN = "waiting_pin"
REGISTERED = "registered"

# ---------------------------------------------------------------------------
# База данных SQLite с историей и календариком
# ---------------------------------------------------------------------------
_db: Optional[aiosqlite.Connection] = None
_db_lock = asyncio.Lock()


@asynccontextmanager
async def db_access():
    if _db is None:
        raise RuntimeError("База данных не инициализирована.")
    async with _db_lock:
        yield _db


async def close_db():
    global _db
    if _db is not None:
        async with _db_lock:
            await _db.close()
            _db = None


async def _fetch_catalog(conn: aiosqlite.Connection) -> Dict[str, Dict[str, Any]]:
    async with conn.execute("SELECT * FROM catalog") as cursor:
        rows = await cursor.fetchall()
    return {row["item_id"]: dict(row) for row in rows}


async def _fetch_pending_orders(
    conn: aiosqlite.Connection, route_code: str
) -> List[Dict[str, Any]]:
    async with conn.execute("""
        SELECT o.*, u.fio, u.phone, u.address
        FROM orders o
        LEFT JOIN users u ON o.user_id = u.user_id
        WHERE o.route_code = ? AND o.status = 'pending'
        ORDER BY o.order_id ASC
    """, (route_code,)) as cursor:
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def _summarize_orders(
    conn: aiosqlite.Connection, orders: List[Dict[str, Any]]
):
    catalog = await _fetch_catalog(conn)
    total_revenue = sum(order["total_price"] for order in orders)
    items_total: Dict[str, int] = {}
    for order in orders:
        for item_id, count in json.loads(order["items_json"]).items():
            items_total[item_id] = items_total.get(item_id, 0) + count

    summary_list = []
    for item_id, count in items_total.items():
        item = catalog.get(item_id)
        if item:
            summary_list.append({
                "name": item["name"],
                "count": count,
                "unit": item["unit"],
                "sum": count * item["price"]
            })
    return summary_list, total_revenue


async def init_db():
    global _db
    if _db is not None:
        return
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA busy_timeout = 5000")
    async with db_access() as conn:
        schema = (
            """CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY, fio TEXT, phone TEXT, address TEXT,
                route_code TEXT, registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS admins (
                user_id TEXT PRIMARY KEY, role TEXT,
                authorized_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS pin_attempts (
                user_id TEXT PRIMARY KEY, count INTEGER DEFAULT 0,
                blocked_until TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS user_states (
                user_id TEXT PRIMARY KEY, state TEXT, temp_fio TEXT,
                temp_phone TEXT, temp_address TEXT, temp_route_code TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS carts (
                user_id TEXT, item_id TEXT, count INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, item_id)
            )""",
            """CREATE TABLE IF NOT EXISTS user_categories (
                user_id TEXT PRIMARY KEY, category TEXT DEFAULT 'milk'
            )""",
            """CREATE TABLE IF NOT EXISTS catalog (
                item_id TEXT PRIMARY KEY, name TEXT, price INTEGER,
                unit TEXT, stock INTEGER, cat TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT,
                items_json TEXT, total_price INTEGER, route_code TEXT,
                trip_id INTEGER DEFAULT 0, status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                stock_reserved INTEGER DEFAULT 0
            )""",
            """CREATE TABLE IF NOT EXISTS trips (
                trip_id INTEGER PRIMARY KEY AUTOINCREMENT, route_code TEXT,
                route_title TEXT, start_time TEXT, end_time TEXT,
                orders_count INTEGER, total_revenue INTEGER, summary_json TEXT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
        )
        for statement in schema:
            await conn.execute(statement)

        async with conn.execute("PRAGMA table_info(orders)") as cursor:
            order_columns = {row["name"] for row in await cursor.fetchall()}
        if "stock_reserved" not in order_columns:
            await conn.execute(
                "ALTER TABLE orders ADD COLUMN stock_reserved INTEGER DEFAULT 0"
            )
        if "payment_status" not in order_columns:
            await conn.execute(
                "ALTER TABLE orders ADD COLUMN payment_status TEXT DEFAULT 'unknown'"
            )

        await conn.execute(
            "DELETE FROM user_states WHERE updated_at < datetime('now', '-24 hours')"
        )
        await conn.execute(
            "DELETE FROM carts WHERE updated_at < datetime('now', '-7 days')"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_user_created "
            "ON orders(user_id, created_at)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_route_status "
            "ON orders(route_code, status)"
        )
        async with conn.execute("SELECT COUNT(*) FROM catalog") as cursor:
            catalog_count = (await cursor.fetchone())[0]
        if catalog_count == 0:
            for item_id, item in CATALOG_DEFAULT.items():
                await conn.execute(
                    """INSERT INTO catalog
                    (item_id, name, price, unit, stock, cat)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (item_id, item["name"], item["price"], item["unit"],
                     item["stock"], item["cat"])
                )
        await conn.commit()
    logger.info("Асинхронная база данных готова к работе.")


async def get_state(user_id: str) -> Optional[Dict[str, Optional[str]]]:
    async with db_access() as conn:
        async with conn.execute(
            "SELECT * FROM user_states WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None


async def set_state(user_id: str, state: str, **kwargs):
    allowed_fields = {"temp_fio", "temp_phone", "temp_address", "temp_route_code"}
    unknown_fields = set(kwargs) - allowed_fields
    if unknown_fields:
        raise ValueError(f"Неизвестные поля состояния пользователя: {unknown_fields}")
    columns = ["user_id", "state", *kwargs]
    values = [user_id, state, *kwargs.values()]
    updates = ["state = excluded.state", "updated_at = CURRENT_TIMESTAMP"]
    updates.extend(f"{field} = excluded.{field}" for field in kwargs)
    placeholders = ", ".join("?" for _ in columns)
    async with db_access() as conn:
        await conn.execute(
            f"""INSERT INTO user_states ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(user_id) DO UPDATE SET {", ".join(updates)}""",
            values
        )
        await conn.commit()


async def clear_state(user_id: str):
    async with db_access() as conn:
        await conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
        await conn.commit()


async def get_cart(user_id: str) -> Dict[str, int]:
    async with db_access() as conn:
        async with conn.execute(
            "SELECT item_id, count FROM carts WHERE user_id = ?", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        return {row["item_id"]: row["count"] for row in rows}


async def add_to_cart(user_id: str, item_id: str, count: int = 1):
    async with db_access() as conn:
        await conn.execute("""
            INSERT INTO carts (user_id, item_id, count) VALUES (?, ?, ?)
            ON CONFLICT(user_id, item_id) DO UPDATE SET
                count = carts.count + excluded.count,
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, item_id, count))
        await conn.commit()


async def remove_from_cart(user_id: str, item_id: str):
    async with db_access() as conn:
        async with conn.execute(
            "SELECT count FROM carts WHERE user_id = ? AND item_id = ?",
            (user_id, item_id)
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            if row["count"] > 1:
                await conn.execute("""
                    UPDATE carts SET count = count - 1, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND item_id = ?
                """, (user_id, item_id))
            else:
                await conn.execute(
                    "DELETE FROM carts WHERE user_id = ? AND item_id = ?",
                    (user_id, item_id)
                )
        await conn.commit()


async def clear_cart(user_id: str):
    async with db_access() as conn:
        await conn.execute("DELETE FROM carts WHERE user_id = ?", (user_id,))
        await conn.commit()


async def get_active_category(user_id: str) -> str:
    async with db_access() as conn:
        async with conn.execute(
            "SELECT category FROM user_categories WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return row["category"] if row else "milk"


async def set_active_category(user_id: str, category: str):
    async with db_access() as conn:
        await conn.execute("""
            INSERT INTO user_categories (user_id, category) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET category = excluded.category
        """, (user_id, category))
        await conn.commit()


async def get_pin_blocked_minutes(user_id: str) -> Optional[int]:
    async with db_access() as conn:
        async with conn.execute(
            "SELECT blocked_until FROM pin_attempts WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row or not row["blocked_until"]:
            return None
        blocked_until = datetime.datetime.fromisoformat(row["blocked_until"])
        remaining_seconds = (blocked_until - datetime.datetime.now()).total_seconds()
        if remaining_seconds <= 0:
            await conn.execute(
                "UPDATE pin_attempts SET count = 0, blocked_until = NULL "
                "WHERE user_id = ?", (user_id,)
            )
            await conn.commit()
            return None
        return max(1, int((remaining_seconds + 59) // 60))


async def record_failed_pin_attempt(user_id: str) -> Optional[int]:
    async with db_access() as conn:
        async with conn.execute(
            "SELECT count FROM pin_attempts WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        attempt_count = (row["count"] if row else 0) + 1
        blocked_minutes = 10 if attempt_count >= 3 else None
        blocked_until = (
            (datetime.datetime.now() + datetime.timedelta(minutes=10))
            .isoformat(timespec="seconds") if blocked_minutes else None
        )
        await conn.execute("""
            INSERT INTO pin_attempts (user_id, count, blocked_until)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                count = excluded.count, blocked_until = excluded.blocked_until
        """, (user_id, attempt_count, blocked_until))
        await conn.commit()
        return blocked_minutes


async def reset_pin_attempts(user_id: str):
    async with db_access() as conn:
        await conn.execute(
            "UPDATE pin_attempts SET count = 0, blocked_until = NULL WHERE user_id = ?",
            (user_id,)
        )
        await conn.commit()


async def get_user_from_db(user_id: str) -> Optional[Dict[str, Any]]:
    async with db_access() as conn:
        async with conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None


async def save_user_to_db(user_id: str, fio: str, phone: str, address: str, route_code: str):
    async with db_access() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, fio, phone, address, route_code)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                fio=excluded.fio, phone=excluded.phone, address=excluded.address,
                route_code=excluded.route_code
        """, (user_id, fio, phone, address, route_code))
        await conn.commit()
    logger.info(
        "✨ [НОВЫЙ ПОЛЬЗОВАТЕЛЬ] Зарегистрирован: ID=%s | ФИО=%r | "
        "Маршрут=%s", user_id, fio, route_code
    )


async def save_admin_to_db(user_id: str, role: str):
    async with db_access() as conn:
        await conn.execute("""
            INSERT INTO admins (user_id, role) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET role=excluded.role
        """, (user_id, role))
        await conn.commit()
    logger.info(
        "🔑 Администратор вошёл: роль=%s (%s), User ID=%s",
        ROLE_NAMES.get(role, role), role, user_id
    )


async def remove_admin_from_db(user_id: str):
    async with db_access() as conn:
        await conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await conn.commit()


async def reset_user_test_data(user_id: str):
    async with db_access() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            async with conn.execute(
                "SELECT items_json FROM orders WHERE user_id = ? "
                "AND status = 'pending' AND stock_reserved = 1", (user_id,)
            ) as cursor:
                orders = await cursor.fetchall()
            for order in orders:
                for item_id, count in json.loads(order["items_json"]).items():
                    await conn.execute(
                        "UPDATE catalog SET stock = stock + ? WHERE item_id = ?",
                        (count, item_id)
                    )
            await conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            await conn.execute(
                "DELETE FROM orders WHERE user_id = ? AND status = 'pending'",
                (user_id,)
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise


async def get_all_admins_from_db() -> Dict[str, str]:
    async with db_access() as conn:
        async with conn.execute("SELECT user_id, role FROM admins") as cursor:
            rows = await cursor.fetchall()
        return {row["user_id"]: row["role"] for row in rows}


async def get_catalog_items() -> Dict[str, Dict[str, Any]]:
    async with db_access() as conn:
        return await _fetch_catalog(conn)


async def get_low_stock_items(threshold: int = 2) -> List[Dict[str, Any]]:
    async with db_access() as conn:
        async with conn.execute(
            "SELECT * FROM catalog WHERE stock < ? ORDER BY stock ASC",
            (threshold,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]


class DuplicateOrderError(Exception):
    pass


async def save_order_to_db(
    user_id: str, items: Dict[str, int], total_price: int, route_code: str
) -> int:
    if not items or any(
        not isinstance(count, int) or isinstance(count, bool) or count <= 0
        for count in items.values()
    ):
        raise ValueError("Состав заказа содержит некорректное количество товара.")
    async with db_access() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            async with conn.execute("""
                SELECT order_id FROM orders
                WHERE user_id = ? AND created_at >= datetime('now', '-30 seconds')
                LIMIT 1
            """, (user_id,)) as cursor:
                if await cursor.fetchone():
                    raise DuplicateOrderError(
                        "Заказ уже оформлен за последние 30 секунд"
                    )

            for item_id, count in items.items():
                cursor = await conn.execute("""
                    UPDATE catalog SET stock = stock - ?
                    WHERE item_id = ? AND stock >= ?
                """, (count, item_id, count))
                if cursor.rowcount != 1:
                    async with conn.execute(
                        "SELECT stock FROM catalog WHERE item_id = ?", (item_id,)
                    ) as stock_cursor:
                        stock_row = await stock_cursor.fetchone()
                    available = stock_row["stock"] if stock_row else 0
                    raise ValueError(
                        f"Недостаточно остатка: {item_id} "
                        f"(запрошено {count}, есть {available})"
                    )

            cursor = await conn.execute("""
                INSERT INTO orders
                    (user_id, items_json, total_price, route_code, status, stock_reserved)
                VALUES (?, ?, ?, ?, 'pending', 1)
            """, (
                user_id, json.dumps(items, ensure_ascii=False), total_price, route_code
            ))
            order_id = cursor.lastrowid
            await conn.commit()
            return order_id
        except BaseException:
            await conn.rollback()
            raise


async def get_order_for_cancellation(
    order_id: int, user_id: str
) -> Optional[Dict[str, Any]]:
    async with db_access() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            async with conn.execute("""
                SELECT o.*, u.fio
                FROM orders o LEFT JOIN users u ON u.user_id = o.user_id
                WHERE o.order_id = ? AND o.user_id = ? AND o.status = 'pending'
            """, (order_id, user_id)) as cursor:
                row = await cursor.fetchone()
            if not row:
                await conn.rollback()
                return None
            order = dict(row)
            cursor = await conn.execute("""
                UPDATE orders SET status = 'cancelled'
                WHERE order_id = ? AND user_id = ? AND status = 'pending'
            """, (order_id, user_id))
            if cursor.rowcount != 1:
                await conn.rollback()
                return None
            if order["stock_reserved"]:
                for item_id, count in json.loads(order["items_json"]).items():
                    await conn.execute(
                        "UPDATE catalog SET stock = stock + ? WHERE item_id = ?",
                        (count, item_id)
                    )
            await conn.commit()
            return order
        except BaseException:
            await conn.rollback()
            raise


async def get_cart_stock_issues(cart: Dict[str, int]) -> List[str]:
    catalog = await get_catalog_items()
    issues = []
    for item_id, count in cart.items():
        item = catalog.get(item_id)
        if item is None:
            issues.append(f"Товар {item_id} больше не доступен в каталоге.")
        elif item["stock"] <= 0:
            issues.append(f"{item['name']}: товар закончился.")
        elif count > item["stock"]:
            issues.append(
                f"{item['name']}: в наличии {item['stock']}, в корзине {count}."
            )
    return issues


async def get_pending_orders_for_route(route_code: str) -> List[Dict[str, Any]]:
    async with db_access() as conn:
        return await _fetch_pending_orders(conn, route_code)


async def calculate_route_summary(route_code: str):
    async with db_access() as conn:
        orders = await _fetch_pending_orders(conn, route_code)
        summary_list, total_revenue = await _summarize_orders(conn, orders)
    return orders, summary_list, total_revenue


async def get_active_trip() -> Optional[Dict[str, Any]]:
    async with db_access() as conn:
        async with conn.execute(
            "SELECT * FROM trips WHERE status = 'active' "
            "ORDER BY trip_id DESC LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None


async def start_trip(route_code: str) -> Optional[int]:
    route_info = ROUTES.get(route_code, {"full_title": route_code})
    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    async with db_access() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            async with conn.execute(
                "SELECT trip_id FROM trips WHERE status = 'active' LIMIT 1"
            ) as cursor:
                if await cursor.fetchone():
                    await conn.rollback()
                    return None
            orders = await _fetch_pending_orders(conn, route_code)
            if not orders:
                await conn.rollback()
                return None
            summary_list, total_revenue = await _summarize_orders(conn, orders)
            cursor = await conn.execute("""
                INSERT INTO trips
                    (route_code, route_title, start_time, orders_count,
                     total_revenue, summary_json, status)
                VALUES (?, ?, ?, ?, ?, ?, 'active')
            """, (
                route_code, route_info["full_title"], now_str, len(orders),
                total_revenue, json.dumps(summary_list, ensure_ascii=False)
            ))
            trip_id = cursor.lastrowid
            await conn.execute("""
                UPDATE orders SET status = 'in_trip', trip_id = ?
                WHERE route_code = ? AND status = 'pending'
            """, (trip_id, route_code))
            await conn.commit()
            return trip_id
        except BaseException:
            await conn.rollback()
            raise


async def finish_trip(trip_id: int):
    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    async with db_access() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            async with conn.execute(
                "SELECT * FROM trips WHERE trip_id = ?", (trip_id,)
            ) as cursor:
                trip_row = await cursor.fetchone()
            if not trip_row or trip_row["status"] != "active":
                await conn.rollback()
                return
            trip = dict(trip_row)
            async with conn.execute("""
                SELECT o.user_id, o.items_json, o.stock_reserved, u.fio
                FROM orders o LEFT JOIN users u ON u.user_id = o.user_id
                WHERE o.trip_id = ?
            """, (trip_id,)) as cursor:
                delivered_orders = [dict(row) for row in await cursor.fetchall()]

            # Возвращаем прежнее списание для заказов, созданных до резервирования.
            legacy_items: Dict[str, int] = {}
            delivered_items: Dict[str, int] = {}
            for order in delivered_orders:
                try:
                    for item_id, count in json.loads(order["items_json"]).items():
                        delivered_items[item_id] = delivered_items.get(item_id, 0) + count
                        if not order["stock_reserved"]:
                            legacy_items[item_id] = legacy_items.get(item_id, 0) + count
                except (TypeError, ValueError, json.JSONDecodeError):
                    logger.exception(
                        "Не удалось прочитать состав заказа при завершении рейса #%s",
                        trip_id
                    )
            for item_id, count in legacy_items.items():
                await conn.execute(
                    "UPDATE catalog SET stock = MAX(0, stock - ?) WHERE item_id = ?",
                    (count, item_id)
                )
            await conn.execute(
                "UPDATE trips SET status = 'completed', end_time = ? WHERE trip_id = ?",
                (now_str, trip_id)
            )
            await conn.execute(
                "UPDATE orders SET status = 'completed' WHERE trip_id = ?",
                (trip_id,)
            )
            catalog = await _fetch_catalog(conn)
            async with conn.execute("SELECT user_id, role FROM admins") as cursor:
                admins = {row["user_id"]: row["role"] for row in await cursor.fetchall()}
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise

    deductions_text = "\n".join(
        f"• {html.escape(catalog[item_id]['name'])}: {count} "
        f"{html.escape(catalog[item_id]['unit'])}"
        for item_id, count in delivered_items.items() if item_id in catalog
    ) or "Нет списаний."
    admin_message = (
        f"✅ Рейс #{trip_id} завершён.\n"
        f"Маршрут: {html.escape(trip['route_title'])}.\n"
        f"Выручка: {trip['total_revenue']} ₽.\n"
        f"Клиентов: {trip['orders_count']}.\n"
        f"Списано со склада:\n{deductions_text}"
    )
    for admin_id, role in admins.items():
        if role not in ("mom", "vadim"):
            continue
        try:
            await bot.send_message(
                chat_id=int(admin_id), text=encode_html_unicode(admin_message)
            )
        except Exception:
            logger.exception(
                "Не удалось уведомить администратора %s о завершении рейса #%s",
                admin_id, trip_id
            )

    schedule = ROUTES.get(trip["route_code"], {}).get("days", "по согласованию")
    client_message = (
        "🙏 Спасибо за заказ!\n"
        f"Ждём вас снова. График доставки: {schedule}."
    )
    for client_id in {order.get("user_id") for order in delivered_orders} - {None}:
        try:
            await bot.send_message(
                chat_id=int(client_id), text=encode_html_unicode(client_message)
            )
        except Exception:
            logger.exception(
                "Не удалось уведомить клиента %s о завершении рейса #%s",
                client_id, trip_id
            )


async def get_trips_history(limit: int = 15) -> List[Dict[str, Any]]:
    async with db_access() as conn:
        async with conn.execute("""
            SELECT * FROM trips WHERE status = 'completed'
            ORDER BY trip_id DESC LIMIT ?
        """, (limit,)) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_trip_by_id(trip_id: int) -> Optional[Dict[str, Any]]:
    async with db_access() as conn:
        async with conn.execute(
            "SELECT * FROM trips WHERE trip_id = ?", (trip_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

# ---------------------------------------------------------------------------
# Хелперы сообщений
# ---------------------------------------------------------------------------
def get_user_id(ctx) -> int:
    try:
        if hasattr(ctx, 'chat_id') and ctx.chat_id: return int(ctx.chat_id)
        if hasattr(ctx, 'user_id') and ctx.user_id: return int(ctx.user_id)
        if hasattr(ctx, 'from_user') and ctx.from_user and hasattr(ctx.from_user, 'id'): return int(ctx.from_user.id)
        if hasattr(ctx, 'chat') and ctx.chat and hasattr(ctx.chat, 'id'): return int(ctx.chat.id)
        if hasattr(ctx, 'message') and ctx.message:
            if hasattr(ctx.message, 'chat_id') and ctx.message.chat_id: return int(ctx.message.chat_id)
            if hasattr(ctx.message, 'chat') and hasattr(ctx.message.chat, 'id'): return int(ctx.message.chat.id)
    except Exception as e:
        logger.error(f"Ошибка получения ID пользователя: {e}")
    return 0

def extract_payload(cb: aiomax.Callback) -> str:
    for attr in ['payload', 'data', 'callback_data']:
        val = getattr(cb, attr, None)
        if val is not None and str(val):
            return str(val)
    return ''


def encode_html_unicode(text: str) -> str:
    return ''.join(
        char if ord(char) < 128 else f'&#{ord(char)};'
        for char in text
    )


async def reply(ctx, text: str, keyboard=None):
    try:
        text = encode_html_unicode(text)
        if hasattr(ctx, 'reply') and callable(ctx.reply):
            if keyboard: return await ctx.reply(text, keyboard=keyboard)
            return await ctx.reply(text)
        if hasattr(ctx, 'answer') and callable(ctx.answer):
            if keyboard: return await ctx.answer(text, keyboard=keyboard)
            return await ctx.answer(text)
        c_id = get_user_id(ctx)
        if c_id:
            if keyboard:
                return await bot.send_message(
                    chat_id=c_id, text=text, keyboard=keyboard
                )
            return await bot.send_message(chat_id=c_id, text=text)
    except Exception as e:
        logger.error(f"Ошибка в reply: {e}")


async def send_service_message(ctx, user_id: str, text: str, keyboard=None):
    return await reply(ctx, text, keyboard=keyboard)


async def edit_or_reply(ctx, text: str, keyboard=None):
    text = encode_html_unicode(text)
    if not isinstance(ctx, aiomax.Callback):
        await reply(ctx, text, keyboard=keyboard)
        return

    message = getattr(ctx, "message", None)
    mid = (
        getattr(ctx, "message_id", None)
        or getattr(message, "id", None)
        or getattr(message, "message_id", None)
    )
    if mid:
        try:
            await bot.edit_message(message_id=str(mid), text=text, keyboard=keyboard)
            return
        except Exception as e:
            logger.debug(f"bot.edit_message failed: {e}")

    for target in [message, ctx]:
        if not target:
            continue
        for meth_name in ['edit', 'edit_text', 'modify']:
            meth = getattr(target, meth_name, None)
            if callable(meth):
                try:
                    await meth(text, keyboard=keyboard)
                    return
                except Exception as e:
                    logger.debug(f"{meth_name} failed: {e}")

    logger.error(
        "Не удалось отредактировать сообщение callback; "
        "новое сообщение не отправлялось."
    )

def is_valid_phone(phone: str) -> bool:
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)
    return bool(re.match(r'^\+?[0-9]{10,15}$', cleaned))


def normalize_fio(fio: str) -> str:
    return re.sub(r"\s+", " ", fio.strip())


def is_valid_fio(fio: str) -> bool:
    normalized = normalize_fio(fio)
    if len(normalized) > 100:
        return False
    words = normalized.split()
    return len(words) >= 2 and all(
        re.fullmatch(r"[^\W\d_]+(?:-[^\W\d_]+)*", word, re.UNICODE)
        for word in words
    )


def build_registration_back_keyboard():
    kb = aiomax.buttons.KeyboardBuilder()
    kb.row(aiomax.buttons.CallbackButton("🔙 Назад", "registration_back"))
    return kb


# ---------------------------------------------------------------------------
# Клавиатура витрины-маркетплейса (СТРОГО kb.row для MAX, стабильное меню)
# ---------------------------------------------------------------------------
async def build_marketplace_keyboard(
    user_id_str: str,
    active_cat: str = "milk",
    page: int = 0
):
    kb = aiomax.buttons.KeyboardBuilder()
    catalog = await get_catalog_items()
    cart = await get_cart(user_id_str)
    
    total_count = sum(cart.values())
    total_price = sum(cart[k] * catalog[k]["price"] for k in cart if k in catalog)

    # 1. Верхние кнопки: выбор категорий (по 2 кнопки в ряд)
    kb.row(
        aiomax.buttons.CallbackButton(
            "• Молоко •" if active_cat == 'milk' else "🥛 Молоко", "cat_milk"
        ),
        aiomax.buttons.CallbackButton(
            "• Творог •" if active_cat == 'sour' else "🥣 Творог", "cat_sour"
        )
    )
    kb.row(
        aiomax.buttons.CallbackButton(
            "• Масла •" if active_cat == 'oil' else "🧈 Масла", "cat_oil"
        ),
        aiomax.buttons.CallbackButton(
            "• Сыры •" if active_cat == 'cheese' else "🧀 Сыры", "cat_cheese"
        )
    )

    # 2. Товары текущей категории:
    cat_items = [it for it in catalog.values() if it.get("cat") == active_cat]
    page_count = (len(cat_items) + 5) // 6 if len(cat_items) > 8 else 1
    page = min(max(page, 0), page_count - 1)
    page_items = cat_items[page * 6:(page + 1) * 6] if page_count > 1 else cat_items
    for item in page_items:
        item_id = item["item_id"]
        count = cart.get(item_id, 0)
        in_cart_badge = f" ({count})" if count > 0 else ""
        title = item["name"]
        if len(title) > 22:
            title = title[:21] + "…"

        kb.row(aiomax.buttons.CallbackButton(
            f"➕ {title} — {item['price']}₽{in_cart_badge}",
            f"add_{item_id}"
        ))
    if page_count > 1:
        page_buttons = []
        if page > 0:
            page_buttons.append(aiomax.buttons.CallbackButton(
                "⬅️ Назад", f"catalog_page_{active_cat}_{page - 1}"
            ))
        if page + 1 < page_count:
            page_buttons.append(aiomax.buttons.CallbackButton(
                "Вперёд ➡️", f"catalog_page_{active_cat}_{page + 1}"
            ))
        kb.row(*page_buttons)

    # 3. Нижние кнопки: Корзина (в ней весь состав и удаление лишнего) и Главное меню
    if total_count > 0:
        kb.row(aiomax.buttons.CallbackButton(
            f"🛒 Корзина: {total_count} шт ({total_price} ₽) →", "btn_cart"
        ))
    else:
        kb.row(aiomax.buttons.CallbackButton("🛒 Корзина пуста", "btn_cart"))

    kb.row(aiomax.buttons.CallbackButton("🏠 Главное меню", "btn_menu"))
    return kb


async def get_catalog_page_count(category: str) -> int:
    catalog = await get_catalog_items()
    count = sum(1 for item in catalog.values() if item.get("cat") == category)
    return (count + 5) // 6 if count > 8 else 1


async def catalog_page_text(category: str, page: int) -> str:
    page_count = await get_catalog_page_count(category)
    return f"\n\n<i>Страница {page + 1} из {page_count}</i>" if page_count > 1 else ""

# ---------------------------------------------------------------------------
# Главное меню пользователя (ЧИСТО ДЛЯ КЛИЕНТОВ, БЕЗ СЕМЕЙНЫХ ДАННЫХ!)
# ---------------------------------------------------------------------------
async def show_main_menu(ctx):
    user_id_str = str(get_user_id(ctx))
    user_info = await get_user_from_db(user_id_str)

    text = "🐄 <b>Семейная ферма</b>\n\n"
    if user_info:
        route_info = ROUTES.get(user_info['route_code'], {})
        route_title = route_info.get("full_title", "Не указан")
        text += f"Здравствуйте, <b>{html.escape(user_info['fio'])}</b>!\n"
        text += f"📍 Ваш маршрут доставки: <b>{html.escape(route_title)}</b>\n\n"
    
    text += "Выберите раздел на кнопках ниже:"

    kb = aiomax.buttons.KeyboardBuilder()
    kb.row(aiomax.buttons.CallbackButton("🛍 Открыть витрину фермы", "btn_catalog"))
    kb.row(aiomax.buttons.CallbackButton("🛒 Моя корзина", "btn_cart"))
    kb.row(aiomax.buttons.CallbackButton("📞 Контакты фермы", "btn_contacts"))

    if user_id_str in await get_all_admins_from_db():
        kb.row(aiomax.buttons.CallbackButton("⚙️ Админ-панель", "btn_admin_panel"))

    await edit_or_reply(ctx, text, keyboard=kb)

# ---------------------------------------------------------------------------
# Reply-клавиатуры и разделы админ-панели
# ---------------------------------------------------------------------------
ADMIN_BACK = "🔙 Назад в админку"
LOGISTICS_BACK = "🔙 Назад в логистику"
CALENDAR_BACK = "🔙 Назад в календарь"


def build_message_keyboard(rows: List[List[str]]):
    keyboard = aiomax.buttons.KeyboardBuilder()
    for row in rows:
        keyboard.row(*(aiomax.buttons.MessageButton(text) for text in row))
    return keyboard


async def show_admin_panel(ctx):
    user_id_str = str(get_user_id(ctx))
    role = (await get_all_admins_from_db()).get(user_id_str)
    if not role:
        await edit_or_reply(ctx, "Доступ открыт только для членов семьи.")
        return

    logger.info(
        "Администратор %s (%s) открыл панель управления (ID=%s)",
        ROLE_NAMES.get(role, role), role, user_id_str
    )
    active_trip = await get_active_trip()
    low_stock = await get_low_stock_items(2)
    text = (
        "⚙️ <b>Панель управления фермой</b>\n"
        f"Профиль: <b>{html.escape(ROLE_NAMES.get(role, role))} "
        f"({html.escape(role)})</b>\n"
    )
    if low_stock:
        text += f"\n🔔 Товаров с остатком меньше 2: <b>{len(low_stock)}</b>\n"
    if active_trip:
        text += (
            f"\n🚚 Активный рейс #{active_trip['trip_id']}: "
            f"{html.escape(active_trip['route_title'])}\n"
        )
    text += "\nВыберите раздел:"
    keyboard = build_message_keyboard([
        ["📦 Склад", "👥 Клиенты"],
        ["💰 Финансы", "🚚 Логистика (Выезды)"],
        ["📅 Календарь"],
        ["🏠 Выход из админки"],
    ])
    await edit_or_reply(ctx, text, keyboard=keyboard)


async def show_admin_warehouse(ctx):
    await edit_or_reply(
        ctx,
        "📦 <b>Склад</b>\nВыберите действие:",
        keyboard=build_message_keyboard([
            ["➕ Добавить товар"],
            ["📊 Остатки товаров"],
            [ADMIN_BACK],
        ])
    )


async def show_admin_clients(ctx):
    await edit_or_reply(
        ctx,
        "👥 <b>Клиенты</b>\nВыберите действие:",
        keyboard=build_message_keyboard([
            ["👥 Список клиентов", "🔎 Поиск клиента"],
            [ADMIN_BACK],
        ])
    )


async def show_admin_finances(ctx):
    await edit_or_reply(
        ctx,
        "💰 <b>Финансы</b>\nВыберите действие:",
        keyboard=build_message_keyboard([
            ["💰 Выручка", "⚠️ Должники"],
            [ADMIN_BACK],
        ])
    )


async def show_admin_logistics(ctx):
    active_trip = await get_active_trip()
    rows = [
        ["📦 Сводка: Каменка", "📦 Сводка: Кургановка"],
    ]
    if active_trip:
        rows.append([f"🏠 Завершить рейс #{active_trip['trip_id']}"])
    else:
        rows.append(["🚚 Выехал: Каменка", "🚚 Выехал: Кургановка"])
    rows.append([ADMIN_BACK])
    text = "🚚 <b>Логистика и выезды</b>\nСводки заказов и управление рейсом:"
    if active_trip:
        text += (
            f"\n\nАктивный рейс #{active_trip['trip_id']}: "
            f"{html.escape(active_trip['route_title'])}"
        )
    await edit_or_reply(ctx, text, keyboard=build_message_keyboard(rows))


async def show_admin_inventory(ctx):
    catalog = await get_catalog_items()
    if not catalog:
        text = "📦 Каталог пуст."
    else:
        text = "📊 <b>Остатки товаров</b>\n\n" + "\n".join(
            f"• {html.escape(item['name'])}: <b>{item['stock']} "
            f"{html.escape(item['unit'])}</b>"
            for item in catalog.values()
        )
    await edit_or_reply(
        ctx, text,
        keyboard=build_message_keyboard([["🔄 Обновить остатки"], [ADMIN_BACK]])
    )


async def show_admin_debtors(ctx):
    async with db_access() as conn:
        async with conn.execute("""
            SELECT o.order_id, o.total_price, o.created_at, u.fio, u.phone
            FROM orders o LEFT JOIN users u ON u.user_id = o.user_id
            WHERE o.payment_status = 'debt' AND o.status != 'cancelled'
            ORDER BY o.order_id DESC
        """) as cursor:
            debtors = [dict(row) for row in await cursor.fetchall()]
    if not debtors:
        text = "⚠️ <b>Должников нет.</b>"
    else:
        text = "⚠️ <b>Заказы с задолженностью</b>\n\n" + "\n".join(
            f"Заказ №{row['order_id']} — {html.escape(row.get('fio') or 'Клиент')}, "
            f"{html.escape(row.get('phone') or '')}: "
            f"<b>{row['total_price']} ₽</b> ({row['created_at']})"
            for row in debtors
        )
    await edit_or_reply(
        ctx, text, keyboard=build_message_keyboard([["🔄 Обновить должников"], ["🔙 Назад в финансы"]])
    )


async def show_admin_stub(ctx, title: str, back_button: str):
    await edit_or_reply(
        ctx,
        f"🛠 <b>{html.escape(title)}</b>\n\nРаздел в разработке.",
        keyboard=build_message_keyboard([[back_button]])
    )


async def show_admin_search(ctx):
    await edit_or_reply(
        ctx,
        "🔎 Для поиска клиента отправьте команду <code>/find имя</code> "
        "или <code>/find улица</code>.",
        keyboard=build_message_keyboard([[ADMIN_BACK]])
    )

# ---------------------------------------------------------------------------
# Сводка калькулятора по направлению (Каменка или Кургановка)
# ---------------------------------------------------------------------------
async def show_route_calc_summary(ctx, route_code: str):
    route_info = ROUTES.get(route_code, {"full_title": route_code})
    orders, summary_list, total_revenue = await calculate_route_summary(route_code)

    if not orders:
        text = (
            f"📦 <b>Сводка заказов: {route_info['full_title']}</b>\n\n"
            "На это направление сейчас нет ожидающих заказов.\n"
            "Все клиенты обслужены либо еще не оставили заявки."
        )
        kb = build_message_keyboard([[LOGISTICS_BACK], [ADMIN_BACK]])
        await edit_or_reply(ctx, text, keyboard=kb)
        return

    text = (
        f"📋 <b>ЧТО ПРИГОТОВИТЬ: {route_info['full_title']}</b>\n"
        f"Ожидают доставки: <b>{len(orders)} клиентов</b>\n"
        f"Общая сумма рейса: <b>{total_revenue} ₽</b>\n\n"
        "<b>ИТОГОВЫЙ СПИСОК ПРОДУКЦИИ ДЛЯ СБОРА:</b>\n"
    )

    for item in summary_list:
        text += f"• <b>{item['name']}</b>: {item['count']} {item['unit']} ({item['sum']} ₽)\n"

    text += "\n<b>Список клиентов по адресам:</b>\n"
    for idx, ord_data in enumerate(orders, 1):
        text += (
            f"{idx}. {html.escape(ord_data.get('fio') or 'Клиент')} "
            f"({html.escape(ord_data.get('phone') or '')})\n"
            f"   🏠 {html.escape(ord_data.get('address') or '')} — <b>{ord_data['total_price']} ₽</b>\n"
        )

    rows = []
    active_trip = await get_active_trip()
    if not active_trip:
        rows.append([f"🚚 Начать выезд: {route_info['name']}"])
    rows.extend([[LOGISTICS_BACK], [ADMIN_BACK]])
    kb = build_message_keyboard(rows)

    await edit_or_reply(ctx, text, keyboard=kb)

# ---------------------------------------------------------------------------
# Выезд на маршрут и завершение рейса (для Папы)
# ---------------------------------------------------------------------------
async def handle_trip_start(ctx, route_code: str):
    active = await get_active_trip()
    if active:
        await edit_or_reply(ctx, f"⚠️ Сначала завершите текущий активный рейс #{active['trip_id']} ({active['route_title']})!")
        return

    trip_id = await start_trip(route_code)
    route_info = ROUTES[route_code]

    if not trip_id:
        await edit_or_reply(ctx, f"На маршрут {route_info['full_title']} нет активных заказов для выезда!")
        return

    orders = await get_pending_orders_for_route(route_code)
    
    # Оповещаем клиентов этого маршрута
    for ord_data in orders:
        c_id = ord_data.get("user_id")
        if c_id:
            try:
                client_notice = (
                    f"🚚 <b>Здравствуйте, {html.escape(ord_data.get('fio') or '')}!</b>\n\n"
                    f"Машина нашей семейной фермы <b>выехала по вашему маршруту ({route_info['full_title']})</b>!\n"
                    f"Ваш заказ: <b>{ord_data['total_price']} ₽</b>.\n"
                    "Ожидайте прибытия сегодня по адресу: "
                    f"<b>{html.escape(ord_data.get('address') or '')}</b>."
                )
                await bot.send_message(
                    chat_id=int(c_id),
                    text=encode_html_unicode(client_notice)
                )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление клиенту {c_id}: {e}")

    # Уведомление Папе
    text = (
        f"🚀 <b>РЕЙС #{trip_id} УСПЕШНО НАЧАТ!</b>\n\n"
        f"Маршрут: <b>{route_info['full_title']}</b>\n"
        f"Клиентам разосланы оповещения о выезде машины.\n\n"
        "Когда закончите раздачу и вернетесь домой, нажмите кнопку <b>«Я приехал»</b> — "
        "рейс автоматически запишется в календарь продаж!"
    )
    kb = build_message_keyboard([
        [f"🏠 Завершить рейс #{trip_id}"],
        [LOGISTICS_BACK],
        [ADMIN_BACK],
    ])

    await edit_or_reply(ctx, text, keyboard=kb)

async def handle_trip_finish(ctx, trip_id: int):
    trip = await get_trip_by_id(trip_id)
    if not trip or trip.get("status") != "active":
        await edit_or_reply(ctx, "Рейс не найден или уже завершён.")
        return

    await finish_trip(trip_id)
    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")

    text = (
        f"🎉 <b>С ВОЗВРАЩЕНИЕМ ДОМОЙ! РЕЙС #{trip_id} ЗАВЕРШЕН</b>\n\n"
        f"📍 Маршрут: <b>{trip['route_title']}</b>\n"
        f"⏰ Время в пути: с {trip['start_time']} по {now_str}\n"
        f"👥 Обслужено клиентов: <b>{trip['orders_count']}</b>\n"
        f"💰 <b>ИТОГОВАЯ ВЫРУЧКА: {trip['total_revenue']} ₽</b>\n\n"
        "✅ Данные сохранены в базе Календаря продаж!"
    )

    kb = build_message_keyboard([["📅 Открыть календарь продаж"], [ADMIN_BACK]])

    await edit_or_reply(ctx, text, keyboard=kb)

# ---------------------------------------------------------------------------
# Календарик и история продаж
# ---------------------------------------------------------------------------
async def show_calendar(ctx):
    trips = await get_trips_history(10)
    
    if not trips:
        text = (
            "📅 <b>Календарь и история рейсов</b>\n\n"
            "Пока нет завершенных рейсов в базе.\n"
            "Как только Папа нажмет 'Я приехал', здесь появятся отчеты по дням!"
        )
        kb = build_message_keyboard([[ADMIN_BACK]])
        await edit_or_reply(ctx, text, keyboard=kb)
        return

    text = (
        "📅 <b>КАЛЕНДАРЬ И ИСТОРИЯ РЕЙСОВ</b>\n\n"
        "Нажмите на любой рейс ниже, чтобы посмотреть подробный отчет:\n"
        "<i>(когда, куда, чего и насколько продали)</i>\n\n"
    )

    rows = []
    for t in trips:
        date_short = t["start_time"].split()[0]
        route_title = t["route_title"].split("(")[0].strip()
        rows.append([
            f"🗓 Рейс #{t['trip_id']} • {date_short} • "
            f"{route_title} • {t['total_revenue']} ₽"
        ])

    kb = build_message_keyboard(rows + [[ADMIN_BACK]])
    await edit_or_reply(ctx, text, keyboard=kb)

async def show_trip_details(ctx, trip_id: int):
    trip = await get_trip_by_id(trip_id)
    if not trip:
        await edit_or_reply(ctx, "Рейс не найден.")
        return

    summary = json.loads(trip["summary_json"]) if trip.get("summary_json") else []
    
    text = (
        f"📊 <b>ОТЧЕТ О РЕЙСЕ #{trip['trip_id']}</b>\n\n"
        f"📍 <b>Направление:</b> {trip['route_title']}\n"
        f"⏰ <b>Время выезда:</b> {trip['start_time']}\n"
        f"🏁 <b>Время возвращения:</b> {trip['end_time'] or 'Не указано'}\n"
        f"👥 <b>Клиентов обслужено:</b> {trip['orders_count']}\n"
        f"💰 <b>ИТОГОВАЯ ВЫРУЧКА: {trip['total_revenue']} ₽</b>\n\n"
        "<b>ЧТО БЫЛО ПРОДАНО:</b>\n"
    )

    for item in summary:
        text += f"• {item['name']}: <b>{item['count']} {item['unit']}</b> ({item['sum']} ₽)\n"

    kb = build_message_keyboard([[CALENDAR_BACK], [ADMIN_BACK]])

    await edit_or_reply(ctx, text, keyboard=kb)

# ---------------------------------------------------------------------------
# ЕДИНЫЙ ЦЕНТРАЛЬНЫЙ РОУТЕР КНОПОК (100% совместимость с aiomax)
# ---------------------------------------------------------------------------
async def _master_callback_router_impl(callback: aiomax.Callback):
    payload = extract_payload(callback)
    if not payload:
        return

    user_id_str = str(get_user_id(callback))
    admin_callbacks = {
        "btn_admin_panel", "btn_calendar", "calc_kamenka", "calc_kurganovka",
        "admin_warehouse", "admin_clients", "admin_finances", "admin_logistics",
        "admin_inventory", "admin_add_item", "admin_client_list", "admin_search",
        "admin_revenue", "admin_debtors", "admin_back", "admin_logistics_back",
        "admin_finances_back", "admin_clients_back", "admin_warehouse_back",
        "admin_refresh_inventory", "admin_refresh_debtors", "admin_calendar_back",
    }
    is_admin_callback = (
        payload in admin_callbacks
        or payload.startswith(("trip_start_", "trip_finish_", "trip_view_"))
    )
    if is_admin_callback and user_id_str not in await get_all_admins_from_db():
        await edit_or_reply(callback, "Доступ открыт только для членов семьи.")
        return

    # Навигация
    if payload == "btn_menu":
        await show_main_menu(callback)
    elif payload == "btn_force_start":
        await cmd_start(callback)
    elif payload == "btn_catalog":
        cat = await get_active_category(user_id_str)
        text = (
            f"🛍 <b>Витрина фермы</b> | Раздел: <b>{CATEGORIES[cat]}</b>\n\n"
            f"Нажимайте кнопки <b>➕</b> и <b>➖</b>:"
            f"{await catalog_page_text(cat, 0)}"
        )
        kb = await build_marketplace_keyboard(user_id_str, cat)
        await edit_or_reply(callback, text, keyboard=kb)
    elif payload == "btn_cart":
        await show_cart_view(callback)
    elif payload == "btn_contacts":
        text = (
            "📞 <b>Контакты нашей фермы:</b>\n\n"
            "Сергей Александрович\n"
            "Телефон: <b>+7 963 103-52-62</b>\n\n"
            "🚚 <b>График доставки:</b>\n"
            "• г. Каменка — Среда\n"
            "• с. Кургановка — Вторник, Пятница"
        )
        kb = aiomax.buttons.KeyboardBuilder()
        kb.row(aiomax.buttons.CallbackButton("🛍 В витрину", "btn_catalog"))
        kb.row(aiomax.buttons.CallbackButton("🏠 Главное меню", "btn_menu"))
        await edit_or_reply(callback, text, keyboard=kb)
    elif payload == "btn_admin_panel":
        await show_admin_panel(callback)
    elif payload in ("admin_warehouse",):
        await show_admin_warehouse(callback)
    elif payload == "admin_clients":
        await show_admin_clients(callback)
    elif payload == "admin_finances":
        await show_admin_finances(callback)
    elif payload == "admin_logistics":
        await show_admin_logistics(callback)
    elif payload == "admin_inventory":
        await show_admin_inventory(callback)
    elif payload == "admin_refresh_inventory":
        await show_admin_inventory(callback)
    elif payload == "admin_add_item":
        await show_admin_stub(callback, "Добавить товар", "🔙 Назад на склад")
    elif payload == "admin_client_list":
        await show_admin_stub(callback, "Список клиентов", "🔙 Назад к клиентам")
    elif payload == "admin_search":
        await show_admin_search(callback)
    elif payload == "admin_revenue":
        await show_admin_stub(callback, "Выручка", "🔙 Назад в финансы")
    elif payload in ("admin_debtors", "admin_refresh_debtors"):
        await show_admin_debtors(callback)
    elif payload == "admin_back":
        await show_admin_panel(callback)
    elif payload == "admin_warehouse_back":
        await show_admin_warehouse(callback)
    elif payload == "admin_clients_back":
        await show_admin_clients(callback)
    elif payload == "admin_finances_back":
        await show_admin_finances(callback)
    elif payload == "admin_logistics_back":
        await show_admin_logistics(callback)
    elif payload == "admin_calendar_back":
        await show_calendar(callback)
    elif payload == "btn_calendar":
        await show_calendar(callback)
    elif payload == "registration_back":
        await handle_registration_back(callback)
    elif payload.startswith("catalog_page_"):
        page_data = payload.split("_", 3)
        if len(page_data) == 4:
            _, _, category, page_value = page_data
        else:
            category, page_value = "", ""
        if category in CATEGORIES and page_value.isdigit():
            page = int(page_value)
            page_count = await get_catalog_page_count(category)
            if page < page_count:
                await set_active_category(user_id_str, category)
                text = (
                    f"🛍 <b>Витрина фермы</b> | Раздел: <b>{CATEGORIES[category]}</b>\n\n"
                    f"Выберите товары на кнопках:{await catalog_page_text(category, page)}"
                )
                await edit_or_reply(
                    callback,
                    text,
                    keyboard=await build_marketplace_keyboard(user_id_str, category, page)
                )

    # Калькулятор по направлениям
    elif payload == "calc_kamenka":
        await show_route_calc_summary(callback, "kamenka")
    elif payload == "calc_kurganovka":
        await show_route_calc_summary(callback, "kurganovka")

    # Управление рейсами папы
    elif payload == "trip_start_kamenka":
        await handle_trip_start(callback, "kamenka")
    elif payload == "trip_start_kurganovka":
        await handle_trip_start(callback, "kurganovka")
    elif payload.startswith("trip_finish_"):
        trip_id = int(payload.replace("trip_finish_", ""))
        await handle_trip_finish(callback, trip_id)
    elif payload.startswith("trip_view_"):
        trip_id = int(payload.replace("trip_view_", ""))
        await show_trip_details(callback, trip_id)

    # Категории товаров
    elif payload.startswith("cat_"):
        cat_key = payload.replace("cat_", "")
        if cat_key in CATEGORIES:
            await set_active_category(user_id_str, cat_key)
            text = (
                f"🛍 <b>Витрина фермы</b> | Раздел: <b>{CATEGORIES[cat_key]}</b>\n\n"
                f"Выберите товары на кнопках:{await catalog_page_text(cat_key, 0)}"
            )
            kb = await build_marketplace_keyboard(user_id_str, cat_key)
            await edit_or_reply(callback, text, keyboard=kb)

    # Добавление товаров (гладкое добавление с toast-уведомлением без спама)
    elif payload.startswith("add_"):
        item_id = payload.replace("add_", "")
        catalog = await get_catalog_items()
        item = catalog.get(item_id)
        if item:
            cart = await get_cart(user_id_str)
            current_count = cart.get(item_id, 0)
            stock = item["stock"]
            if stock <= 0:
                toast = "Извините, товар закончился"
            elif current_count >= stock:
                toast = f"В наличии только {stock} {item['unit']}."
            else:
                await add_to_cart(user_id_str, item_id, min(1, stock - current_count))
                cnt = current_count + 1
                toast = f"✅ Добавлено: {item['name']} ({cnt} {item['unit']})"

            try:
                await callback.answer(toast)
            except Exception as e:
                logger.warning(f"Не удалось показать уведомление о товаре: {e}")
            cat = await get_active_category(user_id_str)
            category_items = [
                product for product in catalog.values()
                if product.get("cat") == cat
            ]
            item_page = next(
                (
                    index // 6 for index, product in enumerate(category_items)
                    if product["item_id"] == item_id
                ),
                0
            )
            page = item_page if len(category_items) > 8 else 0
            text = (
                f"🛍 <b>Витрина фермы</b> | Раздел: <b>{CATEGORIES[cat]}</b>\n\n"
                "Нажимайте на товары для добавления в заказ.\n"
                "Чтобы проверить список или убрать лишнее — нажмите <b>Корзину</b> внизу:"
                f"{await catalog_page_text(cat, page)}"
            )
            kb = await build_marketplace_keyboard(user_id_str, cat, page)
            await edit_or_reply(callback, text, keyboard=kb)

    # Удаление товара (работает внутри корзины)
    elif payload.startswith("del_"):
        item_id = payload.replace("del_", "")
        catalog = await get_catalog_items()
        item = catalog.get(item_id)
        cart = await get_cart(user_id_str)
        if item_id in cart:
            await remove_from_cart(user_id_str, item_id)
            try:
                await callback.answer("Убрано 1 шт.")
            except Exception as e:
                logger.warning(f"Не удалось показать уведомление об удалении товара: {e}")
        await show_cart_view(callback)

    # Корзина и оформление
    elif payload == "btn_checkout":
        await handle_checkout(callback)
    elif payload == "btn_confirm_order":
        await handle_checkout(callback, confirm=True)
    elif payload == "btn_back_to_cart":
        await show_cart_view(callback)
    elif payload == "btn_clear_cart":
        await clear_cart(user_id_str)
        await show_cart_view(callback)
    elif payload.startswith("cancel_order_"):
        order_id_value = payload.replace("cancel_order_", "", 1)
        if order_id_value.isdigit():
            await cancel_order(callback, int(order_id_value))

    # Регистрация маршрута
    elif payload in ["route_kamenka", "route_kurganovka"]:
        route_code = "kamenka" if payload == "route_kamenka" else "kurganovka"
        await complete_registration(callback, user_id_str, route_code)


@bot.on_button_callback()
async def master_callback_router(callback: aiomax.Callback):
    try:
        await _master_callback_router_impl(callback)
    except Exception:
        logger.error("Ошибка в master_callback_router", exc_info=True)


# ---------------------------------------------------------------------------
# Корзина и оформление (здесь клиент проверяет состав и убирает лишнее)
# ---------------------------------------------------------------------------
async def show_cart_view(ctx):
    user_id_str = str(get_user_id(ctx))
    cart = await get_cart(user_id_str)
    catalog = await get_catalog_items()

    total_count = sum(cart.values())
    total_price = sum(cart[k] * catalog[k]["price"] for k in cart if k in catalog)

    if total_count == 0:
        kb = aiomax.buttons.KeyboardBuilder()
        kb.row(aiomax.buttons.CallbackButton("🛍 В каталог товаров", "btn_catalog"))
        kb.row(aiomax.buttons.CallbackButton("🏠 Главное меню", "btn_menu"))
        await edit_or_reply(ctx, "🛒 <b>Ваша корзина пуста.</b>\n\nОткройте витрину, чтобы выбрать продукты:", keyboard=kb)
        return

    user_info = await get_user_from_db(user_id_str)
    route_name = ROUTES[user_info['route_code']]['full_title'] if user_info and user_info.get('route_code') in ROUTES else "Не выбран"

    text = "🛒 <b>СОСТАВ ВАШЕГО ЗАКАЗА:</b>\n\n"
    for item_id, count in cart.items():
        if count > 0 and item_id in catalog:
            item = catalog[item_id]
            text += f"• <b>{html.escape(item['name'])}</b>: {count} {item['unit']} = <b>{count * item['price']} ₽</b>\n"

    text += f"\n💰 <b>Итого к оплате: {total_price} ₽</b>\n"
    text += f"📍 <b>Маршрут доставки:</b> {html.escape(route_name)}\n\n"
    text += "<i>Нажимайте кнопки с крестиком ниже, чтобы убрать лишний товар:</i>"

    kb = aiomax.buttons.KeyboardBuilder()

    # Кнопки быстрого удаления лишнего для каждого товара
    for item_id, count in cart.items():
        if count > 0 and item_id in catalog:
            item = catalog[item_id]
            kb.row(aiomax.buttons.CallbackButton(
                f"❌ Убрать 1 {item['name'][:18]}", f"del_{item_id}"
            ))

    kb.row(aiomax.buttons.CallbackButton(f"✅ Оформить заказ на {total_price} ₽", "btn_checkout"))
    kb.row(aiomax.buttons.CallbackButton("🛍 Добавить еще продуктов", "btn_catalog"))
    kb.row(aiomax.buttons.CallbackButton("🗑 Очистить корзину", "btn_clear_cart"))
    kb.row(aiomax.buttons.CallbackButton("🏠 Главное меню", "btn_menu"))

    await edit_or_reply(ctx, text, keyboard=kb)

async def handle_checkout(ctx, confirm: bool = False):
    user_id_str = str(get_user_id(ctx))
    cart = await get_cart(user_id_str)
    catalog = await get_catalog_items()

    if not cart:
        await edit_or_reply(ctx, "Корзина пуста.")
        return

    user_info = await get_user_from_db(user_id_str)
    if not user_info:
        await edit_or_reply(ctx, "Для заказа необходимо сначала пройти знакомство. Напишите /start")
        return

    stock_issues = await get_cart_stock_issues(cart)
    if stock_issues:
        kb = aiomax.buttons.KeyboardBuilder()
        kb.row(aiomax.buttons.CallbackButton("🔙 Вернуться в корзину", "btn_back_to_cart"))
        await edit_or_reply(
            ctx,
            "⚠️ <b>Некоторые товары нельзя заказать в выбранном количестве:</b>\n"
            + "\n".join(f"• {html.escape(issue)}" for issue in stock_issues)
            + "\n\nИзмените корзину и попробуйте снова.",
            keyboard=kb
        )
        return

    total_price = sum(cart[k] * catalog[k]["price"] for k in cart if k in catalog)
    route_code = user_info.get("route_code", "kamenka")
    route_info = ROUTES.get(route_code, {"full_title": route_code})

    if not confirm:
        text = (
            "🔎 <b>Проверьте заказ. Оформить?</b>\n\n"
            + "\n".join(
                f"• {html.escape(catalog[item_id]['name'])}: {count} "
                f"{catalog[item_id]['unit']} — {count * catalog[item_id]['price']} ₽"
                for item_id, count in cart.items() if item_id in catalog
            )
            + f"\n\n💰 <b>Итого: {total_price} ₽</b>"
            + f"\n📍 Маршрут: <b>{html.escape(route_info['full_title'])}</b>"
        )
        kb = aiomax.buttons.KeyboardBuilder()
        kb.row(aiomax.buttons.CallbackButton("✅ Да, оформить", "btn_confirm_order"))
        kb.row(aiomax.buttons.CallbackButton("❌ Нет, вернуться в корзину", "btn_back_to_cart"))
        await edit_or_reply(ctx, text, keyboard=kb)
        return

    try:
        order_id = await save_order_to_db(user_id_str, cart, total_price, route_code)
    except DuplicateOrderError:
        await edit_or_reply(ctx, "Заказ уже оформлен, подождите.")
        return
    except ValueError:
        logger.warning(f"Остаток товара изменился при оформлении заказа пользователя {user_id_str}")
        kb = aiomax.buttons.KeyboardBuilder()
        kb.row(aiomax.buttons.CallbackButton("🔙 Вернуться в корзину", "btn_back_to_cart"))
        await edit_or_reply(
            ctx,
            "⚠️ Остаток товара изменился. Проверьте корзину и попробуйте оформить заказ снова.",
            keyboard=kb
        )
        return

    await clear_cart(user_id_str)

    success_text = (
        f"🎉 <b>Спасибо! Заказ №{order_id} принят!</b>\n\n"
        f"📍 <b>Маршрут:</b> {route_info['full_title']}\n"
        f"🗓 <b>Ожидаемая доставка:</b> {route_info.get('days', 'по согласованию')}\n"
        f"🏠 <b>Адрес:</b> {html.escape(user_info['address'])}\n"
        f"💰 <b>Сумма к оплате: {total_price} ₽</b>\n\n"
        f"📞 Мы свяжемся с вами по телефону <b>{html.escape(user_info['phone'])}</b> в день выезда."
    )
    kb = aiomax.buttons.KeyboardBuilder()
    kb.row(aiomax.buttons.CallbackButton("❌ Отменить заказ", f"cancel_order_{order_id}"))
    kb.row(aiomax.buttons.CallbackButton("🛍 В витрину", "btn_catalog"))
    kb.row(aiomax.buttons.CallbackButton("🏠 В меню", "btn_menu"))
    await edit_or_reply(ctx, success_text, keyboard=kb)

    # Оповещаем семью о новом заказе
    admins = await get_all_admins_from_db()
    items_desc = "\n".join([f"  • {catalog[k]['name']}: {cnt} {catalog[k]['unit']}" for k, cnt in cart.items() if k in catalog])
    admin_msg = (
        f"🔔 <b>НОВЫЙ ЗАКАЗ №{order_id}</b>\n\n"
        f"📍 <b>Маршрут:</b> {route_info['full_title']}\n"
        f"👤 <b>Клиент:</b> {html.escape(user_info['fio'])}\n"
        f"📞 <b>Телефон:</b> {html.escape(user_info['phone'])}\n"
        f"🏠 <b>Адрес:</b> {html.escape(user_info['address'])}\n\n"
        f"📦 <b>Состав:</b>\n{items_desc}\n"
        f"💰 <b>Сумма: {total_price} ₽</b>\n\n"
        f"<i>Заказ добавлен в калькулятор сбора для {route_info['name']}.</i>"
    )
    for adm_str in admins:
        try:
            await bot.send_message(
                chat_id=int(adm_str),
                text=encode_html_unicode(admin_msg)
            )
        except Exception:
            pass


async def cancel_order(ctx, order_id: int):
    user_id_str = str(get_user_id(ctx))
    order = await get_order_for_cancellation(order_id, user_id_str)
    if not order:
        await edit_or_reply(
            ctx,
            "Не удалось отменить заказ: он не найден, уже обработан или больше не ожидает подтверждения."
        )
        return

    await edit_or_reply(ctx, f"Заказ №{order_id} отменён.")
    admins = await get_all_admins_from_db()
    fio = html.escape(order.get("fio") or "Клиент")
    message = f"❌ Клиент {fio} отменил заказ №{order_id}."
    for admin_id in admins:
        try:
            await bot.send_message(
                chat_id=int(admin_id),
                text=encode_html_unicode(message)
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить администратора {admin_id} об отмене заказа: {e}")

# ---------------------------------------------------------------------------
# Команды и регистрация клиентов
# ---------------------------------------------------------------------------
@bot.on_command('start')
async def cmd_start(ctx):
    user_id_str = str(get_user_id(ctx))
    user_data = await get_user_from_db(user_id_str)
    if user_data:
        await set_state(user_id_str, REGISTERED)
        await show_main_menu(ctx)
        return

    text = (
        "Привет! 👋 Это витрина нашей семейной фермы.\n\n"
        "Здесь вы можете заказать свежее молоко, творог, масло и сыры с доставкой на дом.\n\n"
        "Для начала давайте познакомимся. 📝 Напишите ваше <b>ФИО</b>:"
    )
    await set_state(
        user_id_str,
        WAITING_FIO,
        temp_fio=None,
        temp_phone=None,
        temp_address=None,
        temp_route_code=None
    )
    await send_service_message(
        ctx,
        user_id_str,
        text,
        keyboard=build_registration_back_keyboard()
    )


@bot.on_command('menu')
async def cmd_menu(ctx):
    await show_main_menu(ctx)


@bot.on_command('help')
async def cmd_help(ctx):
    text = (
        "ℹ️ <b>Как пользоваться ботом фермы</b>\n\n"
        "<b>Регистрация:</b> отправьте /start и последовательно укажите ФИО, "
        "телефон, адрес и маршрут доставки.\n\n"
        "<b>Как заказать:</b> откройте витрину, выберите категорию и добавьте "
        "товары кнопками. Затем откройте корзину, проверьте состав и подтвердите заказ.\n\n"
        "<b>График доставки:</b>\n"
        "• г. Каменка — среда.\n"
        "• с. Кургановка — вторник и пятница.\n\n"
        "<b>Контакты:</b> Сергей Александрович, +7 963 103-52-62."
    )
    await reply(ctx, text)


@bot.on_command('find')
@bot.on_command('search')
async def cmd_find(ctx):
    user_id_str = str(get_user_id(ctx))
    admins = await get_all_admins_from_db()
    if user_id_str not in admins:
        await reply(ctx, "Команда доступна только для семьи.")
        return

    text = getattr(ctx, "content", None) or getattr(ctx, "text", None)
    if not text and hasattr(ctx, "message"): text = getattr(ctx.message, "text", "")
    parts = text.split(maxsplit=1) if text else []
    query = parts[1].strip() if len(parts) > 1 else ""

    if not query:
        await reply(ctx, "🔍 <b>Поиск клиента в рейсе</b>\n\nНапишите: <code>/find имя</code> или <code>/find улица</code>\nНапример: <code>/find Ленина</code> или <code>/find Анна</code>")
        return

    async with db_access() as conn:
        async with conn.execute("""
            SELECT o.*, u.fio, u.phone, u.address
            FROM orders o
            LEFT JOIN users u ON o.user_id = u.user_id
            WHERE o.status != 'completed' AND (
                u.fio LIKE ? OR u.address LIKE ? OR u.phone LIKE ?
            )
            ORDER BY o.order_id DESC LIMIT 10
        """, (f"%{query}%", f"%{query}%", f"%{query}%")) as cursor:
            results = [dict(row) for row in await cursor.fetchall()]

    if not results:
        await reply(ctx, f"🔍 По запросу «{html.escape(query)}» клиентов не найдено.")
        return

    res_text = f"🔍 <b>Найдено клиентов ({len(results)}):</b>\n\n"
    for r in results:
        route_info = ROUTES.get(r['route_code'], {})
        res_text += (
            f"👤 <b>{html.escape(r.get('fio') or 'Клиент')}</b>\n"
            f"📍 {html.escape(route_info.get('name') or r['route_code'])}: {html.escape(r.get('address') or '')}\n"
            f"📞 {html.escape(r.get('phone') or '')}\n"
            f"💰 Заказ #{r['order_id']} — <b>{r['total_price']} ₽</b>\n\n"
        )
    await reply(ctx, res_text)

@bot.on_command('admin')
async def cmd_admin(ctx):
    user_id_str = str(get_user_id(ctx))
    admins = await get_all_admins_from_db()
    if user_id_str in admins:
        role = admins[user_id_str]
        await reply(
            ctx,
            f"✅ Вы уже вошли как {ROLE_NAMES.get(role, role)} ({role})."
        )
        await show_admin_panel(ctx)
        return
    blocked_minutes = await get_pin_blocked_minutes(user_id_str)
    if blocked_minutes:
        await reply(ctx, f"Слишком много попыток, попробуйте через {blocked_minutes} минут.")
        return
    await set_state(user_id_str, WAITING_PIN)
    await send_service_message(
        ctx,
        user_id_str,
        "🔧 <b>Вход в систему</b>\n\nВведите ваш PIN-код:"
    )


@bot.on_command('health')
async def cmd_health(ctx):
    user_id_str = str(get_user_id(ctx))
    if user_id_str not in await get_all_admins_from_db():
        await reply(ctx, "Команда доступна только для семьи.")
        return

    try:
        async with db_access() as conn:
            async with conn.execute("SELECT COUNT(*) FROM users") as cursor:
                user_count = (await cursor.fetchone())[0]
            async with conn.execute(
                "SELECT COUNT(*) FROM orders WHERE status IN ('pending', 'in_trip')"
            ) as cursor:
                active_order_count = (await cursor.fetchone())[0]
        database_size = await asyncio.to_thread(os.path.getsize, DB_PATH)
        uptime_seconds = int(time.monotonic() - STARTED_AT)
        uptime_days, remainder = divmod(uptime_seconds, 86400)
        uptime_hours, remainder = divmod(remainder, 3600)
        uptime_minutes, uptime_seconds = divmod(remainder, 60)
        uptime = (
            f"{uptime_days} дн. {uptime_hours} ч. "
            f"{uptime_minutes} мин. {uptime_seconds} сек."
        )
        await reply(
            ctx,
            "🩺 <b>Состояние бота</b>\n\n"
            f"Время работы: {uptime}\n"
            f"Пользователей: {user_count}\n"
            f"Активных заказов: {active_order_count}\n"
            f"Размер БД: {database_size:,} байт"
        )
    except Exception:
        logger.exception("Не удалось собрать данные для /health")
        await reply(ctx, "Не удалось получить состояние бота. Проверьте журнал ошибок.")


@bot.on_command('logout')
async def cmd_logout(ctx):
    user_id_str = str(get_user_id(ctx))
    await remove_admin_from_db(user_id_str)
    await reply(ctx, "✅ Вы вышли из профиля администратора.")

@bot.on_command('restart')
@bot.on_command('reset')
async def cmd_restart(ctx):
    user_id_str = str(get_user_id(ctx))
    await clear_state(user_id_str)
    await clear_cart(user_id_str)
    await set_active_category(user_id_str, "milk")

    try:
        await reset_user_test_data(user_id_str)
    except Exception as e:
        logger.error(f"Ошибка при очистке в cmd_restart: {e}")

    text = (
        "🔄 <b>Тестовые данные успешно сброшены!</b>\n\n"
        "Ваш профиль клиента, корзина и активные заказы удалены из базы.\n"
        "Теперь вы можете протестировать бота заново с чистого листа, как новый клиент!\n\n"
        "Нажмите кнопку ниже или отправьте команду <b>/start</b>:"
    )
    kb = aiomax.buttons.KeyboardBuilder()
    kb.row(aiomax.buttons.CallbackButton("🚀 Начать заново (/start)", "btn_force_start"))
    await reply(ctx, text, keyboard=kb)

@bot.on_message()
async def handle_text(ctx):
    text = getattr(ctx, "content", None) or getattr(ctx, "text", None)
    if not text and hasattr(ctx, "message"): text = getattr(ctx.message, "text", "")
    if not text: return

    text = str(text).strip()
    user_id_str = str(get_user_id(ctx))
    state_data = await get_state(user_id_str) or {}
    current_state = state_data.get("state")

    normalized_text = text.casefold()

    if normalized_text in ["рестарт", "restart", "сброс", "reset"]:
        await cmd_restart(ctx)
        return

    admins = await get_all_admins_from_db()
    if user_id_str in admins:
        admin_actions = {
            "📦 склад": show_admin_warehouse,
            "👥 клиенты": show_admin_clients,
            "💰 финансы": show_admin_finances,
            "🚚 логистика (выезды)": show_admin_logistics,
            "📅 календарь": show_calendar,
            "🔙 назад в админку": show_admin_panel,
            "🔙 назад на склад": show_admin_warehouse,
            "🔙 назад к клиентам": show_admin_clients,
            "🔙 назад в финансы": show_admin_finances,
            "🔙 назад в логистику": show_admin_logistics,
            "🔙 назад в календарь": show_calendar,
            "➕ добавить товар": lambda message: show_admin_stub(
                message, "Добавить товар", "🔙 Назад на склад"
            ),
            "👥 список клиентов": lambda message: show_admin_stub(
                message, "Список клиентов", "🔙 Назад к клиентам"
            ),
            "🔎 поиск клиента": show_admin_search,
            "💰 выручка": lambda message: show_admin_stub(
                message, "Выручка", "🔙 Назад в финансы"
            ),
            "⚠️ должники": show_admin_debtors,
            "🔄 обновить должников": show_admin_debtors,
            "📊 остатки товаров": show_admin_inventory,
            "🔄 обновить остатки": show_admin_inventory,
            "📦 сводка: каменка": lambda message: show_route_calc_summary(
                message, "kamenka"
            ),
            "📦 сводка: кургановка": lambda message: show_route_calc_summary(
                message, "kurganovka"
            ),
            "🚚 выехал: каменка": lambda message: handle_trip_start(
                message, "kamenka"
            ),
            "🚚 выехал: кургановка": lambda message: handle_trip_start(
                message, "kurganovka"
            ),
            "🚚 начать выезд: г. каменка": lambda message: handle_trip_start(
                message, "kamenka"
            ),
            "🚚 начать выезд: с. кургановка": lambda message: handle_trip_start(
                message, "kurganovka"
            ),
            "📅 открыть календарь продаж": show_calendar,
            "🏠 выход из админки": cmd_logout,
        }
        admin_action = admin_actions.get(normalized_text)
        if admin_action:
            await admin_action(ctx)
            return

        finish_match = re.fullmatch(r"🏠 завершить рейс #(\d+)", text, re.IGNORECASE)
        if finish_match:
            await handle_trip_finish(ctx, int(finish_match.group(1)))
            return

        trip_match = re.match(r"🗓 рейс #(\d+)\b", text, re.IGNORECASE)
        if trip_match:
            await show_trip_details(ctx, int(trip_match.group(1)))
            return

    if normalized_text in ["меню", "старт"] and await get_user_from_db(user_id_str):
        await show_main_menu(ctx)
        return

    if normalized_text in ["start", "начать"]:
        if current_state not in [
            WAITING_FIO, WAITING_PHONE, WAITING_ADDRESS, WAITING_ROUTE, WAITING_PIN
        ]:
            await show_main_menu(ctx)
            return

    if text.startswith('/'): return

    try:
        if current_state == WAITING_PIN:
            blocked_minutes = await get_pin_blocked_minutes(user_id_str)
            if blocked_minutes:
                await reply(ctx, f"Слишком много попыток, попробуйте через {blocked_minutes} минут.")
                return

            if text in ADMIN_PINS:
                role = ADMIN_PINS[text]
                await reset_pin_attempts(user_id_str)
                await save_admin_to_db(user_id_str, role)
                await clear_state(user_id_str)
                await show_admin_panel(ctx)
            else:
                blocked_minutes = await record_failed_pin_attempt(user_id_str)
                if blocked_minutes:
                    await reply(ctx, f"Слишком много попыток, попробуйте через {blocked_minutes} минут.")
                else:
                    await reply(ctx, "❌ Неверный PIN-код. Попробуйте еще раз:")
            return

        if current_state == WAITING_FIO:
            normalized_fio = normalize_fio(text)
            if not is_valid_fio(normalized_fio):
                await send_service_message(
                    ctx,
                    user_id_str,
                    "⚠️ Укажите ФИО минимум из двух слов: только буквы, пробелы и дефисы, до 100 символов.",
                    keyboard=build_registration_back_keyboard()
                )
                return
            await set_state(user_id_str, WAITING_PHONE, temp_fio=normalized_fio)
            await send_service_message(
                ctx,
                user_id_str,
                "Принято! ✅ Теперь напишите ваш <b>номер телефона</b> для связи:",
                keyboard=build_registration_back_keyboard()
            )

        elif current_state == WAITING_PHONE:
            if not is_valid_phone(text):
                await send_service_message(
                    ctx,
                    user_id_str,
                    "⚠️ Пожалуйста, напишите правильный номер телефона (например, +79631035262):",
                    keyboard=build_registration_back_keyboard()
                )
                return
            await set_state(user_id_str, WAITING_ADDRESS, temp_phone=text)
            await send_service_message(
                ctx,
                user_id_str,
                "Отлично! 📞 Напишите ваш <b>адрес доставки</b> (улица, дом, кв):",
                keyboard=build_registration_back_keyboard()
            )

        elif current_state == WAITING_ADDRESS:
            address = text.strip()
            if not 5 <= len(address) <= 200:
                await send_service_message(
                    ctx,
                    user_id_str,
                    "⚠️ Адрес должен содержать от 5 до 200 символов. Попробуйте ещё раз:",
                    keyboard=build_registration_back_keyboard()
                )
                return
            await set_state(user_id_str, WAITING_ROUTE, temp_address=address)
            
            kb = aiomax.buttons.KeyboardBuilder()
            kb.row(aiomax.buttons.CallbackButton("1️⃣ г. Каменка (Среда)", "route_kamenka"))
            kb.row(aiomax.buttons.CallbackButton("2️⃣ с. Кургановка (Вт/Пт)", "route_kurganovka"))
            kb.row(aiomax.buttons.CallbackButton("🔙 Назад", "registration_back"))
            await send_service_message(
                ctx,
                user_id_str,
                "📍 <b>Выберите ваш маршрут доставки на кнопках ниже:</b>",
                keyboard=kb
            )

        elif current_state == WAITING_ROUTE:
            if text in ["1", "2"]:
                code = "kamenka" if text == "1" else "kurganovka"
                await complete_registration(ctx, user_id_str, code)

    except Exception as e:
        logger.error(f"Ошибка в handle_text: {e}")


async def handle_registration_back(ctx):
    user_id_str = str(get_user_id(ctx))
    state = await get_state(user_id_str)
    current_state = state.get("state") if state else None

    previous_steps = {
        WAITING_PHONE: (WAITING_FIO, "Напишите ваше <b>ФИО</b>:"),
        WAITING_ADDRESS: (WAITING_PHONE, "Напишите ваш <b>номер телефона</b> для связи:"),
        WAITING_ROUTE: (WAITING_ADDRESS, "Напишите ваш <b>адрес доставки</b> (улица, дом, кв):")
    }

    if current_state == WAITING_FIO:
        await clear_state(user_id_str)
        await show_main_menu(ctx)
        return

    previous = previous_steps.get(current_state)
    if not previous:
        await show_main_menu(ctx)
        return

    previous_state, prompt = previous
    await set_state(user_id_str, previous_state)
    await send_service_message(
        ctx,
        user_id_str,
        f"🔙 {prompt}",
        keyboard=build_registration_back_keyboard()
    )


async def complete_registration(ctx, user_id_str: str, route_code: str):
    user_info = await get_state(user_id_str) or {}
    await save_user_to_db(
        user_id=user_id_str,
        fio=user_info.get("temp_fio") or "Клиент",
        phone=user_info.get("temp_phone") or "",
        address=user_info.get("temp_address") or "",
        route_code=route_code
    )
    await clear_state(user_id_str)
    await set_state(user_id_str, REGISTERED)

    await set_active_category(user_id_str, "milk")
    await show_main_menu(ctx)

# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------
async def main():
    logger.info("Запуск бота семейной фермы на aiomax...")
    try:
        await init_db()
        await bot.start_polling()
    finally:
        await close_db()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем.")
