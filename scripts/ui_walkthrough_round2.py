"""Click through the LIVE portal in Edge (review round 2, PROGRESS section 54): connections (edit from the list, clickable steps, mapping
guide, actual values with a reason), the golden-record preview (search, paging, unmask), delete buttons in the lists, the no-code matching
rules form (adds and proposes a rule). Fails on console errors, server errors, red messages and stray [object]/null/undefined text.

Needs the demo workspace portal on http://127.0.0.1:8765 (admin / admin) and Playwright (dev tool only). It READS the demo insurance-file project
(run R725ae940327a) and CHANGES only the demo project (one proposed matching-rules version) and a throwaway project it deletes again.
The portal allows 10 sign-ins a minute per address.
"""
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


def wait(page):
    page.wait_for_timeout(250)
    page.wait_for_function("() => !document.getElementById('view').hasAttribute('aria-busy')", timeout=30000)
    page.wait_for_timeout(250)


def leaks(page, where):
    b = page.locator("#banner")
    txt = page.locator("#view").inner_text() + " " + (b.inner_text() if b.is_visible() else "")
    for x in re.findall(r"\[object \w+\]|\bundefined\b|\bnull\b|\bNaN\b", txt):
        note("stray text", f"{where}: {x!r}")
    if b.is_visible() and "bad" in (b.get_attribute("class") or ""):
        note("error message", where + ": " + b.inner_text())


def reason(page, text="checking the values on screen"):
    page.locator("#dialog textarea").fill(text)
    page.locator("#dialog .buttons button").last.click()
    wait(page)


