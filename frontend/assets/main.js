import {checkedPipes, loadMlForecast, refreshMlComparison, renderMlForecast, setMlComparisonPipes} from "./ml.js";
import {sectionStyle} from "./plot-markers.js";

const $ = (id) => document.getElementById(id);
const number = (value) => value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
let pipes = [];
let current;
let uploadVersion = 0;
let co2Rate;
let co2Request = 0;
let mlSelection;
let profileRevision = 0;
const currentYear = new Date().getFullYear();
const msPerYear = 365.25 * 86400000;
const resultPipes = new Map();
const pipeThresholds = new Map();
const defaultThreshold = "4";
let exporting = false;
const thicknessOf = (segment) => segment.replacement?.wall_mm ?? segment.thickness_pred;
const orderedInspection = () => current.coordinate_basis === "inspection_order";
const positionOf = (segment) => orderedInspection() ? segment.segment_id : segment.segment_id * 10;
const positionLabel = () => orderedInspection() ? "Номер записи ВТД" : "Дистанция по профилю, м";
const positionUnit = () => orderedInspection() ? "" : " м";
const pointName = () => orderedInspection() ? "Точка ВТД" : "Сегмент";
const baseline = (segment) => Date.parse(segment.replacement?.date ?? segment.inspection_date
  ?? current.commissioned_at ?? `${currentYear}-01-01`);
const endOfYear = (year) => Date.UTC(year, 11, 31);

async function apiRequest(path, options) {
  if (window.location.port !== "5173") return fetch(path, options);
  try {
    return await fetch(`http://127.0.0.1:8000${path}`, options);
  } catch {
    throw new Error("API недоступен. Запустите Start Backend.cmd и обновите страницу.");
  }
}

function threshold() {
  const value = $("threshold").valueAsNumber;
  return value >= 0.01 ? value : null;
}

function plotOptions(id) {
  const index = pipes.findIndex((pipe) => pipe.id.replace(/\.0$/, "") === id.replace(/\.0$/, ""));
  const value = Number(pipeThresholds.get(pipes[index]?.id ?? id) ?? defaultThreshold);
  return {index: Math.max(index, 0), threshold: value >= 0.01 ? value : null};
}

function metric(label, value, alert = false) {
  return `<div class="metric${alert ? " alert" : ""}"><span>${label}</span><strong>${value}</strong></div>`;
}

function showPanel(id) {
  for (const button of document.querySelectorAll("[data-panel]")) {
    const active = button.dataset.panel === id;
    button.setAttribute("aria-pressed", active);
    $(button.dataset.panel).hidden = !active;
  }
}

function chooseSegment(id) {
  if (!current) return;
  $("segment").value = id;
  const scenarioWasHidden = $("scenario").hidden;
  showPanel("scenario");
  const detailWasOpen = $("scenario-detail").open;
  $("scenario-detail").open = true;
  if (scenarioWasHidden || detailWasOpen) renderScenario(scenarioWasHidden);
  $("segment").focus({preventScroll: true});
}

const chartHandlers = new WeakSet();
const chartUpdates = new WeakMap();

async function updateChart(container, plotly, traces, layout, profile) {
  const previous = chartUpdates.get(container);
  const update = {};
  chartUpdates.set(container, update);
  update.done = (async () => {
    await previous?.done;
    if (chartUpdates.get(container) !== update || !container.getClientRects().length) return;

    try {
      await plotly.react(container, traces, layout, {
        responsive: true, scrollZoom: true, displayModeBar: false, doubleClick: "reset",
      });
      if (!profile || chartHandlers.has(container)) return;
      container.on("plotly_click", ({points: [point]}) => {
        if (point?.customdata != null) chooseSegment(point.customdata);
      });
      chartHandlers.add(container);
    } catch (failure) {
      console.error(failure);
      if (chartUpdates.get(container) === update) {
        plotly.purge(container);
        chartHandlers.delete(container);
        container.textContent = "Не удалось отрисовать график.";
      }
    }
  })();
  await update.done;
}

