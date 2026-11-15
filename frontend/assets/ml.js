import {sectionStyle} from "./plot-markers.js";

const $ = (id) => document.getElementById(id);
const fmt = (value, digits = 2) => value == null ? "—"
  : value.toLocaleString("ru-RU", {minimumFractionDigits: digits, maximumFractionDigits: digits});
const escapeHtml = (text) => String(text).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})[c]);
const API = window.location.port === "5173" ? "http://127.0.0.1:8000" : "";
const CONFIG = {responsive: true, scrollZoom: true, displayModeBar: false, doubleClick: "reset"};
const FONT = {family: 'Inter, "Segoe UI", sans-serif', color: "#425b73", size: 13};
const AXIS = {gridcolor: "#e3ebf3", zeroline: false, automargin: true};

const digits = () => state.pipe?.case === 2 ? 3 : 2;
const state = {metrics: null, pipe: null, profile: [], top: [], sort: {key: "thickness_pred", dir: 1}, selected: null};

async function api(path, options) {
  let response;
  try {
    response = await fetch(API + path, {signal: AbortSignal.timeout(20000), ...options});
  } catch (error) {
    if (error.name === "TimeoutError") {
      throw new Error("Сервер не ответил вовремя. Проверьте состояние бэкенда.");
    }
    throw new Error("API недоступен. Запустите Start Backend.cmd и обновите страницу.");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail ?? detail; } catch { /* not JSON */ }
    throw Object.assign(new Error(detail), {status: response.status});
  }
  return response;
}
const json = async (path, options) => (await api(path, options)).json();

function showError(message) {
  $("ml-error").textContent = message ?? "";
  $("ml-error").hidden = !message;
}

function metric(label, value, note = "", wide = false) {
  return `<div class="metric${wide ? " wide" : ""}"><span>${label}</span><strong>${value}</strong>${note ? `<small>${note}</small>` : ""}</div>`;
}

let requestVersion = 0;
const pending = new Map();
let comparisonVersion = 0;
let comparisonTraces = [];
let plotOptions;

function officialPipes() {
  return json("/api/ml/pipes").catch((error) => {
    if (error.status === 503) return [];
    throw error;
  });
}

function calculate(id) {
  if (!pending.has(id)) {
    const request = json(`/api/ml/section/${encodeURIComponent(id)}`, {method: "POST", signal: null})
      .finally(() => pending.delete(id));
    pending.set(id, request);
  }
  return pending.get(id);
}

export async function loadMlForecast(id) {
  const version = ++requestVersion;
  const output = $("ml-view").querySelector(".dashboard-content");
  output.hidden = true;
  $("ml-run-status").hidden = false;
  showError("");
  try {
    const official = await officialPipes();
    if (version !== requestVersion) return;
    const pipe = official.find((entry) => entry.id === id && entry.segments > 0);
    let data;
    if (pipe) {
      const [profile, top, importance, metrics] = await Promise.all([
        json(`/api/ml/profile/${id}`), json(`/api/ml/top10/${id}`),
        json(`/api/ml/importance/${id}`), json("/api/ml/metrics"),
      ]);
      data = {pipe, profile, top, importance, metrics};
    } else {
      data = await calculate(id);
      if (data.error) throw new Error(data.error);
    }
    if (version !== requestVersion) return;
    const byId = new Map(data.profile.map((row) => [row.segment_id, row]));
    Object.assign(state, {
      pipe: data.pipe, profile: data.profile, importance: data.importance, metrics: data.metrics,
      selected: null,
      top: data.top.map((row, i) => ({...byId.get(row.segment_id), ...row, rank: i + 1})),
    });
    output.hidden = false;
    renderMlForecast();
  } catch (failure) {
    if (version === requestVersion) {
      showError(failure.status === 404
        ? "Перезапустите бэкенд PipeTwin и обновите страницу: текущий сервер не поддерживает этот запрос ML."
        : failure.message);
    }
  } finally {
    if (version === requestVersion) $("ml-run-status").hidden = true;
  }
}

export function renderMlForecast() {
  renderComparison();
  if (!state.pipe || $("ml-single").hidden || $("ml-view").querySelector(".dashboard-content").hidden) return;
  renderInfo();
  renderMetrics();
  renderChart();
  renderTable();
  renderImportance();
  renderApproaches();
}

export function checkedPipes() {
  return [...$("ml-comparison-pipes").querySelectorAll("input:checked")];
}

function updatePipeSelectionSummary() {
  const selected = checkedPipes();
  $("pipe-clear").disabled = selected.length === 0;
  $("pipe-selection-summary").textContent = selected.length === 1
    ? selected[0].labels[0].textContent : selected.length ? `Выбрано: ${selected.length}` : "Участки не выбраны";
}

