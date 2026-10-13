import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import discord
import aiohttp
import requests
from aiohttp import web
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ytb-ticket-bot")

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
INGESTION_ENDPOINT = os.getenv("WEBSITE_INGESTION_ENDPOINT", "").strip()
INGESTION_SECRET = os.getenv("INGESTION_SECRET", "").strip()
TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID", "0"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))
TICKET_CATEGORY_NAME = os.getenv("TICKET_CATEGORY_NAME", "").strip()
BLACKLIST_ROLE_ID = int(os.getenv("BLACKLIST_ROLE_ID", "1559151386300518472"))
MEMBER_ROLE_ID = int(os.getenv("MEMBER_ROLE_ID", "1555187430833786881"))
FALLBACK_DIR = Path(os.getenv("FALLBACK_DIR", "./fallback_logs"))
STAFF_ROLE_IDS = {
    1555186239848845312,
    1555186908081029220,
    1555188214866772058,
    1555188619935875143,
    1555188958550564985,
}
HEALTH_PORT = int(os.getenv("PORT", "8080"))
SYSTEM_BOT_ROLE_IDS = {1555263295244468297, 1555363780442202186}
ICON_URL = os.getenv(
    "ICON_URL",
    "https://media.discordapp.net/attachments/1540579548427583558/1555262609983414443/asdas.png?format=webp&quality=lossless&width=640&height=640",
)
BANNER_URL = os.getenv(
    "BANNER_URL",
    "https://media.discordapp.net/attachments/1540579548427583558/1555262757065064519/bc12e32a-7d92-4a84-b19d-d0b0bb274d3e.png?format=webp&quality=lossless",
)

URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".apng")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True


def is_staff(member: discord.Member) -> bool:
    return bool({role.id for role in member.roles} & STAFF_ROLE_IDS)


def clean_channel_part(value: str, max_len: int = 48) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return (value or "user")[:max_len]


def ticket_embed(title: str, description: str, color: discord.Color) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
    embed.set_thumbnail(url=ICON_URL)
    return embed


def setup_embed() -> discord.Embed:
    description = (
        "**Y.T.B MM Service & Support**\n\n"
        "**Y.T.B MM SERVICE REQUIREMENTS:**\n"
        "Both traders must agree to use a Y.T.B MM.\n"
        "Provide the complete trade details.\n"
        "Do not send items or payment before the MM is assigned.\n"
        "Follow the MM’s indication throughout the trade.\n\n"
        "**MM REQUEST:**\n"
        "Click the button below to request a Y.T.B Middleman. Do not trust anyone claiming to be a Y.T.B MM unless they are verified by the server.\n\n"
        "**Report Scam:**\n"
        "Been scammed? Open a report ticket immediately with proof and details."
    )
    embed = ticket_embed("Y.T.B Middleman & Scam Support", description, discord.Color.blurple())
    embed.set_image(url=BANNER_URL)
    embed.set_footer(text="Y.T.B Support • Use the buttons below")
    return embed


async def resolve_member(guild: discord.Guild, raw: str) -> discord.Member | None:
    raw = raw.strip()
    mention = re.fullmatch(r"<@!?(\d+)>", raw)
    numeric = mention.group(1) if mention else raw if raw.isdigit() else None
    if numeric:
        member = guild.get_member(int(numeric))
        if member:
            return member
        try:
            return await guild.fetch_member(int(numeric))
        except (discord.NotFound, discord.HTTPException):
            return None
    lowered = raw.casefold()
    return discord.utils.find(
        lambda m: m.name.casefold() == lowered or m.display_name.casefold() == lowered or str(m).casefold() == lowered,
        guild.members,
    )


def overwrite_for_ticket(guild: discord.Guild, owner: discord.Member, trader2: discord.Member | None) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
        owner: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
    }
    if trader2 and trader2.id != owner.id:
        overwrites[trader2] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
    for role in guild.roles:
        if role.id in STAFF_ROLE_IDS:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
    return overwrites