function graph(container, points, limit, labels, profile = false, range = null) {
  container.hidden = false;
  const plotly = window.Plotly;
  if (!plotly) {
    container.textContent = "Не удалось загрузить Plotly. Проверьте подключение к интернету и обновите страницу.";
    return;
  }
  const x = [], y = [], segmentIds = [];
  for (const point of points) {
    if (profile && !labels.markersOnly && segmentIds.length && point.id !== segmentIds.at(-1) + 1) {
      x.push(null);
      y.push(null);
      segmentIds.push(null);
    }
    x.push(point.x);
    y.push(point.y);
    segmentIds.push(point.id ?? null);
  }
  const traces = [{
    type: labels.markersOnly && points.length > 3000 ? "scattergl" : "scatter",
    mode: labels.markersOnly ? "markers" : "lines+markers", x, y, customdata: segmentIds,
    line: {color: "#2563a8", width: 3},
    marker: {
      size: y.map((value) => value !== null && limit !== null && value <= limit ? 12 : 7),
      color: y.map((value) => value !== null && limit !== null && value <= limit ? "#dc2626" : "#2563a8"),
      line: {color: "#fff", width: 1},
    },
    hovertemplate: (profile || labels.pointName ? (labels.pointName ?? pointName()) + " № %{customdata}<br>" : "")
      + (labels.markersOnly ? "" : (labels.calendar ? "%{x|%d.%m.%Y}" : "%{x:.2~f}" + labels.unit) + " · ")
      + "%{y:.2~f} мм<extra></extra>",
  }];
  if (limit !== null) {
    traces.push({
      type: "scatter", mode: "lines",
      x: range ?? [points[0].x, points.at(-1).x],
      y: [limit, limit],
      line: {color: "#b91c1c", dash: "dash", width: 2},
      hovertemplate: "Порог %{y:.2~f} мм<extra></extra>",
    });
  }
  const axis = {gridcolor: "#e3ebf3", zeroline: false, automargin: true};
  const layout = {
    uirevision: labels.revision,
    autosize: true, dragmode: "pan", hovermode: "closest", showlegend: false,
    margin: {l: 55, r: 18, t: 14, b: 52},
    paper_bgcolor: "#fff", plot_bgcolor: "#fff",
    font: {family: 'Inter, "Segoe UI", sans-serif', color: "#425b73", size: 13},
    xaxis: {...axis, title: {text: labels.x}, ...(labels.calendar && {type: "date", tickformat: "%Y"})},
    yaxis: {...axis, title: {text: "Толщина, мм"}},
  };
  if (range && range[0] !== range[1]) {
    const padding = (range[1] - range[0]) * 0.02;
    layout.xaxis.range = [range[0] - padding, range[1] + padding];
  }
  updateChart(container, plotly, traces, layout, profile);
}

function pipePoints() {
  return current.segments.map((segment) => ({
    id: segment.segment_id, x: positionOf(segment), y: thicknessOf(segment),
  }));
}

function renderTable() {
  const limit = threshold();
  const order = [...current.segments].sort((a, b) => thicknessOf(a) - thicknessOf(b));
  const filter = $("filter").value;
  const rows = filter === "all" ? current.segments : filter === "priority"
    ? order.slice(0, Math.ceil(order.length / 10))
    : order.filter((s) => limit !== null && thicknessOf(s) <= limit);
  if (!rows.length) {
    $("table").textContent = limit === null ? "Задайте порог толщины." : "На пороге или ниже сегментов нет.";
    return;
  }
  $("table").innerHTML = `<table><thead><tr><th>${pointName()}</th>${orderedInspection() ? "" : "<th>Дистанция, м</th>"}<th>Толщина, мм</th><th>Статус</th><th>Исходная дата</th></tr></thead><tbody>${rows.map((s) => {
    const start = s.segment_id * 10;
    const distance = current.length_m ? `${start}–${number(Math.min(start + 10, current.length_m))}` : start;
    const critical = limit !== null && thicknessOf(s) <= limit;
    return `<tr><td><button data-segment="${s.segment_id}">№ ${s.segment_id}</button></td>${orderedInspection() ? "" : `<td>${distance}</td>`}<td>${number(thicknessOf(s))}</td><td class="${critical ? "critical" : ""}">${s.replacement ? "Заменён · " : ""}${limit === null ? "Порог не задан" : critical ? "На пороге или ниже" : "Выше порога"}</td><td>${s.replacement?.date ?? s.inspection_date ?? current.commissioned_at ?? "—"}</td></tr>`;
  }).join("")}</tbody></table>`;
}

