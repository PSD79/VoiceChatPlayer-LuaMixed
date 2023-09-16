import time
import asyncio
import hashlib
import logging
import math
import os
import re
import random
import shutil
from functools import wraps
from moviepy.editor import VideoFileClip
import aiofiles
import sys
import aiohttp
import cv2
import requests
from colorama import Fore
from configparser import ConfigParser
from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, errors, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (CallbackQuery, InlineKeyboardButton,
                            InlineKeyboardMarkup, InlineQueryResultAudio,
                            InputMediaPhoto)
from pytgcalls import PyTgCalls, StreamType
from pytgcalls import exceptions as tgerrors
from pytgcalls import idle, types
from pytgcalls.types import AudioPiped
from pytgcalls.types.input_stream import AudioVideoPiped
from pytgcalls.types.input_stream.quality import (MediumQualityAudio,
                                                  MediumQualityVideo)
from radiojavan import RadioJavan
from redis import Redis

os.makedirs("sessions", exist_ok=True)

config = ConfigParser()

if sys.argv[1:]:
    if sys.argv[1] == "create":
        if sys.argv[2:] and sys.argv[2].isnumeric():
            BOT_ID = sys.argv[2]
            if os.path.exists(f"config_{BOT_ID}.ini"):
                print(f"config_{BOT_ID}.ini already exists.")
                sys.exit(0)
            os.system(f"cp config.ini.example config_{BOT_ID}.ini")
            print(f"config_{BOT_ID}.ini created.")
            sys.exit(0)
        else:
            sys.exit(0)
    elif sys.argv[1].isnumeric():
        BOT_ID = sys.argv[1]
        if not os.path.exists(f"config_{BOT_ID}.ini"):
            print(f"config_{BOT_ID}.ini not exists.")
            sys.exit(0)
        config.read(f"config_{BOT_ID}.ini")
        API_ID = int(config.get("pyrogram", "api_id"))
        API_HASH = config.get("pyrogram", "api_hash")
        BOT_TOKEN = config.get("telegram", "token")
        DATABASE_CHANNEL = int(config.get("telegram", "database_channel"))
        SUDO_USERS = [int(u[1]) for u in config.items("admins")]
        REDIS_URL = config.get("redis", "url")
    else:
        sys.exit(0)
else:
    sys.exit(0)

