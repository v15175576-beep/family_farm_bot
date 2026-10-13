import asyncio
import datetime
import html
import json
import logging
import os
import re
import time
from typing import List

import aiomax

from bot_instance import bot
from config import ADMIN_PINS, CATEGORIES, DB_PATH, ROLE_NAMES, ROUTES, STARTED_AT, WAITING_PIN
from database import add_to_cart, calculate_route_summary, cancel_order_notification, clear_cart, clear_state, db_access, finish_trip, get_active_category, get_active_trip, get_all_admins_from_db, get_cart, get_catalog_items, get_low_stock_items, get_orders_for_trip, get_pending_orders_for_route, get_pin_blocked_minutes, get_trip_by_id, get_trips_history, mark_order_notified, record_failed_pin_attempt, remove_admin_from_db, remove_from_cart, reset_pin_attempts, reset_user_test_data, set_active_category, set_state, start_trip, get_user_from_db
from handlers.client import cancel_order, cmd_start, complete_registration, handle_checkout, handle_registration_back, show_cart_view, show_main_menu
from utils import edit_or_reply, encode_html_unicode, extract_payload, get_user_id, reply, send_service_message
from keyboards import build_marketplace_keyboard, build_trip_route_keyboard, catalog_page_text, get_catalog_page_count

logger = logging.getLogger("farm_bot")
ADMIN_BACK = "🔙 Назад в админку"
LOGISTICS_BACK = "🔙 Назад в логистику"
CALENDAR_BACK = "🔙 Назад в календарь"

async def _answer_callback_notification(callback, notification: str):
    try:
        await callback.answer(notification=notification)
    except Exception as e:
        logger.warning(f"toast failed: {e}")

def build_admin_keyboard(rows: List[List[str]]):
    callback_payloads = {
        "📦 склад": "admin_warehouse",
        "👥 клиенты": "admin_clients",
        "💰 финансы": "admin_finance",
        "🚚 логистика (выезды)": "admin_logistics",
        "📅 календарь": "btn_calendar",
        "🏠 выход из админки": "admin_logout",
        "➕ добавить товар": "admin_add_item",
        "📊 остатки товаров": "admin_inventory",
        ADMIN_BACK.casefold(): "admin_back",
        "👥 список клиентов": "admin_client_list",
        "🔎 поиск клиента": "admin_search",
        "💰 выручка": "admin_revenue",
        "⚠️ должники": "admin_debtors",
        "🔄 обновить должников": "admin_refresh_debtors",
        "🔄 обновить остатки": "admin_refresh_inventory",
        "🔙 назад в финансы": "admin_finances_back",
        "🔙 назад на склад": "admin_warehouse_back",
        "🔙 назад к клиентам": "admin_clients_back",
        LOGISTICS_BACK.casefold(): "admin_logistics_back",
        CALENDAR_BACK.casefold(): "admin_calendar_back",
        "📦 сводка: каменка": "calc_kamenka",
        "📦 сводка: кургановка": "calc_kurganovka",
        "🚚 выехал: каменка": "trip_start_kamenka",
        "🚚 выехал: кургановка": "trip_start_kurganovka",
        "📅 открыть календарь продаж": "btn_calendar",
    }
    keyboard = aiomax.buttons.KeyboardBuilder()
    for row in rows:
        buttons = []
        for text in row:
            payload = callback_payloads.get(text.casefold())
            if payload is None:
                finish_match = re.fullmatch(
                    r"🏠 завершить рейс #(\d+)", text, re.IGNORECASE
                )
                trip_match = re.match(r"🗓 рейс #(\d+)\b", text, re.IGNORECASE)
                start_match = re.fullmatch(
                    r"🚚 начать выезд: (г\. каменка|с\. кургановка)",
                    text,
                    re.IGNORECASE,
                )
                if finish_match:
                    payload = f"trip_finish_{finish_match.group(1)}"
                elif trip_match:
                    payload = f"trip_view_{trip_match.group(1)}"
                elif start_match:
                    route_code = (
                        "kamenka"
                        if start_match.group(1).casefold() == "г. каменка"
                        else "kurganovka"
                    )
                    payload = f"trip_start_{route_code}"
                else:
                    raise ValueError(f"Не настроена callback-кнопка админки: {text}")
            buttons.append(aiomax.buttons.CallbackButton(text, payload))
        keyboard.row(*buttons)
    return keyboard

