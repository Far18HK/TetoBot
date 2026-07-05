import discord
import os
import re
import random
import sqlite3
import logging
import asyncio
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

def is_staff_app():
    async def predicate(interaction: discord.Interaction) -> bool:
        return isinstance(interaction.user, discord.Member) and es_staff_member(interaction.user)
    return app_commands.check(predicate)

# ─────────────────────────────────────────
# DB
# ─────────────────────────────────────────
db = sqlite3.connect("teto.db", check_same_thread=False)
cursor = db.cursor()
cursor.execute("""CREATE TABLE IF NOT EXISTS economia (
    guild_id INTEGER, user_id INTEGER, balance INTEGER DEFAULT 0, banco INTEGER DEFAULT 0,
    last_trabajo TEXT, last_crime TEXT, last_robar TEXT,
    PRIMARY KEY (guild_id, user_id)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS tienda (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, nombre TEXT, precio INTEGER, descripcion TEXT,
    emoji TEXT DEFAULT '📦', usable INTEGER DEFAULT 0, mensaje_uso TEXT DEFAULT '', imagen TEXT DEFAULT ''
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
    ruleta_multi_verde INTEGER DEFAULT 14,
    cooldown_ruletarusa INTEGER DEFAULT 60
)""")
db.commit()

# Migraciones para bases de datos ya existentes (por si les faltan columnas nuevas)
_MIGRACIONES = [
    ("economia", "banco", "INTEGER DEFAULT 0"),
    ("tienda", "emoji", "TEXT DEFAULT '📦'"),
    ("tienda", "usable", "INTEGER DEFAULT 0"),
    ("tienda", "mensaje_uso", "TEXT DEFAULT ''"),
    ("tienda", "imagen", "TEXT DEFAULT ''"),
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
    ("config", "cooldown_ruletarusa", "INTEGER DEFAULT 60"),
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
    "cooldown_slots": ("int", "Cooldown de /slots en segundos"),
    "cooldown_ruleta": ("int", "Cooldown de /ruleta en segundos"),
    "cooldown_coinflip": ("int", "Cooldown de /coinflip en segundos"),
    "cooldown_blackjack": ("int", "Cooldown de /blackjack en segundos"),
    "apuesta_min": ("int", "Apuesta mínima permitida en los juegos de casino"),
    "apuesta_max": ("int", "Apuesta máxima permitida en los juegos de casino"),
    "slots_multi_x2": ("int", "Multiplicador de /slots si salen 2 iguales"),
    "slots_multi_x3": ("int", "Multiplicador de /slots si salen 3 iguales"),
    "ruleta_multi_color": ("int", "Multiplicador de /ruleta si aciertas rojo/negro"),
    "ruleta_multi_verde": ("int", "Multiplicador de /ruleta si aciertas verde (el 0)"),
    "cooldown_ruletarusa": ("int", "Cooldown de /ruletarusa en segundos"),
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
    "ruletarusa": "cooldown_ruletarusa", "rusa": "cooldown_ruletarusa",
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

def get_banco(guild_id: int, user_id: int) -> int:
    _ensure_user(guild_id, user_id)
    cursor.execute("SELECT banco FROM economia WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    row = cursor.fetchone()
    return row[0] if row else 0

def modificar_banco(guild_id: int, user_id: int, cantidad: int):
    _ensure_user(guild_id, user_id)
    cursor.execute("UPDATE economia SET banco = MAX(banco + ?, 0) WHERE guild_id=? AND user_id=?", (cantidad, guild_id, user_id))
    db.commit()

# palabras que el usuario puede escribir para referirse a "todo lo que tengo"
PALABRAS_TODO = {"all", "todo", "all-in", "allin", "everything"}

def parse_cantidad(texto: str, disponible: int):
    """Convierte un texto tipo '500' o 'all' en un número, usando `disponible` como referencia para 'all'.
    Devuelve None si el texto no es válido."""
    if texto is None:
        return None
    texto = texto.strip().lower()
    if texto in PALABRAS_TODO:
        return disponible
    try:
        return int(texto)
    except ValueError:
        return None

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
    cursor.execute("SELECT id, nombre, precio, descripcion, emoji, usable, imagen FROM tienda WHERE guild_id=? ORDER BY precio ASC", (guild_id,))
    return cursor.fetchall()

def get_item_tienda(guild_id: int, nombre: str):
    cursor.execute("SELECT id, nombre, precio, descripcion, emoji, usable, mensaje_uso, imagen FROM tienda WHERE guild_id=? AND LOWER(nombre)=LOWER(?)", (guild_id, nombre))
    return cursor.fetchone()

# ─────────────────────────────────────────
# TIENDA CON BOTONES
# ─────────────────────────────────────────
def parse_emoji_boton(emoji_str: str):
    """Intenta convertir el texto guardado en la BD en un emoji válido para un botón."""
    if not emoji_str:
        return None
    try:
        return discord.PartialEmoji.from_str(emoji_str)
    except Exception:
        return None

MAX_ITEMS_TIENDA = 10  # Discord permite máximo 10 embeds por mensaje

class ComprarButton(discord.ui.Button):
    def __init__(self, nombre: str, precio: int, guild_id: int):
        moneda = get_moneda(guild_id)
        super().__init__(label=f"{precio:,}"[:80], style=discord.ButtonStyle.success,
                          emoji=parse_emoji_boton(moneda) or "💰")
        self.nombre_item = nombre
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        item = get_item_tienda(guild_id, self.nombre_item)
        if not item:
            return await interaction.response.send_message("❌ Ese item ya no existe en la tienda we", ephemeral=True)
        _id, nombre_real, precio, descripcion, emoji, usable, mensaje_uso, imagen = item
        saldo = get_balance(guild_id, interaction.user.id)
        if saldo < precio:
            return await interaction.response.send_message(
                f"❌ No tienes suficiente plata we. Necesitas **{format_dinero(guild_id, precio)}** y tienes **{format_dinero(guild_id, saldo)}**",
                ephemeral=True)
        modificar_balance(guild_id, interaction.user.id, -precio)
        add_item(guild_id, interaction.user.id, nombre_real, 1)
        await interaction.response.send_message(
            f"✅ Compraste **{emoji} {nombre_real}** por **{format_dinero(guild_id, precio)}**", ephemeral=True)

class TiendaLayoutView(discord.ui.LayoutView):
    """Tienda armada con Components V2: cada item es una fila con miniatura + texto,
    y su botón de compra debajo, todo dentro de un único mensaje (sin embeds)."""
    def __init__(self, guild_id: int, items):
        super().__init__(timeout=180)
        total = len(items)
        items = items[:MAX_ITEMS_TIENDA]
        container = discord.ui.Container(accent_color=0x2ECC71)
        container.add_item(discord.ui.TextDisplay(
            "**🛒 Tienda**\nPulsa un botón para comprar el item al instante, o usa el comando `!comprar <item>`.\n"
            "Usa `!inventario` para ver lo que ya compraste."
        ))
        container.add_item(discord.ui.Separator())
        for idx, (_id, nombre, precio, descripcion, emoji, usable, imagen) in enumerate(items):
            etiqueta = "\n*Usable con `!useitem`*" if usable else ""
            texto = f"**{emoji} {nombre}**\n{descripcion or chr(0x200b)}{etiqueta}"
            if imagen and imagen.strip().lower().startswith(("http://", "https://")):
                section = discord.ui.Section(
                    discord.ui.TextDisplay(texto),
                    accessory=discord.ui.Thumbnail(media=imagen.strip()))
            else:
                section = discord.ui.Section(discord.ui.TextDisplay(texto))
            container.add_item(section)
            fila = discord.ui.ActionRow()
            fila.add_item(ComprarButton(nombre, precio, guild_id))
            container.add_item(fila)
            if idx < len(items) - 1:
                container.add_item(discord.ui.Separator())
        if total > len(items):
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(f"⚠️ Mostrando {len(items)} de {total} items we"))
        self.add_item(container)

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

# ─────────────────────────────────────────
# SISTEMA DE "UNIRSE" MULTIJUGADOR PARA CASINO
# ─────────────────────────────────────────
JOIN_SECONDS = 12  # ventana para que otros se unan a la ronda

def duracion_juego(num_participantes: int) -> float:
    """La ronda dura entre 20 y 30s dependiendo de cuánta gente participe."""
    return min(30, 20 + max(0, num_participantes - 1) * 2)

def validar_apuesta_valores(cfg: dict, apuesta):
    if apuesta is None or apuesta <= 0:
        return False, "Tienes que apostar una cantidad válida we"
    if apuesta < cfg["apuesta_min"] or apuesta > cfg["apuesta_max"]:
        return False, f"La apuesta debe estar entre **{cfg['apuesta_min']:,}** y **{cfg['apuesta_max']:,}**"
    return True, ""

def mapa_eleccion(juego_tipo: str) -> dict:
    if juego_tipo == "ruleta":
        return {"rojo": "rojo", "red": "rojo", "negro": "negro", "black": "negro", "verde": "verde", "green": "verde"}
    if juego_tipo == "coinflip":
        return {"cara": "cara", "heads": "cara", "cruz": "cruz", "cola": "cruz", "tails": "cruz"}
    return {}

class ApuestaModal(discord.ui.Modal):
    def __init__(self, view: "JoinView"):
        super().__init__(title=f"Unirse a {view.juego_titulo}"[:45])
        self.view_ref = view
        self.apuesta_input = discord.ui.TextInput(label="¿Cuánto quieres apostar?", placeholder="Ej: 100, o 'all' para apostar todo", max_length=10)
        self.add_item(self.apuesta_input)
        self.eleccion_input = None
        if view.necesita_eleccion:
            placeholder = "rojo, negro o verde" if view.juego_tipo == "ruleta" else "cara o cruz"
            self.eleccion_input = discord.ui.TextInput(label="Tu elección", placeholder=placeholder, max_length=10)
            self.add_item(self.eleccion_input)

    async def on_submit(self, interaction: discord.Interaction):
        view = self.view_ref
        guild_id = view.guild_id
        cfg = get_config(guild_id)
        saldo = get_balance(guild_id, interaction.user.id)
        apuesta = parse_cantidad(self.apuesta_input.value.strip(), saldo)
        if apuesta is None:
            return await interaction.response.send_message("❌ La apuesta tiene que ser un número o `all` we", ephemeral=True)
        ok, error = validar_apuesta_valores(cfg, apuesta)
        if not ok:
            return await interaction.response.send_message(f"❌ {error}", ephemeral=True)
        if saldo < apuesta:
            return await interaction.response.send_message(f"❌ No tienes esa plata we, tu balance es **{format_dinero(guild_id, saldo)}**", ephemeral=True)
        eleccion = None
        if self.eleccion_input is not None:
            texto = self.eleccion_input.value.strip().lower()
            mapa = mapa_eleccion(view.juego_tipo)
            if texto not in mapa:
                return await interaction.response.send_message("❌ Elección inválida we", ephemeral=True)
            eleccion = mapa[texto]
        view.participantes[interaction.user.id] = {"member": interaction.user, "apuesta": apuesta, "eleccion": eleccion}
        await interaction.response.send_message("✅ Te uniste a la ronda, espera a que empiece", ephemeral=True)

class JoinView(discord.ui.View):
    def __init__(self, guild_id: int, juego_tipo: str, juego_titulo: str, necesita_eleccion: bool = False):
        super().__init__(timeout=JOIN_SECONDS + 10)
        self.guild_id = guild_id
        self.juego_tipo = juego_tipo  # usado para cooldowns: slots/ruleta/coinflip/blackjack
        self.juego_titulo = juego_titulo
        self.necesita_eleccion = necesita_eleccion
        self.participantes = {}  # user_id -> {"member", "apuesta", "eleccion"}
        self.cerrado = False

    @discord.ui.button(label="Unirse", emoji="🎟️", style=discord.ButtonStyle.success)
    async def unirse_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.cerrado:
            return await interaction.response.send_message("Ya cerraron las apuestas de esta ronda we", ephemeral=True)
        if interaction.user.id in self.participantes:
            return await interaction.response.send_message("Ya estás dentro de esta ronda we", ephemeral=True)
        cfg = get_config(self.guild_id)
        restante = tiempo_restante(get_cooldown_generic(self.guild_id, interaction.user.id, self.juego_tipo), cfg[f"cooldown_{self.juego_tipo}"])
        if restante:
            return await interaction.response.send_message(f"⏳ Espera **{formatear_tiempo(restante)}** para volver a jugar", ephemeral=True)
        await interaction.response.send_modal(ApuestaModal(self))

def construir_embed_join(view: JoinView, restante: int) -> discord.Embed:
    if view.participantes:
        lineas = []
        for data in view.participantes.values():
            extra = f" a **{data['eleccion']}**" if data.get("eleccion") else ""
            lineas.append(f"• {data['member'].mention} — apuesta **{data['apuesta']:,}**{extra}")
        desc = "\n".join(lineas)
    else:
        desc = "Nadie se ha unido todavía"
    emb = discord.Embed(title=f"{view.juego_titulo} — ¡Únete!", description=desc, color=0xF1C40F)
    emb.set_footer(text=f"⏳ Se cierran las apuestas en {restante}s — pulsa Unirse para participar")
    return emb

async def ejecutar_join_window(view: JoinView, msg: discord.Message):
    restante = JOIN_SECONDS
    while restante > 0:
        espera = min(3, restante)
        await asyncio.sleep(espera)
        restante -= espera
        try:
            await msg.edit(embed=construir_embed_join(view, restante))
        except discord.HTTPException:
            pass
    view.cerrado = True
    for item in view.children:
        item.disabled = True
    try:
        await msg.edit(view=view)
    except discord.HTTPException:
        pass

# ─────────────────────────────────────────
# RULETA RUSA (juego de eliminación, sin apuesta)
# ─────────────────────────────────────────
RUSA_MIN_JUGADORES = 3
RUSA_JOIN_SECONDS = 20
RUSA_INTERVALO = 6  # segundos entre cada eliminación
RUSA_PORCENTAJE = 0.20  # % del balance que pierde el eliminado

class RusaJoinView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=RUSA_JOIN_SECONDS + 10)
        self.guild_id = guild_id
        self.participantes = {}  # user_id -> discord.Member
        self.cerrado = False

    @discord.ui.button(label="Unirse", emoji="🔫", style=discord.ButtonStyle.danger)
    async def unirse_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.cerrado:
            return await interaction.response.send_message("Ya cerraron las inscripciones we", ephemeral=True)
        if interaction.user.id in self.participantes:
            return await interaction.response.send_message("Ya estás en la ronda we", ephemeral=True)
        self.participantes[interaction.user.id] = interaction.user
        await interaction.response.send_message("✅ Te uniste a la Ruleta Rusa, espera a que empiece", ephemeral=True)

