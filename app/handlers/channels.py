"""Admin channel management: the /admin menu, general channel, archive mapping.

Buttons edit one menu message; every sensitive action re-checks that the
caller is an administrator inside the handler itself, not just by hiding a
button.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.database import repositories as repo
from app.database.session import session_scope
from app.services.channels import (
    channel_from_message,
    invite_link,
    resolve_channel,
    verify_channel,
)
from app.services.permissions import admin_only, is_admin
from app.utils.formatting import box, esc, footer, kv
from app.utils.validation import parse_channel_ref

logger = logging.getLogger(__name__)

MAIN_ROLE = "main"

# pending action key stored in context.user_data
PENDING_KEY = "channel_action"


def menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📌 General channel", callback_data="ch:main"),
                InlineKeyboardButton("➕ Add archive channel", callback_data="ch:addsel"),
            ],
            [
                InlineKeyboardButton("📋 List channels", callback_data="ch:list"),
                InlineKeyboardButton("🔎 Verify access", callback_data="ch:verify"),
            ],
            [
                InlineKeyboardButton("✉️ Post to general", callback_data="ch:post"),
                InlineKeyboardButton("🧹 Remove a mapping", callback_data="ch:delsel"),
            ],
        ]
    )


def render_menu(main: str | None = None, archive_count: int = 0, note: str | None = None) -> str:
    lines = [
        box("ANIMATRIXX CHANNEL MENU"),
        "",
        f"  📌 General channel: {esc(main) if main else '<i>not set</i>'}",
        f"  📚 Archive channels mapped: {archive_count}",
        "",
        "  Choose an action:",
    ]
    if note:
        lines.append(f"  ✅ {note}")
    lines.append(footer)
    return "\n".join(lines)


async def _menu_state(factory) -> tuple[str | None, int]:
    async with session_scope(factory) as session:
        main = await repo.get_channel_config(session, MAIN_ROLE)
        channels = await repo.list_channels(session)
        mapped = {c.telegram_chat_id for c in channels if c.telegram_chat_id}
        main_title = None
        if main:
            main_title = main.title or (f"@{main.username}" if main.username else str(main.telegram_chat_id))
        return main_title, len(mapped)


@admin_only
async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin - the administrator menu."""
    main, count = await _menu_state(context.bot_data["session_factory"])
    await update.effective_message.reply_text(
        render_menu(main, count), parse_mode=ParseMode.HTML, reply_markup=menu_keyboard()
    )


# ------------------------------------------------------------ general channel
@admin_only
async def cmd_set_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/set_main [channel] - reply to a forwarded message, or pass @name / id."""
    await _start_channel_action(update, context, target="main")


@admin_only
async def cmd_get_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        main = await repo.get_channel_config(session, MAIN_ROLE)
    header = box("GENERAL CHANNEL")
    if main is None:
        await update.effective_message.reply_text(
            f"{header}\n\n  📌 No general channel configured.\n"
            "  Use /set_main or the /admin menu.\n" + footer,
            parse_mode=ParseMode.HTML,
        )
        return
    text = (
        f"{header}\n\n"
        f"  📌 <b>{esc(main.title or main.telegram_chat_id)}</b>\n"
        f"  {kv('Chat id', main.telegram_chat_id)}\n"
        f"  {kv('Username', f'@{main.username}' if main.username else 'private')}\n"
        f"  {kv('Status', main.status)}\n"
        f"  {kv('Invite', main.invite_link or 'use /invite')}\n"
        f"{footer}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@admin_only
async def cmd_remove_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    header = box("REMOVE GENERAL CHANNEL")
    await update.effective_message.reply_text(
        f"{header}\n\n  📌 This only forgets the mapping.\n"
        "  The Telegram channel and its messages stay untouched.\n" + footer,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅ Remove", callback_data="ch:rmmain:1"),
                InlineKeyboardButton("🛑 Cancel", callback_data="ch:menu:0"),
            ]]
        ),
    )


# ----------------------------------------------------------------- listing
@admin_only
async def cmd_listchannels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, keyboard = await _render_channels(context.bot_data["session_factory"])
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


async def _render_channels(factory) -> tuple[str, InlineKeyboardMarkup | None]:
    async with session_scope(factory) as session:
        main = await repo.get_channel_config(session, MAIN_ROLE)
        archives = await repo.list_channels(session)
        series_rows = await repo.list_series(session)
    titles = {s.archive_channel_id: s.title for s in series_rows if s.archive_channel_id}

    lines = [box("ANIMATRIXX CHANNELS"), ""]
    if main:
        icon = {"active": "✅", "denied": "🚫", "missing": "❌"}.get(main.status, "⚠️")
        lines.append(f"  📌 {icon} <b>General:</b> {esc(main.title or main.telegram_chat_id)}")
    else:
        lines.append("  📌 General channel: <i>not set</i>")
    lines.append("")
    if not archives:
        lines.append("  No archive channels yet - use ➕ Add archive channel.")
    for channel in archives:
        icon = {"active": "✅", "denied": "🚫", "missing": "❌"}.get(channel.status, "⚠️")
        series_name = titles.get(channel.id)
        label = esc(channel.title or channel.telegram_chat_id)
        if series_name:
            lines.append(f"  {icon} {label}  →  {esc(series_name)}")
        else:
            lines.append(f"  {icon} {label}  →  <i>unmapped</i>")
    lines += ["", footer]

    buttons = []
    if archives:
        for channel in archives:
            buttons.append(
                [InlineKeyboardButton(
                    f"🧹 {esc(channel.title or channel.telegram_chat_id)}",
                    callback_data=f"ch:del:{channel.telegram_chat_id}",
                )]
            )
    return "\n".join(lines), InlineKeyboardMarkup(buttons) if buttons else None


# --------------------------------------------------------------- add/remove
@admin_only
async def cmd_addchannel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addchannel [series] - map a private channel onto a series."""
    title = " ".join(context.args).strip()
    factory = context.bot_data["session_factory"]
    if title:
        async with session_scope(factory) as session:
            series = await repo.get_series_by_title(session, title)
            if series is None:
                found = await repo.find_series(session, title, limit=1)
                series = found[0] if found else None
            series_id = series.id if series else None
            series_title = series.title if series else None
        if series_id is None:
            await update.effective_message.reply_text(
                f"⛔ No series named <b>{esc(title)}</b>. Create it first with /addseries.",
                parse_mode=ParseMode.HTML,
            )
            return
        await _ask_for_channel(update, f"series:{series_id}", series_title, context=context)
        return
    await _show_series_picker(update=update, context=context)


