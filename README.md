# Y.T.B Discord Ticket / Middleman / Scam Bot

A persistent `discord.py` bot implementing MM requests, scam reports, private ticket channels, staff claim/close controls, and media-log ingestion into Y.T.B Proof Desk.

## Setup

1. Copy `.env.example` to `.env` and set:
   - `DISCORD_BOT_TOKEN`: real Discord bot token (never commit this file).
   - `WEBSITE_INGESTION_ENDPOINT`: the Proof Desk `/api/ingest` URL.
   - `INGESTION_SECRET`: the same shared secret stored in the Proof Desk project secret configuration.
   - `TARGET_CHANNEL_ID`: channel where `/setup_tickets` posts the panel, or leave `0` to use the command channel.
   - `TICKET_CATEGORY_ID` or `TICKET_CATEGORY_NAME`: category for newly created ticket channels.
   - `BLACKLIST_ROLE_ID` and `MEMBER_ROLE_ID`: the Discord role IDs used for blacklist/member snapshots.
2. Invite the bot with the `bot` and `applications.commands` scopes.
3. Enable **Server Members Intent** and **Message Content Intent** in the Discord Developer Portal.
4. Give the bot these permissions in the relevant server/category: View Channels, Send Messages, Embed Links, Attach Files, Read Message History, Manage Channels, and Manage Messages (for pinning).
5. Run:

```bash
cp .env.example .env
# edit .env
./run.sh
```

## Usage

- Run `/setup_tickets` in the configured target channel.
- Users click **Request Middleman** or **Report Scam**.
- Staff with one of the configured staff role IDs can claim or close tickets.
- Closing gathers the latest 200 messages' attachment URLs, embed image URLs, image URLs in message text, uploader IDs, names, role snapshots, avatar URLs, banner URLs, and timestamps before deleting the channel.
- Members carrying `BLACKLIST_ROLE_ID` are marked as blacklisted for the Proof Desk roster. `MEMBER_ROLE_ID` is included in the member-role snapshot.
- The bot performs an initial full-guild member sync and repeats it every 15 minutes, so the website's Members, Staff, and Blacklist views update without opening Discord.

## Proof Desk payload

The bot posts a payload like this to `WEBSITE_INGESTION_ENDPOINT` with the `x-ingestion-secret` header:

```json
{
  "channel_id": "123",
  "channel_name": "scam-reporter",
  "guild_id": "456",
  "ticket_type": "scam",
  "collected_at": "2026-01-01T00:00:00+00:00",
  "closed_by_staff_id": "789",
  "closed_by_staff_name": "Middleman",
  "closed_by_staff_avatar_url": "https://...",
  "closed_by_staff_banner_url": "https://...",
  "media": [
    {
      "url": "https://...",
      "uploaded_by_user_id": "789",
      "uploaded_by_name": "Member",
      "is_blacklisted": false,
      "is_staff": true,
      "roles_json": "[]",
      "avatar_url": "https://...",
      "banner_url": "https://...",
      "timestamp": "..."
    }
  ]
}
```

The endpoint receives JSON with a 20-second timeout. Any non-2xx response or network error is written to the fallback directory.

## Security notes

- `.env` is ignored by the included `.gitignore`; do not paste tokens into source files or chat.
- Treat fallback JSON files as sensitive because they contain private ticket media URLs and Discord user IDs.
- The bot does not download media; it relays URLs, avoiding unnecessary storage and bandwidth.
- Regenerate any Discord token that has been exposed and update `.env` afterward.
