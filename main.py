import os
import discord
from discord.ext import commands
from discord.ui import View, Select, button
from dotenv import load_dotenv
import yt_dlp
import asyncio
import nest_asyncio
import certifi

os.environ["SSL_CERT_FILE"] = certifi.where()
nest_asyncio.apply()
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("❌ DISCORD_TOKEN not found in .env file!")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

queues = {}
repeats = {}
idle_disconnect = {}

@bot.event
async def on_ready():
    await bot.change_presence(status=discord.Status.idle, activity=discord.Game("Music"))
    print(f"✅ Bot {bot.user} launched and ready!")

def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = asyncio.Queue()
    return queues[guild_id]

def get_repeat_flag(guild_id):
    return repeats.get(guild_id, False)

def toggle_repeat_flag(guild_id):
    repeats[guild_id] = not repeats.get(guild_id, False)

async def search_music(query):
    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "default_search": "ytsearch5",
        "quiet": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            results = ydl.extract_info(query, download=False)
            entries = results.get("entries", [])
            tracks = []
            for entry in entries:
                duration = int(entry.get("duration", 0))
                if duration < 900:
                    mins = duration // 60
                    secs = duration % 60
                    tracks.append({
                        "title": entry["title"],
                        "url": f"https://www.youtube.com/watch?v={entry['id']}",
                        "author": entry.get("uploader", "Unknown"),
                        "duration_seconds": duration,
                        "duration": f"{mins}:{secs:02d}",
                        "thumbnail": entry.get("thumbnail"),
                    })
            return tracks
        except Exception as e:
            print(f"[YT-DLP Error]: {e}")
            return None

async def extract_info_from_url(url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'noplaylist': True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            duration = int(info.get("duration", 0))
            if duration < 900:
                mins = duration // 60
                secs = duration % 60
                return {
                    'title': info['title'],
                    'url': f"https://www.youtube.com/watch?v={info['id']}",
                    'author': info.get('uploader', 'Unknown'),
                    'duration': f"{mins}:{secs:02d}",
                    'duration_seconds': duration,
                    'thumbnail': info.get('thumbnail'),
                }
        except Exception as e:
            print(f"[YT-DLP Error]: {e}")
            return None

class MusicControls(View):
    def __init__(self, player, ctx, track):
        super().__init__(timeout=None)
        self.player = player
        self.ctx = ctx
        self.track = track

    @button(label="⏯ Pause/Continue", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if self.player.is_paused():
            self.player.resume()
            await interaction.followup.send("▶ Continued", ephemeral=True)
        else:
            self.player.pause()
            await interaction.followup.send("⏸ Pause", ephemeral=True)

    @button(label="🔁 Repeat", style=discord.ButtonStyle.secondary)
    async def repeat(self, interaction: discord.Interaction, button: discord.ui.Button):
        toggle_repeat_flag(self.ctx.guild.id)
        status = "included" if get_repeat_flag(self.ctx.guild.id) else "turned off"
        await interaction.response.send_message(f"🔁 Repeat {status}", ephemeral=True)

    @button(label="⏭ Skip", style=discord.ButtonStyle.danger)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if self.player.is_playing():
            self.player.stop()
        await interaction.followup.send("⏭ Track skipped.", ephemeral=True)

    @button(label="📄 Queue", style=discord.ButtonStyle.success)
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = list(get_queue(self.ctx.guild.id)._queue)
        if not queue:
            await interaction.response.send_message("📭 The queue is empty.", ephemeral=True)
            return
        embed = discord.Embed(title="📄 Track queue", color=discord.Color.green())
        for i, track in enumerate(queue[:10], start=1):
            embed.add_field(name=f"{i}. {track['title']}", value=f"Duration: `{track['duration']}`", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=False, delete_after=180)

async def play_next(ctx, voice_client):
    queue = get_queue(ctx.guild.id)

    if get_repeat_flag(ctx.guild.id):
        track = getattr(voice_client, "last_track", None)
        if not track:
            await ctx.send("⛔ There is nothing to repeat.")
            return
    else:
        if queue.empty():
            await ctx.send("📭 The queue is empty.")
            idle_disconnect[ctx.guild.id] = asyncio.create_task(disconnect_after_idle(ctx.guild.id))
            return
        track = await queue.get()

    voice_client.last_track = track

    with yt_dlp.YoutubeDL({
        "format": "bestaudio",
        "quiet": True,
        "nocheckcertificate": True,
        "cachedir": False,
        "source_address": "0.0.0.0"
    }) as ydl:
        info = ydl.extract_info(track["url"], download=False)
        stream_url = info["url"]

    source = await discord.FFmpegOpusAudio.from_probe(stream_url, method="fallback")
    voice_client.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx, voice_client), bot.loop))

    embed = discord.Embed(title="🎶 Now playing", description=f"[{track['title']}]({track['url']})", color=discord.Color.blurple())
    embed.add_field(name="Author", value=track.get("author", "Unknown"), inline=True)
    embed.add_field(name="Duration", value=track.get("duration"), inline=True)

    requester = track.get("requester")
    if requester:
        embed.set_footer(text=f"Requested: {requester.display_name}", icon_url=requester.display_avatar.url)

    if track.get("thumbnail"):
        embed.set_thumbnail(url=track["thumbnail"])

    view = MusicControls(voice_client, ctx, track)

    if hasattr(voice_client, "last_embed_msg"):
        try:
            await voice_client.last_embed_msg.edit(embed=embed, view=view)
        except:
            voice_client.last_embed_msg = await ctx.send(embed=embed, view=view)
    else:
        voice_client.last_embed_msg = await ctx.send(embed=embed, view=view)

