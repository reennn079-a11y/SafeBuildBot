import json
import os
from abc import ABC, abstractmethod

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=25)


class ProviderError(Exception):
    pass


# SMS-Activate protokolidagi davlat ID -> umumiy kalit (5sim nomlari bilan bir xil).
# Ro'yxat to'liq emas: kerak bo'lsa to'ldiring yoki provayder hujjatini tekshiring.
ACTIVATE_COUNTRIES = {
    "0": "russia", "1": "ukraine", "2": "kazakhstan", "3": "china", "4": "philippines",
    "6": "indonesia", "7": "malaysia", "10": "vietnam", "11": "kyrgyzstan", "12": "usa",
    "15": "poland", "16": "england", "22": "india", "40": "uzbekistan", "43": "germany",
    "51": "belarus", "52": "thailand", "62": "turkey", "73": "brazil", "78": "france",
}


class Provider(ABC):
    title = "Server"

    @abstractmethod
    async def get_number(self, country: str) -> tuple[str, str]:
        """(order_id, phone)"""

    @abstractmethod
    async def get_code(self, order_id: str) -> str | None: ...

    @abstractmethod
    async def cancel(self, order_id: str) -> None:
        """Bekor qilolmasa ProviderError"""

    @abstractmethod
    async def finish(self, order_id: str) -> None: ...

    async def balance(self) -> float:
        raise ProviderError("balance qo'llab-quvvatlanmaydi")

    async def prices(self) -> dict[str, float]:
        """{davlat_kodi: eng arzon narx (provayder valyutasida)}"""
        raise ProviderError("prices qo'llab-quvvatlanmaydi")

    def ckey(self, code: str) -> str:
        """Provayderlar orasida solishtirish uchun umumiy davlat kaliti"""
        return code.lower()


class ActivateStyle(Provider):
    """SMS-Activate protokoli (ko'p provayderlar shuni ishlatadi)."""

    def __init__(self, base_url: str, api_key: str, service: str = "tg"):
        self.url, self.key, self.service = base_url, api_key, service

    async def _call(self, **params) -> str:
        params["api_key"] = self.key
        async with aiohttp.ClientSession() as s:
            async with s.get(self.url, params=params, timeout=TIMEOUT) as r:
                return (await r.text()).strip()

    async def get_number(self, country):
        res = await self._call(action="getNumber", service=self.service, country=country)
        if res.startswith("ACCESS_NUMBER"):
            _, oid, phone = res.split(":")
            return oid, phone
        raise ProviderError(res)

    async def get_code(self, order_id):
        res = await self._call(action="getStatus", id=order_id)
        if res.startswith("STATUS_OK"):
            return res.split(":", 1)[1]
        return None

    async def cancel(self, order_id):
        res = await self._call(action="setStatus", id=order_id, status=8)
        if not res.startswith("ACCESS_CANCEL"):
            raise ProviderError(res)

    async def finish(self, order_id):
        await self._call(action="setStatus", id=order_id, status=6)

    async def balance(self):
        res = await self._call(action="getBalance")
        if res.startswith("ACCESS_BALANCE"):
            return float(res.split(":")[1])
        raise ProviderError(res)

    async def prices(self):
        res = await self._call(action="getPrices", service=self.service)
        try:
            data = json.loads(res)
        except ValueError:
            raise ProviderError(res[:200])
        best = {}
        for country, v in data.items():
            node = v.get(self.service) if isinstance(v, dict) else None
            if node and float(node.get("count", 0) or 0) > 0:
                best[str(country)] = float(node["cost"])
        return best

    def ckey(self, code):
        return ACTIVATE_COUNTRIES.get(str(code), f"id{code}")


class FiveSim(Provider):
    """5sim.net. country = nom (russia, kazakhstan, ...)"""

    BASE = "https://5sim.net/v1"

    def __init__(self, token: str, service: str = "telegram"):
        self.h = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.service = service

    async def _get(self, path, **params):
        async with aiohttp.ClientSession(headers=self.h) as s:
            async with s.get(self.BASE + path, params=params or None, timeout=TIMEOUT) as r:
                if r.status != 200:
                    raise ProviderError(await r.text())
                return await r.json()

    async def get_number(self, country):
        d = await self._get(f"/user/buy/activation/{country}/any/{self.service}")
        return str(d["id"]), str(d["phone"]).lstrip("+")

    async def get_code(self, order_id):
        d = await self._get(f"/user/check/{order_id}")
        if d.get("sms"):
            return d["sms"][0]["code"]
        return None

    async def cancel(self, order_id):
        await self._get(f"/user/cancel/{order_id}")

    async def finish(self, order_id):
        await self._get(f"/user/finish/{order_id}")

    async def balance(self):
        d = await self._get("/user/profile")
        return float(d["balance"])

    async def prices(self):
        data = await self._get("/guest/prices", product=self.service)
        best: dict[str, float] = {}

        def walk(node, path):
            if not isinstance(node, dict):
                return
            if "cost" in node and "count" in node:
                names = [x for x in path if x.lower() != self.service]
                if names and (node.get("count") or 0) > 0:
                    c = float(node["cost"])
                    best[names[0]] = min(best.get(names[0], c), c)
                return
            for k, v in node.items():
                walk(v, path + [str(k)])

        walk(data, [])
        return best


def build(cfg: dict | None = None) -> dict[int, Provider]:
    """cfg: {n: {type, token | url, key, service}} (bot bazasidan); bo'lmasa .env dagi SERVER1..9"""
    out: dict[int, Provider] = {}
    for n in range(1, 10):
        c = (cfg or {}).get(n)
        if c is None:
            c = {"type": os.getenv(f"SERVER{n}_TYPE", "").lower(), "token": os.getenv(f"SERVER{n}_TOKEN"),
                 "url": os.getenv(f"SERVER{n}_URL"), "key": os.getenv(f"SERVER{n}_KEY"),
                 "service": os.getenv(f"SERVER{n}_SERVICE", "tg")}
        if c.get("type") == "5sim" and c.get("token"):
            out[n] = FiveSim(c["token"])
        elif c.get("type") == "activate" and c.get("url") and c.get("key"):
            out[n] = ActivateStyle(c["url"], c["key"], c.get("service") or "tg")
    return out


PROVIDERS: dict[int, Provider] = {}


def load(cfg: dict | None = None):
    PROVIDERS.clear()
    PROVIDERS.update(build(cfg))
