import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import os
from dotenv import load_dotenv
import asyncio
from concurrent.futures import ThreadPoolExecutor
import glob

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()

bot = MusicBot()

current_song = None
current_embed = None
current_filename = None
music_queue = []
loop_enabled = False
executor = ThreadPoolExecutor(max_workers=4)

ytdl_format_options = {
    'format': 'bestaudio[ext=webm]/bestaudio/best',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'prefer_ffmpeg': True,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'outtmpl': 'temp_audio_%(id)s.%(ext)s',
    'ffmpeg_location': 'C:\\ffmpeg\\bin\\ffmpeg.exe' if os.name == 'nt' else '/usr/bin/ffmpeg',
}

DEFAULT_THUMBNAIL = "https://i.imgur.com/3lGbihT.png"  # URL da logo do bot


class MusicView(discord.ui.View):
    def __init__(self, vc):
        super().__init__(timeout=None)
        self.vc = vc

    @discord.ui.button(label="Play", style=discord.ButtonStyle.green)
    async def play_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.vc.is_paused():
            await interaction.response.send_message("A música já está tocando!", ephemeral=True)
            return
        self.vc.resume()
        await interaction.response.send_message("Música retomada!", ephemeral=True)

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.blurple)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.vc.is_playing():
            await interaction.response.send_message("Nenhuma música está tocando no momento.", ephemeral=True)
            return
        self.vc.pause()
        await interaction.response.send_message("Música pausada!", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.red)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.vc.is_playing():
            self.vc.stop()
        global current_filename
        await cleanup_file(current_filename, None)
        await self.vc.disconnect()
        await interaction.response.send_message("Música parada e bot desconectado.", ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.gray)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.vc.is_playing():
            self.vc.stop()
        global current_filename
        await cleanup_file(current_filename, None)
        await interaction.response.send_message("Música pulada!", ephemeral=True)
        if music_queue:
            next_query, next_interaction = music_queue.pop(0)
            await process_music(self.vc, next_query, next_interaction)

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.gray)
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global loop_enabled
        loop_enabled = not loop_enabled
        status = "ativado" if loop_enabled else "desativado"
        await interaction.response.send_message(f"Modo loop {status}!", ephemeral=True)


async def process_music(vc, query, interaction):
    def download_audio():
        with yt_dlp.YoutubeDL(ytdl_format_options) as ydl:
            print(f"Baixando áudio com query: {query}")
            info = ydl.extract_info(query, download=True)
            return info

    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(executor, download_audio)

    # Localiza o arquivo mais recente baixado
    downloaded_files = sorted(glob.glob("temp_audio_*.mp3"), key=os.path.getmtime, reverse=True)
    if not downloaded_files:
        raise FileNotFoundError("Nenhum arquivo MP3 encontrado após o download.")

    global current_filename, current_song
    current_filename = downloaded_files[0]  # Pega o mais recente
    current_song = query
    thumbnail_url = info.get("thumbnail", DEFAULT_THUMBNAIL)

    if vc.is_playing():
        vc.stop()

    vc.play(
        discord.FFmpegPCMAudio(current_filename),
        after=lambda e: asyncio.run_coroutine_threadsafe(handle_after_play(vc, current_filename, e, loop), loop)
    )

    embed = discord.Embed(title="🎵 Tocando Agora", description=query, color=discord.Color.blue())
    embed.set_image(url=thumbnail_url)
    embed.set_footer(text="Use os botões abaixo para controlar a reprodução.")
    await interaction.followup.send(embed=embed, view=MusicView(vc))


async def handle_after_play(vc, filename, error, loop):
    global current_song, current_filename
    if error:
        print(f"Erro durante a reprodução: {error}")

    await cleanup_file(filename, error)

    if loop_enabled and current_song:
        await process_music(vc, current_song, None)
        return

    if music_queue:
        next_query, next_interaction = music_queue.pop(0)
        await process_music(vc, next_query, next_interaction)
    else:
        current_song = None
        current_filename = None


async def cleanup_file(filename, error):
    if error:
        print(f"Erro durante a reprodução: {error}")
    if filename and os.path.exists(filename):
        try:
            os.remove(filename)
            print(f"Arquivo {filename} removido com sucesso.")
        except Exception as e:
            print(f"Erro ao remover arquivo {filename}: {e}")


@bot.event
async def on_ready():
    print(f'{bot.user} está online!')


@bot.tree.command(name="play", description="Toque uma música usando uma busca ou URL.")
@app_commands.describe(query="O nome da música ou URL para tocar.")
async def play_command(interaction: discord.Interaction, query: str):
    await interaction.response.defer()

    if not interaction.user.voice:
        await interaction.followup.send("Você precisa estar em um canal de voz para usar este comando.", ephemeral=True)
        return

    voice_channel = interaction.user.voice.channel

    try:
        if interaction.guild.voice_client is None:
            vc = await voice_channel.connect()
        elif interaction.guild.voice_client.channel != voice_channel:
            await interaction.guild.voice_client.move_to(voice_channel)
            vc = interaction.guild.voice_client
        else:
            vc = interaction.guild.voice_client

        if vc.is_playing():
            music_queue.append((query, interaction))
            await interaction.followup.send(f"Música adicionada à fila: {query}")
            return

        await process_music(vc, query, interaction)

    except Exception as e:
        await interaction.followup.send(f"Erro ao processar a música: {e}", ephemeral=True)


bot.run(DISCORD_TOKEN)
