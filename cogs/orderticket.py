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

DEFAULT_ORDER_FORM = {
    "title": "order form ♡",
    "description": "please fill in the details below.",
    "fields": [
        {
            "key": "user",
            "label": "♡ : discord username",
            "placeholder": "ex. @username",
            "style": "short",
            "required": True,
        },
        {
            "key": "item",
            "label": "♡ : exact name of order",
            "placeholder": "ex. gamepass ct, intro boosh, kanba",
            "style": "short",
            "required": True,
        },
        {
            "key": "quantity",
            "label": "♡ : quantity",
            "placeholder": "ex. 1x, 2pcs, 1m",
            "style": "short",
            "required": True,
        },
        {
            "key": "payment",
            "label": "♡ : payment method",
            "placeholder": "gcash, maya, paypal, gotyme",
            "style": "short",
            "required": True,
        },
    ],
}

ORDER_FORM_KEY = "ticket_order_form"
ORDER_ENABLED_KEY = "ticket_order_enabled"
ORIGINAL_QUESTIONS_KEY = "ticket_order_original_questions"

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
        key = question if question in {"item", "price", "quantity", "notes", "user"} else None
        if key == "user":
            continue
        if key == "payment":
            leftovers.append(f"payment method : {answer}")
            continue
        if key in order and not order[key]:
            order[key] = answer
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

    order["item"] = order["item"] or "not provided"
    order["price"] = order["price"] or "not provided"
    order["quantity"] = order["quantity"] or "not provided"
    order["notes"] = "\n".join(leftovers) or "none"
    return order


def receipt_settings(settings):
    settings.setdefault(RECEIPT_FORMAT_KEY, DEFAULT_RECEIPT_FORMAT)
    settings.setdefault(PAYMENTS_KEY, [dict(x) for x in DEFAULT_PAYMENTS])
    if ORDER_FORM_KEY not in settings:
        settings[ORDER_FORM_KEY] = {
            "title": DEFAULT_ORDER_FORM["title"],
            "description": DEFAULT_ORDER_FORM["description"],
            "fields": [dict(field) for field in DEFAULT_ORDER_FORM["fields"]],
        }
    else:
        form = settings[ORDER_FORM_KEY]
        form.setdefault("title", DEFAULT_ORDER_FORM["title"])
        form.setdefault("description", DEFAULT_ORDER_FORM["description"])
        form.setdefault("fields", [dict(field) for field in DEFAULT_ORDER_FORM["fields"]])
        for index, field in enumerate(form["fields"]):
            if index >= len(DEFAULT_ORDER_FORM["fields"]):
                break
            base = DEFAULT_ORDER_FORM["fields"][index]
            field.setdefault("key", base["key"])
            field.setdefault("label", base["label"])
            field.setdefault("placeholder", base["placeholder"])
            field.setdefault("style", base["style"])
            field.setdefault("required", base["required"])
    return settings


def order_form(settings):
    receipt_settings(settings)
    return settings[ORDER_FORM_KEY]


def order_field_map(settings):
    return {field.get("key"): field for field in order_form(settings).get("fields", [])}


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


class OrderFormModal(discord.ui.Modal):
    def __init__(self, setup_view):
        self.setup_view = setup_view
        form = order_form(setup_view.settings)
        super().__init__(title="Order Form Design")
        self.title_field = discord.ui.TextInput(
            label="Form title",
            default=form.get("title", "order form ♡")[:45],
            max_length=45,
            required=True,
        )
        self.description_field = discord.ui.TextInput(
            label="Form description",
            default=form.get("description", "")[:400],
            style=discord.TextStyle.paragraph,
            max_length=400,
            required=False,
        )
        self.add_item(self.title_field)
        self.add_item(self.description_field)

    async def on_submit(self, interaction):
        form = order_form(self.setup_view.settings)
        form["title"] = self.title_field.value.strip() or "order form ♡"
        form["description"] = self.description_field.value.strip()
        confirmation.save_config()
        await interaction.response.edit_message(
            embed=self.setup_view.status_embed(),
            view=self.setup_view,
        )


