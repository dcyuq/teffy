import logging

import discord
from discord import app_commands
from discord.ext import commands

import embeds
from storage import Store

from . import confirmation

log = logging.getLogger(__name__)

TEXT_LIMIT = 2000
LABEL_LIMIT = 80

DEFAULT_TITLE = "**payment**"
DEFAULT_BODY = "click below to read the terms and pick a payment method."
DEFAULT_BUTTON = "pay"

_store = Store("pay_config.json")
config = _store.load()

def save_config():
    _store.save(config)

def defaults():
    return {
        "title": DEFAULT_TITLE,
        "body": DEFAULT_BODY,
        "button": DEFAULT_BUTTON,
        "buttons": [],
    }

def enabled_for(guild_id, key):
    return key in (settings_for(guild_id).get("buttons") or [])

def ticket_buttons(guild_id):
    try:
        from . import ticket
    except Exception:
        return []
    settings = ticket.get_config(guild_id)
    return list((settings or {}).get("buttons") or [])

def ensure_config(guild_id):
    settings = config.setdefault(str(guild_id), {})
    for field, value in defaults().items():
        settings.setdefault(field, value)
    return settings

def settings_for(guild_id):
    settings = config.get(str(guild_id))
    if not settings:
        return defaults()
    for field, value in defaults().items():
        settings.setdefault(field, value)
    return settings

def order_for(user):
    return {"item": "", "price": "", "quantity": "", "notes": ""}

def render(template, user, guild):
    return confirmation.render(template or "", order_for(user), user.id, guild)

