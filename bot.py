import logging
import os
import re
import datetime
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters
)
from telegram.constants import ParseMode

import config
import database
import vercel_deploy

database.init_db()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

EMOJI_IDS = {
    "check":    "4931449589199799267",
    "cross":    "5337248355707794559",
    "fire":     "5427264470730708748",
    "star":     "5469744063815102906",
    "crown":    "5861926791857311729",
    "gem":      "5206302688412723181",
    "hour":     "6102813833699525998",
    "key":      "5456145304002772771",
    "gift":     "5355193051193059834",
    "rocket":   "6334764783765556950",
    "lock":     "5296369303661067030",
    "warning":  "5240241223632954241",
    "party":    "5431454696659267949",
    "bolt":     "5438539112070002676",
    "arrow":    "5416117059207572332",
    "pencil":   "5192843798505332510",
    "folder":   "5258032197943714152",
    "trash":    "5348083793691439817",
    "package":  "5407022779490907157",
    "phone":    "5927173710225084801",
    "info":     "5238099667039822622",
    "gear":     "5341715473882955310",
    "download": "6096080291347042802",
    "mobile":   "5353066170503142084",
    "store":    "5931594395508805861",
}

EMOJI_FALLBACK = {
    "check": "✅", "cross": "❌", "fire": "🔥", "star": "⭐",
    "crown": "👑", "gem": "💎", "hour": "⌛", "key": "🔑",
    "gift": "🎁", "rocket": "🚀", "lock": "🔒", "warning": "⚠️",
    "party": "🎉", "bolt": "⚡", "arrow": "➡️", "pencil": "✏️",
    "folder": "📄", "trash": "🗑️", "package": "📦", "phone": "📞",
    "info": "📋", "gear": "⚙️", "download": "📥", "mobile": "📱",
    "store": "🏪",
}


def e(name):
    if name in EMOJI_IDS:
        return f'<tg-emoji emoji-id="{EMOJI_IDS[name]}">{EMOJI_FALLBACK[name]}</tg-emoji>'
    return EMOJI_FALLBACK.get(name, "")


def eb(name):
    return EMOJI_FALLBACK.get(name, "")


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def detect_premium_emoji(message):
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    if not entities:
        return esc(text)
    utf16 = text.encode("utf-16-le")
    def slice_utf16(start, length):
        return utf16[start * 2:(start + length) * 2].decode("utf-16-le", errors="ignore")
    entities = sorted(entities, key=lambda x: x.offset)
    result = []
    cursor = 0
    total_chars = len(utf16) // 2
    for en in entities:
        if en.offset > cursor:
            result.append(esc(slice_utf16(cursor, en.offset - cursor)))
        segment = slice_utf16(en.offset, en.length)
        if en.type == "custom_emoji":
            result.append(f'<tg-emoji emoji-id="{en.custom_emoji_id}">{esc(segment)}</tg-emoji>')
        else:
            result.append(esc(segment))
        cursor = en.offset + en.length
    if cursor < total_chars:
        result.append(esc(slice_utf16(cursor, total_chars - cursor)))
    return "".join(result)


def make_btn(emoji_name, label, callback_data=None, url=None, style=None):
    kwargs = {"text": f"{eb(emoji_name)} {label}".strip()}
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    if style is not None:
        try:
            return InlineKeyboardButton(**kwargs, style=style)
        except TypeError:
            return InlineKeyboardButton(**kwargs)
    return InlineKeyboardButton(**kwargs)


def format_deploy_result(result, page_name, theme):
    if not result.get("ready"):
        return (
            f"{e('warning')} <b>Deploy Failed or Timed Out</b>\n\n"
            f"Vercel state: <code>{result.get('state', 'unknown')}</code>"
        )
    return (
        f"{e('check')} <b>Page Live!</b>\n\n"
        f"{e('folder')} Domain: <code>{page_name}.vercel.app</code>\n"
        f"{e('bolt')} Theme: <b>{theme}</b>\n"
        f"{e('arrow')} URL: {result['url']}"
    )


def _download_url(page_name: str) -> str:
    base = (config.PUBLIC_BASE_URL or "").rstrip("/")
    if not base:
        raise RuntimeError(
            "PUBLIC_BASE_URL not set. Set it in config.py or as env var to your Railway public URL."
        )
    return f"{base}/dl/{page_name}"


DEFAULT_THEMES = {
    "mpariv1": {"name": "mParivahan Classic", "preview_image": "", "caption": "📱 <b>mParivahan Classic</b>", "emoji": "mobile"},
    "mpari-playstore": {"name": "mParivahan Play Store", "preview_image": "", "caption": "🚗 <b>mParivahan Play Store</b>", "emoji": "store"}
}

THEME_ORDER = ["mpariv1", "mpari-playstore"]


def load_themes():
    cfg = database.load_bot_config()
    themes = cfg.get("themes")
    if not themes:
        cfg["themes"] = DEFAULT_THEMES
        database.save_bot_config(cfg)
        return dict(DEFAULT_THEMES)
    updated = False
    for k, v in DEFAULT_THEMES.items():
        if k not in themes:
            themes[k] = v
            updated = True
    if updated:
        save_themes(themes)
    return themes


def save_themes(themes):
    cfg = database.load_bot_config()
    cfg["themes"] = themes
    database.save_bot_config(cfg)


def get_theme_order(themes):
    ordered = [k for k in THEME_ORDER if k in themes]
    for k in themes:
        if k not in ordered:
            ordered.append(k)
    return ordered


(
    MAIN_MENU, WAITING_FOR_THEME, WAITING_FOR_NAME, WAITING_FOR_APK,
    MY_PAGES, WAITING_FOR_NEW_NAME, WAITING_FOR_NEW_APK, WAITING_FOR_NEW_APK_ONLY
) = range(8)

