(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
  const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  const fmt = (n) => (n == null ? "—" : usd.format(n));
  const fmtBig = (n) => (n == null ? "—" : Math.abs(n) >= 10000 ? usd0.format(n) : usd.format(n));
  const pct = (n) => (n == null ? "—" : `${n > 0 ? "+" : ""}${n.toFixed(1)}%`);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  const SOURCE_LABELS = { tcgplayer: "TCGplayer", pricecharting: "PriceCharting", ebay: "eBay" };
  const FLAG_LABELS = {
    check_match: ["check match", "warn"],
    no_tcgplayer_match: ["not on TCGplayer", "warn"],
    sources_disagree: ["sources disagree", "warn"],
    stale: ["last known price", "warn"],
    unpriced: ["no price yet", "warn"],
    listing_price: ["listing", ""],
  };

  let portfolio = null;
  let history = [];
  let charts = [];
  const state = { sort: "value", dir: -1, q: "", set: "" };

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function delta(n, text) {
    if (n == null) return `<span class="na">—</span>`;
    const cls = n > 0 ? "up" : n < 0 ? "down" : "";
    const arrow = n > 0 ? "▲ " : n < 0 ? "▼ " : "";
    return `<span class="delta ${cls}">${arrow}${text}</span>`;
  }

  // ------------------------------------------------------------------ header + KPIs

  function renderHeader() {
    const d = new Date(portfolio.generated_at);
    $("#updated").textContent = `Prices updated ${d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`;
    const st = portfolio.status || {};
    $("#sources").innerHTML = Object.keys(SOURCE_LABELS).map((k) => {
      const s = st[k] || "";
      const cls = s === "ok" ? "ok" : s.startsWith("off") || s.startsWith("manual") ? "" : "bad";
      const label = s === "ok" ? "live" : s.startsWith("off") ? "off" : s.startsWith("manual") ? "manual" : "issue";
      return `<span class="chip" title="${esc(s)}"><span class="dot ${cls}"></span>${SOURCE_LABELS[k]} · ${label}</span>`;
    }).join("");
  }

  function renderKpis() {
    const t = portfolio.totals;
    const items = portfolio.items.filter((i) => i.gain_pct != null);
    const best = items.slice().sort((a, b) => b.gain_pct - a.gain_pct)[0];
    const partial = t.priced_products < t.products;
    $("#kpis").innerHTML = `
      <div class="kpi hero">
        <div class="label">Market value</div>
        <div class="value">${fmtBig(t.value)}</div>
        <div class="meta">${t.products} product${t.products === 1 ? "" : "s"} · ${t.units} unit${t.units === 1 ? "" : "s"}${partial ? ` · ${t.priced_products} priced` : ""}</div>
      </div>
      <div class="kpi">
        <div class="label">Invested</div>
        <div class="value">${fmtBig(t.cost_priced)}</div>
        <div class="meta">${partial ? `${fmt(t.cost)} incl. unpriced` : "what you paid"}</div>
      </div>
      <div class="kpi">
        <div class="label">Total gain</div>
        <div class="value">${delta(t.gain, fmtBig(Math.abs(t.gain ?? 0)))}</div>
        <div class="meta">${delta(t.gain_pct, pct(t.gain_pct))} on cost</div>
      </div>
      <div class="kpi">
        <div class="label">Best performer</div>
        <div class="value" style="font-size:18px;line-height:1.3;white-space:normal" title="${esc(best?.name)}">${best ? esc(best.name) : "—"}</div>
        <div class="meta">${best ? delta(best.gain_pct, pct(best.gain_pct)) : ""}</div>
      </div>`;
  }

  // ------------------------------------------------------------------ charts

  function baseOptions() {
    const ink2 = css("--ink-2"), muted = css("--muted"), grid = css("--grid"), surface = css("--surface"), ink = css("--ink"), ring = css("--ring");
    Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
    Chart.defaults.color = muted;
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 300 },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: surface, titleColor: ink, bodyColor: ink2, borderColor: ring, borderWidth: 1,
          padding: 10, cornerRadius: 8, boxPadding: 4, usePointStyle: true,
        },
      },
      _grid: grid,
    };
  }

  function renderTrend() {
    const o = baseOptions();
    const s1 = css("--series-1"), cost = css("--series-cost");
    const pts = (history.length ? history : [{ date: portfolio.generated_at.slice(0, 10), value: portfolio.totals.value, cost: portfolio.totals.cost_priced }])
      .map((h) => ({ ...h, t: new Date(h.date + "T12:00:00").getTime() }));
    const day = (t, opts) => new Date(t).toLocaleDateString(undefined, opts);
    $("#trend-legend").innerHTML = `
      <span><i class="swatch" style="background:${s1}"></i>Market value</span>
      <span><i class="swatch dashed"></i>Invested</span>`;
    const note = $("#trend-note");
    const firstValue = pts.find((p) => p.value != null);
    if (pts.length < 2) {
      note.hidden = false;
      note.textContent = `Tracking started ${day(pts[0].t, { dateStyle: "medium" })}. The line grows with each daily price refresh.`;
    } else if (firstValue && firstValue.t > pts[0].t) {
      note.hidden = false;
      note.textContent = `Starts at your first purchase (${day(pts[0].t, { dateStyle: "medium" })}). TCGplayer price history is only available from ${day(firstValue.t, { dateStyle: "medium" })}, so market value begins there.`;
    } else note.hidden = true;
    const span = pts[pts.length - 1].t - pts[0].t;
    const longRange = span > 150 * 864e5;
    const few = pts.filter((p) => p.value != null).length <= 40;
    charts.push(new Chart($("#trend"), {
      type: "line",
      data: {
        datasets: [
          { label: "Market value", data: pts.map((h) => ({ x: h.t, y: h.value })), borderColor: s1, backgroundColor: s1, borderWidth: 2, pointRadius: few ? 3 : 0, pointHoverRadius: 6, tension: 0.2, spanGaps: false },
          { label: "Invested", data: pts.map((h) => ({ x: h.t, y: h.cost })), borderColor: cost, backgroundColor: cost, borderWidth: 2, borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 5, tension: 0, stepped: "before" },
        ],
      },
      options: {
        ...o,
        interaction: { mode: "index", intersect: false },
        plugins: {
          ...o.plugins,
          tooltip: {
            ...o.plugins.tooltip,
            filter: (c) => c.parsed.y != null,
            callbacks: {
              title: (c) => day(c[0].parsed.x, { dateStyle: "medium" }),
              label: (c) => ` ${c.dataset.label}: ${fmt(c.parsed.y)}`,
              afterBody: (c) => {
                const i = c[0]?.dataIndex;
                const v = pts[i]?.value, k = pts[i]?.cost;
                return v != null && k ? `Gain: ${fmt(v - k)} (${pct(((v - k) / k) * 100)})` : "";
              },
            },
          },
        },
        scales: {
          x: {
            type: "linear", min: pts[0].t, max: pts[pts.length - 1].t,
            grid: { display: false }, border: { color: css("--axis") },
            ticks: { maxRotation: 0, autoSkipPadding: 24, maxTicksLimit: 8,
              callback: (v) => day(v, longRange ? { month: "short", year: "numeric" } : { month: "short", day: "numeric" }) },
          },
          y: { beginAtZero: true, grid: { color: o._grid }, border: { display: false }, ticks: { callback: (v) => usd0.format(v), maxTicksLimit: 6 } },
        },
      },
    }));
  }

  function renderBars(canvasId, boxId, keyFn) {
    const o = baseOptions();
    const groups = new Map();
    for (const i of portfolio.items) {
      const k = keyFn(i) || "Other";
      const g = groups.get(k) || { value: 0, cost: 0 };
      g.value += i.value ?? 0;
      g.cost += i.cost ?? 0;
      groups.set(k, g);
    }
    const rows = [...groups.entries()].sort((a, b) => b[1].value - a[1].value);
    const total = rows.reduce((s, r) => s + r[1].value, 0) || 1;
    $(boxId).style.height = `${Math.max(120, rows.length * 36 + 40)}px`;
    const s1 = css("--series-1");
    charts.push(new Chart($(canvasId), {
      type: "bar",
      data: {
        labels: rows.map((r) => r[0]),
        datasets: [{ data: rows.map((r) => r[1].value), backgroundColor: s1, borderRadius: 4, borderSkipped: "start", maxBarThickness: 22, categoryPercentage: 0.8 }],
      },
      options: {
        ...o,
        indexAxis: "y",
        plugins: {
          ...o.plugins,
          tooltip: {
            ...o.plugins.tooltip,
            callbacks: {
              label: (c) => ` ${fmt(c.parsed.x)} · ${((c.parsed.x / total) * 100).toFixed(1)}% of value`,
              afterLabel: (c) => {
                const g = rows[c.dataIndex][1];
                return g.cost ? ` Paid ${fmt(g.cost)} (${pct(((g.value - g.cost) / g.cost) * 100)})` : "";
              },
            },
          },
        },
        scales: {
          x: { beginAtZero: true, grid: { color: o._grid }, border: { display: false }, ticks: { callback: (v) => usd0.format(v), maxTicksLimit: 5 } },
          y: { grid: { display: false }, border: { color: css("--axis") }, ticks: { color: css("--ink-2") } },
        },
      },
    }));
  }

  function renderCharts() {
    charts.forEach((c) => c.destroy());
    charts = [];
    renderTrend();
    renderBars("#by-set", "#by-set-box", (i) => i.set);
    renderBars("#by-type", "#by-type-box", (i) => i.type);
  }

  // ------------------------------------------------------------------ table

  function sourceCell(item, src) {
    const q = item.prices?.[src];
    if (!q || q.price == null) return `<td class="src na">—</td>`;
    const notes = [];
    if (q.basis === "listing") notes.push("listing");
    if (q.uncertain) notes.push("unconfirmed");
    if (q.stale) notes.push("last known");
    const title = q.matched_name ? `Matched: ${q.matched_name}` : "";
    const val = q.url ? `<a href="${esc(q.url)}" target="_blank" rel="noopener" title="${esc(title)}">${fmt(q.price)}</a>` : fmt(q.price);
    return `<td class="src">${val}${notes.length ? `<small>${notes.join(", ")}</small>` : ""}</td>`;
  }

  function sortVal(i, key) {
    if (key.startsWith("p_")) return i.prices?.[key.slice(2)]?.price ?? null;
    return i[key];
  }

  function filtered() {
    const q = state.q.toLowerCase();
    return portfolio.items.filter((i) =>
      (!state.set || i.set === state.set) &&
      (!q || `${i.name} ${i.set} ${i.type} ${i.notes}`.toLowerCase().includes(q)));
  }

  function renderTable() {
    const rows = filtered().sort((a, b) => {
      const x = sortVal(a, state.sort), y = sortVal(b, state.sort);
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return (typeof x === "string" ? x.localeCompare(y) : x - y) * state.dir;
    });
    document.querySelectorAll("#holdings th").forEach((th) => {
      th.setAttribute("aria-sort", th.dataset.sort === state.sort ? (state.dir > 0 ? "ascending" : "descending") : "none");
    });
    $("#count").textContent = `${rows.length}${rows.length !== portfolio.items.length ? ` of ${portfolio.items.length}` : ""}`;

    const body = rows.map((i) => {
      const img = i.image
        ? `<img class="thumb" src="${esc(i.image)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'thumb blank',textContent:'◆'}))">`
        : `<div class="thumb blank">◆</div>`;
      const tags = (i.flags || []).filter((f) => FLAG_LABELS[f] && f !== "listing_price")
        .map((f) => `<span class="tag ${FLAG_LABELS[f][1]}">${FLAG_LABELS[f][0]}</span>`).join("");
      const bought = i.purchase_date && !isNaN(new Date(i.purchase_date + "T12:00:00"))
        ? new Date(i.purchase_date + "T12:00:00").toLocaleDateString(undefined, { dateStyle: "medium" })
        : i.purchase_date;
      const meta = [i.set, i.type, bought && `bought ${bought}`, i.bought_from].filter(Boolean).map(esc).join(" · ");
      return `<tr>
        <td class="left"><div class="prod">${img}<div>
          <div class="pname">${esc(i.name)}</div>
          <div class="pmeta">${meta}</div>
          ${i.notes ? `<div class="pmeta" title="${esc(i.notes)}">${esc(i.notes)}</div>` : ""}
          ${tags ? `<div class="tags">${tags}</div>` : ""}
        </div></div></td>
        <td>${i.qty}</td>
        <td>${fmt(i.unit_cost)}</td>
        ${sourceCell(i, "tcgplayer")}
        ${sourceCell(i, "pricecharting")}
        ${sourceCell(i, "ebay")}
        <td class="strong">${fmt(i.market)}</td>
        <td class="strong">${fmt(i.value)}</td>
        <td>${delta(i.gain, i.gain == null ? "" : fmt(Math.abs(i.gain)))}</td>
        <td>${delta(i.gain_pct, pct(i.gain_pct))}</td>
      </tr>`;
    }).join("");
    $("#holdings tbody").innerHTML = body || `<tr class="empty-row"><td colspan="10">No products match.</td></tr>`;

    const priced = rows.filter((i) => i.value != null);
    const cost = priced.reduce((s, i) => s + i.cost, 0);
    const value = priced.reduce((s, i) => s + i.value, 0);
    const units = rows.reduce((s, i) => s + i.qty, 0);
    $("#holdings tfoot").innerHTML = rows.length > 1 ? `<tr>
      <td class="left">Total${rows.length !== portfolio.items.length ? " (filtered)" : ""}</td>
      <td>${units}</td><td>${fmt(cost)}</td><td></td><td></td><td></td><td></td>
      <td>${fmt(value)}</td>
      <td>${delta(value - cost, fmt(Math.abs(value - cost)))}</td>
      <td>${cost ? delta(value - cost, pct(((value - cost) / cost) * 100)) : "—"}</td>
    </tr>` : "";
  }

  function wireTable() {
    const sets = [...new Set(portfolio.items.map((i) => i.set).filter(Boolean))].sort();
    $("#set-filter").insertAdjacentHTML("beforeend", sets.map((s) => `<option>${esc(s)}</option>`).join(""));
    $("#set-filter").addEventListener("change", (e) => { state.set = e.target.value; renderTable(); });
    $("#search").addEventListener("input", (e) => { state.q = e.target.value.trim(); renderTable(); });
    document.querySelectorAll("#holdings th").forEach((th) => th.addEventListener("click", () => {
      const k = th.dataset.sort;
      if (state.sort === k) state.dir *= -1;
      else { state.sort = k; state.dir = k === "name" ? 1 : -1; }
      renderTable();
    }));
  }

  // ------------------------------------------------------------------ theme

  function wireTheme() {
    $("#theme-btn").addEventListener("click", () => {
      const root = document.documentElement;
      const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("theme", root.dataset.theme); } catch (e) {}
      if (portfolio) renderCharts();
    });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (portfolio && !document.documentElement.dataset.theme) renderCharts(); });
  }

  // ------------------------------------------------------------------ boot

  async function getJSON(path) {
    const r = await fetch(`${path}?v=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }

  async function boot() {
    wireTheme();
    try {
      portfolio = await getJSON("data/portfolio.json");
    } catch (e) {
      $("#updated").textContent = "No price data yet. The first refresh runs automatically after each update to the collection.";
      return;
    }
    try { history = await getJSON("data/history.json"); } catch (e) { history = []; }
    renderHeader();
    renderKpis();
    renderCharts();
    wireTable();
    renderTable();
  }

  boot();
})();