class OrderFieldModal(discord.ui.Modal):
    def __init__(self, setup_view, index):
        self.setup_view = setup_view
        self.index = index
        form = order_form(setup_view.settings)
        existing = form.get("fields", [])[index] if index < len(form.get("fields", [])) else {}
        super().__init__(title=f"Order Field {index + 1}")
        self.label_field = discord.ui.TextInput(
            label="Field label",
            default=existing.get("label", "")[:45],
            max_length=45,
            required=True,
        )
        self.placeholder_field = discord.ui.TextInput(
            label="Placeholder",
            default=existing.get("placeholder", "")[:100],
            max_length=100,
            required=False,
        )
        self.key_field = discord.ui.TextInput(
            label="Field key",
            default=existing.get("key", "")[:45],
            placeholder="item, price, quantity, notes, payment, user",
            max_length=45,
            required=True,
        )
        self.style_field = discord.ui.TextInput(
            label="Box style",
            default=existing.get("style", "short")[:10],
            placeholder="short or paragraph",
            max_length=10,
            required=True,
        )
        self.required_field = discord.ui.TextInput(
            label="Required",
            default="yes" if existing.get("required", True) else "no",
            placeholder="yes or no",
            max_length=3,
            required=True,
        )
        for field in (
            self.label_field,
            self.placeholder_field,
            self.key_field,
            self.style_field,
            self.required_field,
        ):
            self.add_item(field)

    async def on_submit(self, interaction):
        style = self.style_field.value.strip().lower()
        if style not in {"short", "paragraph"}:
            await interaction.response.send_message(
                "box style must be `short` or `paragraph`.",
                ephemeral=True,
            )
            return
        required = self.required_field.value.strip().lower()
        if required not in {"yes", "no"}:
            await interaction.response.send_message(
                "required must be `yes` or `no`.",
                ephemeral=True,
            )
            return
        form = order_form(self.setup_view.settings)
        fields = form.setdefault("fields", [])
        while len(fields) <= self.index:
            fields.append({
                "key": f"field{len(fields) + 1}",
                "label": f"field {len(fields) + 1}",
                "placeholder": "",
                "style": "short",
                "required": True,
            })
        fields[self.index] = {
            "key": self.key_field.value.strip().lower().replace(" ", "_"),
            "label": self.label_field.value.strip(),
            "placeholder": self.placeholder_field.value.strip(),
            "style": style,
            "required": required == "yes",
        }
        confirmation.save_config()
        await interaction.response.edit_message(
            embed=self.setup_view.status_embed(),
            view=self.setup_view,
        )


class OrderFormDesignView(discord.ui.View):
    def __init__(self, setup_view):
        super().__init__(timeout=300)
        self.setup_view = setup_view
        for index, field in enumerate(order_form(setup_view.settings).get("fields", [])[:5]):
            self.add_item(OrderFieldButton(setup_view, index, field))

    @discord.ui.button(label="Form title / intro", style=discord.ButtonStyle.secondary, row=1)
    async def form_text(self, interaction, button):
        await interaction.response.send_modal(OrderFormModal(self.setup_view))


class OrderFieldButton(discord.ui.Button):
    def __init__(self, setup_view, index, field):
        super().__init__(
            label=f"Field {index + 1}",
            style=discord.ButtonStyle.secondary,
            row=0 if index < 5 else 1,
        )
        self.setup_view = setup_view
        self.index = index
        self.field = field

    async def callback(self, interaction):
        await interaction.response.send_modal(
            OrderFieldModal(self.setup_view, self.index)
        )


class OrderFlowSelect(discord.ui.Select):
    def __init__(self, setup_view, button_data):
        self.setup_view = setup_view
        self.button_data = button_data
        current = "order" if button_data.get(ORDER_ENABLED_KEY) else "normal"
        options = [
            discord.SelectOption(
                label="Normal Ticket",
                value="normal",
                description="keep the button opening instantly or using its normal questions.",
                default=current == "normal",
            ),
            discord.SelectOption(
                label="Order",
                value="order",
                description="automatically apply the order form questions.",
                default=current == "order",
            ),
        ]
        super().__init__(
            placeholder="select the ticket flow",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction):
        value = self.values[0]

        if value == "order":
            self.setup_view.settings[TICKET_BUTTON_KEY] = self.button_data["key"]
            enable_order_button(
                interaction.guild,
                self.button_data,
                self.setup_view.settings,
            )
            confirmation.save_config()
            await interaction.response.send_modal(
                OrderQuestionsSetupModal(
                    self.setup_view,
                    self.button_data,
                )
            )
            return

        disable_order_button(
            self.button_data,
            self.setup_view.settings,
        )
        if self.setup_view.settings.get(TICKET_BUTTON_KEY) == self.button_data["key"]:
            self.setup_view.settings.pop(TICKET_BUTTON_KEY, None)

        confirmation.save_config()

        await interaction.response.edit_message(
            content=f"`{self.button_data['label']}` is now using the **normal** flow.",
            view=None,
        )
        await self.setup_view.refresh()