TIER_INFO = {
    1: {"name": "Bronze", "max_pages": 1, "url_changes_per_day": 1},
    2: {"name": "Diamond", "max_pages": 3, "url_changes_per_day": 3},
    3: {"name": "Root", "max_pages": 99999, "url_changes_per_day": 999999},
}


async def is_user_admin(user_id):
    if user_id == config.ROOT_ADMIN_ID:
        return True
    user = database.get_user(user_id)
    if user and user[3] == 1:
        return True
    return False


async def check_user_blocked(update: Update):
    user_id = update.effective_user.id
    user = database.get_user(user_id)
    if user and user[2] == 1:
        try:
            await update.message.reply_text(f"{e('lock')} <b>You are blocked.</b>", parse_mode=ParseMode.HTML)
        except Exception:
            pass
        return True
    return False


async def get_active_tier(user_id):
    key = database.get_user_key(user_id)
    if not key:
        return None
    key_data, err = database.validate_key(key)
    if err:
        return None
    return key_data[1]


def build_admin_keyboard():
    return InlineKeyboardMarkup([
        [make_btn("key", "Generate Key", callback_data='admin_gen_key', style='success')],
        [make_btn("cross", "Cancel Key", callback_data='admin_cancel_key', style='danger')],
        [make_btn("lock", "Block/Unblock", callback_data='admin_block_user', style='danger')],
        [make_btn("info", "View All Pages", callback_data='admin_view_pages', style='primary')],
        [make_btn("arrow", "Back", callback_data='back_main', style='primary')],
    ])


def build_main_keyboard():
    return InlineKeyboardMarkup([
        [make_btn("pencil", "Create Page", callback_data='create_page', style='success')],
        [
            make_btn("folder", "My Pages", callback_data='my_pages', style='primary'),
            make_btn("info", "Help", callback_data='help', style='primary'),
        ],
    ])


def build_theme_list_keyboard(order, themes, base_cb='pick_'):
    rows = []
    for k in order:
        if k not in themes:
            continue
        info = themes[k]
        rows.append([make_btn(info.get("emoji", "fire"), f"Use {info.get('name', k)}", callback_data=f"{base_cb}{k}", style='primary')])
    rows.append([make_btn("arrow", "Back", callback_data='back_main', style='primary')])
    return InlineKeyboardMarkup(rows)


def build_preview_keyboard(theme_key):
    return InlineKeyboardMarkup([
        [make_btn("check", "Use this theme", callback_data=f'usetheme_{theme_key}', style='success')],
        [make_btn("arrow", "Previous", callback_data='preview_back', style='primary')],
        [make_btn("arrow", "Back", callback_data='back_main', style='primary')],
    ])


async def send_main_menu(chat_id, context):
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"{e('arrow')} <b>Main Menu</b>\n\nChoose an option below:",
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_keyboard()
    )


async def send_admin_panel(chat_id, context):
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"{e('gear')} <b>Admin Panel</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_admin_keyboard()
    )


async def send_theme_list(chat_id, context, intro_text=None):
    themes = load_themes()
    order = get_theme_order(themes)
    if not order:
        await context.bot.send_message(chat_id=chat_id, text=f"{e('warning')} No themes available.")
        return
    text = intro_text or f"{e('fire')} <b>Select a Theme</b>\n\nChoose one below:"
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
                                   reply_markup=build_theme_list_keyboard(order, themes))


