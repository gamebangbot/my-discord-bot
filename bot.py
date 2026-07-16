from discord.app_commands.checks import bot_has_permissions
import discord
from discord.ext import commands
from discord import app_commands
import asyncpg
import asyncio
import random
import datetime
import os
import re
import json
import math

import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# Render의 포트 감시를 통과하기 위한 초간단 가짜 웹서버 실행
class MyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), MyHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
#  전역 상수 & 변수
# ══════════════════════════════════════════════════════════════════════════════
DAILY_REWARD = 10000
LOTTERY_COST = 5000
MADE_BY_TAG  = ""          # on_ready 에서 봇 소유자 이름으로 채워짐
BOT_OWNER_ID: int | None = None  # on_ready 에서 bot.owner_id 로 채워짐

admin_mode_users: set[str] = set()
bot_ops_cache:    set[str] = set()   # !op 로 추가된 유저 (DB 동기화)
bot_banned_cache: set[str] = set()   # !봇벤 된 유저 (DB 동기화)
maintenance_mode: bool     = False   # !봇점검 on/off

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

STREAK_BONUS = {7: 50000, 14: 100000, 30: 300000}

# ══════════════════════════════════════════════════════════════════════════════
#  Bot 클래스
# ══════════════════════════════════════════════════════════════════════════════
class Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=intents)

    async def setup_hook(self):
        global BOT_OWNER_ID
        database_url = os.environ["DATABASE_URL"]
        self.db = await asyncpg.create_pool(database_url)
        async with self.db.acquire() as conn:
            # ── 기존 테이블 ──
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    balance BIGINT NOT NULL DEFAULT 0
                )""")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS attendance (
                    id TEXT PRIMARY KEY,
                    last_date TEXT NOT NULL,
                    streak INTEGER NOT NULL DEFAULT 1
                )""")
            await conn.execute("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS streak INTEGER NOT NULL DEFAULT 1")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    amount BIGINT NOT NULL,
                    detail TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )""")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS warnings (
                    id TEXT PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0
                )""")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS titles (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    color TEXT,
                    is_event BOOLEAN DEFAULT FALSE,
                    granted_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, title)
                )""")
            await conn.execute("ALTER TABLE titles ADD COLUMN IF NOT EXISTS color TEXT")
            await conn.execute("ALTER TABLE titles ADD COLUMN IF NOT EXISTS is_event BOOLEAN DEFAULT FALSE")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS equipped_title (
                    user_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL
                )""")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )""")
            # ── 광질 테이블 ──
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS mining_users (
                    id TEXT PRIMARY KEY,
                    pickaxe_grade TEXT,
                    pickaxe_name  TEXT,
                    pickaxe_broken BOOLEAN DEFAULT FALSE,
                    bag_type  TEXT DEFAULT '일반가방',
                    total_mined   INTEGER DEFAULT 0,
                    total_sold    INTEGER DEFAULT 0,
                    mining_speed  FLOAT   DEFAULT 1.0
                )""")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS mining_inventory (
                    id SERIAL PRIMARY KEY,
                    user_id  TEXT NOT NULL,
                    ore_name TEXT NOT NULL,
                    ore_grade TEXT NOT NULL,
                    weight   INTEGER NOT NULL,
                    value    BIGINT  NOT NULL
                )""")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS mining_discovered_ores (
                    user_id  TEXT NOT NULL,
                    ore_name TEXT NOT NULL,
                    PRIMARY KEY (user_id, ore_name)
                )""")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS mining_discovered_picks (
                    user_id   TEXT NOT NULL,
                    pick_name TEXT NOT NULL,
                    PRIMARY KEY (user_id, pick_name)
                )""")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS mining_config (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )""")
            # ── 신규 테이블 ──
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_bans (
                    user_id TEXT PRIMARY KEY,
                    reason TEXT,
                    banned_at TIMESTAMP DEFAULT NOW()
                )""")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_ops (
                    user_id TEXT PRIMARY KEY,
                    granted_at TIMESTAMP DEFAULT NOW()
                )""")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS mining_logs (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    log_type TEXT NOT NULL,
                    detail TEXT,
                    pickaxe_grade TEXT,
                    pickaxe_name TEXT,
                    pickaxe_broken BOOLEAN,
                    bag_type TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )""")
            # ── 티켓 시스템 테이블 ──
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ticket_panels (
                    message_id BIGINT PRIMARY KEY,
                    channel_id BIGINT NOT NULL,
                    guild_id BIGINT NOT NULL,
                    staff_id BIGINT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )""")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS active_tickets (
                    channel_id BIGINT PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    opener_id BIGINT NOT NULL,
                    staff_id BIGINT NOT NULL,
                    panel_message_id BIGINT,
                    created_at TIMESTAMP DEFAULT NOW()
                )""")
        # 캐시 로드
        rows = await self.db.fetch("SELECT user_id FROM bot_bans")
        bot_banned_cache.update(r['user_id'] for r in rows)
        rows = await self.db.fetch("SELECT user_id FROM bot_ops")
        bot_ops_cache.update(r['user_id'] for r in rows)
        maint_row = await self.db.fetchrow("SELECT value FROM bot_config WHERE key='maintenance'")
        global maintenance_mode
        maintenance_mode = (maint_row and maint_row['value'] == 'on')

        app_info = await self.application_info()
        BOT_OWNER_ID = app_info.owner.id
        global MADE_BY_TAG
        MADE_BY_TAG = f"made by @{app_info.owner.name}"

        self.add_view(TicketPanelView())
        self.add_view(TicketCloseView())

        print("데이터베이스 연결 완료")
        await self.tree.sync()
        print("슬래시 명령어 동기화 완료")

    async def on_ready(self):
        print(f'{self.user} 봇이 온라인 상태입니다.')

bot = Bot()

# ══════════════════════════════════════════════════════════════════════════════
#  유틸리티 & 핵심 헬퍼
# ══════════════════════════════════════════════════════════════════════════════
async def get_balance(pool, user_id: str) -> int:
    row = await pool.fetchrow("SELECT balance FROM users WHERE id = $1", user_id)
    return row['balance'] if row else 0

async def ensure_user(pool, user_id: str):
    await pool.execute(
        "INSERT INTO users (id, balance) VALUES ($1, 0) ON CONFLICT (id) DO NOTHING", user_id)

async def add_log(user_id: str, type: str, amount: int, detail: str = ""):
    await bot.db.execute(
        "INSERT INTO transactions (user_id, type, amount, detail) VALUES ($1, $2, $3, $4)",
        user_id, type, amount, detail)

async def get_max_bet(key: str) -> int | None:
    row = await bot.db.fetchrow("SELECT value FROM bot_config WHERE key = $1", key)
    return int(row['value']) if row else None

async def set_max_bet(key: str, value: int) -> None:
    await bot.db.execute(
        "INSERT INTO bot_config (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        key, str(value))

async def get_warn_count(db, user_id: str) -> int:
    row = await db.fetchrow("SELECT count FROM warnings WHERE id = $1", user_id)
    return row['count'] if row else 0

def is_owner(uid_or_ctx) -> bool:
    """봇 개발자인지 확인 (ctx 또는 int/str uid)"""
    if BOT_OWNER_ID is None:
        return False
    if hasattr(uid_or_ctx, 'author'):
        return uid_or_ctx.author.id == BOT_OWNER_ID
    return int(uid_or_ctx) == BOT_OWNER_ID

def is_admin_or_op(ctx) -> bool:
    """서버 관리자 OR bot_ops 유저"""
    uid = str(ctx.author.id)
    return ctx.author.guild_permissions.administrator or uid in bot_ops_cache

def admin_check(ctx) -> bool:
    return str(ctx.author.id) in admin_mode_users

async def bot_guard(ctx) -> bool:
    """봇벤 / 점검 중 차단. True = 통과, False = 차단"""
    uid = str(ctx.author.id)
    if uid in bot_banned_cache and not is_owner(ctx):
        await ctx.send("🚫 봇 사용이 제한된 계정이에요.", delete_after=5)
        return False
    if maintenance_mode and not is_owner(ctx) and uid not in bot_ops_cache:
        await ctx.send("🔧 봇이 점검 중이에요. 잠시 후 다시 이용해주세요.", delete_after=5)
        return False
    return True

async def slash_guard(interaction: discord.Interaction) -> bool:
    uid = str(interaction.user.id)
    if uid in bot_banned_cache and not is_owner(uid):
        await interaction.response.send_message("🚫 봇 사용이 제한된 계정이에요.", ephemeral=True)
        return False
    if maintenance_mode and not is_owner(uid) and uid not in bot_ops_cache:
        await interaction.response.send_message("🔧 봇이 점검 중이에요. 잠시 후 다시 이용해주세요.", ephemeral=True)
        return False
    return True

# ══════════════════════════════════════════════════════════════════════════════
#  칭호 시스템
# ══════════════════════════════════════════════════════════════════════════════
TITLE_CONDITIONS = [
    ("💰 만원의 행복",    "잔액 10,000원 이상"),
    ("💵 부자 지망생",    "잔액 100,000원 이상"),
    ("💎 백만장자",       "잔액 1,000,000원 이상"),
    ("👑 재벌",           "잔액 10,000,000원 이상"),
    ("🎁 출석 시작",      "첫 출석 완료"),
    ("🔥 출석왕",         "7일 연속 출석"),
    ("🏆 출석 마스터",    "30일 연속 출석"),
    ("🎰 도박사",         "도박 10회 이상"),
    ("💀 도박 중독",      "도박 50회 이상"),
    ("💲 도박의 전설",    "도박 100회 이상"),
    ("🌙 도박 신",        "도박 200회 이상"),
    ("🃏 블랙잭 고수",    "블랙잭 20승 이상"),
    ("🎡 슬롯머신 폐인",  "슬롯머신 30회 이상"),
    ("📤 송금왕",         "송금 10회 이상"),
    # ── 광질 칭호 ──
    ("⛏️ 광질 입문자",   "첫 번째 광석 채굴"),
    ("💎 광석 수집가",    "광석 100개 채굴"),
    ("🏔️ 광산의 지배자", "광석 500개 채굴"),
    ("🔱 광산왕",         "광석 1,000개 채굴"),
    ("🌟 채굴 마스터",    "광석 3,000개 채굴"),
]

HIDDEN_TITLES = [
    "🦉 올빼미족",
    "음란한🥵",
    "🎉 미친행운",
    "💝 마음이 넓으시네요!",
    "🎖️ 칭호수집가",
    # ── 광질 히든 ──
    "🌌 우주의 목격자",
    "🔮 전설의 채굴사",
    "💀 광질 폐인",
]

async def get_user_titles(db, user_id: str) -> list[str]:
    rows = await db.fetch("SELECT title FROM titles WHERE user_id = $1 ORDER BY is_event, granted_at", user_id)
    return [r['title'] for r in rows]

async def get_user_titles_full(db, user_id: str) -> list[dict]:
    rows = await db.fetch(
        "SELECT title, color, is_event FROM titles WHERE user_id = $1 ORDER BY is_event, granted_at", user_id)
    return [dict(r) for r in rows]

def hex_to_discord_color(hex_color: str) -> discord.Color:
    try:
        h = hex_color.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return discord.Color.from_rgb(r, g, b)
    except Exception:
        return discord.Color.blurple()

def color_to_emoji(hex_color: str) -> str:
    try:
        h = hex_color.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        colors = [
            (255,0,0,"🔴"),(255,127,0,"🟠"),(255,255,0,"🟡"),
            (0,200,0,"🟢"),(0,0,255,"🔵"),(143,0,255,"🟣"),
            (165,42,42,"🟤"),(0,0,0,"⚫"),(255,255,255,"⚪"),
        ]
        best = min(colors, key=lambda c: (r-c[0])**2+(g-c[1])**2+(b-c[2])**2)
        return best[3]
    except Exception:
        return "🏷️"

async def get_equipped_title(db, user_id: str) -> str | None:
    row = await db.fetchrow("SELECT title FROM equipped_title WHERE user_id = $1", user_id)
    return row['title'] if row else None

async def get_equipped_title_full(db, user_id: str) -> tuple[str, str | None] | None:
    row = await db.fetchrow(
        """SELECT t.title, t.color FROM equipped_title e
           JOIN titles t ON t.user_id = e.user_id AND t.title = e.title
           WHERE e.user_id = $1""", user_id)
    return (row['title'], row['color']) if row else None

async def grant_title(db, user_id: str, title: str, color: str | None = None, is_event: bool = False) -> bool:
    try:
        await db.execute(
            "INSERT INTO titles (user_id, title, color, is_event) VALUES ($1, $2, $3, $4)"
            " ON CONFLICT (user_id, title) DO NOTHING",
            user_id, title, color, is_event)
        return True
    except Exception:
        return False

async def check_and_grant_titles(user_id: str) -> tuple[list[str], list[str]]:
    import datetime as _dt
    db = bot.db
    normal_new: list[str] = []
    bal     = await get_balance(db, user_id)
    att     = await db.fetchrow("SELECT streak FROM attendance WHERE id = $1", user_id)
    streak  = att['streak'] if att else 0
    tx2     = await db.fetchrow(
        "SELECT COUNT(*) as cnt FROM transactions WHERE user_id=$1 AND type IN ('도박_승','도박_패')", user_id)
    gamble_total = tx2['cnt'] if tx2 else 0
    tx3     = await db.fetchrow(
        "SELECT COUNT(*) as cnt FROM transactions WHERE user_id=$1 AND type='블랙잭_승'", user_id)
    bj_wins = tx3['cnt'] if tx3 else 0
    tx4     = await db.fetchrow(
        "SELECT COUNT(*) as cnt FROM transactions WHERE user_id=$1 AND type IN ('슬롯_당첨','슬롯_꽝')", user_id)
    slot_total = tx4['cnt'] if tx4 else 0
    tx5     = await db.fetchrow(
        "SELECT COUNT(*) as cnt FROM transactions WHERE user_id=$1 AND type='송금_발신'", user_id)
    send_total = tx5['cnt'] if tx5 else 0
    mu_row  = await db.fetchrow("SELECT total_mined FROM mining_users WHERE id=$1", user_id)
    total_mined = mu_row['total_mined'] if mu_row else 0

    checks = [
        (bal >= 10_000,          "💰 만원의 행복"),
        (bal >= 100_000,         "💵 부자 지망생"),
        (bal >= 1_000_000,       "💎 백만장자"),
        (bal >= 10_000_000,      "👑 재벌"),
        (streak >= 1,            "🎁 출석 시작"),
        (streak >= 7,            "🔥 출석왕"),
        (streak >= 30,           "🏆 출석 마스터"),
        (gamble_total >= 10,     "🎰 도박사"),
        (gamble_total >= 50,     "💀 도박 중독"),
        (gamble_total >= 100,    "💲 도박의 전설"),
        (gamble_total >= 200,    "🌙 도박 신"),
        (bj_wins >= 20,          "🃏 블랙잭 고수"),
        (slot_total >= 30,       "🎡 슬롯머신 폐인"),
        (send_total >= 10,       "📤 송금왕"),
        (total_mined >= 1,       "⛏️ 광질 입문자"),
        (total_mined >= 100,     "💎 광석 수집가"),
        (total_mined >= 500,     "🏔️ 광산의 지배자"),
        (total_mined >= 1000,    "🔱 광산왕"),
        (total_mined >= 3000,    "🌟 채굴 마스터"),
    ]
    existing = set(await get_user_titles(db, user_id))
    for condition, title in checks:
        if condition and title not in existing:
            await grant_title(db, user_id, title)
            normal_new.append(title)
    all_owned = existing | set(normal_new)
    hidden_new: list[str] = []

    # 🦉 올빼미족 — KST 새벽 3~4시
    kst_now = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=9)
    if kst_now.hour in (3, 4) and "🦉 올빼미족" not in all_owned:
        await grant_title(db, user_id, "🦉 올빼미족", "#8B5CF6")
        hidden_new.append("🦉 올빼미족"); all_owned.add("🦉 올빼미족")

    # 음란한🥵 — 잔액 6974원
    if bal == 6974 and "음란한🥵" not in all_owned:
        await grant_title(db, user_id, "음란한🥵", "#FF69B4")
        hidden_new.append("음란한🥵"); all_owned.add("음란한🥵")

    # 🎖️ 칭호수집가
    needed = {t for t, _ in TITLE_CONDITIONS} | {t for t in HIDDEN_TITLES if t != "🎖️ 칭호수집가"}
    if needed.issubset(all_owned) and "🎖️ 칭호수집가" not in all_owned:
        await grant_title(db, user_id, "🎖️ 칭호수집가", "#FFD700")
        hidden_new.append("🎖️ 칭호수집가")

    # 🔮 전설의 채굴사 — 모든 광석 도감 완성
    from_disc = await db.fetchrow(
        "SELECT COUNT(*) as cnt FROM mining_discovered_ores WHERE user_id=$1", user_id)
    disc_cnt = from_disc['cnt'] if from_disc else 0
    if disc_cnt >= len(ALL_ORES) and "🔮 전설의 채굴사" not in all_owned:
        await grant_title(db, user_id, "🔮 전설의 채굴사", "#00BFFF")
        hidden_new.append("🔮 전설의 채굴사"); all_owned.add("🔮 전설의 채굴사")

    # 💀 광질 폐인 — 5000개 이상 채굴 (히든)
    if total_mined >= 5000 and "💀 광질 폐인" not in all_owned:
        await grant_title(db, user_id, "💀 광질 폐인", "#FF0000")
        hidden_new.append("💀 광질 폐인"); all_owned.add("💀 광질 폐인")

    return normal_new, hidden_new

def build_hidden_title_unlock_embed(title: str, user: discord.Member | discord.User) -> discord.Embed:
    embed = discord.Embed(
        title="✨🔮 히든 칭호 해금!! 🔮✨",
        description=(
            f"## 🎊 **{user.display_name}** 님이\n"
            f"# ✦ ❰ {title} ❱ ✦\n"
            f"## 히든 칭호를 획득했습니다!!\n\n"
            "*이 칭호는 특별한 조건을 달성한 사람만 가질 수 있어요!*"
        ),
        color=discord.Color.from_rgb(255, 215, 0)
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="🌟 축하합니다! 🌟")
    return embed

async def build_profile_embed(db, user: discord.Member | discord.User) -> discord.Embed:
    uid = str(user.id)
    bal = await get_balance(db, uid)
    att = await db.fetchrow("SELECT last_date, streak FROM attendance WHERE id = $1", uid)
    warns = await get_warn_count(db, uid)
    tx_count = await db.fetchrow("SELECT COUNT(*) as cnt FROM transactions WHERE user_id = $1", uid)
    equipped_full = await get_equipped_title_full(db, uid)
    all_titles = await get_user_titles(db, uid)
    warn_str = f"{'⚠️ ' * min(warns, 5)}**{warns}회**" if warns > 0 else "없음 ✅"
    if equipped_full:
        eq_name, eq_color = equipped_full
        embed_color = hex_to_discord_color(eq_color) if eq_color else discord.Color.blurple()
        color_dot = color_to_emoji(eq_color) + " " if eq_color else ""
        title_display = f"{color_dot}**{eq_name}**"
        embed_title = f"{color_dot}{eq_name}  👤 {user.display_name}님의 프로필"
    else:
        embed_color = discord.Color.blurple()
        title_display = "없음"
        embed_title = f"👤 {user.display_name}님의 프로필"
    embed = discord.Embed(title=embed_title, color=embed_color)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="💰 잔액", value=f"{bal:,}원", inline=True)
    embed.add_field(name="⚠️ 경고", value=warn_str, inline=True)
    embed.add_field(name="🏷️ 칭호", value=title_display, inline=True)
    if att:
        embed.add_field(name="🎁 출석 스트릭", value=f"{att['streak']}일 연속", inline=True)
        embed.add_field(name="📅 마지막 출석", value=att['last_date'], inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
    else:
        embed.add_field(name="🎁 출석", value="출석 기록 없음", inline=False)
    embed.add_field(name="📋 총 거래", value=f"{tx_count['cnt']}건", inline=True)
    embed.add_field(name="🎖️ 보유 칭호", value=f"{len(all_titles)}개", inline=True)
    return embed

@bot.tree.command(name="내프로필", description="👤 내 프로필을 확인합니다")
async def slash_profile(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    embed = await build_profile_embed(bot.db, interaction.user)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.command(name='내프로필')
async def prefix_profile(ctx):
    if not await bot_guard(ctx): return
    embed = await build_profile_embed(bot.db, ctx.author)
    await ctx.send(embed=embed)

@bot.command(name='지갑')
async def prefix_wallet(ctx):
    if not await bot_guard(ctx): return
    embed = await build_profile_embed(bot.db, ctx.author)
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  출석
# ══════════════════════════════════════════════════════════════════════════════
async def do_attendance(user_id: str, skip_cooldown: bool = False):
    today = datetime.date.today()
    today_str = today.strftime('%Y-%m-%d')
    yesterday_str = (today - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    row = await bot.db.fetchrow("SELECT last_date, streak FROM attendance WHERE id = $1", user_id)
    if not skip_cooldown and row and row['last_date'] == today_str:
        return "⏰ 오늘은 이미 출석 보상을 받으셨어요. 내일 다시 오세요!"
    if row and row['last_date'] == yesterday_str:
        new_streak = row['streak'] + 1
    else:
        new_streak = 1
    bonus = STREAK_BONUS.get(new_streak, 0)
    total_reward = DAILY_REWARD + bonus
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO attendance (id, last_date, streak) VALUES ($1,$2,$3)"
                " ON CONFLICT (id) DO UPDATE SET last_date=$2, streak=$3",
                user_id, today_str, new_streak)
            await conn.execute(
                "INSERT INTO users (id, balance) VALUES ($1,$2) ON CONFLICT (id) DO UPDATE SET balance=users.balance+$2",
                user_id, total_reward)
    await add_log(user_id, "출석", total_reward, f"{new_streak}일 연속" + (f" (보너스 +{bonus:,}원)" if bonus else ""))
    normal_new, hidden_new = await check_and_grant_titles(user_id)
    streak_bar = "🟩" * min(new_streak, 7) + "⬛" * max(0, 7 - new_streak)
    next_milestone = next((d for d in sorted(STREAK_BONUS) if d > new_streak), None)
    if bonus > 0:
        color = discord.Color.gold()
        title = f"🎊 {new_streak}일 연속 출석 보너스!!"
        desc = f"기본 보상 **{DAILY_REWARD:,}원** + 연속 보너스 **{bonus:,}원**\n합계 **{total_reward:,}원** 지급!"
    else:
        color = discord.Color.blue()
        title = "🎁 출석 완료!"
        desc = f"**{DAILY_REWARD:,}원** 지급!"
    embed = discord.Embed(title=title, description=desc, color=color)
    embed.add_field(name=f"연속 출석 {new_streak}일째", value=streak_bar, inline=False)
    if next_milestone:
        days_left = next_milestone - new_streak
        embed.add_field(name="다음 보너스",
            value=f"{next_milestone}일 연속까지 **{days_left}일** 남음 (+{STREAK_BONUS[next_milestone]:,}원)", inline=False)
    embed.set_footer(text=f"날짜: {today_str}")
    if normal_new:
        embed.add_field(name="🎖️ 새 칭호 해금!", value="\n".join(normal_new), inline=False)
    return embed, hidden_new

@bot.tree.command(name="출석", description=f"🎁 매일 출석 보상!")
async def slash_attendance(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    uid = str(interaction.user.id)
    result = await do_attendance(uid, skip_cooldown=uid in admin_mode_users)
    if isinstance(result, str):
        await interaction.followup.send(result)
    else:
        embed, hidden_new = result
        await interaction.followup.send(embed=embed)
        if hidden_new and interaction.channel:
            for ht in hidden_new:
                await interaction.channel.send(embed=build_hidden_title_unlock_embed(ht, interaction.user))

@bot.command(name='출석')
async def prefix_attendance(ctx):
    if not await bot_guard(ctx): return
    uid = str(ctx.author.id)
    result = await do_attendance(uid, skip_cooldown=uid in admin_mode_users)
    if isinstance(result, str):
        await ctx.send(result)
    else:
        embed, hidden_new = result
        await ctx.send(embed=embed)
        for ht in hidden_new:
            await ctx.send(embed=build_hidden_title_unlock_embed(ht, ctx.author))

# ══════════════════════════════════════════════════════════════════════════════
#  송금
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="송금", description="💸 다른 유저에게 돈을 보냅니다")
@app_commands.describe(대상="송금할 유저", 금액="보낼 금액")
async def slash_transfer(interaction: discord.Interaction, 대상: discord.Member, 금액: int):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    if 금액 <= 0:
        return await interaction.followup.send("❌ 0원 이상만 송금 가능해요.")
    if 대상.id == interaction.user.id:
        return await interaction.followup.send("❌ 자기 자신에게는 송금할 수 없어요.")
    sender_id, target_id = str(interaction.user.id), str(대상.id)
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT balance FROM users WHERE id=$1 FOR UPDATE", sender_id)
            if not row or row['balance'] < 금액:
                return await interaction.followup.send("❌ 잔액이 부족해요!")
            await conn.execute("UPDATE users SET balance=balance-$1 WHERE id=$2", 금액, sender_id)
            await conn.execute(
                "INSERT INTO users (id,balance) VALUES($1,$2) ON CONFLICT (id) DO UPDATE SET balance=users.balance+$2",
                target_id, 금액)
    await add_log(sender_id, "송금_발신", -금액, f"→ {대상.display_name} ({target_id})")
    await add_log(target_id, "송금_수신", 금액, f"← {interaction.user.display_name} ({sender_id})")
    embed = discord.Embed(title="✅ 송금 완료", color=discord.Color.green())
    embed.add_field(name="받는 사람", value=대상.display_name, inline=True)
    embed.add_field(name="금액", value=f"{금액:,}원", inline=True)
    await interaction.followup.send(embed=embed)
    if 대상.id == bot.user.id and 금액 >= 1_000_000:
        existing = await get_user_titles(bot.db, sender_id)
        if "💝 마음이 넓으시네요!" not in existing:
            await grant_title(bot.db, sender_id, "💝 마음이 넓으시네요!", "#FF69B4")
            if interaction.channel:
                await interaction.channel.send(embed=build_hidden_title_unlock_embed("💝 마음이 넓으시네요!", interaction.user))
    _, hidden_new = await check_and_grant_titles(sender_id)
    if hidden_new and interaction.channel:
        for ht in hidden_new:
            await interaction.channel.send(embed=build_hidden_title_unlock_embed(ht, interaction.user))

@bot.command(name='송금')
async def prefix_transfer(ctx, target: discord.Member, amount: int):
    if not await bot_guard(ctx): return
    if amount <= 0: return await ctx.send("❌ 0원 이상만 송금 가능해요.")
    if target.id == ctx.author.id: return await ctx.send("❌ 자기 자신에게는 송금할 수 없어요.")
    sender_id, target_id = str(ctx.author.id), str(target.id)
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT balance FROM users WHERE id=$1 FOR UPDATE", sender_id)
            if not row or row['balance'] < amount: return await ctx.send("❌ 잔액이 부족해요!")
            await conn.execute("UPDATE users SET balance=balance-$1 WHERE id=$2", amount, sender_id)
            await conn.execute(
                "INSERT INTO users (id,balance) VALUES($1,$2) ON CONFLICT (id) DO UPDATE SET balance=users.balance+$2",
                target_id, amount)
    await add_log(sender_id, "송금_발신", -amount, f"→ {target.display_name} ({target_id})")
    await add_log(target_id, "송금_수신", amount, f"← {ctx.author.display_name} ({sender_id})")
    embed = discord.Embed(title="✅ 송금 완료", color=discord.Color.green())
    embed.add_field(name="받는 사람", value=target.display_name, inline=True)
    embed.add_field(name="금액", value=f"{amount:,}원", inline=True)
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  도박
# ══════════════════════════════════════════════════════════════════════════════
def get_gamble_result() -> tuple[bool, float]:
    r = random.random()
    if r < 0.25:   return False, 0.0
    elif r < 0.50:
        r2 = random.random()
        if r2 < 0.60: return True, round(random.uniform(2.1, 2.5), 3)
        else:          return True, round(random.uniform(2.6, 3.0), 3)
    elif r < 0.70:
        r2 = random.random()
        if r2 < 0.55: return True, round(random.uniform(1.1, 1.5), 3)
        else:          return True, round(random.uniform(1.6, 2.0), 3)
    else:
        return False, 0.0

def _gamble_anim_params(won: bool, multiplier: float) -> tuple[int, bool]:
    if not won:
        fake = random.random() < 0.25
        if fake:
            fake_tier = random.choices([1,2,3], weights=[50,35,15])[0]
            return fake_tier, True
        return 0, False
    if multiplier >= 2.6:  return 3, False
    elif multiplier >= 2.1: return 2, False
    elif multiplier >= 1.6: return 1, False
    else:                   return 0, False

class GambleView(discord.ui.View):
    def __init__(self, user_id: str, bet: int, won: bool, multiplier: float,
                 anim_tier: int = 0, fake_hype: bool = False):
        super().__init__(timeout=90)
        self.user_id = user_id; self.bet = bet; self.won = won
        self.multiplier = multiplier; self.anim_tier = anim_tier
        self.fake_hype = fake_hype; self.revealed = False

    @discord.ui.button(label="🎲  굴 려 라 !  🎲", style=discord.ButtonStyle.danger)
    async def reveal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.user_id):
            return await interaction.response.send_message("❌ 본인 게임만 확인할 수 있어요!", ephemeral=True)
        if self.revealed:
            return await interaction.response.send_message("⏳ 이미 확인했어요!", ephemeral=True)
        self.revealed = True
        for child in self.children: child.disabled = True

        if self.anim_tier == 3:
            anim_frames = [
                (discord.Embed(title="💥💥💥  주사위가 폭발하려고 해요!!!",
                               description="```\n🚨   이건... 비정상이에요!!\n```", color=discord.Color.red()), 0.6),
                (discord.Embed(title="🚨🚨🚨  말도 안 돼!!! 이런 게 가능해?!",
                               description="```\n👑   전설의 영역에 진입 중...\n```", color=discord.Color.from_rgb(255,165,0)), 0.9),
                (discord.Embed(title="👑💎✨  전설이 탄생하는 순간...!!!",
                               description="```\n🌟   역사에 기록될 순간!!!\n```", color=discord.Color.from_rgb(255,215,0)), 1.6),
            ]
        elif self.anim_tier == 2:
            anim_frames = [
                (discord.Embed(title="💥  주사위가 심상치 않아요!!",
                               description="```\n🔥   뭔가 다른 느낌인데...?\n```", color=discord.Color.orange()), 0.7),
                (discord.Embed(title="🔥🔥  이건... 뭔가 달라요?!!",
                               description="```\n⚡   이게 맞나...?!\n```", color=discord.Color.red()), 0.9),
                (discord.Embed(title="😱  설마... 진짜?!?!",
                               description="```\n🎊   두근두근... 두근두근...\n```", color=discord.Color.from_rgb(255,165,0)), 1.3),
            ]
        elif self.anim_tier == 1:
            anim_frames = [
                (discord.Embed(title="🎲  주사위가 구르고 있어요...",
                               description="```\n⣾   두근두근...\n```", color=discord.Color.yellow()), 0.8),
                (discord.Embed(title="⚡  뭔가... 감이 와요?!",
                               description="```\n⣿   이게 될 것 같은데...?\n```", color=discord.Color.orange()), 0.9),
                (discord.Embed(title="💫  이번엔 다를 것 같아요!",
                               description="```\n⠿   잠깐만요...\n```", color=discord.Color.green()), 1.0),
            ]
        else:
            anim_frames = [
                (discord.Embed(title="🎲  주사위가 구르고 있어요...",
                               description="```\n⣾   두근두근...\n```", color=discord.Color.yellow()), 0.8),
                (discord.Embed(title="🎲🎲  거의 다 왔어요...",
                               description="```\n⣿   흠...  흠...\n```", color=discord.Color.orange()), 0.9),
                (discord.Embed(title="🎲🎲🎲  결과는...?!",
                               description="```\n⠿   잠깐만요!!!\n```", color=discord.Color.red()), 1.0),
            ]

        first_em, first_delay = anim_frames[0]
        await interaction.response.edit_message(embed=first_em, view=self)
        await asyncio.sleep(first_delay)
        for frame_em, frame_delay in anim_frames[1:]:
            await interaction.edit_original_response(embed=frame_em)
            await asyncio.sleep(frame_delay)

        if self.fake_hype:
            fake_win_em = discord.Embed(
                title="🎉✨  당  첨  !!!  ✨🎉",
                description=f"**{round(random.uniform(1.8,2.6),2)}배** 당첨!!! 🎊🎊🎊\n이게 진짜...?! 잠깐만요...",
                color=discord.Color.gold())
            await interaction.edit_original_response(embed=fake_win_em)
            await asyncio.sleep(1.8)
            reveal_em = discord.Embed(
                title="😅  아 아니였네요..",
                description="훼이크였습니다 ㅠㅠ\n배팅금 전액 증발...",
                color=discord.Color.dark_red())
            await interaction.edit_original_response(embed=reveal_em)
            await asyncio.sleep(0.9)

        if self.won:
            payout = int(self.bet * self.multiplier)
            net = payout - self.bet
            async with bot.db.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("UPDATE users SET balance=balance+$1 WHERE id=$2", payout, self.user_id)
            await add_log(self.user_id, "도박_승", net, f"배팅 {self.bet:,}원 × {self.multiplier:.3f}배")
            new_bal = await get_balance(bot.db, self.user_id)
            if self.multiplier >= 2.6:
                color = discord.Color.from_rgb(255,215,0)
                title = "💰🔥✨  초  대  박  !!!  ✨🔥💰"
                header = f"🎊🎊🎊🎊🎊 **{self.multiplier:.2f}배** 초대박!!! 🎊🎊🎊🎊🎊\n💎 TOP 5% 배율 달성!"
                announce_tier = 4
            elif self.multiplier >= 2.1:
                color = discord.Color.gold()
                title = "💰✨  대  박  !!!  ✨💰"
                header = f"🎊🎊🎊 **{self.multiplier:.2f}배** 대박!!! 🎊🎊🎊\n🔥 TOP 20% 배율!"
                announce_tier = 3
            elif self.multiplier >= 1.6:
                color = discord.Color.green()
                title = "🎉  당  첨  !"
                header = f"🎉🎉 **{self.multiplier:.2f}배** 당첨! 🎉🎉"
                announce_tier = 0
            else:
                color = discord.Color.teal()
                title = "✅  소액 당첨"
                header = f"**{self.multiplier:.2f}배** 당첨"
                announce_tier = 0
            result_em = discord.Embed(title=title, color=color)
            result_em.description = f"{header}\n\n💵 배팅 **{self.bet:,}원** → 수령 **{payout:,}원**\n📈 순수익: **+{net:,}원**"
            result_em.add_field(name="현재 잔액", value=f"{new_bal:,}원", inline=True)
            await interaction.edit_original_response(embed=result_em, view=self)
            if announce_tier >= 3 and interaction.channel:
                try:
                    ann_em = gamble_tier_announce_embed(interaction.user, self.multiplier, self.bet, payout)
                    await interaction.channel.send(embed=ann_em)
                except Exception: pass
        else:
            await add_log(self.user_id, "도박_패", -self.bet, f"배팅 {self.bet:,}원")
            new_bal = await get_balance(bot.db, self.user_id)
            result_em = discord.Embed(title="💀  꽝...",
                description=f"배팅 **{self.bet:,}원** 전액 증발...\n다음엔 잘 될 거예요 😢",
                color=discord.Color.red())
            result_em.add_field(name="현재 잔액", value=f"{new_bal:,}원", inline=True)
            await interaction.edit_original_response(embed=result_em, view=self)
        _, hidden_new = await check_and_grant_titles(self.user_id)
        if hidden_new and interaction.channel:
            for ht in hidden_new:
                try:
                    u = await bot.fetch_user(int(self.user_id))
                    await interaction.channel.send(embed=build_hidden_title_unlock_embed(ht, u))
                except Exception: pass

    async def on_timeout(self):
        for child in self.children: child.disabled = True

def gamble_tier_announce_embed(user, multiplier, bet, payout) -> discord.Embed:
    net = payout - bet
    if multiplier >= 2.6:
        title = "🚨💰🔥✨ 도 박 초 대 박 !!! ✨🔥💰🚨"; color = discord.Color.from_rgb(255,215,0)
        header = f"## 💎 **{multiplier:.2f}배** — TOP 5% 배율 달성!! 💎\n**{user.display_name}** 님이 도박에서 전설적인 배율을 뽑았습니다!!"
        footer = "전설의 도박사 탄생... 🤯"
    else:
        title = "🚨🎲 도 박 대 박 !!! 🎲🚨"; color = discord.Color.gold()
        header = f"## 🔥 **{multiplier:.2f}배** — TOP 20% 배율 달성! 🔥\n**{user.display_name}** 님이 도박에서 대박을 터뜨렸습니다!"
        footer = "대박이다! 부럽다... 🥲"
    embed = discord.Embed(title=title, color=color)
    embed.description = f"{header}\n\n💵 배팅 **{bet:,}원** → 수령 **{payout:,}원**\n📈 순수익: **+{net:,}원**"
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text=footer)
    return embed

@bot.tree.command(name="도박베팅한도", description="🎰 현재 도박 최대 베팅액 확인")
async def slash_gamble_limit(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    max_bet = await get_max_bet("gamble_max_bet")
    embed = discord.Embed(title="🎰 도박 베팅 한도", color=discord.Color.blurple())
    embed.description = f"최대 베팅액: **{max_bet:,}원**" if max_bet else "현재 도박 베팅 한도가 **설정되지 않았어요** (무제한)."
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="도박", description="🎰 금액을 배팅! 주사위를 굴려 행운을 시험해보세요!")
@app_commands.describe(금액="배팅할 금액")
async def slash_jackpot(interaction: discord.Interaction, 금액: int):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    if 금액 <= 0: return await interaction.followup.send("❌ 1원 이상만 배팅 가능해요.")
    user_id = str(interaction.user.id)
    max_bet = await get_max_bet("gamble_max_bet")
    if max_bet and 금액 > max_bet:
        return await interaction.followup.send(f"❌ 도박 최대 베팅액은 **{max_bet:,}원**이에요.")
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT balance FROM users WHERE id=$1 FOR UPDATE", user_id)
            if not row or row['balance'] < 금액: return await interaction.followup.send("❌ 잔액이 부족해요!")
            await conn.execute("UPDATE users SET balance=balance-$1 WHERE id=$2", 금액, user_id)
    won, multiplier = get_gamble_result()
    anim_tier, fake_hype = _gamble_anim_params(won, multiplier)
    init_em = discord.Embed(title="🎰  도박 시작!",
        description=f"💵 배팅금: **{금액:,}원** (선차감 완료)\n\n아래 버튼을 눌러 주사위를 굴리세요!\n행운을 빌어요 🍀",
        color=discord.Color.blurple())
    view = GambleView(user_id, 금액, won, multiplier, anim_tier, fake_hype)
    await interaction.followup.send(embed=init_em, view=view)

@bot.command(name='잭팟', aliases=["도박"])
async def prefix_jackpot(ctx, amount: int = None):
    if not await bot_guard(ctx): return
    if amount is None:
        return await ctx.send("사용법: `!도박 [금액]`")
    if amount <= 0: return await ctx.send("❌ 1원 이상만 배팅 가능해요.")
    user_id = str(ctx.author.id)
    max_bet = await get_max_bet("gamble_max_bet")
    if max_bet and amount > max_bet: return await ctx.send(f"❌ 도박 최대 베팅액은 **{max_bet:,}원**이에요.")
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT balance FROM users WHERE id=$1 FOR UPDATE", user_id)
            if not row or row['balance'] < amount: return await ctx.send("❌ 잔액이 부족해요!")
            await conn.execute("UPDATE users SET balance=balance-$1 WHERE id=$2", amount, user_id)
    won, multiplier = get_gamble_result()
    anim_tier, fake_hype = _gamble_anim_params(won, multiplier)
    init_em = discord.Embed(title="🎰  도박 시작!",
        description=f"💵 배팅금: **{amount:,}원** (선차감 완료)\n\n아래 버튼을 눌러 주사위를 굴리세요!\n행운을 빌어요 🍀",
        color=discord.Color.blurple())
    view = GambleView(user_id, amount, won, multiplier, anim_tier, fake_hype)
    await ctx.send(embed=init_em, view=view)

@bot.tree.command(name="랭킹", description="🏆 서버 자산 TOP 10")
async def slash_ranking(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    rows = await bot.db.fetch("SELECT id, balance FROM users ORDER BY balance DESC LIMIT 10")
    embed = discord.Embed(title="🏆 서버 자산 랭킹 TOP 10", color=discord.Color.gold())
    medals = {1:"🥇",2:"🥈",3:"🥉"}
    lines = []
    for i, row in enumerate(rows, 1):
        member = interaction.guild.get_member(int(row['id'])) if interaction.guild else None
        name = member.display_name if member else "알 수 없음"
        lines.append(f"{medals.get(i, f'**{i}위**')} {name} — {row['balance']:,}원")
    embed.description = "\n".join(lines) or "아직 등록된 유저가 없어요."
    await interaction.followup.send(embed=embed)

@bot.command(name='랭킹')
async def prefix_ranking(ctx):
    if not await bot_guard(ctx): return
    rows = await bot.db.fetch("SELECT id, balance FROM users ORDER BY balance DESC LIMIT 10")
    embed = discord.Embed(title="🏆 서버 자산 랭킹 TOP 10", color=discord.Color.gold())
    medals = {1:"🥇",2:"🥈",3:"🥉"}
    lines = []
    for i, row in enumerate(rows, 1):
        member = ctx.guild.get_member(int(row['id'])) if ctx.guild else None
        name = member.display_name if member else "알 수 없음"
        lines.append(f"{medals.get(i, f'**{i}위**')} {name} — {row['balance']:,}원")
    embed.description = "\n".join(lines) or "아직 등록된 유저가 없어요."
    await ctx.send(embed=embed)

async def _build_networth_embed(guild) -> discord.Embed:
    rows = await bot.db.fetch(
        "SELECT u.id, u.balance, COALESCE(SUM(mi.value),0) AS ore_value "
        "FROM users u LEFT JOIN mining_inventory mi ON mi.user_id = u.id "
        "GROUP BY u.id, u.balance")
    ranked = sorted(
        ({"id": r["id"], "total": r["balance"] + int(r["ore_value"])} for r in rows),
        key=lambda x: x["total"], reverse=True)[:10]
    embed = discord.Embed(title="👑 서버 전체 자산 랭킹 TOP 10 (잔액 + 광석 가치)", color=discord.Color.dark_gold())
    medals = {1:"🥇",2:"🥈",3:"🥉"}
    lines = []
    for i, row in enumerate(ranked, 1):
        member = guild.get_member(int(row["id"])) if guild else None
        name = member.display_name if member else "알 수 없음"
        lines.append(f"{medals.get(i, f'**{i}위**')} {name} — {row['total']:,}원")
    embed.description = "\n".join(lines) or "아직 등록된 유저가 없어요."
    return embed

@bot.tree.command(name="전체랭킹", description="👑 잔액 + 보유 광석 가치를 합산한 전체 자산 TOP 10")
async def slash_networth_ranking(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    embed = await _build_networth_embed(interaction.guild)
    await interaction.followup.send(embed=embed)

@bot.command(name='전체랭킹')
async def prefix_networth_ranking(ctx):
    if not await bot_guard(ctx): return
    embed = await _build_networth_embed(ctx.guild)
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  슬롯머신
# ══════════════════════════════════════════════════════════════════════════════
SLOT_SYMBOLS = [
    ("💎",1,20),("7️⃣",2,12),("🍀",3,7),("⭐",5,4),
    ("🔔",8,3),("🍋",14,2),("🍒",20,1.5),
]

def spin_one() -> tuple:
    return random.choices(SLOT_SYMBOLS, weights=[s[1] for s in SLOT_SYMBOLS], k=1)[0]

def slot_result(reels: list) -> tuple[str, float]:
    syms = [r[0] for r in reels]; mults = [r[2] for r in reels]
    if syms[0]==syms[1]==syms[2]:
        m = mults[0]
        msg = "🎊🎊🎊 **대박 잭팟!!!** 🎊🎊🎊" if m>=12 else "🎉🎉 **엄청난 당첨!!** 🎉🎉" if m>=7 else "🥳 **당첨!** 🥳" if m>=3 else "✅ **당첨!**"
        return msg, float(m)
    if syms[0]==syms[1] or syms[1]==syms[2] or syms[0]==syms[2]:
        return "🎯 **2개 일치! 소액 당첨~**", 1.2
    return "💨 **꽝...**", 0.0

class SlotView(discord.ui.View):
    def __init__(self, user_id: str, bet: int):
        super().__init__(timeout=120)
        self.user_id = user_id; self.bet = bet; self.spinning = False

    @discord.ui.button(label="🎰  슬롯 돌리기!", style=discord.ButtonStyle.success)
    async def spin(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.user_id):
            return await interaction.response.send_message("❌ 본인 게임만 돌릴 수 있어요!", ephemeral=True)
        if self.spinning:
            return await interaction.response.send_message("⏳ 이미 돌아가고 있어요!", ephemeral=True)
        self.spinning = True; button.disabled = True
        async with bot.db.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT balance FROM users WHERE id=$1 FOR UPDATE", self.user_id)
                if not row or row['balance'] < self.bet:
                    self.spinning = False; button.disabled = False
                    return await interaction.response.send_message("❌ 잔액이 부족해요!", ephemeral=True)
                await conn.execute("UPDATE users SET balance=balance-$1 WHERE id=$2", self.bet, self.user_id)
        r1,r2,r3 = spin_one(),spin_one(),spin_one()
        def mk(reel_str,color,title="🎰 슬롯머신",footer=""):
            e = discord.Embed(title=title, description=f"배팅: **{self.bet:,}원**", color=color)
            e.add_field(name="릴", value=reel_str, inline=False)
            if footer: e.set_footer(text=footer)
            return e
        await interaction.response.edit_message(
            embed=mk(f"**{r1[0]}**  |  🎲  |  🎲", discord.Color.yellow(), footer="🎲 돌아가는 중..."), view=self)
        await asyncio.sleep(0.9)
        c2 = discord.Color.orange() if r1[0]==r2[0] else discord.Color.yellow()
        f2 = "⚡ 2개 일치! 마지막 릴에 집중하세요...!" if r1[0]==r2[0] else "🎲 마지막 릴 돌아가는 중..."
        await interaction.edit_original_response(embed=mk(f"**{r1[0]}**  |  **{r2[0]}**  |  🎲", c2, footer=f2))
        await asyncio.sleep(1.5 if r1[0]==r2[0] else 0.9)
        msg, mult = slot_result([r1,r2,r3])
        reel_display = f"**{r1[0]}**  |  **{r2[0]}**  |  **{r3[0]}**"
        if mult > 0:
            payout = int(self.bet * mult); net = payout - self.bet
            await bot.db.execute("UPDATE users SET balance=balance+$1 WHERE id=$2", payout, self.user_id)
            await add_log(self.user_id, "슬롯_당첨", net, f"{r1[0]}{r2[0]}{r3[0]} {mult}배 (배팅 {self.bet:,}원)")
            color = discord.Color.gold() if mult>=7 else discord.Color.green()
            result_line = f"배팅 **{self.bet:,}원** → **{payout:,}원** 획득! (+{net:,}원)"
        else:
            await add_log(self.user_id, "슬롯_꽝", -self.bet, f"{r1[0]}{r2[0]}{r3[0]} (배팅 {self.bet:,}원)")
            color = discord.Color.red(); result_line = f"배팅 **{self.bet:,}원** 잃음"
        new_bal = await get_balance(bot.db, self.user_id)
        res = discord.Embed(title="🎰 슬롯머신 결과", color=color)
        res.add_field(name="결과", value=reel_display, inline=False)
        res.add_field(name=msg, value=result_line, inline=False)
        res.add_field(name="현재 잔액", value=f"{new_bal:,}원", inline=True)
        new_view = SlotView(self.user_id, self.bet)
        if new_bal < self.bet: new_view.spin.disabled = True; new_view.spin.label = "잔액 부족"
        await interaction.edit_original_response(embed=res, view=new_view)
        if mult >= 7 and interaction.channel:
            await interaction.channel.send(
                embed=discord.Embed(title="🚨🎰 슬롯머신 대박!! 🎰🚨",
                    description=f"**{interaction.user.display_name}** 님이 **{int(self.bet*mult):,}원** 획득!!",
                    color=discord.Color.gold()))

    @discord.ui.button(label="📊 배당표 보기", style=discord.ButtonStyle.secondary)
    async def paytable(self, interaction: discord.Interaction, button: discord.ui.Button):
        e = discord.Embed(title="🎰 슬롯머신 배당표", color=discord.Color.gold())
        e.add_field(name="배당표", value="\n".join(f"{s}{s}{s} → **{m}배**" for s,_,m in SLOT_SYMBOLS)+"\n🎯 2개 일치 → **1.2배**\n💨 꽝 → 잃음", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="슬롯머신", description="🎰 슬롯머신 돌리기! 최대 20배!")
@app_commands.describe(금액="배팅할 금액")
async def slash_slot(interaction: discord.Interaction, 금액: int):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    if 금액 <= 0: return await interaction.followup.send("❌ 0원 이상만 배팅 가능해요.")
    user_id = str(interaction.user.id)
    max_bet = await get_max_bet("slot_max_bet")
    if max_bet and 금액 > max_bet: return await interaction.followup.send(f"❌ 슬롯 최대 베팅액은 **{max_bet:,}원**이에요.")
    row = await bot.db.fetchrow("SELECT balance FROM users WHERE id=$1", user_id)
    if not row or row['balance'] < 금액: return await interaction.followup.send("❌ 잔액이 부족해요!")
    embed = discord.Embed(title="🎰 슬롯머신",
        description=f"배팅: **{금액:,}원**\n\n버튼을 눌러 슬롯을 돌리세요!", color=discord.Color.blurple())
    await interaction.followup.send(embed=embed, view=SlotView(user_id, 금액))

@bot.command(name="슬롯머신")
async def prefix_slot(ctx, amount: int = None):
    if not await bot_guard(ctx): return
    if amount is None: return await ctx.send("사용법: `!슬롯머신 [금액]`")
    if amount <= 0: return await ctx.send("❌ 0원 이상만 배팅 가능해요.")
    user_id = str(ctx.author.id)
    max_bet = await get_max_bet("slot_max_bet")
    if max_bet and amount > max_bet: return await ctx.send(f"❌ 슬롯 최대 베팅액은 **{max_bet:,}원**이에요.")
    row = await bot.db.fetchrow("SELECT balance FROM users WHERE id=$1", user_id)
    if not row or row['balance'] < amount: return await ctx.send("❌ 잔액이 부족해요!")
    embed = discord.Embed(title="🎰 슬롯머신",
        description=f"배팅: **{amount:,}원**\n\n버튼을 눌러 슬롯을 돌리세요!", color=discord.Color.blurple())
    await ctx.send(embed=embed, view=SlotView(user_id, amount))

# ══════════════════════════════════════════════════════════════════════════════
#  블랙잭
# ══════════════════════════════════════════════════════════════════════════════
SUITS = ["♠️","♥️","♦️","♣️"]; RANKS = ["A","2","3","4","5","6","7","8","9","10","J","Q","K"]
def new_deck(): deck = [(r,s) for s in SUITS for r in RANKS]; random.shuffle(deck); return deck
def card_value(rank): return 10 if rank in ("J","Q","K") else (11 if rank=="A" else int(rank))
def hand_total(hand):
    total = sum(card_value(r) for r,_ in hand); aces = sum(1 for r,_ in hand if r=="A")
    while total > 21 and aces: total -= 10; aces -= 1
    return total
def hand_str(hand, hide_second=False):
    if hide_second: return f"{hand[0][0]}{hand[0][1]}  🂠"
    return "  ".join(f"{r}{s}" for r,s in hand)

class BlackjackView(discord.ui.View):
    def __init__(self, user_id, bet, deck, player, dealer):
        super().__init__(timeout=60)
        self.user_id=user_id; self.bet=bet; self.deck=deck
        self.player=player; self.dealer=dealer; self.ended=False

    def game_embed(self):
        embed = discord.Embed(title="🃏 블랙잭",description=f"배팅: **{self.bet:,}원**",color=discord.Color.blurple())
        embed.add_field(name=f"내 패 ({hand_total(self.player)}점)",value=hand_str(self.player),inline=False)
        embed.add_field(name="딜러 패",value=hand_str(self.dealer,hide_second=True),inline=False)
        embed.set_footer(text="히트: 카드 한 장 더  |  스탠드: 현재 패로 승부"); return embed

    async def end_game(self, interaction, player_hand, dealer_hand):
        self.ended = True
        for child in self.children: child.disabled = True
        p = hand_total(player_hand); d = hand_total(dealer_hand)
        if p > 21: outcome="패배"; db_refund=0; log_delta=-self.bet
        elif d>21 or p>d: outcome="승리"; db_refund=int(self.bet*1.5); log_delta=int(self.bet*0.5)
        elif p==d: outcome="무승부"; db_refund=self.bet; log_delta=0
        else: outcome="패배"; db_refund=0; log_delta=-self.bet
        if db_refund > 0:
            await bot.db.execute("UPDATE users SET balance=balance+$1 WHERE id=$2", db_refund, self.user_id)
        log_type = {"승리":"블랙잭_승","패배":"블랙잭_패","무승부":"블랙잭_무승부"}.get(outcome, outcome)
        await add_log(self.user_id, log_type, log_delta, f"배팅 {self.bet:,}원")
        new_bal = await get_balance(bot.db, self.user_id)
        colors = {"블랙잭!":discord.Color.gold(),"승리":discord.Color.green(),"패배":discord.Color.red(),"무승부":discord.Color.light_grey()}
        titles = {"블랙잭!":"🃏 블랙잭!","승리":"✅ 승리!","패배":"💀 패배...","무승부":"🤝 무승부"}
        embed = discord.Embed(title=titles[outcome], color=colors[outcome])
        embed.add_field(name="내 패",value=f"{hand_str(player_hand)} → **{p}점**",inline=False)
        embed.add_field(name="딜러 패",value=f"{hand_str(dealer_hand)} → **{d}점**",inline=False)
        embed.add_field(name="현재 잔액",value=f"{new_bal:,}원",inline=True)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="히트 🃏", style=discord.ButtonStyle.primary)
    async def hit(self, interaction, button):
        if interaction.user.id != int(self.user_id):
            return await interaction.response.send_message("❌ 본인 게임만!", ephemeral=True)
        self.player.append(self.deck.pop())
        if hand_total(self.player) > 21: await self.end_game(interaction, self.player, self.dealer)
        else: await interaction.response.edit_message(embed=self.game_embed(), view=self)

    @discord.ui.button(label="스탠드 ✋", style=discord.ButtonStyle.danger)
    async def stand(self, interaction, button):
        if interaction.user.id != int(self.user_id):
            return await interaction.response.send_message("❌ 본인 게임만!", ephemeral=True)
        while hand_total(self.dealer) < 17: self.dealer.append(self.deck.pop())
        await self.end_game(interaction, self.player, self.dealer)

async def start_blackjack(user_id, bet):
    deck = new_deck(); player = [deck.pop(),deck.pop()]; dealer = [deck.pop(),deck.pop()]
    if hand_total(player) == 21:
        if hand_total(dealer) == 21:
            await bot.db.execute("UPDATE users SET balance=balance+$1 WHERE id=$2", bet, user_id)
            new_bal = await get_balance(bot.db, user_id)
            await add_log(user_id, "블랙잭_무승부", 0, f"배팅 {bet:,}원")
            e = discord.Embed(title="🤝 무승부",color=discord.Color.light_grey())
            e.add_field(name="현재 잔액",value=f"{new_bal:,}원",inline=True); return e, None
        refund = int(bet*2)
        await bot.db.execute("UPDATE users SET balance=balance+$1 WHERE id=$2", refund, user_id)
        new_bal = await get_balance(bot.db, user_id)
        await add_log(user_id, "블랙잭_승", bet, f"블랙잭! 배팅 {bet:,}원")
        e = discord.Embed(title="🃏 블랙잭!",color=discord.Color.gold())
        e.add_field(name="현재 잔액",value=f"{new_bal:,}원",inline=True); return e, None
    view = BlackjackView(user_id, bet, deck, player, dealer)
    return view.game_embed(), view

@bot.tree.command(name="블랙잭", description="🃏 딜러와 카드 대결!")
@app_commands.describe(금액="배팅할 금액")
async def slash_blackjack(interaction: discord.Interaction, 금액: int):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    if 금액 <= 0: return await interaction.followup.send("❌ 0원 이상만 배팅 가능해요.")
    user_id = str(interaction.user.id)
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT balance FROM users WHERE id=$1 FOR UPDATE", user_id)
            if not row or row['balance'] < 금액: return await interaction.followup.send("❌ 잔액이 부족해요!")
            await conn.execute("UPDATE users SET balance=balance-$1 WHERE id=$2", 금액, user_id)
    embed, view = await start_blackjack(user_id, 금액)
    if view: await interaction.followup.send(embed=embed, view=view)
    else: await interaction.followup.send(embed=embed)

@bot.command(name="블랙잭")
async def prefix_blackjack(ctx, amount: int = None):
    if not await bot_guard(ctx): return
    if amount is None: return await ctx.send("사용법: `!블랙잭 [금액]`")
    if amount <= 0: return await ctx.send("❌ 0원 이상만 배팅 가능해요.")
    user_id = str(ctx.author.id)
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT balance FROM users WHERE id=$1 FOR UPDATE", user_id)
            if not row or row['balance'] < amount: return await ctx.send("❌ 잔액이 부족해요!")
            await conn.execute("UPDATE users SET balance=balance-$1 WHERE id=$2", amount, user_id)
    embed, view = await start_blackjack(user_id, amount)
    if view: await ctx.send(embed=embed, view=view)
    else: await ctx.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  복권
# ══════════════════════════════════════════════════════════════════════════════
LOTTERY_TIERS = [
    ("👑 1등", 0.00001, 75000000),
    ("💎 2등", 0.0005,  3000000),
    ("🥇 3등", 0.01,    100000),
    ("🥈 4등", 0.05,    20000),
]

@bot.tree.command(name="복권", description="🎟️ 복권을 구매합니다!")
async def slash_lottery(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    uid = str(interaction.user.id)
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT balance FROM users WHERE id=$1 FOR UPDATE", uid)
            if not row or row['balance'] < LOTTERY_COST:
                return await interaction.followup.send(f"❌ 복권 구매 비용 **{LOTTERY_COST:,}원**이 부족해요.")
            await conn.execute("UPDATE users SET balance=balance-$1 WHERE id=$2", LOTTERY_COST, uid)
    prize = None
    for rank, chance, amount in LOTTERY_TIERS:
        if random.random() < chance: prize = (rank, amount); break
    if prize:
        rank, amount = prize
        await bot.db.execute("UPDATE users SET balance=balance+$1 WHERE id=$2", amount, uid)
        await add_log(uid, "복권_당첨", amount-LOTTERY_COST, rank)
        new_bal = await get_balance(bot.db, uid)
        embed = discord.Embed(title=f"🎉 {rank} 당첨!!!",
            description=f"💰 **{amount:,}원** 획득!\n\n현재 잔액: **{new_bal:,}원**", color=discord.Color.gold())
        await interaction.followup.send(embed=embed)
        if interaction.channel and rank != "🥈 4등":
            ann = discord.Embed(title="🚨 복권 당첨자 발생! 🚨",
                description=f"**{interaction.user.display_name}** 님이 {rank} 당첨!\n💰 **{amount:,}원** 획득!",
                color=discord.Color.yellow())
            ann.set_thumbnail(url=interaction.user.display_avatar.url)
            await interaction.channel.send(embed=ann)
        if rank == "👑 1등":
            existing = await get_user_titles(bot.db, uid)
            if "🎉 미친행운" not in existing:
                await grant_title(bot.db, uid, "🎉 미친행운", "#FFD700")
                if interaction.channel:
                    await interaction.channel.send(embed=build_hidden_title_unlock_embed("🎉 미친행운", interaction.user))
    else:
        await add_log(uid, "복권_꽝", -LOTTERY_COST, "꽝")
        new_bal = await get_balance(bot.db, uid)
        embed = discord.Embed(title="🎫 복권 결과",
            description=f"꽝입니다... 😢\n\n현재 잔액: **{new_bal:,}원**", color=discord.Color.red())
        await interaction.followup.send(embed=embed)

@bot.command(name="복권")
async def prefix_lottery(ctx):
    if not await bot_guard(ctx): return
    uid = str(ctx.author.id)
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT balance FROM users WHERE id=$1 FOR UPDATE", uid)
            if not row or row['balance'] < LOTTERY_COST:
                return await ctx.send(f"❌ 복권 구매 비용 **{LOTTERY_COST:,}원**이 부족해요.")
            await conn.execute("UPDATE users SET balance=balance-$1 WHERE id=$2", LOTTERY_COST, uid)
    prize = None
    for rank, chance, amount in LOTTERY_TIERS:
        if random.random() < chance: prize = (rank, amount); break
    if prize:
        rank, amount = prize
        await bot.db.execute("UPDATE users SET balance=balance+$1 WHERE id=$2", amount, uid)
        await add_log(uid, "복권_당첨", amount-LOTTERY_COST, rank)
        new_bal = await get_balance(bot.db, uid)
        embed = discord.Embed(title=f"🎉 {rank} 당첨!!!",
            description=f"💰 **{amount:,}원** 획득!\n\n현재 잔액: **{new_bal:,}원**", color=discord.Color.gold())
        await ctx.send(embed=embed)
        if rank != "🥈 4등":
            ann = discord.Embed(title="🚨 복권 당첨자 발생! 🚨",
                description=f"**{ctx.author.display_name}** 님이 {rank} 당첨!\n💰 **{amount:,}원** 획득!",
                color=discord.Color.yellow())
            ann.set_thumbnail(url=ctx.author.display_avatar.url)
            await ctx.send(embed=ann)
        if rank == "👑 1등":
            existing = await get_user_titles(bot.db, uid)
            if "🎉 미친행운" not in existing:
                await grant_title(bot.db, uid, "🎉 미친행운", "#FFD700")
                await ctx.send(embed=build_hidden_title_unlock_embed("🎉 미친행운", ctx.author))
    else:
        await add_log(uid, "복권_꽝", -LOTTERY_COST, "꽝")
        new_bal = await get_balance(bot.db, uid)
        await ctx.send(discord.Embed(title="🎫 복권 결과",
            description=f"꽝입니다... 😢\n\n현재 잔액: **{new_bal:,}원**", color=discord.Color.red()))

# ══════════════════════════════════════════════════════════════════════════════
#  주사위 PvP
# ══════════════════════════════════════════════════════════════════════════════
class DicePvPView(discord.ui.View):
    def __init__(self, challenger, target, amount):
        super().__init__(timeout=30)
        self.challenger=challenger; self.target=target; self.amount=amount; self.done=False

    async def on_timeout(self):
        if not self.done:
            for child in self.children: child.disabled = True

    @discord.ui.button(label="주사위 굴리기 ✅", style=discord.ButtonStyle.green)
    async def accept(self, interaction, button):
        if interaction.user.id != self.target.id:
            return await interaction.response.send_message("❌ 도전받은 사람만 수락할 수 있어요!", ephemeral=True)
        self.done = True
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(view=self)
        chal_id = str(self.challenger.id); targ_id = str(self.target.id); amount = self.amount
        chal_row = await bot.db.fetchrow("SELECT balance FROM users WHERE id=$1", chal_id)
        targ_row = await bot.db.fetchrow("SELECT balance FROM users WHERE id=$1", targ_id)
        if not chal_row or chal_row['balance'] < amount:
            return await interaction.channel.send(f"❌ {self.challenger.display_name}님의 잔액이 부족해요!")
        if not targ_row or targ_row['balance'] < amount:
            return await interaction.channel.send(f"❌ {self.target.display_name}님의 잔액이 부족해요!")
        c_roll = random.randint(1,6); t_roll = random.randint(1,6)
        dice_emoji = ["⚀","⚁","⚂","⚃","⚄","⚅"]
        if c_roll == t_roll:
            embed = discord.Embed(title="🎲 주사위 대결 — 무승부!",
                description=f"{dice_emoji[c_roll-1]} **{self.challenger.display_name}** : {c_roll}\n{dice_emoji[t_roll-1]} **{self.target.display_name}** : {t_roll}\n\n**동점! 베팅금액은 환불됩니다.**",
                color=discord.Color.greyple())
            return await interaction.channel.send(embed=embed)
        if c_roll > t_roll: winner,loser,w_id,l_id,w_roll,l_roll = self.challenger,self.target,chal_id,targ_id,c_roll,t_roll
        else: winner,loser,w_id,l_id,w_roll,l_roll = self.target,self.challenger,targ_id,chal_id,t_roll,c_roll
        async with bot.db.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE users SET balance=balance+$1 WHERE id=$2", amount, w_id)
                await conn.execute("UPDATE users SET balance=balance-$1 WHERE id=$2", amount, l_id)
        await add_log(w_id, "주사위_승", amount, f"vs {loser.display_name}")
        await add_log(l_id, "주사위_패", -amount, f"vs {winner.display_name}")
        embed = discord.Embed(title="🎲 주사위 대결 결과!",
            description=f"{dice_emoji[c_roll-1]} **{self.challenger.display_name}** : **{c_roll}**\n{dice_emoji[t_roll-1]} **{self.target.display_name}** : **{t_roll}**\n\n🏆 **{winner.display_name}** 님이 승리! **{amount:,}원** 획득!",
            color=discord.Color.gold())
        await interaction.channel.send(embed=embed)
        for uid, user in [(w_id,winner),(l_id,loser)]:
            _, hn = await check_and_grant_titles(uid)
            for ht in hn: await interaction.channel.send(embed=build_hidden_title_unlock_embed(ht, user))

    @discord.ui.button(label="거절 ❌", style=discord.ButtonStyle.red)
    async def decline(self, interaction, button):
        if interaction.user.id != self.target.id:
            return await interaction.response.send_message("❌ 도전받은 사람만 거절할 수 있어요!", ephemeral=True)
        self.done = True
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content=f"❌ **{self.target.display_name}**님이 거절했어요.", view=self)
        self.stop()

@bot.command(name="주사위")
async def prefix_dice_pvp(ctx, target: discord.Member = None, amount: int = None):
    if not await bot_guard(ctx): return
    if target is None or amount is None: return await ctx.send("사용법: `!주사위 @유저 금액`")
    if target.id == ctx.author.id: return await ctx.send("❌ 자기 자신에게는 도전할 수 없어요.")
    if target.bot: return await ctx.send("❌ 봇에게는 도전할 수 없어요.")
    if amount <= 0: return await ctx.send("❌ 0원 이상만 가능해요.")
    chal_id = str(ctx.author.id)
    row = await bot.db.fetchrow("SELECT balance FROM users WHERE id=$1", chal_id)
    if not row or row['balance'] < amount: return await ctx.send("❌ 잔액이 부족해요!")
    view = DicePvPView(ctx.author, target, amount)
    embed = discord.Embed(title="🎲 주사위 대결 신청!",
        description=f"**{ctx.author.display_name}** 님이 **{target.mention}** 님에게 주사위 대결을 신청했어요!\n\n💰 베팅 금액: **{amount:,}원**\n\n30초 안에 수락 또는 거절해주세요!",
        color=discord.Color.blurple())
    await ctx.send(embed=embed, view=view)

# ══════════════════════════════════════════════════════════════════════════════
#  거래 내역
# ══════════════════════════════════════════════════════════════════════════════
LOG_ICONS = {
    "출석":"🎁","송금_발신":"📤","송금_수신":"📥",
    "도박_승":"🎉","도박_패":"💀","슬롯_당첨":"🎰","슬롯_꽝":"💨",
    "블랙잭_승":"✅","블랙잭_패":"❌","블랙잭_무승부":"🤝",
    "관리자_추가":"➕","관리자_차감":"➖","복권_당첨":"🎟️","복권_꽝":"🎫",
    "광석_판매":"⛏️","곡괭이뽑기":"🔨","가방뽑기":"🎒","곡괭이수리":"🔧",
}

def build_log_embed(user, rows, title_override=None) -> discord.Embed:
    embed = discord.Embed(
        title=title_override or f"📋 {user.display_name}님 거래 내역 (최근 15건)",
        color=discord.Color.blurple())
    if not rows:
        embed.description = "거래 내역이 없어요."
    else:
        lines = []
        for r in rows:
            icon = LOG_ICONS.get(r['type'], "•")
            sign = "+" if r['amount'] >= 0 else ""
            ts = r['created_at'].strftime('%m/%d %H:%M')
            detail = f" `{r['detail']}`" if r['detail'] else ""
            lines.append(f"`{ts}` {icon} **{sign}{r['amount']:,}원** — {r['type']}{detail}")
        embed.description = "\n".join(lines)
    return embed

@bot.tree.command(name="거래내역", description="📋 내 최근 거래 내역 15건을 확인합니다")
async def slash_my_log(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    uid = str(interaction.user.id)
    rows = await bot.db.fetch(
        "SELECT type,amount,detail,created_at FROM transactions WHERE user_id=$1 ORDER BY created_at DESC LIMIT 15", uid)
    await interaction.response.send_message(embed=build_log_embed(interaction.user, rows), ephemeral=True)

@bot.command(name='거래내역', aliases=['내거래내역'])
async def prefix_my_log(ctx):
    if not await bot_guard(ctx): return
    uid = str(ctx.author.id)
    rows = await bot.db.fetch(
        "SELECT type,amount,detail,created_at FROM transactions WHERE user_id=$1 ORDER BY created_at DESC LIMIT 15", uid)
    await ctx.send(embed=build_log_embed(ctx.author, rows))

# ══════════════════════════════════════════════════════════════════════════════
#  칭호 명령어
# ══════════════════════════════════════════════════════════════════════════════
def _build_title_list_embed(titles_full, equipped):
    embed = discord.Embed(title="🎖️ 칭호 목록", color=discord.Color.gold())
    if not titles_full:
        embed.description = "아직 보유한 칭호가 없어요!"
    else:
        hidden_set = set(HIDDEN_TITLES)
        normal_lines, event_lines, hidden_lines = [], [], []
        for idx, r in enumerate(titles_full, 1):
            t, is_event = r['title'], r['is_event']
            mark = " ◀ 장착중" if t==equipped else ""
            bold = "**" if t==equipped else ""
            entry = f"`{idx}.` {bold}{t}{bold}{mark}"
            if t in hidden_set: hidden_lines.append(entry)
            elif is_event: event_lines.append(entry)
            else: normal_lines.append(entry)
        parts = []
        if normal_lines: parts.append("**일반 칭호**\n" + "\n".join(normal_lines))
        if event_lines: parts.append("**🎪 이벤트 칭호**\n" + "\n".join(event_lines))
        if hidden_lines: parts.append("**🔮 히든 칭호**\n" + "\n".join(hidden_lines))
        embed.description = "\n\n".join(parts)
        embed.set_footer(text="!칭호장착 [번호 또는 칭호명] 으로 장착 | !칭호해제 로 해제")
    total_possible = len(TITLE_CONDITIONS) + len(HIDDEN_TITLES)
    embed.add_field(name="획득률", value=f"{len(titles_full)}/{total_possible}개", inline=True)
    return embed

@bot.tree.command(name="칭호목록", description="🎖️ 내가 보유한 칭호 목록")
async def slash_title_list(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    uid = str(interaction.user.id)
    _, hidden_new = await check_and_grant_titles(uid)
    titles_full = await get_user_titles_full(bot.db, uid)
    equipped = await get_equipped_title(bot.db, uid)
    await interaction.response.send_message(embed=_build_title_list_embed(titles_full, equipped), ephemeral=True)
    if hidden_new and interaction.channel:
        for ht in hidden_new:
            await interaction.channel.send(embed=build_hidden_title_unlock_embed(ht, interaction.user))

@bot.command(name="칭호목록")
async def prefix_title_list(ctx):
    if not await bot_guard(ctx): return
    uid = str(ctx.author.id)
    _, hidden_new = await check_and_grant_titles(uid)
    titles_full = await get_user_titles_full(bot.db, uid)
    equipped = await get_equipped_title(bot.db, uid)
    await ctx.send(embed=_build_title_list_embed(titles_full, equipped))
    for ht in hidden_new:
        await ctx.send(embed=build_hidden_title_unlock_embed(ht, ctx.author))

@bot.tree.command(name="칭호장착", description="🏷️ 보유한 칭호를 프로필에 장착합니다")
@app_commands.describe(칭호="장착할 칭호 이름 또는 번호")
async def slash_equip_title(interaction: discord.Interaction, 칭호: str):
    if not await slash_guard(interaction): return
    uid = str(interaction.user.id)
    titles = await get_user_titles(bot.db, uid)
    selected = 칭호
    if 칭호.isdigit():
        idx = int(칭호) - 1
        if idx < 0 or idx >= len(titles):
            return await interaction.response.send_message(f"❌ 번호 1~{len(titles)} 사이로 입력해주세요.", ephemeral=True)
        selected = titles[idx]
    if selected not in titles:
        return await interaction.response.send_message("❌ 보유하지 않은 칭호예요.", ephemeral=True)
    await bot.db.execute(
        "INSERT INTO equipped_title (user_id,title) VALUES($1,$2) ON CONFLICT (user_id) DO UPDATE SET title=$2",
        uid, selected)
    await interaction.response.send_message(f"✅ **{selected}** 칭호를 장착했어요!", ephemeral=True)

@bot.command(name="칭호장착")
async def prefix_equip_title(ctx, *, title: str):
    if not await bot_guard(ctx): return
    uid = str(ctx.author.id)
    titles = await get_user_titles(bot.db, uid)
    selected = title
    if title.isdigit():
        idx = int(title) - 1
        if idx < 0 or idx >= len(titles): return await ctx.send(f"❌ 번호 1~{len(titles)} 사이로 입력해주세요.")
        selected = titles[idx]
    if selected not in titles: return await ctx.send("❌ 보유하지 않은 칭호예요.")
    await bot.db.execute(
        "INSERT INTO equipped_title (user_id,title) VALUES($1,$2) ON CONFLICT (user_id) DO UPDATE SET title=$2",
        uid, selected)
    await ctx.send(f"✅ **{selected}** 칭호를 장착했어요!")

@bot.command(name="칭호해제")
async def prefix_unequip_title(ctx):
    if not await bot_guard(ctx): return
    await bot.db.execute("DELETE FROM equipped_title WHERE user_id=$1", str(ctx.author.id))
    await ctx.send("✅ 칭호를 해제했어요.")

@bot.tree.command(name="칭호도감", description="📖 해금 가능한 모든 칭호 목록")
async def slash_title_book(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    uid = str(interaction.user.id)
    owned = set(await get_user_titles(bot.db, uid))
    embed = discord.Embed(title="📖 칭호 도감", color=discord.Color.blurple())
    lines = []
    for title, condition in TITLE_CONDITIONS:
        lines.append(f"✅ **{title}** — {condition}" if title in owned else f"🔒 {title} — {condition}")
    lines.append(""); lines.append("**🔮 히든 칭호 (조건 비공개)**")
    for ht in HIDDEN_TITLES:
        lines.append(f"✅ **{ht}**" if ht in owned else "❓ ???")
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"획득: {len(owned)}/{len(TITLE_CONDITIONS)+len(HIDDEN_TITLES)}개")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.command(name="칭호도감")
async def prefix_title_book(ctx):
    if not await bot_guard(ctx): return
    uid = str(ctx.author.id)
    owned = set(await get_user_titles(bot.db, uid))
    embed = discord.Embed(title="📖 칭호 도감", color=discord.Color.blurple())
    lines = []
    for title, condition in TITLE_CONDITIONS:
        lines.append(f"✅ **{title}** — {condition}" if title in owned else f"🔒 {title} — {condition}")
    lines.append(""); lines.append("**🔮 히든 칭호 (조건 비공개)**")
    for ht in HIDDEN_TITLES:
        lines.append(f"✅ **{ht}**" if ht in owned else "❓ ???")
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"획득: {len(owned)}/{len(TITLE_CONDITIONS)+len(HIDDEN_TITLES)}개")
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  ⛏️ 광질 게임 — 데이터
# ══════════════════════════════════════════════════════════════════════════════
ORE_GRADES: dict[str, dict] = {
    "일반": {"emoji":"🪨","color":0x8B8B8B,"w_min":1,      "w_max":500,    "w_per_g":1,   "prob":0.64},
    "희귀": {"emoji":"🖤","color":0x5C4033,"w_min":400,    "w_max":1500,   "w_per_g":2,   "prob":0.22},
    "레어": {"emoji":"🟠","color":0xFF8C00,"w_min":1400,   "w_max":3500,   "w_per_g":5,   "prob":0.08},
    "고급": {"emoji":"🟢","color":0x00B050,"w_min":3500,   "w_max":8000,   "w_per_g":25,  "prob":0.035},
    "신화": {"emoji":"💠","color":0x7B68EE,"w_min":8000,   "w_max":20000,  "w_per_g":50,  "prob":0.012},
    "전설": {"emoji":"💎","color":0x00BFFF,"w_min":20000,  "w_max":50000,  "w_per_g":100, "prob":0.003},
    "미친": {"emoji":"🌌","color":0xFF00FF,"w_min":50000,  "w_max":99999,  "w_per_g":100, "prob":0.0008},
    "???":  {"emoji":"🔮","color":0xFFFFFF,"w_min":1,      "w_max":10_000_000, "w_per_g":0.5, "prob":0.00005},
}

ORE_LIST: dict[str, list[str]] = {
    "일반": ["자갈","편암","편마암","이암","사암","점토판","석회암","점판암","현무암","화강암","황토","석고","이회암",
             "흑색 이판암","규질 점토","역암"],
    "희귀": ["석탄","갈탄","토탄","무연탄","안산암","응회암","휘록암","각섬암","규암","사문암","흑요석","셰일","박막암",
             "갈철광","황화철광","크롬철광"],
    "레어": ["황동석","적철석","자철석","갈철석","방연석","섬아연석","휘수연석","보르나이트","플루오라이트","휘창석","백반석","진사","능철석",
             "청금석","람베르그아이트","로도나이트"],
    "고급": ["천연 황","청람석","침철석","장석","휘석","공작석","남동석","터키석","마노(아게이트)","타이거즈아이","형석","방해석","홍옥수",
             "황옥","장미석영","달빛 방해석"],
    "신화": ["비취","토파즈","사파이어","자수정","황수정","에메랄드","오팔","아쿠아마린","가넷","페리도트","파이로프","모르가나이트","파라이바 스피넬",
             "아이올라이트","파파라챠 사파이어","해왕성 비취"],
    "전설": ["루비","천연 다이아몬드","백금 원석","흑다이아몬드","알렉산드라이트","파라이바 토르말린","붉은 다이아몬드",
             "핑크 다이아몬드","블루문 사파이어"],
    "미친": ["추락한 운석 파편","블랙홀 잔해 원석","웜홀의 핵","붕괴된 별의 파편","차원 틈새 결정체",
             "소멸된 신의 결정체"],
    "???":  ["승훈이의 생선가시", "남결이가 풀고 버려서 슬픈 수학 문제집",
             "원준이 방의 숨겨진 키 성장제", "승훈이의 멘헤라 정병 고치는 법 검색기록"],
}
ALL_ORES: list[str] = [o for lst in ORE_LIST.values() for o in lst]

PICK_DATA: dict[str, dict] = {
    "일반":{"emoji":"⛏️", "color":0x808080,"time":25,"destroy":0.15,"repair":5_000,
            "items":["휴대용 광선 커터","강화 탄소 섬유 곡괭이","고철더미에서 발굴한 드릴",
                     "수동식 유압 굴착기","에너지 셀이 방전된 정","마그네틱 합금 렌치","기계공의 다목적 망치"]},
    "희귀":{"emoji":"🔵","color":0x1E90FF,"time":20,"destroy":0.10,"repair":10_000,
            "items":["과부하 방전 레이저","티타늄 코어 보링 드릴","마나 가스 충전식 착암기",
                     "고주파 미세 진동 커터","열처리 정밀 융해기","마이크로 펄스 굴착 래칫"]},
    "레어":{"emoji":"🟣","color":0x9B59B6,"time":14,"destroy":0.07,"repair":50_000,
            "items":["프로토타입 플라즈마 드릴","중력장 왜곡 커터","아크 방전식 일렉트로 픽",
                     "네오-디뮴 자력 파쇄기","사이오닉 에너지 나이프"]},
    "에픽":{"emoji":"🟠","color":0xFF8C00,"time":10,"destroy":0.05,"repair":100_000,
            "items":["암흑 물질 인젝터 드릴","쿼크 입자 가속 굴착기","볼텍스 차원 정밀 정","나노 머신 분해 레이저 포"]},
    "신화":{"emoji":"✨","color":0x7B68EE,"time":7, "destroy":0.04,"repair":300_000,
            "items":["반물질 동력원 코어 가해기","시공간 가속 입자 커터","스타더스트 제련 드릴"]},
    "전설":{"emoji":"💎","color":0xFFD700,"time":5, "destroy":0.03,"repair":500_000,
            "items":["초신성 폭발 잔해 크러셔","카지노 리치 골드 엠페러"]},
    "미친":{"emoji":"🌌","color":0xFF00FF,"time":3, "destroy":0.03,"repair":1_000_000,
            "items":["우주의 끝을 갈라낸 균열 드릴"]},
}
ALL_PICKS: list[str] = [p for d in PICK_DATA.values() for p in d["items"]]
PICK_NAME_TO_GRADE: dict[str, str] = {p: g for g, d in PICK_DATA.items() for p in d["items"]}

BAG_DATA: dict[str, dict] = {
    "일반가방":{"emoji":"🎒","cap":10,   "color":0x808080,"prob":0.60},
    "레어가방":{"emoji":"💼","cap":30,   "color":0x1E90FF,"prob":0.25},
    "에픽가방":{"emoji":"🧳","cap":50,   "color":0xFF8C00,"prob":0.10},
    "신화가방":{"emoji":"🎑","cap":100,  "color":0x7B68EE,"prob":0.040},
    "전설가방":{"emoji":"🏺","cap":500,  "color":0xFFD700,"prob":0.009},
    "미친가방":{"emoji":"🌌","cap":6974, "color":0xFF00FF,"prob":0.001},
}

DEFAULT_PICK_PROBS: dict[str, float] = {
    "일반":0.60,"희귀":0.24,"레어":0.10,"에픽":0.035,"신화":0.015,"전설":0.008,"미친":0.002,
}
DEFAULT_BAG_PROBS: dict[str, float] = {k: v["prob"] for k, v in BAG_DATA.items()}
DEFAULT_ORE_PROBS: dict[str, float] = {k: v["prob"] for k, v in ORE_GRADES.items()}

# ══════════════════════════════════════════════════════════════════════════════
#  광질 DB 헬퍼
# ══════════════════════════════════════════════════════════════════════════════
async def get_mine_user(uid: str) -> dict:
    row = await bot.db.fetchrow("SELECT * FROM mining_users WHERE id=$1", uid)
    if not row:
        await bot.db.execute("INSERT INTO mining_users (id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
        row = await bot.db.fetchrow("SELECT * FROM mining_users WHERE id=$1", uid)
    return dict(row)

async def get_mine_inv(uid: str) -> list[dict]:
    rows = await bot.db.fetch(
        "SELECT id,ore_name,ore_grade,weight,value FROM mining_inventory WHERE user_id=$1 ORDER BY id", uid)
    return [dict(r) for r in rows]

async def get_mine_cfg(key: str, default):
    row = await bot.db.fetchrow("SELECT value FROM mining_config WHERE key=$1", key)
    if not row: return default
    try: return json.loads(row['value'])
    except Exception: return default

async def set_mine_cfg(key: str, val) -> None:
    await bot.db.execute(
        "INSERT INTO mining_config (key,value) VALUES($1,$2) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        key, json.dumps(val, ensure_ascii=False))

async def get_discovered_ores(uid: str) -> set[str]:
    rows = await bot.db.fetch("SELECT ore_name FROM mining_discovered_ores WHERE user_id=$1", uid)
    return {r['ore_name'] for r in rows}

async def get_discovered_picks(uid: str) -> set[str]:
    rows = await bot.db.fetch("SELECT pick_name FROM mining_discovered_picks WHERE user_id=$1", uid)
    return {r['pick_name'] for r in rows}

async def add_mining_log(uid: str, log_type: str, detail: str, mu: dict | None = None):
    if mu is None:
        await bot.db.execute(
            "INSERT INTO mining_logs (user_id,log_type,detail) VALUES($1,$2,$3)", uid, log_type, detail)
    else:
        await bot.db.execute(
            "INSERT INTO mining_logs (user_id,log_type,detail,pickaxe_grade,pickaxe_name,pickaxe_broken,bag_type) VALUES($1,$2,$3,$4,$5,$6,$7)",
            uid, log_type, detail, mu.get('pickaxe_grade'), mu.get('pickaxe_name'), mu.get('pickaxe_broken'), mu.get('bag_type'))

def _weighted_choice(probs: dict[str, float]) -> str:
    keys = list(probs.keys()); wts = [probs[k] for k in keys]
    return random.choices(keys, weights=wts, k=1)[0]

async def roll_ore() -> dict:
    ore_probs = await get_mine_cfg("ore_probs", DEFAULT_ORE_PROBS)
    grade = _weighted_choice(ore_probs)
    name  = random.choice(ORE_LIST[grade])
    gd    = ORE_GRADES[grade]
    weight = random.randint(gd["w_min"], gd["w_max"])
    value  = int(round(weight * gd["w_per_g"]))
    return {"ore_name": name, "ore_grade": grade, "weight": weight, "value": value}

async def roll_pickaxe() -> tuple[str, str]:
    pick_probs = await get_mine_cfg("pick_probs", DEFAULT_PICK_PROBS)
    grade = _weighted_choice(pick_probs)
    name  = random.choice(PICK_DATA[grade]["items"])
    return grade, name

async def roll_bag() -> str:
    bag_probs = await get_mine_cfg("bag_probs", DEFAULT_BAG_PROBS)
    return _weighted_choice(bag_probs)

# ══════════════════════════════════════════════════════════════════════════════
#  광질 세션 & 백그라운드 태스크
# ══════════════════════════════════════════════════════════════════════════════
mining_sessions: dict[int, asyncio.Task] = {}
MINE_TIMEOUT = 300  # 최대 광질 대기 시간 (초)

async def _send_message(interaction, content="", embed=None):
    try:
        await interaction.followup.send(content=content, embed=embed)
    except discord.HTTPException:
        ch = interaction.channel
        if ch:
            try: await ch.send(content=content, embed=embed)
            except Exception: pass

async def _ore_cutscene(interaction, ore: dict, uid: int):
    """신화+ 광석 발굴 컷씬"""
    grade = ore['ore_grade']
    if grade == "???":
        intro = discord.Embed(
            title="🔮🚨 [DIMENSIONAL ANOMALY] 알 수 없는 파장이 감지됩니다 🚨🔮",
            description=(
                "```ansi\n\u001b[31m🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨\n"
                "⚠️ REALITY BREACH: 시공간 구조가 붕괴하고 있습니다\n"
                "⚠️ FREQUENCY: ∞Hz — 우주의 근원이 진동합니다\n"
                "🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨\u001b[0m\n```\n"
                "이것은 꿈인가... 현실인가...\n"
                "**존재해서는 안 될 것이 당신 앞에 나타나고 있습니다!!!**"
            ),
            color=0xFFFFFF
        )
        msg = await _send_message(interaction, content=f"<@{uid}>", embed=intro)
        await asyncio.sleep(3.0)

        build = discord.Embed(
            title="✨🌌💫 [???] 무언가가 지면을 뚫고 올라옵니다... 💫🌌✨",
            description=(
                "빛도 중력도 시간도 굴복하는...\n"
                "**이것은 우주 탄생 이전부터 존재하던 물질!!**\n\n"
                "```\n████████████████ 100%\n"
                f"『 {ore['ore_name']} 』발굴 완료...\n```"
            ),
            color=0xFF69B4
        )
        await asyncio.sleep(2.5)

        final = discord.Embed(
            title=f"🔮💠✨👑 [???] {ore['ore_name']} ✨💠🔮",
            description=(
                f"## 🌟🌟🌟 초월적 발굴 성공!!! 🌟🌟🌟\n\n"
                f"무게: **{ore['weight']:,}g** | 가치: **{ore['value']:,}원**\n\n"
                f"*이 광석을 발굴한 자는 우주의 역사에 기록됩니다*\n"
                f"**전 서버에 이 기적이 알려집니다!!**"
            ),
            color=0xFFFFFF
        )
        await _send_message(interaction, content=f"<@{uid}>", embed=final)
        # 서버 전체 공지
        if interaction.channel:
            try:
                user = await bot.fetch_user(uid)
                ann = discord.Embed(
                    title=f"🔮🌌✨ [???] {ore['ore_name']} 출현!!! ✨🌌🔮",
                    description=(
                        f"## 🌟 **{user.display_name}** 님이\n"
                        f"# 우주 최고 희귀 광석\n"
                        f"## **「 {ore['ore_name']} 」** 을 발굴했습니다!!\n\n"
                        f"💰 무게: **{ore['weight']:,}g** | 가치: **{ore['value']:,}원**\n\n"
                        "*이 광석은 우주가 탄생하기 이전부터 존재한 물질입니다*"
                    ),
                    color=0xFFFFFF
                )
                ann.set_thumbnail(url=user.display_avatar.url)
                ann.set_footer(text="🔮 이 순간은 역사에 기록됩니다 🔮")
                await interaction.channel.send(embed=ann)
            except Exception: pass
        # 히든 칭호 부여
        existing = await get_user_titles(bot.db, str(uid))
        if "🌌 우주의 목격자" not in existing:
            await grant_title(bot.db, str(uid), "🌌 우주의 목격자", "#FFFFFF")
            try:
                user = await bot.fetch_user(uid)
                if interaction.channel:
                    await interaction.channel.send(embed=build_hidden_title_unlock_embed("🌌 우주의 목격자", user))
            except Exception: pass
    elif grade == "미친":
        intro = discord.Embed(
            title="🌌🚨 [CRITICAL] 우주 에너지 폭주!!!",
            description="```\n⚠️ SYSTEM OVERHEAT: 우주의 마력이 폭주합니다!\n우주 광석이 강림하려고 합니다!!!\n```",
            color=0xFF00FF
        )
        await _send_message(interaction, content=f"<@{uid}>", embed=intro)
        await asyncio.sleep(2.0)
        final = discord.Embed(
            title=f"🌌🎉 [미친] {ore['ore_name']} 발굴!!!",
            description=f"무게: **{ore['weight']:,}g** | 가치: **{ore['value']:,}원**",
            color=0xFF00FF
        )
        await _send_message(interaction, embed=final)
    elif grade == "전설":
        intro = discord.Embed(
            title="💎⚡ [전설] 대지가 격렬하게 진동합니다!!",
            description="```\n황금빛 차원의 폭풍이 몰아칩니다!\n전설의 광석이...\n```",
            color=0xFFD700
        )
        await _send_message(interaction, content=f"<@{uid}>", embed=intro)
        await asyncio.sleep(1.5)
        final = discord.Embed(
            title=f"💎🎉 [전설] {ore['ore_name']} 발굴!!!",
            description=f"무게: **{ore['weight']:,}g** | 가치: **{ore['value']:,}원**",
            color=0x00BFFF
        )
        await _send_message(interaction, embed=final)
    elif grade == "신화":
        intro = discord.Embed(
            title="💠✨ [신화] 마나의 광운이 감싸기 시작합니다...",
            description="```\n신비로운 에너지가 차원의 벽에 균열을 냅니다...\n```",
            color=0x7B68EE
        )
        await _send_message(interaction, content=f"<@{uid}>", embed=intro)
        await asyncio.sleep(1.2)
        final = discord.Embed(
            title=f"💠🎉 [신화] {ore['ore_name']} 발굴!!!",
            description=f"무게: **{ore['weight']:,}g** | 가치: **{ore['value']:,}원**",
            color=0x7B68EE
        )
        await _send_message(interaction, embed=final)

async def _mine_task(interaction: discord.Interaction, uid: int, original_grade: str,
                     original_name: str, task_ref: list) -> None:
    try:
        mu = await get_mine_user(str(uid))
        base_time = PICK_DATA[original_grade]["time"]
        wait_sec  = max(0.5, base_time * mu["mining_speed"])

        # 타임아웃: 최대 MINE_TIMEOUT 초 대기
        try:
            await asyncio.wait_for(asyncio.sleep(wait_sec), timeout=MINE_TIMEOUT)
        except asyncio.TimeoutError:
            await _send_message(interaction, content=f"<@{uid}>",
                embed=discord.Embed(title="⚠️ 광질 타임아웃",
                    description="광질 시간이 너무 오래 걸려 자동으로 종료됐어요.", color=discord.Color.red()))
            return

        mu = await get_mine_user(str(uid))
        inv = await get_mine_inv(str(uid))
        cap = BAG_DATA[mu["bag_type"]]["cap"]
        if len(inv) >= cap:
            await _send_message(interaction, content=f"<@{uid}>",
                embed=discord.Embed(title="⛏️ 광질 완료",
                    description="⚠️ 가방이 꽉 차서 광석을 담지 못했어요!\n`/판매` 로 광석을 팔거나 더 좋은 가방을 뽑으세요.",
                    color=discord.Color.orange()))
            return

        ore = await roll_ore()
        async with bot.db.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO mining_inventory(user_id,ore_name,ore_grade,weight,value) VALUES($1,$2,$3,$4,$5)",
                    str(uid), ore["ore_name"], ore["ore_grade"], ore["weight"], ore["value"])
                await conn.execute(
                    "INSERT INTO mining_discovered_ores(user_id,ore_name) VALUES($1,$2) ON CONFLICT DO NOTHING",
                    str(uid), ore["ore_name"])
                await conn.execute(
                    "UPDATE mining_users SET total_mined=total_mined+1 WHERE id=$1", str(uid))
                mu2 = await conn.fetchrow("SELECT pickaxe_grade,pickaxe_name,pickaxe_broken FROM mining_users WHERE id=$1", str(uid))
                destroyed = False
                if (mu2 and mu2["pickaxe_name"] == original_name
                        and mu2["pickaxe_grade"] == original_grade
                        and not mu2["pickaxe_broken"]):
                    if random.random() < PICK_DATA[original_grade]["destroy"]:
                        await conn.execute("UPDATE mining_users SET pickaxe_broken=TRUE WHERE id=$1", str(uid))
                        destroyed = True

        # 미닝 로그 기록
        mu = await get_mine_user(str(uid))
        await add_mining_log(str(uid), "mine_ore",
            f"{ore['ore_name']} ({ore['ore_grade']}) {ore['weight']}g {ore['value']}원", mu)

        # 신화+ 컷씬 (별도 메시지로)
        if ore["ore_grade"] in ("신화", "전설", "미친", "???"):
            await _ore_cutscene(interaction, ore, uid)
        else:
            inv = await get_mine_inv(str(uid))
            gd = ORE_GRADES[ore["ore_grade"]]
            embed = discord.Embed(
                title="⛏️ 광질 완료!",
                color=gd["color"] if not destroyed else 0xFF0000)
            embed.add_field(name="획득 광석",
                value=f"{gd['emoji']} **{ore['ore_name']}** ({ore['ore_grade']}등급)", inline=False)
            embed.add_field(name="무게", value=f"{ore['weight']:,}g", inline=True)
            embed.add_field(name="가치", value=f"{ore['value']:,}원", inline=True)
            embed.add_field(name="가방", value=f"{len(inv)}/{cap}", inline=True)
            if destroyed:
                rep = PICK_DATA[original_grade]["repair"]
                embed.add_field(name="💥 곡괭이 파괴!",
                    value=f"**{original_name}**이(가) 파괴됐어요!\n`/수리` 로 **{rep:,}원** 내고 수리하세요.", inline=False)
            await _send_message(interaction, content=f"<@{uid}>", embed=embed)
            if destroyed:
                await add_mining_log(str(uid), "pick_broken", original_name, mu)

        # 칭호 체크
        _, hidden_new = await check_and_grant_titles(str(uid))
        if hidden_new and interaction.channel:
            try:
                user = await bot.fetch_user(uid)
                for ht in hidden_new:
                    await interaction.channel.send(embed=build_hidden_title_unlock_embed(ht, user))
            except Exception: pass

    except asyncio.CancelledError:
        raise
    except Exception as err:
        try: await _send_message(interaction, content=f"⚠️ 광질 중 오류 발생. ({err})")
        except Exception: pass
    finally:
        # 세션 정리: task_ref가 비어있어도 현재 실행 태스크 기준으로 정리
        self_task = task_ref[0] if task_ref else asyncio.current_task()
        if mining_sessions.get(uid) is self_task:
            mining_sessions.pop(uid, None)
        else:
            # 세션이 완료된 태스크를 가리키고 있으면 무조건 정리
            stored = mining_sessions.get(uid)
            if stored is not None and (stored.done() if hasattr(stored, 'done') else False):
                mining_sessions.pop(uid, None)

# ══════════════════════════════════════════════════════════════════════════════
#  슬래시 명령어: /광질
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="광질", description="⛏️ 곡괭이로 광석을 채굴합니다!")
async def slash_mine(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    uid = interaction.user.id; sid = str(uid)

    if uid in mining_sessions:
        return await interaction.followup.send("⚠️ 이미 광질 중이에요! 먼저 끝날 때까지 기다려주세요.", ephemeral=True)
    mining_sessions[uid] = asyncio.current_task()  # 선점

    try:
        mu = await get_mine_user(sid)
        if not mu["pickaxe_name"]:
            mining_sessions.pop(uid, None)
            await interaction.followup.send("❌ 곡괭이가 없어요! `/곡괭이뽑기` 로 곡괭이를 먼저 뽑으세요.", ephemeral=True)
            return
        if mu["pickaxe_broken"]:
            rep = PICK_DATA[mu["pickaxe_grade"]]["repair"]
            mining_sessions.pop(uid, None)
            await interaction.followup.send(f"❌ 곡괭이가 파괴됐어요! `/수리` 로 **{rep:,}원** 내고 수리하세요.", ephemeral=True)
            return
        inv = await get_mine_inv(sid)
        cap = BAG_DATA[mu["bag_type"]]["cap"]
        if len(inv) >= cap:
            mining_sessions.pop(uid, None)
            await interaction.followup.send("❌ 가방이 꽉 찼어요! `/판매` 로 광석을 팔거나 더 좋은 가방을 뽑으세요.", ephemeral=True)
            return

        grade = mu["pickaxe_grade"]; name = mu["pickaxe_name"]
        base_time = PICK_DATA[grade]["time"]
        wait_sec = max(0.5, base_time * mu["mining_speed"])
        pd = PICK_DATA[grade]

        bal = await get_balance(bot.db, sid)
        mine_cnt = await bot.db.fetchrow("SELECT total_mined FROM mining_users WHERE id=$1", sid)
        sold_cnt = await bot.db.fetchrow("SELECT total_sold FROM mining_users WHERE id=$1", sid)
        ore_disc = await get_discovered_ores(sid)

        embed = discord.Embed(title="⛏️ 광질 시작!", color=pd["color"])
        embed.description = (
            f"{pd['emoji']} **{name}** ({grade}등급)\n"
            f"⏱ 소요: **{wait_sec:.1f}초** | 파괴확률: **{pd['destroy']*100:.0f}%**"
        )
        embed.add_field(name="💰 잔액", value=f"{bal:,}원", inline=True)
        embed.add_field(name="⛏️ 총 채굴", value=f"{mine_cnt['total_mined'] if mine_cnt else 0:,}개", inline=True)
        embed.add_field(name="📖 도감", value=f"{len(ore_disc)}/{len(ALL_ORES)}개", inline=True)
        embed.add_field(name="🎒 가방", value=f"{len(inv)}/{cap}개", inline=True)
        embed.set_footer(text="채굴이 완료되면 알림을 보내드릴게요!")
        await interaction.followup.send(embed=embed)

        task_ref: list = []
        task = asyncio.create_task(_mine_task(interaction, uid, grade, name, task_ref))
        task_ref.append(task)
        mining_sessions[uid] = task
    except Exception:
        mining_sessions.pop(uid, None)
        raise

# ══════════════════════════════════════════════════════════════════════════════
#  슬래시 명령어: /가방 (번호 표시)
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="가방", description="🎒 내 가방(인벤토리)을 확인합니다")
async def slash_bag(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    mu  = await get_mine_user(uid)
    inv = await get_mine_inv(uid)
    cap = BAG_DATA[mu["bag_type"]]["cap"]
    bd  = BAG_DATA[mu["bag_type"]]

    embed = discord.Embed(
        title=f"🎒 {interaction.user.display_name}의 가방",
        description=f"{bd['emoji']} **{mu['bag_type']}** — {len(inv)}/{cap}개\n\n*/판매 [번호] [갯수] 로 판매 | /판매 0 0 = 전체 판매*",
        color=bd["color"])

    if not inv:
        embed.add_field(name="📭 비어있음", value="광석이 없어요. `/광질` 로 채굴을 시작해보세요!", inline=False)
    else:
        groups: dict[str, list] = {}
        for idx, ore in enumerate(inv, 1):
            groups.setdefault(ore["ore_grade"], []).append((idx, ore))
        for grade in list(ORE_GRADES.keys()):
            if grade not in groups: continue
            gd = ORE_GRADES[grade]
            lines = [f"`#{idx}` {gd['emoji']} **{o['ore_name']}** — {o['weight']:,}g ({o['value']:,}원)"
                     for idx, o in groups[grade]]
            field_val = "\n".join(lines)
            if len(field_val) > 1024: field_val = field_val[:1020] + "..."
            embed.add_field(name=f"{gd['emoji']} {grade}등급 ({len(groups[grade])}개)",
                            value=field_val, inline=False)
    await interaction.followup.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  슬래시 명령어: /판매 [번호] [갯수]
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="판매", description="💰 가방의 광석을 판매합니다 (번호=0이면 전체)")
@app_commands.describe(번호="판매할 광석 번호 (/가방 확인, 0=전체)", 갯수="판매할 개수 (기본 1, 0=해당 번호부터 전부)")
async def slash_sell(interaction: discord.Interaction, 번호: int = 0, 갯수: int = 1):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    uid = str(interaction.user.id)
    inv = await get_mine_inv(uid)
    if not inv:
        return await interaction.followup.send("❌ 가방이 비어있어요!", ephemeral=True)

    if 번호 == 0:
        # 전체 판매
        target_ids = None  # 전체
    else:
        if 번호 < 1 or 번호 > len(inv):
            return await interaction.followup.send(f"❌ 번호는 1~{len(inv)} 범위여야 해요.", ephemeral=True)
        if 갯수 == 0:
            # 해당 번호부터 끝까지
            target_rows = inv[번호-1:]
        else:
            갯수 = max(1, 갯수)
            target_rows = inv[번호-1:번호-1+갯수]
        target_ids = [r['id'] for r in target_rows]

    async with bot.db.acquire() as conn:
        async with conn.transaction():
            if target_ids is None:
                deleted_rows = await conn.fetch(
                    "DELETE FROM mining_inventory WHERE user_id=$1 RETURNING value", uid)
            else:
                deleted_rows = await conn.fetch(
                    "DELETE FROM mining_inventory WHERE id=ANY($1::int[]) AND user_id=$2 RETURNING value",
                    target_ids, uid)
            if not deleted_rows:
                return await interaction.followup.send("❌ 판매할 광석이 없어요!", ephemeral=True)
            total = sum(r["value"] for r in deleted_rows)
            count = len(deleted_rows)
            await conn.execute(
                "INSERT INTO users (id,balance) VALUES($1,$2) ON CONFLICT (id) DO UPDATE SET balance=users.balance+$2",
                uid, total)
            await conn.execute(
                "UPDATE mining_users SET total_sold=total_sold+$1 WHERE id=$2", count, uid)
    await add_log(uid, "광석_판매", total, f"{count}개 판매")
    mu = await get_mine_user(uid)
    await add_mining_log(uid, "sell", f"{count}개 {total:,}원", mu)

    embed = discord.Embed(title="💰 광석 판매 완료!", color=discord.Color.gold())
    embed.add_field(name="판매 수량", value=f"{count}개", inline=True)
    embed.add_field(name="총 수익", value=f"{total:,}원", inline=True)
    await interaction.followup.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  슬래시 명령어: /광산정보
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="광산정보", description="⛏️ 내 광산 프로필을 확인합니다")
async def slash_mine_profile(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    mu   = await get_mine_user(uid)
    inv  = await get_mine_inv(uid)
    cap  = BAG_DATA[mu["bag_type"]]["cap"]
    disc_o = await get_discovered_ores(uid)
    disc_p = await get_discovered_picks(uid)
    bal    = await get_balance(bot.db, uid)

    embed = discord.Embed(title=f"⛏️ {interaction.user.display_name}의 광산", color=0xD4AF37)
    embed.set_thumbnail(url=interaction.user.display_avatar.url)

    if mu["pickaxe_name"]:
        pd = PICK_DATA[mu["pickaxe_grade"]]
        pick_str = f"{pd['emoji']} **{mu['pickaxe_name']}** ({mu['pickaxe_grade']}등급)"
        if mu["pickaxe_broken"]: pick_str += " 💥 파괴됨"
    else:
        pick_str = "없음 — `/곡괭이뽑기`로 구매하세요"

    embed.add_field(name="⛏️ 곡괭이", value=pick_str, inline=False)
    bd = BAG_DATA[mu["bag_type"]]
    embed.add_field(name="🎒 가방", value=f"{bd['emoji']} **{mu['bag_type']}** ({len(inv)}/{cap}개)", inline=True)
    embed.add_field(name="💰 잔액", value=f"{bal:,}원", inline=True)
    embed.add_field(name="📈 채굴 속도", value=f"×{mu['mining_speed']:.2f}", inline=True)
    embed.add_field(name="⛏️ 총 채굴", value=f"{mu['total_mined']:,}개", inline=True)
    embed.add_field(name="💸 총 판매", value=f"{mu['total_sold']:,}개", inline=True)
    embed.add_field(name="🔄 광질 중", value="예" if interaction.user.id in mining_sessions else "아니오", inline=True)
    embed.add_field(name="📖 광석 도감", value=f"{len(disc_o)}/{len(ALL_ORES)}개", inline=True)
    embed.add_field(name="🔨 곡괭이 도감", value=f"{len(disc_p)}/{len(ALL_PICKS)}개", inline=True)

    # 최근 광산 로그 3건
    recent_logs = await bot.db.fetch(
        "SELECT log_type,detail,created_at FROM mining_logs WHERE user_id=$1 ORDER BY created_at DESC LIMIT 3", uid)
    if recent_logs:
        log_lines = [f"`{r['created_at'].strftime('%m/%d %H:%M')}` {r['log_type']}: {r['detail'] or ''}" for r in recent_logs]
        embed.add_field(name="📋 최근 광산 기록", value="\n".join(log_lines), inline=False)

    await interaction.followup.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  슬래시 명령어: /광석도감 /곡괭이도감
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="광석도감", description="📖 광석 도감을 확인합니다")
async def slash_ore_book(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    found = await get_discovered_ores(uid)
    embed = discord.Embed(title="📖 광석 도감", color=0xD4AF37)
    for grade, ores in ORE_LIST.items():
        gd = ORE_GRADES[grade]
        lines = [f"{gd['emoji']} **{o}**" if o in found else "❓ ???" for o in ores]
        embed.add_field(name=f"{gd['emoji']} {grade}등급 ({len(ores)}종)",
                        value="\n".join(lines), inline=True)
    embed.set_footer(text=f"발견: {len(found)}/{len(ALL_ORES)}개")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="곡괭이도감", description="🔨 곡괭이 도감을 확인합니다")
async def slash_pick_book(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    found = await get_discovered_picks(uid)
    embed = discord.Embed(title="🔨 곡괭이 도감", color=0x7B68EE)
    for grade, pd in PICK_DATA.items():
        lines = [f"{pd['emoji']} **{p}**" if p in found else "❓ ???" for p in pd["items"]]
        embed.add_field(name=f"{pd['emoji']} {grade}등급", value="\n".join(lines), inline=True)
    embed.set_footer(text=f"발견: {len(found)}/{len(ALL_PICKS)}개")
    await interaction.followup.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  슬래시 명령어: /곡괭이뽑기 (버그 수정 완전 재작성)
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="곡괭이뽑기", description="🎰 곡괭이를 뽑습니다! 1회 5,000원")
async def slash_gacha_pick(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    uid = str(interaction.user.id)
    COST = 5_000

    # 1. 잔액 확인 + 차감
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT balance FROM users WHERE id=$1 FOR UPDATE", uid)
            if not row or row["balance"] < COST:
                return await interaction.followup.send("❌ 잔액이 부족해요! 5,000원이 필요합니다.", ephemeral=True)
            await conn.execute("UPDATE users SET balance=balance-$1 WHERE id=$2", COST, uid)

    # 2. 곡괭이 뽑기
    grade, name = await roll_pickaxe()
    pd = PICK_DATA[grade]

    # 3. DB 업데이트 먼저! (컷씬 전에 장착 완료 → 컷씬 중 오류나도 곡괭이 보존)
    mu = await get_mine_user(uid)
    prev_name = mu.get("pickaxe_name")
    prev_info = f"\n(기존 **{prev_name}** 대체됨)" if prev_name else ""

    async with bot.db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO mining_users (id,pickaxe_grade,pickaxe_name,pickaxe_broken) VALUES($1,$2,$3,FALSE)"
                " ON CONFLICT (id) DO UPDATE SET pickaxe_grade=$2, pickaxe_name=$3, pickaxe_broken=FALSE",
                uid, grade, name)
            await conn.execute(
                "INSERT INTO mining_discovered_picks(user_id,pick_name) VALUES($1,$2) ON CONFLICT DO NOTHING",
                uid, name)

    await add_log(uid, "곡괭이뽑기", -COST, f"{grade}등급 {name}")
    mu_new = await get_mine_user(uid)
    await add_mining_log(uid, "pick_gacha", f"{grade}등급 {name}", mu_new)

    # 4. 컷씬 (신화 이상) — 이미 DB에 저장됐으므로 오류 나도 곡괭이는 보존됨
    intro_msg = None
    if grade in ("신화", "전설", "미친"):
        try:
            if grade == "신화":
                embed_intro = discord.Embed(
                    title="🔮 주위의 마나가 서서히 격동합니다...",
                    description="```\n✨ [SYSTEM]: 차원의 벽에 미세한 균열이 발생합니다.\n```\n푸른 마나의 광운이 뽑기 기계를 감싸기 시작합니다...!",
                    color=0x7B68EE)
                intro_msg = await interaction.followup.send(embed=embed_intro, wait=True)
                await asyncio.sleep(1.5)
            elif grade == "전설":
                embed_intro = discord.Embed(
                    title="⚡⚡ 크르릉... 쿠르릉! 대지가 격렬하게 진동합니다! ⚡⚡",
                    description="```diff\n+ [WARNING]: 서버 코어가 비정상적인 에너지를 감지했습니다.\n```\n🔱 천지가 개벽하며 눈을 멀게 할 **황금빛 차원의 폭풍**이 몰아칩니다!!!",
                    color=0xFFD700)
                intro_msg = await interaction.followup.send(embed=embed_intro, wait=True)
                await asyncio.sleep(2.8)
            elif grade == "미친":
                embed_intro = discord.Embed(
                    title="🌌 🚨🚨 [CRITICAL ERROR] 시공간 완전 붕괴 경보!!! 🚨🚨 🌌",
                    description="```\n🚨 SYSTEM OVERHEAT: 우주의 근원 마력이 폭주합니다!\n🚨 EMERGENCY: 뽑기 기계가 형체를 잃고 녹아내립니다!\n```\n🔥 **이것은 현실인가 환상인가!** 🔥\n차원의 한계를 초월한 무언가가 시공간을 찢고 강림하려고 합니다!!!",
                    color=0xFF00FF)
                intro_msg = await interaction.followup.send(embed=embed_intro, wait=True)
                await asyncio.sleep(4.5)

            embed_result = discord.Embed(
                title=f"🎉 [대성공] {pd['emoji']} {grade} 등급 장비 강림! 🎉",
                description=f"마침내 주인을 알아본 전설의 무기,\n👑 **「 {name} 」** 을(를) 획득하셨습니다!!",
                color=pd["color"])
            embed_result.add_field(name="⛏️ 채굴 쿨타임", value=f"`{pd['time']}초`", inline=True)
            embed_result.add_field(name="💥 파괴 확률", value=f"`{int(pd['destroy']*100)}%`", inline=True)
            embed_result.add_field(name="💰 수리비", value=f"`{pd['repair']:,}원`", inline=True)
            embed_result.set_footer(text="이 곡괭이와 함께 광산을 지배하세요!")
            if intro_msg:
                await intro_msg.edit(embed=embed_result)
            else:
                await interaction.followup.send(embed=embed_result)
        except Exception:
            pass  # 컷씬 실패해도 곡괭이는 이미 DB에 저장됨
    else:
        embed_result = discord.Embed(
            title=f"🎰 뽑기 결과: {pd['emoji']} {grade} 등급",
            description=f"**「 {name} 」** 을(를) 획득했습니다.",
            color=pd["color"])
        embed_result.add_field(name="⛏️ 쿨타임", value=f"{pd['time']}초", inline=True)
        embed_result.add_field(name="💥 파괴 확률", value=f"{int(pd['destroy']*100)}%", inline=True)
        await interaction.followup.send(embed=embed_result)

    # 5. 장착 완료 메시지
    confirm = discord.Embed(
        title="✅ 곡괭이 자동 장착 완료!",
        description=f"{pd['emoji']} **{name}** ({grade}등급){prev_info}",
        color=pd["color"])
    await interaction.followup.send(embed=confirm)

# ══════════════════════════════════════════════════════════════════════════════
#  슬래시 명령어: /가방뽑기 (신화+ 컷씬 추가)
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="가방뽑기", description="🎒 가방을 뽑습니다! 1회 5,000원")
async def slash_gacha_bag(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    uid = str(interaction.user.id)
    COST = 5_000

    async with bot.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT balance FROM users WHERE id=$1 FOR UPDATE", uid)
            if not row or row["balance"] < COST:
                return await interaction.followup.send("❌ 잔액이 부족해요! 5,000원이 필요합니다.", ephemeral=True)
            await conn.execute("UPDATE users SET balance=balance-$1 WHERE id=$2", COST, uid)

    bag_type = await roll_bag()
    bd = BAG_DATA[bag_type]
    mu = await get_mine_user(uid)
    prev_cap = BAG_DATA[mu["bag_type"]]["cap"]
    is_upgrade = bd["cap"] > prev_cap

    # 신화가방 이상 컷씬
    intro_msg = None
    grade_map = {"일반가방":"일반","레어가방":"레어","에픽가방":"에픽","신화가방":"신화","전설가방":"전설","미친가방":"미친"}
    bag_grade = grade_map.get(bag_type, "일반")

    if bag_grade in ("신화","전설","미친"):
        if bag_grade == "신화":
            embed_intro = discord.Embed(
                title="🎑✨ 신비로운 마나가 가방을 감쌉니다...",
                description="```\n[신화 등급 가방 출현 예고]\n```", color=0x7B68EE)
            intro_msg = await interaction.followup.send(embed=embed_intro, wait=True)
            await asyncio.sleep(1.5)
        elif bag_grade == "전설":
            embed_intro = discord.Embed(
                title="🏺⚡ 황금빛 빛기둥이 솟아오릅니다!!!",
                description="```diff\n+ [WARNING]: 전설 가방이 나타나려고 합니다!\n```", color=0xFFD700)
            intro_msg = await interaction.followup.send(embed=embed_intro, wait=True)
            await asyncio.sleep(2.5)
        elif bag_grade == "미친":
            embed_intro = discord.Embed(
                title="🌌🚨 [EMERGENCY] 우주 공간이 왜곡됩니다!!!",
                description="```\n🚨 미친 가방 강림 경보!!!\n차원의 틈새에서 가방이 출현하고 있습니다!!\n```", color=0xFF00FF)
            intro_msg = await interaction.followup.send(embed=embed_intro, wait=True)
            await asyncio.sleep(3.5)

        embed_result = discord.Embed(
            title=f"🎉 [대성공] {bd['emoji']} {bag_type} 강림!",
            description=f"**{bag_type}** 획득! 최대 **{bd['cap']:,}개** 수납 가능",
            color=bd["color"])
        embed_result.set_footer(text="가방이 자동 장착됐어요!")
        if intro_msg:
            await intro_msg.edit(embed=embed_result)
        else:
            await interaction.followup.send(embed=embed_result)
    else:
        upgrade_text = " 🆙 업그레이드!" if is_upgrade else (" (기존과 동일)" if bd["cap"]==prev_cap else " (📉 다운그레이드)")
        embed = discord.Embed(title="🎰 가방 뽑기 결과!", color=bd["color"])
        embed.add_field(name="획득 가방", value=f"{bd['emoji']} **{bag_type}** — {bd['cap']:,}개{upgrade_text}", inline=False)
        embed.set_footer(text="가방이 자동 장착됐어요!")
        await interaction.followup.send(embed=embed)

    await bot.db.execute("UPDATE mining_users SET bag_type=$1 WHERE id=$2", bag_type, uid)
    await add_log(uid, "가방뽑기", -COST, f"{bag_type} 획득")
    mu_new = await get_mine_user(uid)
    await add_mining_log(uid, "bag_gacha", f"{bag_type} 획득", mu_new)

# ══════════════════════════════════════════════════════════════════════════════
#  슬래시 명령어: /수리
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="수리", description="🔧 파괴된 곡괭이를 수리합니다")
async def slash_repair(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    uid = str(interaction.user.id)
    mu  = await get_mine_user(uid)
    if not mu["pickaxe_name"]:
        return await interaction.followup.send("❌ 곡괭이가 없어요!", ephemeral=True)
    if not mu["pickaxe_broken"]:
        return await interaction.followup.send("✅ 곡괭이가 멀쩡해요! 수리할 필요가 없어요.", ephemeral=True)
    rep_cost = PICK_DATA[mu["pickaxe_grade"]]["repair"]
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT balance FROM users WHERE id=$1 FOR UPDATE", uid)
            if not row or row["balance"] < rep_cost:
                return await interaction.followup.send(f"❌ 잔액이 부족해요! 수리비는 **{rep_cost:,}원**이에요.", ephemeral=True)
            await conn.execute("UPDATE users SET balance=balance-$1 WHERE id=$2", rep_cost, uid)
            await conn.execute("UPDATE mining_users SET pickaxe_broken=FALSE WHERE id=$1", uid)
    await add_log(uid, "곡괭이수리", -rep_cost, f"{mu['pickaxe_name']} 수리")
    mu_new = await get_mine_user(uid)
    await add_mining_log(uid, "repair", f"{mu['pickaxe_name']} 수리 {rep_cost:,}원", mu_new)
    pd = PICK_DATA[mu["pickaxe_grade"]]
    embed = discord.Embed(title="🔧 수리 완료!", color=pd["color"])
    embed.add_field(name="곡괭이", value=f"{pd['emoji']} **{mu['pickaxe_name']}**", inline=True)
    embed.add_field(name="수리비", value=f"{rep_cost:,}원", inline=True)
    embed.set_footer(text="이제 다시 /광질 을 할 수 있어요!")
    await interaction.followup.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  슬래시 명령어: /광산랭킹
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="광산랭킹", description="🏆 광산 채굴량 TOP 10")
async def slash_mine_ranking(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    rows = await bot.db.fetch(
        "SELECT id,total_mined,total_sold FROM mining_users ORDER BY total_mined DESC LIMIT 10")
    embed = discord.Embed(title="🏆 광산 채굴량 랭킹 TOP 10", color=discord.Color.gold())
    medals = {1:"🥇",2:"🥈",3:"🥉"}
    lines = []
    for i, row in enumerate(rows, 1):
        member = interaction.guild.get_member(int(row['id'])) if interaction.guild else None
        name = member.display_name if member else "알 수 없음"
        lines.append(f"{medals.get(i, f'**{i}위**')} {name} — 채굴 {row['total_mined']:,}개 | 판매 {row['total_sold']:,}개")
    embed.description = "\n".join(lines) or "아직 광질한 유저가 없어요."
    await interaction.followup.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  슬래시+프리픽스 명령어: /비교 — 두 유저 스탯 비교
# ══════════════════════════════════════════════════════════════════════════════
async def _build_compare_embed(guild, uid1: str, uid2: str,
                               name1: str, name2: str) -> discord.Embed:
    bal1 = await get_balance(bot.db, uid1)
    bal2 = await get_balance(bot.db, uid2)
    mu1  = await get_mine_user(uid1)
    mu2  = await get_mine_user(uid2)
    disc1 = await bot.db.fetchval(
        "SELECT COUNT(*) FROM mining_discovered_ores WHERE user_id=$1", uid1) or 0
    disc2 = await bot.db.fetchval(
        "SELECT COUNT(*) FROM mining_discovered_ores WHERE user_id=$1", uid2) or 0

    def cmp(v1, v2):
        if v1 > v2:   return "🏆", "➖"
        elif v1 < v2: return "➖", "🏆"
        else:         return "🤝", "🤝"

    def pick_str(mu):
        if not mu.get("pickaxe_name"): return "없음"
        pd  = PICK_DATA[mu["pickaxe_grade"]]
        brk = " 💔" if mu.get("pickaxe_broken") else ""
        return f"{pd['emoji']} {mu['pickaxe_name']}{brk}"

    def bag_str(mu):
        bt = mu.get("bag_type", "일반가방")
        bd = BAG_DATA.get(bt, BAG_DATA["일반가방"])
        return f"{bd['emoji']} {bt} ({bd['cap']}칸)"

    def speed_str(mu):
        s = mu.get("mining_speed", 1.0)
        if s < 1.0:   return f"⚡ {s}x (빠름)"
        elif s > 1.0: return f"🐢 {s}x (느림)"
        return "보통 (1.0x)"

    bc1, bc2 = cmp(bal1, bal2)
    mc1, mc2 = cmp(mu1.get("total_mined", 0), mu2.get("total_mined", 0))
    sc1, sc2 = cmp(mu1.get("total_sold",  0), mu2.get("total_sold",  0))
    dc1, dc2 = cmp(disc1, disc2)

    embed = discord.Embed(title="⚔️ 광산·경제 스탯 비교", color=discord.Color.orange())
    # 헤더
    embed.add_field(name=f"👤 {name1}", value="\u200b", inline=True)
    embed.add_field(name="VS",         value="\u200b", inline=True)
    embed.add_field(name=f"👤 {name2}", value="\u200b", inline=True)
    # 잔액
    embed.add_field(name=f"{bc1} 💰 잔액", value=f"`{bal1:,}원`", inline=True)
    embed.add_field(name="\u200b",         value="\u200b",         inline=True)
    embed.add_field(name=f"{bc2} 💰 잔액", value=f"`{bal2:,}원`", inline=True)
    # 총 채굴
    embed.add_field(name=f"{mc1} ⛏️ 총 채굴", value=f"`{mu1.get('total_mined',0):,}개`", inline=True)
    embed.add_field(name="\u200b",             value="\u200b",                             inline=True)
    embed.add_field(name=f"{mc2} ⛏️ 총 채굴", value=f"`{mu2.get('total_mined',0):,}개`", inline=True)
    # 총 판매
    embed.add_field(name=f"{sc1} 💎 총 판매", value=f"`{mu1.get('total_sold',0):,}개`", inline=True)
    embed.add_field(name="\u200b",             value="\u200b",                             inline=True)
    embed.add_field(name=f"{sc2} 💎 총 판매", value=f"`{mu2.get('total_sold',0):,}개`", inline=True)
    # 광석 도감
    total_ores = len(ALL_ORES)
    embed.add_field(name=f"{dc1} 📖 도감", value=f"`{disc1}/{total_ores}`", inline=True)
    embed.add_field(name="\u200b",         value="\u200b",                  inline=True)
    embed.add_field(name=f"{dc2} 📖 도감", value=f"`{disc2}/{total_ores}`", inline=True)
    # 곡괭이
    embed.add_field(name="⛏️ 곡괭이", value=pick_str(mu1), inline=True)
    embed.add_field(name="\u200b",     value="\u200b",      inline=True)
    embed.add_field(name="⛏️ 곡괭이", value=pick_str(mu2), inline=True)
    # 가방
    embed.add_field(name="🎒 가방", value=bag_str(mu1), inline=True)
    embed.add_field(name="\u200b",   value="\u200b",     inline=True)
    embed.add_field(name="🎒 가방", value=bag_str(mu2), inline=True)
    # 채굴 속도
    embed.add_field(name="⚡ 속도", value=speed_str(mu1), inline=True)
    embed.add_field(name="\u200b",   value="\u200b",       inline=True)
    embed.add_field(name="⚡ 속도", value=speed_str(mu2), inline=True)

    embed.set_footer(text=f"🏆=우위  ➖=열세  🤝=동점 | {MADE_BY_TAG}")
    return embed

@bot.tree.command(name="비교", description="⚔️ 두 유저의 광산·경제 스탯을 비교합니다")
@app_commands.describe(유저1="비교할 첫 번째 유저", 유저2="비교할 두 번째 유저")
async def slash_compare(interaction: discord.Interaction,
                        유저1: discord.Member, 유저2: discord.Member):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    embed = await _build_compare_embed(
        interaction.guild,
        str(유저1.id), str(유저2.id),
        유저1.display_name, 유저2.display_name)
    await interaction.followup.send(embed=embed)

@bot.command(name="비교")
async def prefix_compare(ctx, 유저1: discord.Member, 유저2: discord.Member):
    if not await bot_guard(ctx): return
    embed = await _build_compare_embed(
        ctx.guild,
        str(유저1.id), str(유저2.id),
        유저1.display_name, 유저2.display_name)
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  슬래시 명령어: /운영통계
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="운영통계", description="📊 봇 운영 전체 통계를 확인합니다")
async def slash_op_stats(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    await interaction.response.defer()
    db = bot.db

    total_balance = await db.fetchrow("SELECT COALESCE(SUM(balance),0) as total FROM users")
    richest = await db.fetchrow("SELECT id, balance FROM users ORDER BY balance DESC LIMIT 1")
    user_cnt = await db.fetchrow("SELECT COUNT(*) as cnt FROM users")
    gamble_out = await db.fetchrow(
        "SELECT COALESCE(SUM(amount),0) as out FROM transactions WHERE type='도박_승' AND amount>0")
    gamble_in = await db.fetchrow(
        "SELECT COALESCE(SUM(ABS(amount)),0) as inp FROM transactions WHERE type='도박_패'")
    total_mined = await db.fetchrow("SELECT COALESCE(SUM(total_mined),0) as t FROM mining_users")
    inv_rows = await db.fetch("SELECT ore_grade, COUNT(*) as cnt FROM mining_inventory GROUP BY ore_grade ORDER BY cnt DESC")

    embed = discord.Embed(title="📊 봇 운영 통계", color=discord.Color.blurple())

    # 경제
    richest_name = "알 수 없음"
    if richest:
        try:
            u = await bot.fetch_user(int(richest['id']))
            richest_name = u.display_name
        except Exception: pass
    embed.add_field(name="💰 총 유통 금액", value=f"{total_balance['total']:,}원", inline=True)
    embed.add_field(name="👑 최고 부자",
        value=f"{richest_name}\n{richest['balance']:,}원" if richest else "없음", inline=True)
    embed.add_field(name="👤 총 유저 수", value=f"{user_cnt['cnt']:,}명", inline=True)

    # 도박
    embed.add_field(name="🎲 도박 지급 총액", value=f"{gamble_out['out']:,}원", inline=True)
    embed.add_field(name="🎲 도박 수거 총액", value=f"{gamble_in['inp']:,}원", inline=True)
    net_gamble = gamble_out['out'] - gamble_in['inp']
    embed.add_field(name="📊 도박 순 손익",
        value=f"{'▲' if net_gamble>=0 else '▼'} {abs(net_gamble):,}원", inline=True)

    # 광질
    embed.add_field(name="⛏️ 총 채굴 수", value=f"{total_mined['t']:,}개", inline=True)
    if inv_rows:
        inv_summary = "\n".join(f"{ORE_GRADES[r['ore_grade']]['emoji']} {r['ore_grade']}: {r['cnt']:,}개"
                                for r in inv_rows[:5])
        embed.add_field(name="🎒 가방 광석 현황 (상위5)", value=inv_summary, inline=False)

    embed.set_footer(text=MADE_BY_TAG)
    await interaction.followup.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  관리자 모드
# ══════════════════════════════════════════════════════════════════════════════
@bot.command(name='관리자모드')
async def prefix_admin_mode(ctx, mode: str = ""):
    if not await bot_guard(ctx): return
    if not is_admin_or_op(ctx) and not is_owner(ctx):
        return await ctx.send("❌ 권한이 없어요.", delete_after=5)
    uid = str(ctx.author.id)
    mode = mode.lower()
    # on/off 명시 시 강제 설정, 아무것도 없으면 토글
    if mode in ("on", "켜기", "활성화"):
        want_on = True
    elif mode in ("off", "끄기", "비활성화"):
        want_on = False
    else:
        want_on = uid not in admin_mode_users  # 토글
    if want_on:
        admin_mode_users.add(uid)
        embed = discord.Embed(title="🔓 관리자 모드 ON",
            description="관리자 명령어가 활성화됐어요.\n끄려면 `!관리자모드 off` 또는 `!관리자모드` 재입력",
            color=discord.Color.red())
    else:
        admin_mode_users.discard(uid)
        embed = discord.Embed(title="🔒 관리자 모드 OFF",
            description="관리자 모드가 **해제**되었습니다.", color=discord.Color.dark_gray())
    await ctx.send(embed=embed)

def _owner_or_admin_check(ctx, target: discord.Member | None = None) -> str | None:
    """봇 개발자 보호. 문제가 있으면 에러 문자열 반환."""
    # 발령자 본인이 봇 개발자면 → 자기 자신에게 명령어 적용 허용
    if target and target.id == BOT_OWNER_ID and ctx.author.id != BOT_OWNER_ID:
        return "봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다."
    if not admin_check(ctx):
        return "🔒 관리자 모드가 꺼져 있어요. `!관리자모드`로 먼저 활성화하세요."
    return None

# ══════════════════════════════════════════════════════════════════════════════
#  경제 관리자 명령어 (다중 유저 지원)
# ══════════════════════════════════════════════════════════════════════════════
@bot.command(name='돈추가')
@commands.has_permissions(administrator=True)
async def prefix_add_money(ctx, *args):
    if not await bot_guard(ctx): return
    err = _owner_or_admin_check(ctx)
    if err: return await ctx.send(err, delete_after=5)
    if len(args) < 2: return await ctx.send("사용법: `!돈추가 @유저1 @유저2... 금액`")
    try: amount = int(args[-1])
    except ValueError: return await ctx.send("❌ 마지막 인자는 금액이어야 해요.")
    if amount <= 0: return await ctx.send("❌ 1원 이상만 추가 가능해요.")
    targets = []
    for arg in args[:-1]:
        try: targets.append(await commands.MemberConverter().convert(ctx, arg))
        except Exception: return await ctx.send(f"❌ 유저를 찾을 수 없어요: {arg}")
    # owner protection (봇 개발자 본인이 자신에게 사용하는 건 허용)
    for t in targets:
        if t.id == BOT_OWNER_ID and ctx.author.id != BOT_OWNER_ID:
            return await ctx.send("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.")
    for target in targets:
        await bot.db.execute(
            "INSERT INTO users (id,balance) VALUES($1,$2) ON CONFLICT (id) DO UPDATE SET balance=users.balance+$2",
            str(target.id), amount)
        await add_log(str(target.id), "관리자_추가", amount, f"관리자 {ctx.author.display_name} 지급")
    names = ", ".join(t.display_name for t in targets)
    embed = discord.Embed(title="✅ 돈 추가 완료", color=discord.Color.green())
    embed.add_field(name="대상", value=names, inline=True)
    embed.add_field(name="추가 금액", value=f"+{amount:,}원", inline=True)
    await ctx.send(embed=embed)

@bot.command(name='돈차감')
@commands.has_permissions(administrator=True)
async def prefix_sub_money(ctx, *args):
    if not await bot_guard(ctx): return
    err = _owner_or_admin_check(ctx)
    if err: return await ctx.send(err, delete_after=5)
    if len(args) < 2: return await ctx.send("사용법: `!돈차감 @유저1 @유저2... 금액`")
    try: amount = int(args[-1])
    except ValueError: return await ctx.send("❌ 마지막 인자는 금액이어야 해요.")
    if amount <= 0: return await ctx.send("❌ 1원 이상만 차감 가능해요.")
    targets = []
    for arg in args[:-1]:
        try: targets.append(await commands.MemberConverter().convert(ctx, arg))
        except Exception: return await ctx.send(f"❌ 유저를 찾을 수 없어요: {arg}")
    for t in targets:
        if t.id == BOT_OWNER_ID and ctx.author.id != BOT_OWNER_ID:
            return await ctx.send("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.")
    for target in targets:
        cur = await get_balance(bot.db, str(target.id))
        deduct = min(amount, cur)
        await bot.db.execute("UPDATE users SET balance=balance-$1 WHERE id=$2", deduct, str(target.id))
        await add_log(str(target.id), "관리자_차감", -deduct, f"관리자 {ctx.author.display_name} 차감")
    names = ", ".join(t.display_name for t in targets)
    embed = discord.Embed(title="🔧 돈 차감 완료", color=discord.Color.orange())
    embed.add_field(name="대상", value=names, inline=True)
    embed.add_field(name="차감 금액", value=f"-{amount:,}원", inline=True)
    await ctx.send(embed=embed)

@bot.command(name='공지')
@commands.has_permissions(administrator=True)
async def prefix_announce(ctx, *, message: str):
    if not await bot_guard(ctx): return
    try: await ctx.message.delete()
    except Exception: pass
    embed = discord.Embed(title="📢 공지사항", description=message, color=discord.Color.blurple())
    embed.set_footer(text=f"공지 by {ctx.author.display_name}")
    await ctx.send(embed=embed)

@bot.command(name='로그확인')
@commands.has_permissions(administrator=True)
async def prefix_log(ctx, target: discord.Member = None):
    if not await bot_guard(ctx): return
    err = _owner_or_admin_check(ctx)
    if err: return await ctx.send(err, delete_after=5)
    user = target or ctx.author
    uid = str(user.id)
    rows = await bot.db.fetch(
        "SELECT type,amount,detail,created_at FROM transactions WHERE user_id=$1 ORDER BY created_at DESC LIMIT 15", uid)
    await ctx.send(embed=build_log_embed(user, rows))

@bot.command(name='유저정보')
@commands.has_permissions(administrator=True)
async def prefix_user_info(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    err = _owner_or_admin_check(ctx)
    if err: return await ctx.send(err, delete_after=5)
    uid = str(target.id)
    bal = await get_balance(bot.db, uid)
    att = await bot.db.fetchrow("SELECT last_date,streak FROM attendance WHERE id=$1", uid)
    tx_count = await bot.db.fetchrow("SELECT COUNT(*) as cnt FROM transactions WHERE user_id=$1", uid)
    mu = await get_mine_user(uid)
    warns = await get_warn_count(bot.db, uid)
    embed = discord.Embed(title=f"👤 {target.display_name} 유저 정보", color=discord.Color.blurple())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="💰 잔액", value=f"{bal:,}원", inline=True)
    embed.add_field(name="🆔 ID", value=f"`{uid}`", inline=True)
    embed.add_field(name="⚠️ 경고", value=f"**{warns}회**" if warns else "없음 ✅", inline=True)
    if att:
        embed.add_field(name="🎁 출석 스트릭", value=f"{att['streak']}일 연속 (마지막: {att['last_date']})", inline=False)
    embed.add_field(name="📋 총 거래 수", value=f"{tx_count['cnt']}건", inline=True)
    # 광질 정보
    if mu["pickaxe_name"]:
        embed.add_field(name="⛏️ 곡괭이",
            value=f"{PICK_DATA[mu['pickaxe_grade']]['emoji']} {mu['pickaxe_name']} ({mu['pickaxe_grade']}등급)"
                  + (" 💥파괴됨" if mu['pickaxe_broken'] else ""), inline=True)
    embed.add_field(name="🎒 가방", value=f"{BAG_DATA[mu['bag_type']]['emoji']} {mu['bag_type']}", inline=True)
    embed.add_field(name="⛏️ 총 채굴", value=f"{mu['total_mined']:,}개", inline=True)
    is_banned = str(uid) in bot_banned_cache
    is_op = str(uid) in bot_ops_cache
    status_parts = []
    if is_banned: status_parts.append("🚫 봇벤")
    if is_op: status_parts.append("⭐ OP")
    if status_parts: embed.add_field(name="상태", value=" | ".join(status_parts), inline=True)
    await ctx.send(embed=embed)

@bot.command(name='초기화')
@commands.has_permissions(administrator=True)
async def prefix_reset(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    err = _owner_or_admin_check(ctx, target)
    if err: return await ctx.send(err, delete_after=5)
    uid = str(target.id)
    prev_bal = await get_balance(bot.db, uid)
    await bot.db.execute("UPDATE users SET balance=0 WHERE id=$1", uid)
    await add_log(uid, "관리자_초기화", -prev_bal, f"관리자 {ctx.author.display_name} 잔액 초기화")
    embed = discord.Embed(title="🗑️ 잔액 초기화 완료", color=discord.Color.dark_red())
    embed.add_field(name="대상", value=target.display_name, inline=True)
    embed.add_field(name="초기화 전 잔액", value=f"{prev_bal:,}원", inline=True)
    await ctx.send(embed=embed)

@bot.command(name='스탯초기화')
@commands.has_permissions(administrator=True)
async def prefix_reset_stats(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    err = _owner_or_admin_check(ctx, target)
    if err: return await ctx.send(err, delete_after=5)
    uid = str(target.id)
    cnt_row = await bot.db.fetchrow("SELECT COUNT(*) as cnt FROM transactions WHERE user_id=$1", uid)
    await bot.db.execute("DELETE FROM transactions WHERE user_id=$1", uid)
    await bot.db.execute("DELETE FROM attendance WHERE id=$1", uid)
    embed = discord.Embed(title="🔄 스탯 초기화 완료", color=discord.Color.orange())
    embed.add_field(name="대상", value=target.display_name, inline=True)
    embed.add_field(name="삭제된 기록", value=f"{cnt_row['cnt'] if cnt_row else 0}건", inline=True)
    await ctx.send(embed=embed)

@bot.command(name='경고부여')
@commands.has_permissions(administrator=True)
async def prefix_warn(ctx, target: discord.Member, count: int = 1):
    if not await bot_guard(ctx): return
    err = _owner_or_admin_check(ctx, target)
    if err: return await ctx.send(err, delete_after=5)
    if count < 1: return await ctx.send("❌ 경고 횟수는 1 이상이어야 해요.")
    uid = str(target.id)
    await bot.db.execute(
        "INSERT INTO warnings (id,count) VALUES($1,$2) ON CONFLICT (id) DO UPDATE SET count=warnings.count+$2",
        uid, count)
    new_count = await get_warn_count(bot.db, uid)
    embed = discord.Embed(title="⚠️ 경고 부여",
        description=f"**{target.display_name}** 님에게 경고 **{count}회** 부여했어요.\n현재 누적 경고: **{new_count}회**",
        color=discord.Color.orange())
    await ctx.send(embed=embed)

@bot.command(name='경고회수')
@commands.has_permissions(administrator=True)
async def prefix_unwarn(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    err = _owner_or_admin_check(ctx, target)
    if err: return await ctx.send(err, delete_after=5)
    uid = str(target.id)
    current = await get_warn_count(bot.db, uid)
    if current <= 0: return await ctx.send(f"❌ **{target.display_name}** 님의 경고가 없어요.")
    new_count = current - 1
    await bot.db.execute("UPDATE warnings SET count=$1 WHERE id=$2", new_count, uid)
    embed = discord.Embed(title="✅ 경고 회수",
        description=f"**{target.display_name}** 님의 경고 1회 회수했어요.\n현재 누적 경고: **{new_count}회**",
        color=discord.Color.green())
    await ctx.send(embed=embed)

@bot.command(name='도박최대베팅액')
@commands.has_permissions(administrator=True)
async def prefix_set_gamble_max(ctx, amount: int):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if amount <= 0: return await ctx.send("❌ 1원 이상으로 설정해주세요.")
    await set_max_bet("gamble_max_bet", amount)
    await ctx.send(f"✅ 도박 최대 베팅액: **{amount:,}원**")

@bot.command(name='슬롯최대베팅액')
@commands.has_permissions(administrator=True)
async def prefix_set_slot_max(ctx, amount: int):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if amount <= 0: return await ctx.send("❌ 1원 이상으로 설정해주세요.")
    await set_max_bet("slot_max_bet", amount)
    await ctx.send(f"✅ 슬롯 최대 베팅액: **{amount:,}원**")

@bot.command(name='칭호부여')
@commands.has_permissions(administrator=True)
async def prefix_grant_title(ctx, target: discord.Member, *, title_input: str):
    if not await bot_guard(ctx): return
    err = _owner_or_admin_check(ctx, target)
    if err: return await ctx.send(err, delete_after=5)
    parts = title_input.rsplit(None, 1)
    color: str | None = None; title: str = title_input
    if len(parts) == 2 and re.fullmatch(r'#[0-9A-Fa-f]{6}', parts[-1]):
        title = parts[0]; color = parts[-1].upper()
    await grant_title(bot.db, str(target.id), title, color, is_event=True)
    embed = discord.Embed(title="🎪 이벤트 칭호 부여 완료",
        color=hex_to_discord_color(color) if color else discord.Color.gold())
    embed.add_field(name="대상", value=target.display_name, inline=True)
    embed.add_field(name="칭호", value=title, inline=True)
    await ctx.send(embed=embed)

@bot.command(name='칭호삭제')
@commands.has_permissions(administrator=True)
async def prefix_delete_title(ctx, target: discord.Member, *, title: str):
    if not await bot_guard(ctx): return
    err = _owner_or_admin_check(ctx, target)
    if err: return await ctx.send(err, delete_after=5)
    uid = str(target.id)
    result = await bot.db.execute("DELETE FROM titles WHERE user_id=$1 AND title=$2", uid, title)
    if result == "DELETE 0": return await ctx.send(f"❌ 해당 칭호를 보유하고 있지 않아요.")
    await bot.db.execute("DELETE FROM equipped_title WHERE user_id=$1 AND title=$2", uid, title)
    embed = discord.Embed(title="🗑️ 칭호 삭제 완료", color=discord.Color.dark_red())
    embed.add_field(name="대상", value=target.display_name, inline=True)
    embed.add_field(name="삭제된 칭호", value=title, inline=True)
    await ctx.send(embed=embed)

@bot.command(name='칭호색상변경')
@commands.has_permissions(administrator=True)
async def prefix_change_title_color(ctx, target: discord.Member, color_code: str, *, title: str):
    if not await bot_guard(ctx): return
    err = _owner_or_admin_check(ctx, target)
    if err: return await ctx.send(err, delete_after=5)
    if not re.fullmatch(r'#[0-9A-Fa-f]{6}', color_code):
        return await ctx.send("❌ 색상 코드 형식이 잘못됐어요. 예: `#FF0000`")
    color_code = color_code.upper()
    result = await bot.db.execute("UPDATE titles SET color=$1 WHERE user_id=$2 AND title=$3", color_code, str(target.id), title)
    if result == "UPDATE 0": return await ctx.send(f"❌ 해당 칭호를 보유하고 있지 않아요.")
    await ctx.send(f"✅ **{title}** 칭호 색상 → `{color_code}`")

@bot.command(name='칭호전체획득')
@commands.has_permissions(administrator=True)
async def prefix_grant_all_titles(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    err = _owner_or_admin_check(ctx, target)
    if err: return await ctx.send(err, delete_after=5)
    uid = str(target.id); granted = 0
    for title, _ in TITLE_CONDITIONS:
        if await grant_title(bot.db, uid, title): granted += 1
    for ht in HIDDEN_TITLES:
        if await grant_title(bot.db, uid, ht): granted += 1
    embed = discord.Embed(title="🎖️ 전체 칭호 획득 완료", color=discord.Color.gold())
    embed.add_field(name="대상", value=target.display_name, inline=True)
    embed.add_field(name="새로 획득", value=f"{granted}개", inline=True)
    await ctx.send(embed=embed)

@bot.command(name='칭호초기화')
@commands.has_permissions(administrator=True)
async def prefix_reset_titles(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    err = _owner_or_admin_check(ctx, target)
    if err: return await ctx.send(err, delete_after=5)
    uid = str(target.id)
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM equipped_title WHERE user_id=$1", uid)
            cnt_row = await conn.fetchrow("SELECT COUNT(*) as cnt FROM titles WHERE user_id=$1", uid)
            await conn.execute("DELETE FROM titles WHERE user_id=$1", uid)
    embed = discord.Embed(title="🗑️ 칭호 초기화 완료", color=discord.Color.orange())
    embed.add_field(name="대상", value=target.display_name, inline=True)
    embed.add_field(name="삭제된 칭호 수", value=f"{cnt_row['cnt'] if cnt_row else 0}개", inline=True)
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  광질 관리자 명령어
# ══════════════════════════════════════════════════════════════════════════════
@bot.command(name="광질속도")
@commands.has_permissions(administrator=True)
async def prefix_mine_speed(ctx, member: discord.Member, speed: str):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if member.id == BOT_OWNER_ID and ctx.author.id != BOT_OWNER_ID:
        return await ctx.send("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.")

    if speed.lower() in ("reset", "초기화", "기본"):
        # 곡괭이 등급의 기본 시간 배율로 초기화 (mining_speed = 1.0)
        await bot.db.execute(
            "INSERT INTO mining_users (id, mining_speed) VALUES($1,1.0) ON CONFLICT (id) DO UPDATE SET mining_speed=1.0",
            str(member.id))
        embed = discord.Embed(title="✅ 광질 속도 초기화", color=discord.Color.green())
        embed.add_field(name="대상", value=member.display_name, inline=True)
        embed.add_field(name="속도 배율", value="×1.000 (기본)", inline=True)
        return await ctx.send(embed=embed)

    try: speed_f = float(speed)
    except ValueError: return await ctx.send("❌ 속도는 숫자 또는 'reset'이어야 해요.")
    if speed_f <= 0: return await ctx.send("❌ 속도는 0 초과여야 해요.")
    await bot.db.execute(
        "INSERT INTO mining_users (id, mining_speed) VALUES($1,$2) ON CONFLICT (id) DO UPDATE SET mining_speed=$2",
        str(member.id), speed_f)
    embed = discord.Embed(title="✅ 광질 속도 설정", color=discord.Color.green())
    embed.add_field(name="대상", value=member.display_name, inline=True)
    embed.add_field(name="속도 배율", value=f"×{speed_f:.3f}", inline=True)
    await ctx.send(embed=embed)

@bot.command(name="확률")
@commands.has_permissions(administrator=True)
async def prefix_mine_probs(ctx):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    ore_p  = await get_mine_cfg("ore_probs",  DEFAULT_ORE_PROBS)
    pick_p = await get_mine_cfg("pick_probs", DEFAULT_PICK_PROBS)
    bag_p  = await get_mine_cfg("bag_probs",  DEFAULT_BAG_PROBS)
    embed = discord.Embed(title="📊 현재 광질 확률", color=discord.Color.blurple())
    embed.add_field(name="⛏️ 곡괭이 확률",
        value="\n".join(f"**{g}**: {v*100:.3f}%" for g,v in pick_p.items()), inline=True)
    embed.add_field(name="🎒 가방 확률",
        value="\n".join(f"**{g}**: {v*100:.3f}%" for g,v in bag_p.items()), inline=True)
    embed.add_field(name="🪨 광석 등급 확률",
        value="\n".join(f"**{g}**: {v*100:.4f}%" for g,v in ore_p.items()), inline=False)
    await ctx.send(embed=embed)

@bot.command(name="광석확률")
@commands.has_permissions(administrator=True)
async def prefix_ore_prob(ctx, 등급: str, 확률: float):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if 등급 not in ORE_GRADES: return await ctx.send(f"❌ 유효하지 않은 등급: {등급}")
    if math.isnan(확률) or math.isinf(확률) or 확률 <= 0 or 확률 > 1:
        return await ctx.send("❌ 확률은 0 초과 1 이하 숫자여야 해요.")
    probs = await get_mine_cfg("ore_probs", DEFAULT_ORE_PROBS)
    probs[등급] = 확률; await set_mine_cfg("ore_probs", probs)
    await ctx.send(f"✅ **{등급}** 등급 광석 확률 → `{확률*100:.4f}%`")

@bot.command(name="곡괭이확률")
@commands.has_permissions(administrator=True)
async def prefix_pick_prob(ctx, 등급: str, 확률: float):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if 등급 not in PICK_DATA: return await ctx.send(f"❌ 유효하지 않은 등급: {등급}")
    if math.isnan(확률) or math.isinf(확률) or 확률 <= 0 or 확률 > 1:
        return await ctx.send("❌ 확률은 0 초과 1 이하 숫자여야 해요.")
    probs = await get_mine_cfg("pick_probs", DEFAULT_PICK_PROBS)
    probs[등급] = 확률; await set_mine_cfg("pick_probs", probs)
    await ctx.send(f"✅ **{등급}** 등급 곡괭이 확률 → `{확률*100:.3f}%`")

@bot.command(name="가방확률")
@commands.has_permissions(administrator=True)
async def prefix_bag_prob(ctx, 등급: str, 확률: float):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if 등급 not in BAG_DATA: return await ctx.send(f"❌ 유효하지 않은 등급: {등급}")
    if math.isnan(확률) or math.isinf(확률) or 확률 <= 0 or 확률 > 1:
        return await ctx.send("❌ 확률은 0 초과 1 이하 숫자여야 해요.")
    probs = await get_mine_cfg("bag_probs", DEFAULT_BAG_PROBS)
    probs[등급] = 확률; await set_mine_cfg("bag_probs", probs)
    await ctx.send(f"✅ **{등급}** 가방 확률 → `{확률*100:.3f}%`")

@bot.command(name="광석소환")
@commands.has_permissions(administrator=True)
async def prefix_ore_spawn(ctx, member: discord.Member, 등급: str, *, 무게: str = None):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if member.id == BOT_OWNER_ID and ctx.author.id != BOT_OWNER_ID:
        return await ctx.send("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.")
    if 등급 not in ORE_GRADES: return await ctx.send(f"❌ 유효하지 않은 등급: {등급}")
    gd = ORE_GRADES[등급]
    try:
        weight = int(무게) if 무게 else random.randint(gd["w_min"], gd["w_max"])
        weight = max(gd["w_min"], min(gd["w_max"], weight))
    except ValueError: return await ctx.send("❌ 무게는 정수여야 해요.")
    name  = random.choice(ORE_LIST[등급])
    value = int(round(weight * gd["w_per_g"]))
    uid   = str(member.id)
    await bot.db.execute(
        "INSERT INTO mining_inventory(user_id,ore_name,ore_grade,weight,value) VALUES($1,$2,$3,$4,$5)",
        uid, name, 등급, weight, value)
    await bot.db.execute(
        "INSERT INTO mining_discovered_ores(user_id,ore_name) VALUES($1,$2) ON CONFLICT DO NOTHING", uid, name)
    await ctx.send(f"✅ **{member.display_name}** 님에게 {gd['emoji']} **{name}** ({등급}등급, {weight:,}g, {value:,}원) 지급 완료!")

@bot.command(name="가방소환")
@commands.has_permissions(administrator=True)
async def prefix_bag_give(ctx, member: discord.Member, 등급: str):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if member.id == BOT_OWNER_ID and ctx.author.id != BOT_OWNER_ID:
        return await ctx.send("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.")
    bag_key = 등급 if 등급 in BAG_DATA else 등급 + "가방"
    if bag_key not in BAG_DATA: return await ctx.send(f"❌ 유효하지 않은 등급. ({'/'.join(BAG_DATA)})")
    await bot.db.execute(
        "INSERT INTO mining_users(id,bag_type) VALUES($1,$2) ON CONFLICT (id) DO UPDATE SET bag_type=$2",
        str(member.id), bag_key)
    bd = BAG_DATA[bag_key]
    await ctx.send(f"✅ **{member.display_name}** 님에게 {bd['emoji']} **{bag_key}** (최대 {bd['cap']:,}개) 장착 완료!")

@bot.command(name="곡괭이장착")
@commands.has_permissions(administrator=True)
async def prefix_pick_give(ctx, member: discord.Member, 등급: str):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if member.id == BOT_OWNER_ID and ctx.author.id != BOT_OWNER_ID:
        return await ctx.send("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.")
    if 등급 not in PICK_DATA: return await ctx.send(f"❌ 유효하지 않은 등급. ({'/'.join(PICK_DATA)})")
    pd = PICK_DATA[등급]; name = random.choice(pd["items"]); uid = str(member.id)
    await bot.db.execute(
        "INSERT INTO mining_users(id,pickaxe_grade,pickaxe_name,pickaxe_broken) VALUES($1,$2,$3,FALSE)"
        " ON CONFLICT (id) DO UPDATE SET pickaxe_grade=$2, pickaxe_name=$3, pickaxe_broken=FALSE",
        uid, 등급, name)
    await bot.db.execute(
        "INSERT INTO mining_discovered_picks(user_id,pick_name) VALUES($1,$2) ON CONFLICT DO NOTHING", uid, name)
    await ctx.send(f"✅ **{member.display_name}** 님에게 {pd['emoji']} **{name}** ({등급}등급) 장착 완료!")

# ══════════════════════════════════════════════════════════════════════════════
#  신규 관리자 명령어 — 봇 제어
# ══════════════════════════════════════════════════════════════════════════════
@bot.command(name="봇벤")
@commands.has_permissions(administrator=True)
async def prefix_bot_ban(ctx, target: discord.Member, *, reason: str = "사유 없음"):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if target.id == BOT_OWNER_ID and ctx.author.id != BOT_OWNER_ID:
        return await ctx.send("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.")
    uid = str(target.id)
    await bot.db.execute(
        "INSERT INTO bot_bans (user_id,reason) VALUES($1,$2) ON CONFLICT (user_id) DO UPDATE SET reason=$2, banned_at=NOW()",
        uid, reason)
    bot_banned_cache.add(uid)
    embed = discord.Embed(title="🚫 봇 벤 완료", color=discord.Color.dark_red())
    embed.add_field(name="대상", value=target.display_name, inline=True)
    embed.add_field(name="사유", value=reason, inline=True)
    await ctx.send(embed=embed)

@bot.command(name="봇언벤")
@commands.has_permissions(administrator=True)
async def prefix_bot_unban(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    uid = str(target.id)
    await bot.db.execute("DELETE FROM bot_bans WHERE user_id=$1", uid)
    bot_banned_cache.discard(uid)
    await ctx.send(f"✅ **{target.display_name}** 님의 봇 밴이 해제됐어요.")

@bot.command(name="봇점검")
@commands.has_permissions(administrator=True)
async def prefix_maintenance(ctx, mode: str):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    global maintenance_mode
    if mode.lower() == "on":
        maintenance_mode = True
        await bot.db.execute(
            "INSERT INTO bot_config (key,value) VALUES('maintenance','on') ON CONFLICT (key) DO UPDATE SET value='on'")
        await ctx.send("🔧 봇 점검 모드 **ON** — 일반 유저가 봇을 사용할 수 없어요.")
    elif mode.lower() == "off":
        maintenance_mode = False
        await bot.db.execute(
            "INSERT INTO bot_config (key,value) VALUES('maintenance','off') ON CONFLICT (key) DO UPDATE SET value='off'")
        await ctx.send("✅ 봇 점검 모드 **OFF** — 정상 서비스 재개됐어요.")
    else:
        await ctx.send("❌ `!봇점검 on` 또는 `!봇점검 off` 를 사용하세요.")

# ══════════════════════════════════════════════════════════════════════════════
#  신규 관리자 명령어 — 실제 디스코드 타임아웃 (벤/언벤)
# ══════════════════════════════════════════════════════════════════════════════
def _parse_timeout_seconds(seconds: int) -> str | None:
    """디스코드 타임아웃 최대 28일 제한 검증. 문제 있으면 에러 문자열 반환."""
    if seconds <= 0:
        return "❌ 시간(초)은 1 이상이어야 해요."
    if seconds > 28 * 24 * 60 * 60:
        return "❌ 디스코드 타임아웃은 최대 28일(2,419,200초)까지만 가능해요."
    return None

@bot.tree.command(name="벤", description="⚠️ [관리자] 유저를 지정한 시간(초) 동안 타임아웃시킵니다")
@app_commands.describe(유저="타임아웃할 유저", 시간="타임아웃 시간(초)", 사유="사유 (선택)")
async def slash_timeout(interaction: discord.Interaction, 유저: discord.Member, 시간: int, 사유: str = "사유 없음"):
    if not await slash_guard(interaction): return
    if not interaction.user.guild_permissions.administrator and str(interaction.user.id) not in bot_ops_cache:
        return await interaction.response.send_message("❌ 관리자만 사용할 수 있어요.", ephemeral=True)
    if 유저.id == BOT_OWNER_ID and interaction.user.id != BOT_OWNER_ID:
        return await interaction.response.send_message("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.", ephemeral=True)
    err = _parse_timeout_seconds(시간)
    if err: return await interaction.response.send_message(err, ephemeral=True)
    try:
        await 유저.timeout(datetime.timedelta(seconds=시간), reason=f"{interaction.user.display_name}: {사유}")
    except discord.Forbidden:
        return await interaction.response.send_message("❌ 봇에게 이 유저를 타임아웃할 권한이 없어요. (역할 순서 확인)", ephemeral=True)
    embed = discord.Embed(title="🔇 타임아웃 적용", color=discord.Color.dark_red())
    embed.add_field(name="대상", value=유저.display_name, inline=True)
    embed.add_field(name="시간", value=f"{시간:,}초", inline=True)
    embed.add_field(name="사유", value=사유, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="언벤", description="⚠️ [관리자] 유저의 타임아웃을 해제합니다")
@app_commands.describe(유저="타임아웃 해제할 유저")
async def slash_untimeout(interaction: discord.Interaction, 유저: discord.Member):
    if not await slash_guard(interaction): return
    if not interaction.user.guild_permissions.administrator and str(interaction.user.id) not in bot_ops_cache:
        return await interaction.response.send_message("❌ 관리자만 사용할 수 있어요.", ephemeral=True)
    try:
        await 유저.timeout(None, reason=f"{interaction.user.display_name}: 타임아웃 해제")
    except discord.Forbidden:
        return await interaction.response.send_message("❌ 봇에게 이 유저의 타임아웃을 해제할 권한이 없어요.", ephemeral=True)
    await interaction.response.send_message(f"✅ **{유저.display_name}** 님의 타임아웃이 해제됐어요.")

@bot.command(name="벤")
@commands.has_permissions(administrator=True)
async def prefix_timeout(ctx, target: discord.Member, seconds: int, *, reason: str = "사유 없음"):
    if not await bot_guard(ctx): return
    if target.id == BOT_OWNER_ID and ctx.author.id != BOT_OWNER_ID:
        return await ctx.send("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.")
    err = _parse_timeout_seconds(seconds)
    if err: return await ctx.send(err)
    try:
        await target.timeout(datetime.timedelta(seconds=seconds), reason=f"{ctx.author.display_name}: {reason}")
    except discord.Forbidden:
        return await ctx.send("❌ 봇에게 이 유저를 타임아웃할 권한이 없어요. (역할 순서 확인)")
    embed = discord.Embed(title="🔇 타임아웃 적용", color=discord.Color.dark_red())
    embed.add_field(name="대상", value=target.display_name, inline=True)
    embed.add_field(name="시간", value=f"{seconds:,}초", inline=True)
    embed.add_field(name="사유", value=reason, inline=False)
    await ctx.send(embed=embed)

@bot.command(name="언벤")
@commands.has_permissions(administrator=True)
async def prefix_untimeout(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    try:
        await target.timeout(None, reason=f"{ctx.author.display_name}: 타임아웃 해제")
    except discord.Forbidden:
        return await ctx.send("❌ 봇에게 이 유저의 타임아웃을 해제할 권한이 없어요.")
    await ctx.send(f"✅ **{target.display_name}** 님의 타임아웃이 해제됐어요.")

@bot.command(name="타임아웃목록")
@commands.has_permissions(administrator=True)
async def prefix_timeout_list(ctx):
    if not await bot_guard(ctx): return
    now = discord.utils.utcnow()
    timed_out = [m for m in ctx.guild.members if m.timed_out_until and m.timed_out_until > now]
    embed = discord.Embed(title="🔇 현재 타임아웃 중인 유저", color=discord.Color.dark_red())
    if not timed_out:
        embed.description = "타임아웃 중인 유저가 없어요."
    else:
        lines = []
        for m in timed_out:
            remain = m.timed_out_until - now
            mins = int(remain.total_seconds() // 60)
            lines.append(f"**{m.display_name}** — 약 {mins}분 남음")
        embed.description = "\n".join(lines)
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  티켓 시스템
# ══════════════════════════════════════════════════════════════════════════════
async def _close_ticket_channel(interaction: discord.Interaction):
    channel = interaction.channel
    row = await bot.db.fetchrow("SELECT * FROM active_tickets WHERE channel_id = $1", channel.id)
    if not row:
        return await interaction.response.send_message("❌ 티켓 채널이 아니에요.", ephemeral=True)
    is_participant = (
        interaction.user.id in (row['opener_id'], row['staff_id'])
        or interaction.user.guild_permissions.administrator
        or str(interaction.user.id) in bot_ops_cache
    )
    if not is_participant:
        return await interaction.response.send_message("❌ 이 티켓의 관계자만 닫을 수 있어요.", ephemeral=True)
    await interaction.response.send_message("🔒 티켓을 닫는 중이에요... 3초 후 채널이 삭제됩니다.")
    await bot.db.execute("DELETE FROM active_tickets WHERE channel_id = $1", channel.id)
    await asyncio.sleep(3)
    try:
        await channel.delete(reason=f"{interaction.user.display_name}: 티켓 닫기")
    except discord.Forbidden:
        pass
    except discord.NotFound:
        pass

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 티켓닫기", style=discord.ButtonStyle.danger, custom_id="ticket_close_button")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _close_ticket_channel(interaction)

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 티켓 열기", style=discord.ButtonStyle.success, custom_id="ticket_open_button")
    async def open_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = await bot.db.fetchrow(
            "SELECT * FROM ticket_panels WHERE message_id = $1", interaction.message.id)
        if not panel:
            return await interaction.response.send_message("❌ 티켓 설정 정보를 찾을 수 없어요.", ephemeral=True)
        staff = interaction.guild.get_member(panel['staff_id'])
        if staff is None:
            return await interaction.response.send_message("❌ 담당자를 찾을 수 없어요. 서버 관리자에게 문의하세요.", ephemeral=True)

        existing = await bot.db.fetchrow(
            "SELECT channel_id FROM active_tickets WHERE opener_id = $1 AND panel_message_id = $2",
            interaction.user.id, panel['message_id'])
        if existing:
            existing_channel = interaction.guild.get_channel(existing['channel_id'])
            if existing_channel:
                return await interaction.response.send_message(
                    f"⚠️ 이미 열려있는 티켓이 있어요: {existing_channel.mention}", ephemeral=True)
            await bot.db.execute("DELETE FROM active_tickets WHERE channel_id = $1", existing['channel_id'])

        await interaction.response.defer(ephemeral=True)

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            staff: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        try:
            ticket_channel = await interaction.guild.create_text_channel(
                name=f"티켓-{interaction.user.name}"[:100],
                category=interaction.channel.category,
                overwrites=overwrites,
                reason=f"{interaction.user.display_name} 님의 티켓 오픈")
        except discord.Forbidden:
            return await interaction.followup.send("❌ 봇에게 채널을 만들 권한이 없어요.", ephemeral=True)

        await bot.db.execute(
            """INSERT INTO active_tickets (channel_id, guild_id, opener_id, staff_id, panel_message_id)
               VALUES ($1, $2, $3, $4, $5)""",
            ticket_channel.id, interaction.guild.id, interaction.user.id, staff.id, panel['message_id'])

        embed = discord.Embed(
            title="🎫 티켓이 열렸어요",
            description=f"{interaction.user.mention} 님과 {staff.mention} 님의 1:1 대화방이에요.\n"
                        f"용건이 끝나면 아래 버튼으로 티켓을 닫아주세요.",
            color=discord.Color.green())
        await ticket_channel.send(content=f"{interaction.user.mention} {staff.mention}", embed=embed, view=TicketCloseView())
        await interaction.followup.send(f"✅ 티켓이 생성됐어요: {ticket_channel.mention}", ephemeral=True)

@bot.tree.command(name="티켓설정", description="⚠️ [관리자] 이 채널에 티켓 열기 버튼을 설치합니다")
@app_commands.describe(유저="티켓을 받을 담당자")
async def slash_ticket_setup(interaction: discord.Interaction, 유저: discord.Member):
    if not await slash_guard(interaction): return
    if not interaction.user.guild_permissions.administrator and str(interaction.user.id) not in bot_ops_cache:
        return await interaction.response.send_message("❌ 관리자만 사용할 수 있어요.", ephemeral=True)
    embed = discord.Embed(
        title="🎫 문의 티켓",
        description=f"아래 버튼을 누르면 **{유저.display_name}** 님과의 1:1 비공개 대화방이 생성돼요.",
        color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, view=TicketPanelView())
    sent = await interaction.original_response()
    await bot.db.execute(
        """INSERT INTO ticket_panels (message_id, channel_id, guild_id, staff_id, created_by)
           VALUES ($1, $2, $3, $4, $5)""",
        sent.id, sent.channel.id, interaction.guild.id, 유저.id, str(interaction.user.id))

@bot.command(name="op")
@commands.has_permissions(administrator=True)
async def prefix_op(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    # op 명령어는 봇 개발자만 사용 가능
    if not is_owner(ctx):
        return await ctx.send("❌ 봇 개발자만 op를 부여할 수 있어요.", delete_after=5)
    uid = str(target.id)
    await bot.db.execute(
        "INSERT INTO bot_ops (user_id) VALUES($1) ON CONFLICT DO NOTHING", uid)
    bot_ops_cache.add(uid)
    embed = discord.Embed(title="⭐ OP 권한 부여", color=discord.Color.gold())
    embed.add_field(name="대상", value=target.display_name, inline=True)
    embed.description = "이 유저는 이제 봇 관리자 명령어를 사용할 수 있어요."
    await ctx.send(embed=embed)

@bot.command(name="unop")
@commands.has_permissions(administrator=True)
async def prefix_unop(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    if not is_owner(ctx):
        return await ctx.send("❌ 봇 개발자만 op를 제거할 수 있어요.", delete_after=5)
    uid = str(target.id)
    await bot.db.execute("DELETE FROM bot_ops WHERE user_id=$1", uid)
    bot_ops_cache.discard(uid)
    await ctx.send(f"✅ **{target.display_name}** 님의 OP 권한이 제거됐어요.")

# ══════════════════════════════════════════════════════════════════════════════
#  신규 관리자 명령어 — 로그 & 롤백
# ══════════════════════════════════════════════════════════════════════════════
@bot.command(name="도박로그")
@commands.has_permissions(administrator=True)
async def prefix_gamble_log(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    uid = str(target.id)
    rows = await bot.db.fetch(
        "SELECT type,amount,detail,created_at FROM transactions WHERE user_id=$1 AND type IN ('도박_승','도박_패','슬롯_당첨','슬롯_꽝','블랙잭_승','블랙잭_패','블랙잭_무승부','복권_당첨','복권_꽝') ORDER BY created_at DESC LIMIT 20",
        uid)
    embed = discord.Embed(title=f"🎲 {target.display_name}님 도박 로그 (최근 20건)", color=discord.Color.red())
    if not rows:
        embed.description = "도박 로그가 없어요."
    else:
        lines = []
        for r in rows:
            icon = LOG_ICONS.get(r['type'], "•")
            sign = "+" if r['amount'] >= 0 else ""
            ts = r['created_at'].strftime('%m/%d %H:%M')
            lines.append(f"`{ts}` {icon} **{sign}{r['amount']:,}원** — {r['type']} {r['detail'] or ''}")
        embed.description = "\n".join(lines)
    await ctx.send(embed=embed)

@bot.command(name="광산로그")
@commands.has_permissions(administrator=True)
async def prefix_mine_log(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    uid = str(target.id)
    rows = await bot.db.fetch(
        "SELECT log_type,detail,pickaxe_name,bag_type,created_at FROM mining_logs WHERE user_id=$1 ORDER BY created_at DESC LIMIT 20",
        uid)
    embed = discord.Embed(title=f"⛏️ {target.display_name}님 광산 로그 (최근 20건)", color=0xD4AF37)
    if not rows:
        embed.description = "광산 로그가 없어요."
    else:
        lines = []
        for r in rows:
            ts = r['created_at'].strftime('%m/%d %H:%M:%S')
            lines.append(f"`{ts}` **{r['log_type']}** — {r['detail'] or ''}")
        embed.description = "\n".join(lines)
    await ctx.send(embed=embed)

@bot.command(name="도박롤백")
@commands.has_permissions(administrator=True)
async def prefix_gamble_rollback(ctx, target: discord.Member, *, datetime_str: str):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if target.id == BOT_OWNER_ID and ctx.author.id != BOT_OWNER_ID:
        return await ctx.send("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.")
    try:
        rollback_time = datetime.datetime.strptime(datetime_str.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return await ctx.send("❌ 날짜 형식이 잘못됐어요. 예: `2024-01-15 10:30:00`")
    uid = str(target.id)
    # 해당 시간 이후의 도박 순손익 계산
    gamble_rows = await bot.db.fetch(
        "SELECT type,amount FROM transactions WHERE user_id=$1 AND created_at > $2 AND type IN ('도박_승','도박_패','슬롯_당첨','슬롯_꽝','블랙잭_승','블랙잭_패','복권_당첨','복권_꽝')",
        uid, rollback_time)
    net_change = sum(r['amount'] for r in gamble_rows)
    if net_change == 0:
        return await ctx.send(f"❌ `{datetime_str}` 이후 도박 기록이 없어요.")
    # 순손익 반전 (롤백)
    reverse = -net_change
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO users (id,balance) VALUES($1,$2) ON CONFLICT (id) DO UPDATE SET balance=users.balance+$2",
                uid, reverse)
            await conn.execute(
                "DELETE FROM transactions WHERE user_id=$1 AND created_at > $2 AND type IN ('도박_승','도박_패','슬롯_당첨','슬롯_꽝','블랙잭_승','블랙잭_패','복권_당첨','복권_꽝')",
                uid, rollback_time)
    new_bal = await get_balance(bot.db, uid)
    embed = discord.Embed(title="↩️ 도박 롤백 완료", color=discord.Color.orange())
    embed.add_field(name="대상", value=target.display_name, inline=True)
    embed.add_field(name="기준 시간", value=datetime_str, inline=True)
    embed.add_field(name="취소된 기록", value=f"{len(gamble_rows)}건", inline=True)
    embed.add_field(name="잔액 조정", value=f"{'+'if reverse>=0 else ''}{reverse:,}원", inline=True)
    embed.add_field(name="현재 잔액", value=f"{new_bal:,}원", inline=True)
    await ctx.send(embed=embed)

@bot.command(name="광산롤백")
@commands.has_permissions(administrator=True)
async def prefix_mine_rollback(ctx, target: discord.Member, *, datetime_str: str):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if target.id == BOT_OWNER_ID and ctx.author.id != BOT_OWNER_ID:
        return await ctx.send("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.")
    try:
        rollback_time = datetime.datetime.strptime(datetime_str.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return await ctx.send("❌ 날짜 형식이 잘못됐어요. 예: `2024-01-15 10:30:00`")
    uid = str(target.id)
    # 해당 시간 이후의 광산 상태 스냅샷 조회
    snapshot = await bot.db.fetchrow(
        "SELECT pickaxe_grade,pickaxe_name,pickaxe_broken,bag_type FROM mining_logs WHERE user_id=$1 AND created_at <= $2 ORDER BY created_at DESC LIMIT 1",
        uid, rollback_time)
    # 해당 시간 이후 추가된 광석 삭제 및 판매 기록 기준 잔액 복구
    sell_rows = await bot.db.fetch(
        "SELECT amount FROM transactions WHERE user_id=$1 AND created_at > $2 AND type='광석_판매'",
        uid, rollback_time)
    sell_total = sum(r['amount'] for r in sell_rows)
    mine_logs_after = await bot.db.fetch(
        "SELECT id,detail FROM mining_logs WHERE user_id=$1 AND created_at > $2 AND log_type IN ('mine_ore','sell')",
        uid, rollback_time)
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            # 판매 취소 (잔액에서 판매금액 차감)
            if sell_total > 0:
                await conn.execute(
                    "UPDATE users SET balance=GREATEST(0,balance-$1) WHERE id=$2", sell_total, uid)
            # 광산 스냅샷 복원
            if snapshot:
                await conn.execute(
                    "UPDATE mining_users SET pickaxe_grade=$1,pickaxe_name=$2,pickaxe_broken=$3,bag_type=$4 WHERE id=$5",
                    snapshot['pickaxe_grade'],snapshot['pickaxe_name'],snapshot['pickaxe_broken'],snapshot['bag_type'],uid)
            # 해당 시간 이후 로그 삭제
            await conn.execute("DELETE FROM mining_logs WHERE user_id=$1 AND created_at > $2", uid, rollback_time)
            await conn.execute(
                "DELETE FROM transactions WHERE user_id=$1 AND created_at > $2 AND type='광석_판매'", uid, rollback_time)
    embed = discord.Embed(title="↩️ 광산 롤백 완료", color=0xD4AF37)
    embed.add_field(name="대상", value=target.display_name, inline=True)
    embed.add_field(name="기준 시간", value=datetime_str, inline=True)
    embed.add_field(name="처리된 로그", value=f"{len(mine_logs_after)}건", inline=True)
    if snapshot:
        embed.add_field(name="복원된 곡괭이", value=f"{snapshot['pickaxe_name'] or '없음'}", inline=True)
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  신규 관리자 명령어 — 광산 관리
# ══════════════════════════════════════════════════════════════════════════════
@bot.command(name="유저가방")
@commands.has_permissions(administrator=True)
async def prefix_view_bag(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    uid = str(target.id)
    mu  = await get_mine_user(uid)
    inv = await get_mine_inv(uid)
    cap = BAG_DATA[mu["bag_type"]]["cap"]
    bd  = BAG_DATA[mu["bag_type"]]
    embed = discord.Embed(
        title=f"🎒 {target.display_name}의 가방",
        description=f"{bd['emoji']} **{mu['bag_type']}** — {len(inv)}/{cap}개",
        color=bd["color"])
    if not inv:
        embed.add_field(name="📭 비어있음", value="광석이 없어요.", inline=False)
    else:
        groups: dict[str, list] = {}
        for idx, ore in enumerate(inv, 1):
            groups.setdefault(ore["ore_grade"], []).append((idx, ore))
        for grade in list(ORE_GRADES.keys()):
            if grade not in groups: continue
            gd = ORE_GRADES[grade]
            lines = [f"`#{idx}` {gd['emoji']} **{o['ore_name']}** — {o['weight']:,}g ({o['value']:,}원)"
                     for idx, o in groups[grade]]
            field_val = "\n".join(lines)
            if len(field_val) > 1024: field_val = field_val[:1020] + "..."
            embed.add_field(name=f"{gd['emoji']} {grade}등급 ({len(groups[grade])}개)", value=field_val, inline=False)
    await ctx.send(embed=embed)

@bot.command(name="광석삭제")
@commands.has_permissions(administrator=True)
async def prefix_delete_ore(ctx, target: discord.Member, slot: int):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if target.id == BOT_OWNER_ID and ctx.author.id != BOT_OWNER_ID:
        return await ctx.send("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.")
    uid = str(target.id)
    inv = await get_mine_inv(uid)
    if slot < 1 or slot > len(inv):
        return await ctx.send(f"❌ 번호는 1~{len(inv)} 범위여야 해요.")
    ore = inv[slot-1]
    await bot.db.execute("DELETE FROM mining_inventory WHERE id=$1", ore['id'])
    await ctx.send(f"✅ **{target.display_name}** 님의 `#{slot}` {ore['ore_name']} ({ore['ore_grade']}등급) 삭제 완료!")

@bot.command(name="곡괭이삭제")
@commands.has_permissions(administrator=True)
async def prefix_delete_pick(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if target.id == BOT_OWNER_ID and ctx.author.id != BOT_OWNER_ID:
        return await ctx.send("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.")
    uid = str(target.id)
    mu = await get_mine_user(uid)
    prev = mu.get("pickaxe_name") or "없음"
    await bot.db.execute(
        "UPDATE mining_users SET pickaxe_grade=NULL, pickaxe_name=NULL, pickaxe_broken=FALSE WHERE id=$1", uid)
    await ctx.send(f"✅ **{target.display_name}** 님의 곡괭이(**{prev}**)가 삭제됐어요.")

@bot.command(name="가방삭제")
@commands.has_permissions(administrator=True)
async def prefix_delete_bag(ctx, target: discord.Member):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    if target.id == BOT_OWNER_ID and ctx.author.id != BOT_OWNER_ID:
        return await ctx.send("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.")
    uid = str(target.id)
    await bot.db.execute("UPDATE mining_users SET bag_type='일반가방' WHERE id=$1", uid)
    await ctx.send(f"✅ **{target.display_name}** 님의 가방이 일반가방으로 초기화됐어요.")

@bot.tree.command(name="광산초기화", description="⚠️ [관리자] 특정 유저의 광산 데이터를 전체 초기화합니다")
@app_commands.describe(유저="초기화할 유저")
async def slash_mine_reset(interaction: discord.Interaction, 유저: discord.Member):
    if not await slash_guard(interaction): return
    uid_me = str(interaction.user.id)
    if uid_me not in admin_mode_users:
        return await interaction.response.send_message("🔒 관리자 모드를 먼저 켜주세요.", ephemeral=True)
    if 유저.id == BOT_OWNER_ID and interaction.user.id != BOT_OWNER_ID:
        return await interaction.response.send_message("봇 개발자는 다른 사용자의 명령어를 적용받지 않습니다.", ephemeral=True)
    uid = str(유저.id)
    await interaction.response.defer()
    async with bot.db.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM mining_inventory WHERE user_id=$1", uid)
            await conn.execute("DELETE FROM mining_discovered_ores WHERE user_id=$1", uid)
            await conn.execute("DELETE FROM mining_discovered_picks WHERE user_id=$1", uid)
            await conn.execute("DELETE FROM mining_logs WHERE user_id=$1", uid)
            await conn.execute(
                "UPDATE mining_users SET pickaxe_grade=NULL,pickaxe_name=NULL,pickaxe_broken=FALSE,"
                "bag_type='일반가방',total_mined=0,total_sold=0,mining_speed=1.0 WHERE id=$1", uid)
    embed = discord.Embed(title="🗑️ 광산 초기화 완료", color=discord.Color.dark_red())
    embed.add_field(name="대상", value=유저.display_name, inline=True)
    embed.description = "인벤토리·도감·곡괭이·가방·로그 전부 삭제됐어요."
    await interaction.followup.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  !저장 — DB 데이터 JSON 백업
# ══════════════════════════════════════════════════════════════════════════════
@bot.command(name="저장")
@commands.has_permissions(administrator=True)
async def prefix_save(ctx):
    if not await bot_guard(ctx): return
    if not admin_check(ctx): return await ctx.send("🔒 관리자 모드를 먼저 켜주세요.", delete_after=5)
    await ctx.send("⏳ 데이터 저장 중...")
    try:
        backup = {}
        backup['users'] = [dict(r) for r in await bot.db.fetch("SELECT * FROM users ORDER BY id")]
        backup['mining_users'] = [dict(r) for r in await bot.db.fetch("SELECT * FROM mining_users ORDER BY id")]
        backup['mining_inventory'] = [dict(r) for r in await bot.db.fetch("SELECT * FROM mining_inventory ORDER BY id")]
        backup['transactions_count'] = await bot.db.fetchval("SELECT COUNT(*) FROM transactions")
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        path = f"backup_{ts}.json"

        # Convert datetime objects to strings
        def serialize(obj):
            if isinstance(obj, datetime.datetime): return obj.isoformat()
            if isinstance(obj, datetime.date): return obj.isoformat()
            return str(obj)

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(backup, f, ensure_ascii=False, indent=2, default=serialize)

        await ctx.send(f"✅ 데이터 저장 완료! 파일: `{path}`\n"
                       f"💰 유저 {len(backup['users'])}명 | ⛏️ 광질 유저 {len(backup['mining_users'])}명 | "
                       f"📦 인벤토리 {len(backup['mining_inventory'])}개 | 📋 거래 {backup['transactions_count']:,}건")
    except Exception as e:
        await ctx.send(f"❌ 저장 실패: {e}")

# ══════════════════════════════════════════════════════════════════════════════
#  관리자 목록 슬래시
# ══════════════════════════════════════════════════════════════════════════════
@bot.command(name="관리자목록")
async def prefix_admin_list(ctx):
    if not await bot_guard(ctx): return
    if not is_admin_or_op(ctx) and not is_owner(ctx):
        return await ctx.send("❌ 권한이 없어요.", delete_after=5)
    guild = ctx.guild
    if not guild: return await ctx.send("❌ 서버 내에서만 사용 가능해요.")
    admins = [m for m in guild.members if m.guild_permissions.administrator and not m.bot]
    admin_lines = "\n".join(
        f"{'🔓 ' if str(m.id) in admin_mode_users else '🔒 '}{m.display_name}"
        + (" ⭐" if str(m.id) in bot_ops_cache else "")
        for m in admins
    ) or "관리자가 없어요."
    embed = discord.Embed(title="👑 서버 관리자 목록 (prefix)", color=discord.Color.red())
    embed.add_field(name=f"관리자 ({len(admins)}명)  |  🔓=ON  🔒=OFF  ⭐=OP",
                    value=admin_lines, inline=False)
    embed.set_footer(text="전체 명령어 목록은 `/관리자목록` (슬래시) 참고")
    await ctx.send(embed=embed)

@bot.tree.command(name="관리자목록", description="👑 서버 관리자 목록 및 관리자 명령어를 확인합니다")
async def slash_admin_list(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    guild = interaction.guild
    if not guild: return await interaction.response.send_message("❌ 서버 내에서만 사용 가능해요.", ephemeral=True)
    admins = [m for m in guild.members if m.guild_permissions.administrator and not m.bot]
    admin_lines = "\n".join(
        f"{'🔓 ' if str(m.id) in admin_mode_users else '🔒 '}{m.display_name}"
        + (" ⭐" if str(m.id) in bot_ops_cache else "")
        for m in admins
    ) or "관리자가 없어요."

    embed = discord.Embed(title="👑 서버 관리자 목록", color=discord.Color.red())
    embed.add_field(
        name=f"관리자 ({len(admins)}명)  |  🔓=ON  🔒=OFF  ⭐=OP",
        value=admin_lines, inline=False)

    def fmt(rows): return "\n".join(f"`{n}` — {d}" for n, d in rows)

    eco_cmds = [
        ("!돈추가 @유저1 @유저2... 금액", "다중 유저 잔액 증가"),
        ("!돈차감 @유저1 @유저2... 금액", "다중 유저 잔액 차감"),
        ("!초기화 @유저",                  "유저 잔액 0으로 초기화"),
        ("!스탯초기화 @유저",              "거래내역·출석기록 초기화"),
    ]
    sanction_cmds = [
        ("!경고부여 @유저 횟수", "경고 횟수 부여 (기본 1)"),
        ("!경고회수 @유저",      "경고 1회 회수"),
        ("!봇벤 @유저 [사유]",  "봇 사용 금지"),
        ("!봇언벤 @유저",        "봇 사용 금지 해제"),
    ]
    sys_cmds = [
        ("!봇점검 on/off",  "봇 점검 모드 ON/OFF"),
        ("!op @유저",       "OP 권한 부여 (봇 개발자만)"),
        ("!unop @유저",     "OP 권한 회수 (봇 개발자만)"),
        ("!저장",            "모든 DB 데이터 JSON 백업"),
    ]
    title_cmds = [
        ("!칭호부여 @유저 칭호명 [#색상]",   "이벤트 칭호 지급"),
        ("!칭호삭제 @유저 칭호명",           "유저 칭호 삭제"),
        ("!칭호색상변경 @유저 #색상 칭호명", "칭호 색상 변경"),
        ("!칭호전체획득 @유저",              "모든 칭호 일괄 지급"),
        ("!칭호초기화 @유저",               "유저 칭호 전체 삭제"),
    ]
    bet_cmds = [
        ("!도박최대베팅액 금액", "도박 최대 베팅액 설정"),
        ("!슬롯최대베팅액 금액", "슬롯머신 최대 베팅액 설정"),
    ]
    info_cmds = [
        ("!로그확인 [@유저]",  "거래 내역 조회"),
        ("!유저정보 @유저",    "잔액·경고·광산 종합 정보"),
        ("!도박로그 @유저",    "도박 전용 로그 20건"),
        ("!광산로그 @유저",    "광산 전용 로그 20건"),
    ]
    rollback_cmds = [
        ("!도박롤백 @유저 YYYY-MM-DD HH:MM:SS", "해당 시간 이후 도박 결과 롤백"),
        ("!광산롤백 @유저 YYYY-MM-DD HH:MM:SS", "해당 시간 이후 광산 상태 롤백"),
    ]
    mine_cmds = [
        ("!광질속도 @유저 배율|reset", "채굴 시간 배율 설정 (reset=기본값 1.0)"),
        ("!확률",                       "광석·곡괭이·가방 확률 전체 조회"),
        ("!광석확률 [등급] [0~1]",      "광석 채굴 확률 변경"),
        ("!곡괭이확률 [등급] [0~1]",    "곡괭이 뽑기 확률 변경"),
        ("!가방확률 [등급] [0~1]",      "가방 뽑기 확률 변경"),
        ("!광석소환 @유저 [등급] [무게]","해당 등급 광석 지급"),
        ("!가방소환 @유저 [등급]",       "해당 등급 가방 장착"),
        ("!곡괭이장착 @유저 [등급]",     "해당 등급 곡괭이 장착"),
        ("/광산초기화 @유저",            "광산 관련 모든 데이터 초기화"),
        ("!유저가방 @유저",              "해당 유저의 가방 내용 조회"),
        ("!광석삭제 @유저 [번호]",       "해당 유저 가방의 광석 삭제"),
        ("!곡괭이삭제 @유저",           "해당 유저의 곡괭이 삭제"),
        ("!가방삭제 @유저",             "해당 유저의 가방을 일반가방으로 초기화"),
    ]
    misc_cmds = [
        ("!관리자모드",   "관리자 모드 ON/OFF 토글"),
        ("!공지 [내용]",  "봇으로 공지 전송"),
    ]

    embed.add_field(name="💰 경제 명령어", value=fmt(eco_cmds), inline=False)
    embed.add_field(name="⚠️ 제재 명령어", value=fmt(sanction_cmds), inline=False)
    embed.add_field(name="⚙️ 시스템 명령어", value=fmt(sys_cmds), inline=False)
    embed.add_field(name="🎖️ 칭호 명령어", value=fmt(title_cmds), inline=False)
    embed.add_field(name="🎰 베팅 한도", value=fmt(bet_cmds), inline=False)
    embed.add_field(name="🔍 조회 명령어", value=fmt(info_cmds), inline=False)
    embed.add_field(name="↩️ 롤백 명령어", value=fmt(rollback_cmds), inline=False)
    embed.add_field(name="⛏️ 광질 게임 관리", value=fmt(mine_cmds), inline=False)
    embed.add_field(name="📢 기타 명령어", value=fmt(misc_cmds), inline=False)
    embed.set_footer(text=f"관리자 모드가 꺼진 상태에서는 일반 유저와 동일하게 동작해요. | {MADE_BY_TAG}")
    await interaction.response.send_message(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  도움말
# ══════════════════════════════════════════════════════════════════════════════
def build_help_embeds(prefix: bool) -> list[discord.Embed]:
    p = "!" if prefix else "/"

    # ── 임베드 1: 경제 / 게임 ──────────────────────────────────────────────
    e1 = discord.Embed(
        title="📖 💲겜방봇💲 명령어 도움말 (1/2)",
        description="슬래시(`/`) 또는 느낌표(`!`) 둘 다 사용 가능해요!\n슬래시는 자동완성이 지원돼서 더 편리해요 😊",
        color=discord.Color.blurple())

    e1.add_field(name="━━━ 👤 프로필 & 경제 ━━━", value="\u200b", inline=False)
    e1.add_field(name=f"👤 {p}내프로필", value="잔액·칭호·출석·경고 등 내 프로필 확인", inline=True)
    e1.add_field(name=f"📋 {p}거래내역", value="최근 15건 거래 내역 조회", inline=True)
    e1.add_field(name=f"🏆 {p}랭킹", value="서버 자산 TOP 10 순위", inline=True)
    e1.add_field(name=f"💸 {p}송금", value=f"다른 유저에게 돈 송금\n`{p}송금 @철수 10000`", inline=True)
    e1.add_field(name=f"📊 {p}운영통계", value="봇 전체 운영 통계 확인", inline=True)
    e1.add_field(name="\u200b", value="\u200b", inline=True)

    e1.add_field(name="━━━ 🎁 출석 ━━━", value="\u200b", inline=False)
    e1.add_field(name=f"🎁 {p}출석",
        value=f"매일 **{DAILY_REWARD:,}원** 지급!\n연속 출석 보너스: 7일→+5만 / 14일→+10만 / 30일→+30만",
        inline=False)

    e1.add_field(name="━━━ 🎮 미니게임 ━━━", value="\u200b", inline=False)
    e1.add_field(name=f"🎰 {p}도박 [금액]",
        value="버튼 클릭 → 두근두근 연출!\nT1 **1.1~1.5배** / T2 **1.6~2.0배** / T3 **2.1~2.5배** 🔥 / T4 **2.6~3.0배** 💎",
        inline=False)
    e1.add_field(name=f"🃏 {p}블랙잭 [금액]",
        value="딜러와 21 숫자 대결!\n승리→1.5배 / 블랙잭(첫2장=21)→2배", inline=True)
    e1.add_field(name=f"🎰 {p}슬롯머신 [금액]",
        value="릴 3개 중 2개 이상 일치 → 당첨!\n💎💎💎 → 최대 20배", inline=True)
    e1.add_field(name=f"🎲 !주사위 @유저 금액",
        value="다른 유저와 주사위 1:1 대결!", inline=True)
    e1.add_field(name=f"🎟️ {p}복권",
        value=f"복권 구매 ({LOTTERY_COST:,}원)\n1등 7,500만원 / 2등 300만 / 3등 10만 / 4등 2만", inline=True)
    e1.add_field(name="\u200b", value="\u200b", inline=True)
    e1.add_field(name="\u200b", value="\u200b", inline=True)

    e1.add_field(name="━━━ 🎖️ 칭호 ━━━", value="\u200b", inline=False)
    e1.add_field(name=f"🎖️ {p}칭호목록", value="내가 보유한 칭호 목록 확인", inline=True)
    e1.add_field(name=f"📖 {p}칭호도감", value="전체 칭호 해금 조건 확인", inline=True)
    e1.add_field(name=f"🏷️ {p}칭호장착", value="보유 칭호 장착 (번호 또는 이름)", inline=True)
    e1.set_footer(text=f"💡 칭호는 출석·도박·잔액·광질 등 다양한 조건으로 자동 해금돼요! | {MADE_BY_TAG}")

    # ── 임베드 2: 광질 ────────────────────────────────────────────────────
    e2 = discord.Embed(
        title="📖 💲겜방봇💲 명령어 도움말 (2/2) — ⛏️ 광질",
        color=discord.Color.gold())

    e2.add_field(name=f"⛏️ {p}광질",
        value="곡괭이로 광석 채굴!\n일반25초 / 희귀20초 / 레어14초 / 에픽10초 / 신화7초 / 전설5초 / 미친3초\n신화 이상 발굴 시 컷씬 연출!",
        inline=True)
    e2.add_field(name=f"🎒 {p}가방",
        value="인벤토리 확인 (슬롯 번호 표시)", inline=True)
    e2.add_field(name=f"💰 {p}판매 [번호] [갯수]",
        value="광석 판매\n번호=0 → 전체 판매\n번호=N, 갯수=0 → N번부터 전부", inline=True)
    e2.add_field(name=f"📊 {p}광산정보",
        value="내 광산 프로필 + 최근 로그", inline=True)
    e2.add_field(name=f"🏆 {p}광산랭킹",
        value="광산 채굴량 TOP 10", inline=True)
    e2.add_field(name=f"📖 {p}광석도감",
        value=f"광석 {len(ALL_ORES)}개 도감", inline=True)
    e2.add_field(name=f"🔨 {p}곡괭이도감",
        value=f"곡괭이 {len(ALL_PICKS)}개 도감", inline=True)
    e2.add_field(name=f"🎰 {p}곡괭이뽑기",
        value="곡괭이 가챠 (5,000원)\n일반60%→희귀24%→레어10%→에픽3.5%→신화1.5%→전설0.8%→미친0.2%\n신화+ 화려한 컷씬!",
        inline=True)
    e2.add_field(name=f"🎒 {p}가방뽑기",
        value="가방 가챠 (5,000원)\n일반60%→레어25%→에픽10%→신화4%→전설0.9%→미친0.1%\n신화+ 컷씬!",
        inline=True)
    e2.add_field(name=f"🔧 {p}수리",
        value="파괴된 곡괭이 수리\n일반5천 / 희귀1만 / 레어5만 / 에픽10만 / 신화30만 / 전설50만 / 미친100만",
        inline=True)
    e2.add_field(name="━━━ 🔮 광질 비밀 ━━━", value="\u200b", inline=False)
    e2.add_field(name="❓ ???등급",
        value="광석 중 극악의 확률로 존재하는 미지의 등급\n발굴 시 서버 전체 공지 + 히든 칭호 + 화려한 컷씬!",
        inline=False)
    e2.set_footer(text=f"⛏️ 광산 채굴로 광석을 모아 판매하세요! | {MADE_BY_TAG}")

    return [e1, e2]

@bot.tree.command(name="도움말", description="📖 모든 명령어 목록과 사용법을 안내합니다")
async def slash_help(interaction: discord.Interaction):
    if not await slash_guard(interaction): return
    embeds = build_help_embeds(prefix=False)
    await interaction.response.send_message(embeds=embeds)

@bot.command(name='도움말')
async def prefix_help(ctx):
    if not await bot_guard(ctx): return
    for embed in build_help_embeds(prefix=True):
        await ctx.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  오류 처리
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    msg = "❌ 오류가 발생했어요. 다시 시도해주세요."
    if isinstance(error, app_commands.MissingPermissions):
        msg = "❌ 관리자만 사용 가능한 명령어예요."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg)
        else:
            await interaction.response.send_message(msg)
    except Exception:
        pass

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ 관리자만 사용 가능한 명령어예요.", delete_after=5)
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ 유저를 찾을 수 없어요.", delete_after=5)
    elif isinstance(error, commands.CommandNotFound):
        pass
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ 인자가 부족해요. `!도움말` 을 확인해주세요.", delete_after=7)

# ══════════════════════════════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════════════════════════════
async def _main():
    await bot.start(os.environ["DISCORD_TOKEN"])

asyncio.run(_main())