class TicketControlView(discord.ui.View):
    def __init__(self, *, timeout=None):
        super().__init__(timeout=timeout)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.primary, custom_id="ticket:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("Only authorized Y.T.B staff can claim tickets.", ephemeral=True)
            return
        message = interaction.message
        if not message or not message.embeds:
            await interaction.response.send_message("Ticket message could not be updated.", ephemeral=True)
            return
        embed = message.embeds[0]
        marker = f"\n\n**Claimed By Middleman:** {interaction.user.mention}"
        if "Claimed By Middleman:" not in (embed.description or ""):
            embed.description = (embed.description or "") + marker
        button.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(f"Ticket ownership changed to {interaction.user.mention}.", ephemeral=True)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("Only authorized Y.T.B staff can close tickets.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("This is not a text ticket channel.", ephemeral=True)
            return
        payload = await gather_media(channel, interaction.user)
        uploaded = await asyncio.to_thread(send_media_payload, payload)
        status = "uploaded to the website" if uploaded else "saved to the local fallback log"
        await interaction.followup.send(f"Ticket media {status}. This channel will be deleted in 5 seconds.", ephemeral=True)
        await channel.send(f"Ticket media {status}. Closing this ticket in **5 seconds**...")
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Ticket closed by {interaction.user} ({interaction.user.id})")
        except discord.NotFound:
            log.info("Ticket channel %s was already deleted", channel.id)
        except discord.HTTPException:
            log.exception("Could not delete ticket channel %s", channel.id)


class MainPanelView(discord.ui.View):
    def __init__(self, *, timeout=None):
        super().__init__(timeout=timeout)

    @discord.ui.button(label="Request Middleman", style=discord.ButtonStyle.primary, custom_id="req_mm")
    async def request_mm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(MMRequestModal())

    @discord.ui.button(label="Report Scam", style=discord.ButtonStyle.danger, custom_id="rep_scam")
    async def report_scam(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScamReportModal())


class MMRequestModal(discord.ui.Modal, title="Request a Y.T.B Middleman"):
    trader2 = discord.ui.TextInput(label="Trader 2 (Username or User ID)", required=True, max_length=100)
    trader1_offer = discord.ui.TextInput(label="Trader 1 Item/Offer", required=True, style=discord.TextStyle.paragraph, max_length=1000)
    trader2_offer = discord.ui.TextInput(label="Trader 2 Item/Offer", required=True, style=discord.TextStyle.paragraph, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        await create_mm_ticket(interaction, str(self.trader2), str(self.trader1_offer), str(self.trader2_offer))


class ScamReportModal(discord.ui.Modal, title="Report a Scam"):
    scammer = discord.ui.TextInput(label="Scammer Username / ID / Profile Link", required=True, max_length=300)
    details = discord.ui.TextInput(label="Describe what happened & provide details", required=True, style=discord.TextStyle.paragraph, max_length=4000)

    async def on_submit(self, interaction: discord.Interaction):
        await create_scam_ticket(interaction, str(self.scammer), str(self.details))


async def find_category(guild: discord.Guild) -> discord.CategoryChannel | None:
    if TICKET_CATEGORY_ID:
        category = guild.get_channel(TICKET_CATEGORY_ID)
        if isinstance(category, discord.CategoryChannel):
            return category
    if TICKET_CATEGORY_NAME:
        return discord.utils.find(lambda c: c.name == TICKET_CATEGORY_NAME, guild.categories)
    return None


async def create_mm_ticket(interaction: discord.Interaction, trader2_raw: str, offer1: str, offer2: str):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This form can only be used inside a server.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    trader2 = await resolve_member(interaction.guild, trader2_raw)
    category = await find_category(interaction.guild)
    name = f"mm-{clean_channel_part(interaction.user.display_name)}-{clean_channel_part(trader2.display_name if trader2 else trader2_raw)}"
    channel = await interaction.guild.create_text_channel(name[:100], category=category, overwrites=overwrite_for_ticket(interaction.guild, interaction.user, trader2), reason="Y.T.B MM ticket created")
    description = (
        f"**Trader 1:** {interaction.user.mention}\n**Offer:** {offer1}\n\n"
        f"**Trader 2:** {trader2.mention if trader2 else discord.utils.escape_markdown(trader2_raw)}\n**Offer:** {offer2}\n\n"
        "A verified Y.T.B Middleman will assist with this trade."
    )
    await channel.send(embed=ticket_embed("Y.T.B Middleman Ticket", description, discord.Color.blurple()), view=TicketControlView())
    await interaction.followup.send(f"Your MM ticket is ready: {channel.mention}", ephemeral=True)


async def create_scam_ticket(interaction: discord.Interaction, scammer: str, details: str):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This form can only be used inside a server.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    category = await find_category(interaction.guild)
    name = f"scam-{clean_channel_part(interaction.user.display_name)}"
    channel = await interaction.guild.create_text_channel(name[:100], category=category, overwrites=overwrite_for_ticket(interaction.guild, interaction.user, None), reason="Y.T.B scam report created")
    description = f"**Reporter:** {interaction.user.mention}\n**Scammer:** {discord.utils.escape_markdown(scammer)}\n\n**Details:**\n{details}"
    await channel.send(embed=ticket_embed("Y.T.B Scam Report", description, discord.Color.red()), view=TicketControlView())
    await interaction.followup.send(f"Your scam report is ready: {channel.mention}", ephemeral=True)


async def member_snapshot(member: discord.Member, fetch_banner: bool = False) -> dict:
    fetched = member
    if fetch_banner:
        try:
            fetched = await bot.fetch_user(member.id)
        except discord.HTTPException:
            pass
    roles = [{"id": str(role.id), "name": role.name} for role in member.roles if role.id != member.guild.default_role.id]
    return {
        "id": str(member.id),
        "name": member.display_name,
        "username": member.name,
        "is_staff": is_staff(member),
        "is_blacklisted": any(role.id == BLACKLIST_ROLE_ID for role in member.roles),
        "has_member_role": any(role.id == MEMBER_ROLE_ID for role in member.roles),
        "roles_json": json.dumps(roles, ensure_ascii=False),
        "avatar_url": str(member.display_avatar.url) if member.display_avatar else None,
        "banner_url": str(fetched.banner.url) if getattr(fetched, "banner", None) else None,
    }


async def gather_media(channel: discord.TextChannel, closed_by: discord.Member) -> dict:
    records = []
    messages = []
    snapshots: dict[int, dict] = {}
    async for message in channel.history(limit=None, oldest_first=True):
        urls: list[str] = [attachment.url for attachment in message.attachments]
        urls.extend(embed.url for embed in message.embeds if embed.url)
        urls.extend(embed.image.url for embed in message.embeds if embed.image and embed.image.url)
        urls.extend(embed.thumbnail.url for embed in message.embeds if embed.thumbnail and embed.thumbnail.url)
        urls.extend(url for url in URL_RE.findall(message.content) if url.lower().split("?", 1)[0].endswith(IMAGE_EXTENSIONS))
        unique_urls = list(dict.fromkeys(urls))
        if isinstance(message.author, discord.Member) and message.author.id not in snapshots:
            snapshots[message.author.id] = await member_snapshot(message.author)
        author_snapshot = snapshots.get(message.author.id, {"id": str(message.author.id), "name": str(message.author)})
        messages.append({
            "id": str(message.id),
            "author_id": str(message.author.id),
            "author_name": author_snapshot.get("name"),
            "author_avatar_url": author_snapshot.get("avatar_url"),
            "timestamp": message.created_at.isoformat(),
            "content": message.content,
            "attachments": [attachment.url for attachment in message.attachments],
            "embeds": [embed.url for embed in message.embeds if embed.url],
        })
        for url in unique_urls:
            records.append({
                "url": url,
                "uploaded_by_user_id": str(message.author.id),
                "uploaded_by_name": author_snapshot.get("name"),
                "is_staff": author_snapshot.get("is_staff", False),
                "is_blacklisted": author_snapshot.get("is_blacklisted", False),
                "roles_json": author_snapshot.get("roles_json"),
                "avatar_url": author_snapshot.get("avatar_url"),
                "banner_url": author_snapshot.get("banner_url"),
                "timestamp": message.created_at.isoformat(),
            })
    closer = await member_snapshot(closed_by, fetch_banner=True)
    ticket_type = "scam" if channel.name.startswith("scam-") else "middleman" if channel.name.startswith("mm-") else "unknown"
    return {
        "channel_id": str(channel.id),
        "channel_name": channel.name,
        "guild_id": str(channel.guild.id),
        "ticket_type": ticket_type,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "closed_by_staff_id": closer["id"],
        "closed_by_staff_name": closer["name"],
        "closed_by_staff_is_blacklisted": closer["is_blacklisted"],
        "closed_by_staff_roles_json": closer["roles_json"],
        "closed_by_staff_avatar_url": closer["avatar_url"],
        "closed_by_staff_banner_url": closer["banner_url"],
        "messages": messages,
        "media": records,
    }


def send_media_payload(payload: dict) -> bool:
    if INGESTION_ENDPOINT:
        try:
            headers = {"Content-Type": "application/json"}
            if INGESTION_SECRET:
                headers["x-ingestion-secret"] = INGESTION_SECRET
            response = requests.post(INGESTION_ENDPOINT, json=payload, headers=headers, timeout=20)
            response.raise_for_status()
            return True
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code in {401, 403, 503}:
                log.error("Website ingestion rejected (%s); set the same INGESTION_SECRET in the bot .env; using fallback", error.response.status_code)
            else:
                log.exception("Website media ingestion failed; using fallback")
        except requests.RequestException:
            log.exception("Website media ingestion failed; using fallback")
    FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
    path = FALLBACK_DIR / f"ticket-{payload['channel_id']}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.warning("Media payload saved to fallback: %s", path)
    return False


async def sync_guild_members(guild: discord.Guild) -> None:
    members = []
    async for member in guild.fetch_members(limit=None):
        snapshot = await member_snapshot(member, fetch_banner=True)
        members.append({
            "id": snapshot["id"],
            "name": snapshot["name"],
            "username": snapshot["username"],
            "is_staff": snapshot["is_staff"],
            "is_blacklisted": snapshot["is_blacklisted"],
            "has_member_role": snapshot["has_member_role"],
            "roles_json": snapshot["roles_json"],
            "avatar_url": snapshot["avatar_url"],
            "banner_url": snapshot["banner_url"],
            "last_activity_at": datetime.now(timezone.utc).isoformat(),
        })
    payload = {
        "event_type": "member_sync",
        "channel_id": f"member-sync-{guild.id}",
        "guild_id": str(guild.id),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "members": members,
    }
    uploaded = await asyncio.to_thread(send_media_payload, payload)
    log.info("Member roster sync for %s: %s members (%s)", guild.name, len(members), "uploaded" if uploaded else "fallback")


async def sync_single_member(member: discord.Member, reason: str) -> None:
    snapshot = await member_snapshot(member, fetch_banner=True)
    payload = {
        "event_type": "member_sync",
        "channel_id": f"member-sync-{member.guild.id}",
        "guild_id": str(member.guild.id),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "members": [{
            "id": snapshot["id"],
            "name": snapshot["name"],
            "username": snapshot["username"],
            "is_staff": snapshot["is_staff"],
            "is_blacklisted": snapshot["is_blacklisted"],
            "has_member_role": snapshot["has_member_role"],
            "roles_json": snapshot["roles_json"],
            "avatar_url": snapshot["avatar_url"],
            "banner_url": snapshot["banner_url"],
            "last_activity_at": datetime.now(timezone.utc).isoformat(),
        }],
    }
    uploaded = await asyncio.to_thread(send_media_payload, payload)
    log.info("Member update for %s (%s): %s", member, reason, "uploaded" if uploaded else "fallback")


class TicketBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(MainPanelView())
        self.add_view(TicketControlView())
        await self.tree.sync()
        self.member_sync_loop.start()
        log.info("Persistent views registered and slash commands synced")

    @tasks.loop(minutes=15)
    async def member_sync_loop(self):
        for guild in self.guilds:
            try:
                await sync_guild_members(guild)
            except discord.HTTPException:
                log.exception("Could not sync member roster for %s", guild.id)

    @member_sync_loop.before_loop
    async def before_member_sync_loop(self):
        await self.wait_until_ready()

    async def on_ready(self):
        log.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "?")

    async def on_member_join(self, member: discord.Member):
        await sync_single_member(member, "joined")

    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles != after.roles or before.display_name != after.display_name or before.avatar != after.avatar:
            await sync_single_member(after, "profile or role change")


bot = TicketBot()


async def health_handler(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "discord_ready": bot.is_ready()})


