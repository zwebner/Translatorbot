import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Modal, TextInput, button
from googletrans import Translator
import openai
from dotenv import load_dotenv
import os, json, asyncio, aiohttp

load_dotenv()
TOKEN = os.getenv("TOKEN")
openai.api_key = os.getenv("OPENAI_API_KEY")

intents = discord.Intents.default()
intents.message_content = True

DATA_FILE = "translation_data.json"
FLAGS_FILE = "language_flags.json"

DEFAULT_SETTINGS = {
    "auto_delete": False,
    "auto_delete_seconds": 30,
    "show_flags": True,
    "embed_color": 0x3498db,
    "max_translation_length": 1500
}

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {
        "translation_channels": {},
        "user_languages": {},
        "channel_settings": {},
        "translation_stats": {"overall": 0, "by_channel": {}}
    }

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def load_flags():
    if os.path.exists(FLAGS_FILE):
        with open(FLAGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {
        "en": "🇺🇸", "ja": "🇯🇵", "de": "🇩🇪", "fr": "🇫🇷",
        "es": "🇪🇸", "it": "🇮🇹", "ko": "🇰🇷", "zh-cn": "🇨🇳",
        "ru": "🇷🇺", "pt": "🇵🇹", "ar": "🇸🇦", "hi": "🇮🇳"
    }

LANG_FLAGS = load_flags()
def get_flag(code): return LANG_FLAGS.get(code.lower(), "")

class StartModal(Modal, title="Enable Translation"):
    langs = TextInput(label="Language codes (comma-separated)", placeholder="en, ja, de")

    async def on_submit(self, interaction: discord.Interaction):
        codes = [l.strip().lower() for l in self.langs.value.split(",") if l.strip()]
        if len(codes) < 2:
            return await interaction.response.send_message("Enter at least two codes.", ephemeral=True)
        gid, cid = str(interaction.guild.id), str(interaction.channel.id)
        bot.translation_channels.setdefault(gid, {})[cid] = codes
        bot.channel_settings.setdefault(gid, {}).setdefault(cid, DEFAULT_SETTINGS.copy())
        bot.save()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Enabled",
                description="Languages: " + ", ".join(f"{c} {get_flag(c)}" for c in codes),
                color=discord.Color.green()
            ), ephemeral=True
        )

class RemoveView(View):
    def __init__(self, gid, cid):
        super().__init__(timeout=None)
        self.gid, self.cid = gid, cid

    @discord.ui.button(label="Confirm Removal", style=discord.ButtonStyle.danger)
    async def confirm(self, inter: discord.Interaction, btn: discord.ui.Button):
        bot.translation_channels[self.gid].pop(self.cid, None)
        bot.channel_settings[self.gid].pop(self.cid, None)
        bot.save()
        await inter.response.edit_message("✅ Translations disabled.", view=None, ephemeral=True)

class SettingsModal(Modal, title="Edit Settings"):
    embed_color = TextInput(label="Embed Color (#hex)", default="#3498db")
    max_length = TextInput(label="Max translation length", default="1500")
    auto_del = TextInput(label="Auto-delete after (sec)", default="30")

    async def on_submit(self, interaction: discord.Interaction):
        gid, cid = str(interaction.guild.id), str(interaction.channel.id)
        s = bot.channel_settings[gid][cid]
        try:
            s["embed_color"] = int(self.embed_color.value.lstrip("#"), 16)
            s["max_translation_length"] = int(self.max_length.value)
            s["auto_delete_seconds"] = int(self.auto_del.value)
        except:
            return await interaction.response.send_message("Invalid input.", ephemeral=True)
        bot.save()
        await interaction.response.send_message("✅ Settings updated.", ephemeral=True)

