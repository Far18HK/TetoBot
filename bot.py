import discord
import os
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
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, nombre TEXT, precio INTEGER, descripcion TEXT
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS inventario (
    guild_id INTEGER, user_id INTEGER, item TEXT, cantidad INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, item)
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
    robar_fail_max INTEGER DEFAULT 150
)""")
db.commit()

# ─────────────────────────────────────────
# CONFIGURACIÓN POR SERVIDOR
# ─────────────────────────────────────────
# nombre_campo: (tipo, descripción)
CONFIG_FIELDS = {
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

DEFAULT_CONFIG = {
    "cooldown_trabajo": 3600, "cooldown_crime": 7200, "cooldown_robar": 10800,
    "trabajo_min": 60, "trabajo_max": 200,
    "crime_chance": 0.55, "crime_win_min": 150, "crime_win_max": 500,
    "crime_loss_min": 50, "crime_loss_max": 200,
    "robar_chance": 0.4, "robar_min_pct": 0.1, "robar_max_pct": 0.3,
    "robar_max_cap": 5000, "robar_min_balance": 100,
    "robar_fail_min": 50, "robar_fail_max": 150,
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
    cursor.execute("SELECT id, nombre, precio, descripcion FROM tienda WHERE guild_id=? ORDER BY precio ASC", (guild_id,))
    return cursor.fetchall()

def get_item_tienda(guild_id: int, nombre: str):
    cursor.execute("SELECT id, nombre, precio, descripcion FROM tienda WHERE guild_id=? AND LOWER(nombre)=LOWER(?)", (guild_id, nombre))
    return cursor.fetchone()

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
        embed = discord.Embed(title="💰 Balance", description=f"{user.mention} tiene **${saldo:,}**", color=0x2ECC71)
        embed.set_thumbnail(url=user.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(name="trabajo", aliases=["work"])
    async def trabajo(self, ctx: commands.Context):
        cfg = get_config(ctx.guild.id)
        restante = tiempo_restante(get_cooldown(ctx.guild.id, ctx.author.id, "last_trabajo"), cfg["cooldown_trabajo"])
        if restante:
            return await ctx.send(f"⏳ Ya trabajaste we, vuelve en **{formatear_tiempo(restante)}**")
        frase = random.choice(TRABAJOS)
        ganancia = random.randint(cfg["trabajo_min"], cfg["trabajo_max"])
        modificar_balance(ctx.guild.id, ctx.author.id, ganancia)
        set_cooldown(ctx.guild.id, ctx.author.id, "last_trabajo")
        await ctx.send(f"💼 {ctx.author.mention} {frase} y ganaste **${ganancia:,}**")

    @commands.command(name="crime")
    async def crime(self, ctx: commands.Context):
        cfg = get_config(ctx.guild.id)
        restante = tiempo_restante(get_cooldown(ctx.guild.id, ctx.author.id, "last_crime"), cfg["cooldown_crime"])
        if restante:
            return await ctx.send(f"⏳ Todavía te andan buscando we, espera **{formatear_tiempo(restante)}**")
        set_cooldown(ctx.guild.id, ctx.author.id, "last_crime")
        frase = random.choice(CRIMENES)
        if random.random() < cfg["crime_chance"]:
            ganancia = random.randint(cfg["crime_win_min"], cfg["crime_win_max"])
            modificar_balance(ctx.guild.id, ctx.author.id, ganancia)
            await ctx.send(f"🕶️ {ctx.author.mention} {frase} y te saliste con la tuya. Ganaste **${ganancia:,}**")
        else:
            perdida = random.randint(cfg["crime_loss_min"], cfg["crime_loss_max"])
            modificar_balance(ctx.guild.id, ctx.author.id, -perdida)
            await ctx.send(f"🚓 {ctx.author.mention} {frase} pero te pillaron. Perdiste **${perdida:,}**")

    @commands.command(name="robar", aliases=["rob"])
    async def robar(self, ctx: commands.Context, victima: discord.Member = None):
        if not victima:
            return await ctx.send("Dime a quién robar we. Uso: `!robar @user`")
        if victima.id == ctx.author.id:
            return await ctx.send("No te puedes robar a ti mismo we")
        if victima.bot:
            return await ctx.send("A los bots no se les roba we")

        cfg = get_config(ctx.guild.id)
        restante = tiempo_restante(get_cooldown(ctx.guild.id, ctx.author.id, "last_robar"), cfg["cooldown_robar"])
        if restante:
            return await ctx.send(f"⏳ Ya intentaste robar, espera **{formatear_tiempo(restante)}**")

        saldo_victima = get_balance(ctx.guild.id, victima.id)
        if saldo_victima < cfg["robar_min_balance"]:
            return await ctx.send(f"{victima.mention} anda más pelado que tú, no vale la pena robarle we")

        set_cooldown(ctx.guild.id, ctx.author.id, "last_robar")

        if random.random() < cfg["robar_chance"]:
            porcentaje = random.uniform(cfg["robar_min_pct"], cfg["robar_max_pct"])
            robado = min(int(saldo_victima * porcentaje), cfg["robar_max_cap"])
            modificar_balance(ctx.guild.id, victima.id, -robado)
            modificar_balance(ctx.guild.id, ctx.author.id, robado)
            await ctx.send(f"🥷 {ctx.author.mention} le robó **${robado:,}** a {victima.mention}")
        else:
            multa = random.randint(cfg["robar_fail_min"], cfg["robar_fail_max"])
            modificar_balance(ctx.guild.id, ctx.author.id, -multa)
            modificar_balance(ctx.guild.id, victima.id, multa)
            await ctx.send(f"🚨 {ctx.author.mention} intentó robarle a {victima.mention} pero lo pillaron y pagó una multa de **${multa:,}**")

    @commands.command(name="dar", aliases=["pay", "give"])
    async def dar(self, ctx: commands.Context, user: discord.Member = None, cantidad: int = None):
        if not user or cantidad is None:
            return await ctx.send("Uso: `!dar @user <cantidad>`")
        if user.id == ctx.author.id:
            return await ctx.send("No te puedes dar plata a ti mismo we")
        if cantidad <= 0:
            return await ctx.send("La cantidad tiene que ser mayor a 0 we")
        saldo = get_balance(ctx.guild.id, ctx.author.id)
        if saldo < cantidad:
            return await ctx.send(f"No tienes esa plata we, tu balance es **${saldo:,}**")
        modificar_balance(ctx.guild.id, ctx.author.id, -cantidad)
        modificar_balance(ctx.guild.id, user.id, cantidad)
        await ctx.send(f"🤝 {ctx.author.mention} le dio **${cantidad:,}** a {user.mention}")

    @commands.command(name="add$", aliases=["addmoney"])
    @is_staff_ctx()
    async def add_money(self, ctx: commands.Context, user: discord.Member = None, cantidad: int = None):
        if not user or cantidad is None:
            return await ctx.send("Uso: `!add$ @user <cantidad>`")
        if cantidad <= 0:
            return await ctx.send("La cantidad tiene que ser mayor a 0 we")
        modificar_balance(ctx.guild.id, user.id, cantidad)
        nuevo = get_balance(ctx.guild.id, user.id)
        await ctx.send(f"✅ Se le agregaron **${cantidad:,}** a {user.mention}. Nuevo balance: **${nuevo:,}**")

    @commands.command(name="remove$", aliases=["removemoney"])
    @is_staff_ctx()
    async def remove_money(self, ctx: commands.Context, user: discord.Member = None, cantidad: int = None):
        if not user or cantidad is None:
            return await ctx.send("Uso: `!remove$ @user <cantidad>`")
        if cantidad <= 0:
            return await ctx.send("La cantidad tiene que ser mayor a 0 we")
        modificar_balance(ctx.guild.id, user.id, -cantidad)
        nuevo = get_balance(ctx.guild.id, user.id)
        await ctx.send(f"✅ Se le quitaron **${cantidad:,}** a {user.mention}. Nuevo balance: **${nuevo:,}**")

    # ── LEADERBOARD ──
    @commands.command(name="leaderboard", aliases=["top", "ranking"])
    async def leaderboard(self, ctx: commands.Context):
        cursor.execute("SELECT user_id, balance FROM economia WHERE guild_id=? ORDER BY balance DESC LIMIT 10", (ctx.guild.id,))
        rows = cursor.fetchall()
        if not rows:
            return await ctx.send("Todavía no hay nadie con plata we")
        medallas = ["🥇", "🥈", "🥉"]
        embed = discord.Embed(title="🏆 Leaderboard — Los más ricos", color=0xF1C40F)
        lineas = []
        for i, (uid, bal) in enumerate(rows):
            member = ctx.guild.get_member(uid)
            nombre = member.display_name if member else f"ID:{uid}"
            prefijo = medallas[i] if i < 3 else f"**{i+1}.**"
            lineas.append(f"{prefijo} {nombre} — ${bal:,}")
        embed.description = "\n".join(lineas)
        embed.set_footer(text=f"Servidor: {ctx.guild.name}")
        await ctx.send(embed=embed)

    # ── TIENDA ──
    @commands.command(name="tienda", aliases=["shop"])
    async def tienda_cmd(self, ctx: commands.Context):
        items = get_tienda(ctx.guild.id)
        if not items:
            return await ctx.send("La tienda está vacía we, un staff puede agregar items con `!additem`")
        embed = discord.Embed(title="🛒 Tienda", color=0x3498DB)
        for _id, nombre, precio, descripcion in items:
            embed.add_field(name=f"{nombre} — ${precio:,}", value=descripcion or "\u200b", inline=False)
        embed.set_footer(text="Usa !comprar <nombre> para comprar")
        await ctx.send(embed=embed)

    @commands.command(name="comprar", aliases=["buy"])
    async def comprar(self, ctx: commands.Context, *, nombre: str = None):
        if not nombre:
            return await ctx.send("Uso: `!comprar <nombre del item>`")
        item = get_item_tienda(ctx.guild.id, nombre)
        if not item:
            return await ctx.send(f"No existe el item `{nombre}` en la tienda we")
        _id, nombre_real, precio, descripcion = item
        saldo = get_balance(ctx.guild.id, ctx.author.id)
        if saldo < precio:
            return await ctx.send(f"No tienes suficiente plata we. Necesitas **${precio:,}** y tienes **${saldo:,}**")
        modificar_balance(ctx.guild.id, ctx.author.id, -precio)
        add_item(ctx.guild.id, ctx.author.id, nombre_real, 1)
        await ctx.send(f"✅ {ctx.author.mention} compró **{nombre_real}** por **${precio:,}**")

    @commands.command(name="inventario", aliases=["inv"])
    async def inventario_cmd(self, ctx: commands.Context, user: discord.Member = None):
        user = user or ctx.author
        items = get_inventario(ctx.guild.id, user.id)
        if not items:
            return await ctx.send(f"{user.mention} no tiene items we")
        embed = discord.Embed(title=f"🎒 Inventario de {user.display_name}", color=0x9B59B6)
        embed.description = "\n".join(f"**{item}** x{cantidad}" for item, cantidad in items)
        await ctx.send(embed=embed)

    @commands.command(name="additem")
    @is_staff_ctx()
    async def additem(self, ctx: commands.Context, nombre: str, precio: int, *, descripcion: str = ""):
        if precio <= 0:
            return await ctx.send("El precio tiene que ser mayor a 0 we")
        existente = get_item_tienda(ctx.guild.id, nombre)
        if existente:
            return await ctx.send(f"Ya existe un item llamado `{nombre}` we, usa `!delitem` primero si quieres reemplazarlo")
        cursor.execute("INSERT INTO tienda (guild_id, nombre, precio, descripcion) VALUES (?,?,?,?)", (ctx.guild.id, nombre, precio, descripcion))
        db.commit()
        await ctx.send(f"✅ Agregado **{nombre}** a la tienda por **${precio:,}**")

    @commands.command(name="delitem")
    @is_staff_ctx()
    async def delitem(self, ctx: commands.Context, *, nombre: str = None):
        if not nombre:
            return await ctx.send("Uso: `!delitem <nombre>`")
        item = get_item_tienda(ctx.guild.id, nombre)
        if not item:
            return await ctx.send(f"No existe el item `{nombre}` we")
        cursor.execute("DELETE FROM tienda WHERE id=?", (item[0],))
        db.commit()
        await ctx.send(f"🗑️ Item **{nombre}** eliminado de la tienda")

    # ── CONFIGURACIÓN ──
    @commands.command(name="config")
    @is_staff_ctx()
    async def config_cmd(self, ctx: commands.Context):
        cfg = get_config(ctx.guild.id)
        embed = discord.Embed(title="⚙️ Configuración de Teto", color=0x95A5A6,
                               description="Usa `!setconfig <clave> <valor>` para cambiar un valor.")
        for campo, (tipo, desc) in CONFIG_FIELDS.items():
            embed.add_field(name=campo, value=f"Valor actual: `{cfg[campo]}`\n{desc}", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="setconfig")
    @is_staff_ctx()
    async def setconfig_cmd(self, ctx: commands.Context, campo: str = None, valor: str = None):
        if not campo or valor is None:
            return await ctx.send("Uso: `!setconfig <clave> <valor>`. Usa `!config` para ver las claves disponibles.")
        campo = campo.lower()
        if campo not in CONFIG_FIELDS:
            return await ctx.send(f"❌ No existe la clave `{campo}` we. Usa `!config` para ver las disponibles.")
        tipo, _desc = CONFIG_FIELDS[campo]
        try:
            valor_convertido = int(valor) if tipo == "int" else float(valor)
        except ValueError:
            return await ctx.send(f"❌ `{campo}` espera un valor tipo `{tipo}` we")
        if tipo == "float" and not (0 <= valor_convertido <= 1):
            return await ctx.send("❌ Ese valor tiene que estar entre 0 y 1 we")
        set_config_field(ctx.guild.id, campo, valor_convertido)
        await ctx.send(f"✅ `{campo}` ahora vale `{valor_convertido}`")

    @commands.command(name="resetconfig")
    @is_staff_ctx()
    async def resetconfig_cmd(self, ctx: commands.Context):
        cursor.execute("DELETE FROM config WHERE guild_id=?", (ctx.guild.id,))
        db.commit()
        get_config(ctx.guild.id)
        await ctx.send("✅ Configuración restablecida a los valores por defecto")

    @add_money.error
    @remove_money.error
    @additem.error
    @delitem.error
    @config_cmd.error
    @setconfig_cmd.error
    @resetconfig_cmd.error
    async def staff_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ Ese comando es solo para Staff we")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ Argumentos inválidos we, revisa el uso del comando")

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
            "`!inventario` — Ve tu inventario"
        ), inline=False)

        if staff:
            embed.add_field(name="💰 Economía Staff 🔒", value=(
                "`!add$ @user <cantidad>` — Agrega plata a un usuario\n"
                "`!remove$ @user <cantidad>` — Quita plata a un usuario"
            ), inline=False)
            embed.add_field(name="🛒 Tienda Staff 🔒", value=(
                "`!additem <nombre> <precio> <descripción>` — Agrega un item a la tienda\n"
                "`!delitem <nombre>` — Elimina un item de la tienda"
            ), inline=False)
            embed.add_field(name="⚙️ Configuración Staff 🔒", value=(
                "`!config` — Ve la configuración actual\n"
                "`!setconfig <clave> <valor>` — Cambia un valor\n"
                "`!resetconfig` — Restablece los valores por defecto"
            ), inline=False)

        embed.set_footer(text="Teto Bot • Economía")
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_ready():
    await bot.add_cog(EconomiaCog(bot))
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
