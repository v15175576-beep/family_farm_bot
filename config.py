import os
import re
import time
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

STARTED_AT = time.monotonic()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "farm_data.db"))
