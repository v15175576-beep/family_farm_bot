import logging
import re

import aiomax

from bot_instance import bot

logger = logging.getLogger("farm_bot")
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