async def send_theme_preview(chat_id, context, theme_key):
    themes = load_themes()
    if theme_key not in themes:
        await context.bot.send_message(chat_id=chat_id, text=f"{e('cross')} Theme not found.")
        return
    info = themes[theme_key]
    emoji_name = info.get("emoji", "fire")
    name = info.get("name", theme_key)
    caption = info.get("caption", name)
    preview = info.get("preview_image", "")
    kb = build_preview_keyboard(theme_key)
    full_caption = f"{eb(emoji_name)} <b>{name}</b>\n\n{caption}"
    if preview:
        try:
            await context.bot.send_photo(chat_id=chat_id, photo=preview, caption=full_caption,
                                         parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        except Exception:
            pass
    await context.bot.send_message(chat_id=chat_id, text=full_caption, parse_mode=ParseMode.HTML, reply_markup=kb)


async def notify_admin_new_page(context, user, page_name, theme, apk_name, vercel_url, tier_name, expires_at, key_code):
    try:
        text = (
            f"{e('party')} <b>New Page Created</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{e('star')} <b>User:</b> @{user.username or user.first_name}\n"
            f"{e('arrow')} <b>ID:</b> <code>{user.id}</code>\n"
            f"{e('key')} <b>Key:</b> <code>{key_code}</code>\n"
            f"{e('crown')} <b>Tier:</b> {tier_name}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{e('rocket')} <b>Page:</b> <code>{page_name}</code>\n"
            f"{e('bolt')} <b>Theme:</b> <code>{theme}</code>\n"
            f"{e('package')} <b>APK:</b> <code>{apk_name}</code>\n"
            f"{e('hour')} <b>Expires:</b> {expires_at[:10]}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{e('check')} <b>Live URL:</b>\n{vercel_url}"
        )
        await context.bot.send_message(chat_id=config.ROOT_ADMIN_ID, text=text,
                                       parse_mode=ParseMode.HTML, disable_web_page_preview=False)
    except Exception:
        pass


async def send_start_message(update, context, is_premium):
    cfg = database.load_bot_config()
    media = cfg.get("start1_media") if is_premium else cfg.get("start_media")
    text = cfg.get("start1_text") if is_premium else cfg.get("start_text")
    if not text:
        if is_premium:
            text = f"{e('gem')} <b>Premium Access Active</b>\n\nWelcome back!"
        else:
            text = (
                f"{e('fire')} <b>Welcome to NEMO HOST</b>\n\n"
                f"Host your APK files on custom Vercel pages.\n\n"
                f"{e('key')} Use /redeem to activate your key\n"
                f"{e('gift')} Use /trail for a free trial\n"
                f"{e('arrow')} Contact admin to purchase"
            )
    try:
        if media:
            try:
                if media["type"] == "photo":
                    await update.message.reply_photo(photo=media["file_id"], caption=text, parse_mode=ParseMode.HTML)
                    return
                elif media["type"] == "video":
                    await update.message.reply_video(video=media["file_id"], caption=text, parse_mode=ParseMode.HTML)
                    return
            except Exception:
                pass
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception:
        try:
            await update.message.reply_text(text)
        except Exception:
            pass


async def expire_pages_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = sqlite3.connect(database.DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, page_name, key_code, expires_at, created_at FROM pages")
        rows = cur.fetchall()
        now = datetime.datetime.now()

        for page_id, user_id, page_name, key_code, expires_at, created_at in rows:
            should_delete = False

            if key_code:
                key_data, err = database.validate_key(key_code)
                if err:
                    should_delete = True
                else:
                    continue
            else:
                if created_at:
                    try:
                        created_dt = datetime.datetime.fromisoformat(created_at)
                        age_hours = (now - created_dt).total_seconds() / 3600
                        if age_hours < 24:
                            continue
                    except Exception:
                        pass
                should_delete = True

            if should_delete and expires_at:
                try:
                    page_exp = datetime.datetime.fromisoformat(expires_at)
                    if page_exp > now:
                        continue
                except Exception:
                    pass

            if should_delete:
                try:
                    vercel_deploy.delete_vercel_project(page_name)
                except Exception:
                    pass
                cur.execute("DELETE FROM pages WHERE id = ?", (page_id,))
                conn.commit()
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"{e('warning')} Your page <code>{page_name}</code> has expired and been removed.",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
        conn.close()
    except Exception as ex:
        logger.error(f"Expiry job error: {ex}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    database.add_user(user.id, user.username or user.first_name)
    if await check_user_blocked(update):
        return ConversationHandler.END
    has_key = await get_active_tier(user.id) is not None
    await send_start_message(update, context, has_key)
    await update.message.reply_text(
        f"{e('arrow')} <b>Main Menu</b>\n\nChoose an option below:",
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_keyboard()
    )
    return MAIN_MENU


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    database.add_user(user.id, user.username or user.first_name)
    if not await is_user_admin(user.id):
        await update.message.reply_text(f"{e('cross')} <b>Not authorized.</b>", parse_mode=ParseMode.HTML)
        return
    await send_admin_panel(user.id, context)


async def admin_gen_key_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await is_user_admin(query.from_user.id):
        await query.answer("Not authorized.", show_alert=True)
        return
    await query.edit_message_text(
        f"{e('key')} <b>Generate Key</b>\n\n1 = Bronze\n2 = Diamond\n3 = Root\n\nSend tier:",
        parse_mode=ParseMode.HTML
    )
    context.user_data['admin_awaiting'] = 'gen_key_tier'


async def admin_cancel_key_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await is_user_admin(query.from_user.id):
        await query.answer("Not authorized.", show_alert=True)
        return
    await query.edit_message_text(f"{e('cross')} Send Key Code to cancel:", parse_mode=ParseMode.HTML)
    context.user_data['admin_awaiting'] = 'cancel_key'


async def admin_block_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await is_user_admin(query.from_user.id):
        await query.answer("Not authorized.", show_alert=True)
        return
    await query.edit_message_text(f"{e('lock')} Send User ID to block/unblock:", parse_mode=ParseMode.HTML)
    context.user_data['admin_awaiting'] = 'block_user'


async def admin_view_pages_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await is_user_admin(query.from_user.id):
        await query.answer("Not authorized.", show_alert=True)
        return
    conn = sqlite3.connect(database.DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT page_name, vercel_url, user_id FROM pages ORDER BY id DESC LIMIT 50")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await query.edit_message_text(f"{e('warning')} No pages yet.", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[make_btn("arrow", "Back", callback_data='back_main', style='primary')]]))
        return
    text = f"{e('rocket')} <b>Recent Pages:</b>\n\n"
    for row in rows:
        text += f"{e('fire')} <code>{row[0]}</code>\n   {row[1]}\n   User: <code>{row[2]}</code>\n\n"
    if len(text) > 4000:
        text = text[:4000] + "..."
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[make_btn("arrow", "Back", callback_data='back_main', style='primary')]]))


async def flag_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    admin_awaiting = context.user_data.get('admin_awaiting')
    setting_target = context.user_data.get('setting_target')
    if not (admin_awaiting or setting_target):
        return

    if admin_awaiting and await is_user_admin(update.effective_user.id):
        text = msg.text.strip()
        if admin_awaiting == 'gen_key_tier':
            if text not in ['1', '2', '3']:
                await msg.reply_text(f"{e('cross')} Send 1, 2, or 3.", parse_mode=ParseMode.HTML)
                return
            context.user_data['admin_new_tier'] = int(text)
            tier_names = {1: "Bronze", 2: "Diamond", 3: "Root"}
            await msg.reply_text(f"{e('star')} Tier: <b>{tier_names[int(text)]}</b>\n\nHow many days?", parse_mode=ParseMode.HTML)
            context.user_data['admin_awaiting'] = 'gen_key_days'
            return
        if admin_awaiting == 'gen_key_days':
            try:
                days = int(text)
                if days <= 0: raise ValueError
            except ValueError:
                await msg.reply_text(f"{e('cross')} Send a positive number.", parse_mode=ParseMode.HTML)
                return
            tier = context.user_data.get('admin_new_tier', 1)
            key = database.generate_key(tier, days)
            tier_names = {1: "Bronze", 2: "Diamond", 3: "Root"}
            expires = datetime.datetime.now() + datetime.timedelta(days=days)
            await msg.reply_text(
                f"{e('check')} <b>Key Generated</b>\n\n{e('key')} <code>{key}</code>\n\n"
                f"{e('star')} {tier_names[tier]}\n{e('hour')} {days} day(s)\n"
                f"{e('arrow')} Expires: {expires.strftime('%Y-%m-%d')}",
                parse_mode=ParseMode.HTML
            )
            context.user_data.pop('admin_awaiting', None)
            await send_admin_panel(update.effective_user.id, context)
            return
        if admin_awaiting == 'cancel_key':
            key_code = text.upper()
            database.cancel_key(key_code)
            await msg.reply_text(f"{e('check')} Canceled <code>{key_code}</code>", parse_mode=ParseMode.HTML)
            context.user_data.pop('admin_awaiting', None)
            await send_admin_panel(update.effective_user.id, context)
            return
        if admin_awaiting == 'block_user':
            try:
                user_id = int(text)
            except ValueError:
                await msg.reply_text(f"{e('cross')} Invalid ID.", parse_mode=ParseMode.HTML)
                return
            user = database.get_user(user_id)
            if not user:
                await msg.reply_text(f"{e('cross')} Not found.", parse_mode=ParseMode.HTML)
                context.user_data.pop('admin_awaiting', None)
                await send_admin_panel(update.effective_user.id, context)
                return
            new_status = 0 if user[2] == 1 else 1
            database.block_user(user_id, new_status)
            status = f"{e('lock')} Blocked" if new_status == 1 else f"{e('check')} Unblocked"
            await msg.reply_text(f"User <code>{user_id}</code> → {status}", parse_mode=ParseMode.HTML)
            context.user_data.pop('admin_awaiting', None)
            await send_admin_panel(update.effective_user.id, context)
            return

    if setting_target in ('start_text', 'start1_text') and await is_user_admin(update.effective_user.id):
        html_text = detect_premium_emoji(msg)
        cfg = database.load_bot_config()
        cfg[setting_target] = html_text
        database.save_bot_config(cfg)
        context.user_data.pop('setting_target', None)
        try:
            await msg.reply_text(f"{e('check')} Saved <code>{setting_target}</code>\n\n<b>Preview:</b>\n{html_text}", parse_mode=ParseMode.HTML)
        except Exception:
            await msg.reply_text(f"{e('check')} Saved <code>{setting_target}</code>")
        return


async def flag_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.photo:
        return
    if not await is_user_admin(update.effective_user.id):
        return
    setting_target = context.user_data.get('setting_target')
    if setting_target in ('start_media', 'start1_media'):
        photo = msg.photo[-1]
        cfg = database.load_bot_config()
        cfg[setting_target] = {"type": "photo", "file_id": photo.file_id}
        database.save_bot_config(cfg)
        context.user_data.pop('setting_target', None)
        await msg.reply_text(f"{e('check')} Saved <code>{setting_target}</code>", parse_mode=ParseMode.HTML)


async def flag_video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.video:
        return
    if not await is_user_admin(update.effective_user.id):
        return
    setting_target = context.user_data.get('setting_target')
    if setting_target in ('start_media', 'start1_media'):
        cfg = database.load_bot_config()
        cfg[setting_target] = {"type": "video", "file_id": msg.video.file_id}
        database.save_bot_config(cfg)
        context.user_data.pop('setting_target', None)
        await msg.reply_text(f"{e('check')} Saved <code>{setting_target}</code>", parse_mode=ParseMode.HTML)


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        f"{e('info')} <b>Help</b>\n\n"
        f"{e('key')} /redeem KEY — Activate\n"
        f"{e('info')} /info — Account info\n"
        f"{e('gift')} /trail — Request trial\n"
        f"{e('arrow')} Contact: {config.PURCHASE_USERNAME}"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[make_btn("arrow", "Back", callback_data='back_main', style='primary')]]))


