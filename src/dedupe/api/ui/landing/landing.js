// Aurionpro Dedupe landing page. The story at the top is driven by scroll: this script only measures how far the visitor has scrolled through
// it and writes that as one number (--p, from 0 to 1) on the story; the stylesheet works out every scrap's position, the sun and the golden
// record from it, so only transform and opacity change. Also: reveals as sections scroll in, the step list that follows the panel in view,
// and the sources belt. Plain DOM with class changes and CSSOM only (strict content-security policy, no inline code).
// People who ask their system for reduced motion (prefers-reduced-motion) get everything in its finished state and nothing that moves.
(() => {
  "use strict";
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];

  // headline lines rise in once the font is ready (or straight away)
  const ready = () => requestAnimationFrame(() => document.body.classList.add("ready"));
  if (document.fonts && document.fonts.ready && !still) document.fonts.ready.then(ready); else ready();

  // the story: scraps drift together as the page scrolls. Without motion the stylesheet lays them out still, side by side.
  const story = $("#story");
  const track = $(".track");
  if (story && track && !still) {
    document.documentElement.classList.add("motion");
    let queued = false;
    let last = -1;
    const update = () => {
      queued = false;
      const box = track.getBoundingClientRect();
      const run = box.height - window.innerHeight;
      const p = run > 0 ? Math.min(Math.max(-box.top / run, 0), 1) : 1;
      const rounded = Math.round(p * 1000) / 1000;
      if (rounded === last) return;
      last = rounded;
      story.style.setProperty("--p", String(rounded));
    };
    const ask = () => { if (!queued) { queued = true; requestAnimationFrame(update); } };
    window.addEventListener("scroll", ask, { passive: true });
    window.addEventListener("resize", ask);
    update();
  }

  // a soft shadow under the header once the page has scrolled
  const nav = $(".nav");
  const onScroll = () => nav.classList.toggle("scrolled", window.scrollY > 8);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // the example score counts up with its needle
  const countUp = (el) => {
    const to = Number(el.dataset.to) || 0;
    if (still) { el.textContent = String(to); return; }
    const start = performance.now() + 500;
    const tick = (now) => {
      const t = Math.min(Math.max((now - start) / 1800, 0), 1);
      el.textContent = String(Math.round(to * (1 - Math.pow(1 - t, 3))));
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };

  // sections and panels animate in once, the first time they are seen
  const targets = $$(".reveal, .panel");
  if (still || !("IntersectionObserver" in window)) {
    targets.forEach((n) => n.classList.add("in"));
    $$(".count").forEach(countUp);
  } else {
    const seen = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        e.target.classList.add("in");
        $$(".count", e.target).forEach(countUp);
        seen.unobserve(e.target);
      }
    }, { threshold: 0.2, rootMargin: "0px 0px -8% 0px" });
    targets.forEach((n) => seen.observe(n));
  }

  // the step list marks the panel in the middle of the screen
  const steps = $$("#steps li");
  if (steps.length && "IntersectionObserver" in window) {
    const mark = (i) => steps.forEach((li) => li.classList.toggle("active", li.dataset.step === i));
    const middle = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) mark(e.target.dataset.step);
    }, { rootMargin: "-45% 0px -45% 0px" });
    $$(".panel").forEach((p) => middle.observe(p));
    mark("0");
  }

  // the sources belt: a second copy of the tags makes the loop seamless
  const belt = $(".belt");
  if (belt && !still) $$(".logo", belt).forEach((l) => belt.append(l.cloneNode(true)));
})();
