const $ = (id) => document.getElementById(id);
const number = (value) => value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
let pipes = [];
let current;
let uploadVersion = 0;
let co2Rate;
let co2Request = 0;
let profileRevision = 0;
let scenarioResult;
const resultPipes = new Map();
let resultVersion = 0;
let resultFile;
let resultPreview = [];
const thicknessOf = (segment) => segment.replacement?.wall_mm ?? segment.thickness_pred;
const orderedInspection = () => current.coordinate_basis === "inspection_order";
const historicalRate = (segment) => segment.replacement || orderedInspection() ? undefined : segment.rate;
const positionOf = (segment) => orderedInspection() ? segment.segment_id : segment.segment_id * 10;
const positionLabel = () => orderedInspection() ? "Номер записи ВТД" : "Дистанция по профилю, м";
const positionUnit = () => orderedInspection() ? "" : " м";
const pointName = () => orderedInspection() ? "Точка ВТД" : "Сегмент";
const yearsBetween = (start, end) => (Date.parse(end) - Date.parse(start)) / (365.25 * 86400000);
const replacementYear = (segment) => current.commissioned_at && segment.replacement
  ? yearsBetween(current.commissioned_at, segment.replacement.date) : 0;

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
      size: y.map((value) => value !== null && limit !== null && value <= limit ? 10 : 7),
      color: y.map((value) => value !== null && limit !== null && value <= limit ? "#dc2626" : "#2563a8"),
      line: {color: "#fff", width: 1},
    },
    hovertemplate: (profile || labels.pointName ? (labels.pointName ?? pointName()) + " № %{customdata}<br>" : "")
      + (labels.markersOnly ? "" : "%{x:.2~f}" + labels.unit + " · ")
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
    xaxis: {...axis, title: {text: labels.x}},
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
  const model = $("model").value;
  const startYear = $("start-year").valueAsNumber;
  const target = $("target-date").value;
  const years = current.commissioned_at && target
    ? yearsBetween(current.commissioned_at, target)
    : $("years").valueAsNumber;
  $("years").disabled = Boolean(current.commissioned_at && target);
  const limit = threshold();
  const validYears = startYear >= 0 && startYear < years && years <= 100;
  const rateFor = (segment) => model === "history" ? historicalRate(segment)
    : model === "manual" ? $("rate").valueAsNumber : co2Rate;
  $("co2-form").hidden = model !== "co2";
  $("rate-control").hidden = model !== "manual";
  if (includeOverview) renderForecastProfile(model, rateFor, startYear, years, limit, validYears);
  if ($("scenario-detail").open) renderSegmentScenario(rateFor, startYear, years, limit, validYears);
}