async def redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    database.add_user(user.id, user.username or user.first_name)
    if await check_user_blocked(update):
        return
    if not context.args:
        await update.message.reply_text(f"{e('key')} <b>Redeem Key</b>\n\nUsage: <code>/redeem NEMO-XXXXXXXXXXXX</code>", parse_mode=ParseMode.HTML)
        return
    key_code = context.args[0].strip().upper()
    key_data, error = database.validate_key(key_code)
    if error:
        await update.message.reply_text(f"{e('cross')} <b>{error}</b>", parse_mode=ParseMode.HTML)
        return
    conn = sqlite3.connect(database.DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE active_key = ?", (key_code,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] != user.id:
        await update.message.reply_text(f"{e('cross')} <b>Key already redeemed.</b>", parse_mode=ParseMode.HTML)
        return
    database.set_user_key(user.id, key_code)
    tier_name = TIER_INFO[key_data[1]]["name"]
    await update.message.reply_text(
        f"{e('check')} <b>Key Activated!</b>\n\n{e('star')} <b>Tier:</b> {tier_name}\n"
        f"{e('folder')} <b>Max Pages:</b> {key_data[2]}\n{e('hour')} <b>Expires:</b> {key_data[4][:10]}",
        parse_mode=ParseMode.HTML
    )


async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    database.add_user(user.id, user.username or user.first_name)
    if await check_user_blocked(update):
        return
    key_code = database.get_user_key(user.id)
    if not key_code:
        await update.message.reply_text(f"{e('info')} <b>Account Info</b>\n\n{e('warning')} No active key", parse_mode=ParseMode.HTML)
        return
    key_data, error = database.validate_key(key_code)
    if error:
        await update.message.reply_text(f"{e('info')} <b>Account Info</b>\n\n{e('cross')} {error}", parse_mode=ParseMode.HTML)
        return
    tier = key_data[1]
    tier_name = TIER_INFO[tier]["name"]
    pages = database.get_user_pages(user.id)
    max_pages = TIER_INFO[tier]["max_pages"]
    used_today = database.get_url_change_count_today(user.id)
    limit = TIER_INFO[tier]["url_changes_per_day"]
    remaining = max(0, limit - used_today)
    text = (
        f"{e('info')} <b>Account Info</b>\n"
        f"{e('arrow')} <b>ID:</b> <code>{user.id}</code>\n"
        f"{e('crown')} <b>Tier:</b> {tier_name}\n"
        f"{e('hour')} <b>Expires:</b> {key_data[4][:10]}\n"
        f"{e('rocket')} <b>Pages:</b> {len(pages)}/{max_pages}\n"
        f"{e('bolt')} <b>URL Changes Today:</b> {used_today}/{limit}\n"
        f"{e('check')} <b>Remaining:</b> {remaining}\n"
    )
    if pages:
        text += "\n<b>Your Pages:</b>\n"
        for p in pages[:10]:
            text += f"• <code>{p[1]}</code>\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def trail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    database.add_user(user.id, user.username or user.first_name)
    if await check_user_blocked(update):
        return
    if database.has_used_trial(user.id):
        trial = database.get_trial(user.id)
        status = trial[2] if trial else 'unknown'
        if status == 'approved':
            await update.message.reply_text(f"{e('cross')} <b>You already claimed your trial.</b>", parse_mode=ParseMode.HTML)
        elif status == 'rejected':
            await update.message.reply_text(f"{e('cross')} <b>Trial rejected. One per user.</b>", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"{e('hour')} <b>Your trial is pending.</b>", parse_mode=ParseMode.HTML)
        return
    database.create_trial_request(user.id)
    try:
        admin_text = (
            f"{e('gift')} <b>New Trial Request</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{e('arrow')} <b>User:</b> @{user.username or user.first_name}\n"
            f"{e('arrow')} <b>ID:</b> <code>{user.id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Approve to give a <b>1-day Bronze trial</b>?"
        )
        keyboard = InlineKeyboardMarkup([[
            make_btn("check", "Approve", callback_data=f'trial_yes_{user.id}', style='success'),
            make_btn("cross", "Reject", callback_data=f'trial_no_{user.id}', style='danger')
        ]])
        await context.bot.send_message(chat_id=config.ROOT_ADMIN_ID, text=admin_text,
                                       parse_mode=ParseMode.HTML, reply_markup=keyboard)
        await update.message.reply_text(f"{e('check')} <b>Trial Request Sent</b>", parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text(f"{e('cross')} Failed.", parse_mode=ParseMode.HTML)


async def trial_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await is_user_admin(query.from_user.id):
        await query.answer("Not authorized.", show_alert=True)
        return
    parts = query.data.split('_')
    action = parts[1]
    target_user_id = int(parts[2])
    if action == 'yes':
        key = database.generate_key(tier=1, days=1)
        database.update_trial_status(target_user_id, 'approved', key)
        database.set_user_key(target_user_id, key)
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    f"{e('party')} <b>Trial Approved!</b>\n\n"
                    f"{e('star')} <b>Tier:</b> Bronze\n"
                    f"{e('hour')} <b>Duration:</b> 1 day\n"
                    f"{e('key')} <b>Key:</b> <code>{key}</code>"
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        await query.edit_message_text(
            f"{e('check')} Trial approved for <code>{target_user_id}</code>\n{e('key')} <code>{key}</code>",
            parse_mode=ParseMode.HTML
        )
    else:
        database.update_trial_status(target_user_id, 'rejected')
        try:
            await context.bot.send_message(chat_id=target_user_id, text=f"{e('cross')} <b>Trial Rejected</b>", parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await query.edit_message_text(f"{e('cross')} Rejected for <code>{target_user_id}</code>", parse_mode=ParseMode.HTML)


async def set_start_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update.effective_user.id): return
    context.user_data['setting_target'] = 'start_media'
    await update.message.reply_text(f"{e('mobile')} Send a photo or video.", parse_mode=ParseMode.HTML)


async def set_start1_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update.effective_user.id): return
    context.user_data['setting_target'] = 'start1_media'
    await update.message.reply_text(f"{e('mobile')} Send a photo or video.", parse_mode=ParseMode.HTML)


async def set_start_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update.effective_user.id): return
    context.user_data['setting_target'] = 'start_text'
    await update.message.reply_text(f"{e('pencil')} Send your message.", parse_mode=ParseMode.HTML)