function resetComparison() {
  comparisonVersion++;
  comparisonTraces = [];
  $("ml-comparison-result").hidden = true;
  $("ml-comparison-status").hidden = true;
  $("ml-comparison-chart").hidden = true;
}

export function setMlComparisonPipes(pipes, optionsForPipe) {
  plotOptions = optionsForPipe;
  resetComparison();
  $("ml-comparison-all").hidden = pipes.length < 2;
  $("ml-comparison-pipes").replaceChildren(...pipes.map((pipe, index) => {
    const id = pipe.id.replace(/\.0$/, "");
    const label = document.createElement("label");
    const check = document.createElement("input");
    check.type = "checkbox";
    check.value = id;
    check.dataset.pipeIndex = index;
    check.checked = index === 0;
    label.append(check, pipe.name ? `${pipe.name} · ${id}` : id);
    return label;
  }));
  updatePipeSelectionSummary();
}

export function refreshMlComparison() {
  if ($("scenario").hidden || $("ml-view").hidden || !$("ml-comparison-result").hidden) return;
  comparePipes();
}

function renderComparison() {
  const chart = $("ml-comparison-chart");
  if (chart.hidden || !chart.getClientRects().length) return;
  const P = plotly(chart);
  if (!P) return;
  const traces = comparisonTraces.map(({id, ...trace}) => {
    const {index, threshold} = plotOptions(id);
    return {...trace, ...sectionStyle(index, trace.y, threshold)};
  });
  P.react(chart, traces, {
    autosize: true, font: FONT, margin: {l: 55, r: 20, t: 14, b: 52},
    legend: {orientation: "h", y: 1.03, yanchor: "bottom"},
    xaxis: {...AXIS, title: {text: "Расстояние от начала каждого участка, м"}},
    yaxis: {...AXIS, title: {text: "Толщина, мм"}},
  }, CONFIG).catch((error) => console.error(error));
}

async function comparePipes() {
  const ids = checkedPipes().map((input) => input.value);
  if (ids.length < 2) return;
  const version = ++comparisonVersion;
  $("ml-comparison-result").hidden = false;
  const status = $("ml-comparison-status");
  status.hidden = false;
  status.textContent = "Готовим сравнение…";
  $("ml-comparison-chart").hidden = true;
  try {
    const official = await officialPipes();
    if (version !== comparisonVersion) return;
    const traces = new Map();
    const missing = [];
    let finished = 0;
    await Promise.all(ids.map(async (id) => {
      try {
        const saved = official.find((entry) => entry.id === id && entry.segments > 0);
        const data = saved ? {pipe: saved, profile: await json(`/api/ml/profile/${id}`)} : await calculate(id);
        if (data.error) throw new Error(data.error);
        const {pipe, profile} = data;
        traces.set(id, {
          id, type: "scatter", mode: "lines+markers", name: `${pipe.id} · ${pipe.t_date}`,
          x: profile.map((row) => row.segment_id * 10), y: profile.map((row) => row.thickness_pred),
          hovertemplate: "%{x} м · %{y:.2f} мм",
        });
      } catch (error) {
        missing.push(`${id}: ${error.message}`);
      }
      if (version !== comparisonVersion) return;
      finished++;
      comparisonTraces = ids.map((id) => traces.get(id)).filter(Boolean);
      const progress = finished < ids.length ? `Обработано участков: ${finished} из ${ids.length}` : "";
      status.textContent = [progress, ...missing].filter(Boolean).join("\n");
      status.hidden = !status.textContent;
      $("ml-comparison-chart").hidden = !comparisonTraces.length;
      renderComparison();
    }));
  } catch (error) {
    if (version === comparisonVersion) status.textContent = error.message;
  }
}

$("ml-comparison-pipes").addEventListener("change", () => {
  resetComparison();
  updatePipeSelectionSummary();
});
$("ml-comparison-all").addEventListener("click", () => {
  for (const input of $("ml-comparison-pipes").querySelectorAll("input")) input.checked = true;
  resetComparison();
  updatePipeSelectionSummary();
});
$("pipe-clear").addEventListener("click", () => {
  for (const input of checkedPipes()) input.checked = false;
  resetComparison();
  updatePipeSelectionSummary();
});

function renderInfo() {
  const p = state.pipe;
  $("ml-profile-title").textContent = `Прогноз толщины вдоль трубы ${p.id} на ${p.t_date}`;
}

function renderMetrics() {
  const p = state.pipe;
  const c = state.metrics?.cases.find((item) => item.id === p.id);
  $("ml-metrics").innerHTML = [
    ...(c ? [
    metric("Итоговый r² (валидация)", fmt(c.r2, 3), escapeHtml(c.final_approach ?? "")),
    metric("Схема валидации", escapeHtml(c.validation ?? "—"), c.n_test ? `тестовых сегментов: ${c.n_test}` : "", true),
    metric("Покрытие p10–p90", c.interval_coverage == null ? "—" : `${fmt(c.interval_coverage * 100, 0)} %`, "цель 80 %"),
    ] : []),
    metric("Мин. толщина, мм", fmt(p.min_thickness, digits()), `средняя ${fmt(p.mean_thickness, digits())} мм`),
  ].join("");
}