function renderScenario(includeOverview = true) {
  if ($("scenario").hidden) return;
  const ml = $("forecast-method").value === "ml";
  $("scenario-title").textContent = ml ? "ML-прогноз толщины"
    : orderedInspection() ? "Прогноз по точкам ВТД" : "Прогноз коррозии по всей трубе";
  const wasHidden = $("ml-view").hidden;
  $("ml-view").hidden = !ml;
  $("physics-view").hidden = ml;
  if (ml) {
    const multiple = checkedPipes().length > 1;
    $("ml-single").hidden = multiple;
    if (multiple) {
      mlSelection = undefined;
      refreshMlComparison();
      return;
    }
    const id = current.id.replace(/\.0$/, "");
    if (mlSelection !== id) {
      mlSelection = id;
      loadMlForecast(id);
    } else if (wasHidden) {
      renderMlForecast();
    }
    return;
  }
  const first = $("start-year").valueAsNumber;
  const last = $("years").valueAsNumber;
  const startYear = Date.UTC(first, 0, 1);
  const years = endOfYear(last);
  const limit = threshold();
  const validYears = Number.isInteger(first) && Number.isInteger(last)
    && first >= 1900 && last <= 9999 && last >= first && last - first <= 100;
  if (includeOverview) renderForecastProfile(startYear, years, limit, validYears);
  if ($("scenario-detail").open) renderSegmentScenario(startYear, years, limit, validYears);
}

function renderForecastProfile(startYear, years, limit, validYears) {
  const points = [];
  for (const segment of current.segments) {
    const rate = co2Rate;
    const installed = baseline(segment);
    if (validYears && Number.isFinite(rate) && rate >= 0 && installed <= years) {
      points.push({id: segment.segment_id, x: positionOf(segment),
        y: Math.max(0, thicknessOf(segment) - rate * (years - installed) / msPerYear),
        before: Math.max(0, thicknessOf(segment) - rate * Math.max(0, startYear - installed) / msPerYear)});
    }
  }
  if (!orderedInspection() && points.length === current.segments.length) {
    resultPipes.set(current.id, {
      id: current.id, length_m: current.length_m,
      segments: points.map(({id, y}) => ({segment_id: id, thickness_pred: y})),
    });
    updateExport();
  }
  const total = current.segments.length;
  $("rate-source").textContent = !validYears ? "Укажите календарные годы по порядку. Интервал — до 100 лет."
    : co2Rate === undefined ? "Введите условия CO₂ и нажмите «Рассчитать»."
    : !points.length ? "Конечный год раньше даты исходной толщины." : "";
  const min = points.reduce((lowest, point) => !lowest || point.y < lowest.y ? point : lowest, null);
  const critical = points.filter((point) => limit !== null && point.y <= limit).length;
  $("forecast-metrics").innerHTML = metric(orderedInspection() ? "Точек с прогнозом" : "С прогнозом", `${points.length} из ${total}`)
    + metric("Минимум в конце интервала", min ? `${number(min.y)} мм · № ${min.id}` : "—")
    + metric("Макс. утонение за интервал", min
      ? `${number(points.reduce((max, point) => Math.max(max, point.before - point.y), 0))} мм` : "—")
    + metric("На пороге или ниже", !min ? "—" : limit === null ? "Задайте порог" : critical, critical > 0);
  $("forecast-plot").hidden = !points.length;
  if (points.length) {
    graph($("forecast-profile"), points, limit,
      {x: positionLabel(), unit: positionUnit(), markersOnly: orderedInspection(),
        revision: profileRevision + ":" + current.id + ":" + startYear + ":" + years},
      true, [positionOf(current.segments[0]), positionOf(current.segments.at(-1))]);
  }
}

