const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const number = (value) => value.toLocaleString("en-CA");
const fixed = (value, places = 3) => value.toFixed(places);
const percent = (value) => fixed(100 * value, 1) + "%";
const bandName = (band) => band.replace("-", "–");
const signed = (value, places = 3) => (value < 0 ? "−" : "") + Math.abs(value).toFixed(places);
const methods = { raw: "Raw average", adjusted: "Adjusted estimate" };
const palette = { raw: "#4472a0", adjusted: "#10867f", grid: "#dae5ea", line: "#9db2bd", target: "#835514" };

// Authored fictional examples. Each row contains a name, review count and raw star average.
const examples = [
  ["Aster", 4, 2.90], ["Brook", 2, 5.00], ["Cedar", 65, 4.80], ["Harbor", 11, 5.00],
  ["Juniper", 220, 4.70], ["Linden", 8, 3.90], ["Maple", 50, 4.60], ["Orchard", 26, 3.20],
  ["Pine", 16, 4.72], ["River", 6, 4.88], ["Spruce", 39, 4.64], ["Willow", 500, 4.35],
];

function metrics(selector, entries) {
  $(selector).innerHTML = entries.map(([value, label]) =>
    `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`
  ).join("");
}

function detailList(entries) {
  return `<dl class="detail-list">${entries.map(([label, value]) =>
    `<div><dt>${label}</dt><dd>${value}</dd></div>`).join("")}</dl>`;
}

function options(select, entries) {
  select.replaceChildren(...entries.map(([value, label]) => new Option(label, value)));
}