class OrderFlowView(discord.ui.View):
    def __init__(self, setup_view, button_data):
        super().__init__(timeout=300)
        self.setup_view = setup_view
        self.button_data = button_data
        self.add_item(OrderFlowSelect(setup_view, button_data))


def enable_order_button(guild, button_data, settings):
    if ORIGINAL_QUESTIONS_KEY not in button_data:
        button_data[ORIGINAL_QUESTIONS_KEY] = [dict(q) for q in button_data.get("questions", [])]
    form = order_form(settings)
    button_data[ORDER_ENABLED_KEY] = True
    button_data["questions"] = [
        {
            "label": field["label"][:45],
            "key": field.get("key", "")[:45],
            "placeholder": field.get("placeholder", "")[:100],
            "style": field.get("style", "short"),
            "required": bool(field.get("required", True)),
        }
        for field in form.get("fields", [])[:5]
        if field.get("label")
    ]
    ticket.save_config()


def disable_order_button(button_data, settings):
    button_data[ORDER_ENABLED_KEY] = False
    if ORIGINAL_QUESTIONS_KEY in button_data:
        button_data["questions"] = [dict(q) for q in button_data.pop(ORIGINAL_QUESTIONS_KEY)]
    else:
        button_data["questions"] = []
    ticket.save_config()


class OrderQuestionsSetupModal(discord.ui.Modal):
    def __init__(self, setup_view, button_data):
        self.setup_view = setup_view
        self.button_data = button_data
        existing = button_data.get("questions", [])

        super().__init__(title="Order Questions")

        defaults = [
            ("Discord Username", "ex. @username"),
            ("Exact Name of Order", "ex. gamepass ct, intro boosh, kanba"),
            ("Quantity", "ex. 1x, 2pcs, 1m"),
            ("Payment Method", "ex. gcash, maya, paypal, gotyme"),
            ("Details / Notes", "optional details about the order"),
        ]

        self.fields = []

        for index, (default_label, placeholder) in enumerate(defaults):
            current = existing[index] if index < len(existing) else {}
            field = discord.ui.TextInput(
                label=f"Question {index + 1}",
                default=current.get("label", default_label)[:45],
                placeholder=placeholder[:100],
                required=False,
                max_length=45,
            )
            self.fields.append(field)
            self.add_item(field)

    async def on_submit(self, interaction):
        questions = []

        keys = [
            "user",
            "item",
            "quantity",
            "payment",
            "notes",
        ]

        for key, field in zip(keys, self.fields):
            label = field.value.strip()
            if not label:
                continue

            questions.append(
                {
                    "key": key,
                    "label": label[:45],
                    "placeholder": "",
                    "style": "short",
                    "required": True,
                }
            )

        if not questions:
            await interaction.response.send_message(
                "at least one order question is required.",
                ephemeral=True,
            )
            return

        self.button_data["questions"] = questions
        self.button_data[ORDER_ENABLED_KEY] = True
        ticket.save_config()
        confirmation.save_config()

        await interaction.response.send_message(
            f"`{self.button_data['label']}` is now using the **order** flow with {len(questions)} question(s).",
            ephemeral=True,
        )
        await self.setup_view.refresh()


class OrderTicketQuestionModal(discord.ui.Modal):
    def __init__(self, button_data):
        super().__init__(title=button_data["label"][:45])
        self.button_data = button_data
        self.inputs = []

        for question in button_data.get("questions", [])[:5]:
            style = (
                discord.TextStyle.paragraph
                if question.get("style") == "paragraph"
                else discord.TextStyle.short
            )
            field = discord.ui.TextInput(
                label=question["label"][:45],
                placeholder=question.get("placeholder", "")[:100] or None,
                style=style,
                required=bool(question.get("required", True)),
                max_length=1000,
            )
            self.inputs.append(
                (
                    question.get("key") or question["label"],
                    question["label"],
                    field,
                )
            )
            self.add_item(field)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        answers = []

        for key, label, field in self.inputs:
            answers.append((key, field.value.strip()))

        await ticket.create_ticket(
            interaction,
            self.button_data,
            answers,
        )