function renderSegmentScenario(startYear, years, limit, validYears) {
  const segment = current.segments.find((row) => row.segment_id === Number($("segment").value));
  const details = [
    segment.measurement_ref && "Строка ВТД " + segment.measurement_ref,
    segment.replacement?.date && "Дата замены " + segment.replacement.date,
    !segment.replacement && segment.inspection_date && "Дата замера " + segment.inspection_date,
    !segment.replacement && current.commissioned_at && "Дата ввода " + current.commissioned_at,
  ].filter(Boolean);
  $("measurement-info").textContent = details.join(" · ");
  $("measurement-info").hidden = !details.length;
  const start = thicknessOf(segment);
  const rate = co2Rate;
  const installed = baseline(segment);
  const firstYear = Math.max(startYear, installed);
  const hasForecast = Number.isFinite(rate) && rate >= 0 && validYears && installed <= years;
  $("segment-plot").hidden = !hasForecast;
  if (!hasForecast) {
    $("scenario-metrics").innerHTML = metric("Исходная толщина", `${number(start)} мм`)
      + metric("Прогноз", "Недостаточно данных");
    return;
  }
  const initial = Math.max(0, start - rate * (firstYear - installed) / msPerYear);
  const end = Math.max(0, start - rate * (years - installed) / msPerYear);
  const time = limit === null ? "Задайте порог" : initial <= limit ? "Достигнут к началу"
    : end > limit ? "Выше порога до конца" : new Date(installed + (start - limit) / rate * msPerYear).toLocaleDateString("ru-RU", {timeZone: "UTC"});
  $("scenario-metrics").innerHTML = metric("Толщина в начале интервала", `${number(initial)} мм`)
    + metric("Скорость утонения", `${rate.toLocaleString("ru-RU", {maximumSignificantDigits: 4})} мм/год`)
    + metric("Толщина в конце интервала", `${number(end)} мм`)
    + metric("Достижение порога", time, limit !== null && end <= limit);
  const points = [{x: firstYear, y: initial}];
  const zeroYear = installed + start / rate * msPerYear;
  if (rate > 0 && zeroYear > firstYear && zeroYear < years) points.push({x: zeroYear, y: 0});
  if (years > firstYear) points.push({x: years, y: end});
  graph($("scenario-chart"), points, limit, {
    x: "Год", calendar: true,
    revision: [profileRevision, current.id, segment.segment_id, startYear, years].join(":"),
  });
}

function render() {
  const selected = checkedPipes().map((input) => pipes[Number(input.dataset.pipeIndex)]);
  const multipleProfiles = !$("profile").hidden && selected.length > 1;
  if (multipleProfiles) {
    renderSelectedProfiles(selected);
    return;
  }
  const limit = threshold();
  const count = current.segments.filter((row) => limit !== null && thicknessOf(row) <= limit).length;
  const min = current.segments.reduce((a, b) => thicknessOf(a) <= thicknessOf(b) ? a : b);
  $("metrics").innerHTML = metric(orderedInspection() ? "Точек ВТД" : "Сегментов", current.segments.length)
    + metric("Минимальная толщина", `${number(thicknessOf(min))} мм · № ${min.segment_id}`)
    + metric("На пороге или ниже", limit === null ? "Задайте порог" : count, count > 0);
  if (!$("profile").hidden) {
    $("profile-title").textContent = orderedInspection() ? "Замеры толщины ВТД"
      : current.source_kind === "nominal" ? "Номинальная толщина по паспорту" : "Толщина вдоль трубы";
    $("profile-basis").hidden = !orderedInspection() && current.source_kind !== "nominal";
    graph($("profile-chart"), pipePoints(), limit, {
      x: positionLabel(), unit: positionUnit(), markersOnly: orderedInspection(), revision: profileRevision + ":" + current.id,
    }, true);
  }
  if (!$("inspection").hidden) renderTable();
  renderScenario();
}

