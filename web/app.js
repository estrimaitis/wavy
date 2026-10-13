"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const fmtTime = (s) => {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60), r = Math.floor(s % 60);
  return `${m}:${String(r).padStart(2, "0")}`;
};
const fmtAgo = (t) => {
  const d = (Date.now() / 1000 - t);
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
};

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return res.json();
}

let toastTimer;
function toast(msg, ms = 3500) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), ms);
}

function showView(name) {
  for (const v of ["home", "processing", "mixer"]) $(`#view-${v}`).hidden = v !== name;
}

/* ------------------------------------------------------------------ routing */

let current = null; // { id, poll, mixer }

function teardown() {
  if (!current) return;
  clearTimeout(current.poll);
  current.mixer?.destroy();
  current = null;
}

function route() {
  teardown();
  const m = location.hash.match(/^#\/song\/([a-z0-9]+)(?:\?t=([\d.]+))?/);
  if (m) openSong(m[1], m[2] ? +m[2] : 0);
  else openHome();
}
window.addEventListener("hashchange", route);

/* ------------------------------------------------------------------ home */

const OPTS_KEY = "wavy.options";
function loadOptions() {
  try {
    const o = JSON.parse(localStorage.getItem(OPTS_KEY) || "{}");
    for (const k of ["singers", "drumkit", "lyrics"]) if (k in o) $(`#opt-${k}`).checked = o[k];
    if (o.language) $("#opt-language").value = o.language;
  } catch { /* storage unavailable */ }
}
function saveOptions() {
  try {
    localStorage.setItem(OPTS_KEY, JSON.stringify({
      singers: $("#opt-singers").checked, drumkit: $("#opt-drumkit").checked,
      lyrics: $("#opt-lyrics").checked, language: $("#opt-language").value,
    }));
  } catch { /* storage unavailable */ }
}

const STATUS_TEXT = { queued: "Queued", running: "Unbaking…", done: "Ready", error: "Failed", cancelled: "Cancelled" };

async function openHome() {
  showView("home");
  document.title = "Wavy: unbake any song";
  await renderLibrary();
}

async function renderLibrary() {
  let list = [];
  try { list = await api("/api/jobs"); } catch (e) { toast(`Can't reach the server: ${e.message}`); }
  const ul = $("#library");
  ul.innerHTML = "";
  $("#library-empty").hidden = list.length > 0;
  for (const j of list) {
    const li = document.createElement("li");
    const pct = j.status === "running" ? ` ${Math.round(j.progress * 100)}%` : "";
    li.innerHTML = `
      <a href="#/song/${j.id}">
        <span class="lib-icon"><svg viewBox="0 0 24 24"><path d="M3 12h2l2-6 3 12 3-14 3 16 3-10 1 2h2"/></svg></span>
        <span><div class="lib-name"></div><div class="lib-sub">${fmtAgo(j.created)}${j.elapsed ? ` · took ${fmtTime(j.elapsed)}` : ""}</div></span>
        <span class="pill ${j.status}">${STATUS_TEXT[j.status] || j.status}${pct}</span>
      </a>`;
    $(".lib-name", li).textContent = j.name;
    ul.append(li);
  }
  if (list.some((j) => j.status === "running" || j.status === "queued") && !current && !$("#view-home").hidden) {
    clearTimeout(renderLibrary.t);
    renderLibrary.t = setTimeout(() => { if (!$("#view-home").hidden) renderLibrary(); }, 3000);
  }
}

function upload(file) {
  saveOptions();
  const form = new FormData();
  form.append("file", file);
  form.append("singers", $("#opt-singers").checked);
  form.append("drumkit", $("#opt-drumkit").checked);
  form.append("lyrics", $("#opt-lyrics").checked);
  form.append("language", $("#opt-language").value);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs");
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) toast(`Uploading ${file.name}… ${Math.round((e.loaded / e.total) * 100)}%`, 60000);
  };
  xhr.onload = () => {
    $("#toast").hidden = true;
    if (xhr.status >= 200 && xhr.status < 300) {
      location.hash = `#/song/${JSON.parse(xhr.responseText).id}`;
    } else {
      let msg = xhr.statusText;
      try { msg = JSON.parse(xhr.responseText).detail; } catch { /* keep statusText */ }
      toast(`Upload failed: ${msg}`, 6000);
    }
  };
  xhr.onerror = () => toast("Upload failed. Is the server still running?", 6000);
  xhr.send(form);
}

