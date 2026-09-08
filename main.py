import asyncio
import io
import os
import random
import re
import sqlite3
import textwrap
from typing import Optional

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from pymorphy3 import MorphAnalyzer
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from vkbottle import GroupEventType, GroupTypes, PhotoMessageUploader
from vkbottle.bot import Bot, Message

load_dotenv()

HTTP_SESSION = requests.Session()
_retry_policy = Retry(
    total=2, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504]
)
HTTP_SESSION.mount("https://", HTTPAdapter(max_retries=_retry_policy))
HTTP_SESSION.mount("http://", HTTPAdapter(max_retries=_retry_policy))

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "481879978"))
COMMUNITY_ID = 227912847

bot = Bot(token=BOT_TOKEN)
morph = MorphAnalyzer()

db = sqlite3.connect("quote_database.db")
cursor = db.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        vk_id INTEGER,
        author TEXT,
        chat_id INTEGER,
        count INTEGER
    )
""")
try:
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_vk_chat ON users(vk_id, chat_id)"
    )
except sqlite3.IntegrityError:
    print(
        "[WARN] В таблице users есть дубли (vk_id, chat_id) — уникальный индекс не создан."
    )
db.commit()


def update_quote_count(vk_id: int, author_name: str, chat_id: int) -> None:
    try:
        cursor.execute(
            """
            INSERT INTO users (vk_id, author, chat_id, count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(vk_id, chat_id) DO UPDATE SET count = count + 1
        """,
            (vk_id, author_name, chat_id),
        )
    except sqlite3.OperationalError:
        cursor.execute(
            "SELECT count FROM users WHERE vk_id = ? AND chat_id = ?", (vk_id, chat_id)
        )
        row = cursor.fetchone()
        if row:
            cursor.execute(
                "UPDATE users SET count = ? WHERE vk_id = ? AND chat_id = ?",
                (row[0] + 1, vk_id, chat_id),
            )
        else:
            cursor.execute(
                "INSERT INTO users VALUES (?, ?, ?, 1)", (vk_id, author_name, chat_id)
            )
    db.commit()


EMOJI_PATTERN = re.compile(
    "["
    "\U0001f600-\U0001f64f"
    "\U0001f300-\U0001f5ff"
    "\U0001f680-\U0001f6ff"
    "\U0001f1e0-\U0001f1ff"
    "\U00002500-\U00002bef"
    "\U00002702-\U000027b0"
    "\U0001f926-\U0001f937"
    "\U00010000-\U0010ffff"
    "\u2640-\u2642"
    "\u2600-\u2b55"
    "\u200d"
    "\u23cf"
    "\u23e9"
    "\u231a"
    "\ufe0f"
    "\u3030"
    "]+",
    re.UNICODE,
)
VK_LINK_PATTERN = re.compile(r"\[https://vk\.com/id\d+\|([^]]+)]")
VK_MENTION_PATTERN = re.compile(r"\[id\d+\|([^]]+)]")


def remove_emojis(text: str) -> str:
    return EMOJI_PATTERN.sub("", text)


def clean_text(raw_text: str) -> str:
    text = f"«{remove_emojis(raw_text)}»."
    text = VK_LINK_PATTERN.sub(r"\1", text)
    text = VK_MENTION_PATTERN.sub(r"\1", text)
    return text


IMAGE_WIDTH = 1050
MARGIN = 50
AVATAR_SIZE = 170
TITLE_AREA = 150  # отступ до начала текста первой цитаты
GAP_TEXT_TO_AVATAR = 70  # между последней строкой текста и аватаром
GAP_AFTER_AVATAR = 55  # между аватаром/подписью автора и следующим элементом
GAP_BETWEEN_QUOTES = 15  # между блоками соседних цитат
BOTTOM_MARGIN = 0  # отступ снизу картинки после последнего блока
TITLE_TEXT = "Цитаты великих людей"
WHITE_COLORS = {"#ffffff", "#f5f5f5", "#fffafa", "white", "gray"}

font_path = os.path.join(os.path.dirname(__file__), "ArialUnicodeMS.ttf")
font = ImageFont.truetype(font_path, 34)


def _wrap_lines(text: str, width: int = 45) -> list[str]:
    lines = []
    for line in text.split("\n"):
        wrapped = textwrap.wrap(line, width=width)
        lines += wrapped if wrapped else [""]
    return lines


def _compute_height(wrapped_quotes: list[list[str]]) -> int:
    y = TITLE_AREA
    for i, lines in enumerate(wrapped_quotes):
        y += len(lines) * font.size
        y += GAP_TEXT_TO_AVATAR
        y += AVATAR_SIZE
        y += GAP_AFTER_AVATAR
        if i != len(wrapped_quotes) - 1:
            y += GAP_BETWEEN_QUOTES
    return y + BOTTOM_MARGIN


def generate_quote_image(
    quotes: list[dict], background_image: Optional[bytes] = None, color: str = "black"
):
    try:
        color_text = "black" if color in WHITE_COLORS else "white"

        wrapped_quotes = [_wrap_lines(q["text"]) for q in quotes]
        height = _compute_height(wrapped_quotes)
        width = IMAGE_WIDTH

        if background_image:
            background = Image.open(io.BytesIO(background_image)).resize(
                (width, height)
            )
            image = ImageEnhance.Brightness(background).enhance(0.3).convert("RGBA")
        elif color == "random":
            rgb = tuple(random.randint(0, 255) for _ in range(3))
            image = Image.new("RGB", (width, height), color=rgb)
        else:
            try:
                image = Image.new("RGB", (width, height), color=color)
            except ValueError:
                return None, 1

        draw = ImageDraw.Draw(image)

        title_bbox = draw.textbbox((0, 0), TITLE_TEXT, font=font)
        title_width = title_bbox[2] - title_bbox[0]
        draw.text(
            ((width - title_width) // 2, 50),
            TITLE_TEXT,
            fill=color_text,
            font=font,
            align="center",
        )

        y = TITLE_AREA
        for i, (lines, q) in enumerate(zip(wrapped_quotes, quotes)):
            for line in lines:
                draw.text((MARGIN, y), line, fill=color_text, font=font)
                y += font.size
            y += GAP_TEXT_TO_AVATAR

            if q.get("avatar_bytes"):
                try:
                    avatar = (
                        Image.open(io.BytesIO(q["avatar_bytes"]))
                        .resize((AVATAR_SIZE, AVATAR_SIZE))
                        .convert("RGBA")
                    )
                    mask = Image.new("L", avatar.size, 0)
                    ImageDraw.Draw(mask).ellipse(
                        (0, 0, AVATAR_SIZE, AVATAR_SIZE), fill=255
                    )
                    image.paste(avatar, (MARGIN, y), mask)
                except Exception as e:
                    print(f"[ERROR] Не удалось вставить аватар: {e}")

            draw.text(
                (MARGIN + AVATAR_SIZE + 40, y + AVATAR_SIZE // 2 - font.size // 2),
                q["author_name"],
                fill=color_text,
                font=font,
            )
            y += AVATAR_SIZE + GAP_AFTER_AVATAR

            if i != len(quotes) - 1:
                y += GAP_BETWEEN_QUOTES

        with io.BytesIO() as output:
            image.save(output, format="PNG")
            output.seek(0)
            png_bytes = output.read()

        if not png_bytes:
            print(
                "[ERROR] generate_quote_image вернул 0 байт — не отправляю пустую картинку"
            )
            return None, 2

        print(
            f"[DEBUG] Сгенерирована картинка: {len(png_bytes)} байт, {width}x{height}px, quotes={len(quotes)}"
        )
        return png_bytes, 0
    except Exception as e:
        print(f"[ERROR] Ошибка генерации изображения: {e}")
        return None, 2


async def fetch_bytes(
    url: Optional[str], timeout: float = 5, retries: int = 1
) -> Optional[bytes]:
    if not url:
        return None
    for attempt in range(retries + 1):
        try:
            response = await asyncio.to_thread(HTTP_SESSION.get, url, timeout=timeout)
            response.raise_for_status()
            return response.content
        except Exception as e:
            if attempt < retries:
                print(
                    f"[WARN] Не удалось загрузить {url} (попытка {attempt + 1}/{retries + 1}): {e}, повторяю..."
                )
            else:
                print(f"[ERROR] Не удалось загрузить {url}: {e}")
    return None


async def upload_quote_photo(image_data: bytes, message: Message, attempts: int = 3):
    photo_uploader = PhotoMessageUploader(bot.api)
    for attempt in range(attempts):
        try:
            if message.group_id:
                return await photo_uploader.upload(
                    file_source=image_data, group_id=message.group_id
                )
            return await photo_uploader.upload(
                file_source=image_data, peer_id=message.peer_id
            )
        except Exception as e:
            if attempt < attempts - 1:
                print(
                    f"[WARN] Не удалось загрузить фото в VK (попытка {attempt + 1}/{attempts}): {e}, повторяю..."
                )
                await asyncio.sleep(1.5)
            else:
                print(
                    f"[ERROR] Не удалось загрузить фото в VK после {attempts} попыток: {e}"
                )
    return None


def collect_original_messages(message: Message) -> list:
    """Собирает все пересланные/реплайнутые сообщения — основа для мультицитаты."""
    messages = []
    if message.reply_message:
        messages.append(message.reply_message)
    if message.fwd_messages:
        messages.extend(message.fwd_messages)
    return messages


async def resolve_authors(original_messages: list) -> list[tuple[str, str]]:
    user_ids = list({m.from_id for m in original_messages if m.from_id > 0})
    group_ids = list({abs(m.from_id) for m in original_messages if m.from_id < 0})

    users_map: dict[int, tuple[str, str]] = {}
    groups_map: dict[int, tuple[str, str]] = {}

    tasks = []
    if user_ids:
        tasks.append(bot.api.users.get(user_ids=user_ids, fields="photo_200"))
    if group_ids:
        tasks.append(bot.api.groups.get_by_id(group_id=group_ids))

    results = await asyncio.gather(*tasks) if tasks else []

    idx = 0
    if user_ids:
        for u in results[idx]:
            users_map[u.id] = (f"{u.first_name} {u.last_name}", u.photo_200)
        idx += 1
    if group_ids:
        for g in results[idx].groups:
            groups_map[g.id] = (g.name, g.photo_200)

    authors = []
    for m in original_messages:
        if m.from_id > 0:
            authors.append(users_map.get(m.from_id, ("Неизвестный", "")))
        else:
            authors.append(
                groups_map.get(abs(m.from_id), ("Неизвестное сообщество", ""))
            )
    return authors


@bot.on.raw_event(GroupEventType.WALL_POST_NEW, dataclass=GroupTypes.WallPostNew)
async def new_wall_post(event: GroupTypes.WallPostNew):
    post_owner_id = event.object.owner_id
    post_id = event.object.id
    message_text = "Новая запись на стене!"
    attachment = f"wall{post_owner_id}_{post_id}"

    semaphore = asyncio.Semaphore(20)

    async def send_to_chat(chat_id: int):
        async with semaphore:
            try:
                await bot.api.messages.send(
                    peer_id=chat_id,
                    message=message_text,
                    random_id=random.randint(1, 2_000_000_000),
                    attachment=attachment,
                )
            except Exception as e:
                print(f"[ERROR] Не удалось отправить сообщение в чат {chat_id}: {e}")

    await asyncio.gather(
        *(send_to_chat(chat_id) for chat_id in range(2000000001, 2000000101))
    )


@bot.on.private_message()
async def private_handle_quote_request(message: Message):
    await handle_quote_request(message)


@bot.on.message(text=["/", "/c=<color>"])
async def handle_quote_request(message: Message):
    try:
        is_member = await bot.api.groups.is_member(
            group_id=COMMUNITY_ID, user_id=message.from_id
        )
        if not is_member:
            await message.answer("Чтобы создать цитату, подпишитесь на сообщество ❤️")
            return
    except Exception as e:
        print(f"[ERROR] Проверка подписки не удалась: {e}")
        await message.answer("Ошибка проверки подписки, попробуйте позже.")
        return

    original_messages = collect_original_messages(message)
    if not original_messages:
        await message.answer(
            "Чтобы создать цитату, перешлите одно или несколько сообщений с текстом."
        )
        return

    color = "black"
    if message.text.startswith("/c="):
        parts = message.text.split("=", 1)
        if len(parts) == 2 and parts[1]:
            color = parts[1]

    authors = await resolve_authors(original_messages)

    quotes = []
    for msg, (author_name, avatar_url) in zip(original_messages, authors):
        text = clean_text(msg.text)
        if text == "«».":
            continue
        quotes.append(
            {
                "from_id": msg.from_id,
                "text": text,
                "author_name": author_name,
                "avatar_url": avatar_url,
            }
        )

    if not quotes:
        await message.answer(
            "Сообщения должны содержать текст и не состоять полностью из emoji."
        )
        return

    avatar_bytes_list = await asyncio.gather(
        *(fetch_bytes(q["avatar_url"], timeout=8, retries=1) for q in quotes)
    )
    for q, avatar_bytes in zip(quotes, avatar_bytes_list):
        q["avatar_bytes"] = avatar_bytes

    background_image = None
    if message.attachments:
        for attachment in message.attachments:
            if attachment.type == attachment.type.PHOTO:
                url = attachment.photo.sizes[-1].url
                background_image = await fetch_bytes(url)
                if background_image:
                    break

    image_data, error_code = await asyncio.to_thread(
        generate_quote_image, quotes, background_image, color
    )

    if error_code == 1:
        await message.answer("Неверный цвет. Попробуйте ещё раз.")
        return
    elif error_code == 2 or not image_data:
        print(
            f"[ERROR] Картинка не сгенерирована (error_code={error_code}, image_data={'пусто' if not image_data else len(image_data)})"
        )
        await message.answer("Ошибка при генерации изображения. Попробуйте позже.")
        return

    photo = await upload_quote_photo(image_data, message)
    if not photo:
        await message.answer(
            "Не получилось загрузить картинку в VK, попробуйте ещё раз."
        )
        return

    await message.answer(attachment=photo)

    chat_id = message.peer_id
    for q in quotes:
        update_quote_count(q["from_id"], q["author_name"], chat_id)


@bot.on.chat_message(text="/top")
async def top_quoters_handler(message: Message):
    chat_id = message.peer_id
    cursor.execute(
        "SELECT vk_id, author, count FROM users WHERE chat_id = ? ORDER BY count DESC LIMIT 10",
        (chat_id,),
    )
    top_quoters = cursor.fetchall()

    if not top_quoters:
        await message.answer("В вашей беседе ещё не составлялись цитаты")
        return

    quote_word_base = morph.normal_forms("цитата")[0]
    quote_word_parsed = morph.parse(quote_word_base)[0]

    lines = ["Самые цитируемые участники беседы:\n"]
    for i, (vk_id, author_name, count) in enumerate(top_quoters, 1):
        word = quote_word_parsed.make_agree_with_number(count).word
        lines.append(f"{i}. [id{vk_id}|{author_name}] - {count} {word}")

    await message.answer("\n".join(lines), disable_mentions=True)


@bot.on.chat_message(text="/devf <vk_id> <count>")
async def dev_handler(message: Message, vk_id, count):
    if message.from_id == ADMIN_ID:
        chat_id = message.peer_id
        cursor.execute(
            "UPDATE users SET count = ? WHERE vk_id = ? AND chat_id = ?",
            (count, vk_id, chat_id),
        )
        db.commit()
        await message.answer("Успешно")
    else:
        await message.answer("Отказано!")


bot.run_forever()