function renderSelectedProfiles(selected) {
  $("profile-title").textContent = "Профили выбранных участков";
  $("profile-basis").hidden = true;
  const byRecord = selected.some((pipe) => pipe.coordinate_basis === "inspection_order");
  let minimum = Infinity;
  let count = 0;
  const traces = selected.map((pipe) => {
    const x = [];
    const y = [];
    let previous;
    for (const row of pipe.segments) {
      const position = row.segment_id;
      if (!byRecord && previous != null && position !== previous + 1) {
        x.push(null);
        y.push(null);
      }
      const thickness = thicknessOf(row);
      x.push(byRecord ? position : position * 10);
      y.push(thickness);
      minimum = Math.min(minimum, thickness);
      previous = position;
    }
    count += pipe.segments.length;
    const {index, threshold} = plotOptions(pipe.id);
    return {
      type: x.length > 3000 ? "scattergl" : "scatter",
      mode: byRecord ? "markers" : "lines+markers",
      ...sectionStyle(index, y, threshold), name: pipe.id, x, y,
      hovertemplate: "%{y:.2f} мм · %{fullData.name}<extra></extra>",
    };
  });
  $("metrics").innerHTML = metric("Участков", selected.length)
    + metric(byRecord ? "Точек" : "Сегментов", count)
    + metric("Минимальная толщина", `${number(minimum)} мм`);
  const chart = $("profile-chart");
  if (!window.Plotly) {
    chart.textContent = "Не удалось загрузить Plotly.";
    return;
  }
  updateChart(chart, window.Plotly, traces, {
    autosize: true, dragmode: "pan", showlegend: true,
    uirevision: [profileRevision, ...selected.map((pipe) => pipe.id)].join(":"),
    legend: {orientation: "h", y: 1.12},
    margin: {l: 55, r: 18, t: 35, b: 52},
    xaxis: {title: {text: byRecord ? "Номер точки или сегмента на участке" : "Расстояние от начала участка, м"}},
    yaxis: {title: {text: "Толщина, мм"}},
  }, false);
}

function changePipe(pipe) {
  current = pipe;
  $("threshold").value = pipeThresholds.get(current.id) ?? defaultThreshold;
  co2Request++;
  co2Rate = undefined;
  $("co2-error").hidden = true;
  for (const [input, param] of Object.entries(co2Fields)) {
    $(input).value = current.co2_inputs?.[param] ?? co2Defaults[param];
  }
  $("co2-input-source").textContent = (current.co2_sources ?? []).join(" · ");
  const ordered = orderedInspection();
  const nominal = current.source_kind === "nominal";
  $("replacement-tab").hidden = ordered;
  if (ordered && !$("replacement").hidden) showPanel("profile");
  $("profile-title").textContent = ordered ? "Замеры толщины ВТД" : nominal ? "Номинальная толщина по паспорту" : "Толщина вдоль трубы";
  $("profile-basis").hidden = !ordered && !nominal;
  $("profile-basis").textContent = ordered
    ? `Замеры от ${current.segments[0].inspection_date ?? "неуказанной даты"} в порядке записей CSV. Ось показывает номера записей.`
    : "Исходная дата — ввод в эксплуатацию " + current.commissioned_at + ". Используется паспортная толщина.";
  $("inspection-title").textContent = ordered ? "Точки ВТД для проверки" : "Сегменты для проверки";
  $("forecast-title").textContent = ordered ? "Толщина в точках ВТД в конце интервала" : "Толщина вдоль трубы в конце интервала";
  $("detail-title").textContent = ordered ? "Изменение толщины в точке по годам" : "Изменение толщины сегмента по годам";
  $("detail-label").textContent = pointName();
  $("search-label").textContent = ordered ? "Найти точку ВТД" : "Найти сегмент";
  $("search-value").placeholder = ordered ? "Номер точки или row:123" : "Номер сегмента";
  $("search-value").value = "";
  $("search-status").hidden = true;
  const options = document.createDocumentFragment();
  for (const row of current.segments) {
    options.append(new Option(ordered ? `Точка № ${row.segment_id}` :
      `№ ${row.segment_id} · от ${positionOf(row)} м`, row.segment_id));
  }
  $("segment").replaceChildren(options);
  if (!ordered) {
    for (const id of ["replacement-start", "replacement-end"]) {
      const choices = document.createDocumentFragment();
      for (const row of current.segments) {
        choices.append(new Option(`№ ${row.segment_id} · от ${positionOf(row)} м`, row.segment_id));
      }
      $(id).replaceChildren(choices);
    }
    loadReplacement();
  }
  $("segment").value = current.segments.reduce((a, b) => thicknessOf(a) <= thicknessOf(b) ? a : b).segment_id;
  render();
}