function setupDropzone() {
  const drop = $("#drop"), input = $("#file"), cover = $("#dragcover");
  $("#pick").addEventListener("click", (e) => { e.stopPropagation(); input.click(); });
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => { if (input.files[0]) upload(input.files[0]); input.value = ""; });

  // The whole window is a drop target, from any page.
  let depth = 0;
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  window.addEventListener("dragenter", (e) => { if (!hasFiles(e)) return; depth++; cover.hidden = false; });
  window.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) cover.hidden = true; });
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    cover.hidden = true;
    const f = e.dataTransfer.files[0];
    if (f) upload(f);
  });
  for (const k of ["singers", "drumkit", "lyrics", "language"]) $(`#opt-${k}`).addEventListener("change", saveOptions);
  $("#try-demo").addEventListener("click", async () => {
    try { location.hash = `#/song/${(await api("/api/demo", { method: "POST" })).id}`; }
    catch (e) { toast(e.message); }
  });
}

/* ------------------------------------------------------------------ song (processing / mixer) */

async function openSong(id, startAt = 0) {
  current = { id };
  let job;
  try { job = await api(`/api/jobs/${id}`); } catch (e) {
    toast(e.message);
    location.hash = "#/";
    return;
  }
  if (!current || current.id !== id) return;
  document.title = `${job.name} | Wavy`;
  if (job.status === "done") {
    showView("mixer");
    current.mixer = new Mixer(job);
    if (startAt) current.mixer.seek(startAt);
  } else {
    showView("processing");
    renderProcessing(job);
  }
}

function renderProcessingSoon(job) {
  if (!current || current.id !== job.id) return;
  clearTimeout(current.poll);
  renderProcessing(job);
}

