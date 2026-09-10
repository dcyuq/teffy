import logging
import uuid

import discord
from discord.ext import commands

import cogs.ticket as ticket
import cogs.confirmation as confirmation

log = logging.getLogger(__name__)

TICKET_BUTTON_KEY = "ticket_button_key"
RECEIPT_FORMAT_KEY = "ticket_receipt_format"
PAYMENTS_KEY = "ticket_payment_methods"

DEFAULT_RECEIPT_FORMAT = (
    "**order confirmation**\n"
    "\n"
    "order : {item}\n"
    "amount : {price}\n"
    "quantity : {quantity}\n"
    "details/notes : {notes}\n"
    "customer : {user}"
)

DEFAULT_PAYMENTS = [
    {
        "key": "gcash",
        "label": "gcash",
        "text": "",
    }
]

ITEM_WORDS = {
    "item", "order", "product", "service",
    "what are you ordering", "what would you like",
    "what do you want", "exact order", "name of order",
}

PRICE_WORDS = {
    "price", "amount", "cost", "total", "budget", "how much",
}

QUANTITY_WORDS = {
    "quantity", "qty", "how many", "number of", "amount needed",
}

NOTES_WORDS = {
    "note", "notes", "detail", "details", "extra", "additional",
    "anything else",
}

PAYMENT_WORDS = {
    "payment", "payment method", "mode of payment", "mop",
}


def normal(text):
    return " ".join((text or "").strip().lower().split())


def contains(text, words):
    text = normal(text)
    return any(word in text for word in words)


def build_order(answers, author_id):
    order = {
        "item": "",
        "price": "",
        "quantity": "",
        "notes": "",
        "user": f"<@{author_id}>",
    }

    leftovers = []

    for question, answer in answers:
        answer = (answer or "").strip()
        if not answer:
            continue

        if not order["item"] and contains(question, ITEM_WORDS):
            order["item"] = answer
        elif not order["price"] and contains(question, PRICE_WORDS):
            order["price"] = answer
        elif not order["quantity"] and contains(question, QUANTITY_WORDS):
            order["quantity"] = answer
        elif contains(question, PAYMENT_WORDS):
            leftovers.append(f"payment method : {answer}")
        elif contains(question, NOTES_WORDS):
            leftovers.append(answer)
        else:
            leftovers.append(f"{question.strip()} : {answer}")

    nonempty = [
        (question, answer.strip())
        for question, answer in answers
        if (answer or "").strip()
    ]

    if not order["item"] and nonempty:
        order["item"] = nonempty[0][1]

    order["item"] = order["item"] or "not provided"
    order["price"] = order["price"] or "not provided"
    order["quantity"] = order["quantity"] or "not provided"
    order["notes"] = "\n".join(leftovers) or "none"

    return order


def receipt_settings(settings):
    settings.setdefault(RECEIPT_FORMAT_KEY, DEFAULT_RECEIPT_FORMAT)
    settings.setdefault(PAYMENTS_KEY, [dict(x) for x in DEFAULT_PAYMENTS])
    return settings


def selected_button(settings, guild):
    key = settings.get(TICKET_BUTTON_KEY)
    if not key:
        return None

    ticket_settings = ticket.get_config(guild.id) or {}
    return ticket.find_button(ticket_settings, key)


def payment_methods(settings):
    methods = settings.get(PAYMENTS_KEY) or []
    return methods[:25]


def render_receipt(settings, order, author_id, guild):
    template = settings.get(RECEIPT_FORMAT_KEY) or DEFAULT_RECEIPT_FORMAT
    return confirmation.render(template, order, author_id, guild)[:4000]


class ReceiptFormatModal(discord.ui.Modal, title="Opening Ticket Receipt"):
    def __init__(self, setup_view):
        super().__init__()
        self.setup_view = setup_view

        self.field = discord.ui.TextInput(
            label="Receipt format",
            default=(
                setup_view.settings.get(RECEIPT_FORMAT_KEY)
                or DEFAULT_RECEIPT_FORMAT
            )[:2000],
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction):
        self.setup_view.settings[RECEIPT_FORMAT_KEY] = self.field.value.strip()
        confirmation.save_config()

        await interaction.response.edit_message(
            embed=self.setup_view.status_embed(),
            view=self.setup_view,
        )


