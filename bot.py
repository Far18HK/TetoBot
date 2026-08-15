import discord
import os
import re
import random
import psycopg2
import logging
import asyncio
import math
import json
import io
import base64
import httpx
from datetime import timedelta, datetime, timezone
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

# ─────────────────────────────────────────
# CONFIG GENERAL
# ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("Teto")

TOKEN = os.getenv("TOKEN")
TU_ID = 1180967503682355220
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY")

if not TOKEN:
    raise RuntimeError("❌ Falta TOKEN")

# ─────────────────────────────────────────
# SUBIDA DE IMÁGENES (para que se puedan subir archivos en vez de solo pegar un URL)
# ─────────────────────────────────────────
async def subir_imagen(attachment: discord.Attachment) -> str:
    """Sube un archivo adjunto de Discord a imgbb y devuelve el URL permanente.
    Si falla (o no hay IMGBB_API_KEY configurada) devuelve "".
    No usamos el .url del adjunto directamente porque los links de attachments de
    Discord expiran (llevan parámetros ex/is/hm que vencen), así que guardarlos tal
    cual en la base de datos los terminaría rompiendo."""
    if not IMGBB_API_KEY:
        log.warning("IMGBB_API_KEY no configurada — no se puede subir la imagen")
        return ""
    if not (attachment.content_type or "").startswith("image/"):
        return ""
    try:
        data = await attachment.read()
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.imgbb.com/1/upload",
                data={"key": IMGBB_API_KEY, "image": base64.b64encode(data).decode()},
            )
        if resp.status_code == 200:
            return resp.json()["data"]["url"]
        log.warning(f"imgbb respondió {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.warning(f"Fallo subiendo imagen a imgbb: {e}")
    return ""

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────────────────
# CHECK PA STAFF
# (el dueño del server y tu ID siempre tienen acceso total; además el dueño puede dar
# permisos a roles o usuarios específicos desde el Dashboard web)
# ─────────────────────────────────────────
def es_staff_member(member: discord.Member) -> bool:
    """Staff 'de máximo nivel': tu ID o el dueño del server. Se usa para /datos (backup completo)."""
    if member.id == TU_ID:
        return True
    return member.id == member.guild.owner_id

def get_staff_permisos(guild_id: int):
    cursor.execute("SELECT tipo, discord_id, puede_tienda, puede_economia FROM dashboard_staff WHERE guild_id=%s", (guild_id,))
    return cursor.fetchall()

def _tiene_permiso(member: discord.Member, campo: str) -> bool:
    """campo: 'puede_tienda' o 'puede_economia'. Revisa el dueño/TU_ID primero (acceso total),
    luego busca en dashboard_staff si el usuario o alguno de sus roles tiene ese permiso."""
    if es_staff_member(member):
        return True
    idx = 2 if campo == "puede_tienda" else 3
    ids_roles = {r.id for r in member.roles}
    for tipo, discord_id, puede_tienda, puede_economia in get_staff_permisos(member.guild.id):
        valor = puede_tienda if campo == "puede_tienda" else puede_economia
        if not valor:
            continue
        if tipo == "usuario" and discord_id == member.id:
            return True
        if tipo == "rol" and discord_id in ids_roles:
            return True
    return False

def puede_tienda_member(member: discord.Member) -> bool:
    # el permiso de economía también da acceso a la tienda (jerarquía: economía > tienda)
    return _tiene_permiso(member, "puede_tienda") or _tiene_permiso(member, "puede_economia")

def puede_economia_member(member: discord.Member) -> bool:
    return _tiene_permiso(member, "puede_economia")

def is_staff_ctx():
    async def predicate(ctx: commands.Context) -> bool:
        return es_staff_member(ctx.author)
    return commands.check(predicate)

def is_staff_app():
    """Solo tu ID o el dueño del server (para acciones sensibles como /datos)."""
    async def predicate(interaction: discord.Interaction) -> bool:
        return isinstance(interaction.user, discord.Member) and es_staff_member(interaction.user)
    return app_commands.check(predicate)

def is_staff_tienda_app():
    """Dueño/TU_ID, o cualquier rol/usuario al que el dueño le haya dado permiso de tienda
    (o de economía, que incluye tienda) desde el Dashboard."""
    async def predicate(interaction: discord.Interaction) -> bool:
        return isinstance(interaction.user, discord.Member) and puede_tienda_member(interaction.user)
    return app_commands.check(predicate)

def is_staff_economia_app():
    """Dueño/TU_ID, o cualquier rol/usuario al que el dueño le haya dado permiso de economía
    (dar/quitar plata) desde el Dashboard."""
    async def predicate(interaction: discord.Interaction) -> bool:
        return isinstance(interaction.user, discord.Member) and puede_economia_member(interaction.user)
    return app_commands.check(predicate)

# ─────────────────────────────────────────
# DB
# ─────────────────────────────────────────
# Base de datos Postgres (ej. Neon). Define la variable de entorno DATABASE_URL
# con la connection string que te da tu proveedor (incluye sslmode=require).
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("❌ Falta DATABASE_URL")


def _conectar_db():
    """Abre una conexión nueva a Postgres. Se usa al inicio y para reconectar si Neon corta la conexión."""
    global db
    db = psycopg2.connect(DATABASE_URL)  # la connection string de Neon ya incluye ?sslmode=require
    db.autocommit = True  # cada statement se confirma solo; evita transacciones colgadas si algo falla
    return db.cursor()


class CursorConReconexion:
    """Envoltorio sobre el cursor de psycopg2 que reconecta solo si Neon cerró la conexión
    por inactividad (típico en Postgres serverless). Si execute() falla por eso, reabre la
    conexión y reintenta una vez; si vuelve a fallar, deja que el error suba como normalmente."""

    def __init__(self):
        self._real = _conectar_db()

    def execute(self, query, params=None):
        try:
            return self._real.execute(query, params if params is not None else ())
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            self._real = _conectar_db()
            return self._real.execute(query, params if params is not None else ())

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)


cursor = CursorConReconexion()
cursor.execute("""CREATE TABLE IF NOT EXISTS economia (
    guild_id BIGINT, user_id BIGINT, balance INTEGER DEFAULT 0, banco INTEGER DEFAULT 0,
    last_trabajo TEXT, last_crime TEXT, last_robar TEXT,
    PRIMARY KEY (guild_id, user_id)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS tienda (
    id SERIAL PRIMARY KEY, guild_id BIGINT, nombre TEXT, precio INTEGER, descripcion TEXT,
    usable INTEGER DEFAULT 0, mensaje_uso TEXT DEFAULT '', imagen TEXT DEFAULT '',
    rol_id BIGINT DEFAULT 0, dinero_efecto INTEGER DEFAULT 0, es_seguro INTEGER DEFAULT 0
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS inventario (
    guild_id BIGINT, user_id BIGINT, item TEXT, cantidad INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, item)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS seguros (
    guild_id BIGINT, user_id BIGINT, cantidad INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS cooldowns (
    guild_id BIGINT, user_id BIGINT, tipo TEXT, ultimo TEXT,
    PRIMARY KEY (guild_id, user_id, tipo)
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS config (
    guild_id BIGINT PRIMARY KEY,
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
    ruleta_multi_numero INTEGER DEFAULT 35,
    cooldown_ruletarusa INTEGER DEFAULT 60,
    cooldown_slut INTEGER DEFAULT 1800,
    slut_chance REAL DEFAULT 0.6,
    slut_win_min INTEGER DEFAULT 100,
    slut_win_max INTEGER DEFAULT 400,
    slut_loss_min INTEGER DEFAULT 50,
    slut_loss_max INTEGER DEFAULT 250,
    usar_respuestas_default INTEGER DEFAULT 1
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS dashboard_staff (
    id SERIAL PRIMARY KEY, guild_id BIGINT, tipo TEXT, discord_id BIGINT,
    puede_tienda INTEGER DEFAULT 1, puede_economia INTEGER DEFAULT 0
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS mensajes_custom (
    id SERIAL PRIMARY KEY, guild_id BIGINT, tipo TEXT, texto TEXT
)""")
# Migraciones para bases de datos ya existentes (por si les faltan columnas nuevas)
_MIGRACIONES = [
    ("economia", "banco", "INTEGER DEFAULT 0"),
    ("config", "usar_respuestas_default", "INTEGER DEFAULT 1"),
    ("tienda", "usable", "INTEGER DEFAULT 0"),
    ("tienda", "mensaje_uso", "TEXT DEFAULT ''"),
    ("tienda", "imagen", "TEXT DEFAULT ''"),
    ("tienda", "rol_id", "INTEGER DEFAULT 0"),
    ("tienda", "dinero_efecto", "INTEGER DEFAULT 0"),
    ("tienda", "es_seguro", "INTEGER DEFAULT 0"),
    ("tienda", "categoria", "TEXT DEFAULT 'General'"),
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
    ("config", "ruleta_multi_numero", "INTEGER DEFAULT 35"),
    ("config", "cooldown_ruletarusa", "INTEGER DEFAULT 60"),
    ("config", "cooldown_slut", "INTEGER DEFAULT 1800"),
    ("config", "slut_chance", "REAL DEFAULT 0.6"),
    ("config", "slut_win_min", "INTEGER DEFAULT 100"),
    ("config", "slut_win_max", "INTEGER DEFAULT 400"),
    ("config", "slut_loss_min", "INTEGER DEFAULT 50"),
    ("config", "slut_loss_max", "INTEGER DEFAULT 250"),
]
for _tabla, _columna, _definicion in _MIGRACIONES:
    cursor.execute(f"ALTER TABLE {_tabla} ADD COLUMN IF NOT EXISTS {_columna} {_definicion}")