async def disconnect_after_idle(guild_id):
    await asyncio.sleep(300)
    vc = discord.utils.get(bot.voice_clients, guild__id=guild_id)
    if vc and not vc.is_playing():
        await vc.disconnect()
        print(f"⏹ Bot disabled due to guild downtime {guild_id}")

@bot.command()
async def play(ctx, *, query: str):
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        print("⚠️ The bot does not have permission to delete messages.")
    except Exception as e:
        print(f"❌ Error deleting command: {e}")

    if ctx.guild.id in idle_disconnect:
        idle_disconnect[ctx.guild.id].cancel()

    if ctx.voice_client is None:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect(self_deaf=True)
        else:
            await ctx.send("⚠️ You must be in the voice channel!")
            return

    if "youtube.com/watch" in query or "youtu.be/" in query:
        track = await extract_info_from_url(query)
        if track:
            track["requester"] = ctx.author
            await get_queue(ctx.guild.id).put(track)
            if not ctx.voice_client.is_playing():
                await play_next(ctx, ctx.voice_client)
        else:
            await ctx.send("⚠️ Failed to load track.")
        return

    results = await search_music(query)
    if not results:
        await ctx.send("⚠️ Nothing found.")
        return

    class SongSelect(Select):
        def __init__(self):
            options = [discord.SelectOption(label=r["title"][:100], value=str(i)) for i, r in enumerate(results)]
            super().__init__(placeholder="Select a track", options=options, min_values=1, max_values=1)

        async def callback(self, interaction: discord.Interaction):
            index = int(self.values[0])
            selected = results[index]
            selected["requester"] = ctx.author
            await get_queue(ctx.guild.id).put(selected)

            await interaction.response.send_message("🎶 Track selected, loading...", ephemeral=True)

            await asyncio.sleep(2)
            try:
                await interaction.message.delete()
            except:
                pass

            if not ctx.voice_client.is_playing():
                asyncio.create_task(play_next(ctx, ctx.voice_client))

    view = View(timeout=180)
    view.add_item(SongSelect())
    await ctx.send(
        embed=discord.Embed(title="🔍 Found:", description="Select a track:", color=0x3498db),
        view=view
    )

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("🛑 Stopped and disabled.")

bot.run(TOKEN)