class SettingsView(View):
    def __init__(self, gid, cid):
        super().__init__(timeout=None)
        self.gid, self.cid = gid, cid

    @discord.ui.button(label="Edit Advanced", style=discord.ButtonStyle.primary)
    async def edit(self, inter, btn: discord.Interaction, button: discord.ui.Button=None):
        await inter.response.send_modal(SettingsModal())

    @discord.ui.button(label="Toggle Auto-delete", style=discord.ButtonStyle.secondary)
    async def autodel(self, inter: discord.Interaction, btn: discord.ui.Button=None):
        s = bot.channel_settings[self.gid][self.cid]
        s["auto_delete"] = not s["auto_delete"]
        bot.save(); await self.refresh(inter)

    @discord.ui.button(label="Toggle Flags", style=discord.ButtonStyle.secondary)
    async def flags(self, inter: discord.Interaction, btn: discord.ui.Button=None):
        s = bot.channel_settings[self.gid][self.cid]
        s["show_flags"] = not s["show_flags"]
        bot.save(); await self.refresh(inter)

    async def refresh(self, inter: discord.Interaction):
        s = bot.channel_settings[self.gid][self.cid]
        embed = discord.Embed(title="⚙️ Settings", color=s["embed_color"])
        for k, v in s.items():
            embed.add_field(name=k.replace("_"," ").title(), value=str(v), inline=True)
        await inter.response.edit_message(embed=embed, view=self, ephemeral=True)

class TranslateBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.translator = Translator()
        self.data = load_data()
        self.translation_channels = self.data["translation_channels"]
        self.user_languages = self.data["user_languages"]
        self.channel_settings = self.data["channel_settings"]
        self.stats = self.data["translation_stats"]
        self.webhook_cache = {}

    async def setup_hook(self):
        guild_obj = discord.Object(id=1380199579470794914)
        self.tree.copy_global_to(guild=guild_obj)
        await self.tree.sync(guild=guild_obj)

    async def get_webhook(self, channel):
        if channel.id in self.webhook_cache:
            return self.webhook_cache[channel.id]
        
        webhooks = await channel.webhooks()
        if webhooks:
            webhook = next((w for w in webhooks if w.name == "Translation Webhook"), None)
            if webhook:
                self.webhook_cache[channel.id] = webhook
                return webhook
        
        webhook = await channel.create_webhook(name="Translation Webhook")
        self.webhook_cache[channel.id] = webhook
        return webhook

    async def cleanup_webhook(self, webhook):
        try:
            await webhook.delete()
            if webhook.channel.id in self.webhook_cache:
                del self.webhook_cache[webhook.channel.id]
        except:
            pass

    def save(self):
        self.data.update({
            "translation_channels": self.translation_channels,
            "user_languages": self.user_languages,
            "channel_settings": self.channel_settings,
            "translation_stats": self.stats
        })
        save_data(self.data)

bot = TranslateBot()

@bot.tree.command(name="start", description="Enable translation in this channel")
async def cmd_start(inter: discord.Interaction):
    await inter.response.send_modal(StartModal())

@bot.tree.command(name="remove", description="Disable translation in this channel")
async def cmd_remove(inter: discord.Interaction):
    gid, cid = str(inter.guild.id), str(inter.channel.id)
    if cid not in bot.translation_channels.get(gid, {}):
        return await inter.response.send_message("Nothing to disable.", ephemeral=True)
    await inter.response.send_message(view=RemoveView(gid, cid), ephemeral=True)

@bot.tree.command(name="setlang", description="Set your default language")
@app_commands.describe(language="Language code (e.g. en, ja, de)")
async def cmd_setlang(inter: discord.Interaction, language: str):
    bot.user_languages[str(inter.user.id)] = language.lower()
    bot.save()
    await inter.response.send_message(f"✅ Language set to {language}", ephemeral=True)

@bot.tree.command(name="listlangs", description="Show supported language codes")
async def cmd_listlangs(inter: discord.Interaction):
    lines = [f"{c}: {f}" for c, f in LANG_FLAGS.items()]
    await inter.response.send_message("**Supported Codes**\n" + "\n".join(lines), ephemeral=True)

@bot.tree.command(name="status", description="View translation status & stats")
async def cmd_status(inter: discord.Interaction):
    gid, cid = str(inter.guild.id), str(inter.channel.id)
    langs = bot.translation_channels.get(gid, {}).get(cid, [])
    key = f"{gid}-{cid}"
    embed = discord.Embed(title="📊 Status", color=discord.Color.blurple())
    embed.add_field(name="Channel Langs", value=", ".join(langs) or "<none>", inline=False)
    embed.add_field(name="Your Lang", value=bot.user_languages.get(str(inter.user.id), "<not set>", inline=False))
    embed.add_field(name="Messages Translated", value=f"Channel: {bot.stats['by_channel'].get(key,0)}\nTotal: {bot.stats['overall']}", inline=False)
    await inter.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="settings", description="Manage channel settings")