def _parse_order_notification_time(value):
    if isinstance(value, datetime.datetime):
        return value
    timestamp = str(value)
    try:
        return datetime.datetime.fromisoformat(timestamp)
    except ValueError:
        for timestamp_format in (
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ):
            try:
                return datetime.datetime.strptime(timestamp, timestamp_format)
            except ValueError:
                continue
    raise ValueError(f"Неизвестный формат времени уведомления: {value}")

def _build_trip_route_text(trip: dict, orders: List[dict], page: int = 0) -> str:
    page_size = 6 if len(orders) > 8 else max(len(orders), 1)
    page_count = max(1, (len(orders) + page_size - 1) // page_size)
    page = min(max(page, 0), page_count - 1)
    route_info = ROUTES.get(trip.get("route_code"), {})
    route_days = route_info.get("days", "по согласованию")
    page_orders = orders[page * page_size:(page + 1) * page_size]

    text = (
        f"🚀 <b>РЕЙС #{trip['trip_id']}</b> | "
        f"{html.escape(trip.get('route_title') or '')}\n"
        f"🗓 {html.escape(route_days)}\n"
        f"{'━' * 20}\n"
    )
    for order in page_orders:
        fio = order.get("fio") or "Клиент"
        fio_short = fio.split()[0] if fio.split() else "Клиент"
        notified_at = order.get("notified_at")
        if notified_at is None:
            status_emoji = "🔔"
            status_text = "не оповещён"
        else:
            status_emoji = "✅"
            status_text = (
                "оповещён в "
                f"{_parse_order_notification_time(notified_at).strftime('%H:%M')}"
            )
        text += (
            f"👤 <b>{html.escape(fio_short)}</b>\n"
            f"📍 {html.escape(order.get('address') or '')}\n"
            f"💰 {order['total_price']} ₽ | {status_emoji} {status_text}\n"
            f"{'━' * 20}\n"
        )

    total_sum = sum(order["total_price"] for order in orders)
    text += (
        f"💰 <b>Итого: {total_sum} ₽</b> | "
        f"👥 {len(orders)} клиентов"
    )
    if page_count > 1:
        text += f"\n<i>Страница {page + 1} из {page_count}</i>"
    return text

async def _refresh_trip_route(ctx, trip_id: int, page: int = 0):
    trip = await get_trip_by_id(trip_id)
    if not trip:
        await edit_or_reply(ctx, "Рейс не найден.")
        return

    orders = await get_orders_for_trip(trip_id)
    await edit_or_reply(
        ctx,
        _build_trip_route_text(trip, orders, page),
        keyboard=build_trip_route_keyboard(trip_id, orders, page),
    )

async def _show_order_card(ctx, order_id: int):
    async with db_access() as conn:
        async with conn.execute("""
            SELECT o.order_id, o.trip_id, o.user_id, o.items_json,
                   o.total_price, o.status, o.notified_at,
                   u.fio, u.phone, u.address
            FROM orders o
            LEFT JOIN users u ON u.user_id = o.user_id
            WHERE o.order_id = ?
        """, (order_id,)) as cursor:
            row = await cursor.fetchone()
    if not row:
        await edit_or_reply(ctx, "Заказ не найден.")
        return

    order = dict(row)
    if order["status"] not in ("in_trip", "completed"):
        await edit_or_reply(ctx, "Заказ не в рейсе.")
        return

    catalog = await get_catalog_items()
    order_items = json.loads(order["items_json"])
    fio = order.get("fio") or "Клиент"
    text = (
        f"👤 <b>{html.escape(fio)}</b>\n"
        f"📞 {html.escape(order.get('phone') or '')}\n"
        f"📍 {html.escape(order.get('address') or '')}\n"
        f"{'━' * 20}\n"
        "📦 <b>ЗАКАЗ:</b>\n"
    )
    for item_id, count in order_items.items():
        item = catalog.get(item_id, {})
        item_name = item.get("name", item_id)
        unit = item.get("unit", "шт")
        unit_price = item.get("price", 0)
        text += (
            f"• {html.escape(item_name)} — {count} {html.escape(unit)} — "
            f"{count * unit_price} ₽\n"
        )
    text += (
        f"{'━' * 20}\n"
        f"💰 <b>Итого: {order['total_price']} ₽</b>"
    )

    keyboard = aiomax.buttons.KeyboardBuilder()
    notified_at = order.get("notified_at")
    if notified_at is None:
        keyboard.row(aiomax.buttons.CallbackButton(
            "🔔 Подъезжаю", f"notify_arrive_{order_id}"
        ))
    else:
        notified_time = _parse_order_notification_time(notified_at)
        text += (
            "\n✅ Оповещён в "
            f"{notified_time.strftime('%H:%M')}"
        )
        if (datetime.datetime.now() - notified_time).total_seconds() < 60:
            keyboard.row(aiomax.buttons.CallbackButton(
                "↩️ Отменить оповещение", f"notify_cancel_{order_id}"
            ))
    keyboard.row(aiomax.buttons.CallbackButton(
        "✅ Выдано (оплачено)", f"mark_paid_{order_id}"
    ))
    keyboard.row(aiomax.buttons.CallbackButton(
        "⬅️ К путевому листу", f"back_to_trip_{order['trip_id']}"
    ))
    await edit_or_reply(ctx, text, keyboard=keyboard)

async def handle_notify_arrive(ctx, order_id: int):
    user_id_str = str(get_user_id(ctx))
    if user_id_str not in await get_all_admins_from_db():
        await ctx.answer("Доступ открыт только для членов семьи.")
        return

    async with db_access() as conn:
        async with conn.execute("""
            SELECT o.order_id, o.trip_id, o.user_id, o.status, o.total_price,
                   o.notified_at, u.address
            FROM orders o
            LEFT JOIN users u ON u.user_id = o.user_id
            WHERE o.order_id = ?
        """, (order_id,)) as cursor:
            row = await cursor.fetchone()
    if not row:
        await ctx.answer("Заказ не найден")
        return

    order = dict(row)
    if order["status"] not in ("in_trip", "completed"):
        await ctx.answer("Заказ не в рейсе")
        return

    now = datetime.datetime.now()
    now_str = now.strftime("%d.%m.%Y %H:%M:%S")
    if not await mark_order_notified(order_id, now_str):
        await ctx.answer("Уже оповещён")
        return

    try:
        await bot.send_message(
            chat_id=int(order["user_id"]),
            text=encode_html_unicode(
                "🚚 <b>Мы подъезжаем!</b>\n\n"
                f"Выходите встречать по адресу: "
                f"<b>{html.escape(order.get('address') or '')}</b>\n\n"
                f"Ваш заказ: {order['total_price']} ₽"
            ),
        )
    except Exception:
        logger.exception(
            "Не удалось отправить уведомление о подъезде по заказу %s",
            order_id,
        )

    await _show_order_card(ctx, order_id)
    await ctx.answer("Оповещение отправлено")

async def handle_notify_cancel(ctx, order_id: int):
    user_id_str = str(get_user_id(ctx))
    if user_id_str not in await get_all_admins_from_db():
        await ctx.answer("Доступ открыт только для членов семьи.")
        return

    async with db_access() as conn:
        async with conn.execute("""
            SELECT order_id, trip_id, notified_at
            FROM orders
            WHERE order_id = ?
        """, (order_id,)) as cursor:
            row = await cursor.fetchone()
    if not row:
        await ctx.answer("Заказ не найден")
        return

    order = dict(row)
    if order["notified_at"] is None:
        await ctx.answer("Заказ не оповещён")
        return

    notified_at = _parse_order_notification_time(order["notified_at"])
    if (datetime.datetime.now() - notified_at).total_seconds() >= 60:
        await ctx.answer("Время отмены истекло")
        return

    if not await cancel_order_notification(order_id):
        await ctx.answer("Оповещение уже отменено")
        return

    await _show_order_card(ctx, order_id)
    await ctx.answer("Оповещение отменено")

async def handle_notify_all(ctx, trip_id: int):
    orders = await get_orders_for_trip(trip_id)
    unnotified_orders = [
        order for order in orders if order.get("notified_at") is None
    ]
    notified_count = 0
    for order in unnotified_orders:
        now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        if not await mark_order_notified(order["order_id"], now_str):
            continue
        try:
            await bot.send_message(
                chat_id=int(order["user_id"]),
                text=encode_html_unicode(
                    "🚚 <b>Мы подъезжаем!</b>\n\n"
                    f"Выходите встречать по адресу: "
                    f"<b>{html.escape(order.get('address') or '')}</b>\n\n"
                    f"Ваш заказ: {order['total_price']} ₽"
                ),
            )
        except Exception:
            logger.exception(
                "Не удалось отправить уведомление о подъезде по заказу %s",
                order["order_id"],
            )
        notified_count += 1

    await _refresh_trip_route(ctx, trip_id)
    await ctx.answer(f"Оповещено клиентов: {notified_count}")

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
    keyboard = build_admin_keyboard([
        ["📦 Склад", "👥 Клиенты"],
        ["💰 Финансы", "🚚 Логистика (Выезды)"],
        ["📅 Календарь"],
        ["🏠 Выход из админки"],
    ])
    keyboard.row(aiomax.buttons.CallbackButton("🏠 Главное меню", "btn_menu"))
    await edit_or_reply(ctx, text, keyboard=keyboard)

async def show_admin_warehouse(ctx):
    await edit_or_reply(
        ctx,
        "📦 <b>Склад</b>\nВыберите действие:",
        keyboard=build_admin_keyboard([
            ["➕ Добавить товар"],
            ["📊 Остатки товаров"],
            [ADMIN_BACK],
        ])
    )

async def show_admin_clients(ctx):
    await edit_or_reply(
        ctx,
        "👥 <b>Клиенты</b>\nВыберите действие:",
        keyboard=build_admin_keyboard([
            ["👥 Список клиентов", "🔎 Поиск клиента"],
            [ADMIN_BACK],
        ])
    )

async def show_admin_finances(ctx):
    await edit_or_reply(
        ctx,
        "💰 <b>Финансы</b>\nВыберите действие:",
        keyboard=build_admin_keyboard([
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
    await edit_or_reply(ctx, text, keyboard=build_admin_keyboard(rows))

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
        keyboard=build_admin_keyboard([["🔄 Обновить остатки"], [ADMIN_BACK]])
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
        ctx, text, keyboard=build_admin_keyboard([["🔄 Обновить должников"], ["🔙 Назад в финансы"]])
    )

async def show_admin_stub(ctx, title: str, back_button: str):
    await edit_or_reply(
        ctx,
        f"🛠 <b>{html.escape(title)}</b>\n\nРаздел в разработке.",
        keyboard=build_admin_keyboard([[back_button]])
    )

async def show_admin_search(ctx):
    await edit_or_reply(
        ctx,
        "🔎 Для поиска клиента отправьте команду <code>/find имя</code> "
        "или <code>/find улица</code>.",
        keyboard=build_admin_keyboard([[ADMIN_BACK]])
    )

async def show_route_calc_summary(ctx, route_code: str):
    route_info = ROUTES.get(route_code, {"full_title": route_code})
    orders, summary_list, total_revenue = await calculate_route_summary(route_code)

    if not orders:
        text = (
            f"📦 <b>Сводка заказов: {route_info['full_title']}</b>\n\n"
            "На это направление сейчас нет ожидающих заказов.\n"
            "Все клиенты обслужены либо еще не оставили заявки."
        )
        kb = build_admin_keyboard([[LOGISTICS_BACK], [ADMIN_BACK]])
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
    kb = build_admin_keyboard(rows)

    await edit_or_reply(ctx, text, keyboard=kb)

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

    orders = await get_orders_for_trip(trip_id)
    
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

    await _refresh_trip_route(ctx, trip_id)

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

    kb = build_admin_keyboard([["📅 Открыть календарь продаж"], [ADMIN_BACK]])

    await edit_or_reply(ctx, text, keyboard=kb)

async def show_calendar(ctx):
    trips = await get_trips_history(10)
    
    if not trips:
        text = (
            "📅 <b>Календарь и история рейсов</b>\n\n"
            "Пока нет завершенных рейсов в базе.\n"
            "Как только Папа нажмет 'Я приехал', здесь появятся отчеты по дням!"
        )
        kb = build_admin_keyboard([[ADMIN_BACK]])
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

    kb = build_admin_keyboard(rows + [[ADMIN_BACK]])
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

    kb = build_admin_keyboard([[CALENDAR_BACK], [ADMIN_BACK]])

    await edit_or_reply(ctx, text, keyboard=kb)

async def _master_callback_router_impl(callback: aiomax.Callback):
    payload = extract_payload(callback)
    if not payload:
        return

    user_id_str = str(get_user_id(callback))
    admin_callbacks = {
        "btn_admin_panel", "btn_calendar", "calc_kamenka", "calc_kurganovka",
        "admin_warehouse", "admin_clients", "admin_finance", "admin_finances",
        "admin_logistics",
        "admin_inventory", "admin_add_item", "admin_client_list", "admin_search",
        "admin_revenue", "admin_debtors", "admin_back", "admin_logistics_back",
        "admin_finances_back", "admin_clients_back", "admin_warehouse_back",
        "admin_refresh_inventory", "admin_refresh_debtors", "admin_calendar_back",
        "admin_logout",
    }
    is_admin_callback = (
        payload in admin_callbacks
        or payload.startswith((
            "trip_start_", "trip_finish_", "trip_view_",
            "trip_page_", "view_order_", "back_to_trip_",
            "notify_arrive_", "notify_cancel_", "notify_all_",
            "mark_paid_",
        ))
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
    elif payload in ("admin_finance", "admin_finances"):
        await show_admin_finances(callback)
    elif payload == "admin_logistics":
        await show_admin_logistics(callback)
    elif payload == "admin_inventory":
        await show_admin_inventory(callback)
    elif payload == "admin_refresh_inventory":
        await show_admin_inventory(callback)
    elif payload == "admin_add_item":
        await _answer_callback_notification(
            callback, "⚠️ Раздел в разработке"
        )
    elif payload == "admin_client_list":
        await _answer_callback_notification(
            callback, "⚠️ Раздел в разработке"
        )
    elif payload == "admin_search":
        await show_admin_search(callback)
    elif payload == "admin_revenue":
        await _answer_callback_notification(
            callback, "⚠️ Раздел в разработке"
        )
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
    elif payload == "admin_logout":
        await remove_admin_from_db(user_id_str)
        await edit_or_reply(callback, "✅ Вы вышли из профиля администратора.")
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
    elif payload.startswith("notify_arrive_"):
        order_id = int(payload.replace("notify_arrive_", ""))
        await handle_notify_arrive(callback, order_id)
    elif payload.startswith("notify_cancel_"):
        order_id = int(payload.replace("notify_cancel_", ""))
        await handle_notify_cancel(callback, order_id)
    elif payload.startswith("view_order_"):
        order_id = int(payload.replace("view_order_", ""))
        await _show_order_card(callback, order_id)
    elif payload.startswith("back_to_trip_"):
        trip_id = int(payload.replace("back_to_trip_", ""))
        await _refresh_trip_route(callback, trip_id)
    elif payload.startswith("trip_page_"):
        _, _, trip_id_value, page_value = payload.split("_", 3)
        if trip_id_value.isdigit() and page_value.isdigit():
            await _refresh_trip_route(
                callback, int(trip_id_value), int(page_value)
            )
    elif payload.startswith("notify_all_"):
        trip_id = int(payload.replace("notify_all_", ""))
        await handle_notify_all(callback, trip_id)
    elif payload.startswith("mark_paid_"):
        await _answer_callback_notification(
            callback, "⚠️ Раздел в разработке"
        )
    elif payload.startswith("sold_out_"):
        await _answer_callback_notification(
            callback, "❌ Товар закончился"
        )

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
        if not item:
            await _answer_callback_notification(
                callback, "❌ Товар не найден"
            )
            return

        cart = await get_cart(user_id_str)
        current_count = cart.get(item_id, 0)
        stock = item["stock"]
        if stock <= 0:
            await _answer_callback_notification(
                callback, "❌ Товар закончился"
            )
            return
        if current_count >= stock:
            await _answer_callback_notification(
                callback, f"⚠️ В наличии только {stock} {item['unit']}"
            )
            return

        await add_to_cart(user_id_str, item_id, 1)
        cnt = current_count + 1
        await _answer_callback_notification(
            callback, f"✅ Добавлено в корзину: {cnt} {item['unit']}"
        )
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
            await _answer_callback_notification(callback, "Убрано 1 шт.")
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
