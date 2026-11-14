"""Browser smoke test (Playwright + Chrome). Run with the server on BASE."""
import asyncio, os, sys
from playwright.async_api import async_playwright

BASE = os.environ.get("BASE", "http://localhost:8765")
OUT = os.environ.get("SHOTS", "/home/claude/shots")
errors = []

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--autoplay-policy=no-user-gesture-required", "--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type in ("error",) else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        async def shot(name): await page.screenshot(path=f"{OUT}/{name}.png")
        await page.goto(BASE + "/"); await page.wait_for_selector(".rail .card"); await page.wait_for_timeout(800); await shot("01_home")
        # play a song from a card
        await page.hover(".rail .card >> nth=0"); await page.click(".rail .card >> nth=0 >> .card-play")
        await page.wait_for_timeout(1500)
        t = await page.evaluate("document.getElementById('audio').currentTime"); print("home play currentTime", round(t,2))
        await shot("02_playing")
        # highlights
        await page.click(".sidebar [data-nav=highlights]"); await page.wait_for_selector("#hl-go"); await shot("03_hl_start")
        await page.click("#hl-go"); await page.wait_for_timeout(3000)
        st = await page.evaluate("({src: document.getElementById('audio').src, t: document.getElementById('audio').currentTime, paused: document.getElementById('audio').paused})"); print("highlight", st)
        await shot("04_hl_playing")
        await page.keyboard.press("ArrowDown"); await page.wait_for_timeout(2000)
        st2 = await page.evaluate("document.getElementById('audio').src"); print("after swipe src", st2.split('?')[0])
        await shot("05_hl_next")
        # play full from highlight
        await page.click(".hl-slide[data-i='1'] [data-hl=full]"); await page.wait_for_timeout(1500)
        print("fullplayer visible", await page.is_visible("#fullplayer"))
        await page.click("[data-action=toggle-fp-lyrics]"); await page.wait_for_timeout(1500); await shot("06_fullplayer")
        await page.click("[data-action=close-fullplayer]")
        # search
        await page.fill("#searchbox input", "lov"); await page.wait_for_selector("#suggest li"); await shot("07_suggest")
        await page.keyboard.press("Escape"); await page.goto(BASE + "/search?q=love"); await page.wait_for_selector(".songlist"); await shot("08_search")
        # language page
        await page.goto(BASE + "/language/te"); await page.wait_for_selector(".hero"); await page.wait_for_timeout(500); await shot("09_language")
        await page.goto(BASE + "/songs"); await page.wait_for_selector(".songlist .row[data-song-row]"); await shot("10_full_songs")
        await page.goto(BASE + "/song/dil-ki-raah"); await page.wait_for_selector(".hero"); await page.wait_for_timeout(500); await shot("11_song")
        # login as demo
        await page.goto(BASE + "/login"); await page.fill("#em", "demo@pendel.local"); await page.fill("#pw", "Demo@12345"); await page.click("#login-form button")
        await page.wait_for_selector(".avatar"); await page.wait_for_timeout(800)
        # favourite + playlist
        await page.goto(BASE + "/song/vennela-daari"); await page.wait_for_selector(".hero [data-action=fav]")
        await page.click(".hero [data-action=fav]"); await page.wait_for_timeout(500)
        await page.click(".hero [data-action=add-to-playlist]"); await page.fill("#np", "Late Night Telugu"); await page.click("form[data-new] button"); await page.wait_for_timeout(800)
        await page.goto(BASE + "/library"); await page.wait_for_selector(".grid .card"); await shot("12_library")
        await page.goto(BASE + "/library/favourites"); await page.wait_for_selector(".songlist"); await shot("13_favs")
        await page.goto(BASE + "/premium"); await page.wait_for_selector("[data-plan]"); await page.click("[data-plan='premium-monthly']")
        await page.wait_for_selector("[data-out=success]"); await shot("14_checkout"); await page.click("[data-out=success]"); await page.wait_for_url("**/profile"); await page.wait_for_timeout(800); await shot("15_profile")
        # admin
        await page.click(".avatar"); await page.click("text=Log out"); await page.wait_for_timeout(300)
        await page.goto(BASE + "/login"); await page.fill("#em", "admin@pendel.local"); await page.fill("#pw", "Admin@12345"); await page.click("#login-form button"); await page.wait_for_selector(".avatar")
        await page.goto(BASE + "/admin"); await page.wait_for_selector(".chart"); await shot("16_admin_dash")
        await page.goto(BASE + "/admin/songs"); await page.wait_for_selector("[data-edit]"); await page.click("[data-edit] >> nth=0")
        await page.wait_for_selector("#wave"); await page.wait_for_timeout(2500); await shot("17_admin_editor")
        await page.click("#hauto"); await page.wait_for_timeout(2500); print("auto msg:", await page.inner_text("#hmsg"))
        await page.click("#hsave"); await page.wait_for_timeout(2500); print("save msg:", await page.inner_text("#hmsg"))
        await page.keyboard.press("Escape")
        # light theme
        await page.click("[data-action=toggle-theme]"); await page.goto(BASE + "/"); await page.wait_for_selector(".rail .card"); await page.wait_for_timeout(600); await shot("18_light")
        await page.click("[data-action=toggle-theme]")
        # mobile
        m = await b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        mp = await m.new_page(); mp.on("pageerror", lambda e: errors.append(f"mobile pageerror: {e}"))
        await mp.goto(BASE + "/"); await mp.wait_for_selector(".rail .card"); await mp.wait_for_timeout(600); await mp.screenshot(path=f"{OUT}/20_m_home.png")
        await mp.goto(BASE + "/highlights/love"); await mp.wait_for_selector("#hl-go"); await mp.tap("#hl-go"); await mp.wait_for_timeout(2500); await mp.screenshot(path=f"{OUT}/21_m_hl.png")
        await mp.goto(BASE + "/explore"); await mp.wait_for_selector(".tile"); await mp.screenshot(path=f"{OUT}/22_m_explore.png")
        await b.close()
    print("ERRORS:", len(errors)); [print(" ", e) for e in errors[:30]]

asyncio.run(main())