# ─────────────────────────────────────────
# CONFIGURACIÓN POR SERVIDOR
# (los valores ahora se editan desde el Dashboard web, el bot solo los lee)
# ─────────────────────────────────────────
def get_config(guild_id: int) -> dict:
    cursor.execute("INSERT INTO config (guild_id) VALUES (%s) ON CONFLICT (guild_id) DO NOTHING", (guild_id,))
    db.commit()
    cursor.execute("SELECT * FROM config WHERE guild_id=%s", (guild_id,))
    row = cursor.fetchone()
    columnas = [d[0] for d in cursor.description]
    return dict(zip(columnas, row))

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
    cursor.execute("INSERT INTO economia (guild_id, user_id, balance) VALUES (%s,%s,0) ON CONFLICT (guild_id, user_id) DO NOTHING", (guild_id, user_id))
    db.commit()

def get_balance(guild_id: int, user_id: int) -> int:
    _ensure_user(guild_id, user_id)
    cursor.execute("SELECT balance FROM economia WHERE guild_id=%s AND user_id=%s", (guild_id, user_id))
    row = cursor.fetchone()
    return row[0] if row else 0

def modificar_balance(guild_id: int, user_id: int, cantidad: int):
    _ensure_user(guild_id, user_id)
    cursor.execute("UPDATE economia SET balance = GREATEST(balance + %s, 0) WHERE guild_id=%s AND user_id=%s", (cantidad, guild_id, user_id))
    db.commit()

def get_banco(guild_id: int, user_id: int) -> int:
    _ensure_user(guild_id, user_id)
    cursor.execute("SELECT banco FROM economia WHERE guild_id=%s AND user_id=%s", (guild_id, user_id))
    row = cursor.fetchone()
    return row[0] if row else 0

def modificar_banco(guild_id: int, user_id: int, cantidad: int):
    _ensure_user(guild_id, user_id)
    cursor.execute("UPDATE economia SET banco = GREATEST(banco + %s, 0) WHERE guild_id=%s AND user_id=%s", (cantidad, guild_id, user_id))
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
    cursor.execute(f"SELECT {campo} FROM economia WHERE guild_id=%s AND user_id=%s", (guild_id, user_id))
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    return datetime.fromisoformat(row[0])

def set_cooldown(guild_id: int, user_id: int, campo: str):
    _ensure_user(guild_id, user_id)
    cursor.execute(f"UPDATE economia SET {campo} = %s WHERE guild_id=%s AND user_id=%s", (datetime.now(timezone.utc).isoformat(), guild_id, user_id))
    db.commit()

def get_cooldown_generic(guild_id: int, user_id: int, tipo: str):
    cursor.execute("SELECT ultimo FROM cooldowns WHERE guild_id=%s AND user_id=%s AND tipo=%s", (guild_id, user_id, tipo))
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    return datetime.fromisoformat(row[0])