function setData(data, name) {
  pipes = data;
  setMlComparisonPipes(pipes, plotOptions);
  resultPipes.clear();
  $("export-pipe").replaceChildren();
  const first = Math.max(currentYear, ...data.map((pipe) => Number(pipe.commissioned_at?.slice(0, 4) ?? 0)));
  $("start-year").value = first;
  $("years").value = first + 5;
  updateExport();
  mlSelection = undefined;
  profileRevision++;
  $("save-status").hidden = true;
  $("source").textContent = name;
  $("empty-state").hidden = true;
  $("empty-selection").hidden = true;
  $("loaded-controls").hidden = false;
  $("dashboard-content").hidden = false;
  $("download").disabled = false;
  changePipe(pipes[0]);
}

function error(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}

document.querySelectorAll("[data-panel]").forEach((button) => button.addEventListener("click", () => {
  showPanel(button.dataset.panel);
  if (current) {
    render();
    if (button.dataset.panel === "scenario" && !$("ml-view").hidden) renderMlForecast();
  }
}));
document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-segment]");
  if (target) chooseSegment(target.dataset.segment);
});
function renderPipeSelection() {
  const selected = checkedPipes().map((input) => pipes[Number(input.dataset.pipeIndex)]);
  $("dashboard-content").hidden = !selected.length;
  $("empty-selection").hidden = !!selected.length;
  $("download").disabled = !selected.length;
  if (!selected.length) {
    current = undefined;
    mlSelection = undefined;
    co2Request++;
    return;
  }
  const active = selected.includes(current) ? current : selected[0];
  if (active !== current) changePipe(active);
  else render();
}
$("ml-comparison-pipes").addEventListener("change", renderPipeSelection);
$("ml-comparison-all").addEventListener("click", renderPipeSelection);
$("pipe-clear").addEventListener("click", renderPipeSelection);
$("threshold").addEventListener("input", () => {
  if (!current) return;
  pipeThresholds.set(current.id, $("threshold").value);
  render();
  if (!$("scenario").hidden && !$("ml-view").hidden) renderMlForecast();
});
$("filter").addEventListener("change", () => current && renderTable());
$("segment").addEventListener("change", () => renderScenario(false));
$("scenario-detail").addEventListener("toggle", () => {
  if (current && $("scenario-detail").open) renderScenario(false);
});
$("forecast-method").addEventListener("change", () => current && renderScenario());
function clearCo2() {
  co2Request++;
  co2Rate = undefined;
  $("co2-error").hidden = true;
  if (current) renderScenario();
}
const co2Fields = {
  temperature: "temperature_c", pressure: "total_pressure_bar", "co2-percent": "co2_mol_percent",
  ph: "ph", velocity: "flow_velocity_ms", diameter: "diameter_mm",
};
const co2Defaults = {
  temperature_c: 25, total_pressure_bar: 10, co2_mol_percent: 1,
  ph: 5, flow_velocity_ms: 1, diameter_mm: 200,
};
for (const id of Object.keys(co2Fields)) {
  $(id).addEventListener("input", clearCo2);
}
$("co2-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  clearCo2();
  const request = co2Request;
  const params = new URLSearchParams(Object.entries(co2Fields).map(([id, param]) => [param, $(id).value]));
  try {
    const response = await apiRequest(`/api/corrosion/co2?${params}`);
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Проверьте параметры CO₂.");
    if (request !== co2Request) return;
    co2Rate = data.rate;
    renderScenario();
  } catch (failure) {
    if (request !== co2Request) return;
    $("co2-error").textContent = failure.message;
    $("co2-error").hidden = false;
  }
});
for (const id of ["start-year", "years"]) $(id).addEventListener("input", () => current && renderScenario());
$("file").addEventListener("change", async () => {
  const file = $("file").files[0];
  if (!file) return;
  const version = ++uploadVersion;
  error("");
  $("upload-status").hidden = false;
  $("dashboard-content").setAttribute("aria-busy", "true");
  try {
    const asOf = $("inspection-as-of").value;
    const path = asOf ? `/api/forecast?${new URLSearchParams({as_of: asOf})}` : "/api/forecast";
    const response = await apiRequest(path, {method: "POST", body: file});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail);
    if (version === uploadVersion) setData(data, file.name);
  } catch (failure) {
    if (version === uploadVersion) error(failure.message);
  } finally {
    if (version === uploadVersion) {
      $("file").value = "";
      $("upload-status").hidden = true;
      $("dashboard-content").removeAttribute("aria-busy");
    }
  }
});
const replacementFields = {
  date: "date", wall_mm: "wall", diameter_mm: "diameter",
  steel: "steel", coating: "coating", notes: "notes",
};

