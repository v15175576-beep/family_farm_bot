import asyncio
import datetime
import html
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import aiosqlite

from bot_instance import bot
from config import CATALOG_DEFAULT, DB_PATH, ROLE_NAMES, ROUTES
from utils import encode_html_unicode

logger = logging.getLogger("farm_bot")

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
        # Добавляем поля уведомлений в существующую таблицу заказов.
        if "notified_at" not in order_columns:
            await conn.execute(
                "ALTER TABLE orders ADD COLUMN notified_at TIMESTAMP NULL"
            )
        if "notify_cancelled" not in order_columns:
            await conn.execute(
                "ALTER TABLE orders ADD COLUMN notify_cancelled INTEGER DEFAULT 0"
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

async def mark_order_notified(order_id: int, timestamp: str) -> bool:
    """Атомарно отмечает заказ как оповещённый, если он ещё не был оповещён."""
    async with db_access() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conn.execute(
                "UPDATE orders SET notified_at = ? "
                "WHERE order_id = ? AND notified_at IS NULL",
                (timestamp, order_id),
            )
            await conn.commit()
            return cursor.rowcount == 1
        except BaseException:
            await conn.rollback()
            raise

async def cancel_order_notification(order_id: int) -> bool:
    """Сбрасывает отметку уведомления и отмечает отмену только для оповещённого заказа."""
    async with db_access() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conn.execute(
                "UPDATE orders SET notified_at = NULL, notify_cancelled = 1 "
                "WHERE order_id = ? AND notified_at IS NOT NULL",
                (order_id,),
            )
            await conn.commit()
            return cursor.rowcount == 1
        except BaseException:
            await conn.rollback()
            raise

async def get_orders_for_trip(trip_id: int) -> list[dict]:
    """Возвращает заказы рейса со статусом в пути или завершён."""
    async with db_access() as conn:
        try:
            async with conn.execute(
                "SELECT o.order_id, o.user_id, u.fio, u.address, "
                "o.total_price, o.notified_at, o.status "
                "FROM orders o LEFT JOIN users u ON u.user_id = o.user_id "
                "WHERE o.trip_id = ? AND o.status IN ('in_trip', 'completed') "
                "ORDER BY o.order_id ASC",
                (trip_id,),
            ) as cursor:
                rows = await cursor.fetchall()
            await conn.commit()
            return [dict(row) for row in rows]
        except BaseException:
            await conn.rollback()
            raise

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