class PayButton(discord.ui.Button):
    def __init__(self, guild=None, label=None):
        super().__init__(style=discord.ButtonStyle.secondary, custom_id="pay:start")
        confirmation.apply_label(self, label, guild, DEFAULT_BUTTON)

    async def callback(self, interaction):
        settings = confirmation.settings_for(interaction.guild.id)
        if not settings.get("payment_methods"):
            await interaction.response.send_message(
                embed=embeds.error("no payment methods have been set up yet."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            view=PayMethodsView(settings, interaction.user, interaction.guild),
            allowed_mentions=discord.AllowedMentions.none(),
        )

class PayPanelView(discord.ui.LayoutView):
    def __init__(self, settings=None, guild=None):
        super().__init__(timeout=None)
        settings = settings or defaults()

        box = discord.ui.Container()
        title = (settings.get("title") or "").strip()
        body = (settings.get("body") or "").strip()

        if title:
            box.add_item(discord.ui.TextDisplay(title[:TEXT_LIMIT]))
        if body:
            box.add_item(discord.ui.TextDisplay(body[:TEXT_LIMIT]))
        if title or body:
            box.add_item(discord.ui.Separator())

        row = discord.ui.ActionRow()
        row.add_item(PayButton(guild, settings.get("button")))
        box.add_item(row)
        self.add_item(box)

class PayMethodsView(discord.ui.View):
    def __init__(self, settings, user, guild):
        super().__init__(timeout=600)
        self.settings = settings
        self.user = user
        self.guild = guild

        for method in (settings.get("payment_methods") or [])[:25]:
            button = discord.ui.Button(style=discord.ButtonStyle.secondary)
            confirmation.apply_label(
                button, method.get("label"), guild, method.get("id", "payment")
            )
            button.callback = self._callback(method)
            self.add_item(button)

    def _callback(self, method):
        async def callback(interaction):
            await interaction.response.send_message(
                view=PayMethodView(self.settings, interaction.user, interaction.guild, method),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return callback

class PayMethodView(discord.ui.LayoutView):
    def __init__(self, settings, user, guild, method):
        super().__init__(timeout=None)
        box = discord.ui.Container()
        body, image_url = confirmation.split_image(
            render(method.get("text"), user, guild)
        )

        if body.strip():
            box.add_item(discord.ui.TextDisplay(body[:4000]))
        if image_url:
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media=image_url)
            box.add_item(gallery)

        self.add_item(box)

class PanelModal(discord.ui.Modal, title="Pay Panel"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.f_title = discord.ui.TextInput(
            label="Heading",
            default=panel.settings.get("title") or "",
            max_length=200,
            required=False,
        )
        self.f_body = discord.ui.TextInput(
            label="Text",
            default=panel.settings.get("body") or "",
            style=discord.TextStyle.paragraph,
            max_length=TEXT_LIMIT,
            required=False,
        )
        self.f_button = discord.ui.TextInput(
            label="Button label",
            default=panel.settings.get("button") or DEFAULT_BUTTON,
            placeholder="pay, or :gcash: pay",
            max_length=LABEL_LIMIT,
            required=True,
        )
        for item in (self.f_title, self.f_body, self.f_button):
            self.add_item(item)

    async def on_submit(self, interaction):
        self.panel.settings["title"] = self.f_title.value.strip()
        self.panel.settings["body"] = self.f_body.value.strip()
        self.panel.settings["button"] = self.f_button.value.strip() or DEFAULT_BUTTON
        save_config()
        await self.panel.refresh(interaction)

class TicketButtonSelect(discord.ui.Select):
    def __init__(self, panel):
        entries = ticket_buttons(panel.ctx.guild.id)[:25]
        chosen = panel.settings.get("buttons") or []
        options = [
            discord.SelectOption(
                label=str(entry.get("label") or "ticket")[:100],
                value=entry["key"],
                default=entry["key"] in chosen,
            )
            for entry in entries
            if entry.get("key")
        ]
        super().__init__(
            placeholder="Ticket buttons that get a pay button",
            options=options or [discord.SelectOption(label="no ticket buttons", value="none")],
            min_values=0,
            max_values=len(options) or 1,
            disabled=not options,
        )
        self.panel = panel

    async def callback(self, interaction):
        self.panel.settings["buttons"] = [v for v in self.values if v != "none"]
        save_config()
        refreshed = PaySetupView(self.panel.ctx, self.panel.settings)
        await interaction.response.edit_message(embed=refreshed.summary(), view=refreshed)

class PaySetupView(discord.ui.View):
    def __init__(self, ctx, settings):
        super().__init__(timeout=600)
        self.ctx = ctx
        self.settings = settings
        self.add_item(TicketButtonSelect(self))

    async def interaction_check(self, interaction):
        return interaction.user.id == self.ctx.author.id

    def summary(self):
        methods = confirmation.settings_for(self.ctx.guild.id).get("payment_methods") or []
        names = ", ".join(str(m.get("label") or m.get("id")) for m in methods) or "none"

        chosen = self.settings.get("buttons") or []
        labels = {
            entry.get("key"): entry.get("label")
            for entry in ticket_buttons(self.ctx.guild.id)
        }
        tickets_text = ", ".join(str(labels.get(key, key)) for key in chosen) or "none"

        return embeds.build(
            f"**On tickets** - {tickets_text}\n"
            f"**Button** - {self.settings.get('button')}\n"
            f"**Methods** - {names}\n\n"
            f"**Panel heading** - {self.settings.get('title') or 'none'}\n"
            f"**Panel text** - {self.settings.get('body') or 'none'}\n\n"
            "tickets opened from the picked buttons get a pay button next to claim and "
            "close. the terms, the wording after agreeing and the payment methods come "
            "from `confirmation setup`.",
            title="Pay Setup",
        )

    async def refresh(self, interaction):
        await interaction.response.edit_message(embed=self.summary(), view=self)

    @discord.ui.button(label="Edit Panel", style=discord.ButtonStyle.secondary)
    async def edit_panel(self, interaction, button):
        await interaction.response.send_modal(PanelModal(self))

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction, button):
        await interaction.response.send_message(
            view=PayPanelView(self.settings, interaction.guild),
            ephemeral=True,
        )

    @discord.ui.button(label="Post Panel", style=discord.ButtonStyle.primary)
    async def post_panel(self, interaction, button):
        await interaction.response.send_message(
            embed=embeds.notice(f"posted in {interaction.channel.mention}."),
            ephemeral=True,
        )
        await interaction.channel.send(
            view=PayPanelView(self.settings, interaction.guild),
            allowed_mentions=discord.AllowedMentions.none(),
        )

class Pay(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(PayPanelView())

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.NoPrivateMessage):
            await embeds.send(ctx, embeds.error("this command only works in a server."))
        elif isinstance(error, commands.MissingPermissions):
            await embeds.send(ctx, embeds.error("you need manage server permission for that."))
        else:
            log.exception("Unhandled error in %s", ctx.command, exc_info=error)
            await embeds.send(ctx, embeds.error("something broke on my end. it has been logged."))

    @commands.hybrid_group(
        name="pay",
        invoke_without_command=True,
        fallback="panel",
        description="Post the pay button.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def pay(self, ctx, channel: discord.TextChannel = None):
        settings = ensure_config(ctx.guild.id)
        save_config()
        target = channel or ctx.channel

        await target.send(
            view=PayPanelView(settings, ctx.guild),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if target.id != ctx.channel.id:
            await ctx.send(embed=embeds.notice(f"posted in {target.mention}."))

    @commands.hybrid_command(
        name="paysetup",
        description="Pick which ticket buttons get a pay button.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def paysetup(self, ctx):
        settings = ensure_config(ctx.guild.id)
        save_config()
        view = PaySetupView(ctx, settings)
        await ctx.send(embed=view.summary(), view=view, allowed_mentions=discord.AllowedMentions.none())

async def setup(bot):
    await bot.add_cog(Pay(bot))