function loadReplacement() {
  const start = $("replacement-start").value;
  $("replacement-end").value = start;
  const replacement = current.segments.find((segment) => segment.segment_id === Number(start))?.replacement;
  for (const [key, field] of Object.entries(replacementFields)) {
    $("replacement-" + field).value = replacement?.[key] ?? "";
  }
  $("replacement-status").textContent = current.segments.some((segment) => segment.replacement)
    ? `Сегментов с отметкой о замене: ${current.segments.filter((segment) => segment.replacement).length}.`
    : "";
}

function setReplacement(replacement) {
  if (!current || orderedInspection()) return;
  const start = Number($("replacement-start").value);
  const end = Number($("replacement-end").value);
  const status = $("replacement-status");
  if (start > end) {
    status.textContent = "Последний сегмент должен быть не раньше первого.";
    return;
  }
  const affected = current.segments.filter((segment) => start <= segment.segment_id && segment.segment_id <= end);
  if (replacement && affected.length !== end - start + 1) {
    status.textContent = "В диапазоне есть сегменты без данных. Выберите непрерывный диапазон.";
    return;
  }
  if (replacement && affected.some((segment) => (segment.inspection_date ?? current.commissioned_at) > replacement.date)) {
    status.textContent = "Дата замены должна быть не раньше исходной даты в выбранном диапазоне.";
    return;
  }
  for (const segment of affected) {
    if (replacement) segment.replacement = replacement;
    else delete segment.replacement;
  }
  profileRevision++;
  status.textContent = `Сегментов обновлено: ${affected.length}. Сохраните профиль CSV, чтобы записать изменения в файл.`;
  $("save-status").textContent = "Есть изменения для сохранения.";
  $("save-status").hidden = false;
  render();
}

$("replacement-start").addEventListener("change", loadReplacement);
$("replacement-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const wall = $("replacement-wall").valueAsNumber;
  const diameter = $("replacement-diameter").valueAsNumber;
  if (!Number.isFinite(diameter) || !(wall > 0 && wall < diameter / 2)) {
    $("replacement-status").textContent = "Толщина должна быть меньше половины наружного диаметра.";
    return;
  }
  const replacement = Object.fromEntries(Object.entries(replacementFields).map(([key, field]) =>
    [key, $("replacement-" + field).value]));
  replacement.wall_mm = wall;
  replacement.diameter_mm = diameter;
  setReplacement(replacement);
});
$("remove-replacement").addEventListener("click", () => setReplacement(null));

function csvCell(value) {
  return '"' + String(value ?? "").replaceAll('"', '""') + '"';
}

function updateExport() {
  const physical = $("export-model").value === "physics";
  const ids = [...resultPipes.keys()];
  const select = $("export-pipe");
  $("export-pipe-control").hidden = !physical || !ids.length;
  const options = ids.map((id) => new Option(id, id));
  if (ids.length > 1) options.push(new Option("Все рассчитанные", ""));
  if (select.options.length !== options.length) {
    const previous = select.value;
    const hadOptions = select.options.length > 0;
    select.replaceChildren(...options);
    if (hadOptions && options.some((option) => option.value === previous)) select.value = previous;
  }
  $("download-result").disabled = exporting || (physical && !ids.length);
  if (!exporting) {
    $("result-status").textContent = !physical || ids.length ? ""
      : pipes.some((pipe) => pipe.coordinate_basis !== "inspection_order")
        ? "Рассчитайте физический прогноз для участка."
        : "Загрузите паспорт или профиль с 10-метровыми сегментами.";
  }
}

