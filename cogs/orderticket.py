import copy
import logging
import re

import discord
from discord.ext import commands

import cogs.confirmation as confirmation
import cogs.ticket as ticket
from storage import Store

log = logging.getLogger(__name__)

_store = Store("orderticket_config.json")
config = _store.load()

DEFAULT_FIELDS = [
    {
        "key": "item",
        "label": "Exact Name of Order",
        "placeholder": "What are you ordering?",
        "style": "short",
        "required": True,
    },
    {
        "key": "price",
        "label": "Price",
        "placeholder": "Amount or price",
        "style": "short",
        "required": True,
    },
    {
        "key": "quantity",
        "label": "Quantity",
        "placeholder": "How many?",
        "style": "short",
        "required": True,
    },
    {
        "key": "notes",
        "label": "Details / Notes",
        "placeholder": "Anything else the seller should know?",
        "style": "paragraph",
        "required": False,
    },
]


def save_config():
    _store.save(config)


def default_settings():
    return {
        "button_key": None,
        "fields": copy.deepcopy(DEFAULT_FIELDS),
    }


def settings_for(guild_id):
    key = str(guild_id)
    if key not in config:
        config[key] = default_settings()

    settings = config[key]
    defaults = default_settings()

    settings.setdefault("button_key", defaults["button_key"])
    settings.setdefault("fields", copy.deepcopy(defaults["fields"]))

    fields = settings.get("fields")

    if not isinstance(fields, list) or len(fields) != len(DEFAULT_FIELDS):
        settings["fields"] = copy.deepcopy(DEFAULT_FIELDS)

    for index, field in enumerate(settings["fields"]):
        source = DEFAULT_FIELDS[index]
        field.setdefault("key", source["key"])
        field.setdefault("label", source["label"])
        field.setdefault("placeholder", source["placeholder"])
        field.setdefault("style", source["style"])
        field.setdefault("required", source["required"])

    return settings


def sync_ticket_override(guild_id):
    order_settings = settings_for(guild_id)
    ticket_settings = ticket.get_config(guild_id)

    if not ticket_settings:
        return

    selected_key = order_settings.get("button_key")
    changed = False

    for button_data in ticket_settings.get("buttons", []):
        enabled = (
            selected_key is not None
            and button_data.get("key") == selected_key
        )

        if button_data.get("order_enabled") != enabled:
            button_data["order_enabled"] = enabled
            changed = True

    if changed:
        ticket.save_config()


def selected_button(guild_id):
    sync_ticket_override(guild_id)

    settings = ticket.get_config(guild_id)
    order_settings = settings_for(guild_id)

    if not settings or not order_settings.get("button_key"):
        return None

    return ticket.find_button(
        settings,
        order_settings["button_key"],
    )


def button_label(guild_id):
    button_data = selected_button(guild_id)
    return button_data["label"] if button_data else "none"


def answer_kind(label):
    text = re.sub(
        r"[^a-z0-9]+",
        " ",
        label.lower(),
    ).strip()

    if any(word in text for word in ("payment", "pay", "method")):
        return "payment"

    if any(word in text for word in ("quantity", "qty", "how many")):
        return "quantity"

    if any(word in text for word in ("price", "amount", "cost", "total")):
        return "price"

    if any(word in text for word in ("note", "detail", "description", "extra")):
        return "notes"

    if any(word in text for word in ("user", "username", "customer", "discord")):
        return "user"

    if any(word in text for word in ("item", "order", "product", "name")):
        return "item"

    return None


def order_from_values(values, answers, user):
    order = {
        "item": values.get("item", "").strip(),
        "price": values.get("price", "").strip(),
        "quantity": values.get("quantity", "").strip(),
        "notes": values.get("notes", "").strip(),
        "user": user.mention,
        "answers": answers,
    }

    for label, answer in answers:
        kind = answer_kind(label)

        if kind and not order.get(kind):
            order[kind] = answer

    fallback = [
        answer
        for _, answer in answers
        if answer
    ]

    if not order["item"] and fallback:
        order["item"] = fallback[0]

    if not order["price"] and len(fallback) > 1:
        order["price"] = fallback[1]

    if not order["quantity"] and len(fallback) > 2:
        order["quantity"] = fallback[2]

    if not order["notes"] and len(fallback) > 3:
        order["notes"] = fallback[-1]

    return order


def order_values(order_settings, inputs):
    values = {
        field["key"]: text_input.value.strip()
        for field, text_input in inputs
    }

    answers = [
        (field["label"], text_input.value.strip())
        for field, text_input in inputs
    ]

    return values, answers


