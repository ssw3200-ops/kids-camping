"""실제 운영 페이지 화면 캡처 (블로그용) — Actions 에서만 실행 (비밀번호는 Secrets)"""
import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

URL = "https://ssw3200-ops.github.io/kids-camping/"
PW = os.environ["PAGE_PASSWORD"]
OUT = Path("shots")
OUT.mkdir(exist_ok=True)
GEO = {"latitude": 36.4801, "longitude": 127.2890}  # 세종시청 기준


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, geolocation=GEO,
                                  permissions=["geolocation"], locale="ko-KR", timezone_id="Asia/Seoul")
        pg = await ctx.new_page()
        await pg.goto(URL + "?shot=" + str(os.getpid()))
        await pg.wait_for_selector("#pw", state="visible")
        await pg.fill("#pw", PW)
        await pg.click("#pwBtn")
        await pg.wait_for_selector(".card", timeout=60000)
        await pg.screenshot(path=OUT / "1_첫화면.png")
        await pg.click("#loc")
        await pg.wait_for_function("document.querySelector('#count').innerText.includes('내 위치')", timeout=30000)
        await pg.click("#rad button[data-v='100']")
        await pg.wait_for_timeout(1500)
        await pg.screenshot(path=OUT / "2_내주변.png")
        await pg.click("#chips button[data-k='T']")
        await pg.wait_for_timeout(800)
        await pg.screenshot(path=OUT / "3_트램펄린.png")
        await pg.click(".card .open")
        await pg.wait_for_timeout(800)
        card = await pg.query_selector(".card")
        await card.screenshot(path=OUT / "4_자세히.png")
        await b.close()


asyncio.run(main())