class PaymentMethodModal(discord.ui.Modal):
    def __init__(self, setup_view, existing=None):
        self.setup_view = setup_view
        self.existing = existing

        super().__init__(
            title="Edit Payment Method"
            if existing
            else "Add Payment Method"
        )

        self.label_field = discord.ui.TextInput(
            label="Button label",
            default=(existing or {}).get("label", "")[:80],
            max_length=80,
            required=True,
        )

        self.text_field = discord.ui.TextInput(
            label="Payment information",
            default=(existing or {}).get("text", "")[:2000],
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=False,
        )

        self.add_item(self.label_field)
        self.add_item(self.text_field)

    async def on_submit(self, interaction):
        methods = payment_methods(self.setup_view.settings)

        if self.existing is None:
            if len(methods) >= 25:
                await interaction.response.send_message(
                    embed=confirmation.embeds.error(
                        "you already have 25 payment methods."
                    ),
                    ephemeral=True,
                )
                return

            methods.append(
                {
                    "key": uuid.uuid4().hex[:8],
                    "label": self.label_field.value.strip(),
                    "text": self.text_field.value.strip(),
                }
            )
        else:
            self.existing["label"] = self.label_field.value.strip()
            self.existing["text"] = self.text_field.value.strip()

        self.setup_view.settings[PAYMENTS_KEY] = methods
        confirmation.save_config()

        await interaction.response.edit_message(
            embed=self.setup_view.status_embed(),
            view=self.setup_view,
        )


class PaymentManageSelect(discord.ui.Select):
    def __init__(self, setup_view):
        self.setup_view = setup_view
        methods = payment_methods(setup_view.settings)

        options = [
            discord.SelectOption(
                label=method["label"][:100],
                value=method["key"],
                description="edit payment method",
            )
            for method in methods
        ]

        super().__init__(
            placeholder="select a payment method",
            options=options or [
                discord.SelectOption(
                    label="none",
                    value="none",
                )
            ],
            disabled=not options,
        )

    async def callback(self, interaction):
        method = next(
            (
                method
                for method in payment_methods(self.setup_view.settings)
                if method["key"] == self.values[0]
            ),
            None,
        )

        if method is None:
            await interaction.response.send_message(
                "that payment method no longer exists.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            PaymentMethodModal(self.setup_view, method)
        )