def construir_embed_rusa_join(view: RusaJoinView, restante: int) -> discord.Embed:
    if view.participantes:
        desc = "\n".join(f"• {m.mention}" for m in view.participantes.values())
    else:
        desc = "Nadie se ha unido todavía"
    emb = discord.Embed(
        title="🔫 Ruleta Rusa — ¡Únete!",
        description=(f"{desc}\n\nSe necesitan mínimo **{RUSA_MIN_JUGADORES}** jugadores.\n"
                     f"Cada {RUSA_INTERVALO}s se elimina a alguien al azar y pierde el **{int(RUSA_PORCENTAJE*100)}%** de su plata.\n"
                     f"El último que quede se gana todo lo que perdieron los demás."),
        color=0xE74C3C)
    emb.set_footer(text=f"⏳ Se cierran las inscripciones en {restante}s — pulsa Unirse para participar")
    return emb

async def ejecutar_rusa_join_window(view: RusaJoinView, msg: discord.Message):
    restante = RUSA_JOIN_SECONDS
    while restante > 0:
        espera = min(3, restante)
        await asyncio.sleep(espera)
        restante -= espera
        try:
            await msg.edit(embed=construir_embed_rusa_join(view, restante))
        except discord.HTTPException:
            pass
    view.cerrado = True
    for item in view.children:
        item.disabled = True
    try:
        await msg.edit(view=view)
    except discord.HTTPException:
        pass