function plotly(container) {
  if (window.Plotly) return window.Plotly;
  container.textContent = "Не удалось загрузить Plotly. Проверьте подключение к интернету и обновите страницу.";
  return null;
}

function renderChart() {
  const container = $("ml-chart");
  const P = plotly(container);
  if (!P || !container.getClientRects().length) return;
  const rows = state.profile;
  const x = rows.map((r) => r.segment_id * 10);
  const ids = rows.map((r) => r.segment_id);
  const traces = [
    {x, y: rows.map((r) => r.p90), type: "scatter", mode: "lines", line: {width: 0}, hoverinfo: "skip", showlegend: false},
    {x, y: rows.map((r) => r.p10), type: "scatter", mode: "lines", line: {width: 0}, fill: "tonexty",
      fillcolor: "rgba(37, 99, 168, 0.16)", name: "Интервал p10–p90", customdata: rows.map((r) => [r.p10, r.p90]),
      hovertemplate: `p10–p90: %{customdata[0]:.${digits()}f}–%{customdata[1]:.${digits()}f} мм<extra></extra>`},
  ];
  if (state.pipe.case === 1) {
    traces.push({x, y: rows.map((r) => r.thk_prev), type: "scatter", mode: "lines", name: "ВТД 2022 (со сдвигом)",
      line: {color: "#94a3b8", width: 1.5, dash: "dot"}, hovertemplate: "ВТД 2022: %{y:.2f} мм<extra></extra>"});
  }
  traces.push({x, y: rows.map((r) => r.thickness_pred), customdata: ids, type: "scatter", mode: "lines",
    name: "Прогноз", line: {color: "#2563a8", width: 2.5},
    hovertemplate: `Сегмент № %{customdata} · %{x} м<br>Прогноз: %{y:.${digits()}f} мм<extra></extra>`});
  traces.push({x: state.top.map((r) => r.segment_id * 10), y: state.top.map((r) => r.thickness_pred),
    customdata: state.top.map((r) => r.segment_id), type: "scatter", mode: "markers", name: "10% самых тонких",
    marker: {color: "#9a5700", size: 6, symbol: "diamond", line: {color: "#fff", width: 1}},
    hovertemplate: "Сегмент № %{customdata}: %{y:.2f} мм<extra></extra>"});
  const {threshold} = plotOptions(state.pipe.id);
  const critical = threshold === null ? [] : rows.filter((row) => row.thickness_pred <= threshold);
  if (critical.length) {
    traces.push({x: critical.map((row) => row.segment_id * 10), y: critical.map((row) => row.thickness_pred),
      customdata: critical.map((row) => row.segment_id), type: "scatter", mode: "markers", name: "На пороге или ниже",
      marker: {color: "#dc2626", size: 11, line: {color: "#7f1d1d", width: 1.5}},
      hovertemplate: "Сегмент № %{customdata}: %{y:.2f} мм<extra></extra>"});
  }
  const shapes = [];
  const selected = rows.find((r) => r.segment_id === state.selected);
  if (selected) {
    traces.push({x: [selected.segment_id * 10], y: [selected.thickness_pred], customdata: [selected.segment_id],
      type: "scatter", mode: "markers", name: "Выбранный сегмент",
      marker: {color: threshold !== null && selected.thickness_pred <= threshold ? "#dc2626" : "#f59e0b",
        size: 14, symbol: "diamond", line: {color: "#7c2d12", width: 1.5}},
      hovertemplate: "Выбран № %{customdata}: %{y:.2f} мм<extra></extra>"});
    shapes.push({type: "line", xref: "x", yref: "paper", x0: selected.segment_id * 10, x1: selected.segment_id * 10,
      y0: 0, y1: 1, line: {color: "#f59e0b", width: 1.5, dash: "dash"}});
  }
  const layout = {
    uirevision: state.pipe.id, autosize: true, dragmode: "pan", hovermode: "x unified",
    margin: {l: 55, r: 18, t: 14, b: 52}, paper_bgcolor: "#fff", plot_bgcolor: "#fff", font: FONT, shapes,
    legend: {orientation: "h", y: 1.02, yanchor: "bottom", x: 0},
    xaxis: {...AXIS, title: {text: "Дистанция по участку, м"}},
    yaxis: {...AXIS, title: {text: "Толщина, мм"}},
  };
  P.react(container, traces, layout, CONFIG).then(() => {
    if (container.dataset.bound) return;
    container.dataset.bound = "1";
    container.on("plotly_click", ({points}) => {
      const id = points?.find((pt) => Number.isInteger(pt.customdata))?.customdata;
      if (id != null) select(id, true);
    });
  }).catch((failure) => {
    console.error(failure);
    container.textContent = "Не удалось отрисовать график.";
  });
}

