r"""Click through the LIVE portal in Edge and fail on any console error, server error or red message.

Needs a running portal on http://127.0.0.1:8765 (local sign-in, the demo workspace, account admin / admin) and Playwright
(dev tool only, not a project dependency): .venv\Scripts\python -m pip install playwright. Uses the installed Microsoft Edge.
The portal allows 10 sign-ins a minute per address: wait a minute between runs of the two scripts.
Second pass: dialogs and keyboard, rules, connections, schedules, dark theme."""
import re
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765/ui"
OUT = Path(tempfile.gettempdir()) / "dedupe_ui_walk"          # screenshots land here
problems = []


def note(kind, msg):
    problems.append(f"{kind}: {msg}")
    print(f"  !! {kind}: {msg}")


def wait(page):
    page.wait_for_timeout(250)
    page.wait_for_function("() => !document.getElementById('view').hasAttribute('aria-busy')", timeout=20000)
    page.wait_for_timeout(150)


def bad(page, where):
    b = page.locator("#banner")
    if b.is_visible() and "bad" in (b.get_attribute("class") or ""):
        note("error message", where + ": " + b.locator(".msg").inner_text())
    txt = page.locator("#view").inner_text() + " " + (b.inner_text() if b.is_visible() else "")
    for x in re.findall(r"\[object \w+\]|undefined|null|NaN", txt):       # an object, a null or a failed number put on screen
        note("stray text", f"{where}: {x!r} on the screen")


with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900}, color_scheme="dark")
    page.on("console", lambda m: note("console", m.text + " " + str(m.location)) if m.type == "error" else None)
    page.on("pageerror", lambda e: note("page error", str(e)))
    page.goto(BASE + "#/projects/customer-master/setup/matching")
    page.fill("#username", "admin"); page.fill("#password", "admin"); page.click("#signin")
    page.wait_for_selector("#nav a"); wait(page)
    print("landed on", page.url.split("/ui")[1], "theme", page.evaluate("document.documentElement.dataset.theme"))
    # rules: a plain summary first; the editor opens on "Change the rules" and its JSON part is filled; a version can be viewed
    print("summary steps:", page.locator(".rule-step").count(), "| score bar:", page.locator(".score-bar").count())
    if not page.locator(".score-bar").count():
        note("flow", "the rules summary has no score bar")
    view = page.get_by_role("button", name="View")
    if view.count():
        view.first.click(); wait(page); print("viewed a rule version")
    page.get_by_role("button", name=re.compile("^(Change the rules|Change them first)$")).click(); wait(page)
    page.locator("details.advanced summary", has_text="For experts").click(); page.wait_for_timeout(300)
    editor = page.locator("textarea.code").first
    print("rules editor filled:", len(editor.input_value()) > 20)
    if len(editor.input_value()) < 20:
        note("flow", "rules editor is empty")
    page.get_by_role("button", name="Cancel").click(); wait(page)
    bad(page, "rules")
    page.screenshot(path=str(OUT / "10_rules_dark.png"), full_page=True)

    # connection page from the Connections tab, keyboard
    page.goto(BASE + "#/projects/customer-master/setup/data"); wait(page)
    page.locator("#view tbody button.link").first.click(); wait(page)
    print("connection:", page.locator("#view h1").inner_text(), page.url.split("/ui")[1]); bad(page, "connection")
    page.screenshot(path=str(OUT / "11_connection_dark.png"), full_page=True)

    # schedules: open the edit dialog, Escape closes it and focus goes back to the Edit button
    page.goto(BASE + "#/projects/customer-master/schedules"); wait(page)
    edit = page.get_by_role("button", name="Edit").first
    if edit.count():
        edit.focus(); page.keyboard.press("Enter"); page.wait_for_selector("#dialog h2")
        print("dialog:", page.locator("#dialog h2").inner_text(), "| app inert:", page.evaluate("document.getElementById('app').inert"))
        page.locator("#dialog select").first.select_option("hourly")
        print("time label after hourly:", page.locator("#dialog label", has_text="Minutes past").count() == 1)
        print("load choices:", page.locator("#dialog .choice-card").count(), "| incremental chosen:", page.locator("#dialog .choice-card input[value=incremental]").is_checked())
        if page.locator("#dialog .choice-card").count() != 2:
            note("flow", "the schedule dialog does not offer incremental and full reload")
        page.keyboard.press("Escape"); page.wait_for_timeout(200)
        print("closed:", page.locator("#overlay").is_hidden(), "| focus back on:", page.evaluate("document.activeElement.textContent"))
        if page.evaluate("document.activeElement.textContent") != "Edit":
            note("a11y", "focus did not return to the Edit button")
    else:
        note("flow", "no schedule to edit")

    # tabs with arrow keys
    page.goto(BASE + "#/projects/customer-master"); wait(page)
    page.locator("[role=tab][aria-selected=true]").focus(); page.keyboard.press("ArrowRight")
    print("arrow moved focus to:", page.evaluate("document.activeElement.textContent"))

    # compare page, dark, decision panel near the top
    page.goto(BASE + "#/review"); wait(page)
    page.get_by_role("button", name="Open full page").first.click(); wait(page)
    y = page.get_by_role("button", name="Same person").first.bounding_box()["y"]
    print("Same person button at y =", round(y))
    if y > 900:
        note("layout", "decision buttons are below the first screen")
    page.screenshot(path=str(OUT / "12_compare_dark.png"))
    page.get_by_role("button", name="Open person").first.click(); wait(page); bad(page, "person")
    page.screenshot(path=str(OUT / "13_person_dark.png"), full_page=True)

    # approvals and decisions
    for part in ("matching", "golden", "technical"):
        page.goto(BASE + "#/projects/customer-master/setup/" + part); wait(page); bad(page, "setup/" + part)
    for path in ["#/approvals", "#/review/decisions", "#/projects/insurance-file/results", "#/audit"]:
        page.goto(BASE + path); wait(page); bad(page, path)
        print(path, "->", page.locator("#view h1").inner_text())
    page.screenshot(path=str(OUT / "14_audit_dark.png"))
    browser.close()

print("\nPROBLEMS:", len(problems))
for p in problems:
    print(" -", p)
sys.exit(1 if problems else 0)
