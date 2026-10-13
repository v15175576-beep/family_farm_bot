import aiomax

from config import CATEGORIES
from database import get_cart, get_catalog_items
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

        if item["stock"] <= 0:
            kb.row(aiomax.buttons.CallbackButton(
                f"❌ {title} (распродан)",
                f"sold_out_{item_id}"
            ))
            continue

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

def build_registration_back_keyboard():
    kb = aiomax.buttons.KeyboardBuilder()
    kb.row(aiomax.buttons.CallbackButton("🔙 Назад", "registration_back"))
    return kb

def build_trip_route_keyboard(trip_id: int, orders: list, page: int = 0):
    """Строит страницу клавиатуры путевого листа."""
    kb = aiomax.buttons.KeyboardBuilder()
    page_size = 6 if len(orders) > 8 else max(len(orders), 1)
    page_count = max(1, (len(orders) + page_size - 1) // page_size)
    page = min(max(page, 0), page_count - 1)
    page_orders = orders[page * page_size:(page + 1) * page_size]

    buttons = []
    for order in page_orders:
        order_id = order["order_id"]
        fio = order.get("fio") or ""
        short_name = fio.split()[0] if fio.split() else "клиент"
        button_text = f"🔔 {short_name}"
        if order.get("notified_at") is not None:
            button_text = f"✅ {short_name}"
        buttons.append(
            aiomax.buttons.CallbackButton(
                button_text, f"view_order_{order_id}"
            )
        )
    for index in range(0, len(buttons), 2):
        kb.row(*buttons[index:index + 2])

    unnotified_count = sum(
        1 for order in orders if order.get("notified_at") is None
    )
    if unnotified_count > 1:
        kb.row(aiomax.buttons.CallbackButton(
            "🔔 Оповестить всех", f"notify_all_{trip_id}"
        ))
    if page_count > 1:
        page_buttons = []
        if page > 0:
            page_buttons.append(aiomax.buttons.CallbackButton(
                "⬅️ Назад", f"trip_page_{trip_id}_{page - 1}"
            ))
        if page + 1 < page_count:
            page_buttons.append(aiomax.buttons.CallbackButton(
                "Вперёд ➡️", f"trip_page_{trip_id}_{page + 1}"
            ))
        kb.row(*page_buttons)
    kb.row(aiomax.buttons.CallbackButton(
        "🏠 Я ПРИЕХАЛ", f"trip_finish_{trip_id}"
    ))
    return kb