async def set_start1_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update.effective_user.id): return
    context.user_data['setting_target'] = 'start1_text'
    await update.message.reply_text(f"{e('pencil')} Send your message.", parse_mode=ParseMode.HTML)


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'my_pages':
        return await show_my_pages(update, context)
    elif data == 'help':
        return await help_callback(update, context)
    elif data == 'back_main':
        try:
            await query.message.delete()
        except Exception:
            pass
        await send_main_menu(query.from_user.id, context)
        return MAIN_MENU


async def create_page_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    tier = await get_active_tier(user_id)
    if tier is None:
        await query.edit_message_text(f"{e('cross')} No valid key. Use /redeem first.", parse_mode=ParseMode.HTML)
        return MAIN_MENU
    max_pages = TIER_INFO[tier]["max_pages"]
    current = database.get_page_count(user_id)
    if current >= max_pages:
        await query.edit_message_text(f"{e('warning')} Page limit reached ({current}/{max_pages}).", parse_mode=ParseMode.HTML)
        return MAIN_MENU
    context.user_data.pop('page_name', None)
    context.user_data.pop('theme', None)
    try:
        await query.message.delete()
    except Exception:
        pass
    await send_theme_list(user_id, context)
    return WAITING_FOR_THEME


async def pick_theme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    theme_key = query.data.replace('pick_', '')
    context.user_data['preview_theme'] = theme_key
    try:
        await query.message.delete()
    except Exception:
        pass
    await send_theme_preview(query.from_user.id, context, theme_key)
    return WAITING_FOR_THEME