def revalidar_participantes(guild_id: int, participantes: dict) -> dict:
    """Vuelve a chequear que cada quien tenga plata suficiente justo antes de resolver."""
    validos = {}
    for uid, data in participantes.items():
        saldo = get_balance(guild_id, uid)
        if saldo >= data["apuesta"]:
            validos[uid] = data
    return validos

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
        cartera = get_balance(ctx.guild.id, user.id)
        banco = get_banco(ctx.guild.id, user.id)
        embed = discord.Embed(title="💰 Balance", description=f"{user.mention}", color=0x2ECC71)
        embed.add_field(name="👛 Cartera", value=format_dinero(ctx.guild.id, cartera), inline=True)
        embed.add_field(name="🏦 Banco", value=format_dinero(ctx.guild.id, banco), inline=True)
        embed.add_field(name="💎 Total", value=format_dinero(ctx.guild.id, cartera + banco), inline=True)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text="Lo que está en el banco no te lo pueden robar")
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
    async def dar(self, ctx: commands.Context, user: discord.Member = None, *, cantidad: str = None):
        if not user or cantidad is None:
            return await send_msg(ctx, "Uso: `!dar @user <cantidad>` (también puedes usar `all` para dar toda tu plata)", title="🤝 Dar")
        if user.id == ctx.author.id:
            return await send_msg(ctx, "No te puedes dar plata a ti mismo we", title="🤝 Dar", color=0xE74C3C)
        if user.bot:
            return await send_msg(ctx, "A los bots no se les puede dar plata we", title="🤝 Dar", color=0xE74C3C)
        saldo = get_balance(ctx.guild.id, ctx.author.id)
        monto = parse_cantidad(cantidad, saldo)
        if monto is None:
            return await send_msg(ctx, "Cantidad inválida we. Usa un número o `all`", title="🤝 Dar", color=0xE74C3C)
        if monto <= 0:
            return await send_msg(ctx, "La cantidad tiene que ser mayor a 0 we", title="🤝 Dar", color=0xE74C3C)
        if saldo < monto:
            return await send_msg(ctx, f"No tienes esa plata we, tu balance es **{format_dinero(ctx.guild.id, saldo)}**", title="🤝 Dar", color=0xE74C3C)
        modificar_balance(ctx.guild.id, ctx.author.id, -monto)
        modificar_balance(ctx.guild.id, user.id, monto)
        await send_msg(ctx, f"{ctx.author.mention} le dio **{format_dinero(ctx.guild.id, monto)}** a {user.mention}", title="🤝 Dar")

    # ── BANCO ──
    @commands.command(name="deposit", aliases=["depositar", "dep"])
    async def deposit(self, ctx: commands.Context, *, cantidad: str = None):
        if cantidad is None:
            return await send_msg(ctx, "Uso: `!deposit <cantidad>` (también puedes usar `all` para depositar todo)", title="🏦 Depósito")
        cartera = get_balance(ctx.guild.id, ctx.author.id)
        monto = parse_cantidad(cantidad, cartera)
        if monto is None:
            return await send_msg(ctx, "Cantidad inválida we. Usa un número o `all`", title="🏦 Depósito", color=0xE74C3C)
        if monto <= 0:
            return await send_msg(ctx, "La cantidad tiene que ser mayor a 0 we", title="🏦 Depósito", color=0xE74C3C)
        if cartera < monto:
            return await send_msg(ctx, f"No tienes esa plata en la cartera we, tienes **{format_dinero(ctx.guild.id, cartera)}**", title="🏦 Depósito", color=0xE74C3C)
        modificar_balance(ctx.guild.id, ctx.author.id, -monto)
        modificar_banco(ctx.guild.id, ctx.author.id, monto)
        nuevo_banco = get_banco(ctx.guild.id, ctx.author.id)
        await send_msg(ctx, f"{ctx.author.mention} depositó **{format_dinero(ctx.guild.id, monto)}** en el banco. Ahora tiene **{format_dinero(ctx.guild.id, nuevo_banco)}** guardados", title="🏦 Depósito")

    @commands.command(name="retirar", aliases=["withdraw", "retire"])
    async def retirar(self, ctx: commands.Context, *, cantidad: str = None):
        if cantidad is None:
            return await send_msg(ctx, "Uso: `!retirar <cantidad>` (también puedes usar `all` para retirar todo)", title="🏦 Retiro")
        banco = get_banco(ctx.guild.id, ctx.author.id)
        monto = parse_cantidad(cantidad, banco)
        if monto is None:
            return await send_msg(ctx, "Cantidad inválida we. Usa un número o `all`", title="🏦 Retiro", color=0xE74C3C)
        if monto <= 0:
            return await send_msg(ctx, "La cantidad tiene que ser mayor a 0 we", title="🏦 Retiro", color=0xE74C3C)
        if banco < monto:
            return await send_msg(ctx, f"No tienes esa plata en el banco we, tienes **{format_dinero(ctx.guild.id, banco)}** guardados", title="🏦 Retiro", color=0xE74C3C)
        modificar_banco(ctx.guild.id, ctx.author.id, -monto)
        modificar_balance(ctx.guild.id, ctx.author.id, monto)
        nueva_cartera = get_balance(ctx.guild.id, ctx.author.id)
        await send_msg(ctx, f"{ctx.author.mention} retiró **{format_dinero(ctx.guild.id, monto)}** del banco. Ahora tiene **{format_dinero(ctx.guild.id, nueva_cartera)}** en la cartera", title="🏦 Retiro")

    # ── LEADERBOARD ──
    @commands.command(name="leaderboard", aliases=["top", "ranking"])
    async def leaderboard(self, ctx: commands.Context):
        cursor.execute("SELECT user_id, balance + banco AS total FROM economia WHERE guild_id=? ORDER BY total DESC LIMIT 10", (ctx.guild.id,))
        rows = cursor.fetchall()
        if not rows:
            return await send_msg(ctx, "Todavía no hay nadie con plata we", title="🏆 Leaderboard")
        medallas = ["🥇", "🥈", "🥉"]
        embed = discord.Embed(title="🏆 Leaderboard — Los más ricos", color=0xF1C40F)
        lineas = []
        for i, (uid, total) in enumerate(rows):
            member = ctx.guild.get_member(uid)
            nombre = member.display_name if member else f"ID:{uid}"
            prefijo = medallas[i] if i < 3 else f"**{i+1}.**"
            lineas.append(f"{prefijo} {nombre} — {format_dinero(ctx.guild.id, total)}")
        embed.description = "\n".join(lineas)
        embed.set_footer(text=f"Servidor: {ctx.guild.name} • Incluye cartera + banco")
        await ctx.send(embed=embed)

    # ── TIENDA ──
    @commands.command(name="tienda", aliases=["shop"])
    async def tienda_cmd(self, ctx: commands.Context):
        items = get_tienda(ctx.guild.id)
        if not items:
            return await send_msg(ctx, "La tienda está vacía we, un staff puede agregar items con `/additem`", title="🛒 Tienda")
        view = TiendaLayoutView(ctx.guild.id, items)
        await ctx.send(view=view)

    @commands.command(name="comprar", aliases=["buy"])
    async def comprar(self, ctx: commands.Context, *, nombre: str = None):
        if not nombre:
            return await send_msg(ctx, "Uso: `!comprar <nombre del item>` (o usa los botones de `!tienda`)", title="🛒 Comprar")
        item = get_item_tienda(ctx.guild.id, nombre)
        if not item:
            return await send_msg(ctx, f"No existe el item `{nombre}` en la tienda we", title="🛒 Comprar", color=0xE74C3C)
        _id, nombre_real, precio, descripcion, emoji, usable, mensaje_uso, imagen = item
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