_original_ticket_open_callback = ticket.TicketOpenButton.callback
_original_ticket_question_modal = ticket.TicketQuestionModal


async def order_ticket_open_callback(self, interaction):
    settings = ticket.get_config(interaction.guild.id)

    if not ticket.is_configured(settings):
        await interaction.response.send_message(
            embed=confirmation.embeds.error(
                "the ticket system isn't finished being set up."
            ),
            ephemeral=True,
        )
        return

    button_data = ticket.find_button(
        settings,
        self.button_key,
    )

    if button_data is None:
        await interaction.response.send_message(
            embed=confirmation.embeds.error(
                "this button is no longer configured."
            ),
            ephemeral=True,
        )
        return

    if button_data.get(ORDER_ENABLED_KEY):
        await interaction.response.send_modal(
            OrderTicketQuestionModal(button_data)
        )
        return

    await _original_ticket_open_callback(
        self,
        interaction,
    )


ticket.TicketOpenButton.callback = order_ticket_open_callback

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

            await interaction.response.edit_message(
                content=(
                    f"`{button_data['label']}` selected. choose the flow below."
                ),
                view=OrderFlowView(self.setup_view, button_data),
            )
            return

        confirmation.save_config()

        await interaction.response.edit_message(
            content="order ticket button selection cleared.",
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
            "choose a ticket button, then choose whether it stays normal or uses the order flow.",
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


class OrderFormSetupButton(discord.ui.Button):
    def __init__(self, setup_view):
        super().__init__(
            label="order form",
            style=discord.ButtonStyle.secondary,
            row=4,
        )
        self.setup_view = setup_view

    async def callback(self, interaction):
        form = order_form(self.setup_view.settings)
        fields = form.get("fields", [])
        lines = [
            f"**{form.get('title', 'order form ♡')}**",
            form.get("description") or "no intro text",
            "",
            "**fields**",
        ]
        for index, field in enumerate(fields[:5]):
            style = field.get("style", "short")
            required = "required" if field.get("required", True) else "optional"
            lines.append(
                f"{index + 1}. **{field.get('label', 'field')}** · {style} · {required}"
            )
        await interaction.response.send_message(
            embed=discord.Embed(description="\n".join(lines)[:4096]),
            view=OrderFormDesignView(self.setup_view),
            ephemeral=True,
        )


class PaymentSetupButton(discord.ui.Button):
    def __init__(self, setup_view):
        super().__init__(
            label="payment methods",
            style=discord.ButtonStyle.secondary,
            row=4,
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
_original_setup_refresh = confirmation.SetupView.refresh
_original_status_embed = confirmation.SetupView.status_embed


def add_order_setup_controls(view):
    existing = {type(item) for item in view.children}

    if TicketButtonSetupButton not in existing:
        view.add_item(TicketButtonSetupButton(view))

    if ReceiptFormatSetupButton not in existing:
        view.add_item(ReceiptFormatSetupButton(view))

    if PaymentSetupButton not in existing:
        view.add_item(PaymentSetupButton(view))

    if OrderFormSetupButton not in existing:
        view.add_item(OrderFormSetupButton(view))


def setup_init(self, ctx, settings):
    _original_setup_init(self, ctx, settings)
    receipt_settings(settings)
    add_order_setup_controls(self)


async def setup_refresh(self, interaction=None):
    add_order_setup_controls(self)
    return await _original_setup_refresh(self, interaction)


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
    form = order_form(self.settings)
    form_fields = ", ".join(
        field.get("key", "field")
        for field in form.get("fields", [])[:5]
    ) or "none"

    embed.description = (
        (embed.description or "")
        + "\n\n"
        + f"**order ticket button** : {selected_text}\n"
        + f"**ticket receipt format** : configured\n"
        + f"**order form** : {form.get('title', 'order form ♡')}\n"
        + f"**order fields** : {form_fields}\n"
        + f"**payment methods** : {payment_text}"
    )[:4096]

    return embed


confirmation.SetupView.__init__ = setup_init
confirmation.SetupView.refresh = setup_refresh
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
