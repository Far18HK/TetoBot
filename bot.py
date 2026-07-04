import discord
import os
import re
import random
import sqlite3
import logging
from datetime import timedelta, datetime, timezone
from discord import app_commands
from discord.ext import commands

# ─────────────────────────────────────────
# CONFIG GENERAL
# ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("Teto")

TOKEN = os.getenv("TOKEN")
TU_ID = 1180967503682355220
ROLES_COMANDOS = ["Admin", "Moderador", "Shogun 🦈", "ViceRoot", "Root", "Daimyō", "Rōnin"]

if not TOKEN:
    raise RuntimeError("❌ Falta TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────────────────
# CHECK PA STAFF
# ─────────────────────────────────────────
def es_staff_member(member: discord.Member) -> bool:
    if member.id == TU_ID:
        return True
    return any(rol.name in ROLES_COMANDOS for rol in member.roles)

def is_staff_ctx():
    async def predicate(ctx: commands.Context) -> bool:
        return es_staff_member(ctx.author)
    return commands.check(predicate)

# ─────────────────────────────────────────
# DB
# ─────────────────────────────────────────
db = sqlite3.connect("teto.db", check_same_thread=False)
cursor = db.cursor()
cursor.execute("""CREATE TABLE IF NOT EXISTS economia (
    guild_id INTEGER, user_id INTEGER, balance INTEGER DEFAULT 0,
    last_trabajo TEXT, last_crime TEXT, last_robar TEXT,
    PRIMARY KEY (guild_id, user_id)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS tienda (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, nombre TEXT, precio INTEGER, descripcion TEXT,
    emoji TEXT DEFAULT '📦', usable INTEGER DEFAULT 0, mensaje_uso TEXT DEFAULT ''
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS inventario (
    guild_id INTEGER, user_id INTEGER, item TEXT, cantidad INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, item)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS cooldowns (
    guild_id INTEGER, user_id INTEGER, tipo TEXT, ultimo TEXT,
    PRIMARY KEY (guild_id, user_id, tipo)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS config (
    guild_id INTEGER PRIMARY KEY,
    cooldown_trabajo INTEGER DEFAULT 3600,
    cooldown_crime INTEGER DEFAULT 7200,
    cooldown_robar INTEGER DEFAULT 10800,
    trabajo_min INTEGER DEFAULT 60,
    trabajo_max INTEGER DEFAULT 200,
    crime_chance REAL DEFAULT 0.55,
    crime_win_min INTEGER DEFAULT 150,
    crime_win_max INTEGER DEFAULT 500,
    crime_loss_min INTEGER DEFAULT 50,
    crime_loss_max INTEGER DEFAULT 200,
    robar_chance REAL DEFAULT 0.4,
    robar_min_pct REAL DEFAULT 0.1,
    robar_max_pct REAL DEFAULT 0.3,
    robar_max_cap INTEGER DEFAULT 5000,
    robar_min_balance INTEGER DEFAULT 100,
    robar_fail_min INTEGER DEFAULT 50,
    robar_fail_max INTEGER DEFAULT 150,
    moneda_emoji TEXT DEFAULT '💵',
    cooldown_slots INTEGER DEFAULT 30,
    cooldown_ruleta INTEGER DEFAULT 45,
    cooldown_coinflip INTEGER DEFAULT 20,
    cooldown_blackjack INTEGER DEFAULT 30,
    apuesta_min INTEGER DEFAULT 10,
    apuesta_max INTEGER DEFAULT 10000,
    slots_multi_x2 INTEGER DEFAULT 2,
    slots_multi_x3 INTEGER DEFAULT 10,
    ruleta_multi_color INTEGER DEFAULT 2,
    ruleta_multi_verde INTEGER DEFAULT 14
)""")
db.commit()

# Migraciones para bases de datos ya existentes (por si les faltan columnas nuevas)
_MIGRACIONES = [
    ("tienda", "emoji", "TEXT DEFAULT '📦'"),
    ("tienda", "usable", "INTEGER DEFAULT 0"),
    ("tienda", "mensaje_uso", "TEXT DEFAULT ''"),
    ("config", "moneda_emoji", "TEXT DEFAULT '💵'"),
    ("config", "cooldown_slots", "INTEGER DEFAULT 30"),
    ("config", "cooldown_ruleta", "INTEGER DEFAULT 45"),
    ("config", "cooldown_coinflip", "INTEGER DEFAULT 20"),
    ("config", "cooldown_blackjack", "INTEGER DEFAULT 30"),
    ("config", "apuesta_min", "INTEGER DEFAULT 10"),
    ("config", "apuesta_max", "INTEGER DEFAULT 10000"),
    ("config", "slots_multi_x2", "INTEGER DEFAULT 2"),
    ("config", "slots_multi_x3", "INTEGER DEFAULT 10"),
    ("config", "ruleta_multi_color", "INTEGER DEFAULT 2"),
    ("config", "ruleta_multi_verde", "INTEGER DEFAULT 14"),
]
for _tabla, _columna, _definicion in _MIGRACIONES:
    try:
        cursor.execute(f"ALTER TABLE {_tabla} ADD COLUMN {_columna} {_definicion}")
        db.commit()
    except sqlite3.OperationalError:
        pass  # la columna ya existe

# ─────────────────────────────────────────
# CONFIGURACIÓN POR SERVIDOR
# ─────────────────────────────────────────
CONFIG_FIELDS_ECONOMIA = {
    "cooldown_trabajo": ("int", "Cooldown de !trabajo en segundos"),
    "cooldown_crime": ("int", "Cooldown de !crime en segundos"),
    "cooldown_robar": ("int", "Cooldown de !robar en segundos"),
    "trabajo_min": ("int", "Ganancia mínima de !trabajo"),
    "trabajo_max": ("int", "Ganancia máxima de !trabajo"),
    "crime_chance": ("float", "Probabilidad de éxito de !crime (0 a 1)"),
    "crime_win_min": ("int", "Ganancia mínima si el crime sale bien"),
    "crime_win_max": ("int", "Ganancia máxima si el crime sale bien"),
    "crime_loss_min": ("int", "Pérdida mínima si el crime sale mal"),
    "crime_loss_max": ("int", "Pérdida máxima si el crime sale mal"),
    "robar_chance": ("float", "Probabilidad de éxito de !robar (0 a 1)"),
    "robar_min_pct": ("float", "Porcentaje mínimo del balance de la víctima a robar (0 a 1)"),
    "robar_max_pct": ("float", "Porcentaje máximo del balance de la víctima a robar (0 a 1)"),
    "robar_max_cap": ("int", "Tope máximo que se puede robar de una vez"),
    "robar_min_balance": ("int", "Balance mínimo que debe tener la víctima para poder robarle"),
    "robar_fail_min": ("int", "Multa mínima si falla el robo"),
    "robar_fail_max": ("int", "Multa máxima si falla el robo"),
}

CONFIG_FIELDS_CASINO = {
    "cooldown_slots": ("int", "Cooldown de !slots en segundos"),
    "cooldown_ruleta": ("int", "Cooldown de !ruleta en segundos"),
    "cooldown_coinflip": ("int", "Cooldown de !coinflip en segundos"),
    "cooldown_blackjack": ("int", "Cooldown de !blackjack en segundos"),
    "apuesta_min": ("int", "Apuesta mínima permitida en los juegos de casino"),
    "apuesta_max": ("int", "Apuesta máxima permitida en los juegos de casino"),
    "slots_multi_x2": ("int", "Multiplicador de !slots si salen 2 iguales"),
    "slots_multi_x3": ("int", "Multiplicador de !slots si salen 3 iguales"),
    "ruleta_multi_color": ("int", "Multiplicador de !ruleta si aciertas rojo/negro"),
    "ruleta_multi_verde": ("int", "Multiplicador de !ruleta si aciertas verde (el 0)"),
}

CONFIG_FIELDS = {**CONFIG_FIELDS_ECONOMIA, **CONFIG_FIELDS_CASINO}

# nombre amigable de comando -> campo real de cooldown en la tabla config
MAPA_COOLDOWNS = {
    "trabajo": "cooldown_trabajo", "work": "cooldown_trabajo",
    "crime": "cooldown_crime",
    "robar": "cooldown_robar", "rob": "cooldown_robar",
    "slots": "cooldown_slots", "tragamonedas": "cooldown_slots",
    "ruleta": "cooldown_ruleta", "roulette": "cooldown_ruleta",
    "coinflip": "cooldown_coinflip", "cf": "cooldown_coinflip",
    "blackjack": "cooldown_blackjack", "bj": "cooldown_blackjack",
}

def get_config(guild_id: int) -> dict:
    cursor.execute("INSERT OR IGNORE INTO config (guild_id) VALUES (?)", (guild_id,))
    db.commit()
    cursor.execute("SELECT * FROM config WHERE guild_id=?", (guild_id,))
    row = cursor.fetchone()
    columnas = [d[0] for d in cursor.description]
    return dict(zip(columnas, row))

def set_config_field(guild_id: int, campo: str, valor):
    get_config(guild_id)  # asegura que la fila exista
    cursor.execute(f"UPDATE config SET {campo} = ? WHERE guild_id=?", (valor, guild_id))
    db.commit()

def parse_tiempo(texto: str):
    """Convierte '1h30m', '45s', '90' etc. a segundos. Devuelve None si es inválido."""
    if texto is None:
        return None
    texto = texto.strip().lower()
    if texto.isdigit():
        return int(texto)
    partes = re.findall(r"(\d+)\s*([hms])", texto)
    if not partes:
        return None
    total = 0
    for cantidad, unidad in partes:
        cantidad = int(cantidad)
        if unidad == "h":
            total += cantidad * 3600
        elif unidad == "m":
            total += cantidad * 60
        elif unidad == "s":
            total += cantidad
    return total if total > 0 else None

# ─────────────────────────────────────────
# HELPERS DE MONEDA / EMBEDS
# ─────────────────────────────────────────
def get_moneda(guild_id: int) -> str:
    cfg = get_config(guild_id)
    return cfg.get("moneda_emoji") or "💵"

def format_dinero(guild_id: int, cantidad: int) -> str:
    return f"{cantidad:,} {get_moneda(guild_id)}"

def make_embed(description: str, title: str = None, color: int = 0x2ECC71) -> discord.Embed:
    embed = discord.Embed(description=description, color=color)
    if title:
        embed.title = title
    return embed

async def send_msg(ctx: commands.Context, description: str, title: str = None, color: int = 0x2ECC71):
    await ctx.send(embed=make_embed(description, title, color))

# ─────────────────────────────────────────
# HELPERS ECONOMÍA
# ─────────────────────────────────────────
def _ensure_user(guild_id: int, user_id: int):
    cursor.execute("INSERT OR IGNORE INTO economia (guild_id, user_id, balance) VALUES (?,?,0)", (guild_id, user_id))
    db.commit()

def get_balance(guild_id: int, user_id: int) -> int:
    _ensure_user(guild_id, user_id)
    cursor.execute("SELECT balance FROM economia WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    row = cursor.fetchone()
    return row[0] if row else 0

def modificar_balance(guild_id: int, user_id: int, cantidad: int):
    _ensure_user(guild_id, user_id)
    cursor.execute("UPDATE economia SET balance = MAX(balance + ?, 0) WHERE guild_id=? AND user_id=?", (cantidad, guild_id, user_id))
    db.commit()

def get_cooldown(guild_id: int, user_id: int, campo: str):
    _ensure_user(guild_id, user_id)
    cursor.execute(f"SELECT {campo} FROM economia WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    return datetime.fromisoformat(row[0])

def set_cooldown(guild_id: int, user_id: int, campo: str):
    _ensure_user(guild_id, user_id)
    cursor.execute(f"UPDATE economia SET {campo} = ? WHERE guild_id=? AND user_id=?", (datetime.now(timezone.utc).isoformat(), guild_id, user_id))
    db.commit()

def get_cooldown_generic(guild_id: int, user_id: int, tipo: str):
    cursor.execute("SELECT ultimo FROM cooldowns WHERE guild_id=? AND user_id=? AND tipo=?", (guild_id, user_id, tipo))
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    return datetime.fromisoformat(row[0])

def set_cooldown_generic(guild_id: int, user_id: int, tipo: str):
    ahora = datetime.now(timezone.utc).isoformat()
    cursor.execute("""INSERT INTO cooldowns (guild_id, user_id, tipo, ultimo) VALUES (?,?,?,?)
        ON CONFLICT(guild_id, user_id, tipo) DO UPDATE SET ultimo = excluded.ultimo""",
        (guild_id, user_id, tipo, ahora))
    db.commit()

def tiempo_restante(ultimo: datetime, segundos: int):
    if not ultimo:
        return None
    fin = ultimo + timedelta(seconds=segundos)
    ahora = datetime.now(timezone.utc)
    if ahora >= fin:
        return None
    return fin - ahora

def formatear_tiempo(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    h, resto = divmod(total, 3600)
    m, s = divmod(resto, 60)
    partes = []
    if h: partes.append(f"{h}h")
    if m: partes.append(f"{m}m")
    if s or not partes: partes.append(f"{s}s")
    return " ".join(partes)

def get_inventario(guild_id: int, user_id: int):
    cursor.execute("SELECT item, cantidad FROM inventario WHERE guild_id=? AND user_id=? AND cantidad > 0 ORDER BY item", (guild_id, user_id))
    return cursor.fetchall()

def add_item(guild_id: int, user_id: int, item: str, cantidad: int = 1):
    cursor.execute("""INSERT INTO inventario (guild_id, user_id, item, cantidad) VALUES (?,?,?,?)
        ON CONFLICT(guild_id, user_id, item) DO UPDATE SET cantidad = cantidad + excluded.cantidad""",
        (guild_id, user_id, item, cantidad))
    db.commit()

def get_tienda(guild_id: int):
    cursor.execute("SELECT id, nombre, precio, descripcion, emoji, usable FROM tienda WHERE guild_id=? ORDER BY precio ASC", (guild_id,))
    return cursor.fetchall()

def get_item_tienda(guild_id: int, nombre: str):
    cursor.execute("SELECT id, nombre, precio, descripcion, emoji, usable, mensaje_uso FROM tienda WHERE guild_id=? AND LOWER(nombre)=LOWER(?)", (guild_id, nombre))
    return cursor.fetchone()

# ─────────────────────────────────────────
# HELPERS CASINO
# ─────────────────────────────────────────
SLOT_EMOJIS = ["🍒", "🍋", "🍊", "🍇", "🔔", "⭐", "💎"]
SLOT_WEIGHTS = [30, 25, 20, 15, 6, 3, 1]  # entre más raro, más vale

ROULETTE_ROJOS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

def color_ruleta(numero: int) -> str:
    if numero == 0:
        return "verde"
    return "rojo" if numero in ROULETTE_ROJOS else "negro"

PALOS = ["♠", "♥", "♦", "♣"]
RANGOS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

def crear_mazo():
    mazo = [f"{r}{p}" for p in PALOS for r in RANGOS]
    random.shuffle(mazo)
    return mazo

def valor_carta(carta: str) -> int:
    rango = carta[:-1]
    if rango in ("J", "Q", "K"):
        return 10
    if rango == "A":
        return 11
    return int(rango)

def valor_mano(cartas) -> int:
    total = sum(valor_carta(c) for c in cartas)
    ases = sum(1 for c in cartas if c.startswith("A"))
    while total > 21 and ases:
        total -= 10
        ases -= 1
    return total

class BlackjackView(discord.ui.View):
    def __init__(self, ctx: commands.Context, apuesta: int, jugador, dealer, mazo):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.apuesta = apuesta
        self.jugador = jugador
        self.dealer = dealer
        self.mazo = mazo
        self.terminado = False
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Esta partida no es tuya we", ephemeral=True)
            return False
        return True

    def construir_embed(self, revelar_dealer: bool = False, resultado: str = None, color: int = 0x3498DB) -> discord.Embed:
        emb = discord.Embed(title="🃏 Blackjack", color=color)
        emb.add_field(name=f"Tu mano ({valor_mano(self.jugador)})", value=" ".join(self.jugador), inline=False)
        if revelar_dealer:
            emb.add_field(name=f"Mano del dealer ({valor_mano(self.dealer)})", value=" ".join(self.dealer), inline=False)
        else:
            emb.add_field(name="Mano del dealer", value=f"{self.dealer[0]} 🂠", inline=False)
        if resultado:
            emb.description = resultado
        emb.set_footer(text=f"Apuesta: {format_dinero(self.ctx.guild.id, self.apuesta)}")
        return emb

    async def finalizar(self, interaction: discord.Interaction, ganancia: int, resultado_texto: str, color: int):
        self.terminado = True
        for item in self.children:
            item.disabled = True
        if ganancia > 0:
            modificar_balance(self.ctx.guild.id, self.ctx.author.id, ganancia)
        emb = self.construir_embed(revelar_dealer=True, resultado=resultado_texto, color=color)
        await interaction.response.edit_message(embed=emb, view=self)
        self.stop()

    @discord.ui.button(label="Pedir", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.terminado:
            return
        self.jugador.append(self.mazo.pop())
        if valor_mano(self.jugador) > 21:
            await self.finalizar(interaction, 0, f"💥 Te pasaste de 21. Perdiste **{format_dinero(self.ctx.guild.id, self.apuesta)}**", 0xE74C3C)
        else:
            await interaction.response.edit_message(embed=self.construir_embed(), view=self)

    @discord.ui.button(label="Plantarse", style=discord.ButtonStyle.secondary, emoji="✋")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.terminado:
            return
        while valor_mano(self.dealer) < 17:
            self.dealer.append(self.mazo.pop())
        pj, pd = valor_mano(self.jugador), valor_mano(self.dealer)
        if pd > 21 or pj > pd:
            ganancia = self.apuesta * 2
            texto = f"🎉 ¡Ganaste! ({pj} vs {pd}). Ganaste **{format_dinero(self.ctx.guild.id, ganancia)}**"
            color = 0x2ECC71
        elif pj == pd:
            ganancia = self.apuesta
            texto = f"🤝 Empate ({pj} vs {pd}). Recuperas tu apuesta"
            color = 0xF1C40F
        else:
            ganancia = 0
            texto = f"😢 Perdiste ({pj} vs {pd})"
            color = 0xE74C3C
        await self.finalizar(interaction, ganancia, texto, color)

    async def on_timeout(self):
        if not self.terminado:
            self.terminado = True
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    pass

# ─────────────────────────────────────────
# COG ECONOMÍA
# ─────────────────────────────────────────
TRABAJOS = [
    "repartiste pizza toda la noche",
    "le hiciste la tarea a un cabro de colegio",
    "vendiste completos en la esquina",
    "hiciste de streamer 2 horas",
    "ayudaste a mudar un piano",
    "cuidaste perros del vecino",
]

CRIMENES = [
    "intentaste clonar una tarjeta",
    "le robaste el WiFi al vecino",
    "vendiste copias piratas",
    "asaltaste un kiosko",
    "hackeaste una cuenta de Netflix",
]

class EconomiaCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── BÁSICOS ──
    @commands.command(name="balance", aliases=["bal", "plata"])
    async def balance(self, ctx: commands.Context, user: discord.Member = None):
        user = user or ctx.author
        saldo = get_balance(ctx.guild.id, user.id)
        embed = discord.Embed(title="💰 Balance", description=f"{user.mention} tiene **{format_dinero(ctx.guild.id, saldo)}**", color=0x2ECC71)
        embed.set_thumbnail(url=user.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(name="trabajo", aliases=["work"])
    async def trabajo(self, ctx: commands.Context):
        cfg = get_config(ctx.guild.id)
        restante = tiempo_restante(get_cooldown(ctx.guild.id, ctx.author.id, "last_trabajo"), cfg["cooldown_trabajo"])
        if restante:
            return await send_msg(ctx, f"⏳ Ya trabajaste we, vuelve en **{formatear_tiempo(restante)}**", title="💼 Trabajo")
        frase = random.choice(TRABAJOS)
        ganancia = random.randint(cfg["trabajo_min"], cfg["trabajo_max"])
        modificar_balance(ctx.guild.id, ctx.author.id, ganancia)
        set_cooldown(ctx.guild.id, ctx.author.id, "last_trabajo")
        await send_msg(ctx, f"{ctx.author.mention} {frase} y ganaste **{format_dinero(ctx.guild.id, ganancia)}**", title="💼 Trabajo")

    @commands.command(name="crime")
    async def crime(self, ctx: commands.Context):
        cfg = get_config(ctx.guild.id)
        restante = tiempo_restante(get_cooldown(ctx.guild.id, ctx.author.id, "last_crime"), cfg["cooldown_crime"])
        if restante:
            return await send_msg(ctx, f"⏳ Todavía te andan buscando we, espera **{formatear_tiempo(restante)}**", title="🕶️ Crime")
        set_cooldown(ctx.guild.id, ctx.author.id, "last_crime")
        frase = random.choice(CRIMENES)
        if random.random() < cfg["crime_chance"]:
            ganancia = random.randint(cfg["crime_win_min"], cfg["crime_win_max"])
            modificar_balance(ctx.guild.id, ctx.author.id, ganancia)
            await send_msg(ctx, f"{ctx.author.mention} {frase} y te saliste con la tuya. Ganaste **{format_dinero(ctx.guild.id, ganancia)}**", title="🕶️ Crime")
        else:
            perdida = random.randint(cfg["crime_loss_min"], cfg["crime_loss_max"])
            modificar_balance(ctx.guild.id, ctx.author.id, -perdida)
            await send_msg(ctx, f"🚓 {ctx.author.mention} {frase} pero te pillaron. Perdiste **{format_dinero(ctx.guild.id, perdida)}**", title="🕶️ Crime", color=0xE74C3C)

    @commands.command(name="robar", aliases=["rob"])
    async def robar(self, ctx: commands.Context, victima: discord.Member = None):
        if not victima:
            return await send_msg(ctx, "Dime a quién robar we. Uso: `!robar @user`", title="🥷 Robar")
        if victima.id == ctx.author.id:
            return await send_msg(ctx, "No te puedes robar a ti mismo we", title="🥷 Robar", color=0xE74C3C)
        if victima.bot:
            return await send_msg(ctx, "A los bots no se les roba we", title="🥷 Robar", color=0xE74C3C)

        cfg = get_config(ctx.guild.id)
        restante = tiempo_restante(get_cooldown(ctx.guild.id, ctx.author.id, "last_robar"), cfg["cooldown_robar"])
        if restante:
            return await send_msg(ctx, f"⏳ Ya intentaste robar, espera **{formatear_tiempo(restante)}**", title="🥷 Robar")

        saldo_victima = get_balance(ctx.guild.id, victima.id)
        if saldo_victima < cfg["robar_min_balance"]:
            return await send_msg(ctx, f"{victima.mention} anda más pelado que tú, no vale la pena robarle we", title="🥷 Robar")

        set_cooldown(ctx.guild.id, ctx.author.id, "last_robar")

        if random.random() < cfg["robar_chance"]:
            porcentaje = random.uniform(cfg["robar_min_pct"], cfg["robar_max_pct"])
            robado = min(int(saldo_victima * porcentaje), cfg["robar_max_cap"])
            modificar_balance(ctx.guild.id, victima.id, -robado)
            modificar_balance(ctx.guild.id, ctx.author.id, robado)
            await send_msg(ctx, f"{ctx.author.mention} le robó **{format_dinero(ctx.guild.id, robado)}** a {victima.mention}", title="🥷 Robar")
        else:
            multa = random.randint(cfg["robar_fail_min"], cfg["robar_fail_max"])
            modificar_balance(ctx.guild.id, ctx.author.id, -multa)
            modificar_balance(ctx.guild.id, victima.id, multa)
            await send_msg(ctx, f"🚨 {ctx.author.mention} intentó robarle a {victima.mention} pero lo pillaron y pagó una multa de **{format_dinero(ctx.guild.id, multa)}**", title="🥷 Robar", color=0xE74C3C)

    @commands.command(name="dar", aliases=["pay", "give"])
    async def dar(self, ctx: commands.Context, user: discord.Member = None, cantidad: int = None):
        if not user or cantidad is None:
            return await send_msg(ctx, "Uso: `!dar @user <cantidad>`", title="🤝 Dar")
        if user.id == ctx.author.id:
            return await send_msg(ctx, "No te puedes dar plata a ti mismo we", title="🤝 Dar", color=0xE74C3C)
        if cantidad <= 0:
            return await send_msg(ctx, "La cantidad tiene que ser mayor a 0 we", title="🤝 Dar", color=0xE74C3C)
        saldo = get_balance(ctx.guild.id, ctx.author.id)
        if saldo < cantidad:
            return await send_msg(ctx, f"No tienes esa plata we, tu balance es **{format_dinero(ctx.guild.id, saldo)}**", title="🤝 Dar", color=0xE74C3C)
        modificar_balance(ctx.guild.id, ctx.author.id, -cantidad)
        modificar_balance(ctx.guild.id, user.id, cantidad)
        await send_msg(ctx, f"{ctx.author.mention} le dio **{format_dinero(ctx.guild.id, cantidad)}** a {user.mention}", title="🤝 Dar")

    @commands.command(name="add$", aliases=["addmoney"])
    @is_staff_ctx()
    async def add_money(self, ctx: commands.Context, user: discord.Member = None, cantidad: int = None):
        if not user or cantidad is None:
            return await send_msg(ctx, "Uso: `!add$ @user <cantidad>`", title="💰 Staff")
        if cantidad <= 0:
            return await send_msg(ctx, "La cantidad tiene que ser mayor a 0 we", title="💰 Staff", color=0xE74C3C)
        modificar_balance(ctx.guild.id, user.id, cantidad)
        nuevo = get_balance(ctx.guild.id, user.id)
        await send_msg(ctx, f"Se le agregaron **{format_dinero(ctx.guild.id, cantidad)}** a {user.mention}. Nuevo balance: **{format_dinero(ctx.guild.id, nuevo)}**", title="✅ Plata agregada")

    @commands.command(name="remove$", aliases=["removemoney"])
    @is_staff_ctx()
    async def remove_money(self, ctx: commands.Context, user: discord.Member = None, cantidad: int = None):
        if not user or cantidad is None:
            return await send_msg(ctx, "Uso: `!remove$ @user <cantidad>`", title="💰 Staff")
        if cantidad <= 0:
            return await send_msg(ctx, "La cantidad tiene que ser mayor a 0 we", title="💰 Staff", color=0xE74C3C)
        modificar_balance(ctx.guild.id, user.id, -cantidad)
        nuevo = get_balance(ctx.guild.id, user.id)
        await send_msg(ctx, f"Se le quitaron **{format_dinero(ctx.guild.id, cantidad)}** a {user.mention}. Nuevo balance: **{format_dinero(ctx.guild.id, nuevo)}**", title="✅ Plata quitada")

    # ── LEADERBOARD ──
    @commands.command(name="leaderboard", aliases=["top", "ranking"])
    async def leaderboard(self, ctx: commands.Context):
        cursor.execute("SELECT user_id, balance FROM economia WHERE guild_id=? ORDER BY balance DESC LIMIT 10", (ctx.guild.id,))
        rows = cursor.fetchall()
        if not rows:
            return await send_msg(ctx, "Todavía no hay nadie con plata we", title="🏆 Leaderboard")
        medallas = ["🥇", "🥈", "🥉"]
        embed = discord.Embed(title="🏆 Leaderboard — Los más ricos", color=0xF1C40F)
        lineas = []
        for i, (uid, bal) in enumerate(rows):
            member = ctx.guild.get_member(uid)
            nombre = member.display_name if member else f"ID:{uid}"
            prefijo = medallas[i] if i < 3 else f"**{i+1}.**"
            lineas.append(f"{prefijo} {nombre} — {format_dinero(ctx.guild.id, bal)}")
        embed.description = "\n".join(lineas)
        embed.set_footer(text=f"Servidor: {ctx.guild.name}")
        await ctx.send(embed=embed)

    # ── TIENDA ──
    @commands.command(name="tienda", aliases=["shop"])
    async def tienda_cmd(self, ctx: commands.Context):
        items = get_tienda(ctx.guild.id)
        if not items:
            return await send_msg(ctx, "La tienda está vacía we, un staff puede agregar items con `!additem`", title="🛒 Tienda")
        embed = discord.Embed(title="🛒 Tienda", color=0x3498DB)
        for _id, nombre, precio, descripcion, emoji, usable in items:
            etiqueta = "\n*Usable con `!useitem`*" if usable else ""
            embed.add_field(name=f"{emoji} {nombre} — {format_dinero(ctx.guild.id, precio)}", value=(descripcion or "\u200b") + etiqueta, inline=False)
        embed.set_footer(text="Usa !comprar <nombre> para comprar")
        await ctx.send(embed=embed)

    @commands.command(name="comprar", aliases=["buy"])
    async def comprar(self, ctx: commands.Context, *, nombre: str = None):
        if not nombre:
            return await send_msg(ctx, "Uso: `!comprar <nombre del item>`", title="🛒 Comprar")
        item = get_item_tienda(ctx.guild.id, nombre)
        if not item:
            return await send_msg(ctx, f"No existe el item `{nombre}` en la tienda we", title="🛒 Comprar", color=0xE74C3C)
        _id, nombre_real, precio, descripcion, emoji, usable, mensaje_uso = item
        saldo = get_balance(ctx.guild.id, ctx.author.id)
        if saldo < precio:
            return await send_msg(ctx, f"No tienes suficiente plata we. Necesitas **{format_dinero(ctx.guild.id, precio)}** y tienes **{format_dinero(ctx.guild.id, saldo)}**", title="🛒 Comprar", color=0xE74C3C)
        modificar_balance(ctx.guild.id, ctx.author.id, -precio)
        add_item(ctx.guild.id, ctx.author.id, nombre_real, 1)
        await send_msg(ctx, f"{ctx.author.mention} compró **{emoji} {nombre_real}** por **{format_dinero(ctx.guild.id, precio)}**", title="✅ Compra exitosa")

    @commands.command(name="inventario", aliases=["inv"])
    async def inventario_cmd(self, ctx: commands.Context, user: discord.Member = None):
        user = user or ctx.author
        items = get_inventario(ctx.guild.id, user.id)
        if not items:
            return await send_msg(ctx, f"{user.mention} no tiene items we", title="🎒 Inventario")
        embed = discord.Embed(title=f"🎒 Inventario de {user.display_name}", color=0x9B59B6)
        lineas = []
        for item, cantidad in items:
            info = get_item_tienda(ctx.guild.id, item)
            emoji = info[4] if info else "📦"
            lineas.append(f"{emoji} **{item}** x{cantidad}")
        embed.description = "\n".join(lineas)
        await ctx.send(embed=embed)

    @commands.command(name="useitem", aliases=["usaritem", "useit"])
    async def useitem_cmd(self, ctx: commands.Context, *, nombre: str = None):
        if not nombre:
            return await send_msg(ctx, "Uso: `!useitem <nombre del item>`", title="🎒 Usar item")
        inventario = get_inventario(ctx.guild.id, ctx.author.id)
        encontrado = next((it for it in inventario if it[0].lower() == nombre.lower()), None)
        if not encontrado:
            return await send_msg(ctx, f"No tienes el item `{nombre}` we", title="🎒 Usar item", color=0xE74C3C)
        item_nombre, _cantidad = encontrado
        info = get_item_tienda(ctx.guild.id, item_nombre)
        emoji = info[4] if info else "📦"
        usable = info[5] if info else 0
        mensaje_uso = info[6] if info else ""
        if not usable:
            return await send_msg(ctx, f"**{item_nombre}** no se puede usar we, es solo de colección", title="🎒 Usar item", color=0xE74C3C)
        add_item(ctx.guild.id, ctx.author.id, item_nombre, -1)
        texto = mensaje_uso or f"Usaste **{item_nombre}**."
        texto = texto.replace("{user}", ctx.author.mention).replace("{item}", item_nombre)
        await ctx.send(embed=discord.Embed(title=f"{emoji} Item usado", description=texto, color=0x9B59B6))

    @commands.command(name="additem")
    @is_staff_ctx()
    async def additem(self, ctx: commands.Context, nombre: str, precio: int, *, descripcion: str = ""):
        if precio <= 0:
            return await send_msg(ctx, "El precio tiene que ser mayor a 0 we", title="🛒 Staff", color=0xE74C3C)
        existente = get_item_tienda(ctx.guild.id, nombre)
        if existente:
            return await send_msg(ctx, f"Ya existe un item llamado `{nombre}` we, usa `!delitem` primero si quieres reemplazarlo", title="🛒 Staff", color=0xE74C3C)
        cursor.execute("INSERT INTO tienda (guild_id, nombre, precio, descripcion, emoji, usable, mensaje_uso) VALUES (?,?,?,?,?,?,?)",
                       (ctx.guild.id, nombre, precio, descripcion, "📦", 0, ""))
        db.commit()
        await send_msg(ctx, f"Agregado **{nombre}** a la tienda por **{format_dinero(ctx.guild.id, precio)}**\nUsa `!edititem {nombre} emoji <emoji>` o `!edititem {nombre} usable si` para personalizarlo.", title="✅ Item agregado")

    @commands.command(name="edititem")
    @is_staff_ctx()
    async def edititem(self, ctx: commands.Context, nombre: str = None, campo: str = None, *, valor: str = None):
        if not nombre or not campo or valor is None:
            return await send_msg(ctx,
                "Uso: `!edititem <nombre> <campo> <valor>`\n"
                "Campos disponibles: `nombre`, `precio`, `descripcion`, `emoji`, `usable` (si/no), `mensaje` (usa `{user}` y `{item}`)",
                title="✏️ Editar item")
        item = get_item_tienda(ctx.guild.id, nombre)
        if not item:
            return await send_msg(ctx, f"No existe el item `{nombre}` we", title="✏️ Editar item", color=0xE74C3C)
        _id = item[0]
        campo = campo.lower()
        if campo == "nombre":
            cursor.execute("UPDATE tienda SET nombre=? WHERE id=?", (valor, _id))
        elif campo == "precio":
            try:
                precio = int(valor)
            except ValueError:
                return await send_msg(ctx, "❌ El precio tiene que ser un número we", title="✏️ Editar item", color=0xE74C3C)
            if precio <= 0:
                return await send_msg(ctx, "❌ El precio tiene que ser mayor a 0 we", title="✏️ Editar item", color=0xE74C3C)
            cursor.execute("UPDATE tienda SET precio=? WHERE id=?", (precio, _id))
        elif campo == "descripcion":
            cursor.execute("UPDATE tienda SET descripcion=? WHERE id=?", (valor, _id))
        elif campo == "emoji":
            cursor.execute("UPDATE tienda SET emoji=? WHERE id=?", (valor, _id))
        elif campo in ("usable", "usar"):
            usable = 1 if valor.lower() in ("si", "sí", "true", "1", "yes") else 0
            cursor.execute("UPDATE tienda SET usable=? WHERE id=?", (usable, _id))
        elif campo in ("mensaje", "mensaje_uso"):
            cursor.execute("UPDATE tienda SET mensaje_uso=? WHERE id=?", (valor, _id))
        else:
            return await send_msg(ctx, f"❌ No reconozco el campo `{campo}` we. Usa: nombre, precio, descripcion, emoji, usable, mensaje", title="✏️ Editar item", color=0xE74C3C)
        db.commit()
        await send_msg(ctx, f"El item **{nombre}** fue actualizado (`{campo}` → `{valor}`)", title="✅ Item editado")

    @commands.command(name="delitem")
    @is_staff_ctx()
    async def delitem(self, ctx: commands.Context, *, nombre: str = None):
        if not nombre:
            return await send_msg(ctx, "Uso: `!delitem <nombre>`", title="🛒 Staff")
        item = get_item_tienda(ctx.guild.id, nombre)
        if not item:
            return await send_msg(ctx, f"No existe el item `{nombre}` we", title="🛒 Staff", color=0xE74C3C)
        cursor.execute("DELETE FROM tienda WHERE id=?", (item[0],))
        db.commit()
        await send_msg(ctx, f"Item **{nombre}** eliminado de la tienda", title="🗑️ Item eliminado")

    # ── MONEDA ──
    @commands.command(name="setmoneda", aliases=["seticono", "setcurrency"])
    @is_staff_ctx()
    async def setmoneda_cmd(self, ctx: commands.Context, emoji: str = None):
        if not emoji:
            return await send_msg(ctx, "Uso: `!setmoneda <emoji>` — puede ser un emoji normal 😀 o uno personalizado del server (escríbelo tal cual, ej: `<:nombre:1234567890>`)", title="🪙 Moneda del servidor")
        set_config_field(ctx.guild.id, "moneda_emoji", emoji)
        await ctx.send(embed=discord.Embed(title="✅ Moneda actualizada", description=f"La moneda del servidor ahora es: {emoji}", color=0x2ECC71))

    # ── CONFIGURACIÓN ──
    @commands.command(name="config")
    @is_staff_ctx()
    async def config_cmd(self, ctx: commands.Context):
        cfg = get_config(ctx.guild.id)
        emb1 = discord.Embed(title="⚙️ Configuración — Economía", color=0x95A5A6,
                              description="Usa `!setconfig <clave> <valor>` para cambiar un valor.")
        for campo, (tipo, desc) in CONFIG_FIELDS_ECONOMIA.items():
            emb1.add_field(name=campo, value=f"Valor actual: `{cfg[campo]}`\n{desc}", inline=False)
        emb2 = discord.Embed(title="🎰 Configuración — Casino", color=0x95A5A6,
                              description=f"Moneda del servidor: {get_moneda(ctx.guild.id)} (cámbiala con `!setmoneda`)")
        for campo, (tipo, desc) in CONFIG_FIELDS_CASINO.items():
            emb2.add_field(name=campo, value=f"Valor actual: `{cfg[campo]}`\n{desc}", inline=False)
        await ctx.send(embeds=[emb1, emb2])

    @commands.command(name="setconfig")
    @is_staff_ctx()
    async def setconfig_cmd(self, ctx: commands.Context, campo: str = None, valor: str = None):
        if not campo or valor is None:
            return await send_msg(ctx, "Uso: `!setconfig <clave> <valor>`. Usa `!config` para ver las claves disponibles.", title="⚙️ Configuración")
        campo = campo.lower()
        if campo not in CONFIG_FIELDS:
            return await send_msg(ctx, f"❌ No existe la clave `{campo}` we. Usa `!config` para ver las disponibles.", title="⚙️ Configuración", color=0xE74C3C)
        tipo, _desc = CONFIG_FIELDS[campo]
        try:
            valor_convertido = int(valor) if tipo == "int" else float(valor)
        except ValueError:
            return await send_msg(ctx, f"❌ `{campo}` espera un valor tipo `{tipo}` we", title="⚙️ Configuración", color=0xE74C3C)
        if tipo == "float" and not (0 <= valor_convertido <= 1):
            return await send_msg(ctx, "❌ Ese valor tiene que estar entre 0 y 1 we", title="⚙️ Configuración", color=0xE74C3C)
        set_config_field(ctx.guild.id, campo, valor_convertido)
        await send_msg(ctx, f"`{campo}` ahora vale `{valor_convertido}`", title="✅ Configuración actualizada")

    @commands.command(name="setcooldown", aliases=["settiempo"])
    @is_staff_ctx()
    async def setcooldown_cmd(self, ctx: commands.Context, comando: str = None, *, tiempo: str = None):
        if not comando or not tiempo:
            opciones = ", ".join(sorted(set(MAPA_COOLDOWNS.keys())))
            return await send_msg(ctx,
                f"Uso: `!setcooldown <comando> <tiempo>`\nEjemplo: `!setcooldown trabajo 1h30m`\nComandos disponibles: {opciones}",
                title="⏱️ Configurar cooldowns")
        comando = comando.lower()
        if comando not in MAPA_COOLDOWNS:
            return await send_msg(ctx, f"❌ No reconozco el comando `{comando}` we", title="⏱️ Configurar cooldowns", color=0xE74C3C)
        segundos = parse_tiempo(tiempo)
        if segundos is None or segundos <= 0:
            return await send_msg(ctx, "❌ Formato de tiempo inválido. Usa algo como `30s`, `10m`, `2h` o `1h30m`", title="⏱️ Configurar cooldowns", color=0xE74C3C)
        campo = MAPA_COOLDOWNS[comando]
        set_config_field(ctx.guild.id, campo, segundos)
        await ctx.send(embed=discord.Embed(
            title="✅ Cooldown actualizado",
            description=f"El cooldown de **{comando}** ahora es de **{formatear_tiempo(timedelta(seconds=segundos))}**",
            color=0x2ECC71))

    @commands.command(name="resetconfig")
    @is_staff_ctx()
    async def resetconfig_cmd(self, ctx: commands.Context):
        cursor.execute("DELETE FROM config WHERE guild_id=?", (ctx.guild.id,))
        db.commit()
        get_config(ctx.guild.id)
        await send_msg(ctx, "Configuración restablecida a los valores por defecto", title="✅ Configuración")

# ─────────────────────────────────────────
# COG CASINO
# ─────────────────────────────────────────
class CasinoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _validar_apuesta(self, ctx: commands.Context, cfg: dict, apuesta) -> bool:
        if apuesta is None or apuesta <= 0:
            await send_msg(ctx, "Tienes que apostar una cantidad válida we", title="🎰 Casino", color=0xE74C3C)
            return False
        if apuesta < cfg["apuesta_min"] or apuesta > cfg["apuesta_max"]:
            await send_msg(ctx, f"La apuesta debe estar entre **{format_dinero(ctx.guild.id, cfg['apuesta_min'])}** y **{format_dinero(ctx.guild.id, cfg['apuesta_max'])}**", title="🎰 Casino", color=0xE74C3C)
            return False
        saldo = get_balance(ctx.guild.id, ctx.author.id)
        if saldo < apuesta:
            await send_msg(ctx, f"No tienes esa plata we, tu balance es **{format_dinero(ctx.guild.id, saldo)}**", title="🎰 Casino", color=0xE74C3C)
            return False
        return True

    @commands.command(name="slots", aliases=["tragamonedas", "slot"])
    async def slots(self, ctx: commands.Context, apuesta: int = None):
        cfg = get_config(ctx.guild.id)
        restante = tiempo_restante(get_cooldown_generic(ctx.guild.id, ctx.author.id, "slots"), cfg["cooldown_slots"])
        if restante:
            return await send_msg(ctx, f"⏳ Espera **{formatear_tiempo(restante)}** para volver a jugar", title="🎰 Tragamonedas")
        if not await self._validar_apuesta(ctx, cfg, apuesta):
            return
        set_cooldown_generic(ctx.guild.id, ctx.author.id, "slots")
        modificar_balance(ctx.guild.id, ctx.author.id, -apuesta)
        reels = random.choices(SLOT_EMOJIS, weights=SLOT_WEIGHTS, k=3)
        if reels[0] == reels[1] == reels[2]:
            ganancia = apuesta * cfg["slots_multi_x3"]
        elif len(set(reels)) == 2:
            ganancia = apuesta * cfg["slots_multi_x2"]
        else:
            ganancia = 0
        if ganancia:
            modificar_balance(ctx.guild.id, ctx.author.id, ganancia)
        resultado = " | ".join(reels)
        if ganancia > 0:
            desc = f"[ {resultado} ]\n\n🎉 ¡Ganaste **{format_dinero(ctx.guild.id, ganancia)}**!"
            color = 0x2ECC71
        else:
            desc = f"[ {resultado} ]\n\n💸 Perdiste **{format_dinero(ctx.guild.id, apuesta)}**"
            color = 0xE74C3C
        await ctx.send(embed=discord.Embed(title="🎰 Tragamonedas", description=desc, color=color))

    @commands.command(name="ruleta", aliases=["roulette"])
    async def ruleta(self, ctx: commands.Context, apuesta: int = None, color: str = None):
        mapa_colores = {"rojo": "rojo", "red": "rojo", "negro": "negro", "black": "negro", "verde": "verde", "green": "verde"}
        if not color or color.lower() not in mapa_colores:
            return await send_msg(ctx, "Uso: `!ruleta <apuesta> <rojo/negro/verde>`", title="🎡 Ruleta")
        color_elegido = mapa_colores[color.lower()]
        cfg = get_config(ctx.guild.id)
        restante = tiempo_restante(get_cooldown_generic(ctx.guild.id, ctx.author.id, "ruleta"), cfg["cooldown_ruleta"])
        if restante:
            return await send_msg(ctx, f"⏳ Espera **{formatear_tiempo(restante)}** para volver a jugar", title="🎡 Ruleta")
        if not await self._validar_apuesta(ctx, cfg, apuesta):
            return
        set_cooldown_generic(ctx.guild.id, ctx.author.id, "ruleta")
        modificar_balance(ctx.guild.id, ctx.author.id, -apuesta)
        numero = random.randint(0, 36)
        resultado_color = color_ruleta(numero)
        emojis_color = {"rojo": "🔴", "negro": "⚫", "verde": "🟢"}
        if color_elegido == resultado_color:
            multi = cfg["ruleta_multi_verde"] if color_elegido == "verde" else cfg["ruleta_multi_color"]
            ganancia = int(apuesta * multi)
            modificar_balance(ctx.guild.id, ctx.author.id, ganancia)
            desc = f"La bola cayó en **{numero} {emojis_color[resultado_color]}**\n\n🎉 ¡Acertaste! Ganaste **{format_dinero(ctx.guild.id, ganancia)}**"
            color_embed = 0x2ECC71
        else:
            desc = f"La bola cayó en **{numero} {emojis_color[resultado_color]}**\n\n💸 Perdiste **{format_dinero(ctx.guild.id, apuesta)}**"
            color_embed = 0xE74C3C
        await ctx.send(embed=discord.Embed(title="🎡 Ruleta", description=desc, color=color_embed))

    @commands.command(name="coinflip", aliases=["cf"])
    async def coinflip(self, ctx: commands.Context, apuesta: int = None, lado: str = None):
        mapa = {"cara": "cara", "heads": "cara", "cruz": "cruz", "cola": "cruz", "tails": "cruz"}
        if not lado or lado.lower() not in mapa:
            return await send_msg(ctx, "Uso: `!coinflip <apuesta> <cara/cruz>`", title="🪙 Coinflip")
        lado_elegido = mapa[lado.lower()]
        cfg = get_config(ctx.guild.id)
        restante = tiempo_restante(get_cooldown_generic(ctx.guild.id, ctx.author.id, "coinflip"), cfg["cooldown_coinflip"])
        if restante:
            return await send_msg(ctx, f"⏳ Espera **{formatear_tiempo(restante)}** para volver a jugar", title="🪙 Coinflip")
        if not await self._validar_apuesta(ctx, cfg, apuesta):
            return
        set_cooldown_generic(ctx.guild.id, ctx.author.id, "coinflip")
        modificar_balance(ctx.guild.id, ctx.author.id, -apuesta)
        resultado = random.choice(["cara", "cruz"])
        emoji_resultado = "🙂" if resultado == "cara" else "🌀"
        if lado_elegido == resultado:
            ganancia = apuesta * 2
            modificar_balance(ctx.guild.id, ctx.author.id, ganancia)
            desc = f"Salió **{resultado} {emoji_resultado}**\n\n🎉 ¡Ganaste **{format_dinero(ctx.guild.id, ganancia)}**!"
            color = 0x2ECC71
        else:
            desc = f"Salió **{resultado} {emoji_resultado}**\n\n💸 Perdiste **{format_dinero(ctx.guild.id, apuesta)}**"
            color = 0xE74C3C
        await ctx.send(embed=discord.Embed(title="🪙 Coinflip", description=desc, color=color))

    @commands.command(name="blackjack", aliases=["bj"])
    async def blackjack(self, ctx: commands.Context, apuesta: int = None):
        cfg = get_config(ctx.guild.id)
        restante = tiempo_restante(get_cooldown_generic(ctx.guild.id, ctx.author.id, "blackjack"), cfg["cooldown_blackjack"])
        if restante:
            return await send_msg(ctx, f"⏳ Espera **{formatear_tiempo(restante)}** antes de otra partida", title="🃏 Blackjack")
        if not await self._validar_apuesta(ctx, cfg, apuesta):
            return
        set_cooldown_generic(ctx.guild.id, ctx.author.id, "blackjack")
        modificar_balance(ctx.guild.id, ctx.author.id, -apuesta)
        mazo = crear_mazo()
        jugador = [mazo.pop(), mazo.pop()]
        dealer = [mazo.pop(), mazo.pop()]

        if valor_mano(jugador) == 21:
            if valor_mano(dealer) == 21:
                modificar_balance(ctx.guild.id, ctx.author.id, apuesta)
                texto = "🤝 Ambos tienen Blackjack, empate. Recuperas tu apuesta"
                color = 0xF1C40F
            else:
                ganancia = int(apuesta * 2.5)
                modificar_balance(ctx.guild.id, ctx.author.id, ganancia)
                texto = f"🂡 ¡Blackjack! Ganaste **{format_dinero(ctx.guild.id, ganancia)}**"
                color = 0x2ECC71
            emb = discord.Embed(title="🃏 Blackjack", description=texto, color=color)
            emb.add_field(name=f"Tu mano ({valor_mano(jugador)})", value=" ".join(jugador), inline=False)
            emb.add_field(name=f"Mano del dealer ({valor_mano(dealer)})", value=" ".join(dealer), inline=False)
            return await ctx.send(embed=emb)

        view = BlackjackView(ctx, apuesta, jugador, dealer, mazo)
        msg = await ctx.send(embed=view.construir_embed(), view=view)
        view.message = msg

# ─────────────────────────────────────────
# COG HELP
# ─────────────────────────────────────────
class HelpCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="help", description="Muestra todos los comandos de Teto")
    async def help(self, interaction: discord.Interaction):
        staff = interaction.user.id == TU_ID or any(r.name in ROLES_COMANDOS for r in interaction.user.roles)
        embed = discord.Embed(title="📖 Comandos de Teto", color=0x3498DB,
                              description="Bot de economía. Todos los comandos usan el prefijo `!`.")
        embed.set_thumbnail(url=interaction.guild.me.display_avatar.url)

        embed.add_field(name="💰 Economía", value=(
            "`!balance` — Ve tu plata\n"
            "`!trabajo` — Trabaja y gana plata\n"
            "`!crime` — Arriésgate a ganar o perder plata\n"
            "`!robar @user` — Intenta robarle a alguien\n"
            "`!dar @user <cantidad>` — Regala plata a otro\n"
            "`!leaderboard` — Ranking de los más ricos"
        ), inline=False)
        embed.add_field(name="🛒 Tienda", value=(
            "`!tienda` — Ve los items disponibles\n"
            "`!comprar <item>` — Compra un item\n"
            "`!inventario` — Ve tu inventario\n"
            "`!useitem <item>` — Usa un item de tu inventario"
        ), inline=False)
        embed.add_field(name="🎰 Casino", value=(
            "`!slots <apuesta>` — Tragamonedas\n"
            "`!ruleta <apuesta> <rojo/negro/verde>` — Ruleta\n"
            "`!coinflip <apuesta> <cara/cruz>` — Cara o cruz\n"
            "`!blackjack <apuesta>` — Blackjack contra el dealer"
        ), inline=False)

        if staff:
            embed.add_field(name="💰 Economía Staff 🔒", value=(
                "`!add$ @user <cantidad>` — Agrega plata a un usuario\n"
                "`!remove$ @user <cantidad>` — Quita plata a un usuario"
            ), inline=False)
            embed.add_field(name="🛒 Tienda Staff 🔒", value=(
                "`!additem <nombre> <precio> <descripción>` — Agrega un item a la tienda\n"
                "`!edititem <nombre> <campo> <valor>` — Edita nombre/precio/descripción/emoji/usable/mensaje\n"
                "`!delitem <nombre>` — Elimina un item de la tienda"
            ), inline=False)
            embed.add_field(name="⚙️ Configuración Staff 🔒", value=(
                "`!config` — Ve la configuración actual\n"
                "`!setconfig <clave> <valor>` — Cambia un valor\n"
                "`!setcooldown <comando> <tiempo>` — Cambia un cooldown (ej: `1h30m`)\n"
                "`!setmoneda <emoji>` — Cambia el emoji de la moneda del servidor\n"
                "`!resetconfig` — Restablece los valores por defecto"
            ), inline=False)

        embed.set_footer(text="Teto Bot • Economía")
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        return await ctx.send(embed=discord.Embed(description="❌ Ese comando es solo para Staff we", color=0xE74C3C))
    if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
        return await ctx.send(embed=discord.Embed(description="❌ Argumentos inválidos we, revisa el uso del comando con `!help`", color=0xE74C3C))
    log.exception("Error no manejado", exc_info=error)
    await ctx.send(embed=discord.Embed(description="❌ Ocurrió un error inesperado we", color=0xE74C3C))

@bot.event
async def on_ready():
    await bot.add_cog(EconomiaCog(bot))
    await bot.add_cog(CasinoCog(bot))
    await bot.add_cog(HelpCog(bot))
    await bot.tree.sync()
    log.info(f"Online: {bot.user} | {len(bot.tree.get_commands())} slash commands cargados")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="la economía de LatamOS"))

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    await bot.process_commands(message)

bot.run(TOKEN)
