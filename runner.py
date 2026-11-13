"""Har bir yaratilgan botni alohida jarayon (subprocess) sifatida ishga tushiradi.
Har bir botning o'z papkasi bor: data/<bot_id>/ (baza, json, log, pid) — ma'lumotlar aralashmaydi."""
import asyncio, os, re, shutil, signal, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOTS_DIR = ROOT / "bots"
DATA_DIR = (Path(os.environ["DATA_DIR"]) / "data") if os.getenv("DATA_DIR") else ROOT / "data"
procs: dict[int, asyncio.subprocess.Process] = {}

# Bolalarga o'tmasligi kerak bo'lgan konstruktor sirlari
SECRET_ENV = ("BUILDER_TOKEN", "CLICK_PROVIDER_TOKEN", "PAYME_PROVIDER_TOKEN", "SUPER_ADMINS", "CARD_INFO", "REMOVEBG_KEY")


def _kill_orphan(folder: Path):
    """Konstruktor qayta ishga tushganda oldingi (yetim qolgan) bola jarayonni o'ldiradi —
    aks holda bir token bilan ikkita polling ishlab, Conflict xatosi chiqadi."""
    pf = folder / "pid"
    try:
        pid = int(pf.read_text().strip())
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
        if str(BOTS_DIR).encode() in cmd:                # boshqa jarayon emasligini tekshiramiz
            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass
    try: pf.unlink()
    except Exception: pass


async def start(bot_id: int, file: str, token: str, admin_ids: list[int], extra_env: dict | None = None) -> bool:
    await stop(bot_id)
    folder = DATA_DIR / str(bot_id)
    folder.mkdir(parents=True, exist_ok=True)
    _kill_orphan(folder)
    await asyncio.sleep(0.5)
    env = os.environ.copy()
    for k in list(env):
        if k in SECRET_ENV or re.match(r"SERVER\d+_", k):
            env.pop(k, None)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(BOTS_DIR), env.get("PYTHONPATH")]))   # yuklangan botlar plan_limits ni import qila oladi
    env["BOT_TOKEN"] = token
    env["ADMIN_IDS"] = ",".join(map(str, admin_ids))
    env.update(extra_env or {})                          # tarif limitlari: PLAN_DAILY / PLAN_MONTHLY / PLAN_RATE
    with open(folder / "bot.log", "ab") as log:
        p = await asyncio.create_subprocess_exec(
            sys.executable, str(BOTS_DIR / file), cwd=str(folder), env=env,
            stdout=log, stderr=asyncio.subprocess.STDOUT)
    procs[bot_id] = p
    (folder / "pid").write_text(str(p.pid))
    await asyncio.sleep(3)                               # darrov yiqilmaganini tekshiramiz (noto'g'ri token va h.k.)
    return p.returncode is None


async def stop(bot_id: int):
    p = procs.pop(bot_id, None)
    if p and p.returncode is None:
        p.terminate()
        try:
            await asyncio.wait_for(p.wait(), 10)
        except asyncio.TimeoutError:
            p.kill()
            await p.wait()
    try: (DATA_DIR / str(bot_id) / "pid").unlink()
    except Exception: pass


async def stop_all():
    for bid in list(procs):
        await stop(bid)


def purge(bot_id: int):
    """Bot o'chirilganda uning papkasini ham o'chiradi."""
    shutil.rmtree(DATA_DIR / str(bot_id), ignore_errors=True)


def alive(bot_id: int) -> bool:
    p = procs.get(bot_id)
    return p is not None and p.returncode is None
