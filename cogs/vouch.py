import discord
from discord import app_commands
from discord.ext import commands

import logging
import re

import embeds
import emojiutils
from storage import Store

log = logging.getLogger(__name__)

_store = Store("vouch.json")
config = _store.load()

DEFAULTS = {
    "channel_id": None,
    "keyword": "vouch",
    "remove_role_id": None,
    "add_role_id": None,
    "accept_emoji": "✅",
    "reject_emoji": "❌",
    "require_role_ids": [],
    "self_vouch": False,
    "enabled": True,
}

MENTION = re.compile(r"<@[!&]?\d{15,25}>")

def save_config():
    _store.save(config)

def get_config(guild_id):
    return config.get(str(guild_id))

def ensure_config(guild_id):
    settings = config.setdefault(str(guild_id), {})
    for field, value in DEFAULTS.items():
        settings.setdefault(field, value)
    return settings

def is_ready(settings):
    return bool(settings and settings.get("channel_id"))

def keyword_of(settings):
    return (settings.get("keyword") or DEFAULTS["keyword"]).strip()

def keyword_parts(settings):
    raw = keyword_of(settings)
    tokens = MENTION.findall(raw)
    required = [(token, int(re.sub(r"\D", "", token))) for token in tokens]
    text = " ".join(MENTION.sub(" ", raw).split())
    return text, required

def find_mentionable(name, guild):
    wanted = name.lower()
    for role in guild.roles:
        if role.name.lower() == wanted and not role.is_default():
            return role
    for member in guild.members:
        if wanted in (member.name.lower(), member.display_name.lower()):
            return member
    return None

def resolve_mentions(raw, guild):
    if guild is None:
        return raw, []

    kept = []

    def stash(match):
        kept.append(match.group(0))
        return f"\x00{len(kept) - 1}\x00"

    text = MENTION.sub(stash, raw)
    unknown = []

    def swap_id(match):
        value = int(match.group(0))
        if guild.get_role(value) is not None:
            return f"<@&{value}>"
        if guild.get_member(value) is not None:
            return f"<@{value}>"
        return match.group(0)

    text = MENTION.sub(stash, re.sub(r"\b\d{15,25}\b", swap_id, text))

    def swap_name(match):
        raw_name = match.group(1)
        words = raw_name.split()
        for size in range(len(words), 0, -1):
            name = " ".join(words[:size])
            if name.lower() in ("everyone", "here"):
                return match.group(0)
            target = find_mentionable(name, guild)
            if target is not None:
                return target.mention + raw_name[len(name):]
        unknown.append(words[0] if words else raw_name)
        return match.group(0)

    text = re.sub(r"@([^\s@][^@]{0,31})", swap_name, text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], text)
    return text, unknown

def text_hit(content, text):
    if not text:
        return True
    return re.search(rf"(?<!\w){re.escape(text.lower())}(?!\w)", content.lower()) is not None

def mentioned_ids(message):
    return set(message.raw_mentions) | set(message.raw_role_mentions)

def is_vouch(message, settings):
    text, required = keyword_parts(settings)
    if not text_hit(message.content, text):
        return False

    found = mentioned_ids(message)
    wanted = settings.get("require_role_ids") or []

    if required and not all(target in found for _, target in required):
        return False
    if wanted and not any(role_id in found for role_id in wanted):
        return False
    if required or wanted:
        return True

    if settings.get("self_vouch"):
        return bool(found)
    return any(target != message.author.id for target in found)

NAME_TOKEN = re.compile(r"^:?([A-Za-z0-9_~]{2,32}):?$")

def find_emoji(name, guild, client):
    base = re.sub(r"~\d+$", "", name).lower()
    if guild is not None:
        for emoji in guild.emojis:
            if emoji.name.lower() == base:
                return emoji
    if client is not None:
        for emoji in client.emojis:
            if emoji.name.lower() == base:
                return emoji
    return None

def resolve_emoji(raw, guild, client):
    text = (raw or "").strip()
    if not text:
        return None, "give me an emoji."

    if text.startswith("<"):
        if not emojiutils.CUSTOM_EMOJI.match(text):
            return None, (
                "that custom emoji is not in a form i can read. type a backslash "
                "before it and paste what discord gives you, like "
                "`<:name:123456789012345678>`."
            )
        emoji_id = int(text.rstrip(">").rsplit(":", 1)[-1])
        if client is not None and client.get_emoji(emoji_id) is None:
            return None, "i can't use that emoji. it has to be from a server i'm also in."
        return text, None

    named = NAME_TOKEN.match(text)
    if named:
        found = find_emoji(named.group(1), guild, client)
        if found is not None:
            return str(found), None
        if text.startswith(":") or text.endswith(":"):
            return None, f"i can't find an emoji named `{named.group(1)}` in any server i'm in."

    return emojiutils.parse(text, guild)

def emoji_for(settings, key):
    raw = settings.get(key) or DEFAULTS[key]
    return emojiutils.to_partial(raw) or raw