class PaymentManageView(discord.ui.View):
    def __init__(self, setup_view):
        super().__init__(timeout=300)
        self.setup_view = setup_view
        self.add_item(PaymentManageSelect(setup_view))

    @discord.ui.button(
        label="Add Payment Method",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def add_method(self, interaction, button):
        if len(payment_methods(self.setup_view.settings)) >= 25:
            await interaction.response.send_message(
                "you already have 25 payment methods.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            PaymentMethodModal(self.setup_view)
        )


class TicketButtonSelect(discord.ui.Select):
    def __init__(self, setup_view):
        self.setup_view = setup_view
        guild = setup_view.ctx.guild
        ticket_settings = ticket.get_config(guild.id) or {}

        options = []

        for button_data in ticket_settings.get("buttons", [])[:24]:
            questions = button_data.get("questions", [])

            options.append(
                discord.SelectOption(
                    label=button_data["label"][:100],
                    value=button_data["key"],
                    description=(
                        f"{len(questions)} question(s)"
                        if questions
                        else "opens instantly"
                    )[:100],
                    default=(
                        button_data["key"]
                        == setup_view.settings.get(TICKET_BUTTON_KEY)
                    ),
                    emoji=ticket.icon_partial(
                        button_data.get("emoji")
                    ),
                )
            )

        options.append(
            discord.SelectOption(
                label="None - disable",
                value="none",
                description="Disable the order ticket integration.",
            )
        )

        super().__init__(
            placeholder="select the ticket button",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction):
        value = self.values[0]

        if value == "none":
            self.setup_view.settings.pop(TICKET_BUTTON_KEY, None)
        else:
            ticket_settings = ticket.get_config(interaction.guild.id) or {}
            button_data = ticket.find_button(ticket_settings, value)

            if button_data is None:
                await interaction.response.send_message(
                    "that ticket button no longer exists.",
                    ephemeral=True,
                )
                return

            if not button_data.get("questions"):
                await interaction.response.send_message(
                    "that ticket button needs questions first. "
                    "set them in `/ticket setup`.",
                    ephemeral=True,
                )
                return

            self.setup_view.settings[TICKET_BUTTON_KEY] = value

        confirmation.save_config()

        await interaction.response.edit_message(
            content="ticket button selection saved.",
            view=None,
        )

        await self.setup_view.refresh()


class TicketButtonPicker(discord.ui.View):
    def __init__(self, setup_view):
        super().__init__(timeout=300)
        self.setup_view = setup_view
        self.add_item(TicketButtonSelect(setup_view))


class TicketButtonSetupButton(discord.ui.Button):
    def __init__(self, setup_view):
        super().__init__(
            label="ticket button",
            style=discord.ButtonStyle.secondary,
            row=3,
        )
        self.setup_view = setup_view

    async def callback(self, interaction):
        ticket_settings = ticket.get_config(interaction.guild.id) or {}

        if not ticket_settings.get("buttons"):
            await interaction.response.send_message(
                "create a ticket button in `/ticket setup` first.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "choose the ticket button that should use the order flow.",
            view=TicketButtonPicker(self.setup_view),
            ephemeral=True,
        )


class ReceiptFormatSetupButton(discord.ui.Button):
    def __init__(self, setup_view):
        super().__init__(
            label="ticket receipt",
            style=discord.ButtonStyle.secondary,
            row=3,
        )
        self.setup_view = setup_view

    async def callback(self, interaction):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Opening Ticket Receipt",
                description=(
                    "Edit the format shown when the selected order ticket opens.\n\n"
                    "**Available fields**\n"
                    "`{item}`\n"
                    "`{price}`\n"
                    "`{quantity}`\n"
                    "`{notes}`\n"
                    "`{user}`"
                ),
            ),
            ephemeral=True,
        )
        await interaction.followup.send(
            "open the editor below.",
            view=ReceiptFormatOnlyView(self.setup_view),
            ephemeral=True,
        )


class ReceiptFormatOnlyView(discord.ui.View):
    def __init__(self, setup_view):
        super().__init__(timeout=300)
        self.setup_view = setup_view

    @discord.ui.button(
        label="Edit Receipt Format",
        style=discord.ButtonStyle.secondary,
    )
    async def edit_format(self, interaction, button):
        await interaction.response.send_modal(
            ReceiptFormatModal(self.setup_view)
        )


class PaymentSetupButton(discord.ui.Button):
    def __init__(self, setup_view):
        super().__init__(
            label="payment methods",
            style=discord.ButtonStyle.secondary,
            row=3,
        )
        self.setup_view = setup_view

    async def callback(self, interaction):
        methods = payment_methods(self.setup_view.settings)

        description = (
            "\n".join(
                f"- {method['label']}"
                for method in methods
            )
            if methods
            else "no payment methods configured."
        )

        await interaction.response.send_message(
            embed=discord.Embed(
                title="Payment Methods",
                description=description,
            ),
            view=PaymentManageView(self.setup_view),
            ephemeral=True,
        )


_original_setup_init = confirmation.SetupView.__init__
_original_status_embed = confirmation.SetupView.status_embed


def setup_init(self, ctx, settings):
    _original_setup_init(self, ctx, settings)
    receipt_settings(settings)

    if not any(
        isinstance(item, TicketButtonSetupButton)
        for item in self.children
    ):
        self.add_item(TicketButtonSetupButton(self))

    if not any(
        isinstance(item, ReceiptFormatSetupButton)
        for item in self.children
    ):
        self.add_item(ReceiptFormatSetupButton(self))

    if not any(
        isinstance(item, PaymentSetupButton)
        for item in self.children
    ):
        self.add_item(PaymentSetupButton(self))


def status_embed(self):
    receipt_settings(self.settings)
    embed = _original_status_embed(self)

    selected = selected_button(
        self.settings,
        self.ctx.guild,
    )

    selected_text = (
        selected["label"]
        if selected
        else "not set"
    )

    methods = payment_methods(self.settings)
    payment_text = (
        ", ".join(method["label"] for method in methods)
        if methods
        else "none"
    )

    embed.description = (
        (embed.description or "")
        + "\n\n"
        + f"**order ticket button** : {selected_text}\n"
        + f"**ticket receipt format** : configured\n"
        + f"**payment methods** : {payment_text}"
    )[:4096]

    return embed