def render_receipt(order, user, guild):
    settings = confirmation.settings_for(guild.id)

    template = settings.get(
        "confirm_format",
        confirmation.DEFAULT_CONFIRM_FORMAT,
    )

    rendered = confirmation.render(
        template,
        order,
        user.id,
        guild,
    )

    details = "\n\n".join(
        f"**{label[:256]}**\n{(answer or '-')[:1024]}"
        for label, answer in order.get("answers", [])
    )

    if details:
        rendered = (
            f"{rendered[:3500]}\n\n"
            f"**submitted details**\n"
            f"{details}"
        )

    return rendered[:4000]


def setup_status(guild_id):
    button = selected_button(guild_id)

    return discord.Embed(
        title="Order ticket setup",
        description=(
            f"**order button** : "
            f"{button['label'] if button else 'none'}\n"
            "**questions** : item, price, quantity, notes\n\n"
            "The selected ticket button always opens the order form first. "
            "All other ticket buttons keep their normal ticket.py behavior."
        ),
    )


async def send_order_modal(interaction, button_data):
    await interaction.response.send_modal(
        OrderTicketModal(
            button_data,
            settings_for(interaction.guild.id),
        )
    )

    return True


class OrderTicketModal(discord.ui.Modal):
    def __init__(self, button_data, order_settings):
        super().__init__(
            title=f"{button_data['label'][:35]} order details"
        )

        self.button_data = button_data
        self.order_settings = order_settings
        self.inputs = []

        for field in order_settings["fields"][:5]:
            text_input = discord.ui.TextInput(
                label=field["label"][:45],
                placeholder=field.get("placeholder", "")[:100],
                style=(
                    discord.TextStyle.paragraph
                    if field.get("style") == "paragraph"
                    else discord.TextStyle.short
                ),
                required=bool(field.get("required", True)),
                max_length=1000,
            )

            self.inputs.append((field, text_input))
            self.add_item(text_input)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)

        values, answers = order_values(
            self.order_settings,
            self.inputs,
        )

        order = order_from_values(
            values,
            answers,
            interaction.user,
        )

        before = set(ticket.tickets.keys())

        await ticket.create_ticket(
            interaction,
            self.button_data,
            answers,
        )

        await customize_ticket(
            interaction,
            self.button_data,
            order,
            before,
        )


class OrderConfirmRow(discord.ui.ActionRow):
    def __init__(self, order, author_id, guild):
        super().__init__()

        self.order = order
        self.author_id = author_id
        self.guild = guild

        settings = confirmation.settings_for(guild.id)

        confirmation.apply_label(
            self.confirm_order,
            settings.get("confirm_button"),
            guild,
            "confirm order",
        )

    @discord.ui.button(
        label="confirm order",
        style=discord.ButtonStyle.secondary,
        custom_id="orderticket:confirm",
    )
    async def confirm_order(self, interaction, button):
        settings = confirmation.settings_for(
            interaction.guild.id
        )

        await interaction.response.edit_message(
            view=confirmation.ConfirmView(
                settings,
                self.order,
                self.author_id,
                interaction.guild,
            )
        )


class OrderTicketView(discord.ui.LayoutView):
    def __init__(
        self,
        ping,
        heading,
        welcome,
        receipt,
        color,
        order,
        author_id,
        guild,
    ):
        super().__init__(timeout=None)

        if ping:
            self.add_item(discord.ui.TextDisplay(ping))

        container = discord.ui.Container(
            accent_colour=(
                discord.Colour(color)
                if color
                else None
            )
        )

        container.add_item(
            discord.ui.TextDisplay(heading[:2000])
        )

        container.add_item(discord.ui.Separator())

        if welcome:
            container.add_item(
                discord.ui.TextDisplay(welcome[:2000])
            )

        container.add_item(
            discord.ui.Separator(
                spacing=discord.SeparatorSpacing.large,
                visible=False,
            )
        )

        container.add_item(
            discord.ui.TextDisplay(receipt[:4000])
        )

        self.add_item(container)
        self.add_item(ticket.TicketControls())
        self.add_item(
            OrderConfirmRow(
                order,
                author_id,
                guild,
            )
        )


async def find_opening_message(channel):
    message_id = getattr(
        channel,
        "last_message_id",
        None,
    )

    if message_id:
        try:
            return await channel.fetch_message(message_id)
        except (
            discord.HTTPException,
            discord.NotFound,
        ):
            pass

    try:
        async for message in channel.history(limit=10):
            if message.author.id == channel.guild.me.id:
                return message
    except (
        discord.HTTPException,
        discord.Forbidden,
        AttributeError,
    ):
        return None

    return None