$("export-model").addEventListener("change", updateExport);
$("download-result").addEventListener("click", async () => {
  if (exporting) return;
  const physical = $("export-model").value === "physics";
  const selectedId = $("export-pipe").value;
  const forecasts = selectedId ? [resultPipes.get(selectedId)] : [...resultPipes.values()];
  if (physical && !forecasts.length) return;
  exporting = true;
  $("export-model").disabled = true;
  updateExport();
  const status = $("result-status");
  status.textContent = "Готовим result.csv…";
  let message;
  try {
    const response = physical
      ? await apiRequest("/api/result", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(forecasts),
      })
      : await apiRequest("/api/ml/result.csv");
    if (!response.ok) {
      const error = await response.json();
      throw new Error(typeof error.detail === "string" ? error.detail : "Не удалось получить result.csv.");
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = "result.csv";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    message = "Файл result.csv готов.";
  } catch (failure) {
    message = failure.message;
  } finally {
    exporting = false;
    $("export-model").disabled = false;
    updateExport();
    status.textContent = message;
  }
});

$("download").addEventListener("click", async () => {
  const fields = Object.keys(replacementFields);
  const columns = ["id", "segment_id", "thickness_pred", "inspection_date", "rate", "coordinate_basis", "measurement_ref",
    "source_kind", "commissioned_at", "length_m", "pipe_name", ...fields.map((field) => "replacement_" + field)];
  const rows = pipes.flatMap((pipe) => pipe.segments.map((segment) =>
    [pipe.id, segment.segment_id, segment.thickness_pred, segment.inspection_date, segment.rate,
      pipe.coordinate_basis, segment.measurement_ref, pipe.source_kind, pipe.commissioned_at, pipe.length_m, pipe.name,
      ...fields.map((field) => segment.replacement?.[field])].map(csvCell).join(",")));
  const name = "pipetwin-profile.csv";
  const file = new Blob(["\uFEFF", [columns.join(","), ...rows].join("\r\n"), "\r\n"],
    {type: "text/csv;charset=utf-8"});
  const status = $("save-status");
  const revision = profileRevision;
  try {
    if (window.showSaveFilePicker) {
      const handle = await window.showSaveFilePicker({
        suggestedName: name, types: [{description: "Профиль CSV", accept: {"text/csv": [".csv"]}}],
      });
      const writable = await handle.createWritable();
      await writable.write(file);
      await writable.close();
      if (revision === profileRevision) status.textContent = `Сохранён ${handle.name}.`;
    } else {
      const link = document.createElement("a");
      link.href = URL.createObjectURL(file);
      link.download = name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      status.textContent = `Подготовлен ${name}. Для продолжения работы загрузите этот файл.`;
    }
    status.hidden = false;
  } catch (failure) {
    if (failure.name === "AbortError") return;
    status.textContent = "Не удалось сохранить CSV. Проверьте доступ к файлу.";
    status.hidden = false;
  }
});

$("segment-search").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!current) return;
  const query = $("search-value").value.trim();
  const rowLookup = /^row:\s*(.+)$/i.exec(query);
  const numberLookup = /^№?\s*(\d+)$/.exec(query);
  const segment = current.segments.find((item) => rowLookup
    ? item.measurement_ref === rowLookup[1]
    : numberLookup && item.segment_id === Number(numberLookup[1]));
  const status = $("search-status");
  status.hidden = false;
  status.textContent = segment ? `${pointName()} № ${segment.segment_id} · ${number(thicknessOf(segment))} мм`
    : "В выбранном трубопроводе такой записи нет.";
  if (segment) {
    chooseSegment(segment.segment_id);
    $("scenario-detail").scrollIntoView({block: "nearest"});
  }
});