async def use_theme_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    theme_key = query.data.replace('usetheme_', '')
    themes = load_themes()
    if theme_key not in themes:
        await query.answer("Invalid theme", show_alert=True)
        return WAITING_FOR_THEME
    context.user_data['theme'] = theme_key
    theme_name = themes[theme_key].get("name", theme_key)
    try:
        await query.message.delete()
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=(
            f"{e('check')} Theme: <b>{theme_name}</b>\n\n"
            f"{e('rocket')} <b>Step 1 of 2 — Domain Name</b>\n\n"
            f"Send the page name (3-40 chars, lowercase, digits, hyphens):"
        ),
        parse_mode=ParseMode.HTML
    )
    return WAITING_FOR_NAME


async def preview_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass
    await send_theme_list(query.from_user.id, context)
    return WAITING_FOR_THEME


async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    requested = update.message.text.lower().strip()
    if not re.match(r'^[a-z0-9-]{3,40}$', requested):
        await update.message.reply_text(f"{e('cross')} Invalid name. Use lowercase, digits, hyphens (3-40 chars).", parse_mode=ParseMode.HTML)
        return WAITING_FOR_NAME

    status = await update.message.reply_text(f"{e('hour')} Checking name availability...", parse_mode=ParseMode.HTML)

    try:
        actual_name = vercel_deploy._find_available_name(requested)
    except Exception:
        actual_name = requested

    renamed = actual_name != requested
    context.user_data['page_name'] = actual_name

    if renamed:
        await status.edit_text(
            f"{e('warning')} <b><code>{requested}</code> is taken on Vercel</b>\n\n"
            f"{e('check')} Using: <code>{actual_name}</code> instead",
            parse_mode=ParseMode.HTML
        )
    else:
        try:
            await status.delete()
        except Exception:
            pass

    theme_key = context.user_data.get('theme')
    themes = load_themes()
    theme_name = themes.get(theme_key, {}).get("name", theme_key)

    await update.message.reply_text(
        f"{e('check')} Domain: <code>{actual_name}.vercel.app</code>\n"
        f"{e('bolt')} Theme: <b>{theme_name}</b>\n\n"
        f"{e('rocket')} <b>Step 2 of 2 — Upload APK</b>\n\n"
        f"Send the .apk file now.\n\n/cancel to stop.",
        parse_mode=ParseMode.HTML
    )
    return WAITING_FOR_APK