async def customize_ticket(
    interaction,
    button_data,
    order,
    before,
):
    candidates = []

    for channel_id, entry in ticket.tickets.items():
        if channel_id in before:
            continue

        if entry.get("guild_id") != interaction.guild.id:
            continue

        if entry.get("opener_id") != interaction.user.id:
            continue

        candidates.append(
            (
                entry.get("opened_at", 0),
                channel_id,
                entry,
            )
        )

    if not candidates:
        return

    _, channel_id, entry = max(candidates)
    channel = interaction.guild.get_channel(channel_id)

    if channel is None:
        return

    settings = ticket.get_config(
        interaction.guild.id
    ) or {}

    panel = settings.get("panel") or {}
    roles = ticket.staff_roles(
        interaction.guild,
        settings,
    )

    mentions = " ".join(
        role.mention
        for role in roles
    )

    ping = f"{interaction.user.mention} {mentions}".strip()

    heading = (
        f"Ticket "
        f"{entry.get('number', 0):04d} "
        f"- {button_data['label']}"
    )

    receipt = render_receipt(
        order,
        interaction.user,
        interaction.guild,
    )

    view = build_order_view(
        ping,
        heading,
        button_data.get("welcome")
        or ticket.DEFAULT_BUTTON["welcome"],
        receipt,
        panel.get("color"),
        order,
        interaction.user.id,
        interaction.guild,
    )

    message = await find_opening_message(channel)

    try:
        if message is not None:
            await message.edit(view=view)
        else:
            message = await channel.send(
                view=view,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=roles or False,
                ),
            )

        entry["order_data"] = order
        entry["order_button_key"] = button_data["key"]
        entry["order_message_id"] = message.id

        ticket.save_tickets()

    except (
        discord.HTTPException,
        discord.Forbidden,
    ):
        log.exception(
            "Unable to customize order ticket %s",
            channel_id,
        )


def build_order_view(
    ping,
    heading,
    welcome,
    receipt,
    color,
    order,
    author_id,
    guild,
):
    return OrderTicketView(
        ping,
        heading,
        welcome,
        receipt,
        color,
        order,
        author_id,
        guild,
    )


class OrderButtonSelect(discord.ui.Select):
    def __init__(self, parent):
        self.owner_view = parent

        settings = ticket.get_config(
            parent.ctx.guild.id
        ) or {}

        buttons = settings.get(
            "buttons",
            [],
        )[:24]

        order_settings = settings_for(
            parent.ctx.guild.id
        )

        options = [
            discord.SelectOption(
                label=button_data["label"][:100],
                value=button_data["key"],
                description="Use the order form for this button",
                default=(
                    button_data["key"]
                    == order_settings.get("button_key")
                ),
            )
            for button_data in buttons
        ]

        options.append(
            discord.SelectOption(
                label="Disable order override",
                value="none",
                description=(
                    "Return every ticket button "
                    "to normal behavior"
                ),
                default=(
                    order_settings.get("button_key")
                    is None
                ),
            )
        )

        super().__init__(
            placeholder="Choose the Order button",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction):
        order_settings = settings_for(
            interaction.guild.id
        )

        selected_key = (
            None
            if self.values[0] == "none"
            else self.values[0]
        )

        order_settings["button_key"] = selected_key
        save_config()

        ticket_settings = ticket.get_config(
            interaction.guild.id
        )

        if ticket_settings:
            for button_data in ticket_settings.get(
                "buttons",
                [],
            ):
                button_data["order_enabled"] = (
                    selected_key is not None
                    and button_data.get("key")
                    == selected_key
                )

            ticket.save_config()

        await interaction.response.edit_message(
            embed=setup_status(interaction.guild.id),
            view=OrderMenuView(self.owner_view),
        )

        await self.owner_view.refresh()


class OrderButtonMenuView(discord.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=300)

        self.owner_view = parent
        self.add_item(OrderButtonSelect(parent))

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_view.ctx.author.id:
            return True

        await interaction.response.send_message(
            embed=discord.Embed(
                description="this panel isn't yours."
            ),
            ephemeral=True,
        )

        return False


