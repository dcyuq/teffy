import asyncio
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

import embeds
import emojiutils
import templating
from prefixes import display_prefix
from storage import Store

log = logging.getLogger(__name__)

FIELD_LIMIT = 200
NOTES_LIMIT = 500
FORMAT_LIMIT = 2000
LABEL_LIMIT = 80
MAX_QUESTIONS = 10
MODAL_LIMIT = 5
FORM_TIMEOUT = 300
CANCEL_WORDS = {"cancel", "stop", "nvm"}

DEFAULT_CONFIRM_FORMAT = (
    "**order confirmation**\n"
    "\n"
    "order : {item}\n"
    "amount : {price}\n"
    "quantity : {quantity}\n"
    "details/notes : {notes}"
)
DEFAULT_FOOTER = "kindly make sure all information is complete and correct before proceeding."
DEFAULT_CONFIRM_BUTTON = "yes, proceed"
DEFAULT_RECEIVED = (
    "**confirmation received**\n"
    "\n"
    "kindly choose your payment option and remain patient for the owner to respond."
)
DEFAULT_TERMS_FORMAT = (
    "**terms and conditions**\n"
    "\n"
    "please review the terms and conditions below before proceeding.\n"
    "\n"
    "by continuing, you agree to the terms and conditions for this order."
)
DEFAULT_TERMS_BUTTON = "yes, i agree"

DEFAULT_PAYMENT_METHODS = [
    {
        "id": "gcash",
        "label": "gcash",
        "text": (
            "**gcash info**\n"
            "\n"
            "no. 09xx xxx xxxx\n"
            "initials : x.x\n"
            "\n"
            "> make sure you've read the tos\n"
            "> always send a receipt\n"
            "> no receipt = no transaction"
        ),
    }
]

DEFAULT_GCASH_BUTTON = "gcash"
DEFAULT_GCASH_TEXT = (
    "**gcash info**\n"
    "\n"
    "no. 09xx xxx xxxx\n"
    "initials : x.x\n"
    "\n"
    "> make sure you've read the tos\n"
    "> always send a receipt\n"
    "> no receipt = no transaction"
)

DEFAULT_PING = "one last check, :c_heart: {user}"

DEFAULT_QUESTIONS = [
    {"field": "item", "label": "name of the order", "kind": "text",
     "hint": "what are you ordering?"},
    {"field": "quantity", "label": "quantity of the order", "kind": "number",
     "hint": "numbers only"},
    {"field": "price", "label": "total price", "kind": "number",
     "hint": "numbers only"},
    {"field": "notes", "label": "gamepass link", "kind": "text",
     "hint": "type no gp if you don't have one"},
]

DEFAULT_FORM_BUTTON = "order form"
DEFAULT_FORM_TEXT = "click below to fill in your order."

SAMPLE_ORDER = {
    "item": "pinned post",
    "price": "₱250.00",
    "quantity": "1",
    "notes": "rushed",
}

FIELDS = ("item", "price", "quantity", "notes", "user")

ALIASES = {
    "item": "item", "order": "item", "product": "item",
    "price": "price", "amount": "price", "cost": "price", "total": "price",
    "quantity": "quantity", "qty": "quantity",
    "notes": "notes", "note": "notes", "details": "notes", "extra": "notes",
    "details/notes": "notes",
    "user": "user", "customer": "user", "buyer": "user",
}

IMAGE_URL = re.compile(
    r"(?<![(\[<])\bhttps?://[^\s<>()\[\]]+?"
    r"\.(?:png|jpe?g|gif|webp|avif)"
    r"(?:\?[^\s<>()\[\]]*)?",
    re.IGNORECASE,
)

CUSTOM_EMOJI = re.compile(r"^(<a?:[A-Za-z0-9_]{2,32}:\d{15,25}>)\s*(.*)$", re.DOTALL)
SHORTCODE = re.compile(r"^:([A-Za-z0-9_~]{2,32}):\s*(.*)$", re.DOTALL)
NUMBERISH = re.compile(r"^[^\d\-]{0,3}\s*-?\d+(?:[.,]\d+)?\s*$")
FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,19}$")

_store = Store("confirmation_config.json")
config = _store.load()


def save_config():
    _store.save(config)


def defaults():
    return {
        "ping": DEFAULT_PING,
        "confirm_format": DEFAULT_CONFIRM_FORMAT,
        "footer": DEFAULT_FOOTER,
        "confirm_button": DEFAULT_CONFIRM_BUTTON,
        "received_format": DEFAULT_RECEIVED,
        "terms_format": DEFAULT_TERMS_FORMAT,
        "terms_button": DEFAULT_TERMS_BUTTON,
        "payment_methods": [dict(method) for method in DEFAULT_PAYMENT_METHODS],
        "gcash_button": DEFAULT_GCASH_BUTTON,
        "gcash_text": DEFAULT_GCASH_TEXT,
        "questions": [dict(question) for question in DEFAULT_QUESTIONS],
        "form_mode": "chat",
        "form_button": DEFAULT_FORM_BUTTON,
        "form_text": DEFAULT_FORM_TEXT,
    }


