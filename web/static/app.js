const form = document.querySelector("#plan-form");
const modeButtons = [...document.querySelectorAll(".mode")];
const placesFields = document.querySelector("#places-fields");
const gpxFields = document.querySelector("#gpx-fields");
const fileInput = document.querySelector("#gpx-file");
const fileDrop = document.querySelector("#file-drop");
const fileTitle = document.querySelector("#file-title");
const fileDetail = document.querySelector("#file-detail");
const submitButton = document.querySelector("#submit-button");
const formError = document.querySelector("#form-error");
const progressCard = document.querySelector("#progress-card");
const progressStage = document.querySelector("#progress-stage");
const results = document.querySelector("#results");
const resultContent = document.querySelector("#result-content");
const downloadGpx = document.querySelector("#download-gpx");
let routeMode = "places";
let pollTimer = null;

modeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    routeMode = button.dataset.mode;
    modeButtons.forEach((item) => item.classList.toggle("active", item === button));
    placesFields.classList.toggle("hidden", routeMode !== "places");
    gpxFields.classList.toggle("hidden", routeMode !== "gpx");
  });
});

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  fileDrop.classList.toggle("has-file", Boolean(file));
  fileTitle.textContent = file ? file.name : "选择手机中的 GPX 文件";
  fileDetail.textContent = file
    ? `${(file.size / 1024 / 1024).toFixed(2)} MB · 点击可重新选择`
    : "上限 20 MB；会严格使用该轨迹，不重新规划主路线";
});

function showError(message) {
  formError.textContent = message;
  formError.classList.remove("hidden");
}

function clearError() {
  formError.textContent = "";
  formError.classList.add("hidden");
}

function getErrorMessage(data, fallback) {
  if (typeof data?.detail === "string") return data.detail;
  if (Array.isArray(data?.detail)) return data.detail.map((item) => item.msg).join("；");
  return fallback;
}

function formatKm(meters) {
  return meters == null ? "暂无" : `${(meters / 1000).toFixed(2)} km`;
}

function formatHours(seconds) {
  return seconds > 0 ? `${(seconds / 3600).toFixed(2)} h` : "未提供";
}