def set_cooldown_generic(guild_id: int, user_id: int, tipo: str):
    ahora = datetime.now(timezone.utc).isoformat()
    cursor.execute("""INSERT INTO cooldowns (guild_id, user_id, tipo, ultimo) VALUES (%s,%s,%s,%s)
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
    cursor.execute("SELECT item, cantidad FROM inventario WHERE guild_id=%s AND user_id=%s AND cantidad > 0 ORDER BY item", (guild_id, user_id))
    return cursor.fetchall()

def add_item(guild_id: int, user_id: int, item: str, cantidad: int = 1):
    cursor.execute("""INSERT INTO inventario (guild_id, user_id, item, cantidad) VALUES (%s,%s,%s,%s)
        ON CONFLICT(guild_id, user_id, item) DO UPDATE SET cantidad = inventario.cantidad + excluded.cantidad""",
        (guild_id, user_id, item, cantidad))
    db.commit()

def get_tienda(guild_id: int, categoria: str = None):
    """Si `categoria` es None, trae todos los items ordenados por categoría y luego precio.
    Si se pasa una categoría, filtra solo esos items (ordenados por precio)."""
    if categoria:
        cursor.execute("""SELECT id, nombre, precio, descripcion, usable, imagen,
                           rol_id, dinero_efecto, es_seguro, categoria FROM tienda
                           WHERE guild_id=%s AND categoria=%s ORDER BY precio ASC""", (guild_id, categoria))
    else:
        cursor.execute("""SELECT id, nombre, precio, descripcion, usable, imagen,
                           rol_id, dinero_efecto, es_seguro, categoria FROM tienda
                           WHERE guild_id=%s ORDER BY categoria ASC, precio ASC""", (guild_id,))
    return cursor.fetchall()

def get_categorias(guild_id: int) -> list:
    """Lista de categorías distintas que ya tienen al menos un item en este server."""
    cursor.execute("SELECT DISTINCT categoria FROM tienda WHERE guild_id=%s ORDER BY categoria ASC", (guild_id,))
    return [fila[0] for fila in cursor.fetchall()]

def get_item_tienda(guild_id: int, nombre: str):
    cursor.execute("""SELECT id, nombre, precio, descripcion, usable, mensaje_uso, imagen,
                       rol_id, dinero_efecto, es_seguro, categoria FROM tienda WHERE guild_id=%s AND LOWER(nombre)=LOWER(%s)""", (guild_id, nombre))
    return cursor.fetchone()

# ─────────────────────────────────────────
# SEGUROS (protección contra !robar)
# ─────────────────────────────────────────
def get_seguros(guild_id: int, user_id: int) -> int:
    cursor.execute("SELECT cantidad FROM seguros WHERE guild_id=%s AND user_id=%s", (guild_id, user_id))
    fila = cursor.fetchone()
    return fila[0] if fila else 0

def add_seguro(guild_id: int, user_id: int, cantidad: int = 1):
    cursor.execute("""INSERT INTO seguros (guild_id, user_id, cantidad) VALUES (%s,%s,%s)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET cantidad = seguros.cantidad + excluded.cantidad""",
        (guild_id, user_id, cantidad))
    db.commit()

def consumir_seguro(guild_id: int, user_id: int):
    cursor.execute("UPDATE seguros SET cantidad = GREATEST(cantidad - 1, 0) WHERE guild_id=%s AND user_id=%s", (guild_id, user_id))
    db.commit()

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

MAX_COMPONENTES_TIENDA = 40  # límite duro de Discord para Components V2 en un solo mensaje

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
        _id, nombre_real, precio, descripcion, usable, mensaje_uso, imagen, rol_id, dinero_efecto, es_seguro, categoria = item
        saldo = get_balance(guild_id, interaction.user.id)
        if saldo < precio:
            return await interaction.response.send_message(
                f"❌ No tienes suficiente plata we. Necesitas **{format_dinero(guild_id, precio)}** y tienes **{format_dinero(guild_id, saldo)}**",
                ephemeral=True)
        modificar_balance(guild_id, interaction.user.id, -precio)
        add_item(guild_id, interaction.user.id, nombre_real, 1)
        await interaction.response.send_message(
            f"✅ Compraste **{nombre_real}** por **{format_dinero(guild_id, precio)}**", ephemeral=True)

CATEGORIA_TODAS = "__all__"

class CategoriaSelect(discord.ui.Select):
    """Dropdown para filtrar la tienda por categoría. 'Todas las categorías' es la opción
    predeterminada."""
    def __init__(self, guild_id: int, categorias: list, categoria_actual: str = None):
        opciones = [discord.SelectOption(
            label="🗂️ Todas las categorías", value=CATEGORIA_TODAS,
            default=(categoria_actual is None))]
        for cat in categorias:
            opciones.append(discord.SelectOption(
                label=cat[:100], value=cat[:100], default=(cat == categoria_actual)))
        super().__init__(placeholder="Filtrar por categoría", min_values=1, max_values=1,
                          options=opciones[:25])
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        seleccion = self.values[0]
        categoria = None if seleccion == CATEGORIA_TODAS else seleccion
        items = get_tienda(self.guild_id, categoria)
        # al cambiar de categoría siempre volvemos a la página 1
        nueva_vista = TiendaLayoutView(self.guild_id, items, categoria_actual=categoria, pagina=0)
        await interaction.response.edit_message(view=nueva_vista)

class TiendaNavButton(discord.ui.Button):
    """Botón de navegación (⏮ ◀ ▶ ⏭) para pasar de página en la tienda."""
    def __init__(self, emoji: str, pagina_destino: int, guild_id: int, categoria_actual: str, disabled: bool):
        super().__init__(style=discord.ButtonStyle.secondary, emoji=emoji, disabled=disabled)
        self.pagina_destino = pagina_destino
        self.guild_id = guild_id
        self.categoria_actual = categoria_actual

    async def callback(self, interaction: discord.Interaction):
        items = get_tienda(self.guild_id, self.categoria_actual)
        nueva_vista = TiendaLayoutView(self.guild_id, items, categoria_actual=self.categoria_actual, pagina=self.pagina_destino)
        await interaction.response.edit_message(view=nueva_vista)

class TiendaLayoutView(discord.ui.LayoutView):
    """Tienda armada con Components V2: selector de categoría arriba, cada item como
    una fila con miniatura + texto + botón de compra, y navegación por páginas abajo,
    todo en un único mensaje (sin embeds).

    Discord permite máximo 40 componentes en total por mensaje (contando todo lo anidado:
    separadores, secciones, texto, botones, etc). Cada item gasta una cantidad distinta de
    componentes según tenga imagen o no, así que en vez de cortar la lista y mostrar un
    aviso, repartimos los items en páginas que sí caben dentro del límite y dejamos
    botones para moverse entre ellas."""

    @staticmethod
    def _costo_bloque(tipo: str, data) -> int:
        if tipo == "header":
            return 2  # Separator(1) + TextDisplay(1)
        _id, nombre, precio, descripcion, usable, imagen, rol_id, dinero_efecto, es_seguro, categoria = data
        tiene_imagen = bool(imagen) and imagen.strip().lower().startswith(("http://", "https://"))
        if tiene_imagen:
            return 6  # Separator(1) + Section(1) + TextDisplay(1) + Thumbnail(1) + ActionRow(1) + Button(1)
        return 4  # Separator(1) + Section(1) + TextDisplay(1) + Button-accessory(1)

    @classmethod
    def _construir_paginas(cls, bloques: list, presupuesto: int) -> list:
        """Reparte los bloques (headers + items) en páginas que quepan dentro del
        presupuesto de componentes disponible."""
        paginas = []
        pagina_actual = []
        gastado = 0
        for tipo, data in bloques:
            costo = cls._costo_bloque(tipo, data)
            if costo > presupuesto:
                # bloque tan pesado que no cabe ni solo — lo mandamos a su propia
                # página igual para no perderlo, en vez de crashear.
                if pagina_actual:
                    paginas.append(pagina_actual)
                    pagina_actual, gastado = [], 0
                paginas.append([(tipo, data)])
                continue
            if gastado + costo > presupuesto:
                paginas.append(pagina_actual)
                pagina_actual, gastado = [], 0
            pagina_actual.append((tipo, data))
            gastado += costo
        if pagina_actual or not paginas:
            paginas.append(pagina_actual)
        return paginas

    def __init__(self, guild_id: int, items, categoria_actual: str = None, pagina: int = 0):
        super().__init__(timeout=180)
        categorias = get_categorias(guild_id)
        container = discord.ui.Container(accent_color=0x2ECC71)
        container.add_item(discord.ui.TextDisplay(
            "**🛒 Tienda**\nPulsa un botón para comprar el item al instante, o usa el comando `!comprar <item>`.\n"
            "Usa `!inventario` para ver lo que ya compraste."
        ))
        presupuesto = MAX_COMPONENTES_TIENDA - 1  # -1 por el Container mismo
        presupuesto -= 1  # el TextDisplay de intro
        if categorias:
            fila_categoria = discord.ui.ActionRow()
            fila_categoria.add_item(CategoriaSelect(guild_id, categorias, categoria_actual))
            container.add_item(fila_categoria)
            presupuesto -= 2  # ActionRow(1) + Select(1)
        presupuesto -= 7  # reservado para la fila de navegación (separator+texto+actionrow+4 botones)
        presupuesto -= 2  # margen de seguridad extra, por si acaso

        if not items:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay("No hay items en esta categoría we."))
            self.add_item(container)
            return

        # Arma una lista de "bloques" (encabezados de categoría + items) para poder
        # meter un separador entre cada uno de forma consistente.
        bloques = []
        categoria_mostrada = None
        for item in items:
            categoria_item = item[-1]
            if categoria_actual is None and categoria_item != categoria_mostrada:
                bloques.append(("header", categoria_item))
                categoria_mostrada = categoria_item
            bloques.append(("item", item))

        paginas = self._construir_paginas(bloques, presupuesto)
        total_paginas = len(paginas)
        pagina = max(0, min(pagina, total_paginas - 1))

        for tipo, data in paginas[pagina]:
            container.add_item(discord.ui.Separator())
            if tipo == "header":
                container.add_item(discord.ui.TextDisplay(f"**📂 {data}**"))
                continue
            _id, nombre, precio, descripcion, usable, imagen, rol_id, dinero_efecto, es_seguro, categoria = data
            etiquetas = []
            if usable:
                etiquetas.append("*Usable con `/useitem`*")
            if rol_id:
                etiquetas.append("🎭 *Da un rol al usarlo*")
            if dinero_efecto > 0:
                etiquetas.append(f"💰 *Da {dinero_efecto:,} al usarlo*")
            elif dinero_efecto < 0:
                etiquetas.append(f"💸 *Cuesta {abs(dinero_efecto):,} extra al usarlo*")
            if es_seguro:
                etiquetas.append("🛡️ *Protege contra `!robar`*")
            etiqueta = ("\n" + "\n".join(etiquetas)) if etiquetas else ""
            texto = f"**{nombre}**\n{descripcion or chr(0x200b)}{etiqueta}"
            boton = ComprarButton(nombre, precio, guild_id)
            # discord.ui.Section SIEMPRE necesita un accessory (Discord lo exige,
            # no es opcional aunque el constructor lo permita) — por eso antes
            # crasheaba la tienda entera cuando un item no tenía imagen. Si hay
            # imagen usamos el Thumbnail como accessory y el botón va abajo en su
            # propia fila; si no hay imagen, el botón mismo hace de accessory.
            if imagen and imagen.strip().lower().startswith(("http://", "https://")):
                section = discord.ui.Section(
                    discord.ui.TextDisplay(texto),
                    accessory=discord.ui.Thumbnail(media=imagen.strip()))
                container.add_item(section)
                fila = discord.ui.ActionRow()
                fila.add_item(boton)
                container.add_item(fila)
            else:
                section = discord.ui.Section(
                    discord.ui.TextDisplay(texto),
                    accessory=boton)
                container.add_item(section)

        if total_paginas > 1:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(f"Página {pagina + 1} de {total_paginas}"))
            fila_nav = discord.ui.ActionRow()
            fila_nav.add_item(TiendaNavButton("⏮️", 0, guild_id, categoria_actual, disabled=(pagina == 0)))
            fila_nav.add_item(TiendaNavButton("◀️", pagina - 1, guild_id, categoria_actual, disabled=(pagina == 0)))
            fila_nav.add_item(TiendaNavButton("▶️", pagina + 1, guild_id, categoria_actual, disabled=(pagina >= total_paginas - 1)))
            fila_nav.add_item(TiendaNavButton("⏭️", total_paginas - 1, guild_id, categoria_actual, disabled=(pagina >= total_paginas - 1)))
            container.add_item(fila_nav)

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

def valor_carta(carta: str, as_alto: bool = False) -> int:
    rango = carta[:-1]
    if rango in ("J", "Q", "K"):
        return 10
    if rango == "A":
        return 10 if as_alto else 1
    return int(rango)

def valor_mano(cartas) -> int:
    """Regla de la casa: el As vale 1, excepto cuando está solo con otra carta
    (mano de exactamente 2 cartas), donde vale 10."""
    as_alto = len(cartas) == 2
    return sum(valor_carta(c, as_alto) for c in cartas)

# ─────────────────────────────────────────
# CARTAS DIBUJADAS (Pillow) — para el blackjack
# ─────────────────────────────────────────
CARTA_ANCHO, CARTA_ALTO = 70, 98
CARTA_GAP = 10
FILA_GAP = 22

_FUENTES_CACHE = {}

def _cargar_fuente(tam: int) -> ImageFont.FreeTypeFont:
    if tam in _FUENTES_CACHE:
        return _FUENTES_CACHE[tam]
    rutas = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]
    fuente = None
    for ruta in rutas:
        try:
            fuente = ImageFont.truetype(ruta, tam)
            break
        except Exception:
            continue
    if fuente is None:
        fuente = ImageFont.load_default()
    _FUENTES_CACHE[tam] = fuente
    return fuente

def _dibujar_carta(carta: str, oculta: bool = False) -> Image.Image:
    img = Image.new("RGBA", (CARTA_ANCHO, CARTA_ALTO), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radio = 10
    if oculta:
        draw.rounded_rectangle([1, 1, CARTA_ANCHO - 2, CARTA_ALTO - 2], radius=radio,
                                fill=(88, 101, 242, 255), outline=(30, 32, 40, 255), width=2)
        draw.rounded_rectangle([9, 9, CARTA_ANCHO - 10, CARTA_ALTO - 10], radius=6,
                                outline=(255, 255, 255, 90), width=2)
        return img

    draw.rounded_rectangle([1, 1, CARTA_ANCHO - 2, CARTA_ALTO - 2], radius=radio,
                            fill=(250, 250, 250, 255), outline=(190, 190, 195, 255), width=2)
    rango, palo = carta[:-1], carta[-1]
    color = (215, 30, 40, 255) if palo in ("♥", "♦") else (25, 25, 30, 255)
    fuente_rango = _cargar_fuente(19)
    fuente_palo_esquina = _cargar_fuente(17)
    fuente_palo_centro = _cargar_fuente(30)

    draw.text((8, 5), rango, font=fuente_rango, fill=color)
    draw.text((8, 25), palo, font=fuente_palo_esquina, fill=color)

    bbox = draw.textbbox((0, 0), palo, font=fuente_palo_centro)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((CARTA_ANCHO - w) / 2 - bbox[0], (CARTA_ALTO - h) / 2 - bbox[1] + 4),
               palo, font=fuente_palo_centro, fill=color)
    return img

def _dibujar_fila(cartas, ocultar_segunda: bool = False) -> Image.Image:
    n = len(cartas)
    fila = Image.new("RGBA", (n * CARTA_ANCHO + (n - 1) * CARTA_GAP, CARTA_ALTO), (0, 0, 0, 0))
    for i, c in enumerate(cartas):
        oculta = ocultar_segunda and i == 1
        carta_img = _dibujar_carta(c, oculta)
        fila.paste(carta_img, (i * (CARTA_ANCHO + CARTA_GAP), 0), carta_img)
    return fila

def generar_imagen_blackjack(mano_jugador, mano_dealer, ocultar_segunda: bool = False) -> discord.File:
    """Genera una sola imagen con la mano del jugador arriba y la del dealer abajo,
    para adjuntar al embed en vez del bloque de texto ANSI."""
    fila_jugador = _dibujar_fila(mano_jugador)
    fila_dealer = _dibujar_fila(mano_dealer, ocultar_segunda)
    ancho = max(fila_jugador.width, fila_dealer.width)
    alto = CARTA_ALTO * 2 + FILA_GAP
    lienzo = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    lienzo.paste(fila_jugador, (0, 0), fila_jugador)
    lienzo.paste(fila_dealer, (0, CARTA_ALTO + FILA_GAP), fila_dealer)
    buf = io.BytesIO()
    lienzo.save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename="blackjack.png")

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

def validar_eleccion_ruleta(texto: str):
    """Valida la elección para /ruleta: acepta rojo/negro/verde o un número del 0 al 36.
    Devuelve None si no es válida, o la elección normalizada (color como string, número como string de dígitos)."""
    texto = texto.strip().lower()
    mapa = mapa_eleccion("ruleta")
    if texto in mapa:
        return mapa[texto]
    if texto.isdigit():
        numero = int(texto)
        if 0 <= numero <= 36:
            return str(numero)
    return None

def texto_eleccion_ruleta(eleccion) -> str:
    """Texto legible de una elección de ruleta, sea color o número."""
    if eleccion is not None and str(eleccion).isdigit():
        return f"número {eleccion}"
    return str(eleccion)

class ApuestaModal(discord.ui.Modal):
    def __init__(self, view: "JoinView"):
        super().__init__(title=f"Unirse a {view.juego_titulo}"[:45])
        self.view_ref = view
        self.apuesta_input = discord.ui.TextInput(label="¿Cuánto quieres apostar?", placeholder="Ej: 100, o 'all' para apostar todo", max_length=10)
        self.add_item(self.apuesta_input)
        self.eleccion_input = None
        if view.necesita_eleccion:
            placeholder = "rojo, negro, verde o un número (0-36)" if view.juego_tipo == "ruleta" else "cara o cruz"
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
            if view.juego_tipo == "ruleta":
                eleccion = validar_eleccion_ruleta(texto)
                if eleccion is None:
                    return await interaction.response.send_message("❌ Elección inválida we (usa rojo/negro/verde o un número del 0 al 36)", ephemeral=True)
            else:
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
            if data.get("eleccion"):
                texto_eleccion = texto_eleccion_ruleta(data["eleccion"]) if view.juego_tipo == "ruleta" else data["eleccion"]
                extra = f" a **{texto_eleccion}**"
            else:
                extra = ""
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
    "repartiste pizza toda la noche y ganaste **{amount}**",
    "le hiciste la tarea a un cabro de colegio y te pagó **{amount}**",
    "vendiste completos en la esquina y sacaste **{amount}**",
    "hiciste de streamer 2 horas y te donaron **{amount}**",
    "ayudaste a mudar un piano y te dieron **{amount}**",
    "cuidaste perros del vecino y ganaste **{amount}**",
]

CRIME_EXITOS = [
    "intentaste clonar una tarjeta y te saliste con la tuya. Ganaste **{amount}**",
    "le robaste el WiFi al vecino y de paso le sacaste **{amount}**",
    "vendiste copias piratas y ganaste **{amount}**",
    "asaltaste un kiosko y te llevaste **{amount}**",
    "hackeaste una cuenta de Netflix y la revendiste por **{amount}**",
]

CRIME_FALLOS = [
    "intentaste clonar una tarjeta pero te pillaron. Perdiste **{amount}**",
    "le robaste el WiFi al vecino pero te cachó y tuviste que pagarle **{amount}**",
    "vendiste copias piratas pero te multaron **{amount}**",
    "asaltaste un kiosko pero te agarró carabineros. Perdiste **{amount}**",
    "hackeaste una cuenta de Netflix pero te rastrearon. Multa de **{amount}**",
]

SLUT_EXITOS = [
    "coqueteaste con alguien en un bar y te invitó unos tragos, terminaste sacándole **{amount}**",
    "vendiste fotos subidas de tono por Internet y ganaste **{amount}**",
    "hiciste de acompañante pagado en una fiesta y te pagaron **{amount}**",
    "le tiraste los perros a alguien con plata y te regaló **{amount}**",
    "trabajaste una noche en un club nocturno y ganaste **{amount}**",
    "conseguiste un sugar daddy/mommy por una noche que te dio **{amount}**",
]

SLUT_FALLOS = [
    "te intentaste ligar a alguien pero resultó ser policía encubierto. Perdiste **{amount}**",
    "te estafaron prometiéndote plata a cambio de nada. Perdiste **{amount}**",
    "te agarraron in fraganti y tuviste que pagar **{amount}** para que no dijeran nada",
    "la persona que ligaste resultó no tener ni un peso y encima te cobró **{amount}** de propina",
    "te cacharon tus papás we, que vergüenza — te descontaron **{amount}** de multa",
]

# tipos válidos: "trabajo", "crime_exito", "crime_fallo", "slut_exito", "slut_fallo"
def get_mensajes_extra(guild_id: int, tipo: str) -> list:
    """Frases personalizadas que el dueño agregó desde el Dashboard, para sumarlas
    (o reemplazar, según el toggle) a las frases por defecto de cada acción."""
    cursor.execute("SELECT texto FROM mensajes_custom WHERE guild_id=%s AND tipo=%s", (guild_id, tipo))
    return [fila[0] for fila in cursor.fetchall()]

def elegir_frase(guild_id: int, tipo: str, defaults: list, monto: int) -> str:
    """Elige una frase (de las por defecto y/o las personalizadas, según el toggle
    'usar_respuestas_default' del Dashboard) y le mete el monto donde diga {amount}."""
    cfg = get_config(guild_id)
    custom = get_mensajes_extra(guild_id, tipo)
    if cfg.get("usar_respuestas_default", 1):
        pool = defaults + custom
    else:
        pool = custom or defaults  # si no hay ninguna personalizada, no lo dejamos sin frase
    plantilla = random.choice(pool)
    monto_str = format_dinero(guild_id, monto)
    if "{amount}" in plantilla:
        return plantilla.replace("{amount}", monto_str)
    return f"{plantilla} — **{monto_str}**"

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
        ganancia = random.randint(cfg["trabajo_min"], cfg["trabajo_max"])
        modificar_balance(ctx.guild.id, ctx.author.id, ganancia)
        set_cooldown(ctx.guild.id, ctx.author.id, "last_trabajo")
        texto = elegir_frase(ctx.guild.id, "trabajo", TRABAJOS, ganancia)
        await send_msg(ctx, f"{ctx.author.mention} {texto}", title="💼 Trabajo")

    @commands.command(name="crime")
    async def crime(self, ctx: commands.Context):
        cfg = get_config(ctx.guild.id)
        restante = tiempo_restante(get_cooldown(ctx.guild.id, ctx.author.id, "last_crime"), cfg["cooldown_crime"])
        if restante:
            return await send_msg(ctx, f"⏳ Todavía te andan buscando we, espera **{formatear_tiempo(restante)}**", title="🕶️ Crime")
        set_cooldown(ctx.guild.id, ctx.author.id, "last_crime")
        if random.random() < cfg["crime_chance"]:
            ganancia = random.randint(cfg["crime_win_min"], cfg["crime_win_max"])
            modificar_balance(ctx.guild.id, ctx.author.id, ganancia)
            texto = elegir_frase(ctx.guild.id, "crime_exito", CRIME_EXITOS, ganancia)
            await send_msg(ctx, f"{ctx.author.mention} {texto}", title="🕶️ Crime")
        else:
            perdida = random.randint(cfg["crime_loss_min"], cfg["crime_loss_max"])
            modificar_balance(ctx.guild.id, ctx.author.id, -perdida)
            texto = elegir_frase(ctx.guild.id, "crime_fallo", CRIME_FALLOS, perdida)
            await send_msg(ctx, f"🚓 {ctx.author.mention} {texto}", title="🕶️ Crime", color=0xE74C3C)

    @commands.command(name="slut")
    async def slut(self, ctx: commands.Context):
        cfg = get_config(ctx.guild.id)
        restante = tiempo_restante(get_cooldown_generic(ctx.guild.id, ctx.author.id, "slut"), cfg["cooldown_slut"])
        if restante:
            return await send_msg(ctx, f"⏳ Espera **{formatear_tiempo(restante)}** para volver a hacerlo we", title="💋 Slut")
        set_cooldown_generic(ctx.guild.id, ctx.author.id, "slut")
        if random.random() < cfg["slut_chance"]:
            ganancia = random.randint(cfg["slut_win_min"], cfg["slut_win_max"])
            modificar_balance(ctx.guild.id, ctx.author.id, ganancia)
            texto = elegir_frase(ctx.guild.id, "slut_exito", SLUT_EXITOS, ganancia)
            await send_msg(ctx, f"{ctx.author.mention} {texto}", title="💋 Slut")
        else:
            perdida = random.randint(cfg["slut_loss_min"], cfg["slut_loss_max"])
            modificar_balance(ctx.guild.id, ctx.author.id, -perdida)
            texto = elegir_frase(ctx.guild.id, "slut_fallo", SLUT_FALLOS, perdida)
            await send_msg(ctx, f"{ctx.author.mention} {texto}", title="💋 Slut", color=0xE74C3C)

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
            if get_seguros(ctx.guild.id, victima.id) > 0:
                consumir_seguro(ctx.guild.id, victima.id)
                await send_msg(ctx,
                    f"{ctx.author.mention} intentó robarle a {victima.mention}, pero su 🛡️ **seguro** lo protegió — "
                    f"no perdió ni un peso y el seguro se gastó",
                    title="🥷 Robar", color=0x3498DB)
            else:
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
    async def leaderboard(self, ctx: commands.Context, flag: str = None):
        flag = (flag or "").lower().lstrip("-")
        if flag in ("money", "cash", "wallet", "cartera"):
            columna, etiqueta, nota = "balance", "cartera", "Solo cartera (sin banco)"
        elif flag in ("bank", "banco"):
            columna, etiqueta, nota = "banco", "banco", "Solo banco (sin cartera)"
        else:
            columna, etiqueta, nota = "balance + banco", "total", "Incluye cartera + banco"
        cursor.execute(f"SELECT user_id, {columna} AS total FROM economia WHERE guild_id=%s ORDER BY total DESC LIMIT 10", (ctx.guild.id,))
        rows = cursor.fetchall()
        if not rows:
            return await send_msg(ctx, "Todavía no hay nadie con plata we", title="🏆 Leaderboard")
        medallas = ["🥇", "🥈", "🥉"]
        embed = discord.Embed(title=f"🏆 Leaderboard — Los más ricos ({etiqueta})", color=0xF1C40F)
        lineas = []
        for i, (uid, total) in enumerate(rows):
            member = ctx.guild.get_member(uid)
            nombre = member.display_name if member else f"ID:{uid}"
            prefijo = medallas[i] if i < 3 else f"**{i+1}.**"
            lineas.append(f"{prefijo} {nombre} — {format_dinero(ctx.guild.id, total)}")
        embed.description = "\n".join(lineas)
        embed.set_footer(text=f"Servidor: {ctx.guild.name} • {nota} • Usa !leaderboard -money o -bank para filtrar")
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
        _id, nombre_real, precio, descripcion, usable, mensaje_uso, imagen, rol_id, dinero_efecto, es_seguro, categoria = item
        saldo = get_balance(ctx.guild.id, ctx.author.id)
        if saldo < precio:
            return await send_msg(ctx, f"No tienes suficiente plata we. Necesitas **{format_dinero(ctx.guild.id, precio)}** y tienes **{format_dinero(ctx.guild.id, saldo)}**", title="🛒 Comprar", color=0xE74C3C)
        modificar_balance(ctx.guild.id, ctx.author.id, -precio)
        add_item(ctx.guild.id, ctx.author.id, nombre_real, 1)
        await send_msg(ctx, f"{ctx.author.mention} compró **{nombre_real}** por **{format_dinero(ctx.guild.id, precio)}**", title="✅ Compra exitosa")

    @commands.command(name="inventario", aliases=["inv"])
    async def inventario_cmd(self, ctx: commands.Context, user: discord.Member = None):
        user = user or ctx.author
        items = get_inventario(ctx.guild.id, user.id)
        if not items:
            return await send_msg(ctx, f"{user.mention} no tiene items we", title="🎒 Inventario")
        embed = discord.Embed(title=f"🎒 Inventario de {user.display_name}", color=0x9B59B6)
        lineas = []
        for item, cantidad in items:
            lineas.append(f"**{item}** x{cantidad}")
        embed.description = "\n".join(lineas)
        await ctx.send(embed=embed)

    @app_commands.command(name="useitem", description="Usa un item de tu inventario")
    @app_commands.describe(nombre="El item que quieres usar")
    async def useitem_slash(self, interaction: discord.Interaction, nombre: str):
        guild_id = interaction.guild.id
        inventario = get_inventario(guild_id, interaction.user.id)
        encontrado = next((it for it in inventario if it[0].lower() == nombre.lower()), None)
        if not encontrado:
            return await interaction.response.send_message(f"No tienes el item `{nombre}` we", ephemeral=True)
        item_nombre, _cantidad = encontrado
        info = get_item_tienda(guild_id, item_nombre)
        usable = info[4] if info else 0
        mensaje_uso = info[5] if info else ""
        rol_id = info[7] if info else 0
        dinero_efecto = info[8] if info else 0
        es_seguro = info[9] if info else 0
        if not usable:
            return await interaction.response.send_message(f"**{item_nombre}** no se puede usar we, es solo de colección", ephemeral=True)
        add_item(guild_id, interaction.user.id, item_nombre, -1)

        efectos = []
        if dinero_efecto:
            modificar_balance(guild_id, interaction.user.id, dinero_efecto)
            if dinero_efecto > 0:
                efectos.append(f"💰 Ganaste **{format_dinero(guild_id, dinero_efecto)}**")
            else:
                efectos.append(f"💸 Perdiste **{format_dinero(guild_id, -dinero_efecto)}**")
        if rol_id:
            rol_obj = interaction.guild.get_role(rol_id)
            if rol_obj:
                try:
                    await interaction.user.add_roles(rol_obj, reason=f"Usó el item {item_nombre}")
                    efectos.append(f"🎭 Obtuviste el rol {rol_obj.mention}")
                except discord.Forbidden:
                    efectos.append("⚠️ No pude darte el rol (me faltan permisos o el rol está por encima del mío)")
            else:
                efectos.append("⚠️ El rol configurado para este item ya no existe")
        if es_seguro:
            add_seguro(guild_id, interaction.user.id, 1)
            efectos.append("🛡️ Activaste un **seguro**: la próxima vez que te roben con éxito, recuperas lo robado")

        texto = mensaje_uso or f"Usaste **{item_nombre}**."
        texto = texto.replace("{user}", interaction.user.mention).replace("{item}", item_nombre)
        if efectos:
            texto += "\n\n" + "\n".join(efectos)
        await interaction.response.send_message(embed=discord.Embed(title="📦 Item usado", description=texto, color=0x9B59B6))

    @useitem_slash.autocomplete("nombre")
    async def useitem_autocomplete(self, interaction: discord.Interaction, current: str):
        if not interaction.guild:
            return []
        inventario = get_inventario(interaction.guild.id, interaction.user.id)
        current = current.lower()
        return [app_commands.Choice(name=f"{nombre} x{cantidad}", value=nombre)
                for nombre, cantidad in inventario if current in nombre.lower()][:25]

async def autocomplete_items_tienda(interaction: discord.Interaction, current: str):
    if not interaction.guild:
        return []
    items = get_tienda(interaction.guild.id)
    current = current.lower()
    return [app_commands.Choice(name=nombre, value=nombre) for _id, nombre, precio, descripcion, usable, imagen, rol_id, dinero_efecto, es_seguro, categoria in items if current in nombre.lower()][:25]

async def autocomplete_categorias_tienda(interaction: discord.Interaction, current: str):
    """Sugiere categorías ya existentes en el server; si escribes un nombre nuevo,
    también aparece como opción para poder crear la categoría al vuelo."""
    if not interaction.guild:
        return []
    categorias = get_categorias(interaction.guild.id)
    current = current.strip()
    opciones = [c for c in categorias if current.lower() in c.lower()]
    if current and current not in opciones:
        opciones.insert(0, current)
    return [app_commands.Choice(name=c, value=c) for c in opciones][:25]

# ─────────────────────────────────────────
# COG STAFF — plata y tienda (la configuración de economía/casino ahora vive en el Dashboard web)
# ─────────────────────────────────────────
class ConfigCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="datos", description="Descarga un respaldo en JSON de toda la base de datos")
    @is_staff_app()
    async def datos_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tablas = ["economia", "tienda", "inventario", "seguros", "cooldowns", "config"]
        respaldo = {}
        for tabla in tablas:
            cursor.execute(f"SELECT * FROM {tabla}")
            columnas = [d[0] for d in cursor.description]
            respaldo[tabla] = [dict(zip(columnas, fila)) for fila in cursor.fetchall()]
        contenido = json.dumps(respaldo, indent=2, ensure_ascii=False, default=str)
        buffer = io.BytesIO(contenido.encode("utf-8"))
        nombre = f"teto_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        archivo = discord.File(buffer, filename=nombre)
        await interaction.followup.send(
            content="📦 Acá está el respaldo en JSON de todas las tablas. La base de datos vive en Neon (Postgres), "
                    "así que para cambiar de host solo necesitas la misma variable `DATABASE_URL` — este archivo es "
                    "solo un backup manual, no hace falta para migrar.",
            file=archivo, ephemeral=True)

    # ── PLATA (STAFF) ──
    @app_commands.command(name="add-money", description="Agrega plata a un usuario")
    @app_commands.describe(user="Usuario a modificar", cantidad="Cuánto agregar")
    @is_staff_economia_app()
    async def add_money_slash(self, interaction: discord.Interaction, user: discord.Member, cantidad: int):
        if cantidad <= 0:
            return await interaction.response.send_message("❌ La cantidad tiene que ser mayor a 0 we", ephemeral=True)
        modificar_balance(interaction.guild.id, user.id, cantidad)
        nuevo = get_balance(interaction.guild.id, user.id)
        await interaction.response.send_message(
            f"✅ Se le agregaron **{format_dinero(interaction.guild.id, cantidad)}** a {user.mention}. Nuevo balance: **{format_dinero(interaction.guild.id, nuevo)}**")

    @app_commands.command(name="remove-money", description="Quita plata a un usuario")
    @app_commands.describe(user="Usuario a modificar", cantidad="Cuánto quitar")
    @is_staff_economia_app()
    async def remove_money_slash(self, interaction: discord.Interaction, user: discord.Member, cantidad: int):
        if cantidad <= 0:
            return await interaction.response.send_message("❌ La cantidad tiene que ser mayor a 0 we", ephemeral=True)
        cartera = get_balance(interaction.guild.id, user.id)
        if cantidad <= cartera:
            modificar_balance(interaction.guild.id, user.id, -cantidad)
        else:
            restante = cantidad - cartera
            modificar_balance(interaction.guild.id, user.id, -cartera)
            modificar_banco(interaction.guild.id, user.id, -restante)
        nueva_cartera = get_balance(interaction.guild.id, user.id)
        nuevo_banco = get_banco(interaction.guild.id, user.id)
        await interaction.response.send_message(
            f"✅ Se le quitaron **{format_dinero(interaction.guild.id, cantidad)}** a {user.mention}. "
            f"👛 Cartera: **{format_dinero(interaction.guild.id, nueva_cartera)}** · 🏦 Banco: **{format_dinero(interaction.guild.id, nuevo_banco)}**")

    # ── TIENDA (STAFF) ──
    @app_commands.command(name="additem", description="Agrega un item a la tienda")
    @app_commands.describe(nombre="Nombre del item", precio="Precio del item", descripcion="Descripción (opcional)",
                           categoria="Categoría del item (si no existe, se crea sola; por defecto 'General')",
                           imagen="URL de una imagen para la miniatura del item (opcional)",
                           imagen_archivo="O sube directamente una imagen desde tus archivos (opcional)")
    @is_staff_tienda_app()
    async def additem_slash(self, interaction: discord.Interaction, nombre: str, precio: int, descripcion: str = "",
                             categoria: str = "General", imagen: str = "", imagen_archivo: discord.Attachment = None):
        if precio <= 0:
            return await interaction.response.send_message("❌ El precio tiene que ser mayor a 0 we", ephemeral=True)
        existente = get_item_tienda(interaction.guild.id, nombre)
        if existente:
            return await interaction.response.send_message(f"❌ Ya existe un item llamado `{nombre}` we, usa `/delitem` primero si quieres reemplazarlo", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        if imagen_archivo:
            url_subida = await subir_imagen(imagen_archivo)
            if url_subida:
                imagen = url_subida
            else:
                return await interaction.followup.send(
                    "❌ No pude subir esa imagen (¿es un archivo de imagen válido? ¿está configurado `IMGBB_API_KEY`?) we", ephemeral=True)
        categoria = categoria.strip() or "General"
        cursor.execute("INSERT INTO tienda (guild_id, nombre, precio, descripcion, usable, mensaje_uso, imagen, categoria) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                       (interaction.guild.id, nombre, precio, descripcion, 0, "", imagen, categoria))
        db.commit()
        await interaction.followup.send(
            f"✅ Agregado **{nombre}** a la tienda por **{format_dinero(interaction.guild.id, precio)}** en la categoría **{categoria}**\n"
            f"Usa `/edititem` con campo `usable`, `imagen`, `rol`, `dinero`, `seguro` o `categoria` para personalizarlo.")

    @additem_slash.autocomplete("categoria")
    async def additem_categoria_autocomplete(self, interaction: discord.Interaction, current: str):
        return await autocomplete_categorias_tienda(interaction, current)

    @app_commands.command(name="edititem", description="Edita un item de la tienda")
    @app_commands.describe(nombre="Item a editar", campo="Qué campo cambiar",
                           valor="Nuevo valor (rol: menciona o pega el ID del rol · dinero: número, puede ser negativo · seguro: si/no). No hace falta si subes imagen_archivo",
                           imagen_archivo="Para cambiar la imagen subiendo un archivo en vez de pegar un URL (ignora 'campo'/'valor')")
    @app_commands.choices(campo=[
        app_commands.Choice(name="nombre", value="nombre"),
        app_commands.Choice(name="precio", value="precio"),
        app_commands.Choice(name="descripcion", value="descripcion"),
        app_commands.Choice(name="categoria", value="categoria"),
        app_commands.Choice(name="usable", value="usable"),
        app_commands.Choice(name="mensaje", value="mensaje"),
        app_commands.Choice(name="imagen", value="imagen"),
        app_commands.Choice(name="rol", value="rol"),
        app_commands.Choice(name="dinero", value="dinero"),
        app_commands.Choice(name="seguro", value="seguro"),
    ])
    @is_staff_tienda_app()
    async def edititem_slash(self, interaction: discord.Interaction, nombre: str,
                              campo: app_commands.Choice[str] = None, valor: str = "",
                              imagen_archivo: discord.Attachment = None):
        item = get_item_tienda(interaction.guild.id, nombre)
        if not item:
            return await interaction.response.send_message(f"❌ No existe el item `{nombre}` we", ephemeral=True)
        _id = item[0]

        # Si suben un archivo de imagen, eso manda por sobre campo/valor.
        if imagen_archivo:
            await interaction.response.defer(ephemeral=True)
            url_subida = await subir_imagen(imagen_archivo)
            if not url_subida:
                return await interaction.followup.send(
                    "❌ No pude subir esa imagen (¿es un archivo de imagen válido? ¿está configurado `IMGBB_API_KEY`?) we", ephemeral=True)
            cursor.execute("UPDATE tienda SET imagen=%s WHERE id=%s", (url_subida, _id))
            db.commit()
            return await interaction.followup.send(f"✅ Imagen de **{nombre}** actualizada we")

        if campo is None:
            return await interaction.response.send_message(
                "❌ Elige un `campo` para editar, o sube `imagen_archivo` directamente we", ephemeral=True)

        campo_val = campo.value
        if campo_val == "nombre":
            cursor.execute("UPDATE tienda SET nombre=%s WHERE id=%s", (valor, _id))
        elif campo_val == "precio":
            try:
                precio = int(valor)
            except ValueError:
                return await interaction.response.send_message("❌ El precio tiene que ser un número we", ephemeral=True)
            if precio <= 0:
                return await interaction.response.send_message("❌ El precio tiene que ser mayor a 0 we", ephemeral=True)
            cursor.execute("UPDATE tienda SET precio=%s WHERE id=%s", (precio, _id))
        elif campo_val == "descripcion":
            cursor.execute("UPDATE tienda SET descripcion=%s WHERE id=%s", (valor, _id))
        elif campo_val == "categoria":
            nueva_categoria = valor.strip() or "General"
            cursor.execute("UPDATE tienda SET categoria=%s WHERE id=%s", (nueva_categoria, _id))
        elif campo_val == "usable":
            usable = 1 if valor.lower() in ("si", "sí", "true", "1", "yes") else 0
            cursor.execute("UPDATE tienda SET usable=%s WHERE id=%s", (usable, _id))
        elif campo_val == "mensaje":
            cursor.execute("UPDATE tienda SET mensaje_uso=%s WHERE id=%s", (valor, _id))
        elif campo_val == "imagen":
            cursor.execute("UPDATE tienda SET imagen=%s WHERE id=%s", (valor, _id))
        elif campo_val == "rol":
            if valor.lower() in ("no", "ninguno", "quitar", "0"):
                cursor.execute("UPDATE tienda SET rol_id=0 WHERE id=%s", (_id,))
            else:
                match = re.search(r"(\d{15,25})", valor)
                if not match:
                    return await interaction.response.send_message(
                        "❌ No reconocí ese rol we. Menciona el rol (@rol) o pega su ID", ephemeral=True)
                rol_id_val = int(match.group(1))
                if not interaction.guild.get_role(rol_id_val):
                    return await interaction.response.send_message("❌ Ese rol no existe en este servidor we", ephemeral=True)
                cursor.execute("UPDATE tienda SET rol_id=%s WHERE id=%s", (rol_id_val, _id))
        elif campo_val == "dinero":
            try:
                dinero_val = int(valor)
            except ValueError:
                return await interaction.response.send_message("❌ El valor de `dinero` tiene que ser un número (puede ser negativo) we", ephemeral=True)
            cursor.execute("UPDATE tienda SET dinero_efecto=%s WHERE id=%s", (dinero_val, _id))
        elif campo_val == "seguro":
            es_seguro_val = 1 if valor.lower() in ("si", "sí", "true", "1", "yes") else 0
            cursor.execute("UPDATE tienda SET es_seguro=%s WHERE id=%s", (es_seguro_val, _id))
        db.commit()
        await interaction.response.send_message(f"✅ El item **{nombre}** fue actualizado (`{campo_val}` → `{valor}`)")

    @edititem_slash.autocomplete("nombre")
    async def edititem_autocomplete(self, interaction: discord.Interaction, current: str):
        return await autocomplete_items_tienda(interaction, current)

    @edititem_slash.autocomplete("valor")
    async def edititem_valor_autocomplete(self, interaction: discord.Interaction, current: str):
        # Solo sugiere algo especial cuando el campo elegido es "categoria"
        campo_actual = getattr(interaction.namespace, "campo", None)
        if campo_actual == "categoria":
            return await autocomplete_categorias_tienda(interaction, current)
        return []

    @app_commands.command(name="delitem", description="Elimina un item de la tienda")
    @app_commands.describe(nombre="Item a eliminar")
    @is_staff_tienda_app()
    async def delitem_slash(self, interaction: discord.Interaction, nombre: str):
        item = get_item_tienda(interaction.guild.id, nombre)
        if not item:
            return await interaction.response.send_message(f"❌ No existe el item `{nombre}` we", ephemeral=True)
        cursor.execute("DELETE FROM tienda WHERE id=%s", (item[0],))
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
    @app_commands.describe(
        apuesta="Cuánto quieres apostar (o escribe 'all' para apostar todo)",
        color="rojo, negro o verde (opcional si apuestas a un número)",
        numero="Un número del 0 al 36 (opcional si apuestas a un color)",
    )
    @app_commands.choices(color=[
        app_commands.Choice(name="Rojo", value="rojo"),
        app_commands.Choice(name="Negro", value="negro"),
        app_commands.Choice(name="Verde", value="verde"),
    ])
    async def ruleta_slash(self, interaction: discord.Interaction, apuesta: str, color: app_commands.Choice[str] = None, numero: app_commands.Range[int, 0, 36] = None):
        if color is None and numero is None:
            return await interaction.response.send_message("❌ Tienes que elegir un color (`rojo`/`negro`/`verde`) o un número del 0 al 36", ephemeral=True)
        if color is not None and numero is not None:
            return await interaction.response.send_message("❌ Elige un color **o** un número, no ambos we", ephemeral=True)
        eleccion = str(numero) if numero is not None else color.value
        cfg, ok, apuesta = await self._chequeo_inicial(interaction, "ruleta", apuesta)
        if not ok:
            return
        guild_id = interaction.guild.id
        view = JoinView(guild_id, "ruleta", "🎡 Ruleta", necesita_eleccion=True)
        view.participantes[interaction.user.id] = {"member": interaction.user, "apuesta": apuesta, "eleccion": eleccion}
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
            eleccion = data["eleccion"]
            gano = False
            multi = 0
            if str(eleccion).isdigit():
                if int(eleccion) == numero:
                    gano = True
                    multi = cfg["ruleta_multi_numero"]
            elif eleccion == resultado_color:
                gano = True
                multi = cfg["ruleta_multi_verde"] if resultado_color == "verde" else cfg["ruleta_multi_color"]
            texto_eleccion = texto_eleccion_ruleta(eleccion)
            if gano:
                ganancia = int(data["apuesta"] * multi)
                modificar_balance(guild_id, uid, ganancia)
                lineas_final.append(f"{data['member'].mention} apostó a **{texto_eleccion}** 🎉 +{format_dinero(guild_id, ganancia)}")
            else:
                lineas_final.append(f"{data['member'].mention} apostó a **{texto_eleccion}** 💸 -{format_dinero(guild_id, data['apuesta'])}")
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
    @app_commands.command(name="blackjack", description="Blackjack — juega tú solo contra el dealer")
    @app_commands.describe(apuesta="Cuánto quieres apostar (o escribe 'all' para apostar todo)")
    async def blackjack_slash(self, interaction: discord.Interaction, apuesta: str):
        cfg, ok, apuesta = await self._chequeo_inicial(interaction, "blackjack", apuesta)
        if not ok:
            return
        guild_id = interaction.guild.id
        modificar_balance(guild_id, interaction.user.id, -apuesta)
        set_cooldown_generic(guild_id, interaction.user.id, "blackjack")
        data = {"member": interaction.user, "apuesta": apuesta}

        mazo = crear_mazo()
        dealer = [mazo.pop(), mazo.pop()]
        mano = [mazo.pop(), mazo.pop()]

        await interaction.response.send_message(embed=discord.Embed(title="🃏 Blackjack", description="Repartiendo cartas...", color=0x3498DB))
        msg = await interaction.original_response()
        await self._resolver_blackjack(guild_id, msg, interaction.user, mano, mazo, dealer, data)

    async def _resolver_blackjack(self, guild_id, msg, member, mano, mazo, dealer, data):
        resultado_natural = None
        if valor_mano(mano) == 21:
            resultado_natural = "blackjack"
        else:
            turno = TurnoBlackjackView(member.id, mano, mazo, dealer, guild_id, data)
            emb, archivo = turno.construir_embed(member)
            try:
                await msg.edit(embed=emb, view=turno, attachments=[archivo])
            except discord.HTTPException:
                pass
            try:
                await asyncio.wait_for(turno.evento.wait(), timeout=35)
            except asyncio.TimeoutError:
                pass
            if valor_mano(mano) > 21:
                resultado_natural = "bust"

        # el dealer juega al final
        while valor_mano(dealer) < 17:
            dealer.append(mazo.pop())
        valor_dealer = valor_mano(dealer)
        valor_jugador = valor_mano(mano)
        apuesta = data["apuesta"]

        if resultado_natural == "blackjack":
            if valor_dealer == 21 and len(dealer) == 2:
                modificar_balance(guild_id, member.id, apuesta)
                texto = "🤝 Empate (Blackjack). Recuperas tu apuesta"
            else:
                ganancia = int(apuesta * 2.5)
                modificar_balance(guild_id, member.id, ganancia)
                texto = f"🂡 ¡Blackjack! +{format_dinero(guild_id, ganancia)}"
        elif valor_jugador > 21:
            texto = f"💥 Bust -{format_dinero(guild_id, apuesta)}"
        elif valor_dealer > 21 or valor_jugador > valor_dealer:
            ganancia = apuesta * 2
            modificar_balance(guild_id, member.id, ganancia)
            texto = f"🎉 ¡Ganaste! +{format_dinero(guild_id, ganancia)}"
        elif valor_jugador == valor_dealer:
            modificar_balance(guild_id, member.id, apuesta)
            texto = "🤝 Empate. Recuperas tu apuesta"
        else:
            texto = f"😢 Perdiste -{format_dinero(guild_id, apuesta)}"

        lineas_final = [
            f"**{member.display_name}** — Result: {texto}",
            "",
            f"**Your Hand** — Value: {valor_jugador}",
            f"**Dealer Hand** — Value: {valor_dealer}",
        ]
        archivo_final = generar_imagen_blackjack(mano, dealer, ocultar_segunda=False)
        emb_final = discord.Embed(title="🃏 Resultado final", description="\n".join(lineas_final), color=0x2ECC71)
        emb_final.set_image(url=f"attachment://{archivo_final.filename}")
        await msg.edit(embed=emb_final, view=None, attachments=[archivo_final])

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
    """Turno individual de un jugador contra el dealer (compartido entre todos los participantes).
    El look está inspirado en el blackjack de UnbelievaBoat: mano propia, mano del dealer con la
    segunda carta oculta, valores, cartas restantes y botones Hit / Stand / Double Down."""
    def __init__(self, participante_id: int, mano, mazo, dealer, guild_id: int, data: dict):
        super().__init__(timeout=30)
        self.participante_id = participante_id
        self.mano = mano
        self.mazo = mazo
        self.dealer = dealer
        self.guild_id = guild_id
        self.data = data  # dict compartido de la ronda: {"member":..., "apuesta":..., ...}
        self.terminado = False
        self.evento = asyncio.Event()
        # Si no le alcanza la plata para doblar, deshabilita el botón desde el arranque
        if get_balance(guild_id, participante_id) < data["apuesta"]:
            self._deshabilitar_doblar()

    def _deshabilitar_doblar(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label == "Double Down":
                child.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.participante_id:
            await interaction.response.send_message("No es tu turno we", ephemeral=True)
            return False
        return True

    def construir_embed(self, member: discord.Member, resultado: str = None):
        """Devuelve (embed, discord.File) — el embed trae la imagen de las cartas adjunta."""
        color = 0x3498DB
        if resultado in ("bust", "doblada_bust"):
            color = 0xE74C3C
        elif resultado in ("stand", "doblada"):
            color = 0x95A5A6

        emb = discord.Embed(color=color)
        emb.set_author(name=member.display_name, icon_url=member.display_avatar.url)

        desc = ""
        if resultado == "bust":
            desc += f"Result: **Bust** 💥 -{format_dinero(self.guild_id, self.data['apuesta'])}\n\n"
        elif resultado == "doblada_bust":
            desc += f"Result: **Bust (doblada)** 💥 -{format_dinero(self.guild_id, self.data['apuesta'])}\n\n"
        elif resultado == "stand":
            desc += "✋ Se plantó\n\n"
        elif resultado == "doblada":
            desc += f"💰 Dobló su apuesta a {format_dinero(self.guild_id, self.data['apuesta'])}\n\n"

        desc += f"**Your Hand** — Value: {valor_mano(self.mano)}\n\n"

        oculto = resultado is None
        valor_dealer_mostrado = valor_mano([self.dealer[0]]) if oculto else valor_mano(self.dealer)
        desc += f"**Dealer Hand** — Value: {valor_dealer_mostrado}\n\n"

        desc += f"Cards remaining: {len(self.mazo)}"
        emb.description = desc
        archivo = generar_imagen_blackjack(self.mano, self.dealer, ocultar_segunda=oculto)
        emb.set_image(url=f"attachment://{archivo.filename}")
        return emb, archivo

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.terminado:
            return
        self.mano.append(self.mazo.pop())
        self._deshabilitar_doblar()  # solo se puede doblar en la primera decisión
        if valor_mano(self.mano) > 21:
            self.terminado = True
            for item in self.children:
                item.disabled = True
            emb, archivo = self.construir_embed(interaction.user, resultado="bust")
            await interaction.response.edit_message(embed=emb, view=self, attachments=[archivo])
            self.evento.set()
        else:
            emb, archivo = self.construir_embed(interaction.user, resultado=None)
            await interaction.response.edit_message(embed=emb, view=self, attachments=[archivo])

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.success, emoji="✋")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.terminado:
            return
        self.terminado = True
        for item in self.children:
            item.disabled = True
        emb, archivo = self.construir_embed(interaction.user, resultado="stand")
        await interaction.response.edit_message(embed=emb, view=self, attachments=[archivo])
        self.evento.set()

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.secondary, emoji="💵")
    async def doblar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.terminado:
            return
        extra = self.data["apuesta"]
        if get_balance(self.guild_id, self.participante_id) < extra:
            return await interaction.response.send_message("No te alcanza la plata para doblar we", ephemeral=True)
        modificar_balance(self.guild_id, self.participante_id, -extra)
        self.data["apuesta"] *= 2
        self.mano.append(self.mazo.pop())
        self.terminado = True
        for item in self.children:
            item.disabled = True
        resultado = "doblada_bust" if valor_mano(self.mano) > 21 else "doblada"
        emb, archivo = self.construir_embed(interaction.user, resultado=resultado)
        await interaction.response.edit_message(embed=emb, view=self, attachments=[archivo])
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
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        es_owner = member is not None and es_staff_member(member)
        tiene_tienda = member is not None and puede_tienda_member(member)
        tiene_economia = member is not None and puede_economia_member(member)
        embed = discord.Embed(title="📖 Comandos de Teto", color=0x3498DB,
                              description="Bot de economía. La economía usa `!`, la configuración y los juegos usan `/`.")
        embed.set_thumbnail(url=interaction.guild.me.display_avatar.url)

        embed.add_field(name="💰 Economía", value=(
            "`!balance` — Ve tu plata (cartera + banco)\n"
            "`!trabajo` — Trabaja y gana plata\n"
            "`!crime` — Arriésgate a ganar o perder plata\n"
            "`!slut` — Arriésgate a ganar o perder plata (a lo picante 😏)\n"
            "`!robar @user` — Intenta robarle a alguien (solo puede robarte lo que tienes en la cartera, no lo del banco)\n"
            "`!dar @user <cantidad|all>` — Regala plata a otro\n"
            "`!leaderboard` — Ranking de los más ricos (usa `!leaderboard -money` para solo cartera, o `!leaderboard -bank` para solo banco)"
        ), inline=False)
        embed.add_field(name="🏦 Banco", value=(
            "`!deposit <cantidad|all>` — Guarda plata en el banco, ahí nadie te la puede robar\n"
            "`!retirar <cantidad|all>` — Saca plata del banco a tu cartera"
        ), inline=False)
        embed.add_field(name="🛒 Tienda", value=(
            "`!tienda` — Ve los items disponibles, con un selector para filtrar por categoría "
            "y botones para comprar al toque\n"
            "`!comprar <item>` — Compra un item (alternativa por texto)\n"
            "`!inventario` — Ve tu inventario\n"
            "`/useitem <item>` — Usa un item de tu inventario"
        ), inline=False)
        embed.add_field(name="🎰 Casino", value=(
            "`/slots <apuesta>` — Tragamonedas\n"
            "`/ruleta <apuesta> <color o número>` — Ruleta, apuesta a rojo/negro/verde o a un número exacto (0-36, paga más)\n"
            "`/coinflip <apuesta> <lado>` — Cara o cruz\n"
            "En todos puedes escribir `all` en vez de un número para apostar toda tu plata.\n"
            "Slots, ruleta y coinflip tienen un botón **Unirse 🎟️** para sumar más jugadores; la ronda dura entre 20 y 30s según cuánta gente participe.\n\n"
            "`/blackjack <apuesta>` — Blackjack en solitario, solo tú contra el dealer\n\n"
            f"`/ruletarusa` — Juego de eliminación, sin apuesta. Mínimo {RUSA_MIN_JUGADORES} jugadores; "
            f"cada {RUSA_INTERVALO}s se elimina a alguien al azar y pierde el {int(RUSA_PORCENTAJE*100)}% de su plata. "
            "El último que quede se gana todo lo perdido por los demás."
        ), inline=False)
        if tiene_economia:
            embed.add_field(name="💰 Economía Staff 🔒", value=(
                "`/add-money <user> <cantidad>` — Agrega plata a un usuario\n"
                "`/remove-money <user> <cantidad>` — Quita plata a un usuario"
            ), inline=False)
        if tiene_tienda:
            embed.add_field(name="🛒 Tienda Staff 🔒", value=(
                "`/additem <nombre> <precio> <descripcion> <categoria> <imagen>` — Agrega un item a la tienda "
                "(si la categoría no existe, se crea sola; por defecto es `General`)\n"
                "`/edititem <nombre> <campo> <valor>` — Edita nombre/precio/descripción/categoria/usable/mensaje/imagen/rol/dinero/seguro\n"
                "   • `categoria`: mueve el item a otra categoría (se crea si no existe)\n"
                "   • `rol`: al usar el item, da ese rol (menciona el rol o pega su ID; `no` para quitarlo)\n"
                "   • `dinero`: al usar el item, suma o resta esa plata (puede ser negativo)\n"
                "   • `seguro`: `si`/`no` — si te roban con éxito, recuperas lo robado (se gasta 1 uso)\n"
                "`/delitem <nombre>` — Elimina un item de la tienda"
            ), inline=False)
        if es_owner:
            embed.add_field(name="⚙️ Configuración Staff 🔒", value=(
                "La configuración de economía y casino (cooldowns, ganancias, apuestas, moneda, etc.), y quién más "
                "tiene acceso a los comandos de arriba, ahora se edita desde el **Dashboard web**, no con comandos.\n"
                "`/datos` — Te manda un respaldo en JSON de toda la base de datos (backup manual)"
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