function renderProcessing(job) {
  $("#proc-name").textContent = job.name;
  const pct = Math.round((job.progress || 0) * 100);
  $("#proc-bar").style.width = `${Math.max(pct, job.status === "running" ? 2 : 0)}%`;
  $("#proc-pct").textContent = job.status === "queued" ? "Waiting" : `${pct}%`;
  const elapsed = job.started ? (job.finished || Date.now() / 1000) - job.started : 0;
  $("#proc-elapsed").textContent = fmtTime(elapsed);
  $("#proc-queue").textContent = job.status === "queued" && job.queue_position > 1
    ? `· ${job.queue_position - 1} song(s) ahead of this one` : "";

  const skip = new Set();
  const o = job.options || {};
  if (o.drumkit === false) skip.add("drumkit");
  if (o.singers === false) skip.add("singers");
  if (o.lyrics === false) skip.add("lyrics");
  const order = job.stages.map((s) => s.key);
  const at = job.stage ? order.indexOf(job.stage) : -1;
  const ol = $("#proc-stages");
  ol.innerHTML = "";
  job.stages.forEach((s, i) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="dot"></span><span></span>`;
    li.lastChild.textContent = s.label;
    if (skip.has(s.key)) li.className = "skipped";
    else if (job.status === "error" && i === at) li.className = "failed";
    else if (i < at) li.className = "done";
    else if (i === at) li.className = "active";
    ol.append(li);
  });

  const err = $("#proc-error");
  const ended = job.status === "error" || job.status === "cancelled";
  err.hidden = job.status !== "error";
  $("#proc-again").hidden = !ended;
  $("#proc-remove").hidden = !ended;
  const cancel = $("#proc-cancel");
  cancel.hidden = ended;
  cancel.disabled = !!job.cancelling;
  cancel.textContent = job.cancelling ? "Cancelling…" : "Cancel";
  cancel.onclick = async () => {
    cancel.disabled = true;
    try { renderProcessingSoon(await api(`/api/jobs/${job.id}/cancel`, { method: "POST" })); } catch (e) { toast(e.message); }
  };
  $("#proc-again").onclick = async () => {
    try { renderProcessingSoon(await api(`/api/jobs/${job.id}/retry`, { method: "POST" })); } catch (e) { toast(e.message); }
  };
  $("#proc-remove").onclick = async () => {
    try { await api(`/api/jobs/${job.id}`, { method: "DELETE" }); } catch { /* already gone */ }
    location.hash = "#/";
  };
  if (ended) {
    if (job.status === "error") err.textContent = job.error;
    $("#proc-pct").textContent = job.status === "cancelled" ? "Cancelled" : "Failed";
    return;
  }
  if (job.status === "done") { route(); return; }
  current.poll = setTimeout(async () => {
    if (!current || current.id !== job.id) return;
    try { renderProcessing(await api(`/api/jobs/${job.id}`)); }
    catch { current.poll = setTimeout(() => renderProcessing(job), 2000); }
  }, 1000);
}

/* ------------------------------------------------------------------ mixer */

const MEMORY_BUDGET = 1.2 * 2 ** 30; // bytes of decoded audio we're willing to hold in the tab

const GROUPS = [
  ["voices", "Voices"],
  ["drums", "Drums"],
  ["instruments", "Instruments"],
];

class Mixer {
  constructor(job) {
    this.job = job;
    this.r = job.result;
    this.duration = this.r.duration;
    // Every audible stem is decoded into memory (float32 stereo). Long songs would need gigabytes,
    // so play them back at a lower rate. Downloads are always full quality.
    const streams = job.result.tracks.filter((t) => !t.empty).length + 1;
    const fullBytes = streams * this.duration * 2 * 4 * 44100;
    const rate = Math.max(22050, Math.min(44100, Math.floor(44100 * MEMORY_BUDGET / fullBytes)));
    this.reducedQuality = rate < 44100;
    this.ctx = new AudioContext({ latencyHint: "playback", sampleRate: rate });
    this.master = this.ctx.createGain();
    this.master.connect(this.ctx.destination);
    this.stemBus = this.ctx.createGain();
    this.stemBus.connect(this.master);
    this.origBus = this.ctx.createGain();
    this.origBus.gain.value = 0;
    this.origBus.connect(this.master);
    this.tracks = [];
    this.playing = false;
    this.offset = 0;      // song position when paused / at last start
    this.startedAt = 0;   // ctx time at last start
    this.sources = [];
    this.alive = true;
    this.showEmpty = false;

    this.buildHeader();
    this.buildTracks();
    this.buildLyrics();
    this.bindTransport();
    this.load();
    this.onResize = () => this.drawAll();
    window.addEventListener("resize", this.onResize);
    this.tick = this.tick.bind(this);
    this.raf = requestAnimationFrame(this.tick);
  }

  destroy() {
    this.alive = false;
    this.stop();
    cancelAnimationFrame(this.raf);
    window.removeEventListener("resize", this.onResize);
    window.removeEventListener("keydown", this.onKey);
    this.ctx.close();
  }

  /* ---------- header ---------- */
  buildHeader() {
    const { job, r } = this;
    const name = $("#song-name");
    name.textContent = job.name;
    name.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); name.blur(); } };
    name.onblur = async () => {
      const v = name.textContent.trim();
      if (!v || v === job.name) { name.textContent = job.name; return; }
      try { await api(`/api/jobs/${job.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: v }) }); job.name = v; document.title = `${v} | Wavy`; }
      catch (e) { toast(e.message); name.textContent = job.name; }
    };
    const audible = r.tracks.filter((t) => !t.empty).length;
    const singers = r.singers.length;
    const bits = [fmtTime(r.duration), `${audible} tracks`];
    if (r.instrumental) bits.push("instrumental");
    else if (singers > 1) bits.push(`${singers} singers`);
    if (r.lyrics.lines.length) bits.push(`${r.lyrics.lines.length} lyric lines${r.lyrics.language ? ` (${r.lyrics.language})` : ""}`);
    if (job.elapsed) bits.push(`unbaked in ${fmtTime(job.elapsed)}`);
    $("#song-meta").textContent = bits.join(" · ");
    $("#dl-zip").href = `/api/jobs/${job.id}/download.zip`;
    const hasLyrics = r.lyrics.lines.length > 0;
    $("#dl-lyrics-wrap").hidden = !hasLyrics;
    for (const f of ["lrc", "srt", "txt"]) $(`#dl-${f}`).href = `/api/jobs/${job.id}/lyrics.${f}`;
    $("#dl-lyrics-btn").onclick = (e) => { e.stopPropagation(); $("#dl-lyrics-menu").hidden = !$("#dl-lyrics-menu").hidden; };
    document.addEventListener("click", () => ($("#dl-lyrics-menu").hidden = true));
    $("#delete-song").onclick = async () => {
      if (!confirm(`Delete "${job.name}" and all its tracks?`)) return;
      try { await api(`/api/jobs/${job.id}`, { method: "DELETE" }); location.hash = "#/"; }
      catch (e) { toast(e.message); }
    };
  }

  /* ---------- tracks ---------- */
  buildTracks() {
    const list = $("#track-list");
    list.innerHTML = "";
    const tpl = $("#track-tpl");
    for (const [gkey, gname] of GROUPS) {
      const items = this.r.tracks.filter((t) => t.group === gkey);
      if (!items.length) continue;
      const head = document.createElement("div");
      head.className = "group-head";
      head.dataset.allEmpty = items.every((t) => t.empty) ? "1" : "";
      const n = items.filter((t) => !t.empty).length;
      head.innerHTML = `<span>${gname}</span><span class="count">${n} track${n === 1 ? "" : "s"}</span>`;
      list.append(head);
      for (const t of items) {
        const el = tpl.content.firstElementChild.cloneNode(true);
        $(".swatch", el).style.background = t.color;
        $(".label", el).textContent = t.label;
        $(".note", el).textContent = t.empty ? "silent in this song" : (t.note || "");
        $(".dl", el).href = `/api/jobs/${this.job.id}/stems/${t.file}?download=1`;
        if (t.empty) el.classList.add("empty");
        const track = { ...t, el, wave: $(".wave", el), meter: $(".meter", el), gain: null, analyser: null, buffer: null,
          vol: 1, mute: false, solo: false, level: 0 };
        this.tracks.push(track);
        $(".m", el).onclick = () => { track.mute = !track.mute; this.applyGains(); };
        $(".s", el).onclick = (e) => {
          if (e.ctrlKey || e.metaKey) { this.tracks.forEach((o) => (o.solo = o === track ? !track.solo : false)); }
          else track.solo = !track.solo;
          this.applyGains();
        };
        const vol = $(".vol", el);
        vol.oninput = () => { track.vol = +vol.value; this.applyGains(); };
        vol.ondblclick = () => { vol.value = 1; track.vol = 1; this.applyGains(); };
        track.wave.addEventListener("pointerdown", (e) => this.seekFromEvent(e, track.wave));
        list.append(el);
        if (t.empty) el.dataset.empty = "1";
      }
    }
    const empties = this.tracks.filter((t) => t.empty).length;
    if (empties) {
      const btn = document.createElement("button");
      btn.className = "empties-toggle";
      const sync = () => {
        btn.textContent = this.showEmpty ? `Hide ${empties} silent track${empties > 1 ? "s" : ""}` : `Show ${empties} silent track${empties > 1 ? "s" : ""}`;
        for (const t of this.tracks) if (t.empty) t.el.hidden = !this.showEmpty;
        for (const h of $$(".group-head", list)) if (h.dataset.allEmpty) h.hidden = !this.showEmpty;
        this.drawAll();
      };
      btn.onclick = () => { this.showEmpty = !this.showEmpty; sync(); };
      list.append(btn);
      sync();
    }
    // Hide group headers whose tracks are all hidden
    this.drawAll();
  }

  drawAll() {
    this.drawRuler();
    for (const t of this.tracks) if (!t.el.hidden) this.drawWave(t);
  }

  sizeCanvas(c) {
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(h * dpr);
    }
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    return [g, w, h];
  }

  drawWave(t) {
    const [g, w, h] = this.sizeCanvas(t.wave);
    g.clearRect(0, 0, w, h);
    const peaks = t.peaks;
    const mid = h / 2;
    g.fillStyle = "rgba(255,255,255,.035)";
    g.fillRect(0, mid, w, 1);
    if (t.empty) return;
    const bars = Math.max(1, Math.floor(w / 2));
    g.fillStyle = t.color;
    for (let i = 0; i < bars; i++) {
      const a = Math.floor((i / bars) * peaks.length), b = Math.max(a + 1, Math.floor(((i + 1) / bars) * peaks.length));
      let v = 0;
      for (let k = a; k < b; k++) v = Math.max(v, peaks[k]);
      const amp = Math.min(1, Math.sqrt(v) * 1.05) * (mid - 3);
      g.fillRect(i * 2, mid - amp, 1.4, Math.max(1, amp * 2));
    }
  }

  drawRuler() {
    const c = $("#ruler");
    const [g, w, h] = this.sizeCanvas(c);
    g.clearRect(0, 0, w, h);
    g.fillStyle = "#5b6476";
    g.font = "10.5px Segoe UI, system-ui, sans-serif";
    const d = this.duration;
    const step = [5, 10, 15, 30, 60, 120].find((s) => (w / d) * s > 70) || 300;
    for (let s = 0; s <= d; s += step) {
      const x = (s / d) * w;
      g.fillRect(x, h - 6, 1, 6);
      if (x < w - 24) g.fillText(fmtTime(s), x + 4, h - 8);
    }
  }

  /* ---------- lyrics ---------- */
  buildLyrics() {
    const { lines, language } = this.r.lyrics;
    const body = $("#lyrics");
    const legend = $("#legend");
    body.innerHTML = "";
    legend.innerHTML = "";
    const singers = this.r.singers;
    if (singers.length > 1 && !this.r.instrumental) {
      for (const s of singers) {
        const span = document.createElement("span");
        span.style.setProperty("--c", s.color);
        span.textContent = `${s.name} · ${s.register}`;
        legend.append(span);
      }
    }
    if (!lines.length) {
      const why = this.r.instrumental ? "No vocals here. This one's an instrumental."
        : this.job.options?.lyrics === false ? "Lyrics transcription was turned off for this song."
        : "No sung words were found.";
      body.innerHTML = `<p class="lyrics-empty">${why}</p>`;
    }
    let lastSinger = null;
    this.lyricEls = lines.map((ln) => {
      const div = document.createElement("div");
      div.className = "lyric-line";
      const s = singers[ln.singer ?? 0] || singers[0];
      if (s) div.style.setProperty("--c", s.color);
      if (singers.length > 1 && ln.singer !== lastSinger && s) {
        const who = document.createElement("span");
        who.className = "who";
        who.textContent = s.name;
        div.append(who);
      }
      lastSinger = ln.singer;
      ln.words.forEach((w, i) => {
        const span = document.createElement("span");
        span.className = "w";
        span.textContent = w.word + (i < ln.words.length - 1 ? " " : "");
        div.append(span);
      });
      div.onclick = () => this.seek(Math.max(0, ln.start - 0.25));
      body.append(div);
      return div;
    });
    this.lyricLines = lines;
    this.curLine = -2;
    if (language) $("#lyrics-panel h2").textContent = `Lyrics`;
  }

  updateLyrics(t) {
    const lines = this.lyricLines;
    if (!lines.length) return;
    let idx = -1;
    for (let i = 0; i < lines.length; i++) { if (lines[i].start - 0.15 <= t) idx = i; else break; }
    if (idx !== this.curLine) {
      this.lyricEls.forEach((el, i) => {
        el.classList.toggle("current", i === idx);
        el.classList.toggle("past", i < idx);
      });
      if (idx >= 0) {
        const body = $("#lyrics"), el = this.lyricEls[idx];
        body.scrollTop = el.offsetTop - body.clientHeight * 0.3;
      }
      this.curLine = idx;
    }
    if (idx >= 0) {
      const words = lines[idx].words, spans = $$(".w", this.lyricEls[idx]);
      words.forEach((w, i) => {
        spans[i].classList.toggle("sung", w.end <= t);
        spans[i].classList.toggle("now", w.start <= t && t < w.end);
      });
    }
  }

  /* ---------- audio ---------- */
  async load() {
    const todo = this.tracks.filter((t) => !t.empty);
    const label = $("#loading");
    let done = 0;
    label.textContent = `Loading tracks 0/${todo.length + 1}…`;
    const fetchBuf = async (file) => {
      const res = await fetch(`/api/jobs/${this.job.id}/stems/${file}`);
      if (!res.ok) throw new Error(`${file}: ${res.status}`);
      const buf = await this.ctx.decodeAudioData(await res.arrayBuffer());
      done++;
      if (this.alive) label.textContent = `Loading tracks ${done}/${todo.length + 1}…`;
      return buf;
    };
    // One bad stem shouldn't take the whole mixer down: load what we can, flag the rest.
    const failed = [];
    const queue = [...todo.map((t) => [t, t.file]), [null, this.r.original.file]];
    const workers = Array.from({ length: 4 }, async () => {
      while (queue.length && this.alive) {
        const [t, file] = queue.shift();
        try {
          const buf = await fetchBuf(file);
          if (t) t.buffer = buf; else this.origBuffer = buf;
        } catch (e) {
          failed.push(t ? t.label : "Original mix");
          if (t) { $(".note", t.el).textContent = "couldn't load this track"; t.el.classList.add("muted"); }
        }
      }
    });
    await Promise.all(workers);
    if (!this.alive) return;
    if (!todo.some((t) => t.buffer)) {
      label.textContent = "Couldn't load the audio. Is the server still running?";
      return;
    }
    if (!this.origBuffer) $("#ab-orig").disabled = true;
    for (const t of this.tracks) {
      if (!t.buffer) continue;
      t.gain = this.ctx.createGain();
      t.analyser = this.ctx.createAnalyser();
      t.analyser.fftSize = 512;
      t.gain.connect(t.analyser);
      t.gain.connect(this.stemBus);
    }
    this.meterBuf = new Float32Array(512);
    label.textContent = failed.length ? `Couldn't load: ${failed.join(", ")}`
      : this.reducedQuality ? "Long song: previewing at reduced quality to save memory (downloads are full quality)" : "";
    $("#play").disabled = false;
    this.applyGains();
  }

  applyGains() {
    const anySolo = this.tracks.some((t) => t.solo);
    for (const t of this.tracks) {
      const audible = !t.mute && (!anySolo || t.solo);
      if (t.gain) t.gain.gain.setTargetAtTime(audible ? t.vol : 0, this.ctx.currentTime, 0.012);
      $(".m", t.el).classList.toggle("on", t.mute);
      $(".s", t.el).classList.toggle("on", t.solo);
      t.el.classList.toggle("muted", t.mute);
      t.el.classList.toggle("dimmed", anySolo && !t.solo && !t.mute);
    }
  }

  position() {
    if (!this.playing) return this.offset;
    return Math.min(this.duration, this.offset + (this.ctx.currentTime - this.startedAt));
  }

  start(at) {
    this.stopSources();
    const when = this.ctx.currentTime + 0.05;
    for (const t of this.tracks) {
      if (!t.buffer) continue;
      const src = this.ctx.createBufferSource();
      src.buffer = t.buffer;
      src.connect(t.gain);
      src.start(when, at);
      this.sources.push(src);
    }
    if (this.origBuffer) {
      const src = this.ctx.createBufferSource();
      src.buffer = this.origBuffer;
      src.connect(this.origBus);
      src.start(when, at);
      this.sources.push(src);
    }
    this.offset = at;
    this.startedAt = when;
    this.playing = true;
    $("#play").classList.add("playing");
  }

  stopSources() {
    for (const s of this.sources) { try { s.stop(); } catch { /* already stopped */ } s.disconnect(); }
    this.sources = [];
  }

  stop() {
    if (this.playing) this.offset = this.position();
    this.stopSources();
    this.playing = false;
    $("#play").classList.remove("playing");
  }

  async toggle() {
    if ($("#play").disabled) return;
    await this.ctx.resume();
    if (this.playing) this.stop();
    else this.start(this.offset >= this.duration - 0.05 ? 0 : this.offset);
  }

  seek(t) {
    t = Math.max(0, Math.min(this.duration, t));
    if (this.playing) this.start(t);
    else this.offset = t;
  }

  seekFromEvent(e, canvas) {
    const rect = canvas.getBoundingClientRect();
    const frac = (e.clientX - rect.left) / rect.width;
    this.seek(frac * this.duration);
    const move = (ev) => {
      const f = (ev.clientX - rect.left) / rect.width;
      this.offset = Math.max(0, Math.min(1, f)) * this.duration;
      if (!this.playing) return;
      clearTimeout(this.scrubT);
      this.scrubT = setTimeout(() => this.seek(this.offset), 40);
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  bindTransport() {
    $("#play").onclick = () => this.toggle();
    $("#t-dur").textContent = fmtTime(this.duration);
    const setAB = (orig) => {
      $("#ab-orig").classList.toggle("on", orig);
      $("#ab-stems").classList.toggle("on", !orig);
      const now = this.ctx.currentTime;
      this.origBus.gain.setTargetAtTime(orig ? 1 : 0, now, 0.015);
      this.stemBus.gain.setTargetAtTime(orig ? 0 : 1, now, 0.015);
    };
    $("#ab-orig").onclick = () => setAB(true);
    $("#ab-stems").onclick = () => setAB(false);
    const master = $("#master");
    master.oninput = () => this.master.gain.setTargetAtTime(+master.value, this.ctx.currentTime, 0.01);
    master.ondblclick = () => { master.value = 1; master.oninput(); };
    $("#reset-mix").onclick = () => {
      for (const t of this.tracks) { t.mute = false; t.solo = false; t.vol = 1; $(".vol", t.el).value = 1; }
      this.applyGains();
    };
    this.onKey = (e) => {
      if (e.target.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
      if (e.code === "Space") { e.preventDefault(); this.toggle(); }
      else if (e.key === "ArrowRight") this.seek(this.position() + 5);
      else if (e.key === "ArrowLeft") this.seek(this.position() - 5);
      else if (e.key === "Home") this.seek(0);
    };
    window.addEventListener("keydown", this.onKey);
  }

  tick() {
    if (!this.alive) return;
    const t = this.position();
    if (this.playing && t >= this.duration - 0.01) { this.stop(); this.offset = this.duration; }
    $("#t-cur").textContent = fmtTime(t);
    const ruler = $("#ruler");
    const x = ruler.offsetLeft + (t / this.duration) * ruler.clientWidth;
    $("#playhead").style.transform = `translateX(${x - ruler.offsetLeft}px)`;
    this.updateLyrics(t);
    if (this.meterBuf) {
      for (const tr of this.tracks) {
        if (!tr.analyser || tr.el.hidden) continue;
        tr.analyser.getFloatTimeDomainData(this.meterBuf);
        let peak = 0;
        for (let i = 0; i < this.meterBuf.length; i++) peak = Math.max(peak, Math.abs(this.meterBuf[i]));
        tr.level = Math.max(peak, tr.level * 0.9);
        const c = tr.meter, g = c.getContext("2d");
        g.clearRect(0, 0, c.width, c.height);
        const db = 20 * Math.log10(tr.level + 1e-6);
        const hgt = Math.max(0, Math.min(1, (db + 54) / 54)) * c.height;
        g.fillStyle = tr.level > 0.98 ? "#ff6b6b" : tr.color;
        g.fillRect(0, c.height - hgt, c.width, hgt);
      }
    }
    this.raf = requestAnimationFrame(this.tick);
  }
}

/* ------------------------------------------------------------------ boot */

async function boot() {
  loadOptions();
  setupDropzone();
  route();
  try {
    const h = await api("/api/health");
    const el = $("#gpu");
    el.textContent = h.gpu ? h.gpu.replace("NVIDIA GeForce ", "") : "CPU mode (slow)";
    el.classList.toggle("on", !!h.gpu);
    if (!h.demo) $("#demo-offer").hidden = true;
    if (!h.ffmpeg) {
      $("#banner").textContent = "ffmpeg isn't installed, so songs can't be read. Install it (Windows: winget install Gyan.FFmpeg, macOS: brew install ffmpeg), then restart Wavy.";
      $("#banner").hidden = false;
    }
  } catch { /* offline */ }
}
boot();
