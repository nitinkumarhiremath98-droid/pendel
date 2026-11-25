"""Browser test for the Legal & Compliance section (Playwright + Chrome).

Checks the footer on normal pages, every policy page inside the SPA router, in-page section links,
deep links, the Profile data controls (download + delete) and that playback still works afterwards:

    python tests/e2e_legal.py
"""
import asyncio, json, os, subprocess, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_autonext as E  # same temporary server set-up, fake YouTube API and songs

PAGES = {"terms": "Terms of Service", "privacy": "Privacy Policy", "copyright": "Copyright & DMCA Policy",
         "refunds": "Refund & Cancellation Policy", "contact": "Contact & Grievance Officer"}


async def main():
    from playwright.async_api import async_playwright
    fails = []
    def check(ok, msg):
        print(("PASS " if ok else "FAIL ") + msg)
        if not ok:
            fails.append(msg)

    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--autoplay-policy=no-user-gesture-required", "--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        page = await ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.route("https://www.youtube.com/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=E.FAKE_YT))
        await page.goto(E.BASE + "/")
        await page.wait_for_selector(".rail .card")

        # footer is the last thing in #view and survives navigation
        last = await page.evaluate("document.querySelector('#view').lastElementChild.className")
        check(last == "site-footer", f"footer is at the end of the home page ({last})")
        links = await page.eval_on_selector_all(".site-footer a", "as => as.map(a => a.getAttribute('href'))")
        check(links == [f"/legal/{k}" for k in ("terms", "privacy", "copyright", "refunds", "contact")], f"footer links {links}")
        await page.click("a[href='/explore'][data-link]")
        await page.wait_for_timeout(600)
        check(await page.evaluate("document.querySelector('#view').lastElementChild.className") == "site-footer", "footer re-attached after navigating")

        # every page renders inside the router (no full reload)
        await page.evaluate("window.__noReload = 1")
        for key, title in PAGES.items():
            await page.click(f".site-footer a[href='/legal/{key}']")
            for _ in range(30):  # wait until the router has rendered this page (the previous page also has an h1)
                if await page.locator("#legal-h1").count() and await page.inner_text("#legal-h1") == title: break
                await page.wait_for_timeout(100)
            h1 = await page.inner_text("#legal-h1")
            check(h1 == title and await page.evaluate("window.__noReload") == 1, f"/legal/{key} renders '{h1}' in-app")
            check(await page.evaluate("document.title") == f"{title} | PENDEL", f"/legal/{key} sets the document title")
            cur = await page.get_attribute(".legal-tabs [aria-current=page]", "href")
            check(cur == f"/legal/{key}", f"/legal/{key} marks the current tab")
        body = await page.inner_text("#view")
        details = await page.evaluate("(async () => { const { LEGAL } = await import('/js/legal.js'); return [LEGAL.operator, LEGAL.email, LEGAL.location]; })()")
        for ph in details:
            check(ph in body, f"contact page shows the grievance officer's {'name / email / location'.split(' / ')[details.index(ph)]}")

        # in-page table of contents scrolls without changing the route
        await page.click(".site-footer a[href='/legal/terms']")
        await page.wait_for_selector(".legal-toc")
        await page.click(".legal-toc a[href='#uploader-warranty']")
        await page.wait_for_timeout(900)
        st = await page.evaluate("({p: location.pathname, f: document.activeElement.id, t: document.querySelector('#uploader-warranty').getBoundingClientRect().top})")
        check(st["p"] == "/legal/terms" and st["f"] == "uploader-warranty-h" and st["t"] < 200, f"TOC link jumps to the section and focuses it {st}")

        # deep link with hash, cross page
        await page.click(".site-footer a[href='/legal/privacy']")
        await page.wait_for_selector("#legal-h1")
        await page.click("a[href='/legal/terms#youtube']")
        await page.wait_for_timeout(900)
        st = await page.evaluate("({p: location.pathname, f: document.activeElement.id})")
        check(st == {"p": "/legal/terms", "f": "youtube-h"}, f"cross-page deep link lands on the section {st}")

        # direct load of a legal URL (server SPA fallback)
        await page.goto(E.BASE + "/legal/copyright")
        await page.wait_for_selector("#legal-h1")
        check("24–36 hours" in await page.inner_text("#view"), "direct load of /legal/copyright works")

        # register shows consent links; profile has data controls
        await page.goto(E.BASE + "/register")
        await page.wait_for_selector("#reg-form")
        check(await page.locator(".consent-line a[href='/legal/terms']").count() == 1, "sign-up form links to the Terms")
        await page.fill("#dn", "Legal Tester"); await page.fill("#em", "legal@example.com"); await page.fill("#pw", "Passw0rd!")
        await page.click("#reg-form button.primary")
        await page.wait_for_timeout(1200)
        await page.goto(E.BASE + "/profile")
        await page.wait_for_selector("#export-data")
        async with page.expect_download() as dl:
            await page.click("#export-data")
        path = await (await dl.value).path()
        data = json.load(open(path))
        check(data["account"]["email"] == "legal@example.com", "Download my data saves a JSON export")
        menu = await page.evaluate("document.querySelector('[data-action=account-menu]') !== null")
        if menu:
            await page.click("[data-action=account-menu]")
            check(await page.locator("#ctx-menu >> text=Legal & privacy").count() == 1, "account menu has Legal & privacy")
            await page.keyboard.press("Escape")
        await page.click("#delete-account")
        await page.fill("#del-pw", "nope")
        await page.click("#del-form .btn.danger")
        await page.wait_for_selector("#del-err .form-error")
        check("incorrect" in (await page.inner_text("#del-err")).lower(), "wrong password is refused")
        await page.fill("#del-pw", "Passw0rd!")
        await page.click("#del-form .btn.danger")
        await page.wait_for_timeout(1200)
        st = await page.evaluate("({p: location.pathname, t: localStorage.getItem('rf_token')})")
        check(st == {"p": "/", "t": None}, f"account deleted, signed out and sent home {st}")

        # playback still works after all of this
        await page.wait_for_selector(".rail .card")
        await page.click(".rail .card [data-action=play-highlight]") if await page.locator(".rail .card [data-action=play-highlight]").count() else await page.click(".rail .card")
        await page.wait_for_timeout(1500)
        title = await page.evaluate("navigator.mediaSession.metadata?.title || ''")
        check(bool(title.strip()), f"a song still starts playing ({title.strip()[:40]})")

        # mobile: footer readable, no horizontal overflow on legal pages
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.goto(E.BASE + "/legal/privacy")
        await page.wait_for_selector(".legal-table")
        ov = await page.evaluate("document.querySelector('#view').scrollWidth - document.querySelector('#view').clientWidth")
        check(ov <= 1, f"no horizontal overflow on mobile ({ov}px)")
        await page.screenshot(path=os.path.join(E.TMP, "legal_mobile.png"))
        await page.set_viewport_size({"width": 1440, "height": 900})
        await page.goto(E.BASE + "/legal/terms")
        await page.wait_for_selector("#legal-h1")
        await page.screenshot(path=os.path.join(E.TMP, "legal_terms.png"))
        check(not errors, f"no page errors: {errors}")
        await b.close()
    return fails


if __name__ == "__main__":
    E.setup_db()
    srv = subprocess.Popen([sys.executable, "-m", "server.app"], cwd=E.ROOT, env=E.ENV, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(E.BASE + "/api/config", timeout=1); break
            except Exception:
                time.sleep(0.2)
        fails = asyncio.run(main())
    finally:
        srv.terminate()
    print(f"screenshots: {E.TMP}")
    sys.exit(1 if fails else 0)