confirmation.SetupView.__init__ = setup_init
confirmation.SetupView.status_embed = status_embed


class OrderConfirmButton(discord.ui.Button):
    def __init__(self, order, author_id, settings, guild):
        super().__init__(
            label="confirm",
            style=discord.ButtonStyle.secondary,
            custom_id=f"orderticket:confirm:{uuid.uuid4().hex}",
        )
        self.order = order
        self.author_id = author_id
        self.settings = settings

        confirmation.apply_label(
            self,
            settings.get("confirm_button"),
            guild,
            "confirm order",
        )

    async def callback(self, interaction):
        methods = payment_methods(self.settings)

        if not methods:
            await interaction.response.send_message(
                embed=confirmation.embeds.error(
                    "no payment methods are configured."
                ),
                ephemeral=True,
            )
            return

        if len(methods) == 1:
            await interaction.response.send_message(
                view=PaymentMethodView(
                    self.settings,
                    self.order,
                    self.author_id,
                    methods,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            view=PaymentMethodView(
                self.settings,
                self.order,
                self.author_id,
                methods,
            ),
            ephemeral=True,
        )


class PaymentMethodButton(discord.ui.Button):
    def __init__(self, settings, order, author_id, method):
        super().__init__(
            label=method["label"][:80],
            style=discord.ButtonStyle.secondary,
            custom_id=f"orderticket:payment:{method['key']}",
        )
        self.settings = settings
        self.order = order
        self.author_id = author_id
        self.method = method

    async def callback(self, interaction):
        text = self.method.get("text") or ""

        if not text:
            if self.method["key"] == "gcash":
                text = settings_gcash_text(
                    self.settings,
                    self.order,
                    self.author_id,
                    interaction.guild,
                )
            else:
                text = "payment information has not been configured."

        rendered = confirmation.render(
            text,
            self.order,
            self.author_id,
            interaction.guild,
        )

        await interaction.response.send_message(
            content=rendered[:2000],
            ephemeral=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def settings_gcash_text(settings, order, author_id, guild):
    text = settings.get("gcash_text") or ""
    return confirmation.render(
        text,
        order,
        author_id,
        guild,
    )


class PaymentMethodView(discord.ui.LayoutView):
    def __init__(self, settings, order, author_id, methods):
        super().__init__(timeout=300)

        methods = methods[:25]

        if not methods:
            return

        for start in range(0, len(methods), 5):
            row = discord.ui.ActionRow()
            for method in methods[start:start + 5]:
                row.add_item(
                    PaymentMethodButton(
                        settings,
                        order,
                        author_id,
                        method,
                    )
                )
            self.add_item(row)


class OrderTicketControls(discord.ui.ActionRow):
    def __init__(self, order, author_id, settings, guild):
        super().__init__()

        self.add_item(
            OrderConfirmButton(
                order,
                author_id,
                settings,
                guild,
            )
        )


class FinalOrderTicketView(discord.ui.LayoutView):
    def __init__(
        self,
        guild,
        ping,
        heading,
        welcome,
        answers,
        color,
        order,
        author_id,
        settings,
    ):
        super().__init__(timeout=None)

        self.guild = guild

        if ping:
            self.add_item(discord.ui.TextDisplay(ping[:2000]))

        box = discord.ui.Container(
            accent_colour=discord.Colour(color) if color else None
        )

        if heading:
            box.add_item(discord.ui.TextDisplay(heading))
            box.add_item(discord.ui.Separator())

        if welcome:
            box.add_item(discord.ui.TextDisplay(welcome[:2000]))
            box.add_item(
                discord.ui.Separator(
                    spacing=discord.SeparatorSpacing.large
                )
            )

        receipt = render_receipt(
            settings,
            order,
            author_id,
            guild,
        )

        box.add_item(discord.ui.TextDisplay(receipt))

        self.add_item(box)

        controls = ticket.TicketControls()
        controls.add_item(
            OrderConfirmButton(
                order,
                author_id,
                settings,
                guild,
            )
        )
        self.add_item(controls)


_original_create_ticket = ticket.create_ticket
_installed = False


async def create_ticket(interaction, button_data, answers):
    await _original_create_ticket(
        interaction,
        button_data,
        answers,
    )

    settings = receipt_settings(
        confirmation.settings_for(interaction.guild.id)
    )

    if button_data.get("key") != settings.get(TICKET_BUTTON_KEY):
        return

    owned = ticket.open_tickets_for(
        interaction.guild.id,
        interaction.user.id,
    )

    if not owned:
        return

    channel = interaction.guild.get_channel(owned[-1])

    if channel is None:
        return

    try:
        messages = [
            message
            async for message in channel.history(
                limit=10,
                oldest_first=False,
            )
        ]

        opening = next(
            (
                message
                for message in messages
                if message.author.id == interaction.client.user.id
            ),
            None,
        )

        if opening is None:
            return

        order = build_order(
            answers,
            interaction.user.id,
        )

        ticket_settings = ticket.get_config(
            interaction.guild.id
        ) or {}

        roles = ticket.staff_roles(
            interaction.guild,
            ticket_settings,
        )

        ping = (
            f"{interaction.user.mention} "
            + " ".join(role.mention for role in roles)
        ).strip()

        entry = ticket.tickets.get(channel.id)

        if entry is None:
            return

        number = entry["number"]

        view = FinalOrderTicketView(
            guild=interaction.guild,
            ping=ping,
            heading=(
                f"### Ticket {number:04d} - "
                f"{button_data['label']}"
            ),
            welcome=(
                button_data.get("welcome")
                or ticket.DEFAULT_BUTTON["welcome"]
            ),
            answers=answers,
            color=ticket_settings.get(
                "panel",
                {},
            ).get("color"),
            order=order,
            author_id=interaction.user.id,
            settings=settings,
        )

        await opening.edit(
            view=view,
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=roles or False,
            ),
        )

        entry["order_ticket"] = True
        entry["opening_message_id"] = opening.id
        ticket.save_tickets()

    except (discord.Forbidden, discord.HTTPException):
        log.exception(
            "Failed to attach order ticket view to %s.",
            channel.id,
        )


def install():
    global _installed

    if _installed:
        return

    ticket.create_ticket = create_ticket
    _installed = True


async def restore_views(bot):
    for channel_id, entry in list(ticket.tickets.items()):
        if not entry.get("order_ticket"):
            continue

        guild = bot.get_guild(entry.get("guild_id"))
        if guild is None:
            continue

        settings = receipt_settings(
            confirmation.settings_for(guild.id)
        )

        ticket_settings = ticket.get_config(guild.id) or {}
        button_data = next(
            (
                button
                for button in ticket_settings.get("buttons", [])
                if button.get("label") == entry.get("kind")
            ),
            None,
        )

        if button_data is None:
            continue

        opener = guild.get_member(entry["opener_id"])
        roles = ticket.staff_roles(
            guild,
            ticket_settings,
        )

        opener_text = opener.mention if opener else f"<@{entry['opener_id']}>"
        ping = (opener_text + " " + " ".join(role.mention for role in roles)).strip()

        order = build_order(
            entry.get("answers", []),
            entry["opener_id"],
        )

        view = FinalOrderTicketView(
            guild=guild,
            ping=ping,
            heading=(
                f"### Ticket {entry['number']:04d} - "
                f"{entry.get('kind', 'Ticket')}"
            ),
            welcome=(
                button_data.get("welcome")
                or ticket.DEFAULT_BUTTON["welcome"]
            ),
            answers=entry.get("answers", []),
            color=ticket_settings.get(
                "panel",
                {},
            ).get("color"),
            order=order,
            author_id=entry["opener_id"],
            settings=settings,
        )

        try:
            bot.add_view(
                view,
                message_id=entry.get("opening_message_id"),
            )
        except (ValueError, TypeError, discord.HTTPException):
            log.exception(
                "Failed to restore order ticket view for %s.",
                channel_id,
            )


class OrderTicket(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.restored = False
        install()

    @commands.Cog.listener()
    async def on_ready(self):
        if self.restored:
            return

        self.restored = True
        await restore_views(self.bot)


async def setup(bot):
    await bot.add_cog(OrderTicket(bot))