async def react(message, settings, accepted):
    try:
        await message.add_reaction(emoji_for(settings, "accept_emoji" if accepted else "reject_emoji"))
    except (discord.Forbidden, discord.HTTPException, TypeError):
        pass

async def apply_roles(member, settings):
    guild = member.guild
    top = guild.me.top_role
    remove = guild.get_role(settings.get("remove_role_id") or 0)
    add = guild.get_role(settings.get("add_role_id") or 0)

    try:
        if remove is not None and remove in member.roles and remove < top:
            await member.remove_roles(remove, reason="vouch posted")
        if add is not None and add not in member.roles and add < top:
            await member.add_roles(add, reason="vouch posted")
    except (discord.Forbidden, discord.HTTPException):
        pass

def role_text(guild, role_id):
    role = guild.get_role(role_id) if role_id else None
    return role.mention if role else "none"

class RemoveRoleSelect(discord.ui.RoleSelect):
    def __init__(self, panel):
        super().__init__(
            placeholder="Role to remove on a valid vouch",
            min_values=0,
            max_values=1,
            row=0,
        )
        self.panel = panel

    async def callback(self, interaction):
        self.panel.settings["remove_role_id"] = self.values[0].id if self.values else None
        save_config()
        await self.panel.refresh(interaction)

class AddRoleSelect(discord.ui.RoleSelect):
    def __init__(self, panel):
        super().__init__(
            placeholder="Role to give on a valid vouch",
            min_values=0,
            max_values=1,
            row=1,
        )
        self.panel = panel

    async def callback(self, interaction):
        self.panel.settings["add_role_id"] = self.values[0].id if self.values else None
        save_config()
        await self.panel.refresh(interaction)

class RequireRoleSelect(discord.ui.RoleSelect):
    def __init__(self, panel):
        super().__init__(
            placeholder="Role the message has to mention (optional)",
            min_values=0,
            max_values=5,
            row=3,
        )
        self.panel = panel

    async def callback(self, interaction):
        self.panel.settings["require_role_ids"] = [role.id for role in self.values]
        save_config()
        await self.panel.refresh(interaction)

class VouchChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, panel):
        super().__init__(
            placeholder="Channel to watch",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=0,
            max_values=1,
            row=2,
        )
        self.panel = panel

    async def callback(self, interaction):
        self.panel.settings["channel_id"] = self.values[0].id if self.values else None
        save_config()
        await self.panel.refresh(interaction)