def ensure_config(guild_id):
    key = str(guild_id)
    if key not in config:
        config[key] = defaults()
    settings = config[key]
    for field, value in defaults().items():
        settings.setdefault(field, value)

    if not settings.get("payment_methods"):
        settings["payment_methods"] = [{
            "id": "gcash",
            "label": settings.get("gcash_button") or DEFAULT_GCASH_BUTTON,
            "text": settings.get("gcash_text") or DEFAULT_GCASH_TEXT,
        }]

    if not settings.get("questions"):
        settings["questions"] = [dict(question) for question in DEFAULT_QUESTIONS]

    return settings


def settings_for(guild_id):
    settings = config.get(str(guild_id))
    if not settings:
        return defaults()

    for field, value in defaults().items():
        settings.setdefault(field, value)

    if not settings.get("payment_methods"):
        settings["payment_methods"] = [{
            "id": "gcash",
            "label": settings.get("gcash_button") or DEFAULT_GCASH_BUTTON,
            "text": settings.get("gcash_text") or DEFAULT_GCASH_TEXT,
        }]

    if not settings.get("questions"):
        settings["questions"] = [dict(question) for question in DEFAULT_QUESTIONS]

    return settings


def questions_of(settings):
    return settings.get("questions") or DEFAULT_QUESTIONS


def find_question(settings, field):
    for entry in questions_of(settings):
        if entry["field"] == field:
            return entry
    return None


def is_number(text):
    return bool(NUMBERISH.match((text or "").replace(",", "")))


def order_values(order, author_id):
    values = {key: value for key, value in order.items() if key != "user"}
    values.setdefault("item", "")
    values.setdefault("price", "")
    values.setdefault("quantity", "")
    values["notes"] = order.get("notes") or "none"
    values["user"] = f"<@{author_id}>" if author_id else ""
    return values


def aliases_for(order):
    aliases = dict(ALIASES)
    for key in order:
        aliases.setdefault(key, key)
    return aliases


def render(template, order, author_id, guild):
    return templating.render(
        template, order_values(order, author_id), aliases_for(order), guild
    )


def placeholder_list(settings):
    lines = [
        f"`{{{q['field']}}}` - {q['label']}" for q in questions_of(settings)
    ]
    lines.append("`{user}` - pings whoever filled the form")
    return "\n".join(lines)


def split_image(body):
    matches = list(IMAGE_URL.finditer(body))
    if not matches:
        return body, None
    last = matches[-1]
    trimmed = body[: last.start()] + body[last.end():]
    return trimmed.strip(), last.group(0)


def split_button_label(raw, guild, fallback):
    text = (raw or "").strip()
    if not text:
        return None, fallback[:LABEL_LIMIT]

    match = CUSTOM_EMOJI.match(text)
    if match:
        try:
            emoji = discord.PartialEmoji.from_str(match.group(1))
        except (ValueError, TypeError):
            emoji = None
        return emoji, (match.group(2).strip() or fallback)[:LABEL_LIMIT]

    match = SHORTCODE.match(text)
    if match:
        found = emojiutils.find_named(guild, match.group(1)) if guild else None
        return (found if found else None), (match.group(2).strip() or fallback)[:LABEL_LIMIT]

    head, _, rest = text.partition(" ")
    if emojiutils.valid_shape(head) and not head.startswith(("<", ":")):
        return head, (rest.strip() or fallback)[:LABEL_LIMIT]

    return None, text[:LABEL_LIMIT]


def apply_label(button, raw, guild, fallback):
    emoji, label = split_button_label(raw, guild, fallback)
    button.label = label
    if emoji is not None:
        button.emoji = emoji


class ConfirmRow(discord.ui.ActionRow):
    def __init__(self, parent, raw, guild):
        super().__init__()
        self.owner = parent
        apply_label(self.go, raw, guild, "confirm order")

    @discord.ui.button(style=discord.ButtonStyle.secondary)
    async def go(self, interaction, button):
        await self.owner.confirm(interaction)


