import html
import logging
import re
from typing import Any

import aiomax

from bot_instance import bot
from config import ADMIN_PINS, CATEGORIES, REGISTERED, ROUTES, WAITING_ADDRESS, WAITING_FIO, WAITING_PHONE, WAITING_PIN, WAITING_ROUTE
from database import add_to_cart, clear_cart, clear_state, get_active_category, get_all_admins_from_db, get_cart, get_cart_stock_issues, get_catalog_items, get_order_for_cancellation, get_state, get_user_from_db, get_pin_blocked_minutes, record_failed_pin_attempt, remove_from_cart, reset_pin_attempts, save_admin_to_db, save_order_to_db, save_user_to_db, set_active_category, set_state, DuplicateOrderError
from keyboards import build_marketplace_keyboard, build_registration_back_keyboard, catalog_page_text
from utils import edit_or_reply, encode_html_unicode, is_valid_fio, is_valid_phone, normalize_fio, reply, send_service_message, get_user_id

logger = logging.getLogger("farm_bot")
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
        from handlers.admin import cmd_restart

        await cmd_restart(ctx)
        return

    admins = await get_all_admins_from_db()
    if user_id_str in admins:
        from handlers.admin import (
            cmd_logout,
            handle_trip_finish,
            handle_trip_start,
            show_admin_clients,
            show_admin_debtors,
            show_admin_finances,
            show_admin_inventory,
            show_admin_logistics,
            show_admin_panel,
            show_admin_search,
            show_admin_stub,
            show_admin_warehouse,
            show_calendar,
            show_route_calc_summary,
            show_trip_details,
        )

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