class KeywordModal(discord.ui.Modal, title="Vouch Keyword"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.f_keyword = discord.ui.TextInput(
            label="Keyword",
            default=keyword_of(panel.settings),
            placeholder="vouch, or vouch @role, or vouch @member",
            max_length=200,
            required=True,
        )
        self.add_item(self.f_keyword)

    async def on_submit(self, interaction):
        word = self.f_keyword.value.strip()
        if not word:
            await interaction.response.send_message(
                embed=embeds.error("the keyword can't be blank."), ephemeral=True
            )
            return

        word, unknown = resolve_mentions(word, interaction.guild)
        if unknown:
            await interaction.response.send_message(
                embed=embeds.error(
                    "i can't find a role or member called "
                    + ", ".join(f"`@{name}`" for name in unknown)
                    + ". check the spelling, or paste the id instead."
                ),
                ephemeral=True,
            )
            return

        self.panel.settings["keyword"] = word
        save_config()
        await self.panel.refresh(interaction)

class EmojiModal(discord.ui.Modal, title="Vouch Reactions"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.f_accept = discord.ui.TextInput(
            label="Accepted reaction",
            default=panel.settings.get("accept_emoji") or DEFAULTS["accept_emoji"],
            placeholder="✅ or :rightteffy: or <:name:123456789012345678>",
            max_length=100,
            required=True,
        )
        self.f_reject = discord.ui.TextInput(
            label="Rejected reaction",
            default=panel.settings.get("reject_emoji") or DEFAULTS["reject_emoji"],
            placeholder="❌ or :wrongteffy: or <:name:123456789012345678>",
            max_length=100,
            required=True,
        )
        self.add_item(self.f_accept)
        self.add_item(self.f_reject)

    async def on_submit(self, interaction):
        accept, problem = resolve_emoji(self.f_accept.value, interaction.guild, interaction.client)
        if problem:
            await interaction.response.send_message(
                embed=embeds.error(problem, title="Bad accepted reaction"), ephemeral=True
            )
            return

        reject, problem = resolve_emoji(self.f_reject.value, interaction.guild, interaction.client)
        if problem:
            await interaction.response.send_message(
                embed=embeds.error(problem, title="Bad rejected reaction"), ephemeral=True
            )
            return

        self.panel.settings["accept_emoji"] = accept or DEFAULTS["accept_emoji"]
        self.panel.settings["reject_emoji"] = reject or DEFAULTS["reject_emoji"]
        save_config()
        await self.panel.refresh(interaction)

class VouchSetupView(discord.ui.View):
    def __init__(self, ctx, settings):
        super().__init__(timeout=600)
        self.ctx = ctx
        self.settings = settings
        self.add_item(RemoveRoleSelect(self))
        self.add_item(AddRoleSelect(self))
        self.add_item(VouchChannelSelect(self))
        self.add_item(RequireRoleSelect(self))
        self.sync_toggle()

    async def interaction_check(self, interaction):
        return interaction.user.id == self.ctx.author.id

    def sync_toggle(self):
        self.toggle.label = "Turn Off" if self.settings.get("enabled") else "Turn On"
        self.self_toggle.label = (
            "Self Vouch: on" if self.settings.get("self_vouch") else "Self Vouch: off"
        )

    def summary(self):
        guild = self.ctx.guild
        channel_id = self.settings.get("channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        text, required = keyword_parts(self.settings)

        state = "on" if self.settings.get("enabled") else "off"
        if not is_ready(self.settings):
            state = "off (no channel set)"

        word = f"`{text}`" if text else "`anything`"
        wanted = [f"<@&{role_id}>" for role_id in self.settings.get("require_role_ids") or []]
        targets = [token for token, _ in required]

        if targets or wanted:
            must = " ".join(targets + wanted)
            rule = f"a message counts when it has {word} and mentions {must}"
        elif self.settings.get("self_vouch"):
            rule = f"a message counts when it has {word} and mentions anyone"
            must = "anyone"
        else:
            rule = f"a message counts when it has {word} and mentions someone other than the sender"
            must = "anyone"

        return embeds.build(
            f"**Channel** - {channel.mention if channel else 'none'}\n"
            f"**Keyword** - {word}\n"
            f"**Must mention** - {must}\n"
            f"**Removes** - {role_text(guild, self.settings.get('remove_role_id'))}\n"
            f"**Gives** - {role_text(guild, self.settings.get('add_role_id'))}\n"
            f"**Accepted** - {self.settings.get('accept_emoji')}\n"
            f"**Rejected** - {self.settings.get('reject_emoji')}\n"
            f"**Status** - {state}\n\n"
            f"{rule}. messages from bots are ignored.",
            title="Vouch Setup",
        )

    async def refresh(self, interaction):
        self.sync_toggle()
        await interaction.response.edit_message(embed=self.summary(), view=self)

    @discord.ui.button(label="Keyword", style=discord.ButtonStyle.secondary, row=4)
    async def keyword(self, interaction, button):
        await interaction.response.send_modal(KeywordModal(self))

    @discord.ui.button(label="Reactions", style=discord.ButtonStyle.secondary, row=4)
    async def reactions(self, interaction, button):
        await interaction.response.send_modal(EmojiModal(self))

    @discord.ui.button(label="Self Vouch: off", style=discord.ButtonStyle.secondary, row=4)
    async def self_toggle(self, interaction, button):
        self.settings["self_vouch"] = not self.settings.get("self_vouch")
        save_config()
        await self.refresh(interaction)

    @discord.ui.button(label="Turn Off", style=discord.ButtonStyle.secondary, row=4)
    async def toggle(self, interaction, button):
        self.settings["enabled"] = not self.settings.get("enabled")
        save_config()
        await self.refresh(interaction)

    @discord.ui.button(label="Done", style=discord.ButtonStyle.primary, row=4)
    async def done(self, interaction, button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=self.summary(), view=self)
        self.stop()

class Vouch(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        perms = ctx.author.guild_permissions
        if perms.administrator or perms.manage_guild:
            return True
        raise commands.MissingPermissions(["manage_guild"])

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=embeds.error("you need Manage Server to use this."))
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send(embed=embeds.error("this command only works in a server."))
        else:
            log.exception("Unhandled error in %s", ctx.command, exc_info=error)
            await embeds.send(ctx, embeds.error("something broke on my end. it has been logged."))

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return

        settings = get_config(message.guild.id)
        if not settings or not settings.get("enabled"):
            return
        if message.channel.id != settings.get("channel_id"):
            return

        accepted = is_vouch(message, settings)
        await react(message, settings, accepted)

        if accepted and isinstance(message.author, discord.Member):
            await apply_roles(message.author, settings)

    @commands.hybrid_group(
        name="vouch",
        description="Vouch channel tools.",
        invoke_without_command=True,
    )
    @app_commands.default_permissions(manage_guild=True)
    @commands.guild_only()
    async def vouch(self, ctx):
        await ctx.send(embed=embeds.notice("use `/vouch setup` to set the vouch channel up."))

    @vouch.command(name="setup", description="Set up the vouch channel, keyword, roles and reactions.")
    @app_commands.default_permissions(manage_guild=True)
    @commands.guild_only()
    async def vouch_setup(self, ctx):
        settings = ensure_config(ctx.guild.id)
        save_config()
        view = VouchSetupView(ctx, settings)
        await ctx.send(embed=view.summary(), view=view)

async def setup(bot):
    await bot.add_cog(Vouch(bot))