@admin_only
async def cmd_delchannel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delchannel <@name|chat_id|series title> - forget a mapping (channel stays)."""
    ref = " ".join(context.args).strip()
    factory = context.bot_data["session_factory"]
    if not ref:
        await update.effective_message.reply_text(
            "Usage: <code>/delchannel @name</code> or <code>/delchannel -100…</code>\n"
            "The channel itself is never deleted, only the mapping.",
            parse_mode=ParseMode.HTML,
        )
        return

    parsed = parse_channel_ref(ref)
    chat_id = parsed if isinstance(parsed, int) else None
    async with session_scope(factory) as session:
        channel = None
        if chat_id is not None:
            channel = await repo.get_channel_by_chat(session, chat_id)
        else:
            for candidate in await repo.list_channels(session):
                if candidate.title and candidate.title.lower() == ref.lower():
                    channel = candidate
                    break
                if candidate.username and candidate.username.lower() == ref.lstrip("@").lower():
                    channel = candidate
                    break
        if channel is None:
            await update.effective_message.reply_text(
                f"⛔ I found no channel matching <b>{esc(ref)}</b>. See /listchannels.",
                parse_mode=ParseMode.HTML,
            )
            return
        await session.delete(channel)
        await session.flush()
        name = channel.title or channel.telegram_chat_id

    await update.effective_message.reply_text(
        f"🧹 Mapping for <b>{esc(str(name))}</b> removed. The channel and its "
        "messages are untouched.",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/invite [chat_id] - invite link for the general channel (or one you name)."""
    factory = context.bot_data["session_factory"]
    arg = " ".join(context.args).strip()
    chat_id = None
    if arg:
        parsed = parse_channel_ref(arg)
        chat_id = parsed if isinstance(parsed, int) else None
        if chat_id is None:
            try:
                chat = await resolve_channel(context.bot, arg)
                chat_id = chat.id
            except ValueError as exc:
                await update.effective_message.reply_text(f"⛔ {esc(exc)}")
                return
    else:
        async with session_scope(factory) as session:
            main = await repo.get_channel_config(session, MAIN_ROLE)
        chat_id = main.telegram_chat_id if main else None
        if chat_id is None:
            await update.effective_message.reply_text(
                "No general channel configured. Use /set_main first, or pass a channel.",
                parse_mode=ParseMode.HTML,
            )
            return

    link = await invite_link(context.bot, chat_id)
    header = box("INVITE LINK")
    if not link:
        await update.effective_message.reply_text(
            f"{header}\n\n  ⚠️ Telegram did not give me an invite link for "
            f"<code>{chat_id}</code>. Private channel posts are only visible to "
            "members - share the link Telegram shows in the channel itself.\n" + footer,
            parse_mode=ParseMode.HTML,
        )
        return
    async with session_scope(factory) as session:
        await repo.set_channel_config(session, MAIN_ROLE, chat_id, invite_link=link, single=True)
    await update.effective_message.reply_text(
        f"{header}\n\n  🔗 {esc(link)}\n\n  Only members of a private channel can "
        "open it - do not post it publicly.\n" + footer,
        parse_mode=ParseMode.HTML,
    )