async def autocomplete_items_tienda(interaction: discord.Interaction, current: str):
    if not interaction.guild:
        return []
    items = get_tienda(interaction.guild.id)
    current = current.lower()
    return [app_commands.Choice(name=nombre, value=nombre) for _id, nombre, precio, descripcion, emoji, usable, imagen in items if current in nombre.lower()][:25]

# ─────────────────────────────────────────
# COG CONFIGURACIÓN (STAFF) — AHORA COMO SLASH "/"
# ─────────────────────────────────────────
class ConfigCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="config", description="Ve la configuración actual del servidor")
    @is_staff_app()
    async def config_slash(self, interaction: discord.Interaction):
        cfg = get_config(interaction.guild.id)
        emb1 = discord.Embed(title="⚙️ Configuración — Economía", color=0x95A5A6,
                              description="Usa `/setconfig` para cambiar un valor.")
        for campo, (tipo, desc) in CONFIG_FIELDS_ECONOMIA.items():
            emb1.add_field(name=campo, value=f"Valor actual: `{cfg[campo]}`\n{desc}", inline=False)
        emb2 = discord.Embed(title="🎰 Configuración — Casino", color=0x95A5A6,
                              description=f"Moneda del servidor: {get_moneda(interaction.guild.id)} (cámbiala con `/setmoneda`)")
        for campo, (tipo, desc) in CONFIG_FIELDS_CASINO.items():
            emb2.add_field(name=campo, value=f"Valor actual: `{cfg[campo]}`\n{desc}", inline=False)
        await interaction.response.send_message(embeds=[emb1, emb2], ephemeral=True)

    @app_commands.command(name="setconfig", description="Cambia un valor de configuración")
    @app_commands.describe(clave="Parámetro a cambiar", valor="Nuevo valor")
    @is_staff_app()
    async def setconfig_slash(self, interaction: discord.Interaction, clave: str, valor: str):
        clave = clave.lower()
        if clave not in CONFIG_FIELDS:
            return await interaction.response.send_message(f"❌ No existe la clave `{clave}` we. Usa `/config` para ver las disponibles.", ephemeral=True)
        tipo, _desc = CONFIG_FIELDS[clave]
        try:
            valor_convertido = int(valor) if tipo == "int" else float(valor)
        except ValueError:
            return await interaction.response.send_message(f"❌ `{clave}` espera un valor tipo `{tipo}` we", ephemeral=True)
        if tipo == "float" and not (0 <= valor_convertido <= 1):
            return await interaction.response.send_message("❌ Ese valor tiene que estar entre 0 y 1 we", ephemeral=True)
        set_config_field(interaction.guild.id, clave, valor_convertido)
        await interaction.response.send_message(f"✅ `{clave}` ahora vale `{valor_convertido}`", ephemeral=True)

    @setconfig_slash.autocomplete("clave")
    async def setconfig_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.lower()
        return [app_commands.Choice(name=c, value=c) for c in CONFIG_FIELDS if current in c.lower()][:25]

    @app_commands.command(name="setcooldown", description="Cambia el cooldown de un comando")
    @app_commands.describe(comando="Comando a modificar", tiempo="Ej: 1h30m, 45s, 10m")
    @is_staff_app()
    async def setcooldown_slash(self, interaction: discord.Interaction, comando: str, tiempo: str):
        comando = comando.lower()
        if comando not in MAPA_COOLDOWNS:
            return await interaction.response.send_message(f"❌ No reconozco el comando `{comando}` we", ephemeral=True)
        segundos = parse_tiempo(tiempo)
        if segundos is None or segundos <= 0:
            return await interaction.response.send_message("❌ Formato de tiempo inválido. Usa algo como `30s`, `10m`, `2h` o `1h30m`", ephemeral=True)
        campo = MAPA_COOLDOWNS[comando]
        set_config_field(interaction.guild.id, campo, segundos)
        await interaction.response.send_message(
            f"✅ El cooldown de **{comando}** ahora es de **{formatear_tiempo(timedelta(seconds=segundos))}**", ephemeral=True)

    @setcooldown_slash.autocomplete("comando")
    async def setcooldown_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.lower()
        opciones = sorted(set(MAPA_COOLDOWNS.keys()))
        return [app_commands.Choice(name=o, value=o) for o in opciones if current in o][:25]

    @app_commands.command(name="setmoneda", description="Cambia el emoji de la moneda del servidor")
    @app_commands.describe(emoji="Emoji normal 😀 o personalizado del server (ej: <:nombre:1234567890>)")
    @is_staff_app()
    async def setmoneda_slash(self, interaction: discord.Interaction, emoji: str):
        set_config_field(interaction.guild.id, "moneda_emoji", emoji)
        await interaction.response.send_message(f"✅ La moneda del servidor ahora es: {emoji}", ephemeral=True)

    @app_commands.command(name="resetconfig", description="Restablece la configuración a los valores por defecto")
    @is_staff_app()
    async def resetconfig_slash(self, interaction: discord.Interaction):
        cursor.execute("DELETE FROM config WHERE guild_id=?", (interaction.guild.id,))
        db.commit()
        get_config(interaction.guild.id)
        await interaction.response.send_message("✅ Configuración restablecida a los valores por defecto", ephemeral=True)

    # ── PLATA (STAFF) ──
    @app_commands.command(name="add-money", description="Agrega plata a un usuario")
    @app_commands.describe(user="Usuario a modificar", cantidad="Cuánto agregar")
    @is_staff_app()
    async def add_money_slash(self, interaction: discord.Interaction, user: discord.Member, cantidad: int):
        if cantidad <= 0:
            return await interaction.response.send_message("❌ La cantidad tiene que ser mayor a 0 we", ephemeral=True)
        modificar_balance(interaction.guild.id, user.id, cantidad)
        nuevo = get_balance(interaction.guild.id, user.id)
        await interaction.response.send_message(
            f"✅ Se le agregaron **{format_dinero(interaction.guild.id, cantidad)}** a {user.mention}. Nuevo balance: **{format_dinero(interaction.guild.id, nuevo)}**")

    @app_commands.command(name="remove-money", description="Quita plata a un usuario")
    @app_commands.describe(user="Usuario a modificar", cantidad="Cuánto quitar")
    @is_staff_app()
    async def remove_money_slash(self, interaction: discord.Interaction, user: discord.Member, cantidad: int):
        if cantidad <= 0:
            return await interaction.response.send_message("❌ La cantidad tiene que ser mayor a 0 we", ephemeral=True)
        modificar_balance(interaction.guild.id, user.id, -cantidad)
        nuevo = get_balance(interaction.guild.id, user.id)
        await interaction.response.send_message(
            f"✅ Se le quitaron **{format_dinero(interaction.guild.id, cantidad)}** a {user.mention}. Nuevo balance: **{format_dinero(interaction.guild.id, nuevo)}**")

    # ── TIENDA (STAFF) ──
    @app_commands.command(name="additem", description="Agrega un item a la tienda")
    @app_commands.describe(nombre="Nombre del item", precio="Precio del item", descripcion="Descripción (opcional)",
                           imagen="URL de una imagen para la miniatura del item (opcional)")
    @is_staff_app()
    async def additem_slash(self, interaction: discord.Interaction, nombre: str, precio: int, descripcion: str = "", imagen: str = ""):
        if precio <= 0:
            return await interaction.response.send_message("❌ El precio tiene que ser mayor a 0 we", ephemeral=True)
        existente = get_item_tienda(interaction.guild.id, nombre)
        if existente:
            return await interaction.response.send_message(f"❌ Ya existe un item llamado `{nombre}` we, usa `/delitem` primero si quieres reemplazarlo", ephemeral=True)
        cursor.execute("INSERT INTO tienda (guild_id, nombre, precio, descripcion, emoji, usable, mensaje_uso, imagen) VALUES (?,?,?,?,?,?,?,?)",
                       (interaction.guild.id, nombre, precio, descripcion, "📦", 0, "", imagen))
        db.commit()
        await interaction.response.send_message(
            f"✅ Agregado **{nombre}** a la tienda por **{format_dinero(interaction.guild.id, precio)}**\n"
            f"Usa `/edititem` con campo `emoji`, `usable` o `imagen` para personalizarlo.")

    @app_commands.command(name="edititem", description="Edita un item de la tienda")
    @app_commands.describe(nombre="Item a editar", campo="Qué campo cambiar", valor="Nuevo valor")
    @app_commands.choices(campo=[
        app_commands.Choice(name="nombre", value="nombre"),
        app_commands.Choice(name="precio", value="precio"),
        app_commands.Choice(name="descripcion", value="descripcion"),
        app_commands.Choice(name="emoji", value="emoji"),
        app_commands.Choice(name="usable", value="usable"),
        app_commands.Choice(name="mensaje", value="mensaje"),
        app_commands.Choice(name="imagen", value="imagen"),
    ])
    @is_staff_app()
    async def edititem_slash(self, interaction: discord.Interaction, nombre: str, campo: app_commands.Choice[str], valor: str):
        item = get_item_tienda(interaction.guild.id, nombre)
        if not item:
            return await interaction.response.send_message(f"❌ No existe el item `{nombre}` we", ephemeral=True)
        _id = item[0]
        campo_val = campo.value
        if campo_val == "nombre":
            cursor.execute("UPDATE tienda SET nombre=? WHERE id=?", (valor, _id))
        elif campo_val == "precio":
            try:
                precio = int(valor)
            except ValueError:
                return await interaction.response.send_message("❌ El precio tiene que ser un número we", ephemeral=True)
            if precio <= 0:
                return await interaction.response.send_message("❌ El precio tiene que ser mayor a 0 we", ephemeral=True)
            cursor.execute("UPDATE tienda SET precio=? WHERE id=?", (precio, _id))
        elif campo_val == "descripcion":
            cursor.execute("UPDATE tienda SET descripcion=? WHERE id=?", (valor, _id))
        elif campo_val == "emoji":
            cursor.execute("UPDATE tienda SET emoji=? WHERE id=?", (valor, _id))
        elif campo_val == "usable":
            usable = 1 if valor.lower() in ("si", "sí", "true", "1", "yes") else 0
            cursor.execute("UPDATE tienda SET usable=? WHERE id=?", (usable, _id))
        elif campo_val == "mensaje":
            cursor.execute("UPDATE tienda SET mensaje_uso=? WHERE id=?", (valor, _id))
        elif campo_val == "imagen":
            cursor.execute("UPDATE tienda SET imagen=? WHERE id=?", (valor, _id))
        db.commit()
        await interaction.response.send_message(f"✅ El item **{nombre}** fue actualizado (`{campo_val}` → `{valor}`)")

    @edititem_slash.autocomplete("nombre")
    async def edititem_autocomplete(self, interaction: discord.Interaction, current: str):
        return await autocomplete_items_tienda(interaction, current)

    @app_commands.command(name="delitem", description="Elimina un item de la tienda")
    @app_commands.describe(nombre="Item a eliminar")
    @is_staff_app()
    async def delitem_slash(self, interaction: discord.Interaction, nombre: str):
        item = get_item_tienda(interaction.guild.id, nombre)
        if not item:
            return await interaction.response.send_message(f"❌ No existe el item `{nombre}` we", ephemeral=True)
        cursor.execute("DELETE FROM tienda WHERE id=?", (item[0],))
        db.commit()
        await interaction.response.send_message(f"🗑️ Item **{nombre}** eliminado de la tienda")

    @delitem_slash.autocomplete("nombre")
    async def delitem_autocomplete(self, interaction: discord.Interaction, current: str):
        return await autocomplete_items_tienda(interaction, current)

