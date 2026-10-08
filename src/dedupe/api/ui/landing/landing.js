// Aurionpro Dedupe landing page: the merge animation in the hero, reveals as sections scroll in, the step list that follows the panel in
// view, and the sources marquee. Everything is plain DOM with class changes (no inline code: strict content-security policy). People who
// ask their system for reduced motion get every element in its finished state and nothing that moves.
(() => {
  "use strict";
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];

  // headline lines rise in once the font is ready (or straight away)
  const ready = () => requestAnimationFrame(() => document.body.classList.add("ready"));
  if (document.fonts && document.fonts.ready && !still) document.fonts.ready.then(ready); else ready();

  // a hairline under the header once the page has scrolled
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

  // the sources marquee: a second copy of the logos makes the loop seamless
  const track = $(".track");
  if (track && !still) $$(".logo", track).forEach((l) => track.append(l.cloneNode(true)));

  // the hero: five records appear, their shared values light up, they merge, the golden record appears; then it starts again
  const demo = $("#demo");
  if (!demo) return;
  const show = (phase) => { demo.className = "demo " + phase; };
  if (still) { show("p1 p2 p3 p4"); return; }
  const timeline = [[0, "p1"], [1500, "p1 p2"], [4100, "p1 p2 p3"], [5000, "p1 p2 p3 p4"], [10000, "p1 p2 p3 p4 out"], [10700, "reset"]];
  const LOOP = 11000;
  let timers = [];
  let playing = false;
  const stop = () => { timers.forEach(clearTimeout); timers = []; playing = false; };
  const play = (delay) => {
    stop();
    playing = true;
    timers = timeline.map(([t, phase]) => setTimeout(() => show(phase), t + delay));
    timers.push(setTimeout(() => play(0), LOOP + delay));
  };
  let visible = true;
  const resume = () => { if (visible && !document.hidden && !playing) { show("reset"); play(300); } };
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(([e]) => {
      visible = e.isIntersecting;
      if (visible) resume(); else { stop(); show("p1 p2 p3 p4"); }
    }, { threshold: 0.25 }).observe(demo);
  }
  document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); else resume(); });
  play(700);
})();