# ------------------------------------------------------------------- posts
@admin_only
async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/post <text> - send an announcement to the general channel."""
    text = " ".join(context.args).strip()
    if not text:
        await _start_channel_action(update, context, target="post", prompt=
            "Send me the announcement text (HTML allowed). Reply with /cancel to abort.")
        return
    await _send_to_general(update, context, text)


async def _send_to_general(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        main = await repo.get_channel_config(session, MAIN_ROLE)
    if main is None:
        await update.effective_message.reply_text(
            "⛔ No general channel configured. Use /set_main first.", parse_mode=ParseMode.HTML
        )
        return
    try:
        message = await context.bot.send_message(
            main.telegram_chat_id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
    except TelegramError as exc:
        logger.warning("Post to general channel failed: %s", exc)
        await update.effective_message.reply_text(
            f"⚠️ Could not post: {esc(str(exc))}", parse_mode=ParseMode.HTML
        )
        return
    await update.effective_message.reply_text(
        f"✅ Posted to <b>{esc(main.title or main.telegram_chat_id)}</b> "
        f"(message {message.id}).",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------- callbacks
async def cb_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user.id, context.bot_data["settings"],
                    context.bot_data.get("db_admin_ids", ())):
        await query.answer("Administration only.", show_alert=True)
        return
    parts = query.data.split(":")            # ch:<action>[:<payload>]
    action = parts[1] if len(parts) > 1 else "menu"
    payload = parts[2] if len(parts) > 2 else ""
    await query.answer()

    if action in {"menu", "0", "main"} and action != "main":
        main, count = await _menu_state(context.bot_data["session_factory"])
        await query.edit_message_text(
            render_menu(main, count), parse_mode=ParseMode.HTML, reply_markup=menu_keyboard()
        )
    elif action == "main":
        await _begin_main(update, context, query)
    elif action == "addsel":
        await _show_series_picker(context=context, query=query, edit=True)
    elif action == "list":
        text, keyboard = await _render_channels(context.bot_data["session_factory"])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    elif action == "verify":
        await _verify_all(update, context, query)
    elif action == "post":
        context.user_data[PENDING_KEY] = "post"
        await query.edit_message_text(
            f"{box('POST TO GENERAL')}\n\n  ✉️ Send the announcement text "
            "(HTML allowed).\n  Reply with /cancel to abort.\n" + footer,
            parse_mode=ParseMode.HTML,
        )
    elif action == "delsel":
        await _show_series_picker(context=context, query=query, edit=True, remove=True)
    elif action == "rmmain":
        await _do_remove_main(update, context, query)
    elif action == "del":
        await _begin_delete(update, context, query, payload)
    elif action == "rmdel":
        await _do_remove_mapping(context, query, payload)
    elif action == "pick":
        try:
            await _begin_series_channel(update, context, query, int(payload))
        except ValueError:
            await query.edit_message_text("Bad series reference.", reply_markup=menu_keyboard())
    else:
        await query.edit_message_text("Unknown action.", reply_markup=menu_keyboard())


async def _begin_main(update, context, query) -> None:
    context.user_data[PENDING_KEY] = "main"
    await query.edit_message_text(
        f"{box('SET GENERAL CHANNEL')}\n\n"
        "  📌 Forward a message from your general channel, or send\n"
        "  <code>@channelname</code> / the numeric chat id.\n\n"
        "  Setup: create the private channel → add me as admin →\n"
        "  forward one of its messages here.\n"
        "  Reply with /cancel to abort.\n" + footer,
        parse_mode=ParseMode.HTML,
    )


async def _show_series_picker(update=None, context=None, query=None, edit: bool = False, remove: bool = False) -> None:
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        rows = await repo.list_series(session)
    if not rows:
        text = (
            f"{box('NO SERIES YET')}\n\n  Create one first with /addseries,\n"
            "  then come back to attach a channel.\n" + footer
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Back", callback_data="ch:menu:0")]])
    else:
        verb = "Remove channel from" if remove else "Attach channel to"
        lines = [box(f"{verb.upper()} A SERIES"), ""]
        keyboard_rows = []
        for row in rows[:30]:
            marker = "📦" if row.archive_channel_id else "⚠️"
            lines.append(f"  {marker} {esc(row.title)}")
            keyboard_rows.append([
                InlineKeyboardButton(
                    f"{marker} {esc(row.title)}",
                    callback_data=f"ch:{'del' if remove else 'pick'}:{row.id}",
                )
            ])
        lines += ["", "  Choose a series:", footer]
        text = "\n".join(lines)
        keyboard_rows.append([InlineKeyboardButton("↩️ Back", callback_data="ch:menu:0")])
        keyboard = InlineKeyboardMarkup(keyboard_rows)

    if edit and query is not None:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    else:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=keyboard
        )


async def _begin_series_channel(update, context, query, series_id: int) -> None:
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        series = await repo.get_series_by_id(session, series_id)
    if series is None:
        await query.edit_message_text("That series no longer exists.", reply_markup=menu_keyboard())
        return
    await _ask_for_channel(update, f"series:{series_id}", series.title, query=query, context=context)


async def _do_remove_mapping(context, query, chat_id: str) -> None:
    factory = context.bot_data["session_factory"]
    try:
        target = int(chat_id)
    except ValueError:
        await query.edit_message_text("Bad channel reference.", reply_markup=menu_keyboard())
        return
    async with session_scope(factory) as session:
        channel = await repo.get_channel_by_chat(session, target)
        name = channel.title if channel else None
        if channel is not None:
            await session.delete(channel)
            await session.flush()
    text, keyboard = await _render_channels(factory)
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
    await query.message.reply_text(
        f"🧹 Mapping for <b>{esc(name or target)}</b> removed. The channel and its "
        "messages are untouched.",
        parse_mode=ParseMode.HTML,
    )


async def _begin_delete(update, context, query, chat_id: str) -> None:
    header = box("REMOVE CHANNEL MAPPING")
    await query.edit_message_text(
        f"{header}\n\n  🧹 Forget chat <code>{esc(chat_id)}</code>?\n"
        "  Only the mapping is removed - never the channel or its files.\n" + footer,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅ Confirm", callback_data=f"ch:rmdel:{chat_id}"),
                InlineKeyboardButton("↩️ Back", callback_data="ch:menu:0"),
            ]]
        ),
    )


async def _do_remove_main(update, context, query) -> None:
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        removed = await repo.delete_channel_config(session, MAIN_ROLE)
    await query.edit_message_text(
        render_menu(None, 0, note="General channel mapping removed." if removed else "Nothing was set."),
        parse_mode=ParseMode.HTML,
        reply_markup=menu_keyboard(),
    )


async def _verify_all(update, context, query) -> None:
    factory = context.bot_data["session_factory"]
    async with session_scope(factory) as session:
        main = await repo.get_channel_config(session, MAIN_ROLE)
        channels = await repo.list_channels(session)

    lines = [box("CHANNEL ACCESS CHECK"), ""]
    if main:
        check = await verify_channel(context.bot, main.telegram_chat_id)
        lines.append(f"  📌 {'✅' if check.ok else '❌'} {esc(main.title or main.telegram_chat_id)}: {esc(check.status)}")
        async with session_scope(factory) as session:
            stored = await repo.get_channel_config(session, MAIN_ROLE)
            if stored:
                stored.status = check.status
    else:
        lines.append("  📌 General channel: <i>not set</i>")
    if not channels:
        lines.append("  📚 No archive channels to check.")
    for channel in channels:
        check = await verify_channel(context.bot, channel.telegram_chat_id)
        lines.append(
            f"  📚 {'✅' if check.ok else '❌'} {esc(channel.title or channel.telegram_chat_id)}: {esc(check.status)}"
        )
        async with session_scope(factory) as session:
            stored = await repo.get_channel_by_id(session, channel.id)
            if stored:
                stored.status = check.status
    lines += ["", "  Denied/missing: re-add me as admin, then verify again.", footer]
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=menu_keyboard())


# -------------------------------------------------- pending message capture
async def _start_channel_action(update, context, *, target: str, prompt: str | None = None) -> None:
    context.user_data[PENDING_KEY] = target
    header = box("ADD CHANNEL" if target == "main" else "CHANNEL ACTION")
    body = prompt or (
        "  Forward a message from the channel, or send <code>@channelname</code>\n"
        "  / the numeric chat id.\n  Reply with /cancel to abort."
    )
    await update.effective_message.reply_text(
        f"{header}\n\n{body}\n" + footer, parse_mode=ParseMode.HTML
    )


async def _ask_for_channel(
    update, target: str, label: str | None = None, query=None, context=None
) -> None:
    if context is not None:
        context.user_data[PENDING_KEY] = target
    if query is not None:
        await query.edit_message_text(
            f"{box('ATTACH ARCHIVE CHANNEL')}\n\n"
            f"  🎬 <b>{esc(label or '')}</b>\n\n"
            "  Now forward a message from the private archive channel, or send\n"
            "  <code>@channelname</code> / the numeric chat id.\n"
            "  Reply with /cancel to abort.\n" + footer,
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.effective_message.reply_text(
            f"{box('ATTACH ARCHIVE CHANNEL')}\n\n"
            f"  🎬 <b>{esc(label or '')}</b>\n\n"
            "  Forward a message from the private archive channel, or send\n"
            "  <code>@channelname</code> / the numeric chat id.\n"
            "  Reply with /cancel to abort.\n" + footer,
            parse_mode=ParseMode.HTML,
        )
    # pending state is set by the caller
    return


def pending_action(context) -> str | None:
    return context.user_data.get(PENDING_KEY)


def clear_pending(context) -> None:
    context.user_data.pop(PENDING_KEY, None)


# ------------------------------------------------- message-based channel flow
async def on_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Capture the next message while an add-channel / set-main / post flow is open.

    Registered after every command and conversation handler, so free text that
    belongs to another flow never reaches here first.
    """
    action = pending_action(context)
    if not action:
        return
    message = update.effective_message
    text = ((message.text or message.caption or "")).strip()

    if action == "post":
        if not text:
            await message.reply_text("Send me the announcement text to post.")
            return
        clear_pending(context)
        await _send_to_general(update, context, text)
        return

    chat = channel_from_message(message)
    ref = chat.id if chat is not None else parse_channel_ref(text)
    if ref is None:
        await message.reply_text(
            "I need a channel: <b>forward a message from it</b>, or send "
            "<code>@channelname</code> / the numeric id (<code>-100…</code>).\n"
            "Reply with /cancel to abort.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        resolved = await resolve_channel(context.bot, ref)
    except ValueError as exc:
        await message.reply_text(f"⛔ {esc(exc)}", parse_mode=ParseMode.HTML)
        return

    check = await verify_channel(context.bot, resolved)
    if not check.ok:
        # keep the flow open so the admin can fix permissions and retry
        await message.reply_text(f"⛔ {esc(check.reason)}", parse_mode=ParseMode.HTML)
        return

    factory = context.bot_data["session_factory"]
    if action == "main":
        async with session_scope(factory) as session:
            await repo.set_channel_config(
                session, MAIN_ROLE, check.chat_id,
                title=check.title, username=check.username, single=True,
            )
        clear_pending(context)
        main, count = await _menu_state(factory)
        await message.reply_text(
            render_menu(main, count, note=f"General channel set to {check.display}."),
            parse_mode=ParseMode.HTML,
            reply_markup=menu_keyboard(),
        )
        return

    if action.startswith("series:"):
        try:
            series_id = int(action.split(":", 1)[1])
        except ValueError:
            clear_pending(context)
            await message.reply_text("Broken series reference - start again with /addchannel.")
            return
        async with session_scope(factory) as session:
            series = await repo.get_series_by_id(session, series_id)
            if series is None:
                clear_pending(context)
                await message.reply_text("That series no longer exists.")
                return
            channel = await repo.get_or_create_channel(
                session, check.chat_id, title=check.title, username=check.username
            )
            await repo.attach_archive_channel(session, series.title, channel)
            series_title = series.title
        clear_pending(context)
        await message.reply_text(
            f"{box('CHANNEL ATTACHED')}\n\n"
            f"  🎬 {esc(series_title)}\n"
            f"  📦 Archive: {esc(check.display)}  ✅ verified\n"
            f"  Use /verifyarchive or the /admin menu to re-check later.\n" + footer,
            parse_mode=ParseMode.HTML,
        )
        return

    clear_pending(context)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Abort whichever flow is open: a channel action, or a job /cancel."""
    if pending_action(context):
        clear_pending(context)
        await update.effective_message.reply_text("Cancelled - no changes made.")
        return
    from app.handlers import media  # imported late: media imports nothing from here

    await media.cmd_cancel(update, context)


__all__ = [
    "menu_keyboard",
    "render_menu",
    "cmd_menu",
    "cmd_set_main",
    "cmd_get_main",
    "cmd_remove_main",
    "cmd_addchannel",
    "cmd_delchannel",
    "cmd_listchannels",
    "cmd_invite",
    "cmd_post",
    "cb_menu",
    "pending_action",
    "clear_pending",
    "on_channel_message",
    "cmd_cancel",
    "PENDING_KEY",
    "MAIN_ROLE",
]