async def start_health_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/health", health_handler)
    app.router.add_get("/", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", HEALTH_PORT).start()
    log.info("Health server listening on 0.0.0.0:%s", HEALTH_PORT)
    return runner


@bot.tree.command(name="setup_tickets", description="Post and pin the Y.T.B ticket panel in the configured channel")
@app_commands.default_permissions(manage_guild=True)
async def setup_tickets(interaction: discord.Interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("You need Manage Server permission to use this command.", ephemeral=True)
        return
    # A channel send/pin can exceed Discord's three-second initial-response window.
    # Acknowledge first so the command never remains stuck on "thinking...".
    await interaction.response.defer(ephemeral=True)
    channel = interaction.guild.get_channel(TARGET_CHANNEL_ID) if TARGET_CHANNEL_ID else interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.followup.send("Configure a valid TARGET_CHANNEL_ID or run this in a text channel.", ephemeral=True)
        return
    message = await channel.send(embed=setup_embed(), view=MainPanelView())
    try:
        await message.pin(reason="Y.T.B ticket panel")
    except discord.HTTPException:
        log.warning("Panel posted but could not be pinned in channel %s", channel.id)
    await interaction.followup.send(f"Ticket panel posted and pinned in {channel.mention}.", ephemeral=True)


if __name__ == "__main__":
    if not TOKEN or TOKEN == "PASTE_YOUR_DISCORD_BOT_TOKEN_HERE":
        raise SystemExit("DISCORD_BOT_TOKEN is not configured. Copy .env.example to .env and add a real token.")

    async def main() -> None:
        health_runner = await start_health_server()
        try:
            # Keep the web health endpoint alive while Discord applies a temporary
            # global rate limit. This prevents Render from repeatedly restarting
            # the service and creating another burst of login requests.
            retry_delay = 30
            while True:
                try:
                    await bot.start(TOKEN)
                    break
                except discord.LoginFailure:
                    log.exception("Discord rejected the bot token; refusing to retry an invalid token.")
                    raise
                except discord.HTTPException as exc:
                    if exc.status != 429:
                        log.exception("Discord HTTP error while starting the bot; retrying in %ss.", retry_delay)
                    else:
                        retry_after = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After")
                        try:
                            retry_delay = max(retry_delay, int(float(retry_after))) if retry_after else retry_delay
                        except (TypeError, ValueError):
                            pass
                        log.warning("Discord rate limit (429) during startup; retrying in %ss.", retry_delay)
                except (aiohttp.ClientError, OSError):
                    log.exception("Transient network error while starting the bot; retrying in %ss.", retry_delay)
                finally:
                    if not bot.is_closed():
                        await bot.close()
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 300)
        finally:
            await health_runner.cleanup()

    asyncio.run(main())