function select(id, fromChart = false) {
  state.selected = state.selected === id && !fromChart ? null : id;
  renderChart();
  renderTable();
  if (fromChart) $("ml-table").querySelector("tr.selected")?.scrollIntoView({block: "nearest"});
}

const COLUMNS = [
  {key: "rank", label: "№"},
  {key: "segment_id", label: "Сегмент"},
  {key: "distance", label: "Дистанция, м", value: (r) => r.segment_id * 10, format: (r) => `${r.segment_id * 10}–${r.segment_id * 10 + 10}`},
  {key: "thickness_pred", label: "Прогноз, мм", format: (r) => fmt(r.thickness_pred, digits())},
  {key: "p10", label: "p10, мм", format: (r) => fmt(r.p10, digits())},
  {key: "p90", label: "p90, мм", format: (r) => fmt(r.p90, digits())},
  {key: "thk_prev", label: "ВТД 2022, мм", format: (r) => fmt(r.thk_prev), case: 1},
];

function renderTable() {
  const columns = COLUMNS.filter((c) => !c.case || c.case === state.pipe.case);
  const {key, dir} = state.sort;
  const col = columns.find((c) => c.key === key) ?? columns[0];
  const value = col.value ?? ((r) => r[col.key]);
  const rows = [...state.top].sort((a, b) => dir * ((value(a) ?? Infinity) - (value(b) ?? Infinity)));
  const head = columns.map((c) => `<th data-sort="${c.key}" scope="col" tabindex="0"${c.key === col.key
    ? ` aria-sort="${dir > 0 ? "ascending" : "descending"}"` : ""}>${c.label}</th>`).join("");
  const body = rows.map((r) => `<tr data-ml-segment="${r.segment_id}"${r.segment_id === state.selected ? ' class="selected"' : ""}>${
    columns.map((c) => `<td>${c.format ? c.format(r) : r[c.key]}</td>`).join("")}</tr>`).join("");
  $("ml-table").innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderImportance() {
  const container = $("ml-importance");
  const P = plotly(container);
  if (!P || !container.getClientRects().length) return;
  const rows = [...state.importance].reverse();
  container.style.height = `${Math.max(240, rows.length * 22 + 70)}px`;
  P.react(container, [{
    type: "bar", orientation: "h", x: rows.map((r) => r.importance), y: rows.map((r) => r.feature),
    marker: {color: "#2563a8"}, hovertemplate: "%{y}: %{x:.1f}<extra></extra>",
  }], {
    autosize: true, margin: {l: 10, r: 18, t: 8, b: 44}, paper_bgcolor: "#fff", plot_bgcolor: "#fff", font: FONT,
    xaxis: {...AXIS, title: {text: "Важность"}}, yaxis: {...AXIS, gridcolor: "rgba(0,0,0,0)", tickmode: "linear", dtick: 1},
  }, {...CONFIG, scrollZoom: false}).catch((failure) => console.error(failure));
}

function renderApproaches() {
  $("ml-validation").hidden = !state.metrics;
  if (!state.metrics) return;
  const rows = state.metrics.approaches;
  const cell = (v) => v == null ? "—" : typeof v === "number" ? fmt(v, Number.isInteger(v) ? 0 : 4) : escapeHtml(v);
  $("ml-approaches").innerHTML = `<table><thead><tr><th>Подход</th><th>Схема</th><th>n</th><th>r²</th><th>MAE</th><th>Покрытие</th></tr></thead><tbody>${
    rows.map((r) => `<tr><td>${cell(r.approach)}</td><td>${cell(r.scheme)}</td><td>${cell(r.n_test)}</td><td>${cell(r.r2)}</td><td>${cell(r.mae)}</td><td>${cell(r.coverage)}</td></tr>`).join("")
  }</tbody></table>`;
}

$("ml-table").addEventListener("click", (event) => {
  const th = event.target.closest("th[data-sort]");
  if (th) {
    const key = th.dataset.sort;
    state.sort = {key, dir: state.sort.key === key ? -state.sort.dir : 1};
    renderTable();
    return;
  }
  const row = event.target.closest("tr[data-ml-segment]");
  if (row) select(Number(row.dataset.mlSegment));
});
$("ml-table").addEventListener("keydown", (event) => {
  if ((event.key === "Enter" || event.key === " ") && event.target.matches("th[data-sort]")) {
    event.preventDefault();
    event.target.click();
  }
});