# ─────────────────────────────────────────
# COG CASINO — AHORA COMO SLASH "/" Y MULTIJUGADOR
# ─────────────────────────────────────────
class CasinoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _chequeo_inicial(self, interaction: discord.Interaction, juego_tipo: str, apuesta_texto: str):
        """Valida cooldown, apuesta (soporta 'all') y saldo del que inicia la ronda. Devuelve (cfg, ok, apuesta)."""
        guild_id = interaction.guild.id
        cfg = get_config(guild_id)
        restante = tiempo_restante(get_cooldown_generic(guild_id, interaction.user.id, juego_tipo), cfg[f"cooldown_{juego_tipo}"])
        if restante:
            await interaction.response.send_message(f"⏳ Espera **{formatear_tiempo(restante)}** para volver a jugar", ephemeral=True)
            return cfg, False, None
        saldo = get_balance(guild_id, interaction.user.id)
        apuesta = parse_cantidad(apuesta_texto, saldo)
        if apuesta is None:
            await interaction.response.send_message("❌ La apuesta tiene que ser un número o `all` we", ephemeral=True)
            return cfg, False, None
        ok, error = validar_apuesta_valores(cfg, apuesta)
        if not ok:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return cfg, False, None
        if saldo < apuesta:
            await interaction.response.send_message(f"❌ No tienes esa plata we, tu balance es **{format_dinero(guild_id, saldo)}**", ephemeral=True)
            return cfg, False, None
        return cfg, True, apuesta

    # ───────── SLOTS ─────────
    @app_commands.command(name="slots", description="Tragamonedas — invita a otros con el botón Unirse")
    @app_commands.describe(apuesta="Cuánto quieres apostar (o escribe 'all' para apostar todo)")
    async def slots_slash(self, interaction: discord.Interaction, apuesta: str):
        cfg, ok, apuesta = await self._chequeo_inicial(interaction, "slots", apuesta)
        if not ok:
            return
        guild_id = interaction.guild.id
        view = JoinView(guild_id, "slots", "🎰 Tragamonedas")
        view.participantes[interaction.user.id] = {"member": interaction.user, "apuesta": apuesta, "eleccion": None}
        await interaction.response.send_message(embed=construir_embed_join(view, JOIN_SECONDS), view=view)
        msg = await interaction.original_response()
        await ejecutar_join_window(view, msg)
        await self._resolver_slots(guild_id, msg, view, cfg)

    async def _resolver_slots(self, guild_id, msg, view, cfg):
        validos = revalidar_participantes(guild_id, view.participantes)
        if not validos:
            return await msg.edit(embed=discord.Embed(title="🎰 Tragamonedas", description="Nadie tenía plata suficiente we, se canceló la ronda", color=0xE74C3C), view=None)
        for uid in validos:
            modificar_balance(guild_id, uid, -validos[uid]["apuesta"])
            set_cooldown_generic(guild_id, uid, "slots")
        duracion = duracion_juego(len(validos))
        intervalo = 2.5
        frames = max(3, int(duracion // intervalo))
        for _ in range(frames):
            lineas = []
            for data in validos.values():
                reel = random.choices(SLOT_EMOJIS, k=3)
                lineas.append(f"{data['member'].mention}: [ {' | '.join(reel)} ]")
            emb = discord.Embed(title="🎰 Girando...", description="\n".join(lineas), color=0x3498DB)
            try:
                await msg.edit(embed=emb, view=None)
            except discord.HTTPException:
                pass
            await asyncio.sleep(intervalo)
        lineas_final = []
        for uid, data in validos.items():
            reel = random.choices(SLOT_EMOJIS, weights=SLOT_WEIGHTS, k=3)
            if reel[0] == reel[1] == reel[2]:
                ganancia = data["apuesta"] * cfg["slots_multi_x3"]
            elif len(set(reel)) == 2:
                ganancia = data["apuesta"] * cfg["slots_multi_x2"]
            else:
                ganancia = 0
            if ganancia:
                modificar_balance(guild_id, uid, ganancia)
            if ganancia > 0:
                lineas_final.append(f"{data['member'].mention}: [ {' | '.join(reel)} ] 🎉 +{format_dinero(guild_id, ganancia)}")
            else:
                lineas_final.append(f"{data['member'].mention}: [ {' | '.join(reel)} ] 💸 -{format_dinero(guild_id, data['apuesta'])}")
        emb_final = discord.Embed(title="🎰 Resultado final", description="\n".join(lineas_final), color=0x2ECC71)
        await msg.edit(embed=emb_final, view=None)

    # ───────── RULETA ─────────
    @app_commands.command(name="ruleta", description="Ruleta — invita a otros con el botón Unirse")
    @app_commands.describe(apuesta="Cuánto quieres apostar (o escribe 'all' para apostar todo)", color="rojo, negro o verde")
    @app_commands.choices(color=[
        app_commands.Choice(name="Rojo", value="rojo"),
        app_commands.Choice(name="Negro", value="negro"),
        app_commands.Choice(name="Verde", value="verde"),
    ])
    async def ruleta_slash(self, interaction: discord.Interaction, apuesta: str, color: app_commands.Choice[str]):
        cfg, ok, apuesta = await self._chequeo_inicial(interaction, "ruleta", apuesta)
        if not ok:
            return
        guild_id = interaction.guild.id
        view = JoinView(guild_id, "ruleta", "🎡 Ruleta", necesita_eleccion=True)
        view.participantes[interaction.user.id] = {"member": interaction.user, "apuesta": apuesta, "eleccion": color.value}
        await interaction.response.send_message(embed=construir_embed_join(view, JOIN_SECONDS), view=view)
        msg = await interaction.original_response()
        await ejecutar_join_window(view, msg)
        await self._resolver_ruleta(guild_id, msg, view, cfg)

    async def _resolver_ruleta(self, guild_id, msg, view, cfg):
        validos = revalidar_participantes(guild_id, view.participantes)
        if not validos:
            return await msg.edit(embed=discord.Embed(title="🎡 Ruleta", description="Nadie tenía plata suficiente we, se canceló la ronda", color=0xE74C3C), view=None)
        for uid in validos:
            modificar_balance(guild_id, uid, -validos[uid]["apuesta"])
            set_cooldown_generic(guild_id, uid, "ruleta")
        duracion = duracion_juego(len(validos))
        intervalo = 2.5
        frames = max(3, int(duracion // intervalo))
        emojis_color = {"rojo": "🔴", "negro": "⚫", "verde": "🟢"}
        for _ in range(frames):
            numero_temp = random.randint(0, 36)
            emb = discord.Embed(title="🎡 Girando...", description=f"La bola rebota... **{numero_temp} {emojis_color[color_ruleta(numero_temp)]}**", color=0x3498DB)
            try:
                await msg.edit(embed=emb, view=None)
            except discord.HTTPException:
                pass
            await asyncio.sleep(intervalo)
        numero = random.randint(0, 36)
        resultado_color = color_ruleta(numero)
        lineas_final = [f"La bola cayó en **{numero} {emojis_color[resultado_color]}**", ""]
        for uid, data in validos.items():
            if data["eleccion"] == resultado_color:
                multi = cfg["ruleta_multi_verde"] if resultado_color == "verde" else cfg["ruleta_multi_color"]
                ganancia = int(data["apuesta"] * multi)
                modificar_balance(guild_id, uid, ganancia)
                lineas_final.append(f"{data['member'].mention} apostó a **{data['eleccion']}** 🎉 +{format_dinero(guild_id, ganancia)}")
            else:
                lineas_final.append(f"{data['member'].mention} apostó a **{data['eleccion']}** 💸 -{format_dinero(guild_id, data['apuesta'])}")
        emb_final = discord.Embed(title="🎡 Resultado final", description="\n".join(lineas_final), color=0x2ECC71)
        await msg.edit(embed=emb_final, view=None)

    # ───────── COINFLIP ─────────
    @app_commands.command(name="coinflip", description="Cara o cruz — invita a otros con el botón Unirse")
    @app_commands.describe(apuesta="Cuánto quieres apostar (o escribe 'all' para apostar todo)", lado="cara o cruz")
    @app_commands.choices(lado=[
        app_commands.Choice(name="Cara", value="cara"),
        app_commands.Choice(name="Cruz", value="cruz"),
    ])
    async def coinflip_slash(self, interaction: discord.Interaction, apuesta: str, lado: app_commands.Choice[str]):
        cfg, ok, apuesta = await self._chequeo_inicial(interaction, "coinflip", apuesta)
        if not ok:
            return
        guild_id = interaction.guild.id
        view = JoinView(guild_id, "coinflip", "🪙 Coinflip", necesita_eleccion=True)
        view.participantes[interaction.user.id] = {"member": interaction.user, "apuesta": apuesta, "eleccion": lado.value}
        await interaction.response.send_message(embed=construir_embed_join(view, JOIN_SECONDS), view=view)
        msg = await interaction.original_response()
        await ejecutar_join_window(view, msg)
        await self._resolver_coinflip(guild_id, msg, view, cfg)

    async def _resolver_coinflip(self, guild_id, msg, view, cfg):
        validos = revalidar_participantes(guild_id, view.participantes)
        if not validos:
            return await msg.edit(embed=discord.Embed(title="🪙 Coinflip", description="Nadie tenía plata suficiente we, se canceló la ronda", color=0xE74C3C), view=None)
        for uid in validos:
            modificar_balance(guild_id, uid, -validos[uid]["apuesta"])
            set_cooldown_generic(guild_id, uid, "coinflip")
        duracion = duracion_juego(len(validos))
        intervalo = 2.0
        frames = max(4, int(duracion // intervalo))
        for i in range(frames):
            cara_arriba = i % 2 == 0
            emoji_temp = "🙂" if cara_arriba else "🌀"
            emb = discord.Embed(title="🪙 Girando la moneda...", description=f"{emoji_temp}", color=0x3498DB)
            try:
                await msg.edit(embed=emb, view=None)
            except discord.HTTPException:
                pass
            await asyncio.sleep(intervalo)
        resultado = random.choice(["cara", "cruz"])
        emoji_resultado = "🙂" if resultado == "cara" else "🌀"
        lineas_final = [f"Salió **{resultado} {emoji_resultado}**", ""]
        for uid, data in validos.items():
            if data["eleccion"] == resultado:
                ganancia = data["apuesta"] * 2
                modificar_balance(guild_id, uid, ganancia)
                lineas_final.append(f"{data['member'].mention} apostó a **{data['eleccion']}** 🎉 +{format_dinero(guild_id, ganancia)}")
            else:
                lineas_final.append(f"{data['member'].mention} apostó a **{data['eleccion']}** 💸 -{format_dinero(guild_id, data['apuesta'])}")
        emb_final = discord.Embed(title="🪙 Resultado final", description="\n".join(lineas_final), color=0x2ECC71)
        await msg.edit(embed=emb_final, view=None)

    # ───────── BLACKJACK (multijugador, turnos secuenciales contra un dealer compartido) ─────────
    @app_commands.command(name="blackjack", description="Blackjack — invita a otros con el botón Unirse")
    @app_commands.describe(apuesta="Cuánto quieres apostar (o escribe 'all' para apostar todo)")
    async def blackjack_slash(self, interaction: discord.Interaction, apuesta: str):
        cfg, ok, apuesta = await self._chequeo_inicial(interaction, "blackjack", apuesta)
        if not ok:
            return
        guild_id = interaction.guild.id
        view = JoinView(guild_id, "blackjack", "🃏 Blackjack")
        view.participantes[interaction.user.id] = {"member": interaction.user, "apuesta": apuesta, "eleccion": None}
        await interaction.response.send_message(embed=construir_embed_join(view, JOIN_SECONDS), view=view)
        msg = await interaction.original_response()
        await ejecutar_join_window(view, msg)
        await self._resolver_blackjack(guild_id, msg, view)

    async def _resolver_blackjack(self, guild_id, msg, view):
        validos = revalidar_participantes(guild_id, view.participantes)
        if not validos:
            return await msg.edit(embed=discord.Embed(title="🃏 Blackjack", description="Nadie tenía plata suficiente we, se canceló la ronda", color=0xE74C3C), view=None)
        for uid in validos:
            modificar_balance(guild_id, uid, -validos[uid]["apuesta"])
            set_cooldown_generic(guild_id, uid, "blackjack")

        mazo = crear_mazo()
        dealer = [mazo.pop(), mazo.pop()]
        manos = {}
        for uid in validos:
            manos[uid] = [mazo.pop(), mazo.pop()]

        resultados_naturales = {}
        for uid in list(validos.keys()):
            if valor_mano(manos[uid]) == 21:
                resultados_naturales[uid] = "blackjack"

        # turnos secuenciales para quienes no tengan blackjack natural
        for uid, data in validos.items():
            if uid in resultados_naturales:
                continue
            turno = TurnoBlackjackView(uid, manos[uid], mazo)
            emb = turno.construir_embed(data["member"], dealer)
            try:
                await msg.edit(embed=emb, view=turno)
            except discord.HTTPException:
                pass
            try:
                await asyncio.wait_for(turno.evento.wait(), timeout=35)
            except asyncio.TimeoutError:
                pass
            if valor_mano(manos[uid]) > 21:
                resultados_naturales[uid] = "bust"

        # el dealer juega al final, contra todos
        while valor_mano(dealer) < 17:
            dealer.append(mazo.pop())
        valor_dealer = valor_mano(dealer)

        lineas_final = [f"Mano del dealer ({valor_dealer}): {' '.join(dealer)}", ""]
        for uid, data in validos.items():
            mano = manos[uid]
            valor_jugador = valor_mano(mano)
            apuesta = data["apuesta"]
            if resultados_naturales.get(uid) == "blackjack":
                if valor_dealer == 21 and len(dealer) == 2:
                    modificar_balance(guild_id, uid, apuesta)
                    texto = f"🤝 Empate (Blackjack). Recupera su apuesta"
                else:
                    ganancia = int(apuesta * 2.5)
                    modificar_balance(guild_id, uid, ganancia)
                    texto = f"🂡 ¡Blackjack! +{format_dinero(guild_id, ganancia)}"
            elif valor_jugador > 21:
                texto = f"💥 Se pasó de 21 ({valor_jugador}). -{format_dinero(guild_id, apuesta)}"
            elif valor_dealer > 21 or valor_jugador > valor_dealer:
                ganancia = apuesta * 2
                modificar_balance(guild_id, uid, ganancia)
                texto = f"🎉 Ganó ({valor_jugador} vs {valor_dealer}). +{format_dinero(guild_id, ganancia)}"
            elif valor_jugador == valor_dealer:
                modificar_balance(guild_id, uid, apuesta)
                texto = f"🤝 Empate ({valor_jugador} vs {valor_dealer}). Recupera su apuesta"
            else:
                texto = f"😢 Perdió ({valor_jugador} vs {valor_dealer}). -{format_dinero(guild_id, apuesta)}"
            lineas_final.append(f"{data['member'].mention}: {' '.join(mano)} ({valor_jugador}) — {texto}")

        emb_final = discord.Embed(title="🃏 Resultado final", description="\n".join(lineas_final), color=0x2ECC71)
        await msg.edit(embed=emb_final, view=None)

    # ───────── RULETA RUSA ─────────
    @app_commands.command(name="ruletarusa", description=f"Ruleta Rusa — juego de eliminación, mínimo {RUSA_MIN_JUGADORES} jugadores")
    async def ruletarusa_slash(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        cfg = get_config(guild_id)
        restante = tiempo_restante(get_cooldown_generic(guild_id, interaction.user.id, "ruletarusa"), cfg["cooldown_ruletarusa"])
        if restante:
            return await interaction.response.send_message(f"⏳ Espera **{formatear_tiempo(restante)}** para volver a jugar", ephemeral=True)
        view = RusaJoinView(guild_id)
        view.participantes[interaction.user.id] = interaction.user
        await interaction.response.send_message(embed=construir_embed_rusa_join(view, RUSA_JOIN_SECONDS), view=view)
        msg = await interaction.original_response()
        await ejecutar_rusa_join_window(view, msg)
        await self._resolver_ruletarusa(guild_id, msg, view)

    async def _resolver_ruletarusa(self, guild_id, msg, view: RusaJoinView):
        jugadores = dict(view.participantes)
        if len(jugadores) < RUSA_MIN_JUGADORES:
            return await msg.edit(
                embed=discord.Embed(title="🔫 Ruleta Rusa",
                                     description=f"No se juntaron los {RUSA_MIN_JUGADORES} jugadores mínimos we, se canceló la ronda",
                                     color=0xE74C3C),
                view=None)
        for uid in jugadores:
            set_cooldown_generic(guild_id, uid, "ruletarusa")

        restantes = dict(jugadores)  # user_id -> Member, todavía en juego
        eliminados_texto = []
        pozo = 0

        def construir_embed_ronda(titulo_extra: str = ""):
            vivos = "\n".join(f"• {m.mention}" for m in restantes.values())
            desc = f"**Quedan en juego:**\n{vivos}\n"
            if eliminados_texto:
                desc += "\n**Eliminados:**\n" + "\n".join(eliminados_texto)
            emb = discord.Embed(title=f"🔫 Ruleta Rusa {titulo_extra}", description=desc, color=0x992D22)
            emb.set_footer(text=f"💰 Pozo acumulado: {format_dinero(guild_id, pozo)}")
            return emb

        try:
            await msg.edit(embed=construir_embed_ronda("— ¡Empieza el juego!"), view=None)
        except discord.HTTPException:
            pass

        while len(restantes) > 1:
            await asyncio.sleep(RUSA_INTERVALO)
            eliminado_id = random.choice(list(restantes.keys()))
            eliminado_member = restantes.pop(eliminado_id)
            saldo_actual = get_balance(guild_id, eliminado_id)
            monto_quitado = int(saldo_actual * RUSA_PORCENTAJE)
            if monto_quitado > 0:
                modificar_balance(guild_id, eliminado_id, -monto_quitado)
                pozo += monto_quitado
            eliminados_texto.append(f"💥 {eliminado_member.mention} fue eliminado — perdió **{format_dinero(guild_id, monto_quitado)}**")
            try:
                await msg.edit(embed=construir_embed_ronda())
            except discord.HTTPException:
                pass

        ganador_id, ganador_member = next(iter(restantes.items()))
        if pozo > 0:
            modificar_balance(guild_id, ganador_id, pozo)
        emb_final = discord.Embed(
            title="🏆 Ruleta Rusa — ¡Tenemos ganador!",
            description=(f"{ganador_member.mention} sobrevivió a todos we 🔫\n\n"
                         f"Se ganó el pozo completo: **{format_dinero(guild_id, pozo)}**\n\n"
                         + ("\n".join(eliminados_texto) if eliminados_texto else "")),
            color=0xF1C40F)
        await msg.edit(embed=emb_final, view=None)

class TurnoBlackjackView(discord.ui.View):
    """Turno individual de un jugador contra el dealer (compartido entre todos los participantes)."""
    def __init__(self, participante_id: int, mano, mazo):
        super().__init__(timeout=30)
        self.participante_id = participante_id
        self.mano = mano
        self.mazo = mazo
        self.terminado = False
        self.evento = asyncio.Event()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.participante_id:
            await interaction.response.send_message("No es tu turno we", ephemeral=True)
            return False
        return True

    def construir_embed(self, member: discord.Member, dealer) -> discord.Embed:
        emb = discord.Embed(title=f"🃏 Turno de {member.display_name}", color=0x3498DB)
        emb.add_field(name=f"Su mano ({valor_mano(self.mano)})", value=" ".join(self.mano), inline=False)
        emb.add_field(name="Mano del dealer", value=f"{dealer[0]} 🂠", inline=False)
        return emb

    @discord.ui.button(label="Pedir", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.terminado:
            return
        self.mano.append(self.mazo.pop())
        if valor_mano(self.mano) > 21:
            self.terminado = True
            for item in self.children:
                item.disabled = True
            emb = discord.Embed(title=f"🃏 {interaction.user.display_name} se pasó de 21", description=f"Mano: {' '.join(self.mano)} ({valor_mano(self.mano)})", color=0xE74C3C)
            await interaction.response.edit_message(embed=emb, view=self)
            self.evento.set()
        else:
            emb = discord.Embed(title=f"🃏 Turno de {interaction.user.display_name}", color=0x3498DB)
            emb.add_field(name=f"Su mano ({valor_mano(self.mano)})", value=" ".join(self.mano), inline=False)
            await interaction.response.edit_message(embed=emb, view=self)

    @discord.ui.button(label="Plantarse", style=discord.ButtonStyle.secondary, emoji="✋")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.terminado:
            return
        self.terminado = True
        for item in self.children:
            item.disabled = True
        emb = discord.Embed(title=f"✋ {interaction.user.display_name} se plantó", description=f"Mano: {' '.join(self.mano)} ({valor_mano(self.mano)})", color=0x95A5A6)
        await interaction.response.edit_message(embed=emb, view=self)
        self.evento.set()

    async def on_timeout(self):
        if not self.terminado:
            self.terminado = True
            self.evento.set()

# ─────────────────────────────────────────
# COG HELP
# ─────────────────────────────────────────
class HelpCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="help", description="Muestra todos los comandos de Teto")
    async def help(self, interaction: discord.Interaction):
        staff = interaction.user.id == TU_ID or any(r.name in ROLES_COMANDOS for r in interaction.user.roles)
        embed = discord.Embed(title="📖 Comandos de Teto", color=0x3498DB,
                              description="Bot de economía. La economía usa `!`, la configuración y los juegos usan `/`.")
        embed.set_thumbnail(url=interaction.guild.me.display_avatar.url)

        embed.add_field(name="💰 Economía", value=(
            "`!balance` — Ve tu plata (cartera + banco)\n"
            "`!trabajo` — Trabaja y gana plata\n"
            "`!crime` — Arriésgate a ganar o perder plata\n"
            "`!robar @user` — Intenta robarle a alguien (solo puede robarte lo que tienes en la cartera, no lo del banco)\n"
            "`!dar @user <cantidad|all>` — Regala plata a otro\n"
            "`!leaderboard` — Ranking de los más ricos"
        ), inline=False)
        embed.add_field(name="🏦 Banco", value=(
            "`!deposit <cantidad|all>` — Guarda plata en el banco, ahí nadie te la puede robar\n"
            "`!retirar <cantidad|all>` — Saca plata del banco a tu cartera"
        ), inline=False)
        embed.add_field(name="🛒 Tienda", value=(
            "`!tienda` — Ve los items disponibles con botones para comprar al toque\n"
            "`!comprar <item>` — Compra un item (alternativa por texto)\n"
            "`!inventario` — Ve tu inventario\n"
            "`!useitem <item>` — Usa un item de tu inventario"
        ), inline=False)
        embed.add_field(name="🎰 Casino", value=(
            "`/slots <apuesta>` — Tragamonedas\n"
            "`/ruleta <apuesta> <color>` — Ruleta\n"
            "`/coinflip <apuesta> <lado>` — Cara o cruz\n"
            "`/blackjack <apuesta>` — Blackjack\n"
            "En todos puedes escribir `all` en vez de un número para apostar toda tu plata.\n"
            "Todos tienen un botón **Unirse 🎟️** para sumar más jugadores; la ronda dura entre 20 y 30s según cuánta gente participe.\n\n"
            f"`/ruletarusa` — Juego de eliminación, sin apuesta. Mínimo {RUSA_MIN_JUGADORES} jugadores; "
            f"cada {RUSA_INTERVALO}s se elimina a alguien al azar y pierde el {int(RUSA_PORCENTAJE*100)}% de su plata. "
            "El último que quede se gana todo lo perdido por los demás."
        ), inline=False)

        if staff:
            embed.add_field(name="💰 Economía Staff 🔒", value=(
                "`/add-money <user> <cantidad>` — Agrega plata a un usuario\n"
                "`/remove-money <user> <cantidad>` — Quita plata a un usuario"
            ), inline=False)
            embed.add_field(name="🛒 Tienda Staff 🔒", value=(
                "`/additem <nombre> <precio> <descripcion> <imagen>` — Agrega un item a la tienda\n"
                "`/edititem <nombre> <campo> <valor>` — Edita nombre/precio/descripción/emoji/usable/mensaje/imagen\n"
                "`/delitem <nombre>` — Elimina un item de la tienda"
            ), inline=False)
            embed.add_field(name="⚙️ Configuración Staff 🔒", value=(
                "`/config` — Ve la configuración actual\n"
                "`/setconfig <clave> <valor>` — Cambia un valor\n"
                "`/setcooldown <comando> <tiempo>` — Cambia un cooldown (ej: `1h30m`)\n"
                "`/setmoneda <emoji>` — Cambia el emoji de la moneda del servidor\n"
                "`/resetconfig` — Restablece los valores por defecto"
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

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        texto = "❌ Ese comando es solo para Staff we"
    else:
        log.exception("Error en slash command", exc_info=error)
        texto = "❌ Ocurrió un error inesperado we"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(texto, ephemeral=True)
        else:
            await interaction.response.send_message(texto, ephemeral=True)
    except discord.HTTPException:
        pass

@bot.event
async def on_ready():
    await bot.add_cog(EconomiaCog(bot))
    await bot.add_cog(CasinoCog(bot))
    await bot.add_cog(ConfigCog(bot))
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
