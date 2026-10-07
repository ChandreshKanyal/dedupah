r"""Click through the LIVE portal in Edge and fail on any console error, server error or red message.

Needs a running portal on http://127.0.0.1:8765 (local sign-in, the demo workspace, account admin / admin) and Playwright
(dev tool only, not a project dependency): .venv\Scripts\python -m pip install playwright. Uses the installed Microsoft Edge.
The portal allows 10 sign-ins a minute per address: wait a minute between runs of the two scripts.
Click through the live portal in Edge and report every console error, failed request and error message shown."""
import re
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765/ui"
OUT = Path(tempfile.gettempdir()) / "dedupe_ui_walk"          # screenshots land here
OUT.mkdir(exist_ok=True)
problems = []


def note(kind, msg):
    problems.append(f"{kind}: {msg}")
    print(f"  !! {kind}: {msg}")


def banner(page):
    b = page.locator("#banner")
    if b.is_visible():
        return (b.get_attribute("class") or "", b.locator(".msg").inner_text())
    return None


def leaks(page, where):
    """Text that only appears when code put an object, a null or a failed number on the screen."""
    txt = page.locator("#view").inner_text() + " " + (page.locator("#banner").inner_text() if page.locator("#banner").is_visible() else "")
    for bad in re.findall(r"\[object \w+\]|undefined|null|NaN", txt):
        note("stray text", f"{where}: {bad!r} on the screen")


def check(page, where):
    b = banner(page)
    if b and "bad" in b[0]:
        note("error message", f"{where}: {b[1]}")
    leaks(page, where)
    h1 = page.locator("#view h1").first.inner_text() if page.locator("#view h1").count() else "(no h1)"
    print(f"{where}: h1={h1!r} url={page.url.split('/ui')[1]} title={page.title()!r}")
    return h1


def wait(page):
    page.wait_for_timeout(250)
    page.wait_for_function("() => !document.getElementById('view').hasAttribute('aria-busy')", timeout=20000)
    page.wait_for_timeout(150)


def signin(page):
    page.fill("#username", "admin")
    page.fill("#password", "admin")
    page.click("#signin")
    page.wait_for_selector("#nav a", timeout=15000)
    wait(page)


with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.on("console", lambda m: note("console", m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: note("page error", str(e)))
    page.on("response", lambda r: note("http", f"{r.status} {r.request.method} {r.url}") if r.status >= 500 else None)
    page.goto(BASE)
    signin(page)
    check(page, "home")
    page.screenshot(path=str(OUT / "01_home.png"), full_page=True)

    for name in ["Projects", "Runs", "Review inbox", "Decisions", "Approvals", "Audit log", "Users", "Roles"]:
        page.click(f"#nav a[data-name='{name}']")
        wait(page)
        check(page, name)
        page.screenshot(path=str(OUT / f"02_{name.replace(' ', '_')}.png"), full_page=True)

    # a project, every tab
    page.click("#nav a[data-name='Projects']"); wait(page)
    page.get_by_role("button", name="Customer master", exact=True).click(); wait(page)
    tabs = page.locator("[role=tab]").all_inner_texts()
    print("project tabs:", tabs)
    for t in tabs:
        page.get_by_role("tab", name=t).click(); wait(page)
        check(page, f"project/{t}")
        page.screenshot(path=str(OUT / f"03_project_{t}.png"), full_page=True)

    # a run, every tab
    page.get_by_role("tab", name="Runs").click(); wait(page)
    page.locator("#view tbody button.link").first.click(); wait(page)
    tabs = page.locator("[role=tab]").all_inner_texts()
    print("run tabs:", tabs)
    for t in tabs:
        page.get_by_role("tab", name=t).click(); wait(page)
        check(page, f"run/{t}")
        page.screenshot(path=str(OUT / f"04_run_{t.replace(' ', '_')}.png"), full_page=True)

    # review: start from the inbox, decide with the keyboard, undo from the message
    page.click("#nav a[data-name='Review inbox']"); wait(page)
    start = page.get_by_role("button", name="Start reviewing").first
    if start.count():
        start.click(); wait(page)
        first = check(page, "compare (first)")
        sub1 = page.locator(".page-head p").last.inner_text()
        page.screenshot(path=str(OUT / "05_compare.png"), full_page=True)
        url1 = page.url
        page.keyboard.press("u"); wait(page)
        check(page, "compare after U")
        print("   moved on:", page.url != url1, "| banner:", banner(page))
        page.screenshot(path=str(OUT / "06_after_decide.png"), full_page=True)
        undo = page.locator("#banner button", has_text="Undo")
        if undo.count():
            undo.click(); wait(page)
            print("   after undo:", page.url == url1, "| banner:", banner(page))
        else:
            note("flow", "no Undo offered after a decision")
        # back / forward
        page.go_back(); wait(page); check(page, "after Back")
        page.go_forward(); wait(page); check(page, "after Forward")
        # open a person from the compare page
        op = page.get_by_role("button", name="Open person").first
        if op.count():
            op.click(); wait(page); check(page, "person")
            page.screenshot(path=str(OUT / "07_person.png"), full_page=True)
        # reload keeps the screen (after signing in again)
        here = page.url
        page.reload(); signin(page)
        print("   reload returned to the same screen:", page.url == here)
        if page.url != here:
            note("flow", "reload did not come back to " + here)
    else:
        note("flow", "no Start reviewing button in the inbox")

    # create a throwaway project from the UI, then delete it from its settings
    page.click("#nav a[data-name='Projects']"); wait(page)
    page.get_by_role("button", name="New project").first.click()
    page.get_by_label("Name", exact=True).fill("Walk test project")
    page.get_by_role("button", name="Create project").click(); wait(page)
    check(page, "new project")
    page.get_by_role("tab", name="Settings").click(); wait(page)
    page.get_by_role("button", name="Delete project").click()
    page.get_by_label("Type the project id").fill("walk-test-project")
    page.get_by_label("Why is it deleted?").fill("only a click-through test")
    page.locator("#dialog").get_by_role("button", name="Delete project").click(); wait(page)
    check(page, "after delete project")
    print("   banner:", banner(page))

    # keyboard only: Tab to a project row button and press Enter
    page.click("#nav a[data-name='Projects']"); wait(page)
    page.locator("#view tbody button.link").first.focus()
    page.keyboard.press("Enter"); wait(page)
    check(page, "keyboard open project")

    # phone width: nothing wider than the screen
    phone = browser.new_page(viewport={"width": 375, "height": 740})
    phone.on("pageerror", lambda e: note("page error (phone)", str(e)))
    phone.goto(BASE); signin(phone)
    for path in ["#/", "#/projects/customer-master", "#/review", "#/projects/customer-master/runs"]:
        phone.goto(BASE + path); signin(phone) if phone.locator("#username").is_visible() else None; wait(phone)
        over = phone.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        print(f"phone {path}: overflow {over}px")
        if over > 2:
            note("layout", f"{path} is {over}px wider than a phone")
        phone.screenshot(path=str(OUT / f"08_phone_{path.strip('#/').replace('/', '_') or 'home'}.png"), full_page=True)
    browser.close()

print("\nPROBLEMS:", len(problems))
for p in problems:
    print(" -", p)
sys.exit(1 if problems else 0)