async def cmd_settings(inter: discord.Interaction):
    gid, cid = str(inter.guild.id), str(inter.channel.id)
    if cid not in bot.channel_settings.get(gid, {}):
        return await inter.response.send_message("Please run `/start` first.", ephemeral=True)
    s = bot.channel_settings[gid][cid]
    embed = discord.Embed(title="⚙️ Settings", color=s["embed_color"])
    for k, v in s.items():
        embed.add_field(name=k.replace("_"," ").title(), value=str(v), inline=True)
    await inter.response.send_message(embed=embed, view=SettingsView(gid, cid), ephemeral=True)

@bot.tree.command(name="translate", description="Inline translation of text")
@app_commands.describe(text="Text to translate", to="Target language code")
async def cmd_translate(inter: discord.Interaction, text: str, to: str):
    src = bot.translator.detect(text).lang
    tx = bot.translator.translate(text, src=src, dest=to).text
    await inter.response.send_message(f"`{src}` → `{to}`: {tx}", ephemeral=True)

@bot.tree.command(name="summarize", description="Summarize recent messages in this channel")
@app_commands.describe(limit="Number of recent messages to summarize (default 20)")
async def cmd_summarize(inter: discord.Interaction, limit: int = 20):
    await inter.response.defer(thinking=True, ephemeral=True)
    user_lang = bot.user_languages.get(str(inter.user.id), "en")
    msgs = [m async for m in inter.channel.history(limit=limit) if not m.author.bot and m.content]
    msgs.reverse()
    if not msgs:
        return await inter.followup.send("❌ No messages to summarize.", ephemeral=True)

    convo = "\n".join(f"{m.author.display_name}: {m.content}" for m in msgs)
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role":"user", "content":f"Summarize this conversation:\n\n{convo}"}],
            temperature=0.7
        )
        summary = resp.choices[0].message.content.strip()
    except Exception as e:
        return await inter.followup.send(f"OpenAI error: {e}", ephemeral=True)

    det = bot.translator.detect(summary).lang
    translated = bot.translator.translate(summary, src=det, dest=user_lang).text if det != user_lang else summary
    await inter.followup.send(f"📌 **Summary ({user_lang}):**\n{translated}", ephemeral=True)

@bot.event
async def on_message(message):
    if message.author.bot or not message.content:
        return

    if isinstance(message.channel, discord.DMChannel):
        src = bot.translator.detect(message.content).lang
        tx = bot.translator.translate(message.content, dest="en").text
        await message.reply(f"Detected `{src}` → English:\n{tx}")
        return

    gid, cid = str(message.guild.id), str(message.channel.id)
    langs = bot.translation_channels.get(gid, {}).get(cid, [])
    if not langs:
        return

    bot.stats["overall"] += 1
    key = f"{gid}-{cid}"
    bot.stats["by_channel"][key] = bot.stats["by_channel"].get(key, 0) + 1

    settings = bot.channel_settings[gid][cid]
    src = bot.translator.detect(message.content).lang

    # Prepare translations
    translations = []
    for lang in langs:
        if lang != src:
            tx = bot.translator.translate(
                message.content[:settings["max_translation_length"]],
                src=src,
                dest=lang
            ).text
            flag = get_flag(lang) if settings["show_flags"] else ""
            translations.append(f"{flag} **{lang.upper()}:** {tx}")

    if not translations:
        return

    # Create webhook message
    try:
        webhook = await bot.get_webhook(message.channel)
        content = "\n".join(translations)
        
        sent_msg = await webhook.send(
            content=content,
            username=f"{message.author.display_name} (Translated)",
            avatar_url=message.author.display_avatar.url,
            wait=True
        )

        # Auto-delete if enabled
        if settings["auto_delete"]:
            await asyncio.sleep(settings["auto_delete_seconds"])
            try:
                await sent_msg.delete()
            except:
                pass

    except Exception as e:
        print(f"Webhook error: {e}")
        # Fallback to regular message if webhook fails
        embed = discord.Embed(
            title=f"Translations for {message.author.display_name}",
            description="\n".join(translations),
            color=settings["embed_color"]
        )
        embed.set_thumbnail(url=message.author.display_avatar.url)
        
        sent_msg = await message.channel.send(embed=embed)
        
        if settings["auto_delete"]:
            await asyncio.sleep(settings["auto_delete_seconds"])
            try:
                await sent_msg.delete()
            except:
                pass

    bot.save()

bot.run(TOKEN)