class OrderMenuView(discord.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=300)
        self.owner_view = parent

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_view.ctx.author.id:
            return True

        await interaction.response.send_message(
            embed=discord.Embed(
                description="this panel isn't yours."
            ),
            ephemeral=True,
        )

        return False

    @discord.ui.button(
        label="choose order button",
        style=discord.ButtonStyle.secondary,
    )
    async def choose_button(self, interaction, button):
        await interaction.response.edit_message(
            embed=setup_status(interaction.guild.id),
            view=OrderButtonMenuView(self.owner_view),
        )

    @discord.ui.button(
        label="edit order form",
        style=discord.ButtonStyle.secondary,
    )
    async def edit_form(self, interaction, button):
        await interaction.response.send_modal(
            OrderFieldsModal(self.owner_view)
        )


class OrderFieldsModal(discord.ui.Modal):
    def __init__(self, parent):
        super().__init__(
            title="Edit order form labels"
        )

        self.owner_view = parent
        self.inputs = []

        fields = settings_for(
            self.owner_view.ctx.guild.id
        )["fields"]

        for index, field in enumerate(fields):
            text_input = discord.ui.TextInput(
                label=f"Field {index + 1} label",
                default=field["label"][:45],
                max_length=45,
                required=True,
            )

            self.inputs.append(
                (field, text_input)
            )

            self.add_item(text_input)

    async def on_submit(self, interaction):
        for field, text_input in self.inputs:
            field["label"] = text_input.value.strip()[:45]

        save_config()

        await interaction.response.send_message(
            embed=discord.Embed(
                description="the order form labels were saved."
            ),
            ephemeral=True,
        )

        await self.owner_view.refresh()


class OrderSetupButton(discord.ui.Button):
    def __init__(self, parent):
        self.owner_view = parent

        super().__init__(
            label="order tickets",
            style=discord.ButtonStyle.secondary,
            row=3,
            custom_id="orderticket:setup",
        )

    async def callback(self, interaction):
        await interaction.response.send_message(
            embed=setup_status(interaction.guild.id),
            view=OrderMenuView(self.owner_view),
            ephemeral=True,
        )


async def order_override(interaction, button_data):
    order_settings = settings_for(
        interaction.guild.id
    )

    if (
        order_settings.get("button_key")
        != button_data.get("key")
        and not button_data.get("order_enabled")
    ):
        return False

    await send_order_modal(
        interaction,
        button_data,
    )

    return True


def patch_confirmation_setup():
    setup_view = confirmation.SetupView

    if getattr(
        setup_view,
        "_orderticket_patched",
        False,
    ):
        return

    original_init = setup_view.__init__

    def patched_init(self, ctx, settings):
        original_init(self, ctx, settings)
        self.add_item(OrderSetupButton(self))

    setup_view.__init__ = patched_init
    setup_view._orderticket_patched = True


patch_confirmation_setup()

for guild_id in list(config):
    sync_ticket_override(int(guild_id))

ticket.ORDER_OVERRIDE_CALLBACK = order_override


class OrderTickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._views_added = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._views_added:
            return

        self._views_added = True

        for channel_id, entry in ticket.tickets.items():
            order = entry.get("order_data")
            message_id = entry.get("order_message_id")

            if not order or not message_id:
                continue

            guild = self.bot.get_guild(
                entry.get("guild_id")
            )

            channel = (
                guild.get_channel(channel_id)
                if guild
                else None
            )

            if guild is None or channel is None:
                continue

            settings = ticket.get_config(
                guild.id
            ) or {}

            button_data = ticket.find_button(
                settings,
                entry.get("order_button_key"),
            )

            if button_data is None:
                for candidate in settings.get(
                    "buttons",
                    [],
                ):
                    if candidate.get("label") == entry.get("kind"):
                        button_data = candidate
                        break

            if button_data is None:
                continue

            opener = guild.get_member(
                entry.get("opener_id")
            )

            if opener is None:
                continue

            roles = ticket.staff_roles(
                guild,
                settings,
            )

            mentions = " ".join(
                role.mention
                for role in roles
            )

            ping = f"{opener.mention} {mentions}".strip()
            panel = settings.get("panel") or {}

            receipt = render_receipt(
                order,
                opener,
                guild,
            )

            view = build_order_view(
                ping,
                f"Ticket {entry.get('number', 0):04d} - {button_data['label']}",
                button_data.get("welcome")
                or ticket.DEFAULT_BUTTON["welcome"],
                receipt,
                panel.get("color"),
                order,
                opener.id,
                guild,
            )

            try:
                self.bot.add_view(
                    view,
                    message_id=message_id,
                )
            except (
                discord.HTTPException,
                ValueError,
            ):
                continue


async def setup(bot):
    await bot.add_cog(OrderTickets(bot))