function formatCost(cost) {
  return cost == null ? "价格暂无" : `参考 ¥${Math.round(cost)} / 人`;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function itemTitle(name, score) {
  const title = element("div", "item-title");
  title.append(element("h4", "", name));
  if (score != null) title.append(element("span", "score", `推荐分 ${score.toFixed(1)}`));
  return title;
}

function poiMeta(poi) {
  const parts = [formatCost(poi.cost)];
  if (poi.rating != null) parts.push(`评分 ${poi.rating.toFixed(1)}`);
  if (poi.distance_meters != null) parts.push(`距搜索点 ${poi.distance_meters} m`);
  parts.push(poi.address || "地址暂无");
  return parts.join("·");
}

function resultSection(title) {
  const section = element("section", "result-section");
  section.append(element("h3", "", title));
  resultContent.append(section);
  return section;
}

function appendEmpty(section, text = "暂无符合条件的结果") {
  section.append(element("p", "empty", text));
}

function renderRoute(route) {
  const section = resultSection("路线概况");
  const stats = element("div", "route-stats");
  [
    [formatKm(route.distance_meters), "累计里程"],
    [formatHours(route.duration_seconds), "预计时间"],
    [String(route.point_count), "路线坐标点"],
  ].forEach(([value, label]) => {
    const stat = element("div", "stat");
    stat.append(element("b", "", value), element("span", "", label));
    stats.append(stat);
  });
  section.append(stats);
  const addresses = element("div", "addresses");
  addresses.append(
    element("div", "", `路线来源：${route.source_label}`),
    element("div", "", `起点：${route.start_address}`),
    element("div", "", `终点：${route.end_address}`),
    element("div", "", `午饭区间共使用 ${route.lunch_search_point_count} 个搜索点`),
  );
  section.append(addresses);
}

function renderLunch(items) {
  const section = resultSection("午饭推荐");
  if (!items.length) return appendEmpty(section);
  items.forEach((recommendation, index) => {
    const item = element("article", "item");
    item.append(itemTitle(`${index + 1}. ${recommendation.poi.name}`, recommendation.score));
    item.append(element("p", "meta", poiMeta(recommendation.poi)));
    item.append(element("p", "meta", `理由：${recommendation.reasons.join("；")}`));
    section.append(item);
  });
}

function renderRestStops(items) {
  const section = resultSection("沿途休息点");
  if (!items.length) return appendEmpty(section, "路程不足 15 公里，无需安排周期性休息点");
  items.forEach((stop) => {
    const item = element("article", "item");
    if (!stop.poi) {
      item.append(itemTitle(`${stop.sequence}. 未找到休息点`));
      item.append(element("p", "meta", `已搜索 ${formatKm(stop.search_start_meters)} 至 ${formatKm(stop.search_end_meters)} 区间`));
    } else {
      item.append(itemTitle(`${stop.sequence}. ${stop.poi.name}`));
      const tags = element("p", "meta");
      tags.append(element("span", "tag", stop.category || "休息点"));
      tags.append(element("span", "tag", `全程 ${formatKm(stop.route_progress_meters)}`));
      item.append(tags);
      item.append(element("p", "meta", `距上一休息点 ${formatKm(stop.distance_since_previous_meters)}；偏离路线约 ${stop.distance_from_route_meters ?? "暂无"} m；${stop.poi.address}`));
    }
    section.append(item);
  });
}

function renderLodging(items) {
  const section = resultSection("住宿优先推荐");
  if (!items.length) return appendEmpty(section, "没有找到终点附近的合适住宿");
  items.forEach((option, index) => {
    const item = element("article", "item");
    item.append(itemTitle(`${index + 1}. ${option.lodging.poi.name}`, option.lodging.score));
    item.append(element("p", "meta", `住宿：${poiMeta(option.lodging.poi)}`));
    const dinner = element("div", "dinner");
    if (option.dinner) {
      dinner.append(element("strong", "", `晚饭：${option.dinner.poi.name}`));
      dinner.append(element("p", "meta", `${poiMeta(option.dinner.poi)}；住宿到晚饭的实际道路距离 ${formatKm(option.dinner_road_distance_meters)}`));
    } else {
      dinner.append(element("span", "", "晚饭：3 公里实际骑行道路范围内暂无已验证结果"));
    }
    item.append(dinner);
    section.append(item);
  });
}

function renderWarnings(warnings) {
  if (!warnings.length) return;
  const section = resultSection("规划提示");
  const list = element("ul", "warning-list");
  warnings.forEach((warning) => list.append(element("li", "", warning)));
  section.append(list);
}

function renderResult(payload) {
  resultContent.replaceChildren();
  renderRoute(payload.result.route);
  renderLunch(payload.result.lunch_recommendations);
  renderRestStops(payload.result.rest_stops);
  renderLodging(payload.result.lodging_options);
  renderWarnings(payload.result.warnings);
  downloadGpx.href = payload.gpx_download_url;
  results.classList.remove("hidden");
  results.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function pollJob(statusUrl) {
  try {
    const response = await fetch(statusUrl, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(getErrorMessage(data, "无法查询规划进度"));
    progressStage.textContent = data.stage;
    if (data.status === "completed") {
      clearTimeout(pollTimer);
      progressCard.classList.add("hidden");
      submitButton.disabled = false;
      renderResult(data);
      return;
    }
    if (data.status === "failed") {
      throw new Error(data.error || "规划失败");
    }
    pollTimer = setTimeout(() => pollJob(statusUrl), 1600);
  } catch (error) {
    clearTimeout(pollTimer);
    progressCard.classList.add("hidden");
    submitButton.disabled = false;
    showError(error.message);
    form.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();
  results.classList.add("hidden");

  const data = new FormData(form);
  if (routeMode === "places") {
    data.delete("gpx_file");
    const hasDescription = String(data.get("description") || "").trim();
    const locationNames = ["start_city", "start_name", "end_city", "end_name"];
    if (!hasDescription && locationNames.some((name) => !String(data.get(name) || "").trim())) {
      return showError("请填写完整起终点，或用一句话描述行程。");
    }
  } else {
    ["start_city", "start_name", "end_city", "end_name"].forEach((name) => data.delete(name));
    if (!fileInput.files[0]) return showError("请先选择一个 GPX 文件。");
  }

  submitButton.disabled = true;
  progressCard.classList.remove("hidden");
  progressStage.textContent = "正在提交规划任务";
  progressCard.scrollIntoView({ behavior: "smooth", block: "center" });

  try {
    const response = await fetch("/api/plans", { method: "POST", body: data });
    const body = await response.json();
    if (!response.ok) throw new Error(getErrorMessage(body, "提交规划失败"));
    pollJob(body.status_url);
  } catch (error) {
    progressCard.classList.add("hidden");
    submitButton.disabled = false;
    showError(error.message);
  }
});