bot = Client(f"sessions/{BOT_ID}-bot-api", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
redis = Redis.from_url(REDIS_URL, encoding='utf-8', decode_responses=True)
logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', filename='./errors.log')

cli = Client(f"sessions/{BOT_ID}-bot-cli", api_id=API_ID, api_hash=API_HASH, in_memory=True, session_string=redis.get(f"{BOT_ID}:SessionString"))
pytgcalls = PyTgCalls(cli)


class Playlist:

    def __init__(self, redis):
        self.redis = redis
    
    def convert_seconds(self, seconds):
        seconds = int(float(seconds))
        hours = seconds // 3600
        seconds %= 3600
        minutes = seconds // 60
        seconds %= 60
        if hours > 0:
            return "%02d:%02d:%02d" % (hours, minutes, seconds)
        return "%02d:%02d" % (minutes, seconds)

    def md5(self, string):
        return hashlib.md5(string.encode()).hexdigest()

    def compress(self, track):
        key = self.md5(f"{track['identifier']}/{track['id']}")
        keys = ','.join(track.keys())
        self.redis.hset(f"{BOT_ID}:Keys", key, keys)
        for i in track.keys():
            self.redis.hset(f"{BOT_ID}:Detail-{i}", key, track[i])
        return key

    def extract(self, key):
        result = dict()
        keys = self.redis.hget(f"{BOT_ID}:Keys", key).split(",")
        for i in keys:
            result[i] = self.redis.hget(f"{BOT_ID}:Detail-{i}", key)
        return result

    def display(self, key, played_time=0):
        datas = self.extract(key)
        result = ""
        if not played_time:
            played_time = 2
        if datas.get("seek"):
            played_time += eval(datas["seek"])
        if "artist" in datas.keys():
            result += f"🗣 خواننده : {datas['artist']}\n"
        if "title" in datas.keys():
            if datas["type"] == "video":
                result += f"🎵 نام موزیک ویدئو : {datas['title']}\n"
            else:
                result += f"🎵 نام آهنگ : {datas['title']}\n"
        if "duration" in datas.keys():
            result += f"⏱ زمان : {self.convert_seconds(datas['duration'])} - {self.convert_seconds(played_time)}"
        return result

    def get(self, chat_id):
        if self.redis.scard(f"{BOT_ID}:Playlist:{chat_id}") == 0:
            return []
        return sorted(list(self.redis.smembers(f"{BOT_ID}:Playlist:{chat_id}")))

    def get_name(self, key):
        datas = self.extract(key)
        result = ""
        if "artist" in datas.keys():
            result += f"{datas['artist']} - "
        if "title" in datas.keys():
            result += f"{datas['title']}"
        if not result:
            result += "Unknown Track"
        icon = "🎵" if datas["type"] == "audio" else "🎬"
        result = f"{icon} {result}"
        return result

    def add(self, chat_id, track):
        _id = self.compress(track)
        if self.redis.sscan(f"{BOT_ID}:Playlist:{chat_id}", 0, f"*-{_id}")[1]:
            return False, _id
        counter = self.redis.scard(f"{BOT_ID}:Playlist:{chat_id}") + 1
        self.redis.sadd(f"{BOT_ID}:Playlist:{chat_id}", f"{counter}-{_id}")
        return True, _id

    def get_full_form(self, chat_id, key):
        return self.redis.sscan(f"{BOT_ID}:Playlist:{chat_id}", 0, f"*-{key}")[1][0]

    def get_possition(self, chat_id, key):
        _list = self.get(chat_id)
        full = self.get_full_form(chat_id, key)
        return _list.index(full) + 1

    def rem(self, chat_id, _id):
        full = self.get_full_form(chat_id, _id)
        self.clear_data(_id)
        self.redis.srem(f"{BOT_ID}:Playlist:{chat_id}", full)
        return True

    def split_key(self, key):
        possition, value = key.split("-")
        return int(possition), value

    def now(self, chat_id):
        return self.redis.hget(f"{BOT_ID}:NowPlaying", chat_id) or None

    def play(self, chat_id, key):
        self.redis.hset(f"{BOT_ID}:NowPlaying", chat_id, key)
        self.redis.hset(f"{BOT_ID}:Status", chat_id, "play")

    def pause(self, chat_id):
        self.redis.hset(f"{BOT_ID}:Status", chat_id, "pause")

    def resume(self, chat_id):
        self.redis.hset(f"{BOT_ID}:Status", chat_id, "play")

    def status(self, chat_id):
        return self.redis.hget(f"{BOT_ID}:Status", chat_id)

    def rule(self, chat_id):
        return self.redis.hget(f"{BOT_ID}:PlayingRule", chat_id)

    def set_rule(self, chat_id, rule):
        self.redis.hset(f"{BOT_ID}:PlayingRule", chat_id, rule)

    def clear_data(self, key):
        keys = self.redis.hget(f"{BOT_ID}:Keys", key).split(",")
        for i in keys:
            if i == "link":
                path = self.redis.hget(f"{BOT_ID}:Detail-{i}", key)
                if os.path.exists(path):
                    os.remove(path)
            if i == "id":
                _id = self.redis.hget(f"{BOT_ID}:Detail-{i}", key)
                self.redis.hdel(f"{BOT_ID}:InProgress", _id)
            self.redis.hdel(f"{BOT_ID}:Detail-{i}", key)
        self.redis.hdel(f"{BOT_ID}:Keys", key)

    def clear(self, chat_id):
        for item in self.get(chat_id):
            _, _id = self.split_key(item)
            self.clear_data(_id)
            self.redis.srem(f"{BOT_ID}:Playlist:{chat_id}", item)
        self.redis.hdel(f"{BOT_ID}:PlayingRule", chat_id)
        self.redis.hdel(f"{BOT_ID}:Status", chat_id)
        self.redis.hdel(f"{BOT_ID}:NowPlaying", chat_id)

    def next(self, chat_id, force=False):
        current = self.get_full_form(chat_id, self.now(chat_id))
        playlist = self.get(chat_id)
        rule = self.rule(chat_id)
        index = playlist.index(current) + 1
        if rule == "shuffle":
            return random.choice(playlist)
        if force:
            if index == len(playlist):
                if rule == "queue":
                    return None
                index = 0
            return playlist[index]
        if rule == "repeat-one":
            return current
        else:
            if index == len(playlist):
                if rule == "repeat":
                    index = 0
                elif rule == "queue":
                    return None
            return playlist[index]

    def previous(self, chat_id):
        current = self.get_full_form(chat_id, self.now(chat_id))
        playlist = self.get(chat_id)
        rule = self.rule(chat_id)
        index = playlist.index(current) - 1
        if rule == "shuffle":
            return random.choice(playlist)
        if index == -1:
            if rule != "repeat":
                return None
        return playlist[index]


playlist = Playlist(redis)
rj = RadioJavan()

############################### Start Utils ###############################


def download_url(url, filename):
    response = requests.get(url)
    with open(filename, mode="wb") as file:
        file.write(response.content)
    return filename


def extract_audio(path):
    video = VideoFileClip(path) # 2.
    audio = video.audio # 3.
    # _, ext = os.path.splitext(path)
    filename = f"audio_{path}"
    audio.write_audiofile(filename)
    return filename

def get_active_calls():
    for chat in pytgcalls.active_calls:
        yield chat.chat_id


def authorized_groups(func):
    @wraps(func)
    async def wrapper(client, message):
        if isinstance(message, CallbackQuery):
            chat_id = message.message.chat.id
        else:
            chat_id = message.chat.id
        if not redis.sismember(f"{BOT_ID}:Groups", chat_id):
            return False
        return await func(client, message)
    return wrapper


def authorized_users(func):
    @wraps(func)
    async def wrapper(client, message):
        if isinstance(message, CallbackQuery):
            chat_id = message.message.chat.id
        else:
            chat_id = message.chat.id
        if message.from_user.id in SUDO_USERS:
            return await func(client, message)
        if redis.sismember(f"{BOT_ID}:Admins:{chat_id}", message.from_user.id):
            return await func(client, message)
        return False
    return wrapper


def has_active_call(func):
    async def wrapper(client, message):
        active_calls = [call for call in get_active_calls()]
        if message.chat.id not in active_calls:
            return await message.reply("🚫 وویس چت فعال در گروه وجود ندارد ❗️")
        return await func(client, message)

    return wrapper


def change_image_size(maxWidth, maxHeight, image):
    widthRatio = maxWidth / image.size[0]
    heightRatio = maxHeight / image.size[1]
    newWidth = int(widthRatio * image.size[0])
    newHeight = int(heightRatio * image.size[1])
    newImage = image.resize((newWidth, newHeight))
    return newImage


def hasher(name):
    return hashlib.md5(name.encode()).hexdigest()


async def cover(artist, title, type="audio", duration=None, thumbnail=None):
    file_name = f"{artist}-{title}-{type}-{duration}-{thumbnail}"
    file_unique_name = hasher(file_name)
    filename = f"covers/{file_unique_name}.png"
    if os.path.exists(filename):
        return filename
    thumb_name = f"covers/{file_unique_name}-thumb.png"
    temp_name = f"covers/{file_unique_name}-temp.png"
    if not os.path.exists("covers"):
        os.makedirs("covers")
    if thumbnail:
        if os.path.exists(thumbnail):
            shutil.copyfile(thumbnail, thumb_name)
        else:
            async with aiohttp.ClientSession() as session:
                async with session.get(thumbnail) as resp:
                    if resp.status == 200:
                        f = await aiofiles.open(thumb_name, mode="wb")
                        await f.write(await resp.read())
                        await f.close()
        image1 = Image.open(thumb_name)
        image2 = Image.open("files/foreground.png")
        image3 = change_image_size(1280, 720, image1)
        image4 = change_image_size(1280, 720, image2)
        image5 = image3.convert("RGBA")
        image6 = image4.convert("RGBA")
        Image.alpha_composite(image5, image6).save(temp_name)
        img = Image.open(temp_name)
    else:
        image1 = Image.open("files/foreground.png")
        image2 = change_image_size(1280, 720, image1)
        img = image2.convert("RGBA")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("files/font.otf", 32)
    draw.text((205, 550), f"Artist: {artist}", (51, 215, 255), font=font)
    draw.text((205, 590), f"Title: {title}", (51, 215, 255), font=font)
    me = await bot.get_me()
    if duration:
        duration = playlist.convert_seconds(duration)
        draw.text((205, 630), f"Duration: {duration}", (255, 255, 255), font=font)
        draw.text((205, 670), f"By: @{me.username}", (255, 255, 255), font=font)
    else:
        draw.text((205, 630), f"By: @{me.username}", (255, 255, 255), font=font)
    # draw.text((205, 630), f"Views: {views}", (255, 255, 255), font=font)
    # draw.text((205, 630), f"Added By: {requested_by}", (255, 255, 255), font=font) # 670
    img.save(filename)
    try:
        os.remove(temp_name)
        os.remove(thumb_name)
    except:
        pass
    return filename


async def prepare_helper(chat_id, message_id, callback=False):
    helper = await cli.get_me()
    try:
        await cli.get_chat(chat_id)
    except errors.ChannelInvalid:
        try:
            chat = await bot.get_chat(chat_id)
            await cli.join_chat(chat.invite_link)
        except errors.ChatAdminRequired:
            if callback:
                await bot.edit_message_caption(chat_id, message_id, caption="❗️ ابتدا من را در گروه ادمین کنید ❗️\n\n⚠️ توجه : دسترسی به لینک های دعوت الزامیست ⚠️")
            else:
                await bot.send_message(chat_id, "❗️ ابتدا من را در گروه ادمین کنید ❗️\n\n⚠️ توجه : دسترسی به لینک های دعوت الزامیست ⚠️", reply_to_message_id=message_id)
            return False
        except errors.InviteHashExpired:
            try:
                await bot.unban_chat_member(chat_id, helper.id)
                chat = await bot.get_chat(chat_id)
                await cli.join_chat(chat.invite_link)
            except errors.InviteHashExpired:
                if callback:
                    await bot.edit_message_caption(chat_id, message_id, caption=f"❗️ در ورود ربات دستیار مشکلی پیش آمده است ❗️\n\n⚠️ ربات دستیار را به گروه اضافه کنید ⚠️\n\n🌐 مشخصات ربات دستیار:\n⭕️ نام: {helper.mention()}\n⭕️ آیدی عددی: {helper.id}")
                else:
                    await bot.send_message(chat_id, f"❗️ در ورود ربات دستیار مشکلی پیش آمده است ❗️\n\n⚠️ ربات دستیار را به گروه اضافه کنید ⚠️\n\n🌐 مشخصات ربات دستیار:\n⭕️ نام: {helper.mention()}\n⭕️ آیدی عددی: {helper.id}", reply_to_message_id=message_id)
                return False
            except errors.ChatAdminRequired:
                if callback:
                    await bot.edit_message_caption(chat_id, message_id, caption=f"❗️ ربات دستیار قبلا از گروه اخراج شده است ❗️\n\n⚠️ به من دسترسی مدیریت کاربران اخراج شده را بدهید یا ربات دستیار را از مسدودیت آزاد کنید ⚠️\n\n🌐 مشخصات ربات دستیار:\n⭕️ نام: {helper.mention()}\n⭕️ آیدی عددی: {helper.id}")
                else:
                    await bot.send_message(chat_id, f"❗️ ربات دستیار قبلا از گروه اخراج شده است ❗️\n\n⚠️ به من دسترسی مدیریت کاربران اخراج شده را بدهید یا ربات دستیار را از مسدودیت آزاد کنید ⚠️\n\n🌐 مشخصات ربات دستیار:\n⭕️ نام: {helper.mention()}\n⭕️ آیدی عددی: {helper.id}", reply_to_message_id=message_id)
                return False
        except errors.FloodWait:
            if callback:
                await bot.edit_message_caption(chat_id, message_id, caption=f"❗️ ربات دستیار به صورت موقع محدود شده است ❗️\n\n⚠️ لطفا آن را به گروه اضافه کنید ⚠️\n\n⭕️ ربات دستیار: {helper.mention()}")
            else:
                await bot.send_message(chat_id, f"❗️ ربات دستیار به صورت موقع محدود شده است ❗️\n\n⚠️ لطفا آن را به گروه اضافه کنید ⚠️\n\n⭕️ ربات دستیار: {helper.mention()}", reply_to_message_id=message_id)
            return False
        except errors.UserAlreadyParticipant:
            pass
    if playlist.now(chat_id):
        redis.sadd(f"{BOT_ID}:CliGroups", chat_id)
        return True
    if callback:
        await bot.edit_message_caption(chat_id, message_id, caption="✅ ربات دستیار آماده شد ✅")
    else:
        await bot.send_message(chat_id, "✅ ربات دستیار آماده شد ✅", reply_to_message_id=message_id)
    redis.sadd(f"{BOT_ID}:CliGroups", chat_id)
    return True


def save_to(directiory, path):
    if not os.path.exists(directiory):
        os.makedirs(directiory)
    filename = f"{directiory}/{os.path.basename(path)}"
    os.rename(path, filename)
    return filename


async def leave_group_call(chat_id):
    try:
        await pytgcalls.leave_group_call(chat_id)
    except:
        pass


async def join_group_call(chat_id, stream):
    try:
        await pytgcalls.join_group_call(chat_id, stream, stream_type=StreamType().pulse_stream)
    except:
        pass


async def change_stream(chat_id, key, seek=None, new=False):
    _, _id = playlist.split_key(key)
    playlist.play(chat_id, _id)
    meta_data = playlist.extract(_id)
    if not seek and meta_data.get("seek"):
        del meta_data["seek"]
        playlist.compress(meta_data)
    stream = AudioPiped(meta_data["path"], MediumQualityAudio())
    if seek:
        stream = AudioPiped(meta_data["path"], MediumQualityAudio(), additional_ffmpeg_parameters="-ss {} -to {}".format(seek, meta_data["duration"]))
    if meta_data["type"] == "video":
        stream = AudioVideoPiped(meta_data["path"], MediumQualityAudio(), MediumQualityVideo())
        if seek:
            stream = AudioVideoPiped(meta_data["path"], MediumQualityAudio(), MediumQualityVideo(), additional_ffmpeg_parameters="-ss {} -to {}".format(seek, meta_data["duration"]))
    if new:
        await leave_group_call(chat_id)
        time.sleep(2)
        await join_group_call(chat_id, stream)
    else:
        await pytgcalls.change_stream(chat_id, stream)
    time.sleep(1)
    played_seconds = await pytgcalls.played_time(chat_id)
    if played_seconds == 0:
        return await change_stream(chat_id, key, seek=2)


async def get_current_volume(chat_id):
    chat_participants = await pytgcalls.get_participants(chat_id)
    me = await cli.get_me()
    for user in chat_participants:
        if user.user_id == me.id:
            return user.volume


async def delete_last_player(chat_id):
    last_player = redis.hget(f"{BOT_ID}:PlayerMessage", chat_id) or None
    if last_player:
        try:
            await bot.delete_messages(chat_id, int(last_player))
        except:
            redis.hdel(f"{BOT_ID}:PlayerMessage", chat_id)


async def prepare_player(chat_id):
    _, first = playlist.split_key(playlist.get(chat_id)[0])
    now_playing = playlist.now(chat_id)
    meta_data = playlist.extract(now_playing)
    thumbnail = meta_data["thumbnail"]
    rule = playlist.rule(chat_id)
    rule_text = ""
    if rule == "queue":
        rule_text = "➡️"
    elif rule == "repeat":
        rule_text = "🔁"
    elif rule == "repeat-one":
        rule_text = "🔂"
    elif rule == "shuffle":
        rule_text = "🔀"
    status = playlist.status(chat_id)
    status_text = ""
    if status == "play":
        status_text = "⏸"
        status_action = "pause"
    elif status == "pause":
        status_text = "▶️"
        status_action = "resume"
    rows = [
        [
            InlineKeyboardButton(text="⏹", callback_data="stop"),
            InlineKeyboardButton(text="⏪", callback_data="previous"),
            InlineKeyboardButton(text=status_text, callback_data=status_action),
            InlineKeyboardButton(text="⏩", callback_data="next"),
            InlineKeyboardButton(text=rule_text, callback_data=f"changerule-{rule}"),
        ],
        [
            InlineKeyboardButton(text="-30", callback_data="seek-30"),
            InlineKeyboardButton(text="-10", callback_data="seek-10"),
            InlineKeyboardButton(text="+10", callback_data="seek+10"),
            InlineKeyboardButton(text="+30", callback_data="seek+30"),
        ],
        [
            InlineKeyboardButton(text="پخش مجدد لیست", callback_data=f"playforce-{first}"),
            InlineKeyboardButton(text="پخش مجدد", callback_data=f"playforce-{now_playing}"),
        ],
        [
            InlineKeyboardButton(text="📥 دانلود موزیک فعلی", callback_data="download")
        ],
        [
            InlineKeyboardButton(text="📶 لیست پخش", callback_data="playlist"),
            InlineKeyboardButton(text="❌ بستن", callback_data="close")
        ]
    ]
    markup = InlineKeyboardMarkup(rows)
    return thumbnail, markup


async def edit_player(chat_id, key):
    last_player = redis.hget(f"{BOT_ID}:PlayerMessage", chat_id) or None
    if last_player:
        try:
            _, _id = playlist.split_key(key)
            thumb, markup = await prepare_player(chat_id)
            await bot.edit_message_media(chat_id, int(last_player), InputMediaPhoto(thumb, caption=playlist.display(_id)), reply_markup=markup)
        except:
            pass


def chunks(lst, n):
    return [lst[i:i + n] for i in range(0, len(lst), n)]


async def prepare_playlist(chat_id, page=0):
    plylist = playlist.get(chat_id)
    plist = chunks(plylist, 10)
    rows = []
    for item in plist[page]:
        _pos, _id = playlist.split_key(item)
        name = f"#{plylist.index(item)+1} : {playlist.get_name(_id)}"
        rows.append([InlineKeyboardButton(name, f"playforce-{_id}"), InlineKeyboardButton("❌", f"delete-{_id}")])
    if page == 0 and len(plist) > 1:
        rows.append([InlineKeyboardButton("صفحه بعد ⏭", f"playlist-{page+1}")])
    elif page > 0 and page == len(plist)-1:
        rows.append([InlineKeyboardButton("⏮ صفحه قبل", f"playlist-{page-1}")])
    elif page > 0 and page < len(plist)-1:
        rows.append([InlineKeyboardButton("⏮ صفحه قبل", f"playlist-{page-1}"), InlineKeyboardButton("صفحه بعد ⏭", f"playlist-{page+1}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", "back"), InlineKeyboardButton("❌ بستن", "close")])
    markup = InlineKeyboardMarkup(rows)
    return markup

############################### End Utils ###############################

############################### Start Inline ###############################

@bot.on_inline_query()
async def inline(client, query):
    answers = []
    search_query = query.query.strip()
    if search_query == "":
        await bot.answer_inline_query(query.id, results=answers, switch_pm_text="نام یک آهنگ را بنویسید ...", switch_pm_parameter="inline", cache_time=0)
    else:
        for result in rj.search(search_query):
            if result["type"] == "audio":
                answers.append(InlineQueryResultAudio(audio_url=result["link"], title=f'🎵 {result["artist"]} - {result["title"]}'))
        try:
            await query.answer(results=answers, cache_time=0)
        except errors.QueryIdInvalid:
            await query.answer(results=answers, cache_time=0, switch_pm_text="لطفا مجددا تلاش کنید.", switch_pm_parameter="")
        except errors.ResultsTooMuch:
            await query.answer(results=answers[:20], cache_time=0)

############################### End Inline ###############################

############################### Start Cli Private Permit ###############################

@cli.on_message(filters.text & filters.private & ~filters.me & ~filters.bot)
async def permit(client, message):
    if not redis.smembers(f"{BOT_ID}:CliAlerts", message.from_user.id):
        api = await bot.get_me()
        await message.reply(message.chat.id, f"❗️ این ربات، دستیار وویس چت ربات زیر میباشد و استفاده دیگری ندارد ❗️\n@{api.username}\n❗️ لطفا از پیام دادن مجدد خودداری کنید، جوابی دریافت خواهید کرد ❗️")
        redis.sadd(f"{BOT_ID}:CliAlerts", message.from_user.id)

############################### End Cli Private Permit ###############################

############################### Start Voice Chat Manager ###############################

@pytgcalls.on_stream_end()
async def on_stream_end(client, message):
    if not playlist.next(message.chat_id):
        await delete_last_player(message.chat_id)
        playlist.clear(message.chat_id)
        await leave_group_call(message.chat_id)
    else:
        key = playlist.next(message.chat_id)
        now = playlist.get_full_form(message.chat_id, playlist.now(message.chat_id))
        if key == now:
            await change_stream(message.chat_id, key, new=True)
        else:
            await change_stream(message.chat_id, key)
        await edit_player(message.chat_id, key)


@pytgcalls.on_kicked()
async def clean_playlist_on_kicked(client, chat_id):
    playlist.clear(chat_id)
    redis.srem(f"{BOT_ID}:CliGroups", chat_id)


@pytgcalls.on_closed_voice_chat()
async def clean_playlist_on_close(client, chat_id):
    playlist.clear(chat_id)
    redis.srem(f"{BOT_ID}:CliGroups", chat_id)
    await delete_last_player(chat_id)

############################### End Voice Chat Manager ###############################

############################### Start Command Manager ###############################


@bot.on_message(filters.command("playlist") & filters.group)
@authorized_groups
@authorized_users
@has_active_call
async def show_playlist(client, message):
    if not playlist.now(message.chat.id):
        return await message.reply("🚫 لیست پخش شما خالی است 🚫")
    await delete_last_player(message.chat.id)
    markup = await prepare_playlist(message.chat.id)
    await message.reply("🔆 برای پخش موزیک خارج از نوبت روی آن کلیک کنید :", reply_markup=markup)


@bot.on_message(filters.command("pause") & filters.group)
@authorized_groups
@authorized_users
@has_active_call
async def pause(client, message):
    if playlist.status(message.chat.id) == "play":
        await pytgcalls.pause_stream(message.chat.id)
        playlist.pause(message.chat.id)
        await message.reply("⚠️ پخش زنده متوقف شد ⚠️")
    else:
        await message.reply("⚠️ پخش زنده متوقف بود ⚠️")


@bot.on_message(filters.command("resume") & filters.group)
@authorized_groups
@authorized_users
@has_active_call
async def resume(client, message):
    if playlist.status(message.chat.id) == "pause":
        await pytgcalls.resume_stream(message.chat.id)
        playlist.resume(message.chat.id)
        await message.reply("⚠️ بخش زنده از سر گرفته شد ⚠️")
    else:
        await message.reply("⚠️ بخش زنده در حال اجرا بود ⚠️")


@bot.on_message(filters.command("stop") & filters.group)
@authorized_groups
@authorized_users
@has_active_call
async def stop(client, message):
    last_player = redis.hget(f"{BOT_ID}:PlayerMessage", message.chat.id) or None
    if last_player:
        try:
            await bot.delete_messages(message.chat.id, int(last_player))
        except:
            redis.hdel(f"{BOT_ID}:PlayerMessage", message.chat.id)
    await leave_group_call(message.chat.id)
    playlist.clear(message.chat.id)
    await message.reply("⚠️ پخش زنده متوقف شد ⚠️")


@bot.on_message(filters.command("download") & filters.group)
@authorized_groups
@authorized_users
@has_active_call
async def download_current(client, message):
    now_playing = playlist.now(message.chat.id)
    meta_data = playlist.extract(now_playing)
    if meta_data["identifier"] == "radiojavan":
        size = os.stat(meta_data["path"])
        size = size.st_size // (1024 * 1024)
        if size <= 20:
            if not redis.hget(f"{BOT_ID}:MessageID", meta_data["id"]):
                if not redis.sismember(f"{BOT_ID}:Saved", meta_data["id"]):
                    if redis.hget(f"{BOT_ID}:InProgress", meta_data["id"]):
                        return await bot.send_message(message.chat.id, "⚠️ این فایل در حال اپلود میباشد... ⚠️", reply_to_message_id=message.id)
                    m = await bot.send_message(message.chat.id, "⚠️ در حال آپلود ⚠️", reply_to_message_id=message.id)
                    redis.hset(f"{BOT_ID}:InProgress", meta_data["id"], "true")
                    if meta_data["type"] == "video":
                        vid = cv2.VideoCapture(meta_data["path"])
                        height = vid.get(cv2.CAP_PROP_FRAME_HEIGHT)
                        width = vid.get(cv2.CAP_PROP_FRAME_WIDTH)
                        _, ext = os.path.splitext(meta_data["path"])
                        msg = await bot.send_video(DATABASE_CHANNEL, open(meta_data["path"], "rb"), file_name=f"{meta_data['title']}{ext}", height=math.ceil(height), width=math.ceil(width), duration=int(meta_data["duration"]), thumb=open(meta_data["thumbnail"], "rb"))
                    else:
                        msg = await bot.send_audio(DATABASE_CHANNEL, open(meta_data["path"], "rb"), file_name=f"{meta_data['title']}{ext}", performer=meta_data["artist"], title=meta_data["title"])
                    redis.hdel(f"{BOT_ID}:InProgress", meta_data["id"])
                    redis.sadd(f"{BOT_ID}:Saved", meta_data["id"])
                    redis.hset(f"{BOT_ID}:MessageID", meta_data["id"], msg.id)
                    await m.delete()
            msg_id = redis.hget(f"{BOT_ID}:MessageID", meta_data["id"])
            return await bot.copy_message(message.chat.id, DATABASE_CHANNEL, int(msg_id))
        else:
            txt = ""
            if "artist" in meta_data.keys():
                txt += "🗣 خواننده : {}\n".format(meta_data['artist'])
            if "title" in meta_data.keys():
                if meta_data["type"] == "video":
                    txt += "🎵 نام موزیک ویدئو : {}\n".format(meta_data['title'])
                else:
                    txt += "🎵 نام آهنگ : {}\n".format(meta_data['title'])
            if "duration" in meta_data.keys():
                txt += "⏱ زمان : {}".format(playlist.convert_seconds(meta_data['duration']))
            txt += "\nⓂ️ حجم : {:,} مگابایت".format(size)
            return await bot.send_message(message.chat.id, "{}\n<a href='{}'>لینک دانلود موزیک فعلی</a>".format(txt, meta_data["link"]), parse_mode=ParseMode.HTML)
    else:
        return await bot.copy_message(message.chat.id, DATABASE_CHANNEL, int(meta_data["msg_id"]))


@bot.on_message(filters.command("next") & filters.group)
@authorized_groups
@authorized_users
@has_active_call
async def next(client, message):
    if not playlist.next(message.chat.id):
        return await message.reply("❗️ آهنگ بعدی در لیست پخش وجود ندارد ❗️")
    key = playlist.next(message.chat.id, force=True)
    now = playlist.now(message.chat.id)
    await change_stream(message.chat.id, key)
    _, _id = playlist.split_key(key)
    meta_data = playlist.extract(_id)
    await delete_last_player(message.chat.id)
    thumb, markup = await prepare_player(message.chat.id)
    player = await message.reply_photo(meta_data["thumbnail"], caption=playlist.display(_id), reply_markup=markup)
    redis.hset(f"{BOT_ID}:PlayerMessage", message.chat.id, player.id)


@bot.on_message(filters.command("previous") & filters.group)
@authorized_groups
@authorized_users
@has_active_call
async def previous(client, message):
    if not playlist.previous(message.chat.id):
        return await message.reply("❗️ آهنگ قبلی در لیست پخش وجود ندارد ❗️")
    key = playlist.previous(message.chat.id)
    await change_stream(message.chat.id, key)
    _, _id = playlist.split_key(key)
    meta_data = playlist.extract(_id)
    await delete_last_player(message.chat.id)
    thumb, markup = await prepare_player(message.chat.id)
    player = await message.reply_photo(meta_data["thumbnail"], caption=playlist.display(_id), reply_markup=markup)
    redis.hset(f"{BOT_ID}:PlayerMessage", message.chat.id, player.id)


@bot.on_message(filters.command("player") & filters.group)
@authorized_groups
@authorized_users
@has_active_call
async def player(client, message):
    if not playlist.now(message.chat.id):
        return await message.reply("🚫 وویس چت فعال در گروه وجود ندارد ❗️")
    await delete_last_player(message.chat.id)
    thumbnail, markup = await prepare_player(message.chat.id)
    played_seconds = await pytgcalls.played_time(message.chat.id)
    player = await message.reply_photo(thumbnail, caption=playlist.display(playlist.now(message.chat.id), played_time=played_seconds), reply_markup=markup)
    redis.hset(f"{BOT_ID}:PlayerMessage", message.chat.id, player.id)


@bot.on_message(filters.regex(r"^/(seek)\s(\+|\-)\s(\d+)$") & filters.group)
@authorized_groups
@authorized_users
@has_active_call
async def seek(client, message):
    if not playlist.now(message.chat.id):
        return await message.reply("🚫 وویس چت فعال در گروه وجود ندارد ❗️")
    op = re.match(r"^/(seek)\s(\+|\-)\s(\d+)$", message.text, re.M|re.I).group(2)
    num = int(re.match(r"^/(seek)\s(\+|\-)\s(\d+)$", message.text, re.M|re.I).group(3))
    played_seconds = await pytgcalls.played_time(message.chat.id)
    now_playing = playlist.now(message.chat.id)
    meta_data = playlist.extract(now_playing)
    if meta_data.get("seek"):
        played_seconds += eval(meta_data["seek"])
    if op == "+":
        to_seek = played_seconds + num
        if int(meta_data["duration"]) - to_seek <= 10:
            return await bot.send_message(message.chat.id, "خطا", reply_to_message_id=message.id)
    else:
        to_seek = played_seconds - num
        if to_seek <= 10:
            return await bot.send_message(message.chat.id, "خطا", reply_to_message_id=message.id)
    await change_stream(message.chat.id, playlist.get_full_form(message.chat.id, now_playing), seek=to_seek)
    if not meta_data.get("seek"):
        meta_data["seek"] = ""
    meta_data["seek"] += f"{op}{num}"
    playlist.compress(meta_data)
    await bot.send_message(message.chat.id, "{} ثانیه {} رفتیم.".format(num, "جلو" if op == "+" else "عقب"), reply_to_message_id=message.id)
    thumb, markup = await prepare_player(message.chat.id)
    message_id = redis.hget(f"{BOT_ID}:PlayerMessage", message.chat.id)
    if message_id:
        try:
            played_seconds = await pytgcalls.played_time(message.chat.id)
            await bot.edit_message_caption(message.chat.id, int(message_id), caption=playlist.display(now_playing, played_time=played_seconds), reply_markup=markup)
        except:
            pass


# @bot.on_message(filters.regex(r"^/(volume)\s(\+|\-)\s(\d+)$") & filters.group)
# @authorized_groups
# @authorized_users
# @has_active_call
# async def volume(client, message):
#     if not playlist.now(message.chat.id):
#         return await message.reply("🚫 وویس چت فعال در گروه وجود ندارد ❗️")
#     op = re.match(r"^/(volume)\s(\+|\-)\s(\d+)$", message.text, re.M|re.I).group(2)
#     num = int(re.match(r"^/(volume)\s(\+|\-)\s(\d+)$", message.text, re.M|re.I).group(3))
#     current_volume = await get_current_volume(message.chat.id)
#     new_volume = eval(f"{current_volume} {op} {num}")
#     if new_volume > 200:
#         new_volume = 200
#     if new_volume < 1:
#         new_volume = 1
#     await pytgcalls.change_volume_call(message.chat.id, new_volume)
#     await bot.send_message(message.chat.id, f"صدا تنظیم شد روی : {new_volume}", reply_to_message_id=message.id)



@bot.on_message(filters.command("play") & filters.group & filters.reply)
@authorized_groups
@authorized_users
async def play_file(client, message):
    if not message.reply_to_message.audio and not message.reply_to_message.video:
        return await message.reply("❗️ پیام ریپلای شده موزیک/ویدئو نمیباشد ❗️")
    if message.reply_to_message.audio:
        thumbnail = None
        artist = message.reply_to_message.audio.performer or ""
        title = message.reply_to_message.audio.title or ""
        item_type = "audio"
        duration = message.reply_to_message.audio.duration
        if not redis.sismember(f"{BOT_ID}:Saved", message.reply_to_message.audio.file_id):
            msg = await bot.copy_message(DATABASE_CHANNEL, message.chat.id, message.reply_to_message.id)
            redis.sadd(f"{BOT_ID}:Saved", message.reply_to_message.audio.file_id)
        msg_id = msg.id
        if message.reply_to_message.audio.thumbs:
            name = message.reply_to_message.audio.file_name
            thumb_id = message.reply_to_message.audio.thumbs[0].file_id
            filename = hasher(f"{name}-{thumb_id}")
            thumbnail = await bot.download_media(thumb_id, file_name=f"{filename}.png")
    if message.reply_to_message.video:
        artist, title = "", ""
        thumbnail = None
        item_type = "video"
        duration = message.reply_to_message.video.duration
        if not redis.sismember(f"{BOT_ID}:Saved", message.reply_to_message.video.file_id):
            msg = await bot.copy_message(DATABASE_CHANNEL, message.chat.id, message.reply_to_message.id)
            redis.sadd(f"{BOT_ID}:Saved", message.reply_to_message.video.file_id)
            msg_id = msg.id
        if message.reply_to_message.video.thumbs:
            name = message.reply_to_message.video.file_name
            thumb_id = message.reply_to_message.video.thumbs[0].file_id
            filename = hasher(f"{name}-{thumb_id}")
            thumbnail = await bot.download_media(thumb_id, file_name=f"{filename}.png")
    data = {
        "identifier": "telegram",
        "id": f"{message.chat.id}/{message.reply_to_message_id}",
        "type": item_type,
        "duration": duration,
        "msg_id": msg_id,
    }
    if artist:
        data["artist"] = artist
    if title:
        data["title"] = title
    pre_msg = await message.reply(f"🔄 در حال آماده سازی موزیک{' ویدئو' if item_type == 'video' else ''} ...")
    cover_path = await cover(artist, title, type=item_type, duration=duration, thumbnail=thumbnail)
    thumb_path = save_to("thumbnails", cover_path)
    data["thumbnail"] = thumb_path
    msg = message.reply_to_message
    media = eval(f"msg.{item_type}")
    _, ext = os.path.splitext(media.file_name)
    filename = hasher(f"{media.file_name}-{media.file_id}")
    await pre_msg.edit("📥 در حال دانلود ...")
    file_path = await msg.download(file_name=f"{filename}{ext}")
    data["path"] = save_to(f"{data['type']}s", file_path)
    playlist.compress(data)
    await pre_msg.edit("📥 دانلود شد ✅")
    is_helper_ready = await prepare_helper(message.chat.id, message.id)
    if is_helper_ready:
        active_calls = [call for call in get_active_calls()]
        _, _id = playlist.add(message.chat.id, data)
        if message.chat.id in active_calls:
            pos = playlist.get_possition(message.chat.id, _id)
            if not _:
                return await pre_msg.edit(f"⚠️ این موزیک/ویدئو در جایگاه {pos} از لیست پخش شما قرار دارد ⚠️")
            await pre_msg.delete()
            return await message.reply_photo(data["thumbnail"], caption=f"✅ موزیک/ویدئو مورد نظر در جایگاه {pos} لیست پخش اضافه شد ✅")
        else:
            stream = AudioPiped(data["path"], MediumQualityAudio())
            if data["type"] == "video":
                stream = AudioVideoPiped(data["path"], MediumQualityAudio(), MediumQualityVideo())
            try:
                if len(active_calls) < pytgcalls.get_max_voice_chat():
                    await join_group_call(message.chat.id, stream)
                    playlist.play(message.chat.id, _id)
                    playlist.set_rule(message.chat.id, "queue")
                    await pre_msg.delete()
                    await player(client, message)
                else:
                    return await pre_msg.edit("🚫 ربات در حداکثر تعداد وویس چت ممکن عضو شده است و تحت فشار است، لطفا بعدا مجددا تلاش فرمایید 🚫")
            except tgerrors.NoActiveGroupCall:
                return await pre_msg.edit("🚫 وویس چت فعال در گروه وجود ندارد ❗️")


@bot.on_message(filters.regex(r"^/(play)\s+(.*)$") & filters.group)
@authorized_groups
@authorized_users
async def play_search(client, message):
    text = re.match(r"^/(play)\s+(.*)$", message.text, re.M|re.I).group(2)
    rows = []
    for i in rj.search(text):
        icon = "🎵" if i["type"] == "audio" else "🎬"
        name = f"{icon} {i['artist']} - {i['title']}"
        rows.append([InlineKeyboardButton(name, f"song-{i['type']}-{i['id']}")])
    if len(rows) > 0:
        markup = InlineKeyboardMarkup(rows)
        await message.reply("🔆 از بین نتیج زیر انتخاب کنید :", reply_markup=markup)
    else:
        await message.reply("☹️ چیزی پیدا نکردم ☹️")


@bot.on_callback_query(filters.regex(r'^(song)-(audio|video)-(\d+)$'))
@authorized_groups
@authorized_users
async def search_select(client, callbackquery):
    groups = re.match(r"^(song)-(audio|video)-(\d+)$", callbackquery.data, re.M|re.I).groups()
    item_type = groups[1]
    item_id = int(groups[2])
    is_helper_ready = await prepare_helper(callbackquery.message.chat.id, callbackquery.message.id, callback=True)
    if is_helper_ready:
        text = f"🔄 در حال آماده سازی موزیک{' ویدئو' if item_type == 'video' else ''} انتخابی شما ..."
        await bot.edit_message_text(callbackquery.message.chat.id, callbackquery.message.id, text=text)
        if item_type == "video":
            result = rj.get_video(item_id)
        else:
            result = rj.get_audio(item_id)
        cover_path = await cover(result["artist"], result["title"], type=item_type, duration=result.get("duration", None), thumbnail=result.get("thumbnail", None))
        thumb_path = save_to("thumbnails", cover_path)
        result["thumbnail"] = thumb_path
        result["identifier"] = "radiojavan"
        _, ext = os.path.splitext(result["link"])
        filename = hasher(f"{result['title']}-{result['id']}")
        await bot.edit_message_text(callbackquery.message.chat.id, callbackquery.message.id, text="📥 در حال دانلود ...")
        file_path = download_url(result["link"], f"{filename}{ext}")
        result["path"] = save_to(f"{result['type']}s", file_path)
        playlist.compress(result)
        active_calls = [call for call in get_active_calls()]
        _, _id = playlist.add(callbackquery.message.chat.id, result)
        if callbackquery.message.chat.id in active_calls:
            pos = playlist.get_possition(callbackquery.message.chat.id, _id)
            if not _:
                return await bot.edit_message_caption(callbackquery.message.chat.id, callbackquery.message.id, caption=f"⚠️ این موزیک/ویدئو در جایگاه {pos} از لیست پخش شما قرار دارد ⚠️")
            await bot.delete_messages(callbackquery.message.chat.id, callbackquery.message.id)
            return await bot.send_photo(callbackquery.message.chat.id, result["thumbnail"], caption=f"✅ موزیک/ویدئو مورد نظر در جایگاه {pos} لیست پخش اضافه شد ✅")
        else:
            stream = AudioPiped(result["path"], MediumQualityAudio())
            if result["type"] == "video":
                stream = AudioVideoPiped(result["path"], MediumQualityAudio(), MediumQualityVideo())
            try:
                if len(active_calls) < pytgcalls.get_max_voice_chat():
                    await join_group_call(callbackquery.message.chat.id, stream)
                    playlist.play(callbackquery.message.chat.id, _id)
                    playlist.set_rule(callbackquery.message.chat.id, "queue")
                    await bot.delete_messages(callbackquery.message.chat.id, callbackquery.message.id)
                    thumb, markup = await prepare_player(callbackquery.message.chat.id)
                    player = await bot.send_photo(callbackquery.message.chat.id, result["thumbnail"], caption=playlist.display(playlist.now(callbackquery.message.chat.id)), reply_markup=markup)
                    redis.hset(f"{BOT_ID}:PlayerMessage", callbackquery.message.chat.id, player.id)
                else:
                    return await bot.edit_message_caption(callbackquery.message.chat.id, callbackquery.message.id, caption="🚫 ربات در حداکثر تعداد وویس چت ممکن عضو شده است و تحت فشار است، لطفا بعدا مجددا تلاش فرمایید 🚫")
            except tgerrors.NoActiveGroupCall:
                return await bot.edit_message_caption(callbackquery.message.chat.id, callbackquery.message.id, caption="🚫 وویس چت فعال در گروه وجود ندارد ❗️")


@bot.on_callback_query(filters.regex(r'^(resume|pause|next|previous|stop|playlist|close|back|download)$'))
@authorized_groups
@authorized_users
async def manage(client, callbackquery):
    command = re.match(r'^(resume|pause|next|previous|stop|playlist|close|back|download)$', callbackquery.data, re.M|re.I).group(0)
    chat_id = callbackquery.message.chat.id
    message_id = callbackquery.message.id
    if command == "previous":
        if not playlist.previous(chat_id):
            return await callbackquery.answer("❗️ آهنگ قبلی در لیست پخش وجود ندارد ❗️", show_alert=True)
        key = playlist.previous(chat_id)
        await change_stream(chat_id, key)
        await edit_player(chat_id, key)
    elif command == "next":
        if not playlist.next(chat_id):
            return await callbackquery.answer("❗️ آهنگ بعدی در لیست پخش وجود ندارد ❗️", show_alert=True)
        key = playlist.next(chat_id, force=True)
        await change_stream(chat_id, key)
        await edit_player(chat_id, key)
    elif command == "pause":
        await pytgcalls.pause_stream(chat_id)
        playlist.pause(chat_id)
        await callbackquery.answer("⚠️ پخش زنده متوقف شد ⚠️", show_alert=True)
        thumb, markup = await prepare_player(chat_id)
        await bot.edit_message_reply_markup(chat_id, message_id, markup)
    elif command == "resume":
        await pytgcalls.resume_stream(chat_id)
        playlist.resume(chat_id)
        await callbackquery.answer("⚠️ بخش زنده از سر گرفته شد ⚠️", show_alert=True)
        thumb, markup = await prepare_player(chat_id)
        await bot.edit_message_reply_markup(chat_id, message_id, markup)
    elif command == "stop":
        await delete_last_player(chat_id)
        await leave_group_call(chat_id)
        playlist.clear(chat_id)
        await bot.send_message(chat_id, "⚠️ پخش زنده متوقف شد ⚠️")
    elif command == "close":
        await bot.edit_message_reply_markup(chat_id, message_id, None)
    elif command == "playlist":
        await delete_last_player(chat_id)
        markup = await prepare_playlist(chat_id)
        await bot.send_message(chat_id, "🔆 برای پخش موزیک خارج از نوبت روی آن کلیک کنید :", reply_markup=markup)
    elif command == "download":
        now_playing = playlist.now(chat_id)
        meta_data = playlist.extract(now_playing)
        if meta_data["identifier"] == "radiojavan":
            size = os.stat(meta_data["path"])
            size = size.st_size // (1024 * 1024)
            if size <= 20:
                if not redis.hget(f"{BOT_ID}:MessageID", meta_data["id"]):
                    if not redis.sismember(f"{BOT_ID}:Saved", meta_data["id"]):
                        if redis.hget(f"{BOT_ID}:InProgress", meta_data["id"]):
                            return await callbackquery.answer("⚠️ این فایل در حال اپلود میباشد... ⚠️", show_alert=True)
                        await callbackquery.answer("⚠️ در حال آپلود ⚠️", show_alert=True)
                        redis.hset(f"{BOT_ID}:InProgress", meta_data["id"], "true")
                        _, ext = os.path.splitext(meta_data["path"])
                        if meta_data["type"] == "video":
                            vid = cv2.VideoCapture(meta_data["path"])
                            height = vid.get(cv2.CAP_PROP_FRAME_HEIGHT)
                            width = vid.get(cv2.CAP_PROP_FRAME_WIDTH)
                            msg = await bot.send_video(DATABASE_CHANNEL, open(meta_data["path"], "rb"), file_name=f"{meta_data['title']}{ext}", height=math.ceil(height), width=math.ceil(width), duration=int(meta_data["duration"]), thumb=open(meta_data["thumbnail"], "rb"))
                        else:
                            msg = await bot.send_audio(DATABASE_CHANNEL, open(meta_data["path"], "rb"), file_name=f"{meta_data['title']}{ext}", performer=meta_data["artist"], title=meta_data["title"])
                        redis.hdel(f"{BOT_ID}:InProgress", meta_data["id"])
                        redis.sadd(f"{BOT_ID}:Saved", meta_data["id"])
                        redis.hset(f"{BOT_ID}:MessageID", meta_data["id"], msg.id)
                msg_id = redis.hget(f"{BOT_ID}:MessageID", meta_data["id"])
                return await bot.copy_message(callbackquery.message.chat.id, DATABASE_CHANNEL, int(msg_id))
            else:
                txt = ""
                if "artist" in meta_data.keys():
                    txt += "🗣 خواننده : {}\n".format(meta_data['artist'])
                if "title" in meta_data.keys():
                    if meta_data["type"] == "video":
                        txt += "🎵 نام موزیک ویدئو : {}\n".format(meta_data['title'])
                    else:
                        txt += "🎵 نام آهنگ : {}\n".format(meta_data['title'])
                if "duration" in meta_data.keys():
                    txt += "⏱ زمان : {}".format(playlist.convert_seconds(meta_data['duration']))
                txt += "\nⓂ️ حجم : {:,} مگابایت".format(size)
                return await bot.send_message(callbackquery.message.chat.id, "{}\n<a href='{}'>لینک دانلود موزیک فعلی</a>".format(txt, meta_data["link"]), parse_mode=ParseMode.HTML)
        else:
            return await bot.copy_message(callbackquery.message.chat.id, DATABASE_CHANNEL, int(meta_data["msg_id"]))
    elif command == "back":
        await bot.delete_messages(chat_id, message_id)
        thumb, markup = await prepare_player(chat_id)
        played_seconds = await pytgcalls.played_time(chat_id)
        player = await bot.send_photo(chat_id, thumb, caption=playlist.display(playlist.now(chat_id), played_time=played_seconds), reply_markup=markup)
        redis.hset(f"{BOT_ID}:PlayerMessage", chat_id, player.id)


@bot.on_callback_query(filters.regex(r'^(changerule)-(.*)$'))
@authorized_groups
@authorized_users
async def change_rule(client, callbackquery):
    rule = re.match(r'^(changerule)-(.*)$', callbackquery.data, re.M|re.I).group(2)
    chat_id = callbackquery.message.chat.id
    message_id = callbackquery.message.id
    rules = ["queue", "repeat", "repeat-one", "shuffle"]
    index = rules.index(rule) + 1
    actions = ["پخش تا انتها", "تکرار لیست پخش", "تکرار یک آهنگ", "پخش در هم"]
    if index == len(rules):
        index = 0
    next_rule = rules[index]
    playlist.set_rule(chat_id, next_rule)
    await callbackquery.answer(f"⚠️ حالت پخش تنظیم شد روی : {actions[index]} ⚠️", show_alert=True)
    thumb, markup = await prepare_player(chat_id)
    await bot.edit_message_reply_markup(chat_id, message_id, markup)


@bot.on_callback_query(filters.regex(r'^(playlist)-(\d+)$'))
@authorized_groups
@authorized_users
async def playlist_paginate(client, callbackquery):
    page = int(re.match(r'^(playlist)-(\d+)$', callbackquery.data, re.M|re.I).group(2))
    chat_id = callbackquery.message.chat.id
    message_id = callbackquery.message.id
    markup = await prepare_playlist(chat_id, page)
    await bot.edit_message_reply_markup(chat_id, message_id, reply_markup=markup)


@bot.on_callback_query(filters.regex(r'^(playforce)-(.*)$'))
@authorized_groups
@authorized_users
async def playforce(client, callbackquery):
    _id = re.match(r'^(playforce)-(.*)$', callbackquery.data, re.M|re.I).group(2)
    chat_id = callbackquery.message.chat.id
    message_id = callbackquery.message.id
    await bot.delete_messages(chat_id, message_id)
    key = playlist.get_full_form(chat_id, _id)
    await change_stream(chat_id, key)
    thumb, markup = await prepare_player(chat_id)
    player = await bot.send_photo(chat_id, thumb, caption=playlist.display(_id), reply_markup=markup)
    redis.hset(f"{BOT_ID}:PlayerMessage", chat_id, player.id)


@bot.on_callback_query(filters.regex(r'^(delete)-(.*)$'))
@authorized_groups
@authorized_users
async def delete_playlist_item(client, callbackquery):
    _id = re.match(r'^(delete)-(.*)$', callbackquery.data, re.M|re.I).group(2)
    chat_id = callbackquery.message.chat.id
    message_id = callbackquery.message.id
    key = playlist.get_full_form(chat_id, _id)
    plylist = playlist.get(chat_id)
    now_playing = playlist.now(chat_id)
    if len(plylist) == 1:
        await delete_last_player(chat_id)
        await leave_group_call(chat_id)
        playlist.clear(chat_id)
        return await bot.edit_message_text(chat_id, message_id, "لیست پخش خالی و پخش زنده متوقف شد.")
    if now_playing == _id:
        next_key = playlist.next(chat_id, force=True)
        playlist.rem(chat_id, _id)
        await change_stream(chat_id, next_key)
        await callbackquery.answer("حذف شد", show_alert=True)
        await bot.delete_messages(chat_id, message_id)
        thumb, markup = await prepare_player(chat_id)
        player = await bot.send_photo(chat_id, thumb, caption=playlist.display(_id), reply_markup=markup)
        redis.hset(f"{BOT_ID}:PlayerMessage", chat_id, player.id)
        return
    playlist.rem(chat_id, _id)
    await bot.edit_message_text(chat_id, message_id, "حذف شد.")


@bot.on_callback_query(filters.regex(r'^(seek)(\+|\-)(\d+)$'))
@authorized_groups
@authorized_users
async def seek_cb(client, callbackquery):
    op = re.match(r"^(seek)(\+|\-)(\d+)$", callbackquery.data, re.M|re.I).group(2)
    num = int(re.match(r"^(seek)(\+|\-)(\d+)$", callbackquery.data, re.M|re.I).group(3))
    chat_id = callbackquery.message.chat.id
    message_id = callbackquery.message.id
    played_seconds = await pytgcalls.played_time(chat_id)
    now_playing = playlist.now(chat_id)
    meta_data = playlist.extract(now_playing)
    if meta_data.get("seek"):
        played_seconds += eval(meta_data["seek"])
    if op == "+":
        to_seek = played_seconds + num
        if int(meta_data["duration"]) - to_seek <= 10:
            return await callbackquery.answer("خطا", show_alert=True)
    else:
        to_seek = played_seconds - num
        if to_seek <= 10:
            return await callbackquery.answer("خطا", show_alert=True)
    await change_stream(chat_id, playlist.get_full_form(chat_id, now_playing), seek=to_seek)
    if not meta_data.get("seek"):
        meta_data["seek"] = ""
    meta_data["seek"] += f"{op}{num}"
    playlist.compress(meta_data)
    await callbackquery.answer("{} ثانیه {} رفتیم.".format(num, "جلو" if op == "+" else "عقب"), show_alert=True)
    thumb, markup = await prepare_player(chat_id)
    try:
        await bot.edit_message_caption(chat_id, message_id, caption=playlist.display(now_playing, played_time=played_seconds), reply_markup=markup)
    except:
        pass


############################### End Command Manager ###############################

async def main():
    async with Client(f"{BOT_ID}-bot-cli", api_id=API_ID, api_hash=API_HASH, in_memory=True) as app:
        session_string = await app.export_session_string()
        redis.set(f"{BOT_ID}:SessionString", session_string)
        print(f"{Fore.GREEN}Logged In Successfully!\nrun : ./start.sh{Fore.RESET}")


if not redis.get(f"{BOT_ID}:SessionString"):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())


bot.start()
pytgcalls.start()
idle()