function renderForecastProfile(model, rateFor, startYear, years, limit, validYears) {
  const points = [];
  for (const segment of current.segments) {
    const rate = rateFor(segment);
    const installed = replacementYear(segment);
    if (validYears && Number.isFinite(rate) && rate >= 0 && installed <= years) {
      points.push({id: segment.segment_id, x: positionOf(segment),
        y: Math.max(0, thicknessOf(segment) - rate * (years - installed)),
        before: Math.max(0, thicknessOf(segment) - rate * Math.max(0, startYear - installed))});
    }
  }
  scenarioResult = !orderedInspection() && points.length === current.segments.length ? {
    id: current.id, length_m: current.length_m,
    segments: points.map(({id, y}) => ({segment_id: id, thickness_pred: y})),
  } : undefined;
  $("stage-result").disabled = !scenarioResult;
  $("stage-status").hidden = true;
  const total = current.segments.length;
  $("rate-source").textContent = !validYears ? "Укажите интервал от 0 до 100 лет. Начало должно быть раньше конца."
    : model === "co2" && co2Rate === undefined ? "Введите условия CO₂ и нажмите «Рассчитать»."
    : model === "manual" && !points.length ? "Введите общую скорость утонения."
    : model === "history" && !points.length ? "По ВТД нет оценок скорости. Выберите общую скорость или CO₂."
    : `Интервал ${number(startYear)}–${number(years)} лет от исходного профиля. График показывает толщину в конце интервала.`;
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

function renderSegmentScenario(rateFor, startYear, years, limit, validYears) {
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
  const rate = rateFor(segment);
  const installed = replacementYear(segment);
  const firstYear = Math.max(startYear, installed);
  const hasForecast = Number.isFinite(rate) && rate >= 0 && validYears && installed <= years;
  $("segment-plot").hidden = !hasForecast;
  if (!hasForecast) {
    $("scenario-metrics").innerHTML = metric("Исходная толщина", `${number(start)} мм`)
      + metric("Прогноз", "Недостаточно данных");
    return;
  }
  const initial = Math.max(0, start - rate * (firstYear - installed));
  const end = Math.max(0, start - rate * (years - installed));
  const time = limit === null ? "Задайте порог" : initial <= limit ? "Достигнут к началу"
    : end > limit ? "Выше порога до конца" : `${number(installed + (start - limit) / rate)} лет от исходного профиля`;
  $("scenario-metrics").innerHTML = metric("Толщина в начале интервала", `${number(initial)} мм`)
    + metric("Скорость утонения", `${rate.toLocaleString("ru-RU", {maximumSignificantDigits: 4})} мм/год`)
    + metric("Толщина в конце интервала", `${number(end)} мм`)
    + metric("Достижение порога", time, limit !== null && end <= limit);
  const points = [{x: firstYear, y: initial}];
  const zeroYear = installed + start / rate;
  if (rate > 0 && zeroYear > firstYear && zeroYear < years) points.push({x: zeroYear, y: 0});
  if (years > firstYear) points.push({x: years, y: end});
  graph($("scenario-chart"), points, limit, {
    x: "Лет после исходного профиля", unit: " лет",
    revision: [profileRevision, current.id, segment.segment_id, startYear, years].join(":"),
  });
}

function render() {
  const limit = threshold();
  const count = current.segments.filter((row) => limit !== null && thicknessOf(row) <= limit).length;
  const min = current.segments.reduce((a, b) => thicknessOf(a) <= thicknessOf(b) ? a : b);
  $("metrics").innerHTML = metric(orderedInspection() ? "Точек ВТД" : "Сегментов", current.segments.length)
    + metric("Минимальная толщина", `${number(thicknessOf(min))} мм · № ${min.segment_id}`)
    + metric("На пороге или ниже", limit === null ? "Задайте порог" : count, count > 0);
  if (!$("profile").hidden) {
    graph($("profile-chart"), pipePoints(), limit, {
      x: positionLabel(), unit: positionUnit(), markersOnly: orderedInspection(), revision: profileRevision + ":" + current.id,
    }, true);
  }
  if (!$("inspection").hidden) renderTable();
  if (!$("result").hidden) renderResult();
  renderScenario();
}

function changePipe() {
  current = pipes[$("pipe").value];
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
  $("scenario-title").textContent = ordered ? "Прогноз по точкам ВТД" : "Прогноз коррозии по всей трубе";
  $("forecast-title").textContent = ordered ? "Толщина в точках ВТД в конце интервала" : "Толщина вдоль трубы в конце интервала";
  $("detail-title").textContent = ordered ? "Изменение толщины в точке по годам" : "Изменение толщины сегмента по годам";
  $("detail-label").textContent = pointName();
  $("target-date-control").hidden = !nominal;
  if (!nominal) $("target-date").value = "";
  $("search-label").textContent = ordered ? "Найти точку ВТД" : "Найти сегмент";
  $("search-value").placeholder = ordered ? "Номер точки или row:123" : "Номер сегмента";
  $("search-value").value = "";
  $("search-status").hidden = true;
  $("history-model").disabled = !current.segments.some((segment) => Number.isFinite(historicalRate(segment)));
  if ($("history-model").disabled && $("model").value === "history") $("model").value = "manual";
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
  profileRevision++;
  $("save-status").hidden = true;
  $("source").textContent = name;
  $("pipe").replaceChildren(...pipes.map((pipe, i) => new Option(pipe.name ? `${pipe.name} · ${pipe.id}` : `ID ${pipe.id}`, i)));
  $("empty-state").hidden = true;
  $("loaded-controls").hidden = false;
  $("dashboard-content").hidden = false;
  $("download").disabled = false;
  changePipe();
}

function error(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}

document.querySelectorAll("[data-panel]").forEach((button) => button.addEventListener("click", () => {
  showPanel(button.dataset.panel);
  if (current) render();
}));
document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-segment]");
  if (target) chooseSegment(target.dataset.segment);
});
$("pipe").addEventListener("change", changePipe);
$("threshold").addEventListener("input", () => current && render());
$("filter").addEventListener("change", () => current && renderTable());
$("segment").addEventListener("change", () => renderScenario(false));
$("scenario-detail").addEventListener("toggle", () => {
  if (current && $("scenario-detail").open) renderScenario(false);
});
$("model").addEventListener("change", () => current && renderScenario());
function clearCo2() {
  co2Request++;
  co2Rate = undefined;
  $("co2-error").hidden = true;
  if (current) renderScenario();
}
for (const id of ["temperature", "pressure", "co2-percent", "ph", "velocity", "diameter"]) {
  $(id).addEventListener("input", clearCo2);
}
$("co2-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  clearCo2();
  const request = co2Request;
  const params = new URLSearchParams({
    temperature_c: $("temperature").value,
    total_pressure_bar: $("pressure").value,
    co2_mol_percent: $("co2-percent").value,
    ph: $("ph").value,
    flow_velocity_ms: $("velocity").value,
    diameter_mm: $("diameter").value,
  });
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
for (const id of ["rate", "start-year", "years", "target-date"]) $(id).addEventListener("input", () => current && renderScenario());
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
  $("history-model").disabled = !current.segments.some((segment) => Number.isFinite(historicalRate(segment)));
  if ($("history-model").disabled && $("model").value === "history") $("model").value = "manual";
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

function resetResultPreview(message = "") {
  resultVersion++;
  resultFile = undefined;
  resultPreview = [];
  $("download-result").disabled = true;
  $("prepare-result").disabled = resultPipes.size !== 2;
  $("result-status").textContent = message ||
    (resultPipes.size ? "В подборке: " + [...resultPipes.keys()].join(", ") + "." : "");
  renderResult();
}

function renderResult() {
  const pipe = resultPreview[$("result-pipe").value];
  $("result-plot").hidden = $("result-pipe-control").hidden = !pipe;
  if (!pipe || $("result").hidden) return;
  graph($("result-chart"), pipe.segments.map((segment) => ({
    id: segment.segment_id, x: segment.segment_id * 10, y: segment.thickness_pred,
  })), threshold(), {
    x: "Начало 10-метрового сегмента, м", unit: " м", pointName: "Сегмент",
    revision: `result:${resultVersion}:${pipe.id}`,
  });
}

$("stage-result").addEventListener("click", () => {
  if (!scenarioResult) return;
  const status = $("stage-status");
  status.hidden = false;
  if (resultPipes.size === 2 && !resultPipes.has(scenarioResult.id)) {
    status.textContent = "В подборке уже два участка. Очистите её на вкладке «Экспорт».";
    return;
  }
  resultPipes.set(scenarioResult.id, scenarioResult);
  resetResultPreview();
  status.textContent = `Прогноз участка ${scenarioResult.id} добавлен. В подборке ${resultPipes.size} из 2.`;
});

$("use-loaded-result").addEventListener("click", () => {
  if (pipes.length !== 2 || pipes.some((pipe) => pipe.source_kind === "nominal" ||
      pipe.coordinate_basis === "inspection_order" || pipe.segments.some((segment) => segment.replacement))) {
    $("result-status").textContent = "Загрузите готовый прогноз по физическим сегментам двух участков.";
    return;
  }
  resultPipes.clear();
  for (const pipe of pipes) resultPipes.set(pipe.id, pipe);
  resetResultPreview();
});

$("clear-result").addEventListener("click", () => {
  resultPipes.clear();
  resetResultPreview();
});

$("prepare-result").addEventListener("click", async () => {
  const version = ++resultVersion;
  const status = $("result-status");
  $("prepare-result").disabled = true;
  $("download-result").disabled = true;
  status.textContent = "Проверяем полноту и собираем CSV…";
  try {
    const response = await apiRequest("/api/result", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify([...resultPipes.values()]),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(typeof error.detail === "string" ? error.detail : "Некорректные данные result.csv.");
    }
    const file = await response.blob();
    const parsed = await apiRequest("/api/forecast", {method: "POST", body: file});
    if (!parsed.ok) throw new Error("Не удалось повторно прочитать сформированный result.csv.");
    const preview = await parsed.json();
    if (version !== resultVersion) return;
    resultFile = file;
    resultPreview = preview;
    $("result-pipe").replaceChildren(...preview.map((pipe, index) =>
      new Option(pipe.name ? `${pipe.name} · ${pipe.id}` : pipe.id, index)));
    const count = preview.reduce((sum, pipe) => sum + pipe.segments.length, 0);
    const coverage = response.headers.get("X-Result-Coverage") === "passport"
      ? "Полнота проверена по длинам участков."
      : "Нумерация проверена. Сверьте число сегментов с длинами участков в паспорте.";
    status.textContent = `Готово. В result.csv два участка и ${count} строк прогноза. ${coverage}`;
    $("download-result").disabled = false;
    renderResult();
  } catch (failure) {
    if (version === resultVersion) status.textContent = failure.message;
  } finally {
    if (version === resultVersion) $("prepare-result").disabled = false;
  }
});

$("result-pipe").addEventListener("change", renderResult);
$("download-result").addEventListener("click", () => {
  if (!resultFile) return;
  const link = document.createElement("a");
  link.href = URL.createObjectURL(resultFile);
  link.download = "result.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
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
