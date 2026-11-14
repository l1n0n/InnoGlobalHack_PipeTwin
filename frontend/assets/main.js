const $ = (id) => document.getElementById(id);
const number = (value) => value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
const demoThreshold = 6.5;
let pipes = [];
let demo = true;
let current;
let uploadVersion = 0;

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
  $("segment").value = id;
  resetRate();
  showPanel("scenario");
  $("segment").focus();
}

function graph(container, points, limit, labels, gradient = false) {
  const width = Math.max(300, container.closest(".panel").clientWidth - 42 || 900);
  const height = gradient ? 420 : 275;
  const left = 60, right = width - 20, top = 16, bottom = height - 48;
  const first = points[0].x, last = points.at(-1).x;
  const values = points.map((point) => point.y);
  const low = values.reduce((a, b) => Math.min(a, b));
  const high = values.reduce((a, b) => Math.max(a, b));
  const min = limit === null ? low : Math.min(low, limit);
  const max = limit === null ? high : Math.max(high, limit);
  const padding = (max - min) * 0.07 || 0.5;
  const x = (value) => last === first ? (left + right) / 2 : left + (value - first) / (last - first) * (right - left);
  const y = (value) => bottom - (value - min + padding) / (max - min + 2 * padding) * (bottom - top);
  const color = (value) => {
    if (!gradient || low === high) return "#2563a8";
    const t = (value - low) / (high - low);
    const a = t < 0.5 ? [220, 38, 38] : [245, 158, 11];
    const b = t < 0.5 ? [245, 158, 11] : [37, 99, 168];
    const fraction = t < 0.5 ? t * 2 : t * 2 - 1;
    return `rgb(${a.map((channel, i) => Math.round(channel + (b[i] - channel) * fraction)).join(",")})`;
  };
  const ticks = Array.from({length: 5}, (_, i) => min + (max - min) * i / 4);
  const xTicks = first === last ? [first] : Array.from({length: 5}, (_, i) => first + (last - first) * i / 4);
  const grid = [...new Set(ticks)].map((value) => `<line x1="${left}" x2="${right}" y1="${y(value)}" y2="${y(value)}" stroke="#e1e9f0"/><text x="${left - 8}" y="${y(value) + 4}" text-anchor="end">${number(value)}</text>`).join("");
  const axis = xTicks.map((value) => `<text x="${x(value)}" y="${bottom + 20}" text-anchor="middle">${number(value)}</text>`).join("");
  const baseline = limit === null ? "" : `<line x1="${left}" x2="${right}" y1="${y(limit)}" y2="${y(limit)}" stroke="#b91c1c" stroke-width="2" stroke-dasharray="7 5"><title>Порог ${number(limit)} мм</title></line>`;
  const definitions = [], lines = [], dots = [];
  points.forEach((point, i) => {
    const next = points[i + 1];
    if (next && (!gradient || next.id === point.id + 1)) {
      const id = `${container.id}-${i}`;
      if (gradient) definitions.push(`<linearGradient id="${id}" gradientUnits="userSpaceOnUse" x1="${x(point.x)}" x2="${x(next.x)}"><stop stop-color="${color(point.y)}"/><stop offset="1" stop-color="${color(next.y)}"/></linearGradient>`);
      lines.push(`<line x1="${x(point.x)}" x2="${x(next.x)}" y1="${y(point.y)}" y2="${y(next.y)}" stroke="${gradient ? `url(#${id})` : color(point.y)}" stroke-width="4"/>`);
    }
    const critical = limit !== null && point.y <= limit;
    const label = `${gradient ? `Сегмент № ${point.id}, ` : ""}${number(point.x)} ${labels.unit} · ${number(point.y)} мм${critical ? " · на пороге или ниже" : ""}`;
    dots.push(`<circle cx="${x(point.x)}" cy="${y(point.y)}" r="${critical ? 6 : 4}" fill="${color(point.y)}" stroke="${critical ? "#991b1b" : "white"}" stroke-width="2" ${gradient ? `data-segment="${point.id}" tabindex="0" role="button" aria-label="${label}"` : ""}><title>${label}</title></circle>`);
  });
  container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-label="${labels.title}">
    <defs>${definitions.join("")}</defs>${grid}${axis}${baseline}${lines.join("")}${dots.join("")}
    <text x="${width / 2}" y="${height - 6}" text-anchor="middle">${labels.x}</text>
    <text x="12" y="${height / 2}" transform="rotate(-90 12 ${height / 2})" text-anchor="middle">Толщина, мм</text>
  </svg>`;
}

function renderTable() {
  const limit = threshold();
  const order = [...current.segments].sort((a, b) => a.thickness_pred - b.thickness_pred);
  const filter = $("filter").value;
  const rows = filter === "all" ? current.segments : filter === "priority"
    ? order.slice(0, Math.ceil(order.length / 10))
    : order.filter((s) => limit !== null && s.thickness_pred <= limit);
  if (!rows.length) {
    $("table").textContent = limit === null ? "Задайте порог толщины." : "На пороге или ниже сегментов нет.";
    return;
  }
  $("table").innerHTML = `<table><thead><tr><th>Сегмент</th><th>Дистанция, м</th><th>Толщина, мм</th><th>Статус</th></tr></thead><tbody>${rows.map((s) => {
    const start = s.segment_id * 10;
    const distance = current.length_m ? `${start}–${Math.min(start + 10, current.length_m)}` : start;
    const critical = limit !== null && s.thickness_pred <= limit;
    return `<tr><td><button data-segment="${s.segment_id}">№ ${s.segment_id}</button></td><td>${distance}</td><td>${number(s.thickness_pred)}</td><td class="${critical ? "critical" : ""}">${limit === null ? "Порог не задан" : critical ? "На пороге или ниже" : "Выше порога"}</td></tr>`;
  }).join("")}</tbody></table>`;
}

function renderScenario() {
  const segment = current.segments.find((row) => row.segment_id === Number($("segment").value));
  const start = segment.thickness_pred;
  const rate = $("rate").valueAsNumber;
  const years = $("years").valueAsNumber;
  const limit = threshold();
  $("rate-source").textContent = segment.rate === undefined
    ? "Для этого сегмента введите скорость вручную."
    : "Начальная скорость оценена по двум прошлым ВТД. Значение можно изменить.";
  $("scenario-chart").innerHTML = "";
  if (!(rate >= 0) || !(years >= 1 && years <= 30)) {
    $("scenario-metrics").innerHTML = metric("Исходная толщина", `${number(start)} мм`);
    return;
  }
  const end = Math.max(0, start - rate * years);
  const time = limit === null ? "Задайте порог" : start <= limit ? "Уже достигнут"
    : rate === 0 ? "Не достигается в сценарии" : `${number((start - limit) / rate)} лет`;
  $("scenario-metrics").innerHTML = metric("Исходная толщина", `${number(start)} мм`)
    + metric("Толщина к концу горизонта", `${number(end)} мм`)
    + metric("Время до порога", time, limit !== null && start <= limit);
  const points = [{x: 0, y: start}];
  if (rate > 0 && start > 0 && start / rate < years) points.push({x: start / rate, y: 0});
  points.push({x: years, y: end});
  graph($("scenario-chart"), points, limit, {x: "Лет после даты прогноза", unit: "лет", title: "Сценарий утонения"});
}

function resetRate() {
  const segment = current.segments.find((row) => row.segment_id === Number($("segment").value));
  $("rate").value = segment.rate ?? "";
  renderScenario();
}

function render() {
  const limit = threshold();
  const count = current.segments.filter((row) => limit !== null && row.thickness_pred <= limit).length;
  const min = current.segments.reduce((a, b) => a.thickness_pred <= b.thickness_pred ? a : b);
  $("metrics").innerHTML = metric("Сегментов", current.segments.length)
    + metric("Минимальная толщина", `${number(min.thickness_pred)} мм · № ${min.segment_id}`)
    + metric("На пороге или ниже", limit === null ? "Задайте порог" : count, count > 0);
  graph($("profile-chart"), current.segments.map((s) => ({
    id: s.segment_id, x: s.segment_id * 10, y: s.thickness_pred,
  })), limit, {x: "Дистанция от начала трубы, м", unit: "м", title: "Профиль толщины"}, true);
  renderTable();
  renderScenario();
}

function changePipe() {
  current = pipes[$("pipe").value];
  const options = document.createDocumentFragment();
  for (const row of current.segments) {
    options.append(new Option(`№ ${row.segment_id} · от ${row.segment_id * 10} м`, row.segment_id));
  }
  $("segment").replaceChildren(options);
  $("segment").value = current.segments.reduce((a, b) => a.thickness_pred <= b.thickness_pred ? a : b).segment_id;
  resetRate();
  render();
}

function setData(data, name, isDemo) {
  pipes = data;
  if (demo !== isDemo) $("threshold").value = isDemo ? demoThreshold : "";
  demo = isDemo;
  $("source").textContent = name;
  $("pipe").replaceChildren(...pipes.map((pipe, i) => new Option(pipe.id, i)));
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
$("profile-chart").addEventListener("keydown", (event) => {
  if (event.target.dataset.segment !== undefined && ["Enter", " "].includes(event.key)) {
    event.preventDefault();
    chooseSegment(event.target.dataset.segment);
  }
});
$("pipe").addEventListener("change", changePipe);
$("threshold").addEventListener("input", () => current && render());
$("filter").addEventListener("change", () => current && renderTable());
$("segment").addEventListener("change", resetRate);
for (const id of ["rate", "years"]) $(id).addEventListener("input", () => current && renderScenario());
window.addEventListener("resize", () => current && render());
$("file").addEventListener("change", async () => {
  const file = $("file").files[0];
  if (!file) return;
  const version = ++uploadVersion;
  error("");
  try {
    const response = await apiRequest("/api/forecast", {method: "POST", body: file});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail);
    if (version === uploadVersion) setData(data, file.name, false);
  } catch (failure) {
    if (version === uploadVersion) error(failure.message);
  } finally {
    if (version === uploadVersion) $("file").value = "";
  }
});
$("download").addEventListener("click", () => {
  const rows = pipes.flatMap((pipe) => pipe.segments.map((s) =>
    `"${pipe.id.replaceAll('"', '""')}",${s.segment_id},${s.thickness_pred}`));
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([["id,segment_id,thickness_pred", ...rows].join("\n") + "\n"], {type: "text/csv"}));
  link.download = "result.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
});

$("threshold").value = demoThreshold;
apiRequest("/api/demo").then((response) => {
  if (!response.ok) throw new Error("Не удалось загрузить демо.");
  return response.json();
}).then((data) => {
  if (!current) setData(data, "Демо · порог 6,5 мм для примера", true);
}).catch((failure) => error(failure.message));