async def receive_apk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text(f"{e('cross')} Send a .apk.", parse_mode=ParseMode.HTML)
        return WAITING_FOR_APK
    doc = update.message.document
    file_name = doc.file_name
    if not file_name.lower().endswith('.apk'):
        await update.message.reply_text(f"{e('cross')} Must be .apk.", parse_mode=ParseMode.HTML)
        return WAITING_FOR_APK

    page_name = context.user_data.get('page_name')
    theme = context.user_data.get('theme')
    key_code = database.get_user_key(update.effective_user.id)
    file_id = doc.file_id

    status_msg = await update.message.reply_text(f"{e('rocket')} Building page...", parse_mode=ParseMode.HTML)

    try:
        download_url = _download_url(page_name)
        result = vercel_deploy.create_vercel_page_with_url(
            page_name=page_name, theme=theme, apk_url=download_url,
            app_display_name=page_name.replace('-', ' ').title()
        )
        vercel_url = result["url"]
        actual_name = result["actual_name"]

        key_data, _ = database.validate_key(key_code)
        expires_at = key_data[4]
        conn = sqlite3.connect(database.DB_NAME)
        cur = conn.cursor()
        cur.execute('''INSERT INTO pages (user_id, key_code, page_name, theme, apk_name, telegram_file_id, vercel_url, created_at, expires_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (update.effective_user.id, key_code, actual_name, theme, file_name, file_id, vercel_url,
             datetime.datetime.now().isoformat(), expires_at))
        conn.commit(); conn.close()

        tier_name = TIER_INFO.get(key_data[1], {}).get("name", "Unknown")
        await notify_admin_new_page(context, update.effective_user, actual_name, theme, file_name, vercel_url, tier_name, expires_at, key_code)
        await status_msg.edit_text(format_deploy_result(result, actual_name, theme), parse_mode=ParseMode.HTML)
        return MAIN_MENU
    except Exception as ex:
        logger.error(f"Deploy error: {ex}")
        await status_msg.edit_text(f"{e('cross')} Failed:\n\n<code>{str(ex)}</code>", parse_mode=ParseMode.HTML)
        return WAITING_FOR_APK


async def show_my_pages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    pages = database.get_user_pages(user_id)
    tier = await get_active_tier(user_id)
    if tier is None:
        await query.edit_message_text(f"{e('cross')} No active key.", parse_mode=ParseMode.HTML)
        return MAIN_MENU
    used = database.get_url_change_count_today(user_id)
    limit = TIER_INFO[tier]["url_changes_per_day"]
    if not pages:
        await query.edit_message_text(
            f"{e('fire')} <b>No Pages Yet</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [make_btn("pencil", "Create Page", callback_data='create_page', style='success')],
                [make_btn("arrow", "Back", callback_data='back_main', style='primary')]
            ])
        )
        return MY_PAGES
    text = f"{e('rocket')} <b>Your Pages</b> — {len(pages)}\n{e('bolt')} Changes Today: {used}/{limit}\n\n"
    keyboard = []
    for p in pages:
        page_id, page_name, theme, url, created, expires = p
        text += f"{e('fire')} <code>{page_name}</code>\n"
        keyboard.append([
            make_btn("bolt", f"URL - {page_name}", callback_data=f'swurl_{page_id}', style='primary'),
            make_btn("package", f"APK - {page_name}", callback_data=f'swapk_{page_id}', style='primary')
        ])
        keyboard.append([
            make_btn("trash", f"Delete - {page_name}", callback_data=f'delpage_{page_id}', style='danger')
        ])
    keyboard.append([make_btn("arrow", "Back", callback_data='back_main', style='primary')])
    if len(text) > 3800:
        text = text[:3800] + "..."
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    return MY_PAGES


async def my_pages_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'back_main':
        try:
            await query.message.delete()
        except Exception:
            pass
        await send_main_menu(query.from_user.id, context)
        return MAIN_MENU
    if data == 'my_pages':
        return await show_my_pages(update, context)
    if data.startswith('delpage_'):
        page_id = int(data.split('_')[1])
        page = database.get_page_by_id(page_id, query.from_user.id)
        if page:
            try: vercel_deploy.delete_vercel_project(page[1])
            except Exception: pass
        ok = database.delete_page(page_id, query.from_user.id)
        msg = f"{e('check')} Page deleted." if ok else f"{e('cross')} Could not delete."
        await query.edit_message_text(msg + "\n\nSend /start to return.", parse_mode=ParseMode.HTML)
        return MAIN_MENU
    if data.startswith('swurl_'):
        page_id = int(data.split('_')[1])
        page = database.get_page_by_id(page_id, query.from_user.id)
        if not page:
            await query.edit_message_text(f"{e('cross')} Not found.", parse_mode=ParseMode.HTML)
            return MY_PAGES
        tier = await get_active_tier(query.from_user.id)
        allowed, remaining = database.can_change_url_today(query.from_user.id, tier)
        if not allowed:
            await query.edit_message_text(f"{e('warning')} Daily limit reached.", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[make_btn("arrow", "Back", callback_data='my_pages', style='primary')]]))
            return MY_PAGES
        context.user_data['edit_page_id'] = page_id
        await query.edit_message_text(f"{e('bolt')} <b>Change URL</b>\nCurrent: <code>{page[3]}</code>\nSend new page name:", parse_mode=ParseMode.HTML)
        return WAITING_FOR_NEW_NAME
    if data.startswith('swapk_'):
        page_id = int(data.split('_')[1])
        page = database.get_page_by_id(page_id, query.from_user.id)
        if not page:
            await query.edit_message_text(f"{e('cross')} Not found.", parse_mode=ParseMode.HTML)
            return MY_PAGES
        context.user_data['edit_page_id'] = page_id
        await query.edit_message_text(f"{e('package')} <b>Change APK</b>\nPage: <code>{page[1]}</code>\n\nSend new .apk.", parse_mode=ParseMode.HTML)
        return WAITING_FOR_NEW_APK_ONLY


async def receive_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    requested = update.message.text.lower().strip()
    if not re.match(r'^[a-z0-9-]{3,40}$', requested):
        await update.message.reply_text(f"{e('cross')} Invalid name.", parse_mode=ParseMode.HTML)
        return WAITING_FOR_NEW_NAME

    status = await update.message.reply_text(f"{e('hour')} Checking name availability...", parse_mode=ParseMode.HTML)
    try:
        actual_name = vercel_deploy._find_available_name(requested)
    except Exception:
        actual_name = requested

    context.user_data['new_page_name'] = actual_name

    if actual_name != requested:
        await status.edit_text(
            f"{e('warning')} <code>{requested}</code> was taken. Using <code>{actual_name}</code>",
            parse_mode=ParseMode.HTML
        )
    else:
        try:
            await status.delete()
        except Exception:
            pass

    await update.message.reply_text(
        f"{e('check')} New name: <code>{actual_name}</code>\n\nSend the APK.",
        parse_mode=ParseMode.HTML
    )
    return WAITING_FOR_NEW_APK


async def receive_new_apk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text(f"{e('cross')} Send a .apk.", parse_mode=ParseMode.HTML)
        return WAITING_FOR_NEW_APK
    doc = update.message.document
    file_name = doc.file_name
    if not file_name.lower().endswith('.apk'):
        await update.message.reply_text(f"{e('cross')} Must be .apk.", parse_mode=ParseMode.HTML)
        return WAITING_FOR_NEW_APK
    user_id = update.effective_user.id
    tier = await get_active_tier(user_id)
    allowed, _ = database.can_change_url_today(user_id, tier)
    if not allowed:
        await update.message.reply_text(f"{e('warning')} Daily limit reached.", parse_mode=ParseMode.HTML)
        return MY_PAGES
    page_id = context.user_data.get('edit_page_id')
    new_name = context.user_data.get('new_page_name')
    old_page = database.get_page_by_id(page_id, user_id)
    if not old_page:
        await update.message.reply_text(f"{e('cross')} Not found.", parse_mode=ParseMode.HTML)
        return MY_PAGES
    old_name = old_page[1]
    theme = old_page[2]
    file_id = doc.file_id

    status = await update.message.reply_text(f"{e('rocket')} Redeploying...", parse_mode=ParseMode.HTML)
    try:
        vercel_deploy.delete_vercel_project(old_name)
        download_url = _download_url(new_name)
        result = vercel_deploy.create_vercel_page_with_url(
            page_name=new_name, theme=theme, apk_url=download_url,
            app_display_name=new_name.replace('-', ' ').title()
        )
        new_url = result["url"]
        actual_name = result["actual_name"]
        database.update_page(page_id, user_id, new_page_name=actual_name, new_vercel_url=new_url, new_theme=theme)
        database.update_page_apk(page_id, user_id, file_name, new_url, file_id)
        database.record_url_change(user_id)
        await status.edit_text(format_deploy_result(result, actual_name, theme), parse_mode=ParseMode.HTML)
        return MAIN_MENU
    except Exception as ex:
        logger.error(f"Rename error: {ex}")
        await status.edit_text(f"{e('cross')} Failed:\n\n<code>{str(ex)}</code>", parse_mode=ParseMode.HTML)
        return WAITING_FOR_NEW_APK


async def receive_new_apk_only(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text(f"{e('cross')} Send a .apk.", parse_mode=ParseMode.HTML)
        return WAITING_FOR_NEW_APK_ONLY
    doc = update.message.document
    file_name = doc.file_name
    if not file_name.lower().endswith('.apk'):
        await update.message.reply_text(f"{e('cross')} Must be .apk.", parse_mode=ParseMode.HTML)
        return WAITING_FOR_NEW_APK_ONLY
    user_id = update.effective_user.id
    page_id = context.user_data.get('edit_page_id')
    page = database.get_page_by_id(page_id, user_id)
    if not page:
        await update.message.reply_text(f"{e('cross')} Not found.", parse_mode=ParseMode.HTML)
        return MY_PAGES
    page_name = page[1]
    theme = page[2]
    file_id = doc.file_id

    status = await update.message.reply_text(f"{e('rocket')} Redeploying...", parse_mode=ParseMode.HTML)
    try:
        vercel_deploy.delete_vercel_project(page_name)
        download_url = _download_url(page_name)
        result = vercel_deploy.create_vercel_page_with_url(
            page_name=page_name, theme=theme, apk_url=download_url,
            app_display_name=page_name.replace('-', ' ').title()
        )
        new_url = result["url"]
        actual_name = result["actual_name"]
        database.update_page_apk(page_id, user_id, file_name, new_url, file_id)
        await status.edit_text(format_deploy_result(result, actual_name, theme), parse_mode=ParseMode.HTML)
        return MAIN_MENU
    except Exception as ex:
        logger.error(f"APK swap error: {ex}")
        await status.edit_message_text(f"{e('cross')} Failed:\n\n<code>{str(ex)}</code>", parse_mode=ParseMode.HTML)
        return WAITING_FOR_NEW_APK_ONLY


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"{e('cross')} Cancelled.", parse_mode=ParseMode.HTML)
    context.user_data.pop('editing_theme', None)
    context.user_data.pop('addtheme_awaiting', None)
    context.user_data.pop('admin_awaiting', None)
    context.user_data.pop('setting_target', None)
    return await start(update, context)


if __name__ == '__main__':
    application = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler('admin', admin_cmd), group=-1)
    application.add_handler(CommandHandler('redeem', redeem_cmd), group=-1)
    application.add_handler(CommandHandler('info', info_cmd), group=-1)
    application.add_handler(CommandHandler('trail', trail_cmd), group=-1)
    application.add_handler(CommandHandler('starttext', set_start_text), group=-1)
    application.add_handler(CommandHandler('startmedia', set_start_media), group=-1)
    application.add_handler(CommandHandler('start1text', set_start1_text), group=-1)
    application.add_handler(CommandHandler('start1media', set_start1_media), group=-1)

    application.add_handler(CallbackQueryHandler(trial_callback, pattern='^trial_(yes|no)_'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_gen_key_callback, pattern='^admin_gen_key$'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_cancel_key_callback, pattern='^admin_cancel_key$'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_block_user_callback, pattern='^admin_block_user$'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_view_pages_callback, pattern='^admin_view_pages$'), group=-1)

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(main_menu_handler, pattern='^(my_pages|help|back_main)$'),
                CallbackQueryHandler(create_page_start, pattern='^create_page$'),
            ],
            WAITING_FOR_THEME: [
                CallbackQueryHandler(pick_theme, pattern='^pick_'),
                CallbackQueryHandler(use_theme_callback, pattern='^usetheme_'),
                CallbackQueryHandler(preview_back_callback, pattern='^preview_back$'),
                CallbackQueryHandler(main_menu_handler, pattern='^back_main$'),
            ],
            WAITING_FOR_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)],
            WAITING_FOR_APK: [MessageHandler(filters.Document.ALL, receive_apk)],
            MY_PAGES: [CallbackQueryHandler(my_pages_handler, pattern='^(back_main|delpage_|swurl_|swapk_|my_pages)')],
            WAITING_FOR_NEW_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_name)],
            WAITING_FOR_NEW_APK: [MessageHandler(filters.Document.ALL, receive_new_apk)],
            WAITING_FOR_NEW_APK_ONLY: [MessageHandler(filters.Document.ALL, receive_new_apk_only)],
        },
        fallbacks=[CommandHandler('cancel', cancel), CommandHandler('start', start)],
    )
    application.add_handler(conv_handler)

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, flag_text_handler))
    application.add_handler(MessageHandler(filters.PHOTO, flag_photo_handler))
    application.add_handler(MessageHandler(filters.VIDEO, flag_video_handler))

    if application.job_queue:
        application.job_queue.run_repeating(expire_pages_job, interval=600, first=60)

    application.run_polling()