class ConfirmView(discord.ui.LayoutView):
    def __init__(self, settings, order, author_id, guild):
        super().__init__(timeout=600)
        self.settings = settings
        self.order = order
        self.author_id = author_id
        self.guild = guild
        self.build()

    def build(self):
        self.clear_items()
        ping = render(self.settings.get("ping") or "", self.order, self.author_id, self.guild).strip()
        if ping:
            self.add_item(discord.ui.TextDisplay(ping[:2000]))

        box = discord.ui.Container()
        box.add_item(discord.ui.TextDisplay(
            render(self.settings["confirm_format"], self.order, self.author_id, self.guild)[:4000]
        ))
        footer = (self.settings.get("footer") or "").strip()
        if footer:
            box.add_item(discord.ui.TextDisplay(footer[:1000]))
        box.add_item(discord.ui.Separator())
        box.add_item(ConfirmRow(self, self.settings.get("confirm_button"), self.guild))
        self.add_item(box)

    async def confirm(self, interaction):
        settings = self.settings
        await interaction.response.send_message(
            view=TermsView(
                settings=settings,
                order=self.order,
                author_id=self.author_id,
                guild=interaction.guild,
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )


class TermsRow(discord.ui.ActionRow):
    def __init__(self, parent, raw, guild):
        super().__init__()
        self.owner = parent
        apply_label(self.agree, raw, guild, "yes, i agree")

    @discord.ui.button(style=discord.ButtonStyle.secondary)
    async def agree(self, interaction, button):
        await self.owner.agree(interaction)


class TermsView(discord.ui.LayoutView):
    def __init__(self, settings, order, author_id, guild):
        super().__init__(timeout=600)
        self.settings = settings
        self.order = order
        self.author_id = author_id
        self.guild = guild
        self.build()

    def build(self):
        self.clear_items()
        ping = render(self.settings.get("ping") or "", self.order, self.author_id, self.guild).strip()
        if ping:
            self.add_item(discord.ui.TextDisplay(ping[:2000]))

        box = discord.ui.Container()
        body = render(
            self.settings.get("terms_format") or DEFAULT_TERMS_FORMAT,
            self.order,
            self.author_id,
            self.guild,
        )
        box.add_item(discord.ui.TextDisplay(body[:4000]))
        box.add_item(discord.ui.Separator())
        box.add_item(TermsRow(self, self.settings.get("terms_button"), self.guild))
        self.add_item(box)

    async def agree(self, interaction):
        view = PaymentView(self.settings, self.order, self.author_id, interaction.guild)
        await interaction.response.send_message(
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class PaymentMethodRow(discord.ui.ActionRow):
    def __init__(self, parent, methods, guild):
        super().__init__()
        self.owner = parent
        self.methods = methods

        for method in methods:
            button = discord.ui.Button(style=discord.ButtonStyle.secondary)
            apply_label(
                button,
                method.get("label"),
                guild,
                method.get("id", "payment"),
            )
            button.callback = self._make_callback(method)
            self.add_item(button)

    def _make_callback(self, method):
        async def callback(interaction):
            await self.owner.pay(interaction, method)
        return callback


class PaymentView(discord.ui.LayoutView):
    def __init__(self, settings, order, author_id, guild):
        super().__init__(timeout=600)
        self.settings = settings
        self.order = order
        self.author_id = author_id
        self.guild = guild
        self.build()

    def build(self):
        self.clear_items()

        box = discord.ui.Container()
        box.add_item(discord.ui.TextDisplay(
            render(
                self.settings.get("received_format") or DEFAULT_RECEIVED,
                self.order,
                self.author_id,
                self.guild,
            )[:4000]
        ))
        box.add_item(discord.ui.Separator())

        methods = self.settings.get("payment_methods") or []
        if not methods:
            box.add_item(discord.ui.TextDisplay("no payment methods have been configured."))
        else:
            for index in range(0, len(methods), 5):
                box.add_item(PaymentMethodRow(self, methods[index:index + 5], self.guild))

        self.add_item(box)

    async def pay(self, interaction, method):
        view = PaymentMethodView(
            self.settings,
            self.order,
            self.author_id,
            interaction.guild,
            method,
        )
        await interaction.response.send_message(
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class PaymentMethodView(discord.ui.LayoutView):
    def __init__(self, settings, order, author_id, guild, method):
        super().__init__(timeout=None)
        self.settings = settings
        self.order = order
        self.author_id = author_id
        self.guild = guild
        self.method = method
        self.build()

    def build(self):
        box = discord.ui.Container()
        body = render(
            self.method.get("text") or "",
            self.order,
            self.author_id,
            self.guild,
        )
        body, image_url = split_image(body)

        if body.strip():
            box.add_item(discord.ui.TextDisplay(body[:4000]))
        if image_url:
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media=image_url)
            box.add_item(gallery)

        self.add_item(box)


active = set()


async def purge(messages):
    for message in messages:
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass


async def ask_questions(client, channel, user, settings):
    trash = []
    order = {}
    entries = questions_of(settings)

    def mine(message):
        return message.author.id == user.id and message.channel.id == channel.id

    for index, entry in enumerate(entries, start=1):
        prompt = f"**question {index} of {len(entries)}**\n{entry['label']}"
        hint = (entry.get("hint") or "").strip()
        if hint:
            prompt += f"\n-# {hint}"

        trash.append(await channel.send(embed=embeds.build(prompt)))

        while True:
            try:
                reply = await client.wait_for("message", timeout=FORM_TIMEOUT, check=mine)
            except asyncio.TimeoutError:
                await purge(trash)
                await channel.send(
                    embed=embeds.error("order form closed, you took too long."),
                    delete_after=20,
                )
                return None

            trash.append(reply)
            text = reply.content.strip()

            if text.lower() in CANCEL_WORDS:
                await purge(trash)
                await channel.send(
                    embed=embeds.notice("order form cancelled."), delete_after=20
                )
                return None

            if entry.get("kind") == "number" and not is_number(text):
                trash.append(await channel.send(
                    embed=embeds.error(
                        f"`{entry['label']}` only takes numbers. send just the "
                        "amount, like `2` or `150`."
                    )
                ))
                continue

            order[entry["field"]] = text[:FIELD_LIMIT]
            break

    await purge(trash)
    return order


async def post_confirmation(channel, user, settings, order, guild):
    await channel.send(
        view=ConfirmView(settings, order, user.id, guild),
        allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
    )


def modal_ready(settings):
    return (
        settings.get("form_mode") == "modal"
        and 0 < len(questions_of(settings)) <= MODAL_LIMIT
    )


class OrderModal(discord.ui.Modal, title="Order Form"):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.fields = []

        for entry in questions_of(settings)[:MODAL_LIMIT]:
            field = discord.ui.TextInput(
                label=entry["label"][:45],
                placeholder=(entry.get("hint") or "")[:100] or None,
                style=discord.TextStyle.short
                if entry.get("kind") == "number"
                else discord.TextStyle.paragraph,
                max_length=FIELD_LIMIT,
                required=True,
            )
            self.fields.append((entry, field))
            self.add_item(field)

    async def on_submit(self, interaction):
        bad = [
            entry["label"]
            for entry, field in self.fields
            if entry.get("kind") == "number" and not is_number(field.value)
        ]
        if bad:
            listed = ", ".join(f"`{item}`" for item in bad)
            await interaction.response.send_message(
                embed=embeds.error(
                    f"{listed} only takes numbers. open the form again and "
                    "send just the amount, like `2` or `150`."
                ),
                ephemeral=True,
            )
            return

        order = {
            entry["field"]: field.value.strip()[:FIELD_LIMIT]
            for entry, field in self.fields
        }
        await interaction.response.defer()
        await post_confirmation(
            interaction.channel, interaction.user, self.settings, order, interaction.guild
        )


class FormRow(discord.ui.ActionRow):
    def __init__(self, raw, guild):
        super().__init__()
        apply_label(self.open_form, raw, guild, DEFAULT_FORM_BUTTON)

    @discord.ui.button(
        style=discord.ButtonStyle.secondary, custom_id="confirmation:form"
    )
    async def open_form(self, interaction, button):
        settings = settings_for(interaction.guild.id)
        channel = interaction.channel
        user = interaction.user
        ticket = (channel.id, user.id)

        if not questions_of(settings):
            await interaction.response.send_message(
                embed=embeds.error("no questions are set up yet."), ephemeral=True
            )
            return

        if ticket in active:
            await interaction.response.send_message(
                embed=embeds.error("you already have a form open here."), ephemeral=True
            )
            return

        if modal_ready(settings):
            await interaction.response.send_modal(OrderModal(settings))
            return

        await interaction.response.send_message(
            embed=embeds.notice(
                "answer each question below. type `cancel` to stop."
            ),
            ephemeral=True,
        )

        active.add(ticket)
        try:
            order = await ask_questions(interaction.client, channel, user, settings)
        finally:
            active.discard(ticket)

        if order is None:
            return

        await post_confirmation(channel, user, settings, order, interaction.guild)


class FormPanel(discord.ui.LayoutView):
    def __init__(self, settings, guild=None, body=None):
        super().__init__(timeout=None)
        text = body if body is not None else (settings.get("form_text") or "")
        if text.strip():
            box = discord.ui.Container()
            box.add_item(discord.ui.TextDisplay(text[:4000]))
            box.add_item(discord.ui.Separator())
            box.add_item(FormRow(settings.get("form_button"), guild))
            self.add_item(box)
        else:
            self.add_item(FormRow(settings.get("form_button"), guild))


class QuestionModal(discord.ui.Modal):
    def __init__(self, panel, existing=None):
        super().__init__(title="edit question" if existing else "add question")
        self.panel = panel
        self.existing = existing
        base = existing or {}

        self.f_label = discord.ui.TextInput(
            label="question",
            default=base.get("label", ""),
            placeholder="name of the order",
            max_length=200,
            required=True,
        )
        self.f_kind = discord.ui.TextInput(
            label="answer type",
            default=base.get("kind", "text"),
            placeholder="text or number",
            max_length=10,
            required=True,
        )
        self.f_hint = discord.ui.TextInput(
            label="small note under the question",
            default=base.get("hint") or "",
            placeholder="optional",
            max_length=150,
            required=False,
        )
        self.add_item(self.f_label)
        self.add_item(self.f_kind)
        self.add_item(self.f_hint)

        if existing is None:
            self.f_field = discord.ui.TextInput(
                label="placeholder name",
                placeholder="gamepass, becomes {gamepass}",
                max_length=20,
                required=True,
            )
            self.add_item(self.f_field)
        else:
            self.f_field = None

    async def on_submit(self, interaction):
        kind = self.f_kind.value.strip().lower()
        if kind not in ("text", "number"):
            await interaction.response.send_message(
                embed=embeds.error("answer type has to be `text` or `number`."),
                ephemeral=True,
            )
            return

        settings = self.panel.settings

        if self.existing is None:
            field = self.f_field.value.strip().lower().replace(" ", "_")
            if not FIELD_NAME.match(field):
                await interaction.response.send_message(
                    embed=embeds.error(
                        "placeholder name has to be lowercase letters, "
                        "numbers or underscores, starting with a letter."
                    ),
                    ephemeral=True,
                )
                return
            if find_question(settings, field) is not None:
                await interaction.response.send_message(
                    embed=embeds.error(f"`{{{field}}}` is already taken."),
                    ephemeral=True,
                )
                return

            settings["questions"].append({
                "field": field,
                "label": self.f_label.value.strip(),
                "kind": kind,
                "hint": self.f_hint.value.strip(),
            })
        else:
            self.existing["label"] = self.f_label.value.strip()
            self.existing["kind"] = kind
            self.existing["hint"] = self.f_hint.value.strip()

        save_config()
        await interaction.response.defer()
        await self.panel.refresh()


class QuestionSelect(discord.ui.Select):
    def __init__(self, panel):
        self.panel = panel
        entries = questions_of(panel.settings)
        options = [
            discord.SelectOption(
                label=entry["label"][:100],
                value=entry["field"],
                description=f"{{{entry['field']}}} · {entry.get('kind', 'text')}"[:100],
            )
            for entry in entries
        ]
        super().__init__(
            placeholder="choose a question to customize",
            options=options or [discord.SelectOption(label="no questions yet", value="none")],
            disabled=not entries,
        )

    async def callback(self, interaction):
        entry = find_question(self.panel.settings, self.values[0])
        if entry is None:
            await interaction.response.send_message(
                embed=embeds.error("that question no longer exists."), ephemeral=True
            )
            return

        view = QuestionManageView(self.panel, entry)
        await interaction.response.edit_message(embed=view.status_embed(), view=view)


class QuestionManageView(discord.ui.View):
    def __init__(self, panel, entry):
        super().__init__(timeout=300)
        self.panel = panel
        self.entry = entry

    async def interaction_check(self, interaction):
        return interaction.user.id == self.panel.ctx.author.id

    def status_embed(self):
        return embeds.build(
            f"**{self.entry['label']}**\n\n"
            f"placeholder : `{{{self.entry['field']}}}`\n"
            f"answer type : {self.entry.get('kind', 'text')}\n"
            f"note : {self.entry.get('hint') or 'none'}"
        )

    @discord.ui.button(label="edit", style=discord.ButtonStyle.secondary, row=0)
    async def edit(self, interaction, button):
        await interaction.response.send_modal(QuestionModal(self.panel, self.entry))

    @discord.ui.button(label="move up", style=discord.ButtonStyle.secondary, row=0)
    async def move_up(self, interaction, button):
        entries = self.panel.settings["questions"]
        index = entries.index(self.entry)
        if index > 0:
            entries[index - 1], entries[index] = entries[index], entries[index - 1]
            save_config()
        await interaction.response.edit_message(embed=self.status_embed(), view=self)
        await self.panel.refresh()

    @discord.ui.button(label="remove", style=discord.ButtonStyle.danger, row=1)
    async def remove(self, interaction, button):
        entries = self.panel.settings["questions"]
        if len(entries) <= 1:
            await interaction.response.send_message(
                embed=embeds.error("you need at least one question."), ephemeral=True
            )
            return

        entries.remove(self.entry)
        save_config()
        await interaction.response.edit_message(
            embed=embeds.notice(f"removed **{self.entry['label']}**."), view=None
        )
        await self.panel.refresh()
        self.stop()


class QuestionsView(discord.ui.View):
    def __init__(self, panel):
        super().__init__(timeout=300)
        self.panel = panel
        self.add_item(QuestionSelect(panel))

    async def interaction_check(self, interaction):
        return interaction.user.id == self.panel.ctx.author.id

    def status_embed(self):
        settings = self.panel.settings
        lines = [
            f"{index}. **{entry['label']}** · `{{{entry['field']}}}` · "
            f"{entry.get('kind', 'text')}"
            for index, entry in enumerate(questions_of(settings), start=1)
        ]
        asked = "a modal" if modal_ready(settings) else "one question at a time"
        return embeds.build(
            "**order form questions**\n\n"
            f"asked in {asked}.\n\n" + "\n".join(lines)
        )

    @discord.ui.button(label="add question", style=discord.ButtonStyle.secondary, row=1)
    async def add(self, interaction, button):
        if len(questions_of(self.panel.settings)) >= MAX_QUESTIONS:
            await interaction.response.send_message(
                embed=embeds.error(f"you already have {MAX_QUESTIONS} questions."),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(QuestionModal(self.panel))

    @discord.ui.button(label="modal / one by one", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_mode(self, interaction, button):
        settings = self.panel.settings
        settings["form_mode"] = (
            "chat" if settings.get("form_mode") == "modal" else "modal"
        )
        save_config()

        if settings["form_mode"] == "modal" and len(questions_of(settings)) > MODAL_LIMIT:
            await interaction.response.send_message(
                embed=embeds.error(
                    f"a modal only fits {MODAL_LIMIT} questions, so the form "
                    "keeps asking one by one until you remove some."
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.edit_message(embed=self.status_embed(), view=self)
        await self.panel.refresh()

    @discord.ui.button(label="reset to defaults", style=discord.ButtonStyle.secondary, row=1)
    async def reset(self, interaction, button):
        self.panel.settings["questions"] = [dict(q) for q in DEFAULT_QUESTIONS]
        save_config()
        await interaction.response.edit_message(embed=self.status_embed(), view=self)
        await self.panel.refresh()


class FieldModal(discord.ui.Modal):
    def __init__(self, panel, field, label, multiline=False, required=True, limit=FORMAT_LIMIT):
        super().__init__(title=label[:45])
        self.panel = panel
        self.field = field
        self.required = required
        self.input = discord.ui.TextInput(
            label=label[:45],
            style=discord.TextStyle.paragraph if multiline else discord.TextStyle.short,
            default=(panel.settings.get(field) or "")[:limit],
            max_length=limit,
            required=required,
        )
        self.add_item(self.input)

    async def on_submit(self, interaction):
        value = self.input.value.strip()
        if not value and self.required:
            await interaction.response.send_message(
                embed=embeds.error("that cannot be empty."), ephemeral=True
            )
            return
        self.panel.settings[self.field] = value
        save_config()
        await interaction.response.defer()
        await self.panel.refresh()


class PaymentMethodModal(discord.ui.Modal):
    def __init__(self, panel, existing=None):
        self.panel = panel
        self.existing = existing

        super().__init__(
            title="edit payment method" if existing else "add payment method"
        )

        self.name_input = discord.ui.TextInput(
            label="payment method",
            default=(existing or {}).get("label", "")[:LABEL_LIMIT],
            placeholder="e.g. gcash, maya, bank transfer",
            max_length=LABEL_LIMIT,
            required=True,
        )

        self.display_input = discord.ui.TextInput(
            label="what should be displayed",
            default=(existing or {}).get("text", "")[:4000],
            placeholder="Enter the payment information/layout to display.",
            style=discord.TextStyle.paragraph,
            max_length=4000,
            required=True,
        )

        self.add_item(self.name_input)
        self.add_item(self.display_input)

    async def on_submit(self, interaction):
        label = self.name_input.value.strip()
        text = self.display_input.value.strip()

        if not label or not text:
            await interaction.response.send_message(
                embed=embeds.error("both fields are required."),
                ephemeral=True,
            )
            return

        methods = self.panel.settings.get("payment_methods") or []

        if self.existing is None:
            if len(methods) >= 25:
                await interaction.response.send_message(
                    embed=embeds.error("you already have 25 payment methods."),
                    ephemeral=True,
                )
                return

            method_id = (
                re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
                or f"payment-{len(methods) + 1}"
            )

            existing_ids = {method.get("id") for method in methods}
            base_id = method_id
            counter = 2
            while method_id in existing_ids:
                method_id = f"{base_id}-{counter}"
                counter += 1

            methods.append({
                "id": method_id,
                "label": label[:LABEL_LIMIT],
                "text": text[:4000],
            })
        else:
            self.existing["label"] = label[:LABEL_LIMIT]
            self.existing["text"] = text[:4000]

        self.panel.settings["payment_methods"] = methods
        save_config()

        await interaction.response.edit_message(
            embed=self.panel.status_embed(),
            view=PaymentMethodsView(self.panel),
        )


class PaymentMethodSelect(discord.ui.Select):
    def __init__(self, panel):
        self.panel = panel
        methods = panel.settings.get("payment_methods") or []

        options = [
            discord.SelectOption(
                label=method.get("label", "payment")[:100],
                value=method.get("id", str(index)),
                description="click to customize this payment method"[:100],
            )
            for index, method in enumerate(methods)
        ]

        if not options:
            options = [
                discord.SelectOption(
                    label="no payment methods yet",
                    value="none",
                    description="use Add Payment Method below",
                )
            ]

        super().__init__(
            placeholder="choose a payment method to customize",
            options=options[:25],
            disabled=not methods,
        )

    async def callback(self, interaction):
        method_id = self.values[0]
        method = next(
            (
                method
                for method in (self.panel.settings.get("payment_methods") or [])
                if method.get("id") == method_id
            ),
            None,
        )

        if method is None:
            await interaction.response.send_message(
                embed=embeds.error("that payment method no longer exists."),
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            PaymentMethodModal(self.panel, method)
        )


class PaymentMethodsView(discord.ui.View):
    def __init__(self, panel):
        super().__init__(timeout=300)
        self.panel = panel
        self.add_item(PaymentMethodSelect(panel))

    def status_embed(self):
        methods = self.panel.settings.get("payment_methods") or []

        if methods:
            lines = [
                f"**{method.get('label', 'payment')}**"
                for method in methods
            ]
            description = (
                "select a payment method below to customize it.\n\n"
                + "\n".join(lines)
            )
        else:
            description = (
                "no payment methods have been added yet.\n\n"
                "click **add payment method** to create one."
            )

        return embeds.build(
            f"**payment methods**\n\n{description}"[:4096]
        )

    @discord.ui.button(
        label="add payment method",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def add_payment_method(self, interaction, button):
        methods = self.panel.settings.get("payment_methods") or []

        if len(methods) >= 25:
            await interaction.response.send_message(
                embed=embeds.error("you already have 25 payment methods."),
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            PaymentMethodModal(self.panel)
        )

    @discord.ui.button(
        label="remove payment method",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def remove_payment_method(self, interaction, button):
        methods = self.panel.settings.get("payment_methods") or []

        if not methods:
            await interaction.response.send_message(
                embed=embeds.error("there are no payment methods to remove."),
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            RemovePaymentMethodModal(self.panel)
        )


class RemovePaymentMethodModal(discord.ui.Modal):
    def __init__(self, panel):
        super().__init__(title="remove payment method")
        self.panel = panel

        self.name_input = discord.ui.TextInput(
            label="payment method name",
            placeholder="enter the exact payment method name",
            max_length=LABEL_LIMIT,
            required=True,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction):
        target = self.name_input.value.strip().lower()
        methods = self.panel.settings.get("payment_methods") or []

        remaining = [
            method
            for method in methods
            if (method.get("label") or "").strip().lower() != target
        ]

        if len(remaining) == len(methods):
            await interaction.response.send_message(
                embed=embeds.error("payment method not found."),
                ephemeral=True,
            )
            return

        self.panel.settings["payment_methods"] = remaining
        save_config()

        await interaction.response.edit_message(
            embed=self.panel.status_embed(),
            view=self,
        )


class SetupView(discord.ui.View):
    def __init__(self, ctx, settings):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.settings = settings
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id == self.ctx.author.id:
            return True
        await interaction.response.send_message(
            embed=embeds.error("this panel isn't yours."), ephemeral=True
        )
        return False

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    def status_embed(self):
        settings = self.settings
        sample = dict(SAMPLE_ORDER)
        for entry in questions_of(settings):
            sample.setdefault(
                entry["field"],
                "2" if entry.get("kind") == "number" else entry["label"],
            )
        preview = render(settings["confirm_format"], sample, self.ctx.author.id, self.ctx.guild)
        asked = "a modal" if modal_ready(settings) else "one question at a time"
        lines = [
            "**confirmation setup**",
            "",
            f"**form button** : {settings.get('form_button') or DEFAULT_FORM_BUTTON}",
            f"**form asks** : {len(questions_of(settings))} questions, {asked}",
            f"**confirm button** : {settings['confirm_button']}",
            f"**terms button** : {settings['terms_button']}",
            f"**payment methods** : {len(settings.get('payment_methods') or [])}",
            "",
            "**confirm box preview**",
            preview[:800],
            "",
            "tip : start any button with an emoji like `:heart: confirm`. "
            "Payment method details can also contain an image link for a qr.",
        ]
        return embeds.build("\n".join(lines)[:4096])

    async def refresh(self, interaction=None):
        embed = self.status_embed()
        if interaction is not None and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message is not None:
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    async def edit(self, interaction, field, label, multiline=False, required=True, limit=FORMAT_LIMIT):
        await interaction.response.send_modal(
            FieldModal(self, field, label, multiline=multiline, required=required, limit=limit)
        )

    @discord.ui.button(label="ping", style=discord.ButtonStyle.secondary, row=0)
    async def ping(self, interaction, button):
        await self.edit(interaction, "ping", "ping line (use {user})", required=False, limit=200)

    @discord.ui.button(label="confirm format", style=discord.ButtonStyle.secondary, row=0)
    async def confirm_format(self, interaction, button):
        await self.edit(interaction, "confirm_format", "confirm format", multiline=True)

    @discord.ui.button(label="footer", style=discord.ButtonStyle.secondary, row=0)
    async def footer(self, interaction, button):
        await self.edit(interaction, "footer", "footer line", multiline=True, required=False, limit=1000)

    @discord.ui.button(label="confirm button", style=discord.ButtonStyle.secondary, row=1)
    async def confirm_button(self, interaction, button):
        await self.edit(interaction, "confirm_button", "confirm button label", limit=LABEL_LIMIT)

    @discord.ui.button(label="received format", style=discord.ButtonStyle.secondary, row=1)
    async def received_format(self, interaction, button):
        await self.edit(interaction, "received_format", "received format", multiline=True)

    @discord.ui.button(label="terms format", style=discord.ButtonStyle.secondary, row=2)
    async def terms_format(self, interaction, button):
        await self.edit(interaction, "terms_format", "terms and conditions", multiline=True)

    @discord.ui.button(label="terms button", style=discord.ButtonStyle.secondary, row=2)
    async def terms_button(self, interaction, button):
        await self.edit(interaction, "terms_button", "terms button label", limit=LABEL_LIMIT)

    @discord.ui.button(label="payment methods", style=discord.ButtonStyle.secondary, row=3)
    async def payment_methods(self, interaction, button):
        view = PaymentMethodsView(self)
        await interaction.response.send_message(
            embed=view.status_embed(),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="questions", style=discord.ButtonStyle.secondary, row=3)
    async def questions(self, interaction, button):
        view = QuestionsView(self)
        await interaction.response.send_message(
            embed=view.status_embed(),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="form button", style=discord.ButtonStyle.secondary, row=3)
    async def form_button(self, interaction, button):
        await self.edit(interaction, "form_button", "order form button label", limit=LABEL_LIMIT)

    @discord.ui.button(label="form text", style=discord.ButtonStyle.secondary, row=4)
    async def form_text(self, interaction, button):
        await self.edit(interaction, "form_text", "text above the form button", multiline=True, required=False, limit=1000)

    @discord.ui.button(label="placeholders", style=discord.ButtonStyle.secondary, row=4)
    async def placeholders(self, interaction, button):
        await interaction.response.send_message(
            embed=embeds.build(
                "**placeholders**\n\n"
                "drop these into any format and the bot fills them in.\n\n"
                + placeholder_list(self.settings)
            ),
            ephemeral=True,
        )


class Confirmation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._views_added = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._views_added:
            return
        self._views_added = True
        self.bot.add_view(FormPanel(defaults()))

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.NoPrivateMessage):
            await embeds.send(ctx, embeds.error("this command only works in a server."))
            return
        if isinstance(error, commands.MissingPermissions):
            await embeds.send(
                ctx,
                embeds.error("you need manage server permission for that.", title="Not allowed"),
            )
            return
        log.exception("Unhandled error in %s", ctx.command, exc_info=error)
        await embeds.send(ctx, embeds.error("something broke on my end. it has been logged."))

    @commands.hybrid_group(
        name="confirmation",
        aliases=["confirm"],
        invoke_without_command=True,
        fallback="new",
        description="Fill in and confirm your order.",
    )
    @app_commands.describe(
        item="what you're ordering",
        price="how much it costs",
        quantity="how many",
        notes="any extra notes (optional)",
    )
    @commands.guild_only()
    async def confirmation(self, ctx, item: str = None, price: str = None,
                           quantity: str = None, *, notes: str = None):
        settings = settings_for(ctx.guild.id)

        if item is None:
            if (ctx.channel.id, ctx.author.id) in active:
                await ctx.send(embed=embeds.error("you already have a form open here."))
                return

            active.add((ctx.channel.id, ctx.author.id))
            try:
                order = await ask_questions(self.bot, ctx.channel, ctx.author, settings)
            finally:
                active.discard((ctx.channel.id, ctx.author.id))

            if order is None:
                return

            await post_confirmation(ctx.channel, ctx.author, settings, order, ctx.guild)
            return

        order = {
            "item": item.strip(),
            "price": (price or "").strip(),
            "quantity": (quantity or "").strip(),
            "notes": (notes or "").strip(),
        }
        view = ConfirmView(settings, order, ctx.author.id, ctx.guild)
        await ctx.send(
            view=view,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )

    @confirmation.command(name="panel", description="Post the order form button here.")
    @app_commands.default_permissions(manage_messages=True)
    @commands.has_permissions(manage_messages=True)
    @commands.guild_only()
    async def panel(self, ctx, *, text: str = None):
        settings = ensure_config(ctx.guild.id)
        save_config()
        await ctx.channel.send(view=FormPanel(settings, ctx.guild, text))
        if ctx.interaction:
            await ctx.send(embed=embeds.notice("panel posted."), ephemeral=True)

    @confirmation.command(name="setup", description="Set up the confirmation formats.")
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def setup_confirmation(self, ctx):
        settings = ensure_config(ctx.guild.id)
        save_config()
        view = SetupView(ctx, settings)
        view.message = await ctx.send(
            embed=view.status_embed(),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @confirmation.command(name="fields", description="Show the order form placeholders.")
    @commands.guild_only()
    async def fields(self, ctx):
        settings = settings_for(ctx.guild.id)
        await ctx.send(
            embed=embeds.build(
                "**order form**\n\n"
                f"`{display_prefix(ctx)}confirmation` fills the form here.\n"
                f"`{display_prefix(ctx)}confirmation panel` posts the button.\n\n"
                + placeholder_list(settings)
            )
        )


async def setup(bot):
    await bot.add_cog(Confirmation(bot))