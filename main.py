from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from vkbottle import GroupEventType, GroupTypes
from vkbottle import PhotoMessageUploader
from vkbottle.bot import Bot, Message
from pymorphy3 import MorphAnalyzer
from dotenv import load_dotenv
import requests
import textwrap
import sqlite3
import random
import os
import io
import re


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)

COMMUNITY_ID = 227912847

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
db.commit()

font_path = os.path.join(os.path.dirname(__file__), "ArialUnicodeMS.ttf")
font = ImageFont.truetype(font_path, 34)


def generate_quote_image(author_text, author_name, author_avatar_url, background_image=None, color='black'):
    try:
        white_color_list = ['#ffffff', '#f5f5f5', '#fffafa', 'white', 'gray']
        color_text = 'black' if color in white_color_list else 'white'

        # Текст переносим построчно
        text_lines = []
        for line in author_text.split('\n'):
            wrapped = textwrap.wrap(line, width=45)
            text_lines += wrapped if wrapped else [""]

        text_height = len(text_lines) * font.size
        height = int(text_height) + 460
        width = 1050

        # Фон
        if background_image:
            background = Image.open(io.BytesIO(background_image)).resize((width, height))
            enhancer = ImageEnhance.Brightness(background)
            image = enhancer.enhance(0.3).convert("RGBA")
        else:
            if color == 'random':
                rgb = tuple(random.randint(0, 255) for _ in range(3))
                image = Image.new("RGB", (width, height), color=rgb)
            else:
                try:
                    image = Image.new("RGB", (width, height), color=color)
                except ValueError:
                    return None, 1

        draw = ImageDraw.Draw(image)

        title_text = "Цитаты великих людей"
        title_text_bbox = draw.textbbox((0, 0), title_text, font=font)
        title_text_width = title_text_bbox[2] - title_text_bbox[0]
        draw.text(((width - title_text_width) // 2, 50), title_text, fill=color_text, font=font, align="center")

        for i, line in enumerate(text_lines):
            draw.text((50, 150 + i * font.size), line, fill=color_text, font=font)

        # Аватар с маской
        response = requests.get(author_avatar_url, timeout=5)
        response.raise_for_status()
        avatar = Image.open(io.BytesIO(response.content)).resize((170, 170)).convert("RGBA")

        mask = Image.new("L", avatar.size, 0)
        draw_mask = ImageDraw.Draw(mask)
        draw_mask.ellipse((0, 0, 170, 170), fill=255)

        image.paste(avatar, (50, height - 230), mask)

        # Автор
        draw.text((260, height - 170), author_name, fill=color_text, font=font)

        # Экспорт
        with io.BytesIO() as output:
            image.save(output, format="PNG")
            output.seek(0)
            return output.read(), 0
    except Exception as e:
        print(f"[ERROR] Ошибка генерации изображения: {e}")
        return None, 2


def remove_emojis(author_text):
    emoji = re.compile("["
                       u"\U0001F600-\U0001F64F"
                       u"\U0001F300-\U0001F5FF"
                       u"\U0001F680-\U0001F6FF"
                       u"\U0001F1E0-\U0001F1FF"
                       u"\U00002500-\U00002BEF"
                       u"\U00002702-\U000027B0"
                       u"\U0001f926-\U0001f937"
                       u"\U00010000-\U0010ffff"
                       u"\u2640-\u2642"
                       u"\u2600-\u2B55"
                       u"\u200d"
                       u"\u23cf"
                       u"\u23e9"
                       u"\u231a"
                       u"\ufe0f"
                       u"\u3030"
                       "]+", re.UNICODE)
    return re.sub(emoji, '', author_text)


@bot.on.raw_event(GroupEventType.WALL_POST_NEW, dataclass=GroupTypes.WallPostNew)
async def new_wall_post(event: GroupTypes.WallPostNew):
    post_owner_id = event.object.owner_id
    post_id = event.object.id

    message_text = "Новая запись на стене!"
    attachment = f"wall{post_owner_id}_{post_id}"

    for chat_id in range(2000000001, 2000000101):
        await bot.api.messages.send(
            peer_id=chat_id,
            message=message_text,
            random_id=random.randint(1, 2000000001),
            attachment=attachment)


@bot.on.private_message()
async def private_handle_quote_request(message: Message):
    await handle_quote_request(message)


@bot.on.message(text=["/", "/c=<color>"])
async def handle_quote_request(message: Message):
    try:
        is_member = await bot.api.groups.is_member(group_id=COMMUNITY_ID, user_id=message.from_id)
        if not is_member:
            await message.answer("Чтобы создать цитату, подпишитесь на сообщество ❤️")
            return
    except Exception as e:
        print(f"[ERROR] Проверка подписки не удалась: {e}")
        await message.answer("Ошибка проверки подписки, попробуйте позже.")
        return

    original_message = None
    if message.reply_message:
        original_message = message.reply_message
    elif message.fwd_messages:
        original_message = message.fwd_messages[0]

    if not original_message:
        await message.answer('Чтобы создать цитату, перешлите сообщение с текстом.')
        return

    color = 'black'
    if message.text.startswith('/c='):
        message_parts = message.text.split('=')
        if len(message_parts) == 2:
            color = message_parts[1]

    # Автор
    user_info = await bot.api.users.get(user_ids=original_message.from_id, fields='photo_200')
    if user_info:
        author_name = f'{user_info[0].first_name} {user_info[0].last_name}'
        author_avatar_url = user_info[0].photo_200
    else:
        original_message.from_id = abs(original_message.from_id)
        group_info = await bot.api.groups.get_by_id(group_id=original_message.from_id)
        author_name = group_info.groups[0].name
        author_avatar_url = group_info.groups[0].photo_200

    chat_id = message.peer_id
    text = f"«{remove_emojis(original_message.text)}»."
    cleaned_text = re.sub(r"\[https://vk\.com/id\d+\|([^]]+)]", r"\1", text)
    author_text = re.sub(r"\[id\d+\|([^]]+)]", r"\1", cleaned_text)

    if author_text == '«».':
        await message.answer('Сообщение должно содержать текст и не состоять полностью из emoji.')
        return

    # Фон
    background_image = None
    if message.attachments:
        for attachment in message.attachments:
            if attachment.type == attachment.type.PHOTO:
                try:
                    url = attachment.photo.sizes[-1].url
                    response = requests.get(url, timeout=5)
                    response.raise_for_status()
                    background_image = response.content
                    break
                except Exception as e:
                    print(f"[ERROR] Не удалось загрузить фон: {e}")

    image_data, error_code = generate_quote_image(author_text, author_name, author_avatar_url, background_image, color)

    if error_code == 1:
        await message.answer('Неверный цвет. Попробуйте ещё раз.')
        return
    elif error_code == 2:
        await message.answer('Ошибка при генерации изображения. Попробуйте позже.')
        return

    photo_uploader = PhotoMessageUploader(bot.api)
    if message.group_id:
        photo = await photo_uploader.upload(file_source=image_data, group_id=message.group_id)
    else:
        photo = await photo_uploader.upload(file_source=image_data, peer_id=message.peer_id)

    await message.answer(attachment=photo)

    cursor.execute("SELECT * FROM users WHERE vk_id = ? AND chat_id = ?", (original_message.from_id, chat_id))
    existing_user = cursor.fetchone()

    if existing_user:
        vk_id, name, chat_id, count = existing_user
        cursor.execute("UPDATE users SET count = ? WHERE vk_id = ? AND chat_id = ?", (count + 1, vk_id, chat_id))
    else:
        cursor.execute("INSERT INTO users VALUES (?, ?, ?, 1)", (original_message.from_id, author_name, chat_id))

    db.commit()


@bot.on.chat_message(text="/top")
async def top_quoters_handler(message: Message):
    chat_id = message.peer_id
    cursor.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
    quoters = cursor.fetchall()

    if not quoters:
        await message.answer("В вашей беседе ещё не составлялись цитаты")
        return

    quoters.sort(key=lambda x: x[3], reverse=True)
    top_quoters = quoters[:10]

    top_text = "Самые цитируемые участники беседы:\n\n"
    for i, (vk_id, author_name, chat_id, count) in enumerate(top_quoters, 1):
        word = morph.normal_forms('цитата')[0]
        word = morph.parse(word)[0].make_agree_with_number(count).word
        top_text += f"{i}. [id{vk_id}|{author_name}] - {count} {word}\n"

    await message.answer(top_text, disable_mentions=True)


@bot.on.chat_message(text='/devf <vk_id> <count>')
async def dev_handler(message: Message, vk_id, count):
    if message.from_id == 481879978:
        chat_id = message.peer_id
        cursor.execute("UPDATE users SET count = ? WHERE vk_id = ? AND chat_id = ?", (count, vk_id, chat_id))
        db.commit()
        await message.answer('Успешно')
    else:
        await message.answer('Отказано!')


bot.run_forever()