function chart(selector, dimensions, label, draw) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${dimensions.join(" ")}`);
  svg.setAttribute("class", "chart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", label);
  $(selector).replaceChildren(svg);
  draw((tag, attrs, content) => {
    const node = document.createElementNS(svg.namespaceURI, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (content !== undefined) node.textContent = content;
    svg.append(node);
  });
}

function setUpTabs() {
  const tabs = $$('[role="tab"]');
  function activate(tab, setHash) {
    for (const item of tabs) {
      const selected = item === tab;
      item.setAttribute("aria-selected", selected);
      item.tabIndex = selected ? 0 : -1;
      document.getElementById(item.getAttribute("aria-controls")).hidden = !selected;
    }
    if (setHash && location.hash !== "#" + tab.getAttribute("aria-controls")) {
      history.pushState(null, "", "#" + tab.getAttribute("aria-controls"));
    }
  }
  function fromHash() {
    const match = tabs.find((tab) => "#" + tab.getAttribute("aria-controls") === location.hash);
    activate(match ?? tabs[0], false);
    return match;
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(tab, true));
    tab.addEventListener("keydown", (event) => {
      const next = {
        ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length,
        Home: 0, End: tabs.length - 1,
      }[event.key];
      if (next === undefined) return;
      event.preventDefault();
      tabs[next].focus();
      activate(tabs[next], true);
    });
  });
  $(".page-nav").hidden = false;
  const initial = fromHash();
  if (initial) document.getElementById(initial.getAttribute("aria-controls")).scrollIntoView();
  window.addEventListener("hashchange", () => {
    if (tabs.some((tab) => "#" + tab.getAttribute("aria-controls") === location.hash) ||
        location.hash === "#report" || !location.hash) fromHash();
  });
}

function overview({ study, volume }) {
  metrics("#overview-kpis", [
    [percent(study.relative), "Lower prediction error"], [fixed(study.raw), "Raw MAE · stars"],
    [fixed(study.adjusted), "Adjusted MAE · stars"], [number(study.paired), "Paired evaluation sellers"],
  ]);
  $("#volume-chart").innerHTML = volume.map((row, i) => `
    <button class="volume-row" type="button" data-band-index="${i}" aria-pressed="false"
      aria-label="${bandName(row.band)} reviews. Raw MAE ${fixed(row.raw)}, adjusted MAE ${fixed(row.adjusted)} stars">
      <strong>${bandName(row.band)}</strong>
      <span class="bars" aria-hidden="true"><i class="raw" style="width:${row.raw / 1.2 * 100}%"></i><i class="adjusted" style="width:${row.adjusted / 1.2 * 100}%"></i></span>
      <span class="bar-stats" aria-hidden="true"><span>${fixed(row.raw)}</span><span>${fixed(row.adjusted)}</span></span>
    </button>`).join("");
  const buttons = $$(".volume-row");
  function inspect(index) {
    const row = volume[index];
    buttons.forEach((button, i) => button.setAttribute("aria-pressed", i === index));
    $("#volume-detail").innerHTML = `<strong class="detail-number">${percent(row.gain / row.raw)}</strong>
      <p class="detail-label">Lower MAE for sellers with ${bandName(row.band)} historical reviews</p>` + detailList([
      ["MAE reduction", fixed(row.gain) + " stars"], ["Paired sellers", number(row.sellers)],
      ["Historical ratings", number(row.historical)], ["Subsequent ratings", number(row.later)],
    ]);
  }
  buttons.forEach((button, index) => button.addEventListener("click", () => inspect(index)));
  inspect(0);
  $("#study-scope").innerHTML = [
    [study.eligible, "eligible ratings"], [study.ratedSellers, "rated sellers"], [study.historicalSellers, "historical sellers"],
    [study.historical, "historical ratings"], [study.later, "subsequent ratings"],
  ].map(([value, label]) => `<span><strong>${number(value)}</strong> ${label}</span>`).join("");
}

function blend(raw, count, study) {
  const weight = count / (count + study.strength);
  return { weight, adjusted: weight * raw + (1 - weight) * study.reference };
}

function reviewBand(count) {
  if (count <= 4) return "1-4";
  if (count <= 9) return "5-9";
  if (count <= 19) return "10-19";
  if (count <= 49) return "20-49";
  return "50+";
}

function ratings(study, sellers, volume) {
  const bandSelect = $("#rating-band");
  const sellerSelect = $("#rating-seller");
  $("#rating-settings").textContent = `Study reference ${fixed(study.reference)} stars · shrinkage strength ${study.strength}`;
  options(bandSelect, [["all", "All bands"], ...volume.map((row) => [row.band, bandName(row.band) + " reviews"])]);
  let visible;
  function selectSeller(name) {
    const seller = visible.find((item) => item.name === name);
    sellerSelect.value = name;
    $$(".dot-button").forEach((button) => button.setAttribute("aria-pressed", button.dataset.seller === name));
    $("#seller-detail").innerHTML = `<strong class="detail-number">${fixed(seller.adjusted, 2)} stars</strong>
      <p class="detail-label">${seller.name} · adjusted rating</p>` + detailList([
        ["Historical reviews", number(seller.count)], ["Raw rating", fixed(seller.raw, 2) + " stars"],
        ["Seller evidence", percent(seller.weight)], ["Pooled reference", percent(1 - seller.weight)],
      ]) + `<div class="weights" aria-hidden="true" style="--share:${seller.weight * 100}%"></div>`;
  }

  function filter() {
    const previous = sellerSelect.value;
    visible = sellers.filter((seller) => bandSelect.value === "all" || seller.band === bandSelect.value);
    options(sellerSelect, visible.map(({ name }) => [name, name]));
    const x = (count) => 4 + 91 * Math.log10(count) / Math.log10(500);
    $("#seller-scatter").innerHTML = `<p class="axis-title">Absolute rating adjustment (stars)</p>
      <div class="scatter-frame"><div class="scatter-area">
      ${[0, .3, .6, .9, 1.2].map((tick) =>
        `<span class="scatter-y" aria-hidden="true" style="bottom:${tick / 1.2 * 100}%">${fixed(tick, 1)}</span>`).join("")}
      ${[1, 5, 20, 100, 500].map((tick) =>
        `<span class="scatter-x" aria-hidden="true" style="left:${x(tick)}%">${tick}</span>`).join("")}
      ${visible.map((seller) => `<button class="dot-button" type="button" data-seller="${seller.name}" aria-pressed="false"
        aria-label="${seller.name}, ${seller.count} reviews, ${fixed(seller.change, 2)} star absolute adjustment"
        title="${seller.name} · ${seller.count} reviews · ${fixed(seller.change, 2)} star adjustment"
        style="left:${x(seller.count)}%;bottom:${seller.change / 1.2 * 100}%"></button>`).join("")}
      </div></div><p class="axis-title">Historical reviews · log scale</p>`;
    $$(".dot-button").forEach((button) => button.addEventListener("click", () => selectSeller(button.dataset.seller)));
    $("#rating-count").textContent = `${visible.length} of ${sellers.length} fictional sellers shown`;
    $("#rating-bands").innerHTML = "<span>Mean adjustment in each visible band</span>" + volume.map(({ band }) => {
      const group = visible.filter((seller) => seller.band === band);
      return group.length ? `<span>${bandName(band)}: <b>${fixed(group.reduce((total, seller) => total + seller.change, 0) / group.length, 2)}★</b></span>` : "";
    }).join("");
    selectSeller(visible.some((seller) => seller.name === previous) ? previous : visible[0].name);
  }

  bandSelect.addEventListener("change", filter);
  sellerSelect.addEventListener("change", () => selectSeller(sellerSelect.value));
  $("#rating-reset").addEventListener("click", () => {
    bandSelect.value = "all";
    filter();
    selectSeller("Brook");
  });
  const custom = $("#custom-rating");
  function calculate() {
    const raw = custom.elements.raw.valueAsNumber;
    const count = custom.elements.count.valueAsNumber;
    const { adjusted, weight } = blend(raw, count, study);
    $("#custom-result").textContent = `${fixed(adjusted, 2)} stars · ${percent(weight)} seller weight`;
  }
  custom.addEventListener("submit", (event) => {
    event.preventDefault();
    calculate();
  });
  filter();
  selectSeller("Brook");
  calculate();
}

function averageRanks(rows, method) {
  const sorted = [...rows].sort((a, b) => b[method] - a[method] || a.name.localeCompare(b.name));
  const rank = new Map();
  let start = 0;
  while (start < sorted.length) {
    let end = start + 1;
    while (end < sorted.length && sorted[end][method] === sorted[start][method]) end += 1;
    const average = (start + 1 + end) / 2;
    for (const seller of sorted.slice(start, end)) rank.set(seller.name, average);
    start = end;
  }
  return rank;
}

function rankings(ranks, sellers) {
  metrics("#ranking-kpis", [
    [number(ranks.tied), "Sellers in raw-rating ties"], [number(ranks.largestTie), "Largest raw-rating tie"],
    [fixed(ranks.movement, 0), "Mean absolute rank movement"], [fixed(ranks.spearman), "Spearman agreement"],
  ]);
  const selection = $("#rank-k");
  function update() {
    const rows = ranks.top.filter((row) => selection.value === "all" || row.k === Number(selection.value));
    $("#rank-table").innerHTML = rows.map((row) => `<tr><th scope="row">Top ${row.k}</th>
      <td>${row.low}–${row.high}</td><td>${fixed(row.fractional, 2)}</td></tr>`).join("");
    $("#rank-summary").textContent = `${number(ranks.sellers)} paired sellers · Kendall τ-b ${fixed(ranks.kendall)}`;
  }
  selection.addEventListener("change", update);
  $("#rank-reset").addEventListener("click", () => { selection.value = "all"; update(); });
  update();

  const named = new Set(["Brook", "Cedar", "Harbor", "Juniper", "Linden", "Maple"]);
  const toy = sellers.filter((item) => named.has(item.name));
  const rawRank = averageRanks(toy, "raw");
  const adjustedRank = averageRanks(toy, "adjusted");
  function renderToy(method) {
    const chosen = method === "raw" ? rawRank : adjustedRank;
    const ordered = [...toy].sort((a, b) => chosen.get(a.name) - chosen.get(b.name) || a.name.localeCompare(b.name));
    $("#toy-ranking").innerHTML = ordered.map(({ name }) => {
      const movement = adjustedRank.get(name) - rawRank.get(name);
      const sign = movement < 0 ? "−" : movement > 0 ? "+" : "";
      return `<tr><th scope="row">${name}</th><td>${rawRank.get(name)}</td><td>${adjustedRank.get(name)}</td><td>${sign}${Math.abs(movement)}</td></tr>`;
    }).join("");
    $("#toy-summary").textContent = `Ordered by ${method} rating. Positive movement means a lower position.`;
  }
  $$('[name="toy-order"]').forEach((radio) => radio.addEventListener("change", () => renderToy(radio.value)));
  renderToy("raw");
}

function validation({ study, calibration, scenarios, volume }) {
  metrics("#validation-kpis", [
    [fixed(study.gain), "MAE reduction · stars"], [`${fixed(study.lower)}–${fixed(study.upper)}`, "95% paired bootstrap interval"],
    [percent(calibration.overall.coverage), "Observed predictive coverage"], [percent(calibration.overall.target), "Nominal target"],
  ]);
  const bandSelect = $("#coverage-band");
  const bands = volume.map(({ band }) => band);
  options(bandSelect, bands.map((band) => [band, bandName(band)]));
  function coverage() {
    const x = (i) => 63 + 104 * i;
    const y = (value) => 154 - (value - .8) * 645;
    chart("#coverage-chart", [520, 196], bands.map((band) => `${bandName(band)} reviews: ${percent(calibration[band].coverage)}`).join(". "), (draw) => {
      for (const tick of [.8, .85, .9, .95, 1]) {
        draw("line", { x1: 46, x2: 507, y1: y(tick), y2: y(tick),
          stroke: tick === .95 ? palette.target : palette.grid, "stroke-dasharray": tick === .95 ? "5 4" : "none" });
        draw("text", { x: 42, y: y(tick) + 5, "text-anchor": "end" }, Math.round(tick * 100) + "%");
      }
      draw("polyline", { points: bands.map((band, i) => `${x(i)},${y(calibration[band].coverage)}`).join(" "),
        fill: "none", stroke: palette.adjusted, "stroke-width": 3 });
      bands.forEach((band, i) => {
        const value = calibration[band].coverage;
        draw("circle", { cx: x(i), cy: y(value), r: band === bandSelect.value ? 7 : 4, fill: palette.adjusted });
        draw("text", { x: x(i), y: y(value) - 10, "text-anchor": "middle", class: "direct-label" }, percent(value));
        draw("text", { x: x(i), y: 181, "text-anchor": "middle" }, bandName(band));
      });
    });
    const row = calibration[bandSelect.value];
    $("#coverage-detail").textContent = `${bandName(bandSelect.value)} reviews · ${number(row.sellers)} paired sellers · ${fixed(row.width, 2)} stars mean interval width. Coverage conditions on the realized number of later reviews.`;
  }
  bandSelect.value = "50+";
  bandSelect.addEventListener("change", coverage);
  coverage();

  const priors = scenarios.filter((row) => row.family === "prior_strength");
  chart("#prior-chart", [520, 196], priors.map((row) => `Strength ${row.strength}: ${fixed(row.gain)} stars`).join(". "), (draw) => {
    const y = (value) => 154 - value * 650;
    for (const tick of [0, .05, .10, .15, .20]) {
      draw("line", { x1: 45, x2: 513, y1: y(tick), y2: y(tick), stroke: palette.grid });
      draw("text", { x: 41, y: y(tick) + 5, "text-anchor": "end" }, fixed(tick, 2));
    }
    priors.forEach((row, i) => {
      const x = 56 + i * 66;
      draw("rect", { x, y: y(row.gain), width: 40, height: 154 - y(row.gain),
        fill: row.strength === study.strength ? palette.adjusted : palette.raw });
      draw("text", { x: x + 20, y: y(row.gain) - 6, "text-anchor": "middle", class: "direct-label" }, fixed(row.gain));
      draw("text", { x: x + 20, y: 180, "text-anchor": "middle" }, "m " + row.strength);
    });
  });

  const family = $("#scenario-family");
  const scenario = $("#scenario-choice");
  function table() {
    const selected = scenarios.filter((row) =>
      (family.value === "all" || row.family === family.value) && (scenario.value === "all" || row.label === scenario.value)
    );
    $("#scenario-table").innerHTML = selected.map((row) => `<tr><th scope="row">${row.label}</th><td>${row.status}</td>
      <td>${fixed(row.raw)}</td><td>${fixed(row.adjusted)}</td><td>${fixed(row.gain)}</td><td>${number(row.sellers)}</td></tr>`).join("");
    $("#scenario-summary").textContent = `${selected.length} of ${scenarios.length} displayed scenarios. MAE and gain are in stars.`;
  }
  function filterFamily() {
    const previous = scenario.value;
    const scoped = scenarios.filter((row) => family.value === "all" || row.family === family.value);
    options(scenario, [["all", "All matching scenarios"], ...scoped.map(({ label }) => [label, label])]);
    if (scoped.some(({ label }) => label === previous)) scenario.value = previous;
    table();
  }
  family.addEventListener("change", filterFamily);
  scenario.addEventListener("change", table);
  $("#scenario-reset").addEventListener("click", () => {
    family.value = "all";
    scenario.value = "all";
    filterFamily();
  });
  filterFamily();
}

function operations(data) {
  metrics("#operations-kpis", [
    [number(data.orders), "Forward orders"], [number(data.evaluable), "Evaluable deliveries"],
    [number(data.late), "Late deliveries"], [percent(data.rate), "Late among evaluable"],
  ]);
  const select = $("#operations-method");
  function highlight() {
    const method = select.value;
    const describe = (row) => `${methods[row.method]}: Spearman ${signed(row.estimate)} [${signed(row.low)}, ${signed(row.high)}], ${number(row.sellers)} sellers.`;
    chart("#operations-chart", [520, 182], data.methods.map(describe).join(" "), (draw) => {
      const x = (value) => 49 + (value + .16) / .2 * 440;
      for (const tick of [-.16, -.12, -.08, -.04, 0, .04]) {
        draw("line", { x1: x(tick), x2: x(tick), y1: 25, y2: 149,
          stroke: tick === 0 ? palette.line : palette.grid, "stroke-width": tick === 0 ? 2 : 1 });
        draw("text", { x: x(tick), y: 173, "text-anchor": "middle" }, tick === 0 ? "0" : signed(tick, 2));
      }
      data.methods.forEach((row, index) => {
        const y = 51 + index * 59;
        const color = palette[row.method];
        if (method === row.method) draw("rect", { class: "association-selected", x: 7, y: y - 24, width: 501, height: 51, rx: 3, fill: "#e8f5f2" });
        draw("text", { x: 12, y: y - 9, class: "direct-label" }, methods[row.method]);
        draw("line", { x1: x(row.low), x2: x(row.high), y1: y + 8, y2: y + 8, stroke: color, "stroke-width": 3 });
        for (const limit of [row.low, row.high]) {
          draw("line", { x1: x(limit), x2: x(limit), y1: y + 3, y2: y + 13, stroke: color, "stroke-width": 2 });
        }
        draw("circle", { cx: x(row.estimate), cy: y + 8, r: 5, fill: color });
      });
    });
    $("#operations-detail").textContent = method === "both" ?
      data.methods.map(describe).join(" ") : describe(data.methods.find((row) => row.method === method));
  }
  select.addEventListener("change", highlight);
  highlight();
}

function evidence(dax) {
  const explanations = {
    "Raw MAE": "Clears report filters before looking up the locked holdout result. A subgroup selection therefore leaves the study KPI unchanged.",
    "Volume Raw MAE": "Uses TREATAS to pass the selected historical-band keys into a disconnected aggregate table. The historical basis stays explicit.",
    "Subgroup Raw MAE": "Iterates over sellers visible in the current context and gives each seller one error. This keeps the subgroup measure seller-equal even when review counts differ.",
    "Scenario Improvement": "Returns the precomputed improvement when exactly one scenario is in context. It leaves totals across multiple scenarios blank.",
    "Selected Late Delivery Rate": "Divides late deliveries by evaluable deliveries in the current context. DIVIDE handles a zero denominator, and the fixed companion measure clears filters.",
  };
  for (const [name, expression] of Object.entries(dax)) {
    const card = document.createElement("section");
    card.className = "dax-example";
    const heading = document.createElement("h3");
    heading.textContent = name;
    const explanation = document.createElement("p");
    explanation.textContent = explanations[name];
    const block = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = name + " = " + expression;
    block.append(code);
    card.append(heading, explanation, block);
    $("#dax-examples").append(card);
  }
}

try {
  const response = await fetch("study.json");
  if (!response.ok) throw new Error("Study data unavailable");
  const data = await response.json();
  const sellers = examples.map(([name, count, raw]) => {
    const estimate = blend(raw, count, data.study);
    return { name, count, raw, band: reviewBand(count), change: Math.abs(raw - estimate.adjusted), ...estimate };
  });
  overview(data);
  ratings(data.study, sellers, data.volume);
  rankings(data.ranks, sellers);
  validation(data);
  operations(data.operations);
  evidence(data.dax);
  setUpTabs();
} catch (error) {
  $("#load-error").hidden = false;
  console.error(error);
}
