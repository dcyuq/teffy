import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

import embeds
import emojiutils
import templating
from storage import Store

log = logging.getLogger(__name__)

FIELD_LIMIT = 200
NOTES_LIMIT = 500
FORMAT_LIMIT = 2000
LABEL_LIMIT = 80

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
        "gcash_button": DEFAULT_GCASH_BUTTON,
        "gcash_text": DEFAULT_GCASH_TEXT,
    }


def ensure_config(guild_id):
    key = str(guild_id)
    if key not in config:
        config[key] = defaults()
    settings = config[key]
    for field, value in defaults().items():
        settings.setdefault(field, value)
    return settings


def settings_for(guild_id):
    return config.get(str(guild_id)) or defaults()


def order_values(order, author_id):
    return {
        "item": order.get("item", ""),
        "price": order.get("price", ""),
        "quantity": order.get("quantity", ""),
        "notes": order.get("notes") or "none",
        "user": f"<@{author_id}>" if author_id else "",
    }


def render(template, order, author_id, guild):
    return templating.render(template, order_values(order, author_id), ALIASES, guild)


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
        view = PaymentView(self.settings, self.order, self.author_id, interaction.guild)
        await interaction.response.edit_message(view=view)


class GcashRow(discord.ui.ActionRow):
    def __init__(self, parent, raw, guild):
        super().__init__()
        self.owner = parent
        apply_label(self.pick, raw, guild, "gcash")

    @discord.ui.button(style=discord.ButtonStyle.secondary)
    async def pick(self, interaction, button):
        await self.owner.pay(interaction)


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
            render(self.settings["received_format"], self.order, self.author_id, self.guild)[:4000]
        ))
        box.add_item(discord.ui.Separator())
        box.add_item(GcashRow(self, self.settings.get("gcash_button"), self.guild))
        self.add_item(box)

    async def pay(self, interaction):
        view = GcashBox(self.settings, self.order, self.author_id, interaction.guild)
        await interaction.response.send_message(
            view=view, allowed_mentions=discord.AllowedMentions.none()
        )


class GcashBox(discord.ui.LayoutView):
    def __init__(self, settings, order, author_id, guild):
        super().__init__(timeout=None)
        box = discord.ui.Container()
        body = render(settings["gcash_text"], order, author_id, guild)
        body, image_url = split_image(body)
        if body.strip():
            box.add_item(discord.ui.TextDisplay(body[:4000]))
        if image_url:
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media=image_url)
            box.add_item(gallery)
        self.add_item(box)


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
        preview = render(settings["confirm_format"], SAMPLE_ORDER, self.ctx.author.id, self.ctx.guild)
        lines = [
            "**confirmation setup**",
            "",
            f"**confirm button** : {settings['confirm_button']}",
            f"**gcash button** : {settings['gcash_button']}",
            "",
            "**confirm box preview**",
            preview[:800],
            "",
            "tip : start any button with an emoji like `:heart: confirm`, and paste "
            "an image link inside the gcash text to show a qr under it.",
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

    @discord.ui.button(label="gcash button", style=discord.ButtonStyle.secondary, row=2)
    async def gcash_button(self, interaction, button):
        await self.edit(interaction, "gcash_button", "gcash button label", limit=LABEL_LIMIT)

    @discord.ui.button(label="gcash text", style=discord.ButtonStyle.secondary, row=2)
    async def gcash_text(self, interaction, button):
        await self.edit(interaction, "gcash_text", "gcash text (+ image link for qr)", multiline=True)


class Confirmation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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
    async def confirmation(self, ctx, item: str, price: str, quantity: str, *, notes: str = None):
        settings = settings_for(ctx.guild.id)
        order = {
            "item": item.strip(),
            "price": price.strip(),
            "quantity": quantity.strip(),
            "notes": (notes or "").strip(),
        }
        view = ConfirmView(settings, order, ctx.author.id, ctx.guild)
        await ctx.send(
            view=view,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )

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


async def setup(bot):
    await bot.add_cog(Confirmation(bot))