with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.on("console", lambda m: note("console", m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: note("page error", str(e)))
    page.on("response", lambda r: note("http", f"{r.status} {r.url}") if r.status >= 500 else None)
    page.goto(BASE + "#/projects/insurance-file/setup/data")
    page.fill("#username", "admin"); page.fill("#password", "admin"); page.click("#signin")
    page.wait_for_selector("#nav a"); wait(page)
    leaks(page, "connections tab")
    print("coverage table:", page.get_by_role("heading", name="Which field comes from which source").count())
    page.screenshot(path=str(OUT / "50_connections.png"), full_page=True)
    # 1. Edit from the list opens the mapping step directly
    page.get_by_role("button", name="Edit connection insurance").click(); wait(page)
    print("edit opens step:", page.locator(".steps [aria-current=step]").inner_text())
    leaks(page, "mapping")
    # real values with a reason
    page.get_by_role("button", name="Show actual values").click(); wait(page)          # no reason asked by default (the view is logged)
    if page.locator("#dialog textarea").count():
        note("flow", "showing actual values asked for a reason")
    print("sample says:", page.locator("h3", has_text="Your data").inner_text())
    page.screenshot(path=str(OUT / "51_mapping.png"), full_page=True)
    # 3. click the steps
    page.locator(".steps button", has_text="Preview and save").click(); wait(page)
    print("after clicking step 3:", page.locator(".steps [aria-current=step]").inner_text())
    page.locator(".steps button", has_text="Connect").click(); wait(page)
    print("after clicking step 1:", page.locator(".steps [aria-current=step]").inner_text())
    page.locator(".steps button", has_text="Map columns").click(); wait(page)
    print("after clicking step 2:", page.locator(".steps [aria-current=step]").inner_text())
    leaks(page, "wizard steps")
    page.get_by_role("button", name="Cancel").click(); wait(page)
    # 2. results preview on a demo insurance-file run
    page.goto(BASE + "#/projects/insurance-file/runs/R725ae940327a/results"); wait(page)
    rows_masked = page.locator(".preview tbody tr").count()
    print("preview rows:", rows_masked, "| masked hint:", page.locator(".preview .hint").first.inner_text()[:90])
    leaks(page, "results preview")
    page.screenshot(path=str(OUT / "52_results.png"), full_page=True)
    page.locator(".preview").get_by_role("button", name="Show identifiers").click(); wait(page)
    if page.locator("#dialog textarea").count():
        note("flow", "showing identifiers asked for a reason")
    print("after unmask:", page.locator(".preview .hint").first.inner_text()[:80])
    page.locator(".preview input[type=search]").fill("a"); page.locator(".preview").get_by_role("button", name="Search").click(); wait(page)
    print("search a:", page.locator(".preview .pager").inner_text() if page.locator(".preview .pager").count() else "no pager")
    page.locator(".preview").get_by_role("button", name="Next").click(); wait(page)
    print("page 2:", page.locator(".preview .pager").inner_text())
    leaks(page, "results paging")
    # 5. delete buttons in the lists
    page.goto(BASE + "#/projects"); wait(page)
    print("project delete buttons:", page.get_by_role("button", name=re.compile("^Delete project ")).count())
    page.goto(BASE + "#/runs"); wait(page)
    print("run delete buttons:", page.get_by_role("button", name=re.compile("^Delete run ")).count())
    leaks(page, "runs list")
    # throwaway project: create, then delete it from the list
    page.goto(BASE + "#/projects"); wait(page)
    page.get_by_role("button", name="New project").first.click()
    page.get_by_label("Name", exact=True).fill("Walk delete test")
    page.get_by_role("button", name="Create and continue").click(); wait(page)
    page.goto(BASE + "#/projects"); wait(page)
    page.get_by_role("button", name="Delete project Walk delete test").click()
    page.get_by_label("Type the project id").fill("walk-delete-test")
    page.get_by_label("Why is it deleted?").fill("only a click-through test")
    page.locator("#dialog").get_by_role("button", name="Delete project").click(); wait(page)
    print("after delete from list:", page.locator("#banner").inner_text()[:60])
    # 6. matching rules form on the demo project
    page.goto(BASE + "#/projects/customer-master/setup/matching"); wait(page)
    leaks(page, "matching rules")
    print("rule cards:", page.locator(".rule").count(), "| identifier cards:", page.locator(".id-card").count())
    page.locator(".rule details").first.click()
    print("first rule in words:", page.locator(".rule details p").first.inner_text()[:140])
    page.screenshot(path=str(OUT / "53_rules.png"), full_page=True)
    page.get_by_role("button", name=re.compile("^(Change the rules|Change them first)$")).click(); wait(page)
    page.get_by_role("button", name="Add your own rule").click(); page.wait_for_selector("#dialog h2")
    page.get_by_label("What should happen").select_option("force_review")
    page.locator("#dialog").get_by_label("Field").first.select_option(label="Phone")
    page.locator("#dialog").get_by_label("Value").first.select_option("same primary number")
    page.get_by_role("button", name="Add a condition").click()
    page.locator("#dialog").get_by_label("Field").nth(1).select_option(label="Birth years")
    page.get_by_label("Name (shown to reviewers)").fill("Same phone but birth years more than 10 apart (walk-through)")
    page.locator("#dialog").get_by_role("button", name="Add rule").click(); wait(page)
    print("custom rule in list:", page.locator(".rule", has_text="(walk-through)").count())
    page.get_by_label("Why this change?").fill("walk-through: a phone-and-age rule")
    page.get_by_role("button", name="Save as a new version").click(); wait(page)
    print("banner:", page.locator("#banner").inner_text()[:90])
    leaks(page, "after propose")
    page.screenshot(path=str(OUT / "54_rules_after.png"), full_page=True)
    # clean up: decline the version this walk-through just proposed (the newest waiting one)
    page.locator(".pending-card").first.get_by_role("button", name="Decline").click()
    page.locator("#dialog textarea").fill("walk-through clean-up")
    page.locator("#dialog .buttons button").last.click(); wait(page)
    print("after decline:", page.locator("#banner").inner_text()[:60])
    browser.close()
print("\nPROBLEMS:", len(problems))
for p in problems:
    print(" -", p)
sys.exit(1 if problems else 0)
