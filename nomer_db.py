import aiosqlite

conn: aiosqlite.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY, name TEXT, balance INTEGER DEFAULT 0, joined INTEGER);
CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS channels(
    chat_id INTEGER PRIMARY KEY, title TEXT, url TEXT);
CREATE TABLE IF NOT EXISTS links(
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, url TEXT);
CREATE TABLE IF NOT EXISTS join_requests(
    chat_id INTEGER, user_id INTEGER, PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS prices(
    server INTEGER, code TEXT, name TEXT, price INTEGER,
    PRIMARY KEY(server, code));
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, server INTEGER,
    pid TEXT, phone TEXT, country TEXT, price INTEGER,
    status TEXT, code TEXT, created INTEGER);
CREATE TABLE IF NOT EXISTS payments(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER,
    status TEXT DEFAULT 'pending', created INTEGER);
CREATE TABLE IF NOT EXISTS balance_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER,
    reason TEXT, created INTEGER);
"""

# eski bazani yangilash (ustun allaqachon bo'lsa xato e'tiborsiz qoldiriladi)
MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN ref_by INTEGER",
    "ALTER TABLE users ADD COLUMN ref_earned INTEGER DEFAULT 0",
    "ALTER TABLE prices ADD COLUMN ckey TEXT",
    "ALTER TABLE prices ADD COLUMN cost INTEGER DEFAULT 0",
    "ALTER TABLE prices ADD COLUMN hidden INTEGER DEFAULT 0",
    "ALTER TABLE prices ADD COLUMN pinned INTEGER DEFAULT 0",
    "ALTER TABLE prices ADD COLUMN custom INTEGER DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN cost INTEGER DEFAULT 0",
]


async def init(path="bot.db"):
    global conn
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.executescript(SCHEMA)
    for q in MIGRATIONS:
        try:
            await conn.execute(q)
        except aiosqlite.OperationalError:
            pass
    await conn.commit()


async def fetchone(q, args=()):
    async with conn.execute(q, args) as c:
        return await c.fetchone()


async def fetchall(q, args=()):
    async with conn.execute(q, args) as c:
        return await c.fetchall()


async def execute(q, args=()):
    cur = await conn.execute(q, args)
    await conn.commit()